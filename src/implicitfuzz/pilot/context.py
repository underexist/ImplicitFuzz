"""Build the cold-judge input bundle for a gate: code slice + ledger subset +
gate id + field clues + task instructions + predicate schema. RED LINE: the
bundle must never contain ground-truth, reconciliation results, or any answer.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from implicitfuzz.pilot.validate import PREDICATE_SCHEMA_PATH

_INSTRUCTIONS = (
    "You are a constrained judge. Using ONLY the code slice and the access-fact "
    "ledger below, infer the state-gate predicate for reaching the target branch. "
    "Output JSON conforming to predicate_schema. Classify each term as premise / "
    "activation / param_align; tag its object; put the referenced field in "
    "field_ref as '<struct>.<member>'. Do NOT reference fields absent from the "
    "slice/ledger. You may abstain or give alt_candidates if unsure."
)


def read_source_window(source_root: str, file_line: str, radius: int = 15) -> str:
    path_str, _, line_str = file_line.rpartition(":")
    line = int(line_str)
    src = Path(source_root) / path_str
    lines = src.read_text(errors="replace").splitlines()
    lo = max(0, line - 1 - radius)
    hi = min(len(lines), line + radius)
    return "\n".join(f"{i + 1}: {lines[i]}" for i in range(lo, hi))


def build_bundle(conn: sqlite3.Connection, source_root: str, gate: dict,
                 radius: int = 15) -> dict:
    conn.row_factory = sqlite3.Row
    code_slices = [
        {"label": loc.get("label", ""), "file_line": loc["file_line"],
         "text": read_source_window(source_root, loc["file_line"], radius)}
        for loc in gate.get("slice_locations", [])
    ]
    ledger = []
    for fn in gate.get("ledger_functions", []):
        for row in conn.execute(
            "SELECT function, semantic_op, access_path_symbolic, numeric_kind, "
            "access_path_numeric FROM access_fact WHERE function = ? ORDER BY id",
            (fn,),
        ).fetchall():
            ledger.append(dict(row))
    return {
        "target_gate": gate["target_gate"],
        "field_clues": gate.get("field_clues", []),
        "code_slices": code_slices,
        "ledger": ledger,
        "instructions": _INSTRUCTIONS,
        "predicate_schema": json.loads(PREDICATE_SCHEMA_PATH.read_text()),
    }
