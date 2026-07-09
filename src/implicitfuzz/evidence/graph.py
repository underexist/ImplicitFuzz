"""Derived evidence graph tables over Phase 1 SQLite facts."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


GRAPH_DDL = """
CREATE TABLE IF NOT EXISTS evidence_node (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  node_kind TEXT NOT NULL,
  fact_type TEXT NOT NULL,
  fact_id INTEGER NOT NULL,
  node_key TEXT NOT NULL,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  label TEXT,
  confidence TEXT NOT NULL,
  UNIQUE(fact_type, fact_id)
);

CREATE TABLE IF NOT EXISTS evidence_edge (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  edge_kind TEXT NOT NULL,
  source_node_id INTEGER NOT NULL,
  target_node_id INTEGER NOT NULL,
  source_fact_type TEXT NOT NULL,
  source_fact_id INTEGER NOT NULL,
  target_fact_type TEXT NOT NULL,
  target_fact_id INTEGER NOT NULL,
  basis TEXT NOT NULL,
  confidence TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  FOREIGN KEY(source_node_id) REFERENCES evidence_node(id),
  FOREIGN KEY(target_node_id) REFERENCES evidence_node(id),
  UNIQUE(edge_kind, source_fact_type, source_fact_id, target_fact_type, target_fact_id, basis)
);
"""

_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def create_evidence_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(GRAPH_DDL)
    conn.commit()


def _access_label(row: sqlite3.Row) -> str:
    if row["access_path_symbolic"]:
        return row["access_path_symbolic"]
    if row["numeric_kind"] == "whole_object":
        return f"whole_object:{row['access_path_numeric'] or '[]'}"
    if row["access_path_numeric"]:
        return f"{row['numeric_kind']}:{row['access_path_numeric']}"
    return "unknown"


def _min_confidence(left: str, right: str) -> str:
    if _CONFIDENCE_ORDER.get(left, 0) <= _CONFIDENCE_ORDER.get(right, 0):
        return left
    return right


def _inserted_since(conn: sqlite3.Connection, before: int) -> bool:
    return conn.total_changes > before


def derive_access_nodes(conn: sqlite3.Connection) -> int:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
          id, bc_unit, function, access_path_symbolic, numeric_kind,
          access_path_numeric, confidence
        FROM access_fact
        ORDER BY id
        """
    ).fetchall()

    inserted = 0
    for row in rows:
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO evidence_node (
              node_kind, fact_type, fact_id, node_key,
              bc_unit, function, label, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "access",
                "access_fact",
                row["id"],
                f"access_fact:{row['id']}",
                row["bc_unit"],
                row["function"],
                _access_label(row),
                row["confidence"],
            ),
        )
        if _inserted_since(conn, before):
            inserted += 1
    conn.commit()
    return inserted


def derive_state_flow_edges(conn: sqlite3.Connection) -> int:
    conn.row_factory = sqlite3.Row
    pairs = conn.execute(
        """
        SELECT
          w.id AS write_fact_id,
          r.id AS read_fact_id,
          w.confidence AS write_confidence,
          r.confidence AS read_confidence,
          wn.id AS write_node_id,
          rn.id AS read_node_id,
          w.access_path_symbolic AS symbolic_field
        FROM access_fact AS w
        JOIN access_fact AS r
          ON w.bc_unit = r.bc_unit
         AND w.access_path_symbolic = r.access_path_symbolic
         AND w.id < r.id
        JOIN evidence_node AS wn
          ON wn.fact_type = 'access_fact'
         AND wn.fact_id = w.id
        JOIN evidence_node AS rn
          ON rn.fact_type = 'access_fact'
         AND rn.fact_id = r.id
        WHERE w.semantic_op = 'write'
          AND r.semantic_op = 'read'
          AND w.access_path_symbolic IS NOT NULL
          AND r.access_path_symbolic IS NOT NULL
        ORDER BY w.id, r.id
        """
    ).fetchall()

    inserted = 0
    for row in pairs:
        confidence = _min_confidence(row["write_confidence"], row["read_confidence"])
        detail = {
            "symbolic_field": row["symbolic_field"],
            "status": "candidate_not_final_dependency",
        }
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO evidence_edge (
              edge_kind, source_node_id, target_node_id,
              source_fact_type, source_fact_id,
              target_fact_type, target_fact_id,
              basis, confidence, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "state_write_read_candidate",
                row["write_node_id"],
                row["read_node_id"],
                "access_fact",
                row["write_fact_id"],
                "access_fact",
                row["read_fact_id"],
                "same_bc_unit_and_symbolic_field",
                confidence,
                json.dumps(detail, sort_keys=True),
            ),
        )
        if _inserted_since(conn, before):
            inserted += 1
    conn.commit()
    return inserted


