"""Aggregate multi-model cold-judge runs: per-model precision/recall vs ground
truth, guard-fire consistency on negative controls, inter-model term agreement,
and abstention/compliance rates. Aggregation is pure (testable offline);
score_run's field-existence guard is the only part needing the DB (conn=None
skips it)."""

from __future__ import annotations
from implicitfuzz.pilot.validate import (validate_schema, validate_field_existence,
                                         validate_synthesizability)
from implicitfuzz.pilot.eval import score_terms, score_terms_relaxed, _term_key


def score_run(judge_output: dict, truth: dict, conn=None) -> dict:
    terms, tterms = judge_output.get("terms", []), truth.get("terms", [])
    fe = validate_field_existence(conn, judge_output) if conn is not None else None
    return {
        "schema": validate_schema(judge_output),
        "field_existence": fe,
        "synthesizability": validate_synthesizability(judge_output),
        "exact": score_terms(terms, tterms),
        "relaxed": score_terms_relaxed(terms, tterms),
        "abstain": bool(judge_output.get("abstain", False)),
        "parse_ok": bool(judge_output.get("_parse_ok", True)),
        "n_terms": len(terms),
    }


def aggregate_models(scored_by_model: dict) -> dict:
    out = {}
    for model, gates in scored_by_model.items():
        n = len(gates) or 1
        def _mean(path):
            tot = 0.0
            for s in gates.values():
                d = s
                for k in path:
                    d = d[k]
                tot += d
            return tot / n
        out[model] = {
            "n_gates": len(gates),
            "exact_precision": _mean(["exact", "precision"]),
            "exact_recall": _mean(["exact", "recall"]),
            "relaxed_field_precision": _mean(["relaxed", "field_set", "precision"]),
            "relaxed_field_recall": _mean(["relaxed", "field_set", "recall"]),
            "abstain_rate": sum(1 for s in gates.values() if s["abstain"]) / n,
            "parse_ok_rate": sum(1 for s in gates.values() if s["parse_ok"]) / n,
            "schema_ok_rate": sum(1 for s in gates.values() if s["schema"]) / n,
        }
    return out


def guard_consistency(scored_by_model: dict, negative_gate_ids) -> dict:
    out = {}
    for model, gates in scored_by_model.items():
        fired = {}
        for gid in negative_gate_ids:
            s = gates.get(gid)
            if s is None:
                fired[gid] = None
                continue
            fe = s.get("field_existence")
            fired[gid] = (not s["schema"]) or (fe is not None and not fe["passed"])
        out[model] = fired
    return out


def _jaccard(sets):
    sets = [s for s in sets if s]
    if len(sets) < 2:
        return None
    union = set.union(*sets)
    return len(set.intersection(*sets)) / len(union) if union else None


def inter_model_agreement(runs_by_model: dict) -> dict:
    models = list(runs_by_model)
    gate_ids = set().union(*[set(g) for g in runs_by_model.values()]) if models else set()
    per_gate = {}
    for gid in gate_ids:
        exact_sets, field_sets = [], []
        for m in models:
            terms = runs_by_model[m].get(gid, {}).get("terms", [])
            exact_sets.append({_term_key(t) for t in terms})
            field_sets.append({t.get("field_ref") for t in terms})
        per_gate[gid] = {"exact_jaccard": _jaccard(exact_sets),
                         "field_jaccard": _jaccard(field_sets)}
    return per_gate
