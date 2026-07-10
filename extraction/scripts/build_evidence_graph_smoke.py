#!/usr/bin/env python3
"""Build Phase 2A evidence graph over the Phase 1 facts database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = Path("/tmp/phase1_facts.db")


def ensure_import_path() -> None:
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def ensure_phase1_db(db_path: Path) -> None:
    if db_path.exists():
        return
    cmd = [
        sys.executable,
        str(ROOT / "extraction" / "scripts" / "ingest_phase1_smoke.py"),
        "--db",
        str(db_path),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite input/output path (default: {DEFAULT_DB})",
    )
    args = parser.parse_args()

    ensure_phase1_db(args.db)
    ensure_import_path()

    from implicitfuzz.evidence.graph import (
        build_evidence_graph,
        summarize_evidence_graph,
    )
    from implicitfuzz.evidence.gates import (
        derive_gate_seed_candidates,
        summarize_gate_seeds,
    )

    conn = sqlite3.connect(str(args.db))
    try:
        counts = build_evidence_graph(conn)
        summary = summarize_evidence_graph(conn)
        gate_seeds_inserted = derive_gate_seed_candidates(conn)
        gate_summary = summarize_gate_seeds(conn)
    finally:
        conn.close()

    print("phase2a evidence graph counts:")
    print(json.dumps(counts, indent=2, sort_keys=True))
    print("phase2a evidence graph summary:")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"gate_seed_candidates inserted this run: {gate_seeds_inserted}")
    print("gate_seed summary:")
    print(json.dumps(gate_summary, indent=2, sort_keys=True))

    # The builder is idempotent. On a rerun, inserted counts may be zero, so
    # smoke validation must use persisted totals from the summary.
    access_nodes = summary.get("nodes", {}).get("access", 0)
    state_edges = summary.get("edges", {}).get("state_write_read_candidate", 0)
    lifecycle_edges = summary.get("edges", {}).get("lifecycle_candidate", 0)
    identity_edges = summary.get("edges", {}).get("object_identity_candidate", 0)
    explicit_edges = summary.get("edges", {}).get("explicit_dependency_candidate", 0)

    if access_nodes < 700:
        raise SystemExit(f"expected at least 700 access nodes, got {access_nodes}")
    if state_edges < 1:
        raise SystemExit("expected at least one state_write_read_candidate edge")
    if lifecycle_edges < 1:
        raise SystemExit("expected at least one lifecycle_candidate edge")
    if identity_edges < 1:
        raise SystemExit("expected at least one object_identity_candidate edge")
    if explicit_edges < 1:
        raise SystemExit("expected at least one explicit_dependency_candidate edge")

    print(f"phase2a evidence graph smoke OK: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
