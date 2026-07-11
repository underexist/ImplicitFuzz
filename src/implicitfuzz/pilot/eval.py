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


def score_terms_relaxed(judge_terms: list[dict], truth_terms: list[dict]) -> dict:
    """Relaxed lower-bound proxy scores (the pilot found exact-key too strict).

    class_field: match on (class, field_ref), ignoring align_target free text.
    field_set:   match on field_ref alone, ignoring class and attribution.
    Human semantic scoring remains the primary metric; these are proxies.
    """
    def _pr(jset, tset):
        matched = jset & tset
        precision = len(matched) / len(jset) if jset else 0.0
        recall = len(matched) / len(tset) if tset else 0.0
        return {"precision": precision, "recall": recall, "matched": len(matched)}

    jcf = {(t.get("class"), t.get("field_ref")) for t in judge_terms}
    tcf = {(t.get("class"), t.get("field_ref")) for t in truth_terms}
    jf = {t.get("field_ref") for t in judge_terms}
    tf = {t.get("field_ref") for t in truth_terms}
    return {"class_field": _pr(jcf, tcf), "field_set": _pr(jf, tf)}


def human_score_template(judge_terms: list[dict]) -> dict:
    return {
        "recovered_required_terms": None,
        "param_align_correct": "n/a",
        "has_wrong_term": None,
        "per_term_verdict": [
            {"field_ref": t.get("field_ref"), "class": t.get("class"), "verdict": None}
            for t in judge_terms
        ],
        "notes": "",
    }


def sandbox_audit(tool_uses: int) -> dict:
    return {"clean": tool_uses == 0, "tool_uses": tool_uses}
