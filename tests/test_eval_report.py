from implicitfuzz.feedback.loop import run_loop
from implicitfuzz.feedback.eval_report import summarize
from implicitfuzz.feedback.pilot_adapter import build_snapshot

GATES_DIR = "src/implicitfuzz/pilot/gates"
RUNS_DIR = "pilot/runs_formal"
IDS = ["read_fixed_file", "fixed_buffer", "cancel_fixed_file", "msg_ring_fixed_file",
       "filetable_slot", "net_fixed_buffer", "poll_polled", "poll_double",
       "link_timeout", "kiocb_flags"]


def _stub_executor(pos, neg, sig):
    # both executable templates satisfy pos deeper than neg
    return (19, 0)


def test_summarize_two_layer_and_rerank():
    snap = build_snapshot(IDS, GATES_DIR, RUNS_DIR, "testsha")
    view = run_loop(snap["candidates"], _stub_executor, kernel_build_identity="k")
    s = summarize(snap, view)
    assert s["coverage"] == {"executable": 2, "total": 10,
                             "unsupported_reasons": {"op_not_covered": 4, "no_family_template": 4}}
    assert s["layer1_all_real"] == {"verified": 2, "unsupported": 8, "total": 10}
    assert s["layer2_executed"]["verified"] == 2 and s["layer2_executed"]["total"] == 2
    assert "frozen replay" in s["layer2_executed"]["note"]
    assert s["distribution_real"] == {"verified": 2, "contradicted": 0,
                                      "undecidable": 0, "unsupported": 8}
    # rerank: two verified promote to top (execution_verified), tie-broken by id
    assert s["rerank"]["after"][:2] == ["fixed_buffer", "read_fixed_file"]
    # before: all static-high, ordered by candidate_id
    assert s["rerank"]["before"] == sorted(IDS)
