# Phase 2 Status Overview — branch `phase2b-evidence-identity`

**Date:** 2026-07-10
**Scope note:** the branch name says "phase2b" but the branch has outgrown it — it now carries Phase 2B (identity candidates), 2C (object-identity refinement), 2D (state-gate skeletons), plus foundation upgrades (indirect calls, opcode/entry attribution) and a static-analysis reuse evaluation. This file is the bird's-eye synthesis; per-phase deep-dives are the individual `docs/phase2*.md`.

This branch builds **研究内容一** of the design (`docs/purpose.md`): the traceable access-evidence database and its two derived views. It does **not** yet touch the LLM judgement layer or 研究内容二 (generation/verification).

---

## 代码功能总结 (What the code does, by component)

### End-to-end pipeline

```
kernel .c ──(Kbuild .o.cmd)──> clang -emit-llvm -O2 -g ──> .bc
  ──> implicitfuzz-extract  (C++, SVF/LLVM-21)     ──> JSONL facts (6 types)
  ──> validate_facts_schema.py                     ──> schema-valid facts
  ──> ingestion/ingest.py::ingest_jsonl            ──> SQLite (per-fact-type tables)
  ──> evidence/graph.py::build_evidence_graph      ──> evidence_node/edge (candidate edges)
  ──> evidence/gates.py::derive_gate_seed_candidates ──> gate_seed_candidate
  ──> ingestion/queries.py · query_phase1_db.py · report_*  ──> inspection
```

### 1. Static extractor — `extraction/src/implicitfuzz-extract.cpp` (~2360 lines, per-`.bc`)

Builds SVF Andersen points-to + SVFG over one bitcode unit, then emits facts:

| Emitter | Produces |
|---|---|
| `writeCallFact` / `writeIndirectCallFact` | call graph — direct calls; indirect calls resolved via SVF's own indirect call graph (`getIndCSCallees`), mostly unresolved per-TU |
| `writeAccessFact` | field accesses (load/store) with DWARF symbolization (struct GEP + `i8` byte-offset); primitive alloc/free/free_async/usercopy/refcount hits; one-hop wrapper propagation |
| `resolveBaseObjectDirect` (tier-1) + `resolveBaseObjectViaPointsTo` (tier-2) | object identity for each access — global / formal_param / allocation_site, refined by Andersen points-to |
| `writeAliasFact` | multi-object Andersen points-to sets (`pta_kind=andersen`) |
| `scanDispatchTables` / `writeEntryFact` | reads const function-pointer dispatch tables (`io_op_defs`) → opcode→handler `entry_fact`; roles from DWARF by byte offset |
| `collectConditionLoads` / `writeBranchFact` | conditional/switch branches → gated object fields via bounded branch-local backward slice |

Supporting indices: `PrimitiveSummaryIndex` (semantic dictionary of alloc/free/… callees), `WrapperSummaryIndex` (one-hop alloc/free wrappers), `DwarfStructIndex` (DWARF struct/member resolution, incl. byte-offset + const-qualified array element types).

### 2. Ingestion — `src/implicitfuzz/ingestion/`

- `ingest.py::ingest_jsonl` — JSON-Schema-validated JSONL → SQLite; one table per fact type; nested structures stored as JSON columns; fact types auto-discovered from the schema dir.
- `queries.py` — read-only query API: `list_symbolic_accesses`, `list_lifecycle_ops`, `list_accesses_by_field`, `export_summary`.

### 3. Evidence graph derivation — `src/implicitfuzz/evidence/graph.py`

`build_evidence_graph` derives access nodes + four candidate edge kinds:
`derive_state_flow_edges` (write→read coupling on a field), `derive_lifecycle_edges` (alloc≺free same function), `derive_object_identity_edges` (same-function symbolic prefix) + `derive_pointsto_identity_edges` (Andersen points-to intersection, cross-function), `derive_explicit_dependency_edges` (alloc≺free promoted). All are **candidates**, not final dependencies.

### 4. State-gate derivation — `src/implicitfuzz/evidence/gates.py`

`derive_gate_seed_candidates` — for each `branch_fact` gated on a *written* (state-carrier) field, emits a `gate_seed_candidate` (target function, gated fields, related objects/access facts, static predicate summary). Low-confidence static skeletons awaiting LLM predicate inference.

### 5. Schema — `extraction/schema/`

