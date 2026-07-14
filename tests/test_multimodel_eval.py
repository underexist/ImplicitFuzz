from implicitfuzz.pilot.multimodel_eval import (
    score_run, aggregate_models, guard_consistency, inter_model_agreement)

TRUTH = {"terms": [{"class": "activation", "field_ref": "io_ring_ctx.nr_user_files", "align_target": ""}]}
GOOD = {"terms": [{"class": "activation", "field_ref": "io_ring_ctx.nr_user_files", "align_target": ""}],
        "abstain": False, "_parse_ok": True}
BADSCHEMA = {"terms": [{"class": "not_a_class", "field_ref": "x.y"}], "abstain": False, "_parse_ok": True}


def test_score_run_no_conn_skips_field_existence():
    s = score_run(GOOD, TRUTH, conn=None)
    assert s["field_existence"] is None
    assert s["exact"]["precision"] == 1.0 and s["exact"]["recall"] == 1.0
    assert s["schema"] in (True, False)  # depends on real schema; smoke


def test_aggregate_models_means_and_rates():
    scored = {"m": {
        "g1": {"exact": {"precision": 1.0, "recall": 1.0}, "relaxed": {"field_set": {"precision": 1.0, "recall": 1.0}},
               "abstain": False, "parse_ok": True, "schema": True},
        "g2": {"exact": {"precision": 0.0, "recall": 0.0}, "relaxed": {"field_set": {"precision": 0.0, "recall": 0.0}},
               "abstain": True, "parse_ok": True, "schema": False}}}
    a = aggregate_models(scored)["m"]
    assert a["n_gates"] == 2 and a["exact_precision"] == 0.5
    assert a["abstain_rate"] == 0.5 and a["schema_ok_rate"] == 0.5


def test_guard_consistency_fires_on_schema_or_field_fail():
    scored = {"m": {
        "neg1": {"schema": False, "field_existence": {"passed": True}},
        "neg2": {"schema": True, "field_existence": {"passed": False}},
        "neg3": {"schema": True, "field_existence": {"passed": True}}}}
    g = guard_consistency(scored, ["neg1", "neg2", "neg3"])["m"]
    assert g["neg1"] is True and g["neg2"] is True and g["neg3"] is False


def test_inter_model_agreement_jaccard():
    runs = {"a": {"g": {"terms": [{"class": "activation", "field_ref": "s.x", "align_target": ""}]}},
            "b": {"g": {"terms": [{"class": "activation", "field_ref": "s.x", "align_target": ""},
                                   {"class": "premise", "field_ref": "s.y", "align_target": ""}]}}}
    per = inter_model_agreement(runs)["g"]
    assert per["field_jaccard"] == 0.5  # {s.x} ∩ {s.x,s.y} / ∪ = 1/2
