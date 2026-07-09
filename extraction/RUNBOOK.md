# Extraction Regression Runbook

Stable entry points for validating the ImplicitFuzz SVF extraction pipeline.

## Phase 1 (all lines)

```bash
/home/xujunru/ImplicitFuzz/extraction/scripts/run_phase1_regression.sh
```

Runs tiny golden + kernel case1/2/3 + optional BTF layout smoke. If `/sys/kernel/btf/vmlinux` is missing, BTF step is skipped with a reason (does not fail the suite).

Expected tail:

```text
[phase1] ALL PASS
```

## Phase 1 ingestion (evidence store smoke)

Import all four Phase 1 JSONL fact sets into SQLite and run sanity queries.

**Prerequisite:** run `run_phase1_regression.sh` first (or each `run_kernel_case*.sh` + `run_golden_test.sh`) so default input files exist.

```bash
/home/xujunru/ImplicitFuzz/extraction/scripts/ingest_phase1_smoke.py
```

Default inputs:

```text
extraction/build/golden-facts.jsonl
/tmp/io_uring_timeout.facts.jsonl
/tmp/io_uring_cancel.facts.jsonl
/tmp/io_uring_kbuf.facts.jsonl
```

Default output: `/tmp/phase1_facts.db` (recreated unless `--keep-db`).

Options:

```bash
python3 extraction/scripts/ingest_phase1_smoke.py --db /tmp/phase1_facts.db
python3 extraction/scripts/ingest_phase1_smoke.py --facts path/to/facts.jsonl --facts other.jsonl
python3 extraction/scripts/ingest_phase1_smoke.py --keep-db   # append to existing DB
```

Expected checks (representative server run):

```text
sqlite counts: {'access_fact': 753, 'call_fact': 189}
symbolic OK: Node.value / int
symbolic OK: Node.next / struct Node *
symbolic OK: io_timeout.off / u32
symbolic OK: io_hash_bucket.list / struct hlist_head
op OK: main alloc call_alloc
op OK: main free call_free
op OK: io_provide_buffers alloc call_alloc
op OK: io_provide_buffers free call_free
phase1 ingestion smoke OK: /tmp/phase1_facts.db
```

Implementation: `src/implicitfuzz/ingestion/` (`ingest.py`, `schema.sql`). No `pytest` required for this smoke entry point.

Full pipeline (extract → ingest):

```bash
extraction/scripts/run_phase1_regression.sh
python3 extraction/scripts/ingest_phase1_smoke.py
```

## Evidence store queries

After `ingest_phase1_smoke.py` creates `/tmp/phase1_facts.db`, use the read-only query CLI to inspect the evidence store.

```bash
python3 extraction/scripts/query_phase1_db.py summary
python3 extraction/scripts/query_phase1_db.py symbolic --limit 20
python3 extraction/scripts/query_phase1_db.py lifecycle --limit 20
python3 extraction/scripts/query_phase1_db.py field io_timeout.off
```

Representative checks:

```text
summary:
  access_fact = 753
  call_fact = 189
  alloc = 6, free = 7, read = 450, write = 290

field io_timeout.off:
  __io_timeout_prep write direct_store io_timeout.off / u32

lifecycle:
  main wrapper alloc/free
  io_provide_buffers alloc/free
```

Implementation: `src/implicitfuzz/ingestion/queries.py` and `extraction/scripts/query_phase1_db.py`.

## Phase 2A Evidence Graph Smoke

Phase 2A derives conservative candidate edges over the Phase 1 SQLite evidence store. These edges are not final dependencies; they are graph seeds for later object identity refinement, gate inference, and execution validation.

Run:

```bash
extraction/scripts/build_evidence_graph_smoke.py
```

Default input:

```text
/tmp/phase1_facts.db
```

If the database does not exist, the script runs `ingest_phase1_smoke.py` first.

Expected:

```text
phase2a evidence graph smoke OK: /tmp/phase1_facts.db
```

Derived edge kinds:

| Edge kind | Meaning |
|-----------|---------|
| `state_write_read_candidate` | Same symbolic field has a write fact before a read fact in the same bitcode unit. This is a state-coupling candidate, not a final implicit dependency. |
| `lifecycle_candidate` | Alloc/free facts appear in the same function and order. This requires later object identity refinement before becoming a dependency edge. |

## Entry Points

