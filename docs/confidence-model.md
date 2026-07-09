# Fact Confidence Model

**Date:** 2026-07-09
**Scope:** Phase 1 `call_fact` / `access_fact` confidence and provenance policy.

## Contract

Facts use a stable enum in `confidence`:

```text
high
medium
low
```

`confidence_score` is an optional audit field. Consumers should use `confidence` as the primary contract and treat `confidence_score` as diagnostic metadata.

## Phase 1 Policy

| Evidence path | Primary provenance | Confidence | Meaning |
|---------------|--------------------|------------|---------|
| DWARF struct GEP field recovery | `dwarf` | `high` | Struct member index maps to a DWARF member. |
| DWARF byte-offset field recovery | `dwarf` | `high` | Single byte offset exactly matches a DWARF member offset in the current statement context. |
| Direct primitive summary match | `summary` | `high` | Callee exactly or prefix-matches a curated primitive summary entry. |
| Wrapper propagation v0 | `summary` | `medium` | Conservative alloc/free wrapper inferred by direct SSA plus one alloca load/store layer. |
| Numeric GEP without symbolic recovery | `numeric_fallback` | `low` | Offset is preserved, but no symbolic field name is claimed. |
| Whole-object fallback | `numeric_fallback` | `low` | Access is retained, but no stable field path is available. |

`btf` is currently used only as an external layout cross-check. It does not change fact provenance in Phase 1.

## Non-Dropping Rule

The extractor should prefer emitting a lower-confidence fact over dropping evidence. When symbolic recovery fails, it should preserve:

```text
access_path_numeric
access_path
gep_raw
source_location
raw_json in the evidence store
```

This rule is important for downstream debugging: a later pass can re-interpret numeric facts without rerunning the expensive kernel extraction step.

## Consumer Guidance

Recommended downstream filters:

| Use case | Suggested filter |
|----------|------------------|
| Human-readable reports | `confidence in ('high', 'medium')` |
| Field-sensitive dependency graph seeds | `access_path_symbolic IS NOT NULL` |
| Recall-oriented audits | include `low`, but display numeric fallback separately |
| Lifecycle summaries | include `summary` provenance; distinguish direct primitive vs wrapper via `summary_detail` |

The SQLite ingestion layer stores the full original fact in `raw_json`, so future schema additions remain queryable even before normalized columns are added.
