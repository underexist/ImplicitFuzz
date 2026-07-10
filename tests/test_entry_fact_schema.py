import json
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "extraction" / "schema" / "facts"


def _validator():
    resources = []
    for p in sorted(SCHEMA_DIR.glob("*.schema.json")):
        s = json.loads(p.read_text())
        resources.append((s.get("$id") or p.as_uri(), Resource.from_contents(s)))
    registry = Registry().with_resources(resources)
    common = json.loads((SCHEMA_DIR / "common.schema.json").read_text())
    entry = json.loads((SCHEMA_DIR / "entry_fact.schema.json").read_text())
    merged = {**common, **entry}
    merged["properties"] = {**common["properties"], **entry.get("properties", {})}
    merged["required"] = list(set(common["required"]) | set(entry.get("required", [])))
    merged.pop("allOf", None)
    return jsonschema.Draft202012Validator(merged, registry=registry)


def test_op_dispatch_entry_fact_validates():
    rec = {
        "fact_type": "entry_fact", "schema_version": "1.0.0",
        "kernel_version": "linux-6.1", "llvm_version": "21.1.8", "opt_level": "-O2 -g",
        "bc_unit": "opdef.bc", "function": "io_op_defs",
        "entry_kind": "op_dispatch", "entry_symbol": "io_read",
        "associated_syscall": "READV", "dispatch_table": "io_op_defs",
        "dispatch_index": 1, "dispatch_role": "issue",
        "primary_provenance": "dwarf", "provenance": ["dwarf"], "confidence": "high",
    }
    _validator().validate(rec)  # must not raise


def test_op_dispatch_entry_kind_currently_invalid_is_now_valid():
    # Guard: 'op_dispatch' must be an accepted entry_kind enum value.
    entry = json.loads((SCHEMA_DIR / "entry_fact.schema.json").read_text())
    assert "op_dispatch" in entry["properties"]["entry_kind"]["enum"]
