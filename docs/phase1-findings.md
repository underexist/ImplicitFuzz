# Phase 1 Findings: Extraction Pipeline Smoke Validation

**Date:** 2026-07-09  
**Milestone:** Phase 1 closed loop + Phase 1.6 kernel primitive golden  
**Status:** Four regression lines stable; static extraction capability matrix fixed below.

---

## Summary

Phase 1 established a minimal but contract-valid extraction pipeline:

```text
.bc → implicitfuzz-extract → call_fact / access_fact JSONL → schema validation → golden assertions
```

Two regression lines now cover complementary capabilities:

| Line | Input | What it validates |
|------|-------|-------------------|
| **tiny case** | `extraction/testdata/input.c` | Direct primitive + wrapper propagation, struct GEP → DWARF, schema |
| **kernel case1** | `io_uring/timeout.c` | Byte-offset `i8` GEP → DWARF, kernel-scale schema |
| **kernel case2** | `io_uring/cancel.c` | Struct-typed GEP → DWARF, primitive usercopy hit |
| **kernel case3** | `io_uring/kbuf.c` | Direct kernel primitive alloc/free summary hits |

---

## Phase 1 Capability Matrix

Static extraction layer — what is **in scope** and **measured** per regression line:

| Capability | tiny | timeout.c | cancel.c | kbuf.c |
|------------|------|-----------|----------|--------|
| direct `call_fact` | yes | yes | yes | yes |
| direct load/store `access_fact` | yes | yes | yes | yes |
| struct GEP → DWARF field | yes | no | yes | n/a |
| i8 byte offset → DWARF field | n/a | yes | n/a | n/a |
| `field_type` from DWARF | yes | yes | yes | yes |
| BTF layout cross-check | n/a | yes | yes | n/a |
| primitive summary direct | yes (malloc/free) | no | usercopy yes | **alloc/free yes** |
| wrapper propagation alloc/free | yes | no | no | no |
| schema validation | yes | yes | yes | yes |

**Notes:**

- `timeout.c` at `-O2` has no struct-typed GEP candidates; byte-offset recovery is the proven path (case1).
- `cancel.c` has struct GEP (`io_hash_bucket.list`) but no in-TU `kmalloc`/`kfree` IR.
- `kbuf.c` is the Phase 1.6 primitive alloc/free golden TU (`kzalloc`/`kfree` hits).
- Kernel wrapper propagation is **not** a Phase 1 pass criterion; see Phase 1.5 audit below.

---

## Toolchain Environment

Validated on Linux server (no root required for toolchain install):

| Component | Version / Path |
|-----------|----------------|
| OS | Linux 5.14 (el9 x86_64) |
| LLVM | 21.1.8 (`/home/xujunru/.local/opt/llvm21-rpm/`) |
| SVF | Built against LLVM 21 (`/home/xujunru/implicitfuzz-toolchain/SVF/verify-llvm21-build4`) |
| Z3 | 4.15.4 (`/home/xujunru/implicitfuzz-toolchain/z3-install`) |
| Extractor | `extraction/build/implicitfuzz-extract` |
| Schema | `extraction/schema/facts/` v1.0.0 |

See `docs/svf-extraction-toolchain-notes.md` for environment setup history and pitfalls.

---

## Tiny Case Results

**Command:**

```bash
extraction/scripts/run_golden_test.sh
```

**Input:** `extraction/testdata/input.c` (compiled at `-O0 -g`)

| Metric | Value |
|--------|-------|
| Total facts | **66** |
| `call_fact` | 7 |
| `access_fact` | 59 |
| Schema validation | **OK** |
| Golden check | **OK** |
| Primitive summary hits | alloc=1, free=1 |
| Wrapper propagation hits | alloc=2, free=2 |

### `numeric_kind` distribution (access_fact)

| Kind | Count |
|------|-------|
| `gep_offsets` | 14 |
| `whole_object` | 43 |

