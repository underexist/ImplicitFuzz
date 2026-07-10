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
  UNIQUE(bc_unit, function, branch_instruction_id)
);
"""


def create_gate_seed_table(conn: sqlite3.Connection) -> None:
    conn.executescript(GATE_SEED_DDL)
    conn.commit()


def _state_fields(conn: sqlite3.Connection) -> set[str]:
    """Symbolic fields that are written somewhere (candidate state carriers)."""
    rows = conn.execute(
        "SELECT DISTINCT access_path_symbolic FROM access_fact "
        "WHERE semantic_op = 'write' AND access_path_symbolic IS NOT NULL"
    ).fetchall()
    return {r[0] for r in rows}


def derive_gate_seed_candidates(conn: sqlite3.Connection) -> int:
    conn.row_factory = sqlite3.Row
    create_gate_seed_table(conn)

    state_fields = _state_fields(conn)
    if not state_fields:
        return 0

    access_by_id = {
        row["instruction_id"]: row
        for row in conn.execute(
            "SELECT instruction_id, access_path_symbolic, base_object_json "
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

        gated_fields: list[str] = []
        related_objects: list[str] = []
        related_access_facts: list[str] = []
        for load_id in related:
            acc = access_by_id.get(load_id)
            if acc is None:
                continue
            field = acc["access_path_symbolic"]
            if not field or field not in state_fields:
                continue
            gated_fields.append(field)
            related_access_facts.append(load_id)
            base = acc["base_object_json"]
            if base and base not in related_objects:
                related_objects.append(base)

        if not gated_fields:
            continue

        # Dedup fields while preserving order.
        seen: set[str] = set()
        gated_fields = [f for f in gated_fields if not (f in seen or seen.add(f))]
        predicate_summary = (
            f"branch in {br['function']} gated on "
            + ", ".join(gated_fields)
            + " (static skeleton; predicate semantics pending LLM inference)"
        )
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO gate_seed_candidate (
              bc_unit, function, branch_instruction_id, gate_kind,
              predicate_summary, gated_fields_json, related_objects_json,
              related_access_facts_json, confidence, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
