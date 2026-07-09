#!/usr/bin/env python3
"""Validate kernel cancel case2 struct-typed GEP golden expectations."""

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


def numeric_of(rec: dict) -> list[int] | None:
    val = rec.get("access_path_numeric")
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else None
        except json.JSONDecodeError:
            return None
    ap = rec.get("access_path") or {}
    parsed = ap.get("numeric")
    return parsed if isinstance(parsed, list) else None


def gep_source_element_type(rec: dict) -> str | None:
    ap = rec.get("access_path") or {}
    steps = (ap.get("gep_raw") or {}).get("steps") or []
    if not steps:
        return None
    val = steps[0].get("source_element_type")
    return val if isinstance(val, str) else None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("facts_jsonl", type=Path)
    p.add_argument("ground_truth_json", type=Path)
    args = p.parse_args()

    gt = json.loads(args.ground_truth_json.read_text(encoding="utf-8"))
    facts = load_jsonl(args.facts_jsonl)

    target = gt["target_source_location"]
    expect_symbolic = gt["expected_access_path_symbolic"]
    expect_type = gt["expected_field_type"]
    expect_semantic = gt["expected_semantic_op"]
    expect_access_kind = gt["expected_access_kind"]
    expect_numeric_kind = gt["expected_numeric_kind"]
    expect_numeric = gt.get("expected_numeric")
    expect_src_prefix = gt.get("expected_gep_source_element_type_prefix", "%struct.")

    hits: list[dict] = []
    for rec in facts:
        if rec.get("fact_type") != "access_fact":
            continue
        src = rec.get("source_location") or {}
        spelling = src.get("spelling", "")
        expansion = src.get("expansion", "")
        if target not in spelling and target not in expansion:
            continue
        hits.append(rec)

    if not hits:
        print(f"kernel case2 FAILED: no access_fact hit for {target}", file=sys.stderr)
        return 1

    best = None
    for rec in hits:
        src_ty = gep_source_element_type(rec)
        if not src_ty or src_ty == "i8":
            continue
        if (
            rec.get("semantic_op") == expect_semantic
            and rec.get("access_kind") == expect_access_kind
            and rec.get("numeric_kind") == expect_numeric_kind
            and rec.get("access_path_symbolic")
            and rec.get("field_type")
            and rec.get("primary_provenance") == "dwarf"
        ):
            if expect_numeric is not None and numeric_of(rec) != expect_numeric:
                continue
            best = rec
            break

    if best is None:
        print(
            "kernel case2 FAILED: source hit exists but struct-GEP/dwarf assertions not met",
            file=sys.stderr,
        )
        return 1

    errors: list[str] = []
    src_ty = gep_source_element_type(best) or ""
    if src_ty == "i8":
        errors.append("gep_raw.source_element_type must not be i8")
    if not src_ty.startswith(expect_src_prefix):
        errors.append(
            f'gep source_element_type expected prefix "{expect_src_prefix}", got {src_ty!r}'
        )
    if best.get("access_path_symbolic") != expect_symbolic:
        errors.append(
            f'access_path_symbolic expected "{expect_symbolic}", got {best.get("access_path_symbolic")!r}'
        )
    if best.get("field_type") != expect_type:
        errors.append(f'field_type expected "{expect_type}", got {best.get("field_type")!r}')

    if errors:
        print("kernel case2 FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        "kernel case2 OK: "
        f'{target} -> {best.get("access_path_symbolic")} / {best.get("field_type")}'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