### Symbolic recovery (DWARF, struct-typed GEP)

| Symbolic path | Numeric | `field_type` |
|---------------|---------|--------------|
| `Node.next` | `[0,1]` | `struct Node *` |
| `Node.value` | `[0,0]` | `int` |
| `Node.name` | `[0,2]` | `const char *` |
| `Node.flags` | `[0,3]` | `unsigned long` |

All four paths: `primary_provenance = dwarf`, `confidence = high`.

---

## Kernel Case Results

**TU:** `io_uring/timeout.c` from Linux **v6.1** (`/home/xujunru/linux-6.1`)  
**Bitcode:** `/tmp/io_uring_timeout.bc` (219 KB, `-O2 -g -emit-llvm`)  
**Command:**

```bash
extraction/scripts/run_kernel_case1.sh
```

| Metric | Value |
|--------|-------|
| Total facts | **341** |
| `call_fact` | 75 |
| `access_fact` | 268 |
| Schema validation | **OK** |
| Kernel case1 golden | **OK** |

### `numeric_kind` distribution (access_fact)

| Kind | Count | Share |
|------|-------|-------|
| `gep_offsets` | **240** | 89.6% |
| `whole_object` | 28 | 10.4% |

### `primary_provenance` distribution (access_fact)

| Provenance | Count |
|------------|-------|
| `dwarf` | 109 |
| `numeric_fallback` | 159 |

After B4.1 byte-offset recovery, **109 / 268** access facts carry DWARF symbolic paths in this TU. Top recovered paths include `io_kiocb.link`, `io_kiocb.flags`, `list_head.prev`, `io_kiocb.ctx`, and `io_timeout.head`.

### Golden case: byte-offset GEP

| Field | Value |
|-------|-------|
| Source | `io_uring/timeout.c:480` |
| Expression | `timeout->off = off` |
| Function | `__io_timeout_prep` |
| IR pattern | `getelementptr i8, ptr %timeout, i64 8` |
| `numeric_kind` | `gep_offsets` |
| `access_path_numeric` | `[8]` |
| `access_path_symbolic` | **`io_timeout.off`** |
| `field_type` | **`u32`** |
| `primary_provenance` | `dwarf` |

Recovery mechanism: local variable `timeout` (`struct io_timeout *`) from `DISubprogram` context + DWARF member offset match (`off` at byte 8).

---

## Regression Entry Points

```text
extraction/scripts/run_phase1_regression.sh   # all Phase 1 lines + BTF smoke
extraction/scripts/run_golden_test.sh         # tiny userland
extraction/scripts/run_kernel_case1.sh        # kernel byte-offset GEP (timeout.c)
extraction/scripts/run_kernel_case2.sh        # kernel struct GEP (cancel.c)
extraction/scripts/run_kernel_case3.sh        # kernel primitive alloc/free (kbuf.c)
```

Manifest: `extraction/manifests/kernel-timeout-v6.1-smoke.json`  
Bitcode generation: `extraction/manifests/kernel-timeout-v6.1-smoke.md`  
Full runbook: `extraction/RUNBOOK.md`

---

## Conclusions

1. **Pipeline is contract-valid.** Tiny (66) and kernel case1/case2/case3 fact sets pass JSON Schema 1.0.0 without relaxation.

2. **Field access is not systematically broken by opaque pointers.** In `io_uring/timeout.c` at `-O2`, **89.6%** of `access_fact` records retain `numeric_kind = gep_offsets`. The IR is heavily `i8` byte-offset shaped, but numeric offsets are still recoverable.

3. **Two symbolic recovery paths are proven:**
   - **B1/B2:** struct-typed GEP indices → DWARF member name + source type (tiny case)
   - **B4.1:** `i8` byte-offset GEP → DWARF member offset from local context (kernel case1)

4. **Kernel DWARF is usable without BTF.** For the tested TU, DWARF debug info alone supports symbolic field recovery for a significant fraction of accesses.

