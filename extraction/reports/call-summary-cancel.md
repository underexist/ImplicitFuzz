# Call Summary: io_uring_cancel.facts.jsonl

## Primitive hits (primary_provenance=summary)

- total summary access_fact: 1
- direct primitive hits: 1
- wrapper propagation hits: 0
- call_alloc (semantic_op=alloc): 0
- call_free (semantic_op=free): 0
- wrapper call_alloc: 0
- wrapper call_free: 0
- call_free (semantic_op=free_async): 0
- call_arg (semantic_op=retain): 0
- call_arg (semantic_op=release): 0
- usercopy: 1
- other summary facts: 0
- total call_fact: 38
- unresolved direct calls: 37

## Matched callees

- `_copy_from_user`: 1

## Top unresolved callees

- `llvm.lifetime.start.p0`: 5
- `llvm.lifetime.end.p0`: 5
- `__io_async_cancel`: 3
- `mutex_lock`: 3
- `io_wq_cancel_cb`: 2
- `io_try_cancel`: 2
- `mutex_unlock`: 2
- `io_poll_cancel`: 1
- `_raw_spin_lock`: 1
- `io_timeout_cancel`: 1
- `_raw_spin_unlock`: 1
- `io_file_get_fixed`: 1
- `io_file_get_normal`: 1
- `llvm.memset.p0.i64`: 1
- `__fdget`: 1
- `ktime_get`: 1
- `prepare_to_wait`: 1
- `io_run_task_work_sig`: 1
- `schedule_hrtimeout`: 1
- `finish_wait`: 1