6 fact schemas (`entry`/`call`/`access`/`alias`/`branch`/`gate_seed`) + `common` envelope; `primitive_summary` semantic dictionary; `manifest`/`run_profile`.

### 6. Regression, smoke & tooling — `extraction/scripts/`, `tests/`

- Regression: `run_phase1_regression.sh` (tiny golden + kernel case1/2/3/5 + BTF layout smoke); per-case golden checkers; `validate_facts_schema.py`, `validate_primitive_summary.py`.
- Smoke/pipeline: `ingest_phase1_smoke.py`, `build_evidence_graph_smoke.py`.
- Inspection: `query_phase1_db.py`, `report_call_summary.py`.
- `pytest`: 23 tests (evidence graph, gate seeds, ingestion, entry_fact schema).

### Capability snapshot

- **Can now:** per-TU field-level access facts; object identity (access-path + points-to/container tiers); call graph incl. indirect (per-TU); opcode→handler attribution (io_uring); state-gate anchors (branch ↔ field ↔ object).
- **Cannot yet:** cross-TU / whole-program closure; fd-indirection identity tier; LLM predicate inference; 反查 (gated field → prior write call); generation / execution verification (研究内容二).

---

## Timeline (19 commits + this doc)

| Phase / theme | What | Commits | Doc |
|---|---|---|---|
| 2A/2B base (pre-session) | Phase 1 extraction + evidence graph seeds; identity/lifecycle/explicit candidates | `1c58ca7`..`7c063fa` | `phase2a-*.md`, `phase2b-evidence-identity.md` |
| **2C tier-1** | real `base_object` (global/formal_param/allocation_site) via direct pointer-chain resolution, replacing the synthetic stub | `91223d8`, `fdf9904` | `phase2c-object-identity-refinement.md` |
| **2C tier-2** | SVF Andersen points-to refines formal_param/synthetic toward concrete objects; emits `alias_fact`; cross-function `pointsto_intersection_andersen` identity edges. Corrected a confidence overclaim per v3 §5.6 | `487bdde` | same |
| **indirect calls** | `call_fact` for indirect calls (`is_indirect`, `resolution_method=pta`) via SVF's indirect call graph; fixed a real `getLLVMValue()` assert-only-guard segfault on large TUs | `4989d9c` | `phase1-findings.md` §"Indirect call resolution" |
| **reuse eval** | evaluated Unias (hybrid alias) & KallGraph (indirect calls); found `io_op_defs` is a readable const table | `be333da`, `8144f9e` | `related-work-static-reuse.md`, plan under `superpowers/plans/` |
| **Part 1: entry_fact** | read `io_op_defs` const dispatch table → 49 opcode→handler entry_facts (opcode attribution); schema + case5 golden + ingestion | `0d57d5f`, `3597af7`, `bf468c2` | plan; `phase1-findings.md` item 17 |
| **Part 2: state gates** | `branch_fact` (object-field-gated branches) + `gate_seed_candidate` (derived state-gate skeletons) | `9b9af6f`, `8d68d3b`, `3116a50` | `phase2d-state-gates.md` |

---

## What the evidence database now produces

**Extracted facts** (per-TU, from real LLVM IR):

| Fact | Status | Notes |
|---|---|---|
| `access_fact` | mature | alloc/read/write/free + field symbolization (~35% DWARF-symbolic) |
| `call_fact` | mature + indirect | direct calls; indirect calls now emitted (`is_indirect`, mostly unresolved per-TU — by design, LLM/whole-program fills residual) |
| `alias_fact` | new (2C tier-2) | Andersen multi-object points-to sets, `pta_kind=andersen` |
| `entry_fact` | new (Part 1) | `op_dispatch`: 49 opcodes → prep/issue/cleanup/... handlers from `io_op_defs` |
| `branch_fact` | new (Part 2) | conditional/switch branches ↔ gated object fields (bounded backward slice) |

**Derived tables** (SQL/Python over the facts, not schema-bound):

| Table | Status | Notes |
|---|---|---|
| `evidence_node` / `evidence_edge` | mature | access nodes + candidate edges |
| `object_identity_candidate` | refined (2C) | `same_function_symbolic_object_prefix` + `pointsto_intersection_andersen` |
| `state_write_read_candidate` | mature | write→read coupling on same field (state-machine raw material) |
| `lifecycle_candidate` / `explicit_dependency_candidate` | thin | alloc≺free only (per-TU limits alloc/free visibility) |
| `gate_seed_candidate` | new (2D) | state-gate skeletons (branch gated on a written field) |