5. **Regression is reproducible.** Three one-command scripts + manifests + ground truth JSON provide a stable baseline.

6. **Primitive + wrapper layer is bounded.** Direct summary and conservative wrapper propagation work on tiny; kernel smoke TUs have no in-TU wrapper candidates under v0 rules.

### Kernel case2 (struct-typed GEP, cancel.c)

| Field | Value |
|-------|-------|
| TU | `io_uring/cancel.c` |
| Facts | 142 (38 call, 104 access), schema OK |
| Source | `cancel.c:207` — `INIT_HLIST_HEAD(&table->hbs[i].list)` |
| IR | `getelementptr %struct.io_hash_bucket, ..., i32 1` |
| `access_path_symbolic` | `io_hash_bucket.list` |
| `field_type` | `struct hlist_head` |

Note: `timeout.c` at `-O2` has zero struct-typed GEP candidates; case2 uses `cancel.c`.

### BTF layout reference (Kernel B5 smoke)

Independent cross-check only; facts still use `primary_provenance: dwarf`.

| Case | DWARF field | Byte offset | BTF match |
|------|-------------|-------------|-----------|
| case1 | `io_timeout.off` | 8 | yes |
| case2 | `io_hash_bucket.list` | 8 | yes |

BTF source: `/sys/kernel/btf/vmlinux` (runtime). Local `linux-6.1` build has no embedded BTF (`CONFIG_DEBUG_INFO_BTF` not enabled).

Script: `extraction/scripts/check_btf_layout.py`  
Report: `extraction/reports/btf-layout-smoke.md`

### Primitive summary v0 (Kernel B6)

| Item | Status |
|------|--------|
| Schema | `primitive_summary.schema.json` |
| Source | `primitive_summary.yaml` (13 v0 entries) |
| Tiny golden | `malloc`/`free` → `call_alloc`/`call_free`, `primary_provenance=summary` |
| Kernel timeout/cancel | 0 primitive hits (expected; kmalloc/kfree not in these TUs) |

Reports: `extraction/reports/call-summary-timeout.md`, `call-summary-cancel.md`

### Primitive summary v0.1 (Kernel B6.1)

| Item | Status |
|------|--------|
| Entries | 26 total (+13 v0.1: usercopy, refcount, percpu_ref) |
| Schema | `semantic_op` extended with `read`/`write` for effects-first entries |
| Tiny golden | no regression (alloc/free summary hits unchanged) |
| Kernel cancel | `_copy_from_user` → 1 `usercopy` hit (`primary_provenance=summary`) |
| Kernel timeout | 0 summary hits (no refcount/usercopy in this TU) |

**Intentionally not added (semantic uncertain):** `INIT_HLIST_HEAD`, `hrtimer_*`, `io_req_*`, spin locks.

**Deferred:** wrapper propagation (seed + fixpoint) until v0.1 dictionary is stable.

Reports regenerated with matched-callees section; cancel unresolved 38→37 direct calls.

### Wrapper propagation v0 (tiny only)

| Item | Status |
|------|--------|
| Rules | alloc: return ← alloc primitive/wrapper; free: formal param → free primitive/wrapper; one alloca hop |
| Fixpoint | yes (tiny converges in one round) |
| Tiny golden | `alloc_node→malloc`, `free_node→free` (direct); `main→alloc_node/free_node` (wrapper) |
| Provenance audit | `summary_detail.match_kind=wrapper_propagation`, `confidence=medium` |
| Kernel timeout/cancel | 0 wrapper hits (expected) |

### Phase 1.5 — kernel wrapper candidate audit

**Goal:** audit only; do not implement kernel wrapper until explicit IR patterns exist.

```bash
python3 extraction/scripts/audit_kernel_wrapper_candidates.py
```

Report: `extraction/reports/kernel-wrapper-candidates.md`

