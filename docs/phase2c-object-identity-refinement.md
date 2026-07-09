# Phase 2C Findings: Object Identity Refinement (Tier 1 + Tier 2)

**Date:** 2026-07-10
**Status:** Tier 1 (direct pointer-chain resolution) and Tier 2 (SVF Andersen points-to refinement) both implemented and passing regression.

---

## Summary

Phase 1/2A/2B `access_fact.base_object` was hardcoded to a single constant:

```json
{"object_scope": "synthetic", "value": "minimal_stub"}
```

for every access fact, regardless of what the pointer actually referred to. As documented in `docs/phase2b-evidence-identity.md`'s "Important Boundary" section, this meant `object_identity_candidate` edges were really just grouping by symbolic field name, not by any real notion of "same object" — despite `extraction/src/implicitfuzz-extract.cpp` already building a full SVF Andersen points-to analysis (`AndersenWaveDiff`) that was never consulted for this purpose.

Phase 2C replaces the hardcoded stub with two layers:

- **Tier 1** — direct pointer-chain resolution at the LLVM IR level (no SVF query).
- **Tier 2** — SVF Andersen points-to, invoked only where tier 1 could not produce a concrete identity, per the v3 spec constraint below.

## Tier 1: Direct Resolution

For each load/store's pointer operand, walk through GEP chains and *unambiguous* local-slot hops (a local variable's stack slot is only followed if it has exactly one store — see "Why not `traceLocalSlotValue`" below) to the ultimate root value, then classify:

| Root value kind | `object_scope` | `value` |
|---|---|---|
| `llvm::GlobalVariable` | `global` | global symbol name |
| `llvm::Argument` | `formal_param` | `"<function>:<arg_index>"` |
| `llvm::AllocaInst` | `allocation_site` | function + address-based id |
| `CallBase` matching `primitive_summary` alloc entry or a detected alloc wrapper | `allocation_site` | function + address-based id |
| anything else (loaded pointer with ambiguous/unknown origin) | `synthetic` | `minimal_stub` (unchanged fallback) |

### Why not reuse `traceLocalSlotValue`?

The existing `traceLocalSlotValue` (used for alloc/free wrapper detection) picks the *first* store found among an alloca's users, which is an accepted approximation for wrapper detection but is wrong for object identity when a stack slot has more than one store — e.g. a loop induction variable like `head = head->next;` in `accumulate()`. Tracing through such an ambiguous slot would silently conflate the original argument with a value computed several iterations later.

Phase 2C introduces `traceUniqueStoreSlot`, which only follows the hop when the alloca has **exactly one** store; otherwise it conservatively stops and the access falls through to `synthetic`. This was caught by an explicit tiny-golden regression before it shipped.

## ⚠️ Correction: `formal_param` is relational evidence only (v3 §5.6)

The first cut of this phase promoted `formal_param` to `medium` confidence in `_identity_confidence_for_scope`, treating it as equivalent to `allocation_site`/`global`. This was **wrong** and has been reverted after (re-)reading `docs/静态抽取层技术路线_v3.md` §5.6, which explicitly states:

> `formal_param` 与 `synthetic` 是**关系证据**，不能被证据库上层直接判为身份等价；必须沿 `call_fact` 的调用点 refine 到具体 allocation site/global 后，才可进入对象身份闭包。

i.e. `formal_param` and `synthetic` must be refined toward a concrete `allocation_site`/`global` before they can support an identity claim — they are not identity-grade on their own, precisely to avoid a helper function's parameter silently merging two unrelated call sites' actual objects. `_identity_confidence_for_scope` now only grants `medium` to `allocation_site`/`global`; `formal_param` stays at `low`, same as `synthetic`. Regression guard: `test_derive_object_identity_edges_gives_low_confidence_for_formal_param_scope`.

Tier 2 (below) is exactly the "refine via a real analysis" step the spec calls for.

## Tier 2: SVF Andersen Points-To Refinement

For pointers where tier 1 left `formal_param` or `synthetic`, additionally resolve the pointer's SVF value node against the already-built `Andersen* ander` points-to analysis (`ander->getPts(nodeId)`), reducing `GepObjVar` results to their `BaseObjVar` (field-sensitivity doesn't matter for "same object" identity):

