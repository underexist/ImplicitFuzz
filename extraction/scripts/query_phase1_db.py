#!/usr/bin/env python3
"""Query the Phase 1 SQLite evidence store."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = Path("/tmp/phase1_facts.db")


def ensure_import_path() -> None:
    src = REPO_ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def print_json(data: object) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    summary_p = sub.add_parser("summary", help="export fact counts and recovery summary")
    summary_p.set_defaults(command="summary")

    symbolic_p = sub.add_parser("symbolic", help="list symbolic field accesses")
    symbolic_p.add_argument("--limit", type=int, default=50)

    lifecycle_p = sub.add_parser("lifecycle", help="list alloc/free/retain/release facts")
    lifecycle_p.add_argument("--limit", type=int, default=100)

    field_p = sub.add_parser("field", help="list accesses matching a struct.field name")
    field_p.add_argument("field")
    field_p.add_argument("--limit", type=int, default=100)

    args = parser.parse_args()
    if not args.db.exists():
        raise SystemExit(f"database not found: {args.db}")

    ensure_import_path()
    from implicitfuzz.ingestion.queries import (
        connect,
        export_summary,
        list_accesses_by_field,
        list_lifecycle_ops,
        list_symbolic_accesses,
    )

    conn = connect(args.db)
    try:
        if args.command == "summary":
            print_json(export_summary(conn))
        elif args.command == "symbolic":
            print_json(list_symbolic_accesses(conn, args.limit))
        elif args.command == "lifecycle":
            print_json(list_lifecycle_ops(conn, args.limit))
        elif args.command == "field":
            print_json(list_accesses_by_field(conn, args.field, args.limit))
        else:
            raise SystemExit(f"unknown command: {args.command}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
