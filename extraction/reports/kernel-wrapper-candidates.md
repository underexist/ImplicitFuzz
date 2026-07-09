# Kernel Wrapper Candidate Audit (Phase 1.5)

Conservative rules (same as wrapper propagation v0):

- **alloc wrapper:** function return directly from alloc primitive (`kmalloc`, `kmem_cache_alloc`, …), one alloca hop only
- **free wrapper:** formal parameter directly passed to free primitive (`kfree`, `kmem_cache_free`, …), one alloca hop only

**Scope:** audit only — no extractor changes.

## io_uring/timeout.c

- bitcode: `/tmp/io_uring_timeout.bc`
- facts: `/tmp/io_uring_timeout.facts.jsonl`
- defined functions scanned: 19
- matched in-TU wrapper candidates: **0**

| function | rule | matched | primitive | call sites (unresolved) | reason | next action |
|----------|------|---------|-----------|-------------------------|--------|-------------|

### External / cross-TU unresolved (selected)

- `io_req_tw_post_queue` (3 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name
- `io_req_task_work_add` (3 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name
- `io_queue_next` (2 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name
- `io_free_req` (2 unresolved call sites): declared only in timeout.c bitcode; source body (io_uring.c) queues req to locked_free_list — no direct kfree/kmem_cache_free on formal param. **Next:** reject for wrapper v0; not a direct-free wrapper
- `io_req_complete_post` (2 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name
- `io_req_task_queue_fail` (1 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name
- `io_alloc_async_data` (1 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name
- `io_cq_unlock_post` (1 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name

**In-TU result:** no function in `io_uring/timeout.c` bitcode matches alloc/free wrapper rules.


## io_uring/cancel.c

- bitcode: `/tmp/io_uring_cancel.bc`
- facts: `/tmp/io_uring_cancel.facts.jsonl`
- defined functions scanned: 7
- matched in-TU wrapper candidates: **0**

| function | rule | matched | primitive | call sites (unresolved) | reason | next action |
|----------|------|---------|-----------|-------------------------|--------|-------------|

### External / cross-TU unresolved (selected)

- `io_wq_cancel_cb` (2 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name
- `io_poll_cancel` (1 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name
- `io_timeout_cancel` (1 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name
- `io_file_get_fixed` (1 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name
- `io_file_get_normal` (1 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name
- `io_run_task_work_sig` (1 unresolved call sites): declared in TU bitcode; body not present (cross-TU audit required). **Next:** inspect defining TU separately; do not guess from name

**In-TU result:** no function in `io_uring/cancel.c` bitcode matches alloc/free wrapper rules.


## Conclusion

- **No in-TU wrapper candidates** in `timeout.c` or `cancel.c` bitcode under v0 rules.
- `io_free_req` is the only alloc/free-adjacent unresolved callee in timeout TU; it is external and does not directly call `kfree` in source.
- **Recommendation:** defer kernel wrapper propagation; expand audit to other io_uring TUs only if direct primitive patterns appear.
