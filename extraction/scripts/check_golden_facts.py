#!/usr/bin/env python3
"""Tiny golden checker for implicitfuzz-extract JSONL output."""

from __future__ import annotations

import argparse
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


def check_facts(facts: list[dict]) -> list[str]:
    errors: list[str] = []

    call_facts = [f for f in facts if f.get("fact_type") == "call_fact"]
    access_facts = [f for f in facts if f.get("fact_type") == "access_fact"]

    if len(call_facts) < 7:
        errors.append(f"expected call_fact >= 7, got {len(call_facts)}")
    if len(access_facts) < 36:
        errors.append(f"expected access_fact >= 36, got {len(access_facts)}")

    semantic_ops = {f.get("semantic_op") for f in access_facts}
    for required in ("alloc", "free", "read", "write"):
        if required not in semantic_ops:
            errors.append(f"missing semantic_op={required}")

    numeric_kinds = set()
    gep_index_sets: list[list] = []
    numeric_reprs: list = []
    for fact in access_facts:
        access_path = fact.get("access_path") or {}
        numeric_kinds.add(access_path.get("numeric_kind") or fact.get("numeric_kind"))
        indices = access_path.get("indices")
        if isinstance(indices, list):
            gep_index_sets.append(indices)
        numeric_repr = fact.get("access_path_numeric")
        if isinstance(numeric_repr, str):
            try:
                numeric_reprs.append(json.loads(numeric_repr))
            except json.JSONDecodeError:
                pass
        elif access_path.get("numeric") is not None:
            numeric_reprs.append(access_path.get("numeric"))

    for required in ("gep_offsets", "whole_object"):
        if required not in numeric_kinds:
            errors.append(f"missing numeric_kind={required}")

    has_main_alloc = any(
        f.get("function") == "main"
        and f.get("callee") == "alloc_node"
        for f in call_facts
    )
    if not has_main_alloc:
        errors.append("missing call_fact main -> alloc_node")

    if not any(indices == [0, 1] for indices in gep_index_sets + numeric_reprs):
        errors.append("missing GEP indices [0,1]")

    def symbolic_path_of(fact: dict) -> str | None:
        if fact.get("access_path_symbolic") is not None:
            return fact.get("access_path_symbolic")
        access_path = fact.get("access_path") or {}
        return access_path.get("symbolic_path")

    def numeric_of(fact: dict) -> list | None:
        numeric_repr = fact.get("access_path_numeric")
        if isinstance(numeric_repr, str):
            try:
                return json.loads(numeric_repr)
            except json.JSONDecodeError:
                return None
        access_path = fact.get("access_path") or {}
        numeric = access_path.get("numeric")
        return numeric if isinstance(numeric, list) else None

    has_node_next = False
    for fact in access_facts:
        if symbolic_path_of(fact) == "Node.next" and numeric_of(fact) == [0, 1]:
            has_node_next = True
            if fact.get("field_type") not in ("struct Node *", "Node *"):
                errors.append('Node.next field_type expected "struct Node *"')
            if fact.get("primary_provenance") != "dwarf":
                errors.append('Node.next primary_provenance expected "dwarf"')
            if fact.get("access_path_recovery") != "dwarf":
                errors.append('Node.next access_path_recovery expected "dwarf"')
            if fact.get("confidence") != "high":
                errors.append('Node.next confidence expected "high"')
            break
    if not has_node_next:
        errors.append('missing symbolic_path "Node.next" with numeric [0,1]')

    has_node_value = False
    for fact in access_facts:
        if symbolic_path_of(fact) == "Node.value" and numeric_of(fact) == [0, 0]:
            has_node_value = True
            if fact.get("field_type") != "int":
                errors.append('Node.value field_type expected "int"')
            if fact.get("primary_provenance") != "dwarf":
                errors.append('Node.value primary_provenance expected "dwarf"')
            if fact.get("access_path_recovery") != "dwarf":
                errors.append('Node.value access_path_recovery expected "dwarf"')
            if fact.get("confidence") != "high":
                errors.append('Node.value confidence expected "high"')
            break
    if not has_node_value:
        errors.append('missing symbolic_path "Node.value" with numeric [0,0]')

    has_node_name = False
    for fact in access_facts:
        if symbolic_path_of(fact) == "Node.name" and numeric_of(fact) == [0, 2]:
            has_node_name = True
            if fact.get("field_type") != "const char *":
                errors.append('Node.name field_type expected "const char *"')
            break
    if not has_node_name:
        errors.append('missing symbolic_path "Node.name" with numeric [0,2]')

    has_node_flags = False
    for fact in access_facts:
        if symbolic_path_of(fact) == "Node.flags" and numeric_of(fact) == [0, 3]:
            has_node_flags = True
            if fact.get("field_type") != "unsigned long":
                errors.append('Node.flags field_type expected "unsigned long"')
            break
    if not has_node_flags:
        errors.append('missing symbolic_path "Node.flags" with numeric [0,3]')

    for fact in facts:
        if "svf_node_id" not in fact:
            errors.append("fact missing svf_node_id field")
            break
        if "icfg_node_id" not in fact:
            errors.append("fact missing icfg_node_id field")
            break

    if not any(
        f.get("function") == "alloc_node"
        and f.get("semantic_op") == "alloc"
        and f.get("access_kind") == "call_alloc"
        and f.get("primary_provenance") == "summary"
        for f in access_facts
    ):
        errors.append("missing primitive summary hit: alloc_node malloc call_alloc")

    if not any(
        f.get("function") == "free_node"
        and f.get("semantic_op") == "free"
        and f.get("access_kind") == "call_free"
        and f.get("primary_provenance") == "summary"
        for f in access_facts
    ):
        errors.append("missing primitive summary hit: free_node free call_free")

    def is_wrapper_propagation(fact: dict) -> bool:
        detail = fact.get("summary_detail") or {}
        return detail.get("match_kind") == "wrapper_propagation"

    wrapper_alloc_main = [
        f
        for f in access_facts
        if f.get("function") == "main"
        and f.get("semantic_op") == "alloc"
        and f.get("access_kind") == "call_alloc"
        and f.get("primary_provenance") == "summary"
        and is_wrapper_propagation(f)
        and (f.get("summary_detail") or {}).get("wrapper_source") == "alloc_node"
    ]
    if len(wrapper_alloc_main) < 2:
        errors.append(
            "missing wrapper propagation: main -> alloc_node call_alloc "
            f"(expected >=2, got {len(wrapper_alloc_main)})"
        )

    wrapper_free_main = [
        f
        for f in access_facts
        if f.get("function") == "main"
        and f.get("semantic_op") == "free"
        and f.get("access_kind") == "call_free"
        and f.get("primary_provenance") == "summary"
        and is_wrapper_propagation(f)
        and (f.get("summary_detail") or {}).get("wrapper_source") == "free_node"
    ]
    if len(wrapper_free_main) < 2:
        errors.append(
            "missing wrapper propagation: main -> free_node call_free "
            f"(expected >=2, got {len(wrapper_free_main)})"
        )

    if not any(
        f.get("semantic_op") == "alloc" and f.get("access_kind") == "call_alloc"
        for f in access_facts
    ):
        errors.append("missing call_alloc fact from primitive summary")
    if not any(
        f.get("semantic_op") == "free" and f.get("access_kind") == "call_free"
        for f in access_facts
    ):
        errors.append("missing call_free fact from primitive summary")

    # base_object (Phase 2C tier-1 direct resolution): formal parameter.
    peek_value_fact = next(
        (
            f
            for f in access_facts
            if f.get("function") == "peek_value" and symbolic_path_of(f) == "Node.value"
        ),
        None,
    )
    if peek_value_fact is None:
        errors.append('missing symbolic_path "Node.value" in function peek_value')
    else:
        base_object = peek_value_fact.get("base_object") or {}
        if base_object.get("object_scope") != "formal_param":
            errors.append(
                "peek_value Node.value base_object.object_scope expected "
                f"'formal_param', got {base_object.get('object_scope')!r}"
            )
        if base_object.get("value") != "peek_value:0":
            errors.append(
                "peek_value Node.value base_object.value expected "
                f"'peek_value:0', got {base_object.get('value')!r}"
            )

    # base_object: heap allocation site, consistent across all field accesses
    # on the same local variable within a function.
    def assert_consistent_allocation_site(function: str) -> None:
        scopes_and_values = {
            (
                (fact.get("base_object") or {}).get("object_scope"),
                (fact.get("base_object") or {}).get("value"),
            )
            for fact in access_facts
            if fact.get("function") == function
            and (symbolic_path_of(fact) or "").startswith("Node.")
        }
        if not scopes_and_values:
            errors.append(f"no Node.* access_fact found in function {function}")
            return
        if len(scopes_and_values) != 1:
            errors.append(
                f"{function}: expected one consistent base_object across Node.* "
                f"accesses, got {scopes_and_values}"
            )
            return
        (scope, value) = next(iter(scopes_and_values))
        if scope != "allocation_site":
            errors.append(
                f"{function}: Node.* base_object.object_scope expected "
                f"'allocation_site', got {scope!r}"
            )
        if not value or not value.startswith(f"{function}#addr"):
            errors.append(
                f"{function}: Node.* base_object.value expected to start with "
                f"'{function}#addr', got {value!r}"
            )

    assert_consistent_allocation_site("alloc_node")
    assert_consistent_allocation_site("main")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path, help="JSONL facts file to validate")
    args = parser.parse_args()

    if not args.jsonl.is_file():
        print(f"error: file not found: {args.jsonl}", file=sys.stderr)
        return 1

    facts = load_facts(args.jsonl)
    errors = check_facts(facts)
    if errors:
        print(f"golden check FAILED ({args.jsonl})", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    call_n = sum(1 for f in facts if f.get("fact_type") == "call_fact")
    access_n = sum(1 for f in facts if f.get("fact_type") == "access_fact")
    print(
        f"golden check OK: {len(facts)} facts "
        f"({call_n} call_fact, {access_n} access_fact)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
