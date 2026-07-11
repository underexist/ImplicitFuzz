"""JSONL fact ingestion into SQLite, validated against extraction/schema/facts/*.schema.json."""
import json
import sqlite3
from pathlib import Path

import jsonschema

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _REPO_ROOT / "extraction" / "schema" / "facts"
_DDL_PATH = Path(__file__).resolve().parent / "schema.sql"

_JSON_COLUMNS = {
    "entry_fact": {"source_location": "source_location_json", "provenance": "provenance_json"},
    "call_fact": {"source_location": "source_location_json", "provenance": "provenance_json", "callee_candidates": "callee_candidates_json"},
    "access_fact": {
        "source_location": "source_location_json",
        "provenance": "provenance_json",
        "base_object": "base_object_json",
        "access_path": "access_path_json",
        "summary_detail": "summary_detail_json",
    },
    "alias_fact": {"source_location": "source_location_json", "provenance": "provenance_json", "object_id": "object_id_json", "points_to_set": "points_to_set_json"},
    "branch_fact": {"source_location": "source_location_json", "provenance": "provenance_json", "control_deps": "control_deps_json", "related_loads": "related_loads_json"},
    "gate_seed_fact": {"source_location": "source_location_json", "provenance": "provenance_json", "related_objects": "related_objects_json", "related_access_facts": "related_access_facts_json"},
    "struct_layout_fact": {"source_location": "source_location_json", "provenance": "provenance_json"},
}

_SCALAR_RENAME = {"schema_version", "kernel_version", "llvm_version", "opt_level", "bc_unit", "function", "primary_provenance", "confidence"}


def _load_schemas() -> dict[str, dict]:
    common = json.loads((_SCHEMA_DIR / "common.schema.json").read_text())
    schemas = {}
    for fact_type in _JSON_COLUMNS:
        raw = json.loads((_SCHEMA_DIR / f"{fact_type}.schema.json").read_text())
        merged = {**common, **raw}
        merged["properties"] = {**common["properties"], **raw.get("properties", {})}
        merged["required"] = list(set(common["required"]) | set(raw.get("required", [])))
        del merged["allOf"]
        schemas[fact_type] = merged
    return schemas


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _row_for(fact_type: str, record: dict, table_columns: set[str]) -> dict:
    json_cols = _JSON_COLUMNS[fact_type]
    row = {"raw_json": json.dumps(record, sort_keys=True)}
    for key, value in record.items():
        if key == "fact_type":
            continue
        if key in json_cols:
            row[json_cols[key]] = json.dumps(value)
        elif key == "is_indirect":
            row[key] = int(bool(value))
        else:
            row[key] = value
    return {key: value for key, value in row.items() if key in table_columns}


def ingest_jsonl(jsonl_path: str, db_path: str) -> dict:
    schemas = _load_schemas()
    conn = sqlite3.connect(db_path)
    conn.executescript(_DDL_PATH.read_text())
    table_columns = {fact_type: _table_columns(conn, fact_type) for fact_type in schemas}

    counts: dict[str, int] = {}
    try:
        with open(jsonl_path) as fh:
            for line_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                fact_type = record.get("fact_type")
                if fact_type not in schemas:
                    raise ValueError(f"line {line_no}: unknown fact_type {fact_type!r}")
                jsonschema.validate(record, schemas[fact_type])

                row = _row_for(fact_type, record, table_columns[fact_type])
                columns = ", ".join(row.keys())
                placeholders = ", ".join("?" for _ in row)
                conn.execute(
                    f"INSERT INTO {fact_type} ({columns}) VALUES ({placeholders})",
                    list(row.values()),
                )
                counts[fact_type] = counts.get(fact_type, 0) + 1

        conn.commit()
    finally:
        conn.close()
    return counts
