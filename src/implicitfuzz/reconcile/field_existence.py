"""Three-state field-existence reconciliation against the access_fact ledger.

The design's anti-hallucination red line (purpose.md 4.2(3)): every field a
predicate references must exist in the fact ledger, else it is a
hallucination. Robust to numeric-only fields -- resolves the name to a byte
offset via the struct layout and matches numeric access_facts on that offset.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from implicitfuzz.reconcile.layout import LayoutIndex

_NUMERIC_KINDS = ("gep_offsets", "byte_range")


@dataclass(frozen=True)
class FieldMatch:
    status: str  # confirmed_symbolic | confirmed_numeric | unconfirmed
    access_fact_id: int | None = None
    offset: int | None = None


def reconcile_field(
    conn: sqlite3.Connection,
    layout: LayoutIndex,
    struct_name: str,
    member_name: str,
) -> FieldMatch:
    symbolic_path = f"{struct_name}.{member_name}"
    row = conn.execute(
        "SELECT id FROM access_fact WHERE access_path_symbolic = ? ORDER BY id LIMIT 1",
        (symbolic_path,),
    ).fetchone()
    if row is not None:
        return FieldMatch("confirmed_symbolic", access_fact_id=row[0])

    offset = layout.offset_of(struct_name, member_name)
    if offset is not None:
        needle = f"[{offset}]"
        row = conn.execute(
            "SELECT id FROM access_fact "
            "WHERE numeric_kind IN (?, ?) AND access_path_numeric = ? "
            "ORDER BY id LIMIT 1",
            (_NUMERIC_KINDS[0], _NUMERIC_KINDS[1], needle),
        ).fetchone()
        if row is not None:
            return FieldMatch("confirmed_numeric", access_fact_id=row[0], offset=offset)

    return FieldMatch("unconfirmed")
