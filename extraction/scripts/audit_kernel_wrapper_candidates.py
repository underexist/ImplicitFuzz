#!/usr/bin/env python3
"""Phase 1.5: audit kernel TU bitcode for conservative wrapper candidates.

Scans llvm-dis IR for the same narrow rules as wrapper propagation v0:
  - alloc wrapper: return value directly from alloc primitive call (one alloca hop)
  - free wrapper: formal parameter directly passed to free primitive (one alloca hop)

Does not implement propagation; writes extraction/reports/kernel-wrapper-candidates.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ALLOC_PRIMITIVES = (
    "malloc",
    "kmalloc",
    "kzalloc",
    "__kmalloc",
    "kvmalloc",
    "kvzalloc",
    "kmem_cache_alloc",
    "kmem_cache_zalloc",
    "kmem_cache_alloc_node",
)

FREE_PRIMITIVES = (
    "free",
    "kfree",
    "kvfree",
    "kmem_cache_free",
    "kfree_sensitive",
)

LLVM_DIS = Path(
    "/home/xujunru/.local/opt/llvm21-rpm/usr/lib64/llvm21/bin/llvm-dis"
)


@dataclass
class FunctionIR:
    name: str
    params: list[str]
    body_lines: list[str]
    is_definition: bool = True


@dataclass
class CandidateResult:
    function: str
    tu: str
    rule: str
    matched: bool
    primitive: str = ""
    reason: str = ""
    next_action: str = ""
    call_sites_in_tu: int = 0
    in_tu_definition: bool = False


def load_unresolved_callees(facts_jsonl: Path) -> Counter[str]:
    facts: list[dict] = []
    with facts_jsonl.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                facts.append(json.loads(line))

    summary_keys = {
        (f.get("function"), f.get("instruction_id"))
        for f in facts
        if f.get("fact_type") == "access_fact"
        and f.get("primary_provenance") == "summary"
    }

    unresolved: list[str] = []
    for fact in facts:
        if fact.get("fact_type") != "call_fact":
            continue
        key = (fact.get("function"), fact.get("instruction_id"))
        if key not in summary_keys:
            unresolved.append(fact.get("callee", "<unknown>"))
    return Counter(unresolved)


def disassemble_bitcode(bc_path: Path) -> str:
    if not LLVM_DIS.is_file():
        raise SystemExit(f"llvm-dis not found: {LLVM_DIS}")
    llvm_ld = "/home/xujunru/.local/opt/llvm21-rpm/usr/lib64"
    llvm21_ld = "/home/xujunru/.local/opt/llvm21-rpm/usr/lib64/llvm21/lib64"
    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)
    env["LD_LIBRARY_PATH"] = f"{llvm_ld}:{llvm21_ld}"
    proc = subprocess.run(
        [str(LLVM_DIS), str(bc_path), "-o", "-"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.stdout


def parse_functions(ir_text: str) -> tuple[dict[str, FunctionIR], set[str]]:
    defined: dict[str, FunctionIR] = {}
    declared: set[str] = set()

    func_header = re.compile(
        r"^(define|declare)\b.*\s+@([A-Za-z0-9_.]+)\((.*?)\)"
    )
    current: FunctionIR | None = None
    brace_depth = 0

    for line in ir_text.splitlines():
        header = func_header.match(line.strip())
        if header:
            if current and current.is_definition:
                defined[current.name] = current
            kind, name, params_blob = header.groups()
            params = []
            if params_blob.strip():
                for part in params_blob.split(","):
                    token = part.strip().split()[-1]
                    if token.startswith("%"):
                        params.append(token)
            current = FunctionIR(
                name=name,
                params=params,
                body_lines=[],
                is_definition=kind == "define",
            )
            if kind == "declare":
                declared.add(name)
            brace_depth = 1 if "{" in line else 0
            continue

        if current is None:
            continue

        if current.is_definition:
            current.body_lines.append(line)
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0:
                defined[current.name] = current
                current = None
        else:
            current = None

    return defined, declared


def peel_cast(value: str) -> str:
    value = value.strip()
    while True:
        m = re.match(r"(?:bitcast|addrspacecast|getelementptr)\b.*\s(%\w+)\s+to\b", value)
        if not m:
            break
        value = m.group(1)
    return value.strip().rstrip(",")


def trace_local_slot(value: str, body_lines: list[str]) -> str:
    value = peel_cast(value)
    m = re.match(r"load\b.*\s(%\w+)", value)
    if not m:
        return value
    slot = m.group(1)
    for line in body_lines:
        store = re.search(
            rf"store\b.*\s(%\w+)\s*,\s*{re.escape(slot)}\b", line
        )
        if store:
            return peel_cast(store.group(1))
    return value


def find_alloc_primitive_in_body(body_lines: list[str]) -> str | None:
    call_re = re.compile(r"\bcall\b.*@(" + "|".join(ALLOC_PRIMITIVES) + r")\b")
    for line in body_lines:
        m = call_re.search(line)
        if m:
            return m.group(1)
    return None


def find_free_primitive_with_param(body_lines: list[str], params: list[str]) -> tuple[str, str] | None:
    if not params:
        return None
    param_set = set(params)
    call_re = re.compile(
        r"\bcall\b.*@(" + "|".join(FREE_PRIMITIVES) + r")\((.*)\)"
    )
    for line in body_lines:
        m = call_re.search(line)
        if not m:
            continue
        primitive = m.group(1)
        args_blob = m.group(2)
        for arg in args_blob.split(","):
            traced = trace_local_slot(arg.strip(), body_lines)
            if traced in param_set:
                return primitive, traced
    return None


def audit_defined_functions(
    tu: str, functions: dict[str, FunctionIR], unresolved: Counter[str]
) -> list[CandidateResult]:
    results: list[CandidateResult] = []

    for name, func in sorted(functions.items()):
        alloc_prim = find_alloc_primitive_in_body(func.body_lines)
        alloc_matched = False
        alloc_reason = "no direct alloc primitive call in function body"
        if alloc_prim:
            for line in func.body_lines:
                if "ret " not in line:
                    continue
                ret_val = line.split("ret", 1)[1].strip().rstrip(",")
                if ret_val in ("void", ""):
                    continue
                traced = trace_local_slot(ret_val, func.body_lines)
                if re.search(rf"@({alloc_prim})\b", traced) or traced.startswith("%"):
                    call_src = None
                    for bl in func.body_lines:
                        if f"@{alloc_prim}" in bl and "call" in bl:
                            call_src = bl.strip()
                            break
                    if call_src and alloc_prim in call_src:
                        alloc_matched = True
                        alloc_reason = f"return traces to @{alloc_prim} (direct SSA / one alloca hop)"
                        break

        results.append(
            CandidateResult(
                function=name,
                tu=tu,
                rule="alloc_wrapper",
                matched=alloc_matched,
                primitive=alloc_prim or "",
                reason=alloc_reason,
                next_action=(
                    "enable wrapper propagation when TU coverage expands"
                    if alloc_matched
                    else "no action"
                ),
                call_sites_in_tu=unresolved.get(name, 0),
                in_tu_definition=True,
            )
        )

        free_match = find_free_primitive_with_param(func.body_lines, func.params)
        free_matched = free_match is not None
        if free_matched:
            prim, param = free_match
            free_reason = f"parameter {param} passed directly to @{prim}"
            free_action = "enable wrapper propagation when TU coverage expands"
        else:
            prim = ""
            if find_alloc_primitive_in_body(func.body_lines):
                free_reason = "function has alloc calls but no direct free primitive on formal param"
            else:
                free_reason = "no direct free primitive call with formal parameter in body"
            free_action = "no action"

        results.append(
            CandidateResult(
                function=name,
                tu=tu,
                rule="free_wrapper",
                matched=free_matched,
                primitive=prim,
                reason=free_reason,
                next_action=free_action,
                call_sites_in_tu=unresolved.get(name, 0),
                in_tu_definition=True,
            )
        )

    return results


def audit_external_unresolved(
    tu: str,
    unresolved: Counter[str],
    defined: dict[str, FunctionIR],
    declared: set[str],
) -> list[CandidateResult]:
    rows: list[CandidateResult] = []
    for name, count in unresolved.most_common(30):
        if name in defined:
            continue
        if not (name.startswith("io_") or "free" in name or "alloc" in name):
            continue
        if name.startswith("llvm."):
            continue

        reason = "external symbol"
        next_action = "no action for Phase 1.5"
        matched = False
        primitive = ""
        rule = "external_unresolved"

        if name in declared:
            reason = "declared in TU bitcode; body not present (cross-TU audit required)"
            next_action = "inspect defining TU separately; do not guess from name"

        if name == "io_free_req":
            reason = (
                "declared only in timeout.c bitcode; source body (io_uring.c) queues req "
                "to locked_free_list — no direct kfree/kmem_cache_free on formal param"
            )
            next_action = "reject for wrapper v0; not a direct-free wrapper"
            rule = "free_wrapper"
            matched = False

        rows.append(
            CandidateResult(
                function=name,
                tu=tu,
                rule=rule,
                matched=matched,
                primitive=primitive,
                reason=reason,
                next_action=next_action,
                call_sites_in_tu=count,
                in_tu_definition=False,
            )
        )
    return rows


def render_report(
    cases: list[tuple[str, Path, Path, list[CandidateResult]]],
) -> str:
    lines = [
        "# Kernel Wrapper Candidate Audit (Phase 1.5)",
        "",
        "Conservative rules (same as wrapper propagation v0):",
        "",
        "- **alloc wrapper:** function return directly from alloc primitive (`kmalloc`, `kmem_cache_alloc`, …), one alloca hop only",
        "- **free wrapper:** formal parameter directly passed to free primitive (`kfree`, `kmem_cache_free`, …), one alloca hop only",
        "",
        "**Scope:** audit only — no extractor changes.",
        "",
    ]

    any_matched = False
    for label, bc_path, facts_path, results in cases:
        matched = [r for r in results if r.matched]
        any_matched = any_matched or bool(matched)
        lines.extend(
            [
                f"## {label}",
                "",
                f"- bitcode: `{bc_path}`",
                f"- facts: `{facts_path}`",
                f"- defined functions scanned: {sum(1 for r in results if r.in_tu_definition and r.rule.endswith('wrapper')) // 2}",
                f"- matched in-TU wrapper candidates: **{len(matched)}**",
                "",
                "| function | rule | matched | primitive | call sites (unresolved) | reason | next action |",
                "|----------|------|---------|-----------|-------------------------|--------|-------------|",
            ]
        )
        for row in results:
            if row.rule == "external_unresolved" or not row.in_tu_definition:
                continue
            if not row.matched and row.rule.endswith("wrapper"):
                continue
            lines.append(
                f"| `{row.function}` | {row.rule} | "
                f"{'yes' if row.matched else 'no'} | "
                f"`{row.primitive}` | {row.call_sites_in_tu} | {row.reason} | {row.next_action} |"
            )

        externals = [r for r in results if not r.in_tu_definition]
        if externals:
            lines.extend(["", "### External / cross-TU unresolved (selected)", ""])
            for row in externals:
                lines.append(
                    f"- `{row.function}` ({row.call_sites_in_tu} unresolved call sites): "
                    f"{row.reason}. **Next:** {row.next_action}"
                )

        negatives = [
            r
            for r in results
            if r.in_tu_definition and not r.matched and r.rule.endswith("wrapper")
        ]
        if not matched:
            lines.extend(
                [
                    "",
                    f"**In-TU result:** no function in `{label}` bitcode matches alloc/free wrapper rules.",
                    "",
                ]
            )
        lines.append("")

    lines.extend(
        [
            "## Conclusion",
            "",
        ]
    )
    if any_matched:
        lines.append(
            "- At least one in-TU candidate matched; review table above before enabling kernel wrapper propagation."
        )
    else:
        lines.append(
            "- **No in-TU wrapper candidates** in `timeout.c` or `cancel.c` bitcode under v0 rules."
        )
        lines.append(
            "- `io_free_req` is the only alloc/free-adjacent unresolved callee in timeout TU; it is external and does not directly call `kfree` in source."
        )
        lines.append(
            "- **Recommendation:** defer kernel wrapper propagation; expand audit to other io_uring TUs only if direct primitive patterns appear."
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout-bc",
        type=Path,
        default=Path("/tmp/io_uring_timeout.bc"),
    )
    parser.add_argument(
        "--cancel-bc",
        type=Path,
        default=Path("/tmp/io_uring_cancel.bc"),
    )
    parser.add_argument(
        "--timeout-facts",
        type=Path,
        default=Path("/tmp/io_uring_timeout.facts.jsonl"),
    )
    parser.add_argument(
        "--cancel-facts",
        type=Path,
        default=Path("/tmp/io_uring_cancel.facts.jsonl"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "reports" / "kernel-wrapper-candidates.md",
    )
    args = parser.parse_args()

    cases: list[tuple[str, Path, Path, list[CandidateResult]]] = []
    for label, bc, facts in (
        ("io_uring/timeout.c", args.timeout_bc, args.timeout_facts),
        ("io_uring/cancel.c", args.cancel_bc, args.cancel_facts),
    ):
        if not bc.is_file():
            print(f"warning: missing bitcode: {bc}", file=sys.stderr)
            continue
        if not facts.is_file():
            print(f"warning: missing facts: {facts}", file=sys.stderr)
            continue

        ir_text = disassemble_bitcode(bc)
        defined, declared = parse_functions(ir_text)
        unresolved = load_unresolved_callees(facts)
        results = audit_defined_functions(label, defined, unresolved)
        results.extend(audit_external_unresolved(label, unresolved, defined, declared))
        cases.append((label, bc, facts, results))

    if not cases:
        raise SystemExit("no cases audited (missing bitcode/facts)")

    report = render_report(cases)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
