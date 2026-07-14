#!/usr/bin/env python3
"""Server-side: build the decontaminated cold-judge bundle for each gate and
dump it to <out-dir>/<gate>.json. RED LINE: bundles never contain ground truth
(build_bundle guarantees this). Usage:
  python3 scripts/build_bundles.py --db <facts.db> --source-root <kernel-src> \\
      --gates-dir src/implicitfuzz/pilot/gates --out-dir pilot/bundles [gate_id ...]"""
import argparse, json, os, sqlite3, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from implicitfuzz.pilot.context import build_bundle


def build_all(conn, source_root, gates_dir, out_dir, gate_ids):
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for gid in gate_ids:
        gate = json.load(open(os.path.join(gates_dir, gid + ".json")))
        bundle = build_bundle(conn, source_root, gate)
        assert "label" not in bundle and "truth" not in bundle, "GT leaked into bundle!"
        json.dump(bundle, open(os.path.join(out_dir, gid + ".json"), "w"), indent=1)
        written.append(gid)
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--gates-dir", default="src/implicitfuzz/pilot/gates")
    ap.add_argument("--out-dir", default="pilot/bundles")
    ap.add_argument("gate_ids", nargs="*")
    a = ap.parse_args()
    ids = a.gate_ids or [f[:-5] for f in sorted(os.listdir(a.gates_dir)) if f.endswith(".json")]
    conn = sqlite3.connect(a.db)
    w = build_all(conn, a.source_root, a.gates_dir, a.out_dir, ids)
    print("wrote %d bundles -> %s" % (len(w), a.out_dir))


if __name__ == "__main__":
    main()
