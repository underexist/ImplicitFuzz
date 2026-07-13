"""Summarize a confidence-feedback eval run into two-layer metrics, the
feedback-state distribution over the real candidate set, and the rerank
before/after. Two layers are always reported together: all real candidates
(honest coverage) AND strictly-supported-and-executed (frozen replay, NOT
held-out generalization). rerank shows execution evidence becoming the new sort
key; it does NOT claim overall ranking-quality improvement (unsupported
candidates have no ground truth)."""

from __future__ import annotations
from implicitfuzz.feedback.update import TIER_ORDER


def summarize(snapshot: dict, calibrated_view: list[dict]) -> dict:
    cands = snapshot["candidates"]
    total = len(cands)
    executable_ids = {c["candidate_id"] for c in cands if c["executable"]}

    reasons = {}
    for c in cands:
        if not c["executable"]:
            reasons[c["unsupported_reason"]] = reasons.get(c["unsupported_reason"], 0) + 1

    dist = {"verified": 0, "contradicted": 0, "undecidable": 0, "unsupported": 0}
    for r in calibrated_view:
        dist[r["execution_status"]] += 1

    ex_view = [r for r in calibrated_view if r["candidate_id"] in executable_ids]
    layer2 = {
        "verified": sum(1 for r in ex_view if r["execution_status"] == "verified"),
        "total": len(ex_view),
        "note": "frozen replay (both participated in template development); NOT held-out generalization",
    }
    before = [c["candidate_id"] for c in sorted(
        cands, key=lambda c: (-TIER_ORDER.index(c["static_confidence"]), c["candidate_id"]))]
    return {
        "coverage": {"executable": len(executable_ids), "total": total,
                     "unsupported_reasons": reasons},
        "distribution_real": dist,
        "layer1_all_real": {"verified": dist["verified"],
                            "unsupported": dist["unsupported"], "total": total},
        "layer2_executed": layer2,
        "rerank": {"before": before,
                   "after": [r["candidate_id"] for r in calibrated_view]},
    }