| Script | Scope | Generates input? |
|--------|-------|------------------|
| `extraction/scripts/run_phase1_regression.sh` | All Phase 1 extraction + BTF smoke | Partial (tiny only; kernel `.bc` prereq) |
| `extraction/scripts/ingest_phase1_smoke.py` | SQLite ingest + query smoke | No (requires Phase 1 JSONL) |
| `extraction/scripts/query_phase1_db.py` | Read-only SQLite evidence queries | No (requires `/tmp/phase1_facts.db`) |
| `extraction/scripts/build_evidence_graph_smoke.py` | Phase 2A evidence graph seeds | No (requires `/tmp/phase1_facts.db`, creates it if missing) |
| `extraction/scripts/run_golden_test.sh` | Tiny userland case (`testdata/input.c`) | Yes (compiles `.bc`) |
| `extraction/scripts/run_kernel_case1.sh` | Kernel `io_uring/timeout.c` smoke | No (requires prebuilt `.bc`) |
| `extraction/scripts/run_kernel_case2.sh` | Kernel `io_uring/cancel.c` struct GEP | No (requires prebuilt `.bc`) |
| `extraction/scripts/run_kernel_case3.sh` | Kernel `io_uring/kbuf.c` primitive alloc/free | No (requires prebuilt `.bc`) |

## 1. Tiny Golden (userland)

```bash
/home/xujunru/ImplicitFuzz/extraction/scripts/run_golden_test.sh
```

Expected tail output:

```text
golden check OK: 66 facts (7 call_fact, 59 access_fact)
schema validation OK: 66 facts
```

Covers:

- `call_fact` / `access_fact` baseline counts
- struct GEP symbolic recovery (`Node.next`, `Node.value`, `Node.name`, `Node.flags`)
- direct primitive summary (`malloc`/`free`) and wrapper propagation (`main→alloc_node/free_node`)
- schema contract 1.0.0

### Wrapper propagation check (after golden)

```bash
python3 extraction/scripts/report_call_summary.py \
  extraction/build/golden-facts.jsonl
```

Expected summary section:

```text
wrapper call_alloc: 2
wrapper call_free: 2
direct primitive hits: 2
```

See also Section 6 below.

## 2. Kernel Case1 (io_uring/timeout.c)

### Prerequisites

Generate bitcode once from Linux v6.1:

```bash
cd /home/xujunru/linux-6.1
make LLVM=1 defconfig
make LLVM=1 CC=clang KCFLAGS='-Wno-error=default-const-init-var-unsafe' io_uring/timeout.o V=1

python3 - <<'PY'
from pathlib import Path
import subprocess
cmd = Path('io_uring/.timeout.o.cmd').read_text().splitlines()[0].split(':=',1)[1].strip().split(';',1)[0].strip()
cmd = cmd.replace(
    '-c -o io_uring/timeout.o io_uring/timeout.c',
    '-emit-llvm -c -g -O2 -o /tmp/io_uring_timeout.bc io_uring/timeout.c',
)
subprocess.run(cmd, shell=True, check=True)
PY
```

### Run regression

```bash
/home/xujunru/ImplicitFuzz/extraction/scripts/run_kernel_case1.sh \
  /tmp/io_uring_timeout.bc \
  /tmp/io_uring_timeout.facts.jsonl
```

Or with defaults (`BC=/tmp/io_uring_timeout.bc`, `OUT=/tmp/io_uring_timeout.facts.jsonl`):

```bash
/home/xujunru/ImplicitFuzz/extraction/scripts/run_kernel_case1.sh
```

Expected tail output:

```text
schema validation OK: 341 facts
kernel case1 OK: io_uring/timeout.c:480 -> io_timeout.off / u32
```

Covers:

- Real kernel TU bitcode + SVF extraction
- Schema validation on kernel-scale output
- Byte-offset GEP → DWARF member recovery (`io_timeout.off` / `u32`)

## 3. Kernel Case2 (io_uring/cancel.c, struct-typed GEP)

### Why cancel.c

`timeout.c` at `-O2 -g` has no struct-typed GEP in facts (all `i8` byte-offset). Case2 uses `cancel.c` for ordinary struct GEP recovery.

### Prerequisites

