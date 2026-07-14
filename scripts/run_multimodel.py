#!/usr/bin/env python3
"""Two-stage multi-model eval CLI.
  judge (Mac, needs internet + ~/.config/implicitfuzz/llm.env):
     for each model x bundle -> run_judge -> <out>/<model>/<gate>.json
  score (server, needs facts DB for field-existence guard):
     load runs + labels + optional Claude runs_formal -> score_run + aggregate."""
import argparse, json, os, sqlite3, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from implicitfuzz.pilot.judge_adapter import run_judge
from implicitfuzz.pilot.multimodel_eval import (
    score_run, aggregate_models, guard_consistency, inter_model_agreement)


def judge_stage(bundles_dir, models, out_root, runner=run_judge):
    gate_ids = [f[:-5] for f in sorted(os.listdir(bundles_dir)) if f.endswith(".json")]
    status = {}
    for model in models:
        mdir = os.path.join(out_root, model)
        os.makedirs(mdir, exist_ok=True)
        status[model] = {}
        for gid in gate_ids:
            bundle = json.load(open(os.path.join(bundles_dir, gid + ".json")))
            out = runner(model, bundle)
            json.dump(out, open(os.path.join(mdir, gid + ".json"), "w"), indent=1)
            status[model][gid] = "ok"
    return status


def _load_runs(runs_root, model):
    mdir = os.path.join(runs_root, model)
    return {f[:-5]: json.load(open(os.path.join(mdir, f)))
            for f in sorted(os.listdir(mdir)) if f.endswith(".json")}


def score_stage(runs_root, labels_dir, models, negatives, conn, extra_runs=None):
    labels = {f[:-5]: json.load(open(os.path.join(labels_dir, f)))
              for f in os.listdir(labels_dir) if f.endswith(".json")}
    runs_by_model = {m: _load_runs(runs_root, m) for m in models}
    if extra_runs:
        runs_by_model.update(extra_runs)
    positives = [g for g in labels if g not in negatives]
    scored = {m: {g: score_run(runs.get(g, {"terms": []}), labels[g], conn)
                  for g in labels}
              for m, runs in runs_by_model.items()}
    return {
        "per_model": aggregate_models({m: {g: s[g] for g in positives} for m, s in scored.items()}),
        "guard_consistency": guard_consistency(scored, negatives),
        "inter_model_agreement": inter_model_agreement(runs_by_model),
        "models": list(runs_by_model),
    }


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("judge")
    j.add_argument("--bundles-dir", default="pilot/bundles")
    j.add_argument("--out-root", default="pilot/runs_multimodel")
    j.add_argument("--models", nargs="+", default=["deepseek-v4-flash", "deepseek-v4-pro"])
    s = sub.add_parser("score")
    s.add_argument("--runs-root", default="pilot/runs_multimodel")
    s.add_argument("--labels-dir", default="src/implicitfuzz/pilot/labels")
    s.add_argument("--db", required=True)
    s.add_argument("--models", nargs="+", default=["deepseek-v4-flash", "deepseek-v4-pro"])
    s.add_argument("--negatives", nargs="+",
                   default=["neg_writer_omitted", "neg_thin_slice", "neg_adversarial"])
    s.add_argument("--baseline-runs-formal", default="pilot/runs_formal")
    s.add_argument("--out", default="pilot/runs_multimodel/summary.json")
    a = ap.parse_args()
    if a.cmd == "judge":
        st = judge_stage(a.bundles_dir, a.models, a.out_root)
        print(json.dumps(st, indent=1))
    else:
        conn = sqlite3.connect(a.db)
        extra = None
        if a.baseline_runs_formal and os.path.isdir(a.baseline_runs_formal):
            extra = {"claude-opus-baseline": {
                f[:-5]: json.load(open(os.path.join(a.baseline_runs_formal, f)))
                for f in os.listdir(a.baseline_runs_formal) if f.endswith(".json")}}
        summary = score_stage(a.runs_root, a.labels_dir,
                              a.models + (["claude-opus-baseline"] if extra else []),
                              a.negatives, conn, extra_runs=extra)
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        json.dump(summary, open(a.out, "w"), indent=2)
        print(json.dumps(summary["per_model"], indent=2))


if __name__ == "__main__":
    main()
