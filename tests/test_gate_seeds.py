import json
import sqlite3

from implicitfuzz.evidence.gates import (
    create_gate_seed_table,
    derive_gate_seed_candidates,
)


def _setup(conn):
    conn.executescript(
        """
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
          confidence TEXT
        );
        CREATE TABLE branch_fact (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          bc_unit TEXT NOT NULL,
          function TEXT NOT NULL,
          branch_instruction_id TEXT NOT NULL,
          condition_value TEXT,
          related_loads_json TEXT NOT NULL
        );
        """
    )


def _access(conn, *, function, op, symbolic, iid, base='{"object_scope":"formal_param","value":"f:0"}'):
    conn.execute(
        "INSERT INTO access_fact (bc_unit, function, semantic_op, access_path_symbolic, "
        "base_object_json, instruction_id, field_type, numeric_kind, access_path_numeric, confidence) "
        "VALUES ('u.bc', ?, ?, ?, ?, ?, NULL, 'gep_offsets', NULL, 'medium')",
        (function, op, symbolic, base, iid),
    )


def _branch(conn, *, function, branch_id, related_loads):
    conn.execute(
        "INSERT INTO branch_fact (bc_unit, function, branch_instruction_id, condition_value, related_loads_json) "
        "VALUES ('u.bc', ?, ?, 'icmp', ?)",
        (function, branch_id, json.dumps(related_loads)),
    )


def test_gate_seed_for_branch_on_written_field():
    conn = sqlite3.connect(":memory:")
    _setup(conn)
    # ctx.file_data is written in one function and gated-on (read) in another.
    _access(conn, function="io_register", op="write", symbolic="io_ring_ctx.file_data", iid="w1")
    _access(conn, function="io_rw", op="read", symbolic="io_ring_ctx.file_data", iid="r1")
    _branch(conn, function="io_rw", branch_id="b1", related_loads=["r1"])
    create_gate_seed_table(conn)

    inserted = derive_gate_seed_candidates(conn)

    assert inserted == 1
    row = conn.execute(
        "SELECT function, branch_instruction_id, gate_kind, gated_fields_json, "
        "related_access_facts_json, confidence FROM gate_seed_candidate"
    ).fetchone()
    assert row[0] == "io_rw"
    assert row[1] == "b1"
    assert row[2] == "premise"
    assert "io_ring_ctx.file_data" in row[3]
    assert "r1" in row[4]
    assert row[5] == "low"


def test_gate_seed_populates_field_key_columns():
    conn = sqlite3.connect(":memory:")
    _setup(conn)
    _access(conn, function="io_register", op="write", symbolic="io_ring_ctx.file_data", iid="w1")
    _access(conn, function="io_rw", op="read", symbolic="io_ring_ctx.file_data", iid="r1")
    _branch(conn, function="io_rw", branch_id="b1", related_loads=["r1"])
    create_gate_seed_table(conn)

    assert derive_gate_seed_candidates(conn) == 1
    row = conn.execute(
        "SELECT gated_fields_json, gated_field_keys_json, state_field_keys_json "
        "FROM gate_seed_candidate"
    ).fetchone()
    # old column: unchanged (symbolic path strings only)
    assert "io_ring_ctx.file_data" in row[0]
    # new columns: field-key dicts with sym: key
    gated_keys = json.loads(row[1])
    assert gated_keys[0]["kind"] == "symbolic"
    assert gated_keys[0]["key"] == "sym:io_ring_ctx.file_data"
    state_keys = json.loads(row[2])
    assert any(k["key"] == "sym:io_ring_ctx.file_data" for k in state_keys)


def test_no_gate_seed_for_branch_on_readonly_field():
    conn = sqlite3.connect(":memory:")
    _setup(conn)
    # opcode is read/gated but never written anywhere -> not a state gate.
    _access(conn, function="io_rw", op="read", symbolic="io_kiocb.opcode", iid="r1")
    _branch(conn, function="io_rw", branch_id="b1", related_loads=["r1"])
    create_gate_seed_table(conn)

    inserted = derive_gate_seed_candidates(conn)

    assert inserted == 0


def test_no_gate_seed_for_branch_without_field_loads():
    conn = sqlite3.connect(":memory:")
    _setup(conn)
    _branch(conn, function="io_rw", branch_id="b1", related_loads=[])
    create_gate_seed_table(conn)

    assert derive_gate_seed_candidates(conn) == 0