```bash
cd /home/xujunru/linux-6.1
make LLVM=1 defconfig
make LLVM=1 CC=clang KCFLAGS='-Wno-error=default-const-init-var-unsafe' io_uring/cancel.o V=1

python3 - <<'PY'
from pathlib import Path
import subprocess
cmd = Path('io_uring/.cancel.o.cmd').read_text().splitlines()[0].split(':=',1)[1].strip().split(';',1)[0].strip()
cmd = cmd.replace(
    '-c -o io_uring/cancel.o io_uring/cancel.c',
    '-emit-llvm -c -g -O2 -o /tmp/io_uring_cancel.bc io_uring/cancel.c',
)
subprocess.run(cmd, shell=True, check=True)
PY
```

### Run regression

```bash
/home/xujunru/ImplicitFuzz/extraction/scripts/run_kernel_case2.sh
```

Expected tail output:

```text
schema validation OK: 142 facts
kernel case2 OK: io_uring/cancel.c:207 -> io_hash_bucket.list / struct hlist_head
```

Covers struct-typed GEP + constant field index → DWARF member (`io_hash_bucket.list`).

## 4. Kernel Case3 (io_uring/kbuf.c, primitive alloc/free)

Phase 1.6: first kernel TU with direct `kzalloc`/`kfree` primitive summary hits.

### Prerequisites

Reuse clang flags from an existing `io_uring/.timeout.o.cmd` (`LLVM=1` build). See `extraction/golden/kernel_kbuf_primitive_case3/README.md`.

### Run regression

```bash
/home/xujunru/ImplicitFuzz/extraction/scripts/run_kernel_case3.sh
```

Expected tail output:

```text
schema validation OK: 393 facts
kernel case3 OK: io_provide_buffers -> call_alloc (primitive); io_uring/kbuf.c:449 -> call_free (primitive); TU hits alloc=3 free=4
```

Call summary:

```bash
python3 extraction/scripts/report_call_summary.py /tmp/io_uring_kbuf.facts.jsonl
```

Expected: `call_alloc` ≥ 3, `call_free` ≥ 4, `wrapper propagation hits: 0`.

## 5. BTF Layout Reference (smoke, no fact changes)

BTF is used only as a **layout cross-check** against existing golden cases. It does not modify `primary_provenance` or fact output.

### Prerequisites

Runtime kernel BTF (default):

```text
/sys/kernel/btf/vmlinux
```

Note: the local `linux-6.1` tree build has `CONFIG_DEBUG_INFO_NONE` and its `vmlinux` has no `.BTF` section. The smoke check uses runtime BTF when available.

### Run cross-check

```bash
python3 /home/xujunru/ImplicitFuzz/extraction/scripts/check_btf_layout.py \
  --btf /sys/kernel/btf/vmlinux \
  --case extraction/golden/kernel_timeout_case1/ground_truth.json \
  --case extraction/golden/kernel_cancel_case2/ground_truth.json
```

Expected output:

```text
kernel_timeout_case1_gep: DWARF io_timeout.off offset=8 bytes, BTF offset=8 bytes -> match
kernel_cancel_case2_struct_gep: DWARF io_hash_bucket.list offset=8 bytes, BTF offset=8 bytes -> match
report: extraction/reports/btf-layout-smoke.md
```

If BTF is unavailable, the script writes a deferred report and exits non-zero without blocking DWARF-based recovery.

## 6. Primitive Summary (Kernel B6 / B6.1)

Direct primitive recognition via `primitive_summary.yaml` → `primitive_summary.json`.

```bash
python3 extraction/scripts/validate_primitive_summary.py
```

Extractor flag:

```bash
-primitive-summary extraction/schema/primitive_summary.json
```

**v0:** alloc/free/free_async (`malloc`, `kmalloc*`, `kfree*`, `call_rcu`, …).

**v0.1:** effects-first entries (no lifecycle): `copy_from_user`/`copy_to_user`, `refcount_*`, `percpu_ref_*`. Extractor maps effects → `usercopy` / `call_arg` (retain/release/read) access facts.

Tiny golden (`run_golden_test.sh`) validates summary schema and asserts `malloc`/`free` → `call_alloc`/`call_free` with `primary_provenance=summary`.

Kernel TUs may have few primitive hits; use call summary report (includes matched callees + unresolved top-N):

```bash
python3 extraction/scripts/report_call_summary.py /tmp/io_uring_timeout.facts.jsonl \
  --report extraction/reports/call-summary-timeout.md
python3 extraction/scripts/report_call_summary.py /tmp/io_uring_cancel.facts.jsonl \
  --report extraction/reports/call-summary-cancel.md
```

