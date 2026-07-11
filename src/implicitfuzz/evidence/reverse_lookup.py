"""Reverse-lookup: gate gated-field -> prior write access_fact (cross-TU).

Implements the "先门控、后反查" step (purpose.md §3.2). For each
gate_seed_candidate gated field key, find every `write` access_fact whose
field key matches -- crossing TU boundaries, since the writing call is
typically in a different .bc than the gated read (e.g. write in rsrc.c,
gated read in rw.c). Each match becomes a prior-write dependency candidate,
left for later LLM predicate inference + execution verification.

Derived table (not schema-bound). v1 matches on symbolic field keys only.
"""

from __future__ import annotations

import json
import sqlite3

from implicitfuzz.evidence.field_key import field_key_for_access

REVERSE_LOOKUP_DDL = """
CREATE TABLE IF NOT EXISTS gate_prior_write_candidate (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gate_seed_id INTEGER NOT NULL,
  field_key TEXT NOT NULL,
  field_key_kind TEXT NOT NULL,
  write_access_fact_id INTEGER NOT NULL,
  write_function TEXT NOT NULL,
  write_bc_unit TEXT NOT NULL,
  write_source_location TEXT,
  write_base_object_json TEXT,
  object_identity_edge_ids_json TEXT,
  entry_attribution_json TEXT,
  basis TEXT NOT NULL,
  confidence TEXT NOT NULL,
  status TEXT NOT NULL,
  UNIQUE(gate_seed_id, field_key, write_access_fact_id)
);
"""

_STATUS = "awaiting_llm_predicate_and_execution_verification"


def create_reverse_lookup_table(conn: sqlite3.Connection) -> None:
    conn.executescript(REVERSE_LOOKUP_DDL)
    conn.commit()


def _source_location(source_location_json: str | None) -> str | None:
    if not source_location_json:
        return None
    try:
        loc = json.loads(source_location_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(loc, dict):
        return loc.get("expansion") or loc.get("spelling")
    return None


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _entry_attribution(conn: sqlite3.Connection, write_function: str) -> str | None:
    """Opcode/role ONLY when the write function is itself a dispatch handler.

    Anti-overclaim: a write buried in a handler's deep callee (function not an
    entry_symbol) gets no opcode -- there is no reliable call-chain evidence.
    """
    if not _has_table(conn, "entry_fact"):
        return None
    rows = conn.execute(
        "SELECT entry_symbol, dispatch_index, dispatch_role FROM entry_fact "
        "WHERE entry_symbol = ?",
        (write_function,),
    ).fetchall()
    if not rows:
        return None
    attrs = [{"entry_symbol": r[0], "opcode": r[1], "role": r[2]} for r in rows]
    return json.dumps(attrs, sort_keys=True)


def _writes_by_field_key(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    index: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute(
        "SELECT id, function, bc_unit, access_path_symbolic, field_type, "
        "numeric_kind, access_path_numeric, confidence, base_object_json, "
        "source_location_json FROM access_fact WHERE semantic_op = 'write'"
    ).fetchall():
        fk = field_key_for_access(
            access_path_symbolic=row["access_path_symbolic"],
            field_type=row["field_type"],
            numeric_kind=row["numeric_kind"],
            access_path_numeric=row["access_path_numeric"],
            confidence=row["confidence"] or "low",
            source_access_fact_id=row["id"],
        )
        if fk is None:
            continue
        index.setdefault(fk.key, []).append(row)
    return index


def derive_gate_prior_write_candidates(conn: sqlite3.Connection) -> int:
    conn.row_factory = sqlite3.Row
    create_reverse_lookup_table(conn)
    if not _has_table(conn, "gate_seed_candidate"):
        return 0

    writes = _writes_by_field_key(conn)
    if not writes:
        return 0

    inserted = 0
    for gate in conn.execute(
        "SELECT id, gated_field_keys_json FROM gate_seed_candidate ORDER BY id"
    ).fetchall():
        try:
            keys = json.loads(gate["gated_field_keys_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        for k in keys:
            key = k.get("key")
            kind = k.get("kind")
            if not key or key not in writes:
                continue
            basis = "numeric_field_key" if kind == "numeric" else "symbolic_field_key"
            confidence = "low" if kind == "numeric" else "medium"
            for w in writes[key]:
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT OR IGNORE INTO gate_prior_write_candidate (
                      gate_seed_id, field_key, field_key_kind,
                      write_access_fact_id, write_function, write_bc_unit,
                      write_source_location, write_base_object_json,
                      object_identity_edge_ids_json, entry_attribution_json,
                      basis, confidence, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        gate["id"], key, kind,
                        w["id"], w["function"], w["bc_unit"],
                        _source_location(w["source_location_json"]),
                        w["base_object_json"],
                        None,
                        _entry_attribution(conn, w["function"]),
                        basis, confidence, _STATUS,
                    ),
                )
                if conn.total_changes > before:
                    inserted += 1
    conn.commit()
    return inserted


def summarize_reverse_lookup(conn: sqlite3.Connection) -> dict[str, int]:
    conn.row_factory = sqlite3.Row
    total = conn.execute(
        "SELECT COUNT(*) c FROM gate_prior_write_candidate"
    ).fetchone()["c"]
    gates = conn.execute(
        "SELECT COUNT(DISTINCT gate_seed_id) c FROM gate_prior_write_candidate"
    ).fetchone()["c"]
    cross_tu = conn.execute(
        "SELECT COUNT(*) c FROM gate_prior_write_candidate p "
        "JOIN gate_seed_candidate g ON g.id = p.gate_seed_id "
        "WHERE p.write_bc_unit != g.bc_unit"
    ).fetchone()["c"]
    return {
        "gate_prior_write_candidates": total,
        "gates_with_prior_write": gates,
        "cross_tu_prior_writes": cross_tu,
    }
