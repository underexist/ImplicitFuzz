#!/usr/bin/env python3
"""Validate primitive_summary.yaml and emit primitive_summary.json for the extractor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import jsonschema
    import yaml
except ImportError as exc:
    raise SystemExit(
        "missing dependency (need PyYAML and jsonschema): "
        f"{exc}"
    ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yaml",
        type=Path,
        default=Path("extraction/schema/primitive_summary.yaml"),
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path("extraction/schema/primitive_summary.schema.json"),
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("extraction/schema/primitive_summary.json"),
    )
    args = parser.parse_args()

    if not args.yaml.is_file():
        print(f"error: yaml not found: {args.yaml}", file=sys.stderr)
        return 1
    if not args.schema.is_file():
        print(f"error: schema not found: {args.schema}", file=sys.stderr)
        return 1

    entries = yaml.safe_load(args.yaml.read_text(encoding="utf-8"))
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(entries)

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"primitive_summary schema OK: {len(entries)} entries")
    print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
