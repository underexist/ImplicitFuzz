# Call Summary: io_uring_kbuf.facts.jsonl

## Primitive hits (primary_provenance=summary)

- total summary access_fact: 9
- direct primitive hits: 9
- wrapper propagation hits: 0
- call_alloc (semantic_op=alloc): 3
- call_free (semantic_op=free): 4
- wrapper call_alloc: 0
- wrapper call_free: 0
- call_free (semantic_op=free_async): 0
- call_arg (semantic_op=retain): 0
- call_arg (semantic_op=release): 0
- usercopy: 2
- other summary facts: 0
- total call_fact: 69
- unresolved direct calls: 60

## Matched callees

- `kfree`: 4
- `kmalloc_trace`: 3
- `_copy_from_user`: 2

## Top unresolved callees

- `xa_load`: 6
- `mutex_lock`: 4
- `mutex_unlock`: 4
- `llvm.lifetime.start.p0`: 4
- `unpin_user_page`: 4
- `kvfree`: 4
- `__SCT__cond_resched`: 4
- `llvm.lifetime.end.p0`: 4
- `_raw_spin_unlock`: 3
- `llvm.memset.p0.i64`: 3
- `_raw_spin_lock`: 2
- `xa_erase`: 2
- `__io_req_complete`: 2
- `io_init_bl_list`: 2
- `io_buffer_add_list`: 2
- `xa_find`: 1
- `xa_find_after`: 1
- `__free_pages`: 1
- `__io_remove_buffers`: 1
- `llvm.uadd.with.overflow.i64`: 1
