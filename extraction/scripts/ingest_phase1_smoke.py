#!/usr/bin/env python3
"""Ingest Phase 1 regression facts into SQLite and run sanity queries."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FACTS = [
    REPO_ROOT / "extraction" / "build" / "golden-facts.jsonl",
    Path("/tmp/io_uring_timeout.facts.jsonl"),
    Path("/tmp/io_uring_cancel.facts.jsonl"),
    Path("/tmp/io_uring_kbuf.facts.jsonl"),
]
DEFAULT_DB = Path("/tmp/phase1_facts.db")


def ensure_import_path() -> None:
    src = REPO_ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def require_file(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"missing required facts file: {path}")


def table_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0])


def fetch_symbolic(
    conn: sqlite3.Connection, symbolic: str, field_type: str
) -> tuple[int, str]:
    row = conn.execute(
        """
        SELECT COUNT(*), COALESCE(MIN(function), '')
        FROM access_fact
        WHERE access_path_symbolic = ? AND field_type = ?
        """,
        (symbolic, field_type),
    ).fetchone()
    return int(row[0]), str(row[1] or "")


def fetch_op(
    conn: sqlite3.Connection,
    function: str,
    semantic_op: str,
    access_kind: str,
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM access_fact
        WHERE function = ? AND semantic_op = ? AND access_kind = ?
        """,
        (function, semantic_op, access_kind),
    ).fetchone()
    return int(row[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite output path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--facts",
        type=Path,
        action="append",
        help="facts JSONL to ingest; may be passed multiple times",
    )
    parser.add_argument(
        "--keep-db",
        action="store_true",
        help="append to an existing database instead of recreating it",
    )
    args = parser.parse_args()

    ensure_import_path()
    from implicitfuzz.ingestion.ingest import ingest_jsonl

    facts_paths = args.facts or DEFAULT_FACTS
    for path in facts_paths:
        require_file(path)

    initial_counts = {"access_fact": 0, "call_fact": 0}
    if args.db.exists() and args.keep_db:
        conn = sqlite3.connect(str(args.db))
        try:
            for table in initial_counts:
                try:
                    initial_counts[table] = table_count(conn, table)
                except sqlite3.OperationalError:
                    initial_counts[table] = 0
        finally:
            conn.close()

    if args.db.exists() and not args.keep_db:
        args.db.unlink()

    total: Counter[str] = Counter()
    for path in facts_paths:
        counts = ingest_jsonl(str(path), str(args.db))
        total.update(counts)
        print(f"ingested {path}: {dict(counts)}")

    conn = sqlite3.connect(str(args.db))
    try:
        raw_counts = {
            "access_fact": table_count(conn, "access_fact"),
            "call_fact": table_count(conn, "call_fact"),
        }
        print(f"sqlite counts: {raw_counts}")
        expected = {
            "access_fact": initial_counts["access_fact"] + total.get("access_fact", 0),
            "call_fact": initial_counts["call_fact"] + total.get("call_fact", 0),
        }
        if raw_counts.get("access_fact", 0) != expected.get("access_fact", 0):
            raise SystemExit(
                f"access_fact count mismatch: sqlite={raw_counts.get('access_fact')} expected={expected.get('access_fact')}"
            )
        if raw_counts.get("call_fact", 0) != expected.get("call_fact", 0):
            raise SystemExit(
                f"call_fact count mismatch: sqlite={raw_counts.get('call_fact')} expected={expected.get('call_fact')}"
            )

        required_symbolics = [
            ("Node.value", "int"),
            ("Node.next", "struct Node *"),
            ("io_timeout.off", "u32"),
            ("io_hash_bucket.list", "struct hlist_head"),
        ]
        for symbolic, field_type in required_symbolics:
            count, function = fetch_symbolic(conn, symbolic, field_type)
            if count <= 0:
                raise SystemExit(f"missing symbolic fact: {symbolic} / {field_type}")
            print(f"symbolic OK: {symbolic} / {field_type} ({count}, first function={function})")

        required_ops = [
            ("main", "alloc", "call_alloc"),
            ("main", "free", "call_free"),
            ("io_provide_buffers", "alloc", "call_alloc"),
            ("io_provide_buffers", "free", "call_free"),
        ]
        for function, semantic_op, access_kind in required_ops:
            count = fetch_op(conn, function, semantic_op, access_kind)
            if count <= 0:
                raise SystemExit(
                    f"missing op fact: {function} {semantic_op} {access_kind}"
                )
            print(f"op OK: {function} {semantic_op} {access_kind} ({count})")

        by_op = conn.execute(
            """
            SELECT semantic_op, COUNT(*)
            FROM access_fact
            GROUP BY semantic_op
            ORDER BY semantic_op
            """
        ).fetchall()
        print("semantic_op distribution:")
        for semantic_op, count in by_op:
            print(f"  {semantic_op}: {count}")

    finally:
        conn.close()

    print(f"phase1 ingestion smoke OK: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
