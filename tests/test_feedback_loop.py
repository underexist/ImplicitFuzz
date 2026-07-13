import copy
from implicitfuzz.feedback.loop import run_loop


def _cands():
    return [
        {"candidate_id": "ff_real", "candidate_origin": "replayed", "gate_family": "fixed_file",
         "static_confidence": "high", "param_align": {"relation": "<"}, "signal_function": "io_read"},
        {"candidate_id": "ff_mutant", "candidate_origin": "synthetic_negative_control",
         "gate_family": "fixed_file", "static_confidence": "high",
         "param_align": {"relation": ">="}, "signal_function": "io_read"},
        {"candidate_id": "flags", "candidate_origin": "replayed", "gate_family": "io_kiocb_flags_premise",
         "static_confidence": "medium", "param_align": {"relation": "<"}, "signal_function": "io_read"},
    ]


def _stub_executor(pos, neg, sig):
    # fd_index=1 (valid) reaches io_read; fd_index=5 (OOB) does not.
    def cov(prog):
        return 19 if "@fd_index=0x1" in prog else 0
    return cov(pos), cov(neg)


def test_loop_three_update_actions_and_immutability():
    cands = _cands(); original = copy.deepcopy(cands)
    view = run_loop(cands, _stub_executor, kernel_build_identity="bzImage@abc123")
    by = {r["candidate_id"]: r for r in view}
    assert by["ff_real"]["execution_status"] == "verified"
    assert by["ff_real"]["final_confidence"] == "execution_verified"
    assert by["ff_mutant"]["execution_status"] == "contradicted"
    assert by["ff_mutant"]["final_confidence"] == "rejected"
    assert by["ff_mutant"]["candidate_origin"] == "synthetic_negative_control"
    assert by["flags"]["execution_status"] == "unsupported"
    assert by["flags"]["final_confidence"] == "medium"          # static held
    # provenance present
    assert by["ff_real"]["kernel_build_identity"] == "bzImage@abc123"
    assert by["ff_real"]["pos_coverage"] == 19 and by["ff_real"]["neg_coverage"] == 0
    # rerank: execution_verified first, rejected last
    assert view[0]["candidate_id"] == "ff_real" and view[-1]["candidate_id"] == "ff_mutant"
    # RED LINE: original candidates unchanged
    assert cands == original