**Next (not yet):** wrapper propagation — functions returning alloc results or passing params to free.

**v0 (done):** conservative one-hop SSA wrapper table + fixpoint. Tiny golden asserts `main -> alloc_node/free_node` produce `call_alloc`/`call_free` with `summary_detail.match_kind=wrapper_propagation`. Kernel runs log wrapper hits for stats only.

## 7. Wrapper Propagation (v0, tiny regression)

Built automatically when `-primitive-summary` is loaded. No separate flag.

```bash
extraction/scripts/run_golden_test.sh

python3 extraction/scripts/report_call_summary.py \
  extraction/build/golden-facts.jsonl
```

Expected extractor log:

```text
wrapper summary: alloc_wrappers=1 free_wrappers=1
wrapper propagation hits: alloc=2 free=2
```

Expected call summary:

```text
wrapper call_alloc: 2
wrapper call_free: 2
```

**Kernel:** wrapper hits are logged but not pass criteria. Use call summary for unresolved stats only.

## 8. Phase 1.5 — Kernel Wrapper Candidate Audit

Audit only — does **not** change extractor output.

```bash
python3 extraction/scripts/audit_kernel_wrapper_candidates.py

python3 extraction/scripts/report_call_summary.py /tmp/io_uring_timeout.facts.jsonl
python3 extraction/scripts/report_call_summary.py /tmp/io_uring_cancel.facts.jsonl
```

Report: `extraction/reports/kernel-wrapper-candidates.md`

Current smoke result: **no in-TU alloc/free wrapper candidates** in `timeout.c` or `cancel.c` under v0 rules. `io_free_req` is external and rejected (deferred free list, not direct `kfree`).

Do not implement kernel wrapper propagation until audit finds functions with direct `kmalloc`/`kfree` IR in the TU bitcode.

## Manifest

Machine-readable smoke profile:

```text
extraction/manifests/kernel-timeout-v6.1-smoke.json
extraction/manifests/kernel-cancel-v6.1-smoke.json
extraction/manifests/kernel-kbuf-v6.1-smoke.json
```

Bitcode generation (reproducible, manual prerequisite):

```text
extraction/manifests/kernel-timeout-v6.1-smoke.md
```

Phase 1 measured results and conclusions:

```text
docs/phase1-findings.md
docs/kernel-target-decision.md
docs/confidence-model.md
```

## Upgrade Procedure

Current Phase 1 baseline:

```text
Kernel target: /home/xujunru/linux-6.1
LLVM: 21.1.8
SVF commit: 9e04f986
```

When changing the kernel tree, LLVM build, SVF commit, or extractor build directory:

1. Keep the old working build directory until the new one passes regression.
2. Regenerate kernel bitcode from the kernel `.cmd` files; do not hand-write kernel flags.
3. Run the extraction regression:

   ```bash
   extraction/scripts/run_phase1_regression.sh
   ```

4. Run evidence-store ingestion and query smoke:

   ```bash
   python3 extraction/scripts/ingest_phase1_smoke.py
   extraction/scripts/query_phase1_db.py summary
   ```

5. Update the measured facts and decision notes in:

   ```text
   docs/phase1-findings.md
   docs/kernel-target-decision.md
   extraction/manifests/*.json
   ```

If any line fails, keep the previous Phase 1 baseline and record the failure as an upgrade candidate, not as a replacement.

## Ground Truth

| Case | Directory | Key assertion |
|------|-----------|---------------|
| userland tiny | `extraction/testdata/` + `check_golden_facts.py` | `Node.next` @ `[0,1]` |
| kernel case1 | `extraction/golden/kernel_timeout_case1/` | `io_timeout.off` @ `timeout.c:480` (byte-offset) |
| kernel case2 | `extraction/golden/kernel_cancel_case2/` | `io_hash_bucket.list` @ `cancel.c:207` (struct GEP) |
| kernel case3 | `extraction/golden/kernel_kbuf_primitive_case3/` | `io_provide_buffers` alloc + `kbuf.c:449` free (primitive) |

## Toolchain Paths

Both scripts expect the server-side LLVM 21 + SVF build documented in `docs/svf-extraction-toolchain-notes.md`. If `implicitfuzz-extract` is missing, each script will configure and build `extraction/build/` on first run.
