# Phase 2C Findings: Object Identity Refinement (Tier 1 — Direct Resolution)

**Date:** 2026-07-10
**Status:** Tier 1 (direct, non-SVF) base object resolution implemented and passing regression. Tier 2 (SVF Andersen points-to for indirect/loaded pointers) is scoped but not yet implemented.

---

## Summary

Phase 1/2A/2B `access_fact.base_object` was hardcoded to a single constant:

```json
{"object_scope": "synthetic", "value": "minimal_stub"}
```

for every access fact, regardless of what the pointer actually referred to. As documented in `docs/phase2b-evidence-identity.md`'s "Important Boundary" section, this meant `object_identity_candidate` edges were really just grouping by symbolic field name, not by any real notion of "same object" — despite `extraction/src/implicitfuzz-extract.cpp` already building a full SVF Andersen points-to analysis (`AndersenWaveDiff`) that was never consulted for this purpose.

Phase 2C tier 1 replaces the hardcoded stub with **direct pointer-chain resolution** performed entirely at the LLVM IR level (no SVF points-to query yet — see "Deferred: Tier 2" below):

For each load/store's pointer operand, walk through GEP chains and *unambiguous* local-slot hops (a local variable's stack slot is only followed if it has exactly one store — see "Why not `traceLocalSlotValue`" below) to the ultimate root value, then classify:

| Root value kind | `object_scope` | `value` |
|---|---|---|
| `llvm::GlobalVariable` | `global` | global symbol name |
| `llvm::Argument` | `formal_param` | `"<function>:<arg_index>"` |
| `llvm::AllocaInst` | `allocation_site` | function + address-based id |
| `CallBase` matching `primitive_summary` alloc entry or a detected alloc wrapper | `allocation_site` | function + address-based id |
| anything else (loaded pointer with ambiguous/unknown origin) | `synthetic` | `minimal_stub` (unchanged fallback) |

## Why not reuse `traceLocalSlotValue`?

The existing `traceLocalSlotValue` (used for alloc/free wrapper detection) picks the *first* store found among an alloca's users, which is an accepted approximation for wrapper detection but is wrong for object identity when a stack slot has more than one store — e.g. a loop induction variable like `head = head->next;` in `accumulate()`. Tracing through such an ambiguous slot would silently conflate the original argument with a value computed several iterations later.

Phase 2C introduces `traceUniqueStoreSlot`, which only follows the hop when the alloca has **exactly one** store; otherwise it conservatively stops and the access falls through to `synthetic`. This was caught by an explicit tiny-golden regression: `accumulate(struct Node *head)`'s field accesses on `head` correctly remain `synthetic` (the slot has two stores — the incoming argument and the loop-carried reassignment), while a new single-store case (`peek_value`, see below) correctly resolves to `formal_param`.

## Tiny Golden Changes

Added `static int peek_value(struct Node *n) { return n->value; }` (called once from `main`) to `extraction/testdata/input.c` as an unambiguous single-store formal-parameter case. `extraction/scripts/check_golden_facts.py` now asserts:

- `peek_value`'s `Node.value` access: `base_object == {"object_scope": "formal_param", "value": "peek_value:0"}` (exact match — deterministic, no address embedded).
- All `Node.*` accesses within `alloc_node` share one consistent `{"object_scope": "allocation_site", "value": "alloc_node#addr..."}` (the `malloc` call site).
- All `Node.*` accesses within `main` (on `b`) share one consistent `allocation_site` value distinct from `alloc_node`'s own internal call site (the `alloc_node(2)` wrapper-alloc call site).
- `accumulate`'s `head`-derived accesses are **not** asserted to any particular scope (documented as the expected ambiguous case — see above).

Existing golden assertions (symbolic path, field type, provenance, confidence for `Node.next`/`Node.value`/etc.) are unchanged and still pass; `base_object` resolution does not affect the DWARF-derived `confidence` field (base_object quality and symbolic-path confidence remain independently scored).

## `graph.py` Change

`_identity_confidence_for_scope` now treats `formal_param` the same as `allocation_site`/`global` (medium confidence), since it is a real, non-synthetic scope:

```python
def _identity_confidence_for_scope(scope: str) -> str:
    if scope in {"allocation_site", "global", "formal_param"}:
        return "medium"
    return "low"
```

New test: `test_derive_object_identity_edges_gives_medium_confidence_for_formal_param_scope` in `tests/test_evidence_graph.py`.

## Measured Impact

Rebuilt `/tmp/phase1_facts.db` from tiny + kernel case1/2/3 (timeout.c, cancel.c, kbuf.c):

| Metric | Before (Phase 2B baseline) | After (Phase 2C tier 1) |
|---|---:|---:|
| access nodes | 753 | 759 (+6 from new `peek_value` golden case) |
| `object_identity_candidate` edges | 642 | **517** |
| `object_identity_candidate` confidence: low / medium | ~642 / ~0 | 240 / **277** |

On `io_uring/timeout.c` alone, `base_object.object_scope` distribution over 266 access facts:

```text
synthetic:       148  (55.6%)
formal_param:    109  (41.0%)
allocation_site:   8  (3.0%)
global:            1  (0.4%)
```

Roughly 44% of access facts in this real kernel TU now carry real object grounding instead of the uniform synthetic stub, and the candidate identity graph is both smaller and has a meaningful confidence split instead of being uniformly low-confidence.

## Deferred: Tier 2 (SVF Andersen points-to)

Not implemented in this pass. Tier 1 only resolves pointers whose origin is directly traceable through GEPs and a single unambiguous local-slot hop. It does **not** help when a pointer is loaded from another object's field (e.g. `req->ctx->something`, where `ctx` was itself loaded via a prior GEP+load) — these remain `synthetic`, same as before.

`extraction/src/implicitfuzz-extract.cpp` already builds `Andersen* ander = AndersenWaveDiff::createAndersenWaveDiff(pag)` and this is unused for object resolution beyond call-graph stats. A follow-up tier 2 should, for pointers tier 1 leaves synthetic:

1. Resolve the pointer operand's SVF value node (`llvmMS->getValueNode(ptr)`) and query `ander->getPts(nodeId)`.
2. For each object node in the points-to set, `pag->getGNode(objId)` → `dyn_cast<BaseObjVar>` → classify via `isGlobalObj()`/`isHeap()`/`isStack()`.
3. When the points-to set has exactly one object, use it as `base_object` directly (same schema, no changes needed).
4. When it has more than one object (common — Andersen is not field/context-sensitive), keep `base_object` synthetic but emit a new `alias_fact` row (`points_to_set`, `relation_evidence = instruction_id`, `pta_kind = "andersen"` — schema and SQLite ingestion for `alias_fact` already exist and require no further changes) and extend `derive_object_identity_edges` in `graph.py` to also link facts whose `alias_fact.points_to_set` intersect.

This is the natural next increment of Part A ("结合 SVF points-to 结果和函数入口上下文" — tier 1 covers "函数入口上下文", tier 2 covers "SVF points-to 结果").

## Regression

```bash
extraction/scripts/run_golden_test.sh        # tiny, including new peek_value assertions
extraction/scripts/run_phase1_regression.sh  # tiny + kernel case1/2/3 + BTF smoke — all PASS
python3 extraction/scripts/ingest_phase1_smoke.py
python3 extraction/scripts/build_evidence_graph_smoke.py
python3 -m pytest tests/                     # 16 passed, including 1 new test
```

All four Phase 1 regression lines and all pytest tests pass unchanged in shape; only `base_object`/`object_scope`/confidence values on `object_identity_candidate` edges changed, as intended.
