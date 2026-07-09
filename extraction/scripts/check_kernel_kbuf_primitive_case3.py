#!/usr/bin/env python3
"""Validate kernel kbuf primitive alloc/free golden expectations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{i}: invalid JSON: {exc}") from exc
    return out


def source_hits(rec: dict, needle: str) -> bool:
    src = rec.get("source_location") or {}
    for key in ("spelling", "expansion"):
        val = src.get(key, "")
        if needle in val:
            return True
    return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("facts_jsonl", type=Path)
    p.add_argument("ground_truth_json", type=Path)
    args = p.parse_args()

    facts = load_jsonl(args.facts_jsonl)
    gt = json.loads(args.ground_truth_json.read_text(encoding="utf-8"))

    prov = gt["expected_primary_provenance"]
    fn_alloc = gt["target_function_alloc"]
    loc_free = gt["target_source_location_free"]

    alloc_hits = [
        f
        for f in facts
        if f.get("fact_type") == "access_fact"
        and f.get("function") == fn_alloc
        and f.get("semantic_op") == gt["expected_semantic_op_alloc"]
        and f.get("access_kind") == gt["expected_access_kind_alloc"]
        and f.get("primary_provenance") == prov
    ]
    free_hits = [
        f
        for f in facts
        if f.get("fact_type") == "access_fact"
        and f.get("semantic_op") == gt["expected_semantic_op_free"]
        and f.get("access_kind") == gt["expected_access_kind_free"]
        and f.get("primary_provenance") == prov
        and source_hits(f, loc_free)
    ]

    errors: list[str] = []
    if not alloc_hits:
        errors.append(
            f"missing primitive alloc: function={fn_alloc} "
            f"semantic_op={gt['expected_semantic_op_alloc']}"
        )
    if not free_hits:
        errors.append(f"missing primitive free at source location {loc_free}")

    stage1 = gt.get("stage1_acceptance") or {}
    summary_alloc = sum(
        1
        for f in facts
        if f.get("fact_type") == "access_fact"
        and f.get("semantic_op") == "alloc"
        and f.get("access_kind") == "call_alloc"
        and f.get("primary_provenance") == prov
    )
    summary_free = sum(
        1
        for f in facts
        if f.get("fact_type") == "access_fact"
        and f.get("semantic_op") == "free"
        and f.get("access_kind") == "call_free"
        and f.get("primary_provenance") == prov
    )
    min_alloc = stage1.get("min_tu_primitive_alloc_hits")
    min_free = stage1.get("min_tu_primitive_free_hits")
    if min_alloc is not None and summary_alloc < min_alloc:
        errors.append(f"TU primitive alloc hits {summary_alloc} < {min_alloc}")
    if min_free is not None and summary_free < min_free:
        errors.append(f"TU primitive free hits {summary_free} < {min_free}")

    if errors:
        print("kernel case3 FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        "kernel case3 OK: "
        f"{fn_alloc} -> call_alloc (primitive); "
        f"{loc_free} -> call_free (primitive); "
        f"TU hits alloc={summary_alloc} free={summary_free}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