## Current numbers

**4-TU regression smoke DB** (tiny + timeout + cancel + kbuf):
access 770, call 191, alias 6, branch 276, evidence_edge 813 (object_identity 524, state_write_read 277, lifecycle 6, explicit_dep 6), gate_seed_candidate 65 / 26 functions. `object_scope`: allocation_site 137, formal_param 264, global 10, synthetic 359.
_Note: entry_fact = 0 here — the default smoke ingest set does not include opdef.c; entry_fact is produced and validated separately by kernel case5._

**Full 26-TU io_uring corpus** (rescan this session, representative scale):
access 6690, call 2776 (incl. 60 indirect, 2 resolved), alias 298, object_identity 7432 (5655 same-function + 1777 points-to-intersection). ~14% of access_facts now have identity-grade object_scope (allocation_site+global) vs 0% before 2C.

**opdef.c case5:** 137 entry_fact rows, 49 opcodes; opcode 22 (READ_FIXED).issue = `io_read`.

## Where we are on the design's architecture

```
访问证据库 (evidence DB) ........................ BUILT (facts + derived tables)
  ├─ 访问事实表 ................................. done
  ├─ 对象身份等价表 (fd/容器/访问路径 三档) ...... PARTIAL — access-path + points-to(容器) tiers done; fd tier not yet
  ├─ 入口元信息表 (entry) ....................... done for io_uring op dispatch (49 opcodes)
  └─ alias/分配释放/字段元信息 .................. present
显式依赖视图 (alloc≺read/write/free 偏序) ........ THIN — alloc≺free only; needs cross-TU + fd/容器 identity
对象状态机视图 / 状态门控 ........................ STATIC SKELETON done (branch_fact + gate_seed_candidate)
LLM 受限判读 (补关联 + 推门控谓词) + 三道校验 ..... NOT STARTED  ← the novelty's other half
反查证据库 (门控字段 → 前序写调用) ............... NOT STARTED
研究内容二 (装配 / syz-executor 执行 / 置信度反馈) . NOT STARTED
```

## Key decisions & self-corrections (this session)

- **`formal_param` is relational evidence only** (v3 §5.6) — reverted an initial overclaim that granted it medium identity confidence; tier-2 points-to is the intended refinement path.
- **gate_seed as a derived table** (`gate_seed_candidate`), not the schema-bound `gate_seed_fact` — the latter is reserved for post-LLM finalized gates.
- **const dispatch-table read beats KallGraph for io_uring's critical path** — the opcode dispatch is a readable const table (cheap, precise, LLVM-21-native); heavy whole-program tools (Unias/KallGraph) are conditional-future for the residual, and share a whole-kernel-bitcode prerequisite.
- **Real-code robustness:** fixed a Release-build segfault from SVF's assert-only-guarded `getLLVMValue()`; handle const-qualified array element types + struct padding/bitfields via DWARF byte-offset lookup.

## Known boundaries

- **Per-TU** is the hard analysis boundary (Andersen, points-to, indirect resolution all single-`.bc`); the 26-file corpus is a union of per-TU analyses. Cross-TU closure is, by design, partly the LLM's job.
- Indirect-call resolution is mostly unresolved per-TU (tracepoints are runtime-registered; residual heap-callbacks are LLM/whole-program territory).
- `gate_seed_candidate` are per-TU static skeletons; the *writing* (prior) call for a gated field is often cross-TU — the "反查" step, not yet built.
- fd-indirection identity tier (io_uring register/enter via fd) not yet modeled.

## Next (in `phase1-findings.md` Next Steps)

1. 反查: join `gate_seed_candidate` gated fields → write `access_fact`s across the corpus → candidate prior calls.
2. LLM constrained judgement: infer gate predicates (premise/activation/param-align) under code slices, with 3-gate hallucination validation (field-existence reconciliation against the fact ledger).
3. (foundation, lower priority) fd-identity tier; whole-program indirect residual (KallGraph, conditional).

## Verification (branch tip `3116a50`)

`run_phase1_regression.sh`: ALL PASS (tiny + kernel case1/2/3/5 + BTF). `pytest`: 23 passed. Working tree clean.
