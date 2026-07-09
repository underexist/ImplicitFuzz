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
