import sqlite3
from pathlib import Path

from implicitfuzz.ingestion.ingest import ingest_jsonl

FIXTURE = Path(__file__).parent / "fixtures" / "sample_facts.jsonl"


def test_ingest_jsonl_counts_per_fact_type(tmp_path):
    db_path = tmp_path / "facts.db"
    counts = ingest_jsonl(str(FIXTURE), str(db_path))
    assert counts == {"entry_fact": 1, "call_fact": 1, "access_fact": 1}


def test_ingest_jsonl_writes_rows_with_expected_fields(tmp_path):
    db_path = tmp_path / "facts.db"
    ingest_jsonl(str(FIXTURE), str(db_path))

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    row = conn.execute("SELECT * FROM access_fact").fetchone()
    assert row["semantic_op"] == "read"
    assert row["numeric_kind"] == "unknown"
    assert row["confidence"] == "low"
    assert row["object_scope"] == "formal_param"

    row = conn.execute("SELECT * FROM entry_fact").fetchone()
    assert row["entry_kind"] == "syscall"
    assert row["entry_symbol"] == "__do_sys_io_uring_enter"

    conn.close()


def test_ingest_jsonl_rejects_schema_invalid_row(tmp_path):
    bad_jsonl = tmp_path / "bad.jsonl"
    bad_jsonl.write_text('{"fact_type": "access_fact", "semantic_op": "not_a_real_op"}\n')
    db_path = tmp_path / "facts.db"

    import pytest
    with pytest.raises(Exception):
        ingest_jsonl(str(bad_jsonl), str(db_path))


def test_ingest_op_dispatch_entry_fact_persists_dispatch_columns(tmp_path):
    import json
    import sqlite3

    from implicitfuzz.ingestion.ingest import ingest_jsonl

    rec = {
        "fact_type": "entry_fact", "schema_version": "1.0.0",
        "kernel_version": "linux-6.1", "llvm_version": "21.1.8", "opt_level": "-O2 -g",
        "bc_unit": "opdef.bc", "function": "io_op_defs",
        "entry_kind": "op_dispatch", "entry_symbol": "io_read",
        "associated_syscall": None, "dispatch_table": "io_op_defs",
        "dispatch_index": 22, "dispatch_role": "issue",
        "source_location": None,
        "primary_provenance": "dwarf", "provenance": ["dwarf"], "confidence": "high",
    }
    jsonl = tmp_path / "opdef.jsonl"
    jsonl.write_text(json.dumps(rec) + "\n")
    db_path = tmp_path / "facts.db"

    counts = ingest_jsonl(str(jsonl), str(db_path))
    assert counts == {"entry_fact": 1}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM entry_fact WHERE entry_kind='op_dispatch'").fetchone()
    assert row["entry_symbol"] == "io_read"
    assert row["dispatch_table"] == "io_op_defs"
    assert row["dispatch_index"] == 22
    assert row["dispatch_role"] == "issue"
    conn.close()
