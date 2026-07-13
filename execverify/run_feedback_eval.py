#!/usr/bin/env python3
"""Server-side driver for the small-scale confidence-feedback evaluation.

Loads the frozen candidate snapshot, runs the loop with the real execverify
executor (only strictly-executable candidates boot qemu; unsupported ones skip
execution), and writes {snapshot provenance, calibrated view, summary} where
summary = two-layer metrics + distribution + rerank before/after. Usage:

  PYTHONPATH=src python3 execverify/run_feedback_eval.py \
      --snapshot feedback/eval/frozen_candidates.json \
      --out feedback/eval/calibrated-eval-<date>.json
"""
import argparse, json, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "..", "src"))
sys.path.insert(0, ROOT)  # for run_feedback_loop
from implicitfuzz.feedback.loop import run_loop            # noqa: E402
from implicitfuzz.feedback.eval_report import summarize    # noqa: E402
from run_feedback_loop import make_executor, _kernel_identity  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    snap = json.load(open(args.snapshot))
    view = run_loop(snap["candidates"], make_executor(),
                    kernel_build_identity=_kernel_identity())
    summary = summarize(snap, view)
    payload = {
        "snapshot_provenance": {k: snap[k] for k in
            ("source_commit", "gate_ids", "adapter_version", "schema_version",
             "generated_hash", "generated_date") if k in snap},
        "calibrated_view": view,
        "summary": summary,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(payload, open(args.out, "w"), indent=2)
    print(json.dumps(summary, indent=2))
    print("\nwrote %s" % args.out, file=sys.stderr)


if __name__ == "__main__":
    main()
