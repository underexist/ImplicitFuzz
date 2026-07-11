"""Term-level scoring of a cold-judge predicate against ground-truth, plus the
auditable per-gate record. Ground-truth is supplied separately by the caller
and MUST never be part of the input bundle."""

from __future__ import annotations


def _term_key(t: dict):
    return (t.get("class"), t.get("field_ref"), (t.get("align_target") or "").strip())


def score_terms(judge_terms: list[dict], truth_terms: list[dict]) -> dict:
    jk = {_term_key(t) for t in judge_terms}
    tk = {_term_key(t) for t in truth_terms}
    matched = jk & tk
    precision = len(matched) / len(jk) if jk else 0.0
    recall = len(matched) / len(tk) if tk else 0.0
    return {"precision": precision, "recall": recall, "matched": len(matched),
            "n_judge": len(jk), "n_truth": len(tk)}


def build_audit_record(gate_id, bundle, judge_output, validation, score,
                       human_score=None, rationale=""):
    return {
        "gate_id": gate_id,
        "bundle": bundle,
        "judge_output": judge_output,
        "validation": validation,
        "score": score,
        "human_score": human_score,
        "rationale": rationale,
    }
