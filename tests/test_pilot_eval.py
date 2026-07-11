from implicitfuzz.pilot.eval import score_terms, build_audit_record


def _t(cls, fr, align=""):
    return {"class": cls, "field_ref": fr, "align_target": align}


def test_score_terms_precision_recall():
    judge = [_t("activation", "io_ring_ctx.nr_user_files"),
             _t("param_align", "io_ring_ctx.nr_user_files", "sqe->fd"),
             _t("premise", "io_ring_ctx.bogus")]           # wrong
    truth = [_t("activation", "io_ring_ctx.nr_user_files"),
             _t("param_align", "io_ring_ctx.nr_user_files", "sqe->fd"),
             _t("premise", "io_ring_ctx.file_data")]        # judge missed
    s = score_terms(judge, truth)
    assert s["matched"] == 2
    assert abs(s["precision"] - 2 / 3) < 1e-9
    assert abs(s["recall"] - 2 / 3) < 1e-9


def test_build_audit_record_shape():
    rec = build_audit_record(
        gate_id="read_fixed_file",
        bundle={"target_gate": {"function": "io_file_get_fixed"}},
        judge_output={"terms": []},
        validation={"field_existence": {"passed": True}},
        score={"precision": 1.0, "recall": 1.0},
        human_score="correct", rationale="param_align aligns with register nr_args",
    )
    for k in ("gate_id", "bundle", "judge_output", "validation", "score",
              "human_score", "rationale"):
        assert k in rec
    assert rec["gate_id"] == "read_fixed_file"
