import sqlite3

from implicitfuzz.pilot.validate import (
    validate_schema,
    validate_field_existence,
    validate_synthesizability,
)


def _good():
    return {
        "target_gate": {"bc_unit": "io_uring.bc", "function": "io_file_get_fixed",
                        "branch_instruction_id": "b1"},
        "terms": [{
            "class": "activation", "object": "io_ring_ctx",
            "field_ref": "io_ring_ctx.nr_user_files",
            "relation": "fd < ctx->nr_user_files", "align_target": "",
            "source": "slice", "confidence": "high", "uncertain": False,
        }],
        "abstain": False, "alt_candidates": [],
    }


def test_validate_schema_accepts_good():
    assert validate_schema(_good()) is True


def test_validate_schema_rejects_bad_class():
    p = _good()
    p["terms"][0]["class"] = "not_a_class"
    assert validate_schema(p) is False


def test_validate_schema_rejects_missing_terms():
    p = _good()
    del p["terms"]
    assert validate_schema(p) is False


_ACC_DDL = """
CREATE TABLE struct_layout_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  struct_name TEXT, member_name TEXT, byte_offset INTEGER, member_type TEXT
);
CREATE TABLE access_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  semantic_op TEXT, access_path_symbolic TEXT, numeric_kind TEXT, access_path_numeric TEXT
);
"""


def _acc_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_ACC_DDL)
    conn.execute("INSERT INTO struct_layout_fact (struct_name,member_name,byte_offset,member_type) "
                 "VALUES ('io_ring_ctx','nr_user_files',160,'unsigned int')")
    conn.execute("INSERT INTO access_fact (semantic_op,access_path_symbolic,numeric_kind,access_path_numeric) "
                 "VALUES ('write',NULL,'gep_offsets','[160]')")
    return conn


def _term(cls, field_ref, relation="", align_target=""):
    return {"class": cls, "object": "io_ring_ctx", "field_ref": field_ref,
            "relation": relation, "align_target": align_target,
            "source": "slice", "confidence": "high", "uncertain": False}


def test_field_existence_confirms_numeric_and_flags_hallucination():
    conn = _acc_db()
    pred = {"terms": [_term("activation", "io_ring_ctx.nr_user_files"),
                      _term("premise", "io_ring_ctx.bogus_field")], "abstain": False}
    res = validate_field_existence(conn, pred)
    assert res["passed"] is False  # one term unconfirmed
    by = {r["field_ref"]: r["status"] for r in res["per_term"]}
    assert by["io_ring_ctx.nr_user_files"] == "confirmed_numeric"
    assert by["io_ring_ctx.bogus_field"] == "unconfirmed"


def test_synthesizability_heuristic():
    pred = {"terms": [
        _term("param_align", "io_ring_ctx.nr_user_files", align_target="sqe->fd"),
        _term("param_align", "io_ring_ctx.nr_user_files", align_target="nothing settable"),
        _term("premise", "io_ring_ctx.file_data"),
    ], "abstain": False}
    res = validate_synthesizability(pred)
    assert res[0]["synthesizable"] is True   # sqe->fd
    assert res[1]["synthesizable"] is False
    assert res[2]["synthesizable"] is True   # premise always synthesizable
