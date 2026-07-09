"""Read-only query helpers for the SQLite evidence store."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _decode_json(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def list_symbolic_accesses(
    conn: sqlite3.Connection, limit: int = 50
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          function,
          semantic_op,
          access_kind,
          access_path_symbolic,
          field_type,
          primary_provenance,
          confidence,
          source_location_json
        FROM access_fact
        WHERE access_path_symbolic IS NOT NULL
        ORDER BY function, access_path_symbolic, semantic_op
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for row in rows:
        item = _row_to_dict(row)
        item["source_location"] = _decode_json(item.pop("source_location_json"))
        out.append(item)
    return out


def list_lifecycle_ops(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          function,
          semantic_op,
          access_kind,
          access_path_symbolic,
          field_type,
          primary_provenance,
          confidence,
          summary_detail_json,
          source_location_json
        FROM access_fact
        WHERE semantic_op IN ('alloc', 'free', 'free_async', 'retain', 'release')
        ORDER BY function, semantic_op, access_kind, instruction_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for row in rows:
        item = _row_to_dict(row)
        item["summary_detail"] = _decode_json(item.pop("summary_detail_json"))
        item["source_location"] = _decode_json(item.pop("source_location_json"))
        out.append(item)
    return out


def list_accesses_by_field(
    conn: sqlite3.Connection, field: str, limit: int = 100
) -> list[dict[str, Any]]:
    pattern = f"%{field}%"
    rows = conn.execute(
        """
        SELECT
          function,
          semantic_op,
          access_kind,
          access_path_symbolic,
          field_type,
          numeric_kind,
          access_path_numeric,
          primary_provenance,
          confidence,
          source_location_json
        FROM access_fact
        WHERE access_path_symbolic = ?
           OR access_path_symbolic LIKE ?
        ORDER BY function, semantic_op, access_kind
        LIMIT ?
        """,
        (field, pattern, limit),
    ).fetchall()
    out = []
    for row in rows:
        item = _row_to_dict(row)
        item["source_location"] = _decode_json(item.pop("source_location_json"))
        out.append(item)
    return out


def export_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    fact_counts = {
        "access_fact": conn.execute("SELECT COUNT(*) FROM access_fact").fetchone()[0],
        "call_fact": conn.execute("SELECT COUNT(*) FROM call_fact").fetchone()[0],
    }
    semantic_ops = {
        row["semantic_op"]: row["count"]
        for row in conn.execute(
            """
            SELECT semantic_op, COUNT(*) AS count
            FROM access_fact
            GROUP BY semantic_op
            ORDER BY semantic_op
            """
        )
    }
    per_bc_unit = [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT
              bc_unit,
              COUNT(*) AS access_facts,
              SUM(CASE WHEN access_path_symbolic IS NOT NULL THEN 1 ELSE 0 END) AS symbolic_accesses,
              ROUND(
                100.0 * SUM(CASE WHEN access_path_symbolic IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*),
                2
              ) AS symbolic_rate_pct
            FROM access_fact
            GROUP BY bc_unit
            ORDER BY bc_unit
            """
        )
    ]
    lifecycle_ops = {
        row["semantic_op"]: row["count"]
        for row in conn.execute(
            """
            SELECT semantic_op, COUNT(*) AS count
            FROM access_fact
            WHERE semantic_op IN ('alloc', 'free', 'free_async', 'retain', 'release')
            GROUP BY semantic_op
            ORDER BY semantic_op
            """
        )
    }
    top_symbolic = [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT access_path_symbolic, field_type, COUNT(*) AS count
            FROM access_fact
            WHERE access_path_symbolic IS NOT NULL
            GROUP BY access_path_symbolic, field_type
            ORDER BY count DESC, access_path_symbolic
            LIMIT 20
            """
        )
    ]
    return {
        "fact_counts": fact_counts,
        "semantic_ops": semantic_ops,
        "lifecycle_ops": lifecycle_ops,
        "per_bc_unit": per_bc_unit,
        "top_symbolic_accesses": top_symbolic,
    }
