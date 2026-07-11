"""Three-gate validation of a cold-judge predicate (schema gate here; the
field-existence and synthesizability gates are added in a later task)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import jsonschema

from implicitfuzz.reconcile.layout import LayoutIndex
from implicitfuzz.reconcile.field_existence import reconcile_field

PREDICATE_SCHEMA_PATH = Path(__file__).parent / "predicate_schema.json"

_SETTABLE_PARAMS = ("sqe->fd", "sqe->buf_index", "buf_index", "->fd")


def validate_schema(predicate: dict) -> bool:
    schema = json.loads(PREDICATE_SCHEMA_PATH.read_text())
    try:
        jsonschema.validate(predicate, schema)
        return True
    except jsonschema.ValidationError:
        return False


def _split_field_ref(field_ref: str):
    if "." not in field_ref:
        return None
    struct, member = field_ref.rsplit(".", 1)
    if not struct or not member:
        return None
    return struct, member


def validate_field_existence(conn: sqlite3.Connection, predicate: dict) -> dict:
    layout = LayoutIndex(conn)
    per_term = []
    for t in predicate.get("terms", []):
        parts = _split_field_ref(t.get("field_ref", ""))
        if parts is None:
            per_term.append({"field_ref": t.get("field_ref"), "status": "unconfirmed",
                             "access_fact_id": None})
            continue
        m = reconcile_field(conn, layout, parts[0], parts[1])
        per_term.append({"field_ref": t["field_ref"], "status": m.status,
                         "access_fact_id": m.access_fact_id})
    if per_term:
        passed = all(r["status"] != "unconfirmed" for r in per_term)
    else:
        passed = bool(predicate.get("abstain", False))
    return {"passed": passed, "per_term": per_term}


def validate_synthesizability(predicate: dict) -> list[dict]:
    out = []
    for t in predicate.get("terms", []):
        if t.get("class") in ("activation", "param_align"):
            hay = (t.get("relation", "") + " " + t.get("align_target", "")).lower()
            ok = any(p in hay for p in _SETTABLE_PARAMS)
        else:
            ok = True  # premise: satisfied by inserting the prior call
        out.append({"class": t.get("class"), "synthesizable": ok})
    return out
