#!/usr/bin/env python3
"""Kernel case5 checker: io_op_defs const dispatch table -> entry_fact.

Asserts the extractor reads opdef.c's io_op_defs constant initializer and emits
entry_fact rows mapping each opcode index to its prep/issue/cleanup/... handler.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load_facts(path: Path) -> list[dict]:
    facts: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                facts.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return facts


def check(facts: list[dict], gt: dict) -> list[str]:
    errors: list[str] = []
    entry_facts = [f for f in facts if f.get("fact_type") == "entry_fact"]

    op_dispatch = [f for f in entry_facts if f.get("entry_kind") == "op_dispatch"]
    if not op_dispatch:
        errors.append("no entry_fact with entry_kind=op_dispatch")
        return errors

    if not all(f.get("dispatch_table") == gt["dispatch_table"] for f in op_dispatch):
        errors.append(f"expected all dispatch_table == {gt['dispatch_table']!r}")

    indices = {f.get("dispatch_index") for f in op_dispatch}
    if len(indices) < gt["min_opcodes"]:
        errors.append(
            f"expected >= {gt['min_opcodes']} distinct opcodes, got {len(indices)}"
        )

    index_role = {
        (f.get("dispatch_index"), f.get("dispatch_role")): f.get("entry_symbol")
        for f in op_dispatch
    }
    for exp in gt["expected"]:
        key = (exp["index"], exp["role"])
        got = index_role.get(key)
        if got != exp["symbol"]:
            errors.append(
                f"io_op_defs[{exp['index']}].{exp['role']} expected "
                f"{exp['symbol']!r}, got {got!r}"
            )
    return errors


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_kernel_opdef_case5.py <facts.jsonl> <ground_truth.json>", file=sys.stderr)
        return 2
    facts = load_facts(Path(sys.argv[1]))
    gt = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    errors = check(facts, gt)
    if errors:
        print(f"kernel case5 FAILED ({sys.argv[1]})", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    n = sum(1 for f in facts if f.get("fact_type") == "entry_fact")
    print(
        f"kernel case5 OK: io_op_defs -> {n} entry_fact op_dispatch rows; "
        "opcode[22].issue -> io_read (READ_FIXED)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
