import json, sys
sys.path.insert(0, "scripts")
from run_multimodel import judge_stage


def test_judge_stage_writes_per_model_runs(tmp_path):
    bundles = tmp_path / "bundles"; bundles.mkdir()
    json.dump({"instructions": "x", "target_gate": {}, "predicate_schema": {}},
              open(bundles / "g1.json", "w"))
    calls = []
    def fake_runner(model, bundle):
        calls.append((model, bundle.get("target_gate")))
        return {"terms": [{"class": "premise"}], "abstain": False, "_parse_ok": True, "_model": model}
    out = tmp_path / "runs"
    res = judge_stage(str(bundles), ["deepseek-v4-flash", "deepseek-v4-pro"], str(out), runner=fake_runner)
    assert res["deepseek-v4-flash"]["g1"] == "ok" and len(calls) == 2
    saved = json.load(open(out / "deepseek-v4-flash" / "g1.json"))
    assert saved["terms"][0]["class"] == "premise"
