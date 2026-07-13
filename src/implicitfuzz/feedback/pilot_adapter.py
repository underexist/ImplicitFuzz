"""Adapt a pilot cold-judge gate (gate def + runs_formal predicate) into a
confidence-feedback candidate. Strict-faithful executability: executable only
if the full signature (operation, target_function, field_relation,
template_family) matches EXECUTABLE_GATES -- a function name alone is
insufficient (io_prep_rw serves READ_FIXED and WRITE_FIXED). Static tier = min
confidence over activation-class terms (conjunctive predicate: no stronger than
its weakest premise); a gate with no activation term is an AdapterError, never a
silent default."""

from __future__ import annotations
import re

ADAPTER_VERSION = "1.0"
PREDICATE_SCHEMA_VERSION = "1.0"  # pilot predicate_schema.json carries no version field

# Full-signature template registry. Key = (operation, target_function,
# field_relation, template_family); value = execution metadata.
EXECUTABLE_GATES = {
    ("READ", "io_file_get_fixed", "fd < nr_user_files", "fixed_file"):
        {"signal_function": "io_read"},
    ("READ_FIXED", "io_prep_rw", "buf_index < nr_user_bufs", "fixed_buffer"):
        {"signal_function": "io_prep_rw"},
}

# Op(s) a target_function is driven by when the predicate carries no explicit
# `opcode == ...` activation term (op-agnostic fixed-resource resolver).
TARGET_FN_DEFAULT_OPS = {"io_file_get_fixed": {"READ"}}

_CONF_MAP = {"high": "high", "medium_high": "medium", "medium": "medium",
             "medium_low": "low", "low": "low"}
_RANK = {"high": 0, "medium": 1, "low": 2}
_BOUND_RE = re.compile(r"([a-z_]+)\s*<\s*(?:ctx->)?(nr_user_files|nr_user_bufs)")


class AdapterError(ValueError):
    pass


def _canon_relation(rel: str):
    m = _BOUND_RE.search(rel)
    return "%s < %s" % (m.group(1), m.group(2)) if m else None


def _family_from_clues(clues):
    joined = " ".join(clues)
    if "nr_user_files" in joined:
        return "fixed_file"
    if "nr_user_bufs" in joined:
        return "fixed_buffer"
    return None


def _activation_bound(terms):
    for t in terms:
        if t["class"] == "activation":
            c = _canon_relation(t.get("relation", ""))
            if c:
                return c
    return None


def _opcode_ops(terms):
    for t in terms:
        if t["class"] == "activation" and "opcode" in t.get("field_ref", "") \
                and "opcode ==" in t.get("relation", ""):
            ops = set(re.findall(r"[A-Z_]*READ[A-Z_]*|[A-Z_]*WRITE[A-Z_]*", t["relation"]))
            return {o for o in ops if o} or None
    return None


def _static_tier(terms):
    acts = [t for t in terms if t["class"] == "activation"]
    if not acts:
        raise AdapterError("gate has no activation term; cannot derive static tier")
    mapped = [_CONF_MAP.get(t.get("confidence", ""), "low") for t in acts]
    return max(mapped, key=lambda c: _RANK[c])  # worst = min confidence


def adapt_gate(gate_id: str, gate_def: dict, run: dict) -> dict:
    terms = run.get("terms", [])
    target_fn = run.get("target_gate", {}).get("function", "")
    static = _static_tier(terms)  # raises AdapterError if no activation term
    family = _family_from_clues(gate_def.get("field_clues", []))
    bound = _activation_bound(terms)
    base = {
        "candidate_id": gate_id, "candidate_origin": "replayed",
        "static_confidence": static, "param_align": {"relation": "<"},
        "target_function": target_fn, "field_relation_family": family,
        "field_relation": bound,
    }
    if family is None:
        base.update(gate_family=None, operation=None, signal_function=None,
                    executable=False, unsupported_reason="no_family_template")
        return base
    ops = _opcode_ops(terms) or TARGET_FN_DEFAULT_OPS.get(target_fn) or set()
    match = None
    if bound:
        for op in ops:
            key = (op, target_fn, bound, family)
            if key in EXECUTABLE_GATES:
                match = (op, EXECUTABLE_GATES[key])
                break
    if match:
        op, meta = match
        base.update(gate_family=family, operation=op,
                    signal_function=meta["signal_function"],
                    executable=True, unsupported_reason=None)
    else:
        base.update(gate_family=None, operation=None, signal_function=None,
                    executable=False, unsupported_reason="op_not_covered")
    return base
