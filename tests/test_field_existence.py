import sqlite3

from implicitfuzz.reconcile.layout import LayoutIndex
from implicitfuzz.reconcile.field_existence import reconcile_field

_DDL = """
CREATE TABLE struct_layout_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  struct_name TEXT, member_name TEXT, byte_offset INTEGER, member_type TEXT
);
CREATE TABLE access_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  semantic_op TEXT, access_path_symbolic TEXT,
  numeric_kind TEXT, access_path_numeric TEXT
);
"""


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    conn.execute("INSERT INTO struct_layout_fact (struct_name,member_name,byte_offset,member_type) "
                 "VALUES ('io_ring_ctx','nr_user_files',160,'unsigned int')")
    # symbolic field present
    conn.execute("INSERT INTO access_fact (semantic_op,access_path_symbolic,numeric_kind,access_path_numeric) "
                 "VALUES ('write','io_ring_ctx.file_data','gep_offsets','[816]')")
    # numeric-only write at offset 160 (nr_user_files, no symbol)
    conn.execute("INSERT INTO access_fact (semantic_op,access_path_symbolic,numeric_kind,access_path_numeric) "
                 "VALUES ('write',NULL,'gep_offsets','[160]')")
    return conn


def test_confirmed_symbolic():
    conn = _db()
    m = reconcile_field(conn, LayoutIndex(conn), "io_ring_ctx", "file_data")
    assert m.status == "confirmed_symbolic"
    assert m.access_fact_id is not None


def test_confirmed_numeric_via_offset():
    conn = _db()
    m = reconcile_field(conn, LayoutIndex(conn), "io_ring_ctx", "nr_user_files")
    assert m.status == "confirmed_numeric"
    assert m.offset == 160
    assert m.access_fact_id is not None


def test_unconfirmed_hallucination():
    conn = _db()
    m = reconcile_field(conn, LayoutIndex(conn), "io_ring_ctx", "nonexistent_field")
    assert m.status == "unconfirmed"
    assert m.access_fact_id is None