| Finding | Result |
|---------|--------|
| In-TU alloc/free wrapper candidates (timeout.c) | **0** / 19 functions scanned |
| In-TU alloc/free wrapper candidates (cancel.c) | **0** / 7 functions scanned |
| `io_free_req` (external, 2 call sites) | **rejected** — body queues to `locked_free_list`, not direct `kfree` |
| `io_alloc_async_data` (external) | cross-TU only; not audited in this smoke |

**Recommendation:** defer kernel wrapper propagation; do not hard-search wrappers in these TUs.

### Phase 1.6 — kernel alloc/free primitive audit

**Goal:** first real kernel TU with direct `kzalloc`/`kfree` primitive summary hits (no new wrapper rules).

```bash
extraction/scripts/run_kernel_case3.sh
```

Report: `extraction/reports/kernel-alloc-free-primitive-audit.md`

| Item | Result |
|------|--------|
| Selected TU | `io_uring/kbuf.c` (551 lines, 6 grep hits) |
| Facts | 393, schema OK |
| primitive alloc / free | **3 / 4** |
| Golden | `io_provide_buffers` → `call_alloc`; `kbuf.c:449` → `call_free` |

Alternates scanned: `poll.c` (alloc=2, free=3), `rsrc.c` (alloc=9, free=21) — stats only.

---

### Indirect call resolution (call_fact gap fix)

Prior to this fix, `call_fact` was only emitted for direct calls (`cb->getCalledFunction()` non-null). Indirect calls (function pointers, op-table/callback dispatch) produced **no fact at all** — a silent call-graph gap, confirmed on real kernel code: `io_uring/io_uring.c` alone has 33 genuine indirect call sites (verified independently via `llvm-dis` + grep for register-target `call` instructions) that were completely invisible to the extraction layer.

Fixed by emitting `call_fact` for indirect calls too (`is_indirect: true`), resolving candidate callees from **SVF's own indirect call graph** (`CallGraph::hasIndCSCallees`/`getIndCSCallees`, built from Andersen points-to on the called operand for the whole `.bc` unit) rather than re-deriving resolution independently — so "resolved" here means exactly what SVF's own `getNumOfResolvedIndCallEdge()` counts, no new heuristic invented. `resolution_method: "pta"`; unresolved candidates get `callee: "<indirect_unresolved>"` and `confidence: low` (schema requires a non-null `callee` string even when nothing resolved); resolved candidates get `confidence: medium`.

Measured on `io_uring.c`: 33 indirect call_fact, **0 resolved** — all 33 are in `__traceiter_*` tracepoint infrastructure functions, whose actual callback targets are registered at runtime by other subsystems (`register_trace_io_uring_create()` etc.), not statically determinable from this TU (or arguably from any static analysis). This is a correct, honest "unresolved," not a bug. Smoke-tested on 5 other large TUs (`net.c`, `rsrc.c`, `poll.c`, `rw.c`, `io-wq.c`): `io-wq.c` resolved 2/10 indirect calls with real candidates, e.g. a match-callback resolving to a single candidate (`io_wq_work_match_item`) and a for-each-worker callback correctly resolving to **two** candidates (`io_wq_worker_cancel`, `io_wq_worker_wake`) rather than guessing one — validates the mechanism works when SVF's per-TU points-to actually has the information.

**Known limitation, not yet closed:** resolution is bounded by the same per-TU scope as everything else in this pipeline (see "Known Limitations" below) — a function pointer whose value flows in from another `.c` file cannot resolve here regardless of how the call graph shakes out at true whole-program scope. The op-table dispatch pattern (`io_op_defs[opcode].issue(req)`) that originally motivated this fix was **not** found as a literal indirect-call instruction in `io_uring.c`'s compiled `-O2` bitcode in this pass — worth a follow-up look at whether `-O2` lowers it to something else (e.g. a jump table with direct calls) rather than assuming it is simply missing.

