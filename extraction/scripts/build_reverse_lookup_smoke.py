#!/usr/bin/env python3
"""Reverse-lookup smoke: ingest multiple TU facts -> gate seeds -> reverse lookup.

Demonstrates cross-TU gate->prior-write reverse lookup on a merged DB
(e.g. io_uring rw.c gated read + rsrc.c prior write). Requires >=1 cross-TU
prior write to pass.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from implicitfuzz.ingestion.ingest import ingest_jsonl
from implicitfuzz.evidence.gates import derive_gate_seed_candidates
from implicitfuzz.evidence.reverse_lookup import (
    derive_gate_prior_write_candidates,
    summarize_reverse_lookup,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--facts", action="append", required=True,
        help="one or more JSONL fact files (repeat)",
    )
    ap.add_argument("--db", default="/tmp/reverse_lookup_smoke.db")
    args = ap.parse_args()

    db = args.db
    Path(db).unlink(missing_ok=True)
    for facts in args.facts:
        counts = ingest_jsonl(facts, db)
        print(f"ingested {facts}: {counts}")

    conn = sqlite3.connect(db)
    seeds = derive_gate_seed_candidates(conn)
    rows = derive_gate_prior_write_candidates(conn)
    summ = summarize_reverse_lookup(conn)
    print(f"gate_seed_candidates derived: {seeds}")
    print(f"gate_prior_write_candidate rows: {rows}")
    print(f"summary: {summ}")

    # show cross-TU prior writes for human inspection
    conn.row_factory = sqlite3.Row
    for r in conn.execute(
        "SELECT p.field_key, p.write_function, p.write_bc_unit, g.bc_unit AS gate_bc, "
        "p.write_source_location FROM gate_prior_write_candidate p "
        "JOIN gate_seed_candidate g ON g.id = p.gate_seed_id "
        "WHERE p.write_bc_unit != g.bc_unit LIMIT 20"
    ).fetchall():
        print(
            f"  CROSS-TU {r['field_key']}  write {r['write_function']}@{r['write_bc_unit']} "
            f"({r['write_source_location']})  gated-in {r['gate_bc']}"
        )
    conn.close()

    assert summ["cross_tu_prior_writes"] >= 1, "expected >=1 cross-TU prior write"
    print("reverse-lookup smoke OK:", db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
