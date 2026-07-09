# Kernel Alloc/Free Primitive Audit (Phase 1.6)

**Goal:** find a real io_uring TU with direct `kmalloc`/`kfree` IR and establish a primitive-summary golden case. No new wrapper rules.

## Source grep (`linux-6.1/io_uring/*.c`)

```bash
cd /home/xujunru/linux-6.1
grep -RInE '\b(kmalloc|kzalloc|kvzalloc|kfree|kmem_cache_alloc|kmem_cache_free|kfree_rcu)\b' io_uring/*.c
```

| TU | Lines | grep hits | direct alloc/free in TU |
|----|-------|-----------|-------------------------|
| filetable.c | 191 | 0 | no |
| msg_ring.c | 175 | 0 | no |
| notif.c | 72 | 0 | no |
| kbuf.c | 551 | 6 | **yes** |
| poll.c | 1017 | 5 | yes |
| rsrc.c | 1367 | 14 | yes |
| io_uring.c | 4158 | many | yes (large) |

**Selected TU:** `io_uring/kbuf.c` — smallest file with direct alloc/free and simple call sites.

## Bitcode + extraction

Bitcode: `/tmp/io_uring_kbuf.bc` (clang flags cloned from `io_uring/.timeout.o.cmd`, `LLVM=1`).

| Metric | Value |
|--------|-------|
| Total facts | 393 |
| Schema | OK |
| primitive alloc (`call_alloc`) | **3** |
| primitive free (`call_free`) | **4** |
| usercopy | 2 |
| wrapper propagation | 0 |

Matched callees: `kfree` (4), `kzalloc` via macro expansion (3 alloc sites).

## Golden case (kernel_kbuf_primitive_case3)

| Assertion | Location | Fact |
|-----------|----------|------|
| alloc primitive | `io_provide_buffers` | `call_alloc`, `primary_provenance=summary` |
| free primitive | `io_uring/kbuf.c:449` (`kfree(bl)`) | `call_free`, `primary_provenance=summary` |

**Note:** `kzalloc` expands through `include/linux/slab.h:553`; alloc facts use macro expansion spelling. Free sites retain `kbuf.c` line numbers.

## Other TUs (stats only)

| TU | facts | alloc | free | wrapper free |
|----|-------|-------|------|--------------|
| poll.c | 505 | 2 | 3 | 0 |
| rsrc.c | 849 | 9 | 21 (+5 wrapper) | 5 |

`rsrc.c` has more hits but is larger; deferred as primary golden. `poll.c` is a backup candidate.

## Conclusion

Phase 1.6 pass criteria met:

1. io_uring TU with direct kmalloc/kfree in source — **kbuf.c**
2. bitcode generated — **yes**
3. extractor + schema — **OK**
4. primitive alloc/free hits > 0 — **alloc=3, free=4**
5. golden case — **kernel_kbuf_primitive_case3**

**Next:** do not expand wrapper rules. Optional: add `poll.c`/`rsrc.c` as secondary audit lines.

Regression: `extraction/scripts/run_kernel_case3.sh`
