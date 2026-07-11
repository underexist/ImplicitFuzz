import sqlite3

from implicitfuzz.reconcile.layout import LayoutIndex

_DDL = """
CREATE TABLE struct_layout_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  struct_name TEXT NOT NULL,
  member_name TEXT NOT NULL,
  byte_offset INTEGER NOT NULL,
  member_type TEXT NOT NULL
);
"""


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    conn.execute("INSERT INTO struct_layout_fact (struct_name, member_name, byte_offset, member_type) "
                 "VALUES ('io_ring_ctx','nr_user_files',160,'unsigned int')")
    conn.execute("INSERT INTO struct_layout_fact (struct_name, member_name, byte_offset, member_type) "
                 "VALUES ('io_ring_ctx','file_data',816,'struct io_rsrc_data *')")
    return conn


def test_offset_of_resolves_member():
    idx = LayoutIndex(_db())
    assert idx.offset_of("io_ring_ctx", "nr_user_files") == 160


def test_offset_of_canonicalizes_struct_name():
    # caller may pass "struct io_ring_ctx"; layout stores canonical "io_ring_ctx"
    idx = LayoutIndex(_db())
    assert idx.offset_of("struct io_ring_ctx", "nr_user_files") == 160


def test_offset_of_unknown_returns_none():
    idx = LayoutIndex(_db())
    assert idx.offset_of("io_ring_ctx", "no_such_member") is None
    assert idx.offset_of("no_such_struct", "x") is None
