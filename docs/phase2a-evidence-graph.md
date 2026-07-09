# Phase 2A Findings: Evidence Graph Seeds

**Date:** 2026-07-09
**Status:** Phase 2A smoke passes on the Phase 1 evidence store.

---

## Summary

Phase 2A derives conservative graph seeds over the Phase 1 SQLite facts:

```text
access_fact rows -> evidence_node rows -> evidence_edge candidate rows
```

The output is not a final dependency graph. It is a queryable candidate layer for later object identity refinement, branch/gate inference, and execution validation.

## Input

Default database:

```text
/tmp/phase1_facts.db
```

Produced by:

```bash
extraction/scripts/run_phase1_regression.sh
python3 extraction/scripts/ingest_phase1_smoke.py --keep-db
```

## Output Tables

| Table | Meaning |
|-------|---------|
| `evidence_node` | One graph node per normalized source fact. Phase 2A creates access nodes only. |
| `evidence_edge` | Candidate graph edges with source/target fact ids, basis, confidence, and detail JSON. |

## Edge Semantics

| Edge kind | Meaning |
|-----------|---------|
| `state_write_read_candidate` | A symbolic field is written and later read in the same bitcode unit. This is a state-coupling candidate, not a final implicit dependency. |
| `lifecycle_candidate` | Alloc/free facts occur in the same function and order. This is a lifecycle candidate that still requires object identity refinement. |

## Smoke Result

Command:

```bash
extraction/scripts/build_evidence_graph_smoke.py
```

Observed result:

```text
access nodes: 753
state_write_read_candidate edges: 272
lifecycle_candidate edges: 6
phase2a evidence graph smoke OK: /tmp/phase1_facts.db
```

## Out of Scope

Phase 2A does not produce:

- `alias_fact`
- `entry_fact`
- `branch_fact`
- `gate_seed_fact`
- syzkaller descriptions
- LLM outputs
- execution feedback
- final dependency edges

Those remain Phase 2B/2C work.

## Phase 2B Extension

Phase 2B extends this candidate graph with `object_identity_candidate` and `explicit_dependency_candidate` edges. See `docs/phase2b-evidence-identity.md`.
