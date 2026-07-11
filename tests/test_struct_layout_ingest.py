import json
import sqlite3

from implicitfuzz.ingestion.ingest import ingest_jsonl


def _fact():
    return {
        "fact_type": "struct_layout_fact",
        "schema_version": "1.0.0",
        "kernel_version": "6.1",
        "llvm_version": "21.1.8",
        "opt_level": "-O2 -g",
        "bc_unit": "io_uring_rsrc.pre.bc",
        "function": "io_ring_ctx",
        "source_location": None,
        "primary_provenance": "dwarf",
        "provenance": ["dwarf"],
        "confidence": "high",
        "struct_name": "io_ring_ctx",
        "member_name": "nr_user_files",
        "byte_offset": 160,
        "member_type": "unsigned int",
    }


def test_struct_layout_fact_ingests(tmp_path):
    jsonl = tmp_path / "facts.jsonl"
    jsonl.write_text(json.dumps(_fact()) + "\n")
    db = tmp_path / "facts.db"
    counts = ingest_jsonl(str(jsonl), str(db))
    assert counts.get("struct_layout_fact") == 1

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT struct_name, member_name, byte_offset, member_type FROM struct_layout_fact"
    ).fetchone()
    assert row == ("io_ring_ctx", "nr_user_files", 160, "unsigned int")
