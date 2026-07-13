"""Confidence-feedback update rule. KCOV is an execution proxy, so a demotion is
'contradicted' (opposite-direction evidence), never 'falsified'; pos~=neg is
'undecidable' (no demotion). unsupported = no template (not executed)."""

from __future__ import annotations

TIER_ORDER = ["rejected", "low", "medium", "high", "execution_verified"]


def classify_outcome(signal_pos, signal_neg, margin: int = 2) -> str:
    if signal_pos is None or signal_neg is None:
        return "undecidable"
    if signal_pos - signal_neg >= margin:
        return "verified"
    if signal_neg - signal_pos >= margin:
        return "contradicted"
    return "undecidable"


def update_confidence(static_tier: str, outcome: str) -> tuple[str, str]:
    if outcome == "verified":
        return "execution_verified", "execution verified: claimed-satisfy covers deeper than claimed-violate"
    if outcome == "contradicted":
        return "rejected", "contradicted: opposite-direction execution evidence"
    if outcome == "unsupported":
        return static_tier, "unsupported: no assembler template for gate_family"
    return static_tier, "undecidable: signals inseparable (pos~=neg) or run failure"


def rerank(view: list[dict]) -> list[dict]:
    return sorted(view, key=lambda r: TIER_ORDER.index(r["final_confidence"]), reverse=True)
