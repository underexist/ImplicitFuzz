"""Three-gate validation of a cold-judge predicate (schema gate here; the
field-existence and synthesizability gates are added in a later task)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

PREDICATE_SCHEMA_PATH = Path(__file__).parent / "predicate_schema.json"


def validate_schema(predicate: dict) -> bool:
    schema = json.loads(PREDICATE_SCHEMA_PATH.read_text())
    try:
        jsonschema.validate(predicate, schema)
        return True
    except jsonschema.ValidationError:
        return False
