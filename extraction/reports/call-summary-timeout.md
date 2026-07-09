# Call Summary: io_uring_timeout.facts.jsonl

## Primitive hits (primary_provenance=summary)

- total summary access_fact: 0
- direct primitive hits: 0
- wrapper propagation hits: 0
- call_alloc (semantic_op=alloc): 0
- call_free (semantic_op=free): 0
- wrapper call_alloc: 0
- wrapper call_free: 0
- call_free (semantic_op=free_async): 0
- call_arg (semantic_op=retain): 0
- call_arg (semantic_op=release): 0
- usercopy: 0
- other summary facts: 0
- total call_fact: 75
- unresolved direct calls: 75

## Matched callees

- (none)

## Top unresolved callees

- `_raw_spin_unlock_irq`: 9
- `_raw_spin_lock_irq`: 7
- `hrtimer_try_to_cancel`: 6
- `llvm.assume`: 5
- `hrtimer_start_range_ns`: 4
- `io_req_tw_post_queue`: 3
- `io_req_task_work_add`: 3
- `hrtimer_init`: 3
- `io_kill_timeout`: 2
- `get_timespec64`: 2
- `llvm.lifetime.start.p0`: 2
- `_raw_spin_lock`: 2
- `llvm.lifetime.end.p0`: 2
- `__io_timeout_prep`: 2
- `_raw_spin_lock_irqsave`: 2
- `_raw_spin_unlock_irqrestore`: 2
- `io_queue_next`: 2
- `io_free_req`: 2
- `io_req_complete_post`: 2
- `io_fail_links`: 1
