#!/usr/bin/env python3
"""Validate ImplicitFuzz JSONL facts against extraction/schema/facts/*.schema.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import jsonschema
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

FACT_SCHEMAS = {
    "call_fact": "call_fact.schema.json",
    "access_fact": "access_fact.schema.json",
}


def build_registry(schema_dir: Path) -> Registry:
    resources: list[tuple[str, dict]] = []
    for path in sorted(schema_dir.glob("*.schema.json")):
        with path.open(encoding="utf-8") as handle:
            schema = json.load(handle)
        uri = schema.get("$id") or path.as_uri()
        resources.append((uri, schema))
    return Registry().with_resources(
        (uri, Resource.from_contents(content)) for uri, content in resources
    )


def load_validators(schema_dir: Path) -> dict[str, Draft202012Validator]:
    registry = build_registry(schema_dir)
    validators: dict[str, Draft202012Validator] = {}
    for fact_type, filename in FACT_SCHEMAS.items():
        with (schema_dir / filename).open(encoding="utf-8") as handle:
            schema = json.load(handle)
        validators[fact_type] = Draft202012Validator(schema, registry=registry)
    return validators


def validate_file(jsonl_path: Path, schema_dir: Path) -> tuple[list[str], int]:
    if not jsonl_path.is_file():
        return [f"file not found: {jsonl_path}"], 0
    if not schema_dir.is_dir():
        return [f"schema dir not found: {schema_dir}"], 0

    validators = load_validators(schema_dir)
    errors: list[str] = []
    count = 0

    with jsonl_path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            count += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{jsonl_path}:{line_no}: invalid JSON: {exc}")
                continue

            fact_type = record.get("fact_type")
            validator = validators.get(fact_type)
            if validator is None:
                errors.append(
                    f"{jsonl_path}:{line_no}: unsupported fact_type {fact_type!r}"
                )
                continue

            for error in sorted(validator.iter_errors(record), key=str):
                path = ".".join(str(part) for part in error.absolute_path) or "<root>"
                errors.append(f"{jsonl_path}:{line_no}: {path}: {error.message}")

    return errors, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path, help="JSONL facts file to validate")
    parser.add_argument(
        "schema_dir",
        type=Path,
        nargs="?",
        default=Path(__file__).resolve().parent.parent / "schema" / "facts",
        help="Directory containing fact JSON schemas",
    )
    args = parser.parse_args()

    errors, count = validate_file(args.jsonl, args.schema_dir)
    if errors:
        print(f"schema validation FAILED ({args.jsonl})", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"schema validation OK: {count} facts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
