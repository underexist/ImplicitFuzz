"""Confidence-feedback loop orchestrator. For each candidate: assemble pos/neg
progs, execute (injected executor), classify the outcome, update the discrete
tier, and build a calibrated view WITHOUT mutating the original candidate."""

from __future__ import annotations

from implicitfuzz.feedback.assemble import assemble_progs
from implicitfuzz.feedback.update import classify_outcome, update_confidence, rerank


def run_loop(candidates: list[dict], executor, kernel_build_identity: str) -> list[dict]:
    view = []
    for c in candidates:
        asm = assemble_progs(c)
        if asm is None:
            outcome, sp, sn, tf, tv = "unsupported", None, None, None, None
        else:
            sp, sn = executor(asm["pos"], asm["neg"], c.get("signal_function"))
            outcome = classify_outcome(sp, sn)
            tf, tv = asm["template_family"], asm["template_version"]
        final, reason = update_confidence(c["static_confidence"], outcome)
        view.append({
            "candidate_id": c["candidate_id"],
            "candidate_origin": c.get("candidate_origin", "replayed"),
            "static_confidence": c["static_confidence"],
            "execution_status": outcome,
            "final_confidence": final,
            "template_family": tf,
            "template_version": tv,
            "kernel_build_identity": kernel_build_identity,
            "signal_function": c.get("signal_function"),
            "pos_coverage": sp,
            "neg_coverage": sn,
            "reason": reason,
        })
    return rerank(view)
