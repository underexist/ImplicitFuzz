# kernel_cancel_case2 (struct-typed GEP)

## Why cancel.c instead of timeout.c

`timeout.c` at `-O2 -g` produces **zero** struct-typed GEP candidates in facts (all field accesses are `i8` byte-offset). Per Phase 1 scan, case2 uses `io_uring/cancel.c` to cover the second recovery path:

```text
struct-typed GEP + constant field index → DWARF member   (case2)
i8 byte-offset GEP → DWARF member offset                 (case1 / timeout.c:480)
```

## Scope

| Item | Value |
|------|-------|
| Kernel tree | `/home/xujunru/linux-6.1` |
| TU | `io_uring/cancel.c` |
| Bitcode | `/tmp/io_uring_cancel.bc` |
| Facts | `/tmp/io_uring_cancel.facts.jsonl` |

## Candidate

- **Line:** `io_uring/cancel.c:207`
- **Expression:** `INIT_HLIST_HEAD(&table->hbs[i].list);`
- **Function:** `init_hash_table`
- **IR pattern:** `getelementptr %struct.io_hash_bucket, ptr %..., i64 %i, i32 1`
- **Not** `i8` byte-offset; **not** `container_of` / `list_entry`

## Expected fact

| Field | Value |
|-------|-------|
| `semantic_op` | `write` |
| `access_kind` | `direct_store` |
| `numeric_kind` | `gep_offsets` |
| `access_path_numeric` | `[1]` |
| `access_path_symbolic` | `io_hash_bucket.list` |
| `field_type` | `struct hlist_head` |
| `primary_provenance` | `dwarf` |
| `gep_raw.steps[0].source_element_type` | starts with `%struct.io_hash_bucket` |

## Bitcode prerequisite

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

## Validation

```bash
/home/xujunru/ImplicitFuzz/extraction/scripts/run_kernel_case2.sh
```

See also `extraction/RUNBOOK.md`.
