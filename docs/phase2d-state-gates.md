# Phase 2D Findings: State-Gate Skeletons (branch_fact + gate_seed_candidate)

**Date:** 2026-07-10
**Status:** Static skeleton of the object-state-machine view. LLM predicate inference is deliberately NOT part of this phase.

---

## Summary

This phase builds the **static half** of the design's 对象状态机视图 / 状态门控 (`docs/purpose.md` 研究内容一) — the part that anchors "which object fields gate which branches" — and stops exactly where the LLM is supposed to take over (inferring the concrete predicate semantics). It is the first phase to touch the project's actual novelty (field-level state gates) rather than the fact-ledger/identity foundation.

Two layers:

1. **`branch_fact`** (extractor, per-TU, `implicitfuzz-extract.cpp`) — for each conditional `BranchInst`/`SwitchInst`, a bounded **branch-local backward slice** over the condition collects the feeding `LoadInst`s. `related_loads` reference the same `instruction_id`s the loads' `access_fact`s carry (via a per-function `Instruction*→id` map), so a branch is tied to the object fields it reads. Fields: `branch_instruction_id`, `condition_value` (IR render), `control_deps` (successor labels), `related_loads`, `slice_range` (firstload..branch source span). Stops at PHIs to stay local (full cross-block slicing is future work, v3 §5.8).

2. **`gate_seed_candidate`** (derived, `src/implicitfuzz/evidence/gates.py`) — a branch is a **state gate** when its gated field is *written somewhere* (a state carrier, i.e. participates in write→read coupling, matching `state_write_read_candidate`). A branch gated only on a never-written field (e.g. `io_kiocb.opcode`) is a premise but not an implicit-dependency state gate, and is excluded. Each state gate emits a `gate_seed_candidate`: `function` (target_symbol), `branch_instruction_id`, `gated_fields`, `related_objects` (base_objects), `related_access_facts`, `gate_kind` (`premise` v0), `predicate_summary` (static render). Rows are `confidence=low`, `status=static_skeleton_awaiting_llm_predicate`.

Like the evidence graph, `gate_seed_candidate` is a **derived table**, not the schema-bound `gate_seed_fact` — so it needs no fact envelope. `gate_seed_fact` remains reserved for finalized gates (post-LLM).

## The LLM boundary (what this phase deliberately does NOT do)

Per `docs/purpose.md` 研究内容一, the concrete gate predicate — its type (premise / activation / **param-align**), the exact comparison, and cross-object predicate conjunction — is inferred by the LLM under the code slice, with the fact ledger as a hallucination check (字段存在性对账). This phase produces only the traceable anchor (branch ↔ state fields ↔ objects ↔ access facts) that the LLM judgement is constrained by and that "反查证据库" later walks to find the writing (prior) calls. The `predicate_summary` here is a placeholder string, not a semantic predicate.

## Measured (4-TU smoke: tiny + timeout.c + cancel.c + kbuf.c)

- `branch_fact` per TU: tiny 3, timeout 106, cancel 59, kbuf 108 (conditional + switch).
- `gate_seed_candidate`: **65 across 26 functions**. Top gated state fields:

```text
io_kiocb.flags          15   (request state flags -- the canonical io_uring gate)
io_buffer_list.buf_nr_pages 12
io_provide_buf.nbufs     6
io_kiocb.link            5   (linked request chains)
io_timeout.head          4
io_provide_buf.bgid      4
io_timeout_data.flags    3
io_cancel.flags          2
...
```

Example: `io_disarm_next` gated on `io_kiocb.flags` / `io_kiocb.link` — exactly the implicit-dependency pattern (a request's link/flag state, set by prior submission, gating the disarm path).

**Boundary note:** these are per-TU static skeletons. The whole point of the design is that the *writing* call for a gated field is often in another syscall/TU; finding it is the later "反查证据库" step (join gate_seed_candidate's fields against write access_facts across the ingested corpus), followed by LLM predicate inference and execution validation. This phase provides the anchors, not the closed dependency.

## Regression

- Tiny golden extended: `gated()`'s `if (n->flags != 0)` → a `branch_fact` whose `related_loads` cross-references an `access_fact` with symbolic `Node.flags`. `check_golden_facts.py`'s node-id assertion scoped to call_fact/access_fact (branch_fact/entry_fact legitimately carry no `svf_node_id`).
- `run_phase1_regression.sh`: ALL PASS. `pytest`: 23 passed (3 new gate-seed tests). `build_evidence_graph_smoke.py`: derives + reports gate seeds.

## 反查 (gate_prior_write_candidate) — done, symbolic v1

The "先门控、后反查" step is now implemented as a derived table
`gate_prior_write_candidate` (`src/implicitfuzz/evidence/reverse_lookup.py`).
For each `gate_seed_candidate` gated field key, it finds every `write`
`access_fact` with a matching field key **across TU boundaries** (write in
rsrc.c, gated read in rw.c) and records the prior write site: write
function, bc_unit, source location, base object, and — only when the write
function is itself a dispatch handler — an opcode/role attribution (no
opcode for deep-callee writes, to avoid overclaim). Rows carry
`basis=symbolic_field_key`, `confidence=medium`, and
`status=awaiting_llm_predicate_and_execution_verification`.

Matching runs through a shared field-key helper
(`src/implicitfuzz/evidence/field_key.py`) used by both gate derivation and
reverse-lookup, so numeric-only fields are not silently dropped at the gate
side. v1 matches symbolic field keys only; the numeric branch is
reserved for a follow-up extractor-symbolization upgrade (numeric-only
access_facts currently carry no struct type, so `ctx->nr_user_files` and
peers stay unsymbolized until then). See
`docs/superpowers/specs/2026-07-10-reverse-lookup-gate-prior-write-design.md`
(§0 决策修订) and the plan under `docs/superpowers/plans/`.

Smoke entry point: `extraction/scripts/build_reverse_lookup_smoke.py`
(ingest multi-TU facts → gate seeds → reverse lookup; asserts ≥1 cross-TU
prior write).

## Next

- LLM constrained judgement: infer predicate (premise/activation/param-align) per gate under the code slice, with the 3-gate validation (field-existence reconciliation against the fact ledger, schema, synthesizability).
- Extractor symbolization upgrade: recover `io_ring_ctx.nr_user_files`/`file_table` etc. from numeric-only to symbolic, so the flagship `sqe->buf_index < ctx->nr_user_files` gate reverse-looks-up end-to-end.
