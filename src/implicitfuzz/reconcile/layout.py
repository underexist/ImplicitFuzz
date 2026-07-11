"""name -> (struct, byte offset) resolution over the struct_layout table.

Backs the field-existence reconciliation of numeric-only fields: a field
referenced by name (io_ring_ctx.nr_user_files) is resolved to its byte
offset via DWARF struct layout, so it can be matched against numeric-only
access_facts (which carry only an offset, no symbol).
"""

from __future__ import annotations

import sqlite3

from implicitfuzz.evidence.field_key import canonicalize_struct_type


class LayoutIndex:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._by_key: dict[tuple[str, str], int] = {}
        for struct_name, member_name, byte_offset in conn.execute(
            "SELECT struct_name, member_name, byte_offset FROM struct_layout_fact"
        ).fetchall():
            canon = canonicalize_struct_type(struct_name) or struct_name
            self._by_key.setdefault((canon, member_name), byte_offset)

    def offset_of(self, struct_name: str, member_name: str) -> int | None:
        canon = canonicalize_struct_type(struct_name) or struct_name
        return self._by_key.get((canon, member_name))
