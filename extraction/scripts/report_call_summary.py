#!/usr/bin/env python3
"""Summarize primitive summary hits and unresolved direct calls in facts JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def load_facts(path: Path) -> list[dict]:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("facts_jsonl", type=Path)
    parser.add_argument(
        "--report",
        type=Path,
        help="optional markdown report path",
    )
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    facts = load_facts(args.facts_jsonl)
    call_facts = [f for f in facts if f.get("fact_type") == "call_fact"]
    access_facts = [f for f in facts if f.get("fact_type") == "access_fact"]
    summary_facts = [
        f for f in access_facts if f.get("primary_provenance") == "summary"
    ]

    primitive_keys = {
        (f.get("function"), f.get("instruction_id")) for f in summary_facts
    }

    def is_wrapper_propagation(fact: dict) -> bool:
        detail = fact.get("summary_detail") or {}
        return detail.get("match_kind") == "wrapper_propagation"

    wrapper_facts = [f for f in summary_facts if is_wrapper_propagation(f)]
    direct_primitive_facts = [
        f for f in summary_facts if not is_wrapper_propagation(f)
    ]

    def count(
        facts: list[dict],
        semantic_op: str | None = None,
        access_kind: str | None = None,
    ) -> int:
        n = 0
        for f in facts:
            if semantic_op is not None and f.get("semantic_op") != semantic_op:
                continue
            if access_kind is not None and f.get("access_kind") != access_kind:
                continue
            n += 1
        return n

    alloc_n = count(summary_facts, "alloc", "call_alloc")
    free_n = count(summary_facts, "free", "call_free")
    free_async_n = count(summary_facts, "free_async", "call_free")
    retain_n = count(summary_facts, "retain", "call_arg")
    release_n = count(summary_facts, "release", "call_arg")
    usercopy_n = count(summary_facts, access_kind="usercopy")
    wrapper_alloc_n = count(wrapper_facts, "alloc", "call_alloc")
    wrapper_free_n = count(wrapper_facts, "free", "call_free")
    other_n = len(summary_facts) - (
        alloc_n + free_n + free_async_n + retain_n + release_n + usercopy_n
    )

    unresolved: list[str] = []
    for c in call_facts:
        key = (c.get("function"), c.get("instruction_id"))
        if key not in primitive_keys:
            unresolved.append(c.get("callee", "<unknown>"))

    unresolved_counts = Counter(unresolved)
    top = unresolved_counts.most_common(args.top)

    matched_callees = Counter(
        c.get("callee", "<unknown>")
        for c in call_facts
        if (c.get("function"), c.get("instruction_id")) in primitive_keys
    )

    lines = [
        f"# Call Summary: {args.facts_jsonl.name}",
        "",
        "## Primitive hits (primary_provenance=summary)",
        "",
        f"- total summary access_fact: {len(summary_facts)}",
        f"- direct primitive hits: {len(direct_primitive_facts)}",
        f"- wrapper propagation hits: {len(wrapper_facts)}",
        f"- call_alloc (semantic_op=alloc): {alloc_n}",
        f"- call_free (semantic_op=free): {free_n}",
        f"- wrapper call_alloc: {wrapper_alloc_n}",
        f"- wrapper call_free: {wrapper_free_n}",
        f"- call_free (semantic_op=free_async): {free_async_n}",
        f"- call_arg (semantic_op=retain): {retain_n}",
        f"- call_arg (semantic_op=release): {release_n}",
        f"- usercopy: {usercopy_n}",
        f"- other summary facts: {other_n}",
        f"- total call_fact: {len(call_facts)}",
        f"- unresolved direct calls: {len(unresolved)}",
        "",
        "## Matched callees",
        "",
    ]
    if not matched_callees:
        lines.append("- (none)")
    else:
        for name, hit_count in matched_callees.most_common(args.top):
            lines.append(f"- `{name}`: {hit_count}")

    lines.extend(["", "## Top unresolved callees", ""])
    if not top:
        lines.append("- (none)")
    else:
        for name, hit_count in top:
            lines.append(f"- `{name}`: {hit_count}")

    report = "\n".join(lines) + "\n"
    print(report, end="")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        print(f"report: {args.report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