def derive_lifecycle_edges(conn: sqlite3.Connection) -> int:
    conn.row_factory = sqlite3.Row
    pairs = conn.execute(
        """
        SELECT
          a.id AS alloc_fact_id,
          f.id AS free_fact_id,
          a.confidence AS alloc_confidence,
          f.confidence AS free_confidence,
          an.id AS alloc_node_id,
          fn.id AS free_node_id,
          a.function AS function
        FROM access_fact AS a
        JOIN access_fact AS f
          ON a.bc_unit = f.bc_unit
         AND a.function = f.function
         AND a.id < f.id
        JOIN evidence_node AS an
          ON an.fact_type = 'access_fact'
         AND an.fact_id = a.id
        JOIN evidence_node AS fn
          ON fn.fact_type = 'access_fact'
         AND fn.fact_id = f.id
        WHERE a.semantic_op = 'alloc'
          AND f.semantic_op IN ('free', 'free_async')
        ORDER BY a.id, f.id
        """
    ).fetchall()

    inserted = 0
    for row in pairs:
        confidence = _min_confidence(row["alloc_confidence"], row["free_confidence"])
        detail = {
            "function": row["function"],
            "status": "candidate_requires_identity_refinement",
        }
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO evidence_edge (
              edge_kind, source_node_id, target_node_id,
              source_fact_type, source_fact_id,
              target_fact_type, target_fact_id,
              basis, confidence, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "lifecycle_candidate",
                row["alloc_node_id"],
                row["free_node_id"],
                "access_fact",
                row["alloc_fact_id"],
                "access_fact",
                row["free_fact_id"],
                "same_function_alloc_before_free",
                confidence,
                json.dumps(detail, sort_keys=True),
            ),
        )
        if _inserted_since(conn, before):
            inserted += 1
    conn.commit()
    return inserted


def build_evidence_graph(conn: sqlite3.Connection) -> dict[str, int]:
    create_evidence_tables(conn)
    access_nodes = derive_access_nodes(conn)
    state_edges = derive_state_flow_edges(conn)
    lifecycle_edges = derive_lifecycle_edges(conn)
    return {
        "access_nodes": access_nodes,
        "state_write_read_candidate_edges": state_edges,
        "lifecycle_candidate_edges": lifecycle_edges,
    }


def _count_by(conn: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    rows = conn.execute(
        f"""
        SELECT {column} AS key, COUNT(*) AS count
        FROM {table}
        GROUP BY {column}
        ORDER BY {column}
        """
    ).fetchall()
    return {row["key"]: row["count"] for row in rows}


def summarize_evidence_graph(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    conn.row_factory = sqlite3.Row
    return {
        "nodes": _count_by(conn, "evidence_node", "node_kind"),
        "edges": _count_by(conn, "evidence_edge", "edge_kind"),
    }


def list_evidence_edges(
    conn: sqlite3.Connection, edge_kind: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    if edge_kind is None:
        rows = conn.execute(
            """
            SELECT edge_kind, source_fact_id, target_fact_id, basis, confidence, detail_json
            FROM evidence_edge
            ORDER BY edge_kind, source_fact_id, target_fact_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT edge_kind, source_fact_id, target_fact_id, basis, confidence, detail_json
            FROM evidence_edge
            WHERE edge_kind = ?
            ORDER BY source_fact_id, target_fact_id
            LIMIT ?
            """,
            (edge_kind, limit),
        ).fetchall()
    return [
        {
            "edge_kind": row["edge_kind"],
            "source_fact_id": row["source_fact_id"],
            "target_fact_id": row["target_fact_id"],
            "basis": row["basis"],
            "confidence": row["confidence"],
            "detail": json.loads(row["detail_json"]),
        }
        for row in rows
    ]