- **Points-to set has exactly one concrete object** (no blackhole target): promote `base_object` to that object's identity — `global` if `isGlobalObj()`, else `allocation_site`.
- **More than one concrete object**: leave `base_object` as tier 1 left it (`formal_param`/`synthetic`), but emit a new `alias_fact` row (`object_id` = tier 1's result, `points_to_set` = the resolved object labels, `relation_evidence = instruction_id`, `pta_kind = "andersen"`). Schema and SQLite ingestion for `alias_fact` already existed and required zero changes.
- **A blackhole object is present** (SVF's "could be anything" node): treated as fully unresolved — no promotion, no `alias_fact` (not a useful signal).

### Label consistency between tier 1 and tier 2

An object reached by tier 2 (via points-to) must get **the same label** as the same object reached directly by tier 1, otherwise the same real object would silently get two different `base_object` strings depending on which pointer expression happened to reach it, defeating grouping. Tier 2 therefore reverse-maps the SVF object back to its LLVM `Value*` (`llvmMS->getLLVMValue(baseObj)`) and classifies it through the **same** `classifyRootValue` function tier 1 uses, falling back to an SVF-node-based label only when the reverse mapping is unavailable. This was caught empirically: without it, `peek_value(n)`'s `n->value` (reached via tier 2) initially got a different-looking label (`svf_obj116@CallICFGNode:...`) than `alloc_node`'s own internal accesses to the *same* heap object (`alloc_node#addr...`) reached via tier 1.

### `derive_pointsto_identity_edges` (`graph.py`)

New function alongside `derive_object_identity_edges`, reading `alias_fact.points_to_set` (via `relation_evidence = access_fact.instruction_id`) and linking any two access facts on the same symbolic field prefix whose points-to sets intersect. Unlike the tier-1 rule (scoped to `a.function = b.function`), this is allowed to **cross function boundaries** — Andersen's points-to is whole-program, and cross-function linkage is exactly the refinement the v3 spec requires before `formal_param`/`synthetic` facts can support an identity claim. Edges use `basis = "pointsto_intersection_andersen"`, `confidence = "medium"`. Wired into `build_evidence_graph`'s pipeline as `pointsto_identity_candidate_edges`; gracefully returns 0 if the `alias_fact` table doesn't exist (e.g. an older DB).

### Known, accepted limitation: two granularities can coexist

`main`'s own `b->...` accesses resolve via tier 1's wrapper-call-site heuristic (`main#addr<call to alloc_node(2)>` — call-site sensitive: distinguishes the two separate `alloc_node()` invocations for `a` and `b`). `accumulate`'s `head->...` and `peek_value`'s `n->...` resolve via tier 2 to the malloc call *inside* `alloc_node` (`alloc_node#addr<malloc>` — context-insensitive: Andersen merges every object `alloc_node` ever returns into one abstract object, since it doesn't distinguish call sites). Both labels are individually self-consistent and correct at their own granularity, but they are **not** the same string, so `main`'s accesses and `accumulate`/`peek_value`'s accesses on what is, at runtime, an overlapping/related object will not automatically merge under `derive_object_identity_edges`'s exact-match rule. Unifying wrapper-return-value flow with real points-to (effectively giving Andersen call-site sensitivity for wrapper allocators) is out of scope for this pass — documented here as a known gap, not silently accepted. The tiny golden explicitly locks in and demonstrates this exact behavior (see `check_golden_facts.py`'s `consistent_allocation_site` assertions).

## Tiny Golden Changes

Added `static int peek_value(struct Node *n) { return n->value; }` (called once from `main`) to `extraction/testdata/input.c`. `extraction/scripts/check_golden_facts.py` now asserts, after both tiers run:

- `alloc_node`'s own `Node.*` accesses share one consistent `allocation_site` value starting with `alloc_node#addr` (its `malloc` call site, tier 1).
- `main`'s `Node.*` accesses (on `b`) share one consistent `allocation_site` value starting with `main#addr` (the `alloc_node(2)` call site, tier 1), and this value is **distinct** from `alloc_node`'s own (different granularity, see above).
- `accumulate`'s `head`-derived and `peek_value`'s `n`-derived accesses each resolve to `allocation_site`, and **both exactly match** `alloc_node`'s own value — proving tier 2 correctly refines two different formal-parameter cases (one loop-ambiguous, one single-call-site) to the real, shared, cross-function object identity via points-to, not just per-function grouping.

Existing golden assertions (symbolic path, field type, provenance, confidence for `Node.next`/`Node.value`/etc.) are unchanged and still pass; `base_object` resolution does not affect the DWARF-derived `confidence` field.

## Bug found and fixed along the way: `validate_facts_schema.py`'s fact-type whitelist

`extraction/scripts/validate_facts_schema.py` had a hardcoded `FACT_SCHEMAS = {"call_fact": ..., "access_fact": ...}` dict that predated `alias_fact`/`branch_fact`/`gate_seed_fact` ever being emitted, even though their schema files already existed. The moment tier 2 emitted a real `alias_fact` on `io_uring/cancel.c` (see below), Phase 1 regression failed with `unsupported fact_type 'alias_fact'`. Fixed at the root: the validator now discovers fact types by globbing `extraction/schema/facts/*.schema.json` (excluding `common.schema.json`) instead of a hand-maintained list, so this class of bug can't recur for `branch_fact`/`gate_seed_fact` in Part B either.

## Measured Impact

Rebuilt `/tmp/phase1_facts.db` from tiny + kernel case1/2/3 (timeout.c, cancel.c, kbuf.c):

| Metric | Before (Phase 2B baseline) | After (Phase 2C tier 1) | After (Phase 2C tier 1 + tier 2) |
|---|---:|---:|---:|
| access nodes | 753 | 759 | 759 |
| `object_identity_candidate` edges (`same_function_symbolic_object_prefix` basis) | 642 | 517 | 517 |
| `object_identity_candidate` edges (`pointsto_intersection_andersen` basis, new) | — | — | **6** |
| confidence split (low / medium) | ~642 / ~0 | 489 low / 28 medium* | 489 low / **34 medium** |

*The tier-1-only number reported earlier (240/277) reflected the since-reverted `formal_param → medium` overclaim; 28 is the corrected figure (`allocation_site`/`global` only).

On `io_uring/cancel.c`, tier 2 found a genuine real-world multi-object case: `io_cancel_data` is stack-allocated separately in `io_async_cancel` and `io_sync_cancel`, both of which call a shared helper through a pointer parameter. Andersen's points-to for that parameter correctly resolves to **both** stack objects, so tier 1's `synthetic`/`formal_param` result is correctly left as relational evidence and an `alias_fact` is emitted instead of over-claiming a single identity:

```json
{"object_prefix": "io_cancel_data",
 "shared_points_to": ["allocation_site:io_async_cancel#addr...", "allocation_site:io_sync_cancel#addr..."],
 "status": "candidate_not_identity_closure"}
```

On `io_uring/timeout.c` alone, `base_object.object_scope` distribution over 266 access facts (tier 1 + tier 2 combined):

```text
synthetic:       148  (55.6%)
formal_param:    109  (41.0%)  -- relational evidence only, not identity-grade (v3 §5.6)
allocation_site:   8  (3.0%)
global:            1  (0.4%)
```

(`timeout.c` happens not to trigger any tier-2 promotions or multi-object cases in this run; `cancel.c` above is where tier 2's effect is visible.)

## Deferred / Out of Scope

- **Wrapper-call-site vs. points-to granularity unification** (see "Known, accepted limitation" above).
- **`alias_fact` for the `alloc`/`free` primitive/wrapper call events themselves** — these still use the unchanged synthetic `base_object`; only load/store field accesses go through tier 1/tier 2. Relevant to future `lifecycle_candidate` refinement, not `object_identity_candidate`.
- **Transitive closure over `alias_fact`/`object_identity_candidate`** — per v3 §5.7, this is intended to be computed by the evidence store (SQL recursive CTE) over the candidate edges, not resolved at extraction time. Not implemented yet.

## Regression

```bash
extraction/scripts/run_golden_test.sh        # tiny, including tier-1/tier-2 consistency assertions
extraction/scripts/run_phase1_regression.sh  # tiny + kernel case1/2/3 + BTF smoke — all PASS
python3 extraction/scripts/ingest_phase1_smoke.py
python3 extraction/scripts/build_evidence_graph_smoke.py
python3 -m pytest tests/                     # 17 passed
```
