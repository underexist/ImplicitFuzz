import json, sqlite3, sys, os
sys.path.insert(0, "scripts")
from build_bundles import build_all

_DDL = ("CREATE TABLE access_fact (id INTEGER PRIMARY KEY AUTOINCREMENT, function TEXT, "
        "semantic_op TEXT, access_path_symbolic TEXT, numeric_kind TEXT, access_path_numeric TEXT);")


def test_build_all_writes_bundles_no_gt(tmp_path):
    conn = sqlite3.connect(":memory:"); conn.executescript(_DDL)
    conn.execute("INSERT INTO access_fact (function,semantic_op,access_path_symbolic,numeric_kind,access_path_numeric)"
                 " VALUES ('io_prep_rw','read','io_ring_ctx.nr_user_bufs','gep_offsets','[8]')")
    src = tmp_path / "src"; (src / "io_uring").mkdir(parents=True)
    (src / "io_uring" / "rw.c").write_text("\n".join("l%d" % i for i in range(1, 40)) + "\n")
    gates = tmp_path / "gates"; gates.mkdir()
    json.dump({"target_gate": {"function": "io_prep_rw"},
               "slice_locations": [{"file_line": "io_uring/rw.c:20", "label": "gate"}],
               "ledger_functions": ["io_prep_rw"], "field_clues": ["buf_index"]},
              open(gates / "fixed_buffer.json", "w"))
    out = tmp_path / "bundles"
    w = build_all(conn, str(src), str(gates), str(out), ["fixed_buffer"])
    assert w == ["fixed_buffer"]
    b = json.load(open(out / "fixed_buffer.json"))
    assert b["target_gate"]["function"] == "io_prep_rw" and b["ledger"]
    assert "label" not in b and "truth" not in b   # RED LINE: no GT
