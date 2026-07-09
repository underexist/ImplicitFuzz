# kernel-timeout-v6.1-smoke: Bitcode Generation

Machine-readable manifest: [`kernel-timeout-v6.1-smoke.json`](kernel-timeout-v6.1-smoke.json)

This document records how to reproduce `/tmp/io_uring_timeout.bc`. The regression script `run_kernel_case1.sh` **does not** generate bitcode; it only validates an existing `.bc`.

---

## Prerequisites

| Item | Value |
|------|-------|
| Kernel tree | `/home/xujunru/linux-6.1` (Linux v6.1) |
| Source TU | `io_uring/timeout.c` |
| Compiler | `clang` via `make LLVM=1 CC=clang` |
| Output bitcode | `/tmp/io_uring_timeout.bc` |

Toolchain (LLVM 21 + SVF) must be available separately; see `docs/svf-extraction-toolchain-notes.md`.

---

## Step 1: Kernel defconfig

```bash
cd /home/xujunru/linux-6.1
make LLVM=1 defconfig
```

This produces `.config` and build infrastructure needed for per-object compile commands.

---

## Step 2: Build `timeout.o` and capture compile command

```bash
make LLVM=1 CC=clang \
  KCFLAGS='-Wno-error=default-const-init-var-unsafe' \
  io_uring/timeout.o V=1
```

Notes:

- `LLVM=1` routes the build through clang/LLVM.
- `KCFLAGS='-Wno-error=default-const-init-var-unsafe'` demotes a kernel 6.1 `-Werror` that otherwise blocks `timeout.c` compilation under clang.
- `V=1` prints the full compile command.
- On success, the kernel records the exact command in `io_uring/.timeout.o.cmd`.

Verify:

```bash
ls -lh io_uring/timeout.o io_uring/.timeout.o.cmd
```

---

## Step 3: Transform compile command to emit LLVM bitcode

Do **not** hand-write include paths or flags. Derive the command from `.timeout.o.cmd`:

```bash
python3 - <<'PY'
from pathlib import Path
import subprocess

cmd_file = Path("io_uring/.timeout.o.cmd")
raw = cmd_file.read_text().splitlines()[0]
compile_cmd = raw.split(":=", 1)[1].strip().split(";", 1)[0].strip()

bc_cmd = compile_cmd.replace(
    "-c -o io_uring/timeout.o io_uring/timeout.c",
    "-emit-llvm -c -g -O2 -o /tmp/io_uring_timeout.bc io_uring/timeout.c",
)

print("Running:\n", bc_cmd, "\n")
subprocess.run(bc_cmd, shell=True, check=True)
PY
```

Key changes from the object build:

| Original | Bitcode build |
|----------|---------------|
| `-c -o io_uring/timeout.o` | `-emit-llvm -c -g -O2 -o /tmp/io_uring_timeout.bc` |
| (kernel default opt) | `-O2` explicit |
| (varies) | `-g` for DWARF debug info |

---

## Step 4: Verify bitcode

```bash
llvm-dis /tmp/io_uring_timeout.bc -o /tmp/io_uring_timeout.ll
head -20 /tmp/io_uring_timeout.ll
ls -lh /tmp/io_uring_timeout.bc
```

Expected: readable LLVM IR with `!dbg` metadata referencing `io_uring/timeout.c`.

---

## Step 5: Run smoke regression

```bash
/home/xujunru/ImplicitFuzz/extraction/scripts/run_kernel_case1.sh \
  /tmp/io_uring_timeout.bc \
  /tmp/io_uring_timeout.facts.jsonl
```

Expected output:

```text
schema validation OK: 343 facts
kernel case1 OK: io_uring/timeout.c:480 -> io_timeout.off / u32
```

---

## Reproducibility Checklist

- [ ] Kernel tree is v6.1 (`linux-6.1`)
- [ ] `make LLVM=1 defconfig` completed
- [ ] `io_uring/timeout.o` builds without the `default-const-init-var-unsafe` error
- [ ] `io_uring/.timeout.o.cmd` exists and is used (not hand-written flags)
- [ ] Bitcode emitted with `-g -O2 -emit-llvm`
- [ ] `llvm-dis` can read the output
- [ ] `run_kernel_case1.sh` passes schema + golden

---

## What this smoke profile does NOT cover

- Automated bitcode generation inside CI (manual prerequisite for now)
- Other kernel TUs or optimization levels
- BTF / `container_of` recovery
- Cross-kernel-version portability

See `docs/phase1-findings.md` for measured results and next-step roadmap.
