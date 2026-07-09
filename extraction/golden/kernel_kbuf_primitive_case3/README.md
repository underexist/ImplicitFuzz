# Kernel case3: io_uring/kbuf.c primitive alloc/free

Smoke regression for **direct** primitive summary hits (`kzalloc`/`kfree`) in a real kernel TU.

## Why kbuf.c

| TU | Lines | grep alloc/free | facts | primitive alloc | primitive free |
|----|-------|-----------------|-------|-----------------|----------------|
| kbuf.c | 551 | 6 | 393 | 3 | 4 |
| poll.c | 1017 | 5 | 505 | 2 | 3 |
| rsrc.c | 1367 | 14 | 849 | 9 | 21 |

`filetable.c`, `msg_ring.c`, `notif.c` have no direct kmalloc/kfree in source.

## Bitcode generation

Requires an existing `io_uring/.timeout.o.cmd` built with `LLVM=1` (clang). Reuse its compile flags:

```bash
cd /home/xujunru/linux-6.1
make LLVM=1 defconfig
make LLVM=1 CC=clang KCFLAGS='-Wno-error=default-const-init-var-unsafe' io_uring/timeout.o V=1

python3 - <<'PY'
from pathlib import Path
import subprocess
template = Path('io_uring/.timeout.o.cmd').read_text().splitlines()[0].split(':=',1)[1].strip().split(';',1)[0].strip()
tu = 'kbuf'
out = '/tmp/io_uring_kbuf.bc'
cmd = template.replace('io_uring/timeout.c', f'io_uring/{tu}.c')
cmd = cmd.replace('-c -o io_uring/timeout.o', f'-emit-llvm -c -g -O2 -o {out}')
subprocess.run(cmd, shell=True, check=True)
PY
```

## Golden assertions

- `io_provide_buffers`: `call_alloc` from `kzalloc` (macro expansion at `slab.h:553`)
- `io_uring/kbuf.c:449`: `call_free` from `kfree(bl)` error path
