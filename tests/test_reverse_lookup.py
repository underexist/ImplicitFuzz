import json
import sqlite3

from implicitfuzz.evidence.gates import derive_gate_seed_candidates
from implicitfuzz.evidence.reverse_lookup import (
    derive_gate_prior_write_candidates,
    summarize_reverse_lookup,
)

_SCHEMA = """
CREATE TABLE access_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  semantic_op TEXT NOT NULL,
  access_path_symbolic TEXT,
  base_object_json TEXT,
  instruction_id TEXT,
  field_type TEXT,
  numeric_kind TEXT,
  access_path_numeric TEXT,
  confidence TEXT,
  source_location_json TEXT
);
CREATE TABLE branch_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  branch_instruction_id TEXT NOT NULL,
  condition_value TEXT,
  related_loads_json TEXT NOT NULL
);
CREATE TABLE entry_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  entry_symbol TEXT NOT NULL,
  dispatch_index INTEGER,
  dispatch_role TEXT
);
"""


def _acc(conn, *, bc, fn, op, sym, iid, loc=None):
    conn.execute(
        "INSERT INTO access_fact (bc_unit, function, semantic_op, access_path_symbolic, "
        "base_object_json, instruction_id, field_type, numeric_kind, access_path_numeric, "
        "confidence, source_location_json) "
        "VALUES (?, ?, ?, ?, '{\"object_scope\":\"global\",\"value\":\"g\"}', ?, "
        "NULL, 'gep_offsets', NULL, 'medium', ?)",
        (bc, fn, op, sym, iid, loc),
    )


def _cross_tu_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    # write side in rsrc.bc, gated read side in rw.bc -> cross-TU
    _acc(conn, bc="rsrc.bc", fn="io_register", op="write", sym="io_ring_ctx.file_data",
         iid="w1", loc=json.dumps({"spelling": "rsrc.c:120", "expansion": "rsrc.c:120", "inlined_at": []}))
    _acc(conn, bc="rw.bc", fn="io_rw", op="read", sym="io_ring_ctx.file_data", iid="r1")
    conn.execute(
        "INSERT INTO branch_fact (bc_unit, function, branch_instruction_id, condition_value, related_loads_json) "
        "VALUES ('rw.bc', 'io_rw', 'b1', 'icmp', ?)",
        (json.dumps(["r1"]),),
    )
    derive_gate_seed_candidates(conn)
    return conn


def test_reverse_lookup_matches_write_cross_tu():
    conn = _cross_tu_db()
    assert derive_gate_prior_write_candidates(conn) == 1
    row = conn.execute(
        "SELECT field_key, field_key_kind, write_function, write_bc_unit, "
        "write_source_location, basis, confidence, status, entry_attribution_json "
        "FROM gate_prior_write_candidate"
    ).fetchone()
    assert row[0] == "sym:io_ring_ctx.file_data"
    assert row[1] == "symbolic"
    assert row[2] == "io_register"
    assert row[3] == "rsrc.bc"          # cross-TU: differs from gate's rw.bc
    assert row[4] == "rsrc.c:120"       # parsed from source_location_json.expansion
    assert row[5] == "symbolic_field_key"
    assert row[6] == "medium"
    assert row[7] == "awaiting_llm_predicate_and_execution_verification"
    assert row[8] is None               # io_register not an entry_symbol handler -> no opcode


def test_reverse_lookup_no_write_no_row():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    _acc(conn, bc="rw.bc", fn="io_rw", op="read", sym="io_ring_ctx.file_data", iid="r1")
    conn.execute(
        "INSERT INTO branch_fact (bc_unit, function, branch_instruction_id, condition_value, related_loads_json) "
        "VALUES ('rw.bc', 'io_rw', 'b1', 'icmp', ?)",
        (json.dumps(["r1"]),),
    )
    derive_gate_seed_candidates(conn)   # no write anywhere -> no gate seed
    assert derive_gate_prior_write_candidates(conn) == 0


def test_reverse_lookup_attaches_opcode_only_for_handler_write():
    conn = _cross_tu_db()
    # write function IS a registered opcode handler -> attribution attached
    conn.execute(
        "INSERT INTO entry_fact (bc_unit, function, entry_symbol, dispatch_index, dispatch_role) "
        "VALUES ('rsrc.bc', 'io_register', 'io_register', 22, 'issue')"
    )
    derive_gate_prior_write_candidates(conn)
    row = conn.execute(
        "SELECT entry_attribution_json FROM gate_prior_write_candidate"
    ).fetchone()
    attr = json.loads(row[0])
    assert attr[0] == {"entry_symbol": "io_register", "opcode": 22, "role": "issue"}


def test_summary_counts():
    conn = _cross_tu_db()
    derive_gate_prior_write_candidates(conn)
    summ = summarize_reverse_lookup(conn)
    assert summ["gate_prior_write_candidates"] == 1
    assert summ["gates_with_prior_write"] == 1
    assert summ["cross_tu_prior_writes"] == 1
