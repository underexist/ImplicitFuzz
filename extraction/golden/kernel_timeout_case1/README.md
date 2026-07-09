# kernel_timeout_case1

## Scope
- Kernel tree: `/home/xujunru/linux-6.1`
- Translation unit: `io_uring/timeout.c`
- Bitcode: `/tmp/io_uring_timeout.bc`
- Facts: `/tmp/io_uring_timeout.facts.jsonl`

## Candidate Selection
- First-pass candidate scan via `rg` on `->` field accesses.
- Excluded complex patterns (`container_of`, list/hlist/xarray helpers, macro-dominant expressions).
- Selected expression: `timeout->off = off` at `io_uring/timeout.c:480`.

## Ground Truth
See `ground_truth.json`.

Key expectations:
- One `access_fact` hits source line `io_uring/timeout.c:480`.
- `semantic_op == write`, `access_kind == direct_store`, `numeric == [8]`.
- Recover symbolic path `io_timeout.off`.
- Recover source-level field type `u32`.
- `primary_provenance == dwarf`.

## Repro Commands
```bash
# 1) Build timeout object and materialize compile command
cd /home/xujunru/linux-6.1
make LLVM=1 defconfig
make LLVM=1 CC=clang KCFLAGS='-Wno-error=default-const-init-var-unsafe' io_uring/timeout.o V=1

# 2) Emit LLVM bitcode from the recorded timeout command
python3 - <<'PY2'
from pathlib import Path
import subprocess
cmd = Path('io_uring/.timeout.o.cmd').read_text().splitlines()[0].split(':=',1)[1].strip().split(';',1)[0].strip()
cmd = cmd.replace('-c -o io_uring/timeout.o io_uring/timeout.c', '-emit-llvm -c -g -O2 -o /tmp/io_uring_timeout.bc io_uring/timeout.c')
subprocess.run(cmd, shell=True, check=True)
PY2

# 3) Extract facts
cd /home/xujunru/ImplicitFuzz/extraction
./build/implicitfuzz-extract -jsonl-out /tmp/io_uring_timeout.facts.jsonl /tmp/io_uring_timeout.bc

# 4) Schema validate
cd /home/xujunru/ImplicitFuzz
python3 extraction/scripts/validate_facts_schema.py /tmp/io_uring_timeout.facts.jsonl extraction/schema/facts
```

## Prerequisites

`run_kernel_case1.sh` assumes `/tmp/io_uring_timeout.bc` has already been generated from Linux v6.1 `io_uring/timeout.c`. Bitcode generation depends on the kernel tree, `.cmd` files, and `KCFLAGS`; the regression script does not build `.bc` itself. See **Repro Commands** below or `extraction/RUNBOOK.md` for bitcode generation steps.

## Validation

One-command regression (extract + schema + case1 assertions):

```bash
/home/xujunru/ImplicitFuzz/extraction/scripts/run_kernel_case1.sh \
  /tmp/io_uring_timeout.bc \
  /tmp/io_uring_timeout.facts.jsonl
```

Default paths when called with no arguments:

```text
BC=/tmp/io_uring_timeout.bc
OUT=/tmp/io_uring_timeout.facts.jsonl
```

Or run the checker alone:

```bash
python3 /home/xujunru/ImplicitFuzz/extraction/scripts/check_kernel_timeout_case1.py \
  /tmp/io_uring_timeout.facts.jsonl \
  /home/xujunru/ImplicitFuzz/extraction/golden/kernel_timeout_case1/ground_truth.json
```
