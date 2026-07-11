import sqlite3

from implicitfuzz.pilot.context import read_source_window, build_bundle

_DDL = """
CREATE TABLE access_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  function TEXT, semantic_op TEXT, access_path_symbolic TEXT,
  numeric_kind TEXT, access_path_numeric TEXT
);
"""


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    conn.execute("INSERT INTO access_fact (function,semantic_op,access_path_symbolic,numeric_kind,access_path_numeric) "
                 "VALUES ('io_file_get_fixed','read','io_ring_ctx.file_data','gep_offsets','[816]')")
    conn.execute("INSERT INTO access_fact (function,semantic_op,access_path_symbolic,numeric_kind,access_path_numeric) "
                 "VALUES ('other_fn','read','x.y','gep_offsets','[0]')")
    return conn


def test_read_source_window(tmp_path):
    f = tmp_path / "rw.c"
    f.write_text("\n".join("line%d" % i for i in range(1, 21)) + "\n")
    win = read_source_window(str(tmp_path), "rw.c:10", radius=2)
    assert "line10" in win and "line8" in win and "line12" in win
    assert "line5" not in win


def test_build_bundle_has_slices_ledger_no_answers(tmp_path):
    (tmp_path / "io_uring").mkdir()
    (tmp_path / "io_uring" / "rw.c").write_text("\n".join("l%d" % i for i in range(1, 40)) + "\n")
    gate = {
        "target_gate": {"bc_unit": "rw.bc", "function": "io_file_get_fixed", "branch_instruction_id": "b1"},
        "slice_locations": [{"file_line": "io_uring/rw.c:20", "label": "gate"}],
        "ledger_functions": ["io_file_get_fixed"],
        "field_clues": ["fd", "ctx->nr_user_files"],
    }
    bundle = build_bundle(_db(), str(tmp_path), gate, radius=3)
    assert bundle["target_gate"]["function"] == "io_file_get_fixed"
    assert any("l20" in s["text"] for s in bundle["code_slices"])
    # ledger scoped to the gate's functions only
    funcs = {row["function"] for row in bundle["ledger"]}
    assert funcs == {"io_file_get_fixed"}
    # RED LINE: no answer leakage
    for forbidden in ("ground_truth", "reconciliation", "labels", "answer"):
        assert forbidden not in bundle
