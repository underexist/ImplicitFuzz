"""Shared field-key normalization for gate derivation and reverse-lookup.

A *field key* is the canonical identity of an object field, used both to
decide "is this field written somewhere (a state carrier)" and to JOIN a
gated field to its prior writes. Symbolic access paths map to `sym:<path>`.
Numeric-only accesses map to `num:<canonical_struct>@<offset>` -- but only
when a struct type is available; in v1 real facts carry no struct type for
numeric-only accesses (see the design spec's 决策修订 §0), so the numeric
branch returns None on real data and is exercised only by synthetic tests
until the follow-up extractor-symbolization upgrade lands.
"""

from __future__ import annotations

from dataclasses import dataclass

_CONFIDENCE_LADDER = ["low", "medium_low", "medium", "medium_high", "high"]
_NUMERIC_KINDS = {"gep_offsets", "byte_range"}


@dataclass(frozen=True)
class FieldKey:
    kind: str  # "symbolic" | "numeric"
    key: str
    confidence: str
    source_access_fact_id: int | None = None


def canonicalize_struct_type(raw: str | None) -> str | None:
    """Strip struct/const/volatile/pointer noise so one type has one spelling."""
    if not raw:
        return None
    tokens = raw.replace("*", " ").split()
    tokens = [
        t for t in tokens if t not in {"struct", "union", "enum", "const", "volatile"}
    ]
    if not tokens:
        return None
    return tokens[-1]


def lower_confidence(conf: str) -> str:
    """One notch down the confidence ladder, floored at 'low'."""
    try:
        idx = _CONFIDENCE_LADDER.index(conf)
    except ValueError:
        return "low"
    return _CONFIDENCE_LADDER[max(0, idx - 1)]


def field_key_for_access(
    *,
    access_path_symbolic: str | None,
    field_type: str | None = None,
    numeric_kind: str | None = None,
    access_path_numeric: str | None = None,
    confidence: str = "low",
    source_access_fact_id: int | None = None,
) -> FieldKey | None:
    if access_path_symbolic:
        return FieldKey(
            "symbolic", f"sym:{access_path_symbolic}", confidence, source_access_fact_id
        )
    struct = canonicalize_struct_type(field_type)
    if struct is None or numeric_kind not in _NUMERIC_KINDS or not access_path_numeric:
        return None  # v1 real numeric-only data lands here (no struct type)
    return FieldKey(
        "numeric",
        f"num:{struct}@{access_path_numeric}",
        lower_confidence(confidence),
        source_access_fact_id,
    )
