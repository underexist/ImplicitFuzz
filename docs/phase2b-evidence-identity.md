# Phase 2B Findings: Identity and Explicit Dependency Candidates

**Date:** 2026-07-09
**Status:** Phase 2B smoke passes on the Phase 1 evidence store.

---

## Summary

Phase 2B extends the Phase 2A evidence graph with two candidate edge kinds:

| Edge kind | Meaning |
|-----------|---------|
| `object_identity_candidate` | Weak same-object candidate based on same function, same bitcode unit, same symbolic object prefix, and same `base_object_json`. |
| `explicit_dependency_candidate` | Producer-consumer style candidate, currently limited to lifecycle alloc-before-free edges. |

The output is still not a final dependency graph. It is an auditable candidate layer for later alias refinement, branch/gate inference, and execution validation.

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
object_identity_candidate edges: 642
explicit_dependency_candidate edges: 6
phase2a evidence graph smoke OK: /tmp/phase1_facts.db
```

The script name still says `phase2a` for compatibility; its checks now include Phase 2B candidate edges.

## Important Boundary

Current Phase 1 facts mostly use:

```json
{"object_scope": "synthetic", "value": "minimal_stub"}
```

Therefore Phase 2B does not claim final object identity. It emits low-confidence identity candidates when two symbolic accesses share local context and symbolic object prefix. These candidates must be refined later by real alias evidence, entry context, call-site binding, or execution feedback.

## Dependency Semantics

Phase 2B deliberately does not convert `write -> read` into explicit producer-consumer dependencies. Write-read pairs remain `state_write_read_candidate` edges for future state-gate reasoning.

Only lifecycle candidates are promoted to `explicit_dependency_candidate`, and even those are marked as requiring identity refinement.

## Out of Scope

Phase 2B does not perform:

- SVF alias fact extraction
- object identity transitive closure
- branch/gate extraction
- LLM inference
- syzkaller generation
- dynamic validation
- final dependency edge materialization
