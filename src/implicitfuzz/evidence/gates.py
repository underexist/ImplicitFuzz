"""Derive static state-gate seed candidates from branch_fact + access_fact.

A branch_fact ties a conditional branch to the object fields it loads. A
*state gate* (v3 §5.8 / purpose.md 研究内容一) is a branch whose gated field is
set by a prior (possibly cross-call) write -- i.e. the field participates in
write->read coupling. This module emits, for each such branch, a
`gate_seed_candidate`: the static skeleton of a state gate. The concrete
predicate semantics (premise / activation / param-align, and the exact
comparison) are left for later LLM inference under the code slice; these rows
are deliberately low-confidence and marked as awaiting that step.

Like the evidence graph, this is a derived table (not the schema-bound
gate_seed_fact), so it needs no fact envelope.
"""

from __future__ import annotations

import json
import sqlite3

from implicitfuzz.evidence.field_key import FieldKey, field_key_for_access


GATE_SEED_DDL = """
CREATE TABLE IF NOT EXISTS gate_seed_candidate (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  branch_instruction_id TEXT NOT NULL,
  gate_kind TEXT NOT NULL,
  predicate_summary TEXT NOT NULL,
  gated_fields_json TEXT NOT NULL,
  related_objects_json TEXT NOT NULL,
  related_access_facts_json TEXT NOT NULL,
  confidence TEXT NOT NULL,
  status TEXT NOT NULL,
  gated_field_keys_json TEXT NOT NULL DEFAULT '[]',
  state_field_keys_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE(bc_unit, function, branch_instruction_id)
);
"""


def create_gate_seed_table(conn: sqlite3.Connection) -> None:
    conn.executescript(GATE_SEED_DDL)
    conn.commit()


def _state_field_keys(conn: sqlite3.Connection) -> dict[str, FieldKey]:
    """Field keys of fields written somewhere (candidate state carriers).

    Keyed by FieldKey.key so gate derivation and reverse-lookup agree on
    field identity. v1 only symbolic writes yield a key (numeric-only writes
    have no struct type in the facts; field_key_for_access returns None).
    """
    out: dict[str, FieldKey] = {}
    for row in conn.execute(
        "SELECT id, access_path_symbolic, field_type, numeric_kind, "
        "access_path_numeric, confidence FROM access_fact WHERE semantic_op = 'write'"
    ).fetchall():
        fk = field_key_for_access(
            access_path_symbolic=row["access_path_symbolic"],
            field_type=row["field_type"],
            numeric_kind=row["numeric_kind"],
            access_path_numeric=row["access_path_numeric"],
            confidence=row["confidence"] or "low",
            source_access_fact_id=row["id"],
        )
        if fk is not None and fk.key not in out:
            out[fk.key] = fk
    return out


def _key_dicts(keys: list[FieldKey]) -> str:
    return json.dumps(
        [
            {
                "kind": k.kind,
                "key": k.key,
                "confidence": k.confidence,
                "source_access_fact_id": k.source_access_fact_id,
            }
            for k in keys
        ],
        sort_keys=True,
    )


def derive_gate_seed_candidates(conn: sqlite3.Connection) -> int:
    conn.row_factory = sqlite3.Row
    create_gate_seed_table(conn)

    state_field_keys = _state_field_keys(conn)
    if not state_field_keys:
        return 0

    access_by_id = {
        row["instruction_id"]: row
        for row in conn.execute(
            "SELECT id, instruction_id, access_path_symbolic, base_object_json, "
            "field_type, numeric_kind, access_path_numeric, confidence "
            "FROM access_fact WHERE instruction_id IS NOT NULL"
        ).fetchall()
    }

    inserted = 0
    for br in conn.execute(
        "SELECT bc_unit, function, branch_instruction_id, related_loads_json "
        "FROM branch_fact ORDER BY id"
    ).fetchall():
        try:
            related = json.loads(br["related_loads_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(related, list):
            continue

        gated_fields: list[str] = []          # old column: symbolic path strings
        gated_keys: dict[str, FieldKey] = {}  # new column: field keys
        related_objects: list[str] = []
        related_access_facts: list[str] = []
        for load_id in related:
            acc = access_by_id.get(load_id)
            if acc is None:
                continue
            fk = field_key_for_access(
                access_path_symbolic=acc["access_path_symbolic"],
                field_type=acc["field_type"],
                numeric_kind=acc["numeric_kind"],
                access_path_numeric=acc["access_path_numeric"],
                confidence=acc["confidence"] or "low",
                source_access_fact_id=acc["id"],
            )
            if fk is None or fk.key not in state_field_keys:
                continue
            gated_keys[fk.key] = fk
            related_access_facts.append(load_id)
            if acc["access_path_symbolic"]:
                gated_fields.append(acc["access_path_symbolic"])
            base = acc["base_object_json"]
            if base and base not in related_objects:
                related_objects.append(base)

        if not gated_keys:
            continue

        # Dedup old-column symbolic fields while preserving order.
        seen: set[str] = set()
        gated_fields = [f for f in gated_fields if not (f in seen or seen.add(f))]
        predicate_summary = (
            f"branch in {br['function']} gated on "
            + ", ".join(gated_fields or sorted(gated_keys))
            + " (static skeleton; predicate semantics pending LLM inference)"
        )
        gated_field_keys_json = _key_dicts(list(gated_keys.values()))
        state_field_keys_json = _key_dicts(
            [state_field_keys[key] for key in gated_keys]
        )
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO gate_seed_candidate (
              bc_unit, function, branch_instruction_id, gate_kind,
              predicate_summary, gated_fields_json, related_objects_json,
              related_access_facts_json, confidence, status,
              gated_field_keys_json, state_field_keys_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                br["bc_unit"],
                br["function"],
                br["branch_instruction_id"],
                "premise",
                predicate_summary,
                json.dumps(gated_fields, sort_keys=True),
                json.dumps(related_objects, sort_keys=True),
                json.dumps(related_access_facts, sort_keys=True),
                "low",
                "static_skeleton_awaiting_llm_predicate",
                gated_field_keys_json,
                state_field_keys_json,
            ),
        )
        if conn.total_changes > before:
            inserted += 1
    conn.commit()
    return inserted


def summarize_gate_seeds(conn: sqlite3.Connection) -> dict[str, int]:
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) c FROM gate_seed_candidate").fetchone()["c"]
    functions = conn.execute(
        "SELECT COUNT(DISTINCT function) c FROM gate_seed_candidate"
    ).fetchone()["c"]
    return {"gate_seed_candidates": total, "gated_functions": functions}
