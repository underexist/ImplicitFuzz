# Kernel Target and Toolchain Decision

**Date:** 2026-07-09
**Scope:** Phase 1 static extraction smoke and evidence-store validation.

## Decision

Phase 1 uses the local Linux **v6.1** tree as the kernel validation target:

```text
/home/xujunru/linux-6.1
```

The locked smoke translation units are:

| Case | Translation unit | Purpose |
|------|------------------|---------|
| case1 | `io_uring/timeout.c` | `i8` byte-offset GEP -> DWARF field recovery |
| case2 | `io_uring/cancel.c` | struct-typed GEP -> DWARF field recovery |
| case3 | `io_uring/kbuf.c` | direct kernel primitive alloc/free summary hits |

The validated toolchain is:

| Component | Decision |
|-----------|----------|
| LLVM | 21.1.8 |
| SVF | commit `9e04f986`, built against LLVM 21.1.8 |
| Kernel build mode | `LLVM=1 CC=clang`, real `.cmd` command reused |
| IR mode | `-emit-llvm -c -g -O2` |
| Kernel warning workaround | `KCFLAGS='-Wno-error=default-const-init-var-unsafe'` |

## Rationale

Linux v6.6 was the original preferred target, but shallow clone attempts timed out on the server. Linux v6.1 was already available locally, contains the required `io_uring` translation units, and is sufficient for Phase 1 because this milestone validates the extraction pipeline rather than a version-specific kernel bug.

LLVM 21.1.8 is accepted for Phase 1 even though earlier planning text mentioned 21.1.0. The important invariant is that `clang`, `llvm-config`, `llvm-link`, `opt`, SVF CMake, and the extractor all use the same LLVM family. This invariant has been validated by build output and by the four Phase 1 regression lines.

The kernel bitcode commands are derived from the kernel-generated `.cmd` files. We do not hand-write kernel compile flags.

## Acceptance Gates

Any future change to the kernel target, LLVM version, SVF commit, or extractor build directory must pass:

```bash
extraction/scripts/run_phase1_regression.sh
python3 extraction/scripts/ingest_phase1_smoke.py
extraction/scripts/query_phase1_db.py summary
```

The expected Phase 1 facts after ingestion are:

```text
access_fact = 753
call_fact   = 189
```

The query smoke must still observe:

```text
Node.value
Node.next
io_timeout.off
io_hash_bucket.list
main alloc/free wrapper hits
io_provide_buffers alloc/free primitive hits
```

If these gates pass, update:

```text
docs/phase1-findings.md
docs/kernel-target-decision.md
extraction/manifests/*.json
```

If they fail, keep the previous target/toolchain as the Phase 1 baseline.