**Bug found and fixed while building this:** `LLVMModuleSet::getLLVMValue()` (used by Phase 2C tier-2's SVF-object reverse mapping, and now by indirect-call candidate name resolution) is guarded only by `assert()` in SVF's header — compiled out entirely in this project's `-DCMAKE_BUILD_TYPE=Release` build. Calling it on an SVF object with no backing LLVM value (common for `-O2` real kernel code with many extapi-modeled/synthetic object kinds that never appear in the small golden TUs) is undefined behavior; **segfaulted on `io_uring.c`** the first time tier-2 ran against real, large-scale code (Phase 2C's regression set — tiny + 3 kernel goldens — was too small to trigger it). Fixed by checking the paired `hasLLVMValue()` before every `getLLVMValue()` call. Worth remembering for any future SVF API use in this codebase: several SVF accessors follow this same assert-only-guard pattern (`getObjectNode()` is another one, deliberately avoided in Phase 2C tier-1 for this reason) — always look for and use the paired `has*()` check rather than trusting the assert.

## Known Limitations (Phase 1 scope)

- No BTF integration in extractor output yet (layout cross-check only via `check_btf_layout.py`).
- No `container_of` / `list_entry` recovery.
- Byte-offset recovery requires identifiable `base->field` in source line + matching local DWARF variable.
- Kernel bitcode generation is manual (not automated in regression script).
- `whole_object` fallback still dominates in tiny case (expected for alloc/free and non-GEP accesses).
- Wrapper propagation limited to direct SSA + one alloca hop; no cross-TU bodies in per-file kernel bitcode.
- Kernel wrapper propagation deferred (Phase 1.5 audit found no in-TU candidates in timeout/cancel smoke).
- **Every SVF analysis (Andersen points-to, indirect call resolution, alias_fact) is scoped to a single `.bc` compilation unit.** Cross-TU calls, cross-TU points-to, and cross-TU indirect-call resolution do not exist yet; the 26-file io_uring experiment is a union of independent per-TU analyses, not a whole-program one.
- Indirect call resolution (new) inherits this same per-TU boundary; runtime-registered callbacks (tracepoints, module-registered handlers) are not resolvable by any static per-TU analysis regardless.

---

## Next Steps (recommended order)

```text
1. [done] Phase 1 findings document
2. [done] Bitcode generation record for kernel-timeout-v6.1-smoke
3. **[done] kernel case2: ordinary struct-typed GEP** — `cancel.c:207` → `io_hash_bucket.list`
4. **[done] BTF layout reference smoke** — cross-check case1/case2 offsets via runtime BTF
5. **[done] primitive_summary v0** — alloc/free/free_async direct primitive recognition
6. **[done] primitive_summary v0.1** — effects-first usercopy/refcount/percpu_ref; cancel `_copy_from_user` hit
7. **[done] wrapper propagation v0** — tiny `alloc_node`/`free_node` + `main` call sites
8. **[done] Phase 1.5 kernel wrapper audit** — no in-TU candidates in timeout/cancel
9. **[done] Phase 1.6 kernel primitive audit** — kbuf.c alloc/free golden (case3)
10. **[done] Phase 1 final regression** — `run_phase1_regression.sh` all lines pass
11. **[done] Phase 1 ingestion smoke** — `ingest_phase1_smoke.py` → `/tmp/phase1_facts.db`
12. **[done] Phase 2A evidence graph seeds** — derive `evidence_node` and `evidence_edge` tables from `/tmp/phase1_facts.db`
13. **[done] Phase 2B identity/dependency candidates** — weak object identity and lifecycle explicit-dependency candidates over the evidence graph
14. **[done] Phase 2C tier-1 object identity refinement** — real `base_object` (global/formal_param/allocation_site) via direct GEP/local-slot resolution, replacing the synthetic stub.
15. **[done] Phase 2C tier-2 object identity refinement** — SVF Andersen points-to refines `formal_param`/`synthetic` toward concrete allocation_site/global (per v3 §5.6, those two scopes are relational evidence only); emits `alias_fact` for genuinely ambiguous multi-object cases; new `derive_pointsto_identity_edges` links facts across function boundaries on points-to intersection. `object_identity_candidate`: 642→517 (`same_function_symbolic_object_prefix`) + 6 new (`pointsto_intersection_andersen`); confidence 489 low / 34 medium (corrected — `formal_param` alone does not grant medium). See `docs/phase2c-object-identity-refinement.md`.
16. **[done] indirect call_fact** — call_fact now emitted for indirect calls (`is_indirect=true`, `resolution_method=pta`) via SVF's own indirect call graph; fixed a real call-graph blind spot (33 indirect sites on `io_uring.c` previously produced zero facts). Also fixed a `getLLVMValue()` assert-only-guard segfault this surfaced on real large-TU code (see "Indirect call resolution" section above).
17. [next] branch_fact / gate_seed_fact for target state gates
18. [later] kernel wrapper propagation — only if audit finds direct primitive patterns in TU bitcode
19. [later] BTF in provenance / main recovery chain
20. [later] container_of / list_entry
21. [later] unify wrapper-call-site vs. Andersen points-to granularity (see phase2c doc's "Known, accepted limitation")
22. [later] investigate why op-table dispatch (`io_op_defs[opcode].issue`) doesn't appear as a literal indirect call in `io_uring.c`'s `-O2` IR
```

