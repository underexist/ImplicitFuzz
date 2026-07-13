#!/usr/bin/env python3
"""Server-side driver for the confidence-feedback closed loop.

Loads candidate JSONs, assembles pos/neg progs (feedback.assemble), runs the
real execverify executor (feedback_executor.sh -> analyze_cover.py), and drives
feedback.loop.run_loop to emit a calibrated view (reranked, provenance,
original candidates untouched). Usage:

  PYTHONPATH=src python3 execverify/run_feedback_loop.py \
      src/implicitfuzz/feedback/candidates/*.json \
      --out feedback/calibrated/run-<date>.json
"""
import argparse, json, subprocess, sys, tempfile, os, glob

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "..", "src"))
from implicitfuzz.feedback.loop import run_loop  # noqa: E402


def _kernel_identity():
    bz = "/home/xujunru/syzkaller-docker/out/kernel/bzImage"
    try:
        h = subprocess.run(["sha256sum", bz], capture_output=True, text=True).stdout.split()[0]
        return "bzImage@" + h[:12]
    except Exception:
        return "bzImage@unknown"


def make_executor():
    def executor(pos_prog, neg_prog, signal_func):
        with tempfile.NamedTemporaryFile("w", suffix=".syz", delete=False) as fp, \
             tempfile.NamedTemporaryFile("w", suffix=".syz", delete=False) as fn:
            fp.write(pos_prog); pos_path = fp.name
            fn.write(neg_prog); neg_path = fn.name
        try:
            r = subprocess.run(
                ["bash", os.path.join(ROOT, "feedback_executor.sh"),
                 pos_path, neg_path, signal_func or "io_read"],
                capture_output=True, text=True)
            try:
                res = json.loads(r.stdout)
                return res["signal_pos"], res["signal_neg"]
            except (json.JSONDecodeError, KeyError):
                sys.stderr.write("[executor] parse failure:\n" + r.stdout + r.stderr + "\n")
                return None, None
        finally:
            os.unlink(pos_path); os.unlink(neg_path)
    return executor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidates", nargs="+")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    paths = []
    for c in args.candidates:
        paths.extend(sorted(glob.glob(c)) if any(ch in c for ch in "*?[") else [c])
    cands = [json.load(open(p)) for p in paths]

    view = run_loop(cands, make_executor(), kernel_build_identity=_kernel_identity())
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(view, open(args.out, "w"), indent=2)
    print(json.dumps(view, indent=2))
    print("\nwrote %s" % args.out, file=sys.stderr)


if __name__ == "__main__":
    main()