**Phase 1 complete.** Do not expand C++ extractor scope until ingestion validates fact consumption.

---

## Final Regression Snapshot

Captured by `extraction/scripts/run_phase1_regression.sh` on this server:

| Field | Value |
|-------|-------|
| Date | 2026-07-08 (UTC) |
| LLVM | 21.1.8 |
| Kernel tree | linux-6.1 |
| SVF commit | `9e04f986` |
| tiny facts | 66 |
| timeout facts | 341 |
| cancel facts | 142 |
| kbuf facts | 393 |
| BTF smoke | match (`/sys/kernel/btf/vmlinux`) |

**Pass criteria (all met):**

```text
tiny:     golden + schema OK
case1:    timeout.c:480 -> io_timeout.off / u32
case2:    cancel.c:207 -> io_hash_bucket.list / struct hlist_head
case3:    kbuf primitive alloc/free OK
BTF:      match (or skipped with reason if runtime BTF unavailable)
```

**Conclusion:** Phase 1 static extraction layer is ready for next-stage dependency graph ingestion (SQLite / evidence store). Ingestion smoke entry: `extraction/scripts/ingest_phase1_smoke.py` (see `extraction/RUNBOOK.md`).

Phase 2A evidence graph seeds are tracked in `docs/phase2a-evidence-graph.md`; Phase 2B identity/dependency candidates are tracked in `docs/phase2b-evidence-identity.md`; Phase 2C object identity refinement (tier-1 direct resolution + tier-2 SVF Andersen points-to, replacing the synthetic stub) is tracked in `docs/phase2c-object-identity-refinement.md`. Smoke entry: `extraction/scripts/build_evidence_graph_smoke.py`.

---

## References

- Toolchain setup: `docs/svf-extraction-toolchain-notes.md`
- Regression runbook: `extraction/RUNBOOK.md`
- Kernel smoke manifest: `extraction/manifests/kernel-timeout-v6.1-smoke.json`
- Kernel case1 ground truth: `extraction/golden/kernel_timeout_case1/ground_truth.json`
- Phase 1.5 audit: `extraction/reports/kernel-wrapper-candidates.md`
- Phase 1.6 audit: `extraction/reports/kernel-alloc-free-primitive-audit.md`
- Kernel case3 ground truth: `extraction/golden/kernel_kbuf_primitive_case3/ground_truth.json`
- Ingestion smoke: `extraction/scripts/ingest_phase1_smoke.py`
