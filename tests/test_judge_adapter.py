from implicitfuzz.pilot.judge_adapter import bundle_to_prompt, parse_predicate, run_judge

BUNDLE = {
    "target_gate": {"function": "io_prep_rw"}, "field_clues": ["buf_index"],
    "code_slices": [{"label": "gate", "file_line": "rw.c:91", "text": "91: if (x)"}],
    "ledger": [{"function": "io_prep_rw", "semantic_op": "read"}],
    "instructions": "You are a constrained judge. Output JSON.",
    "predicate_schema": {"type": "object"},
}


def test_bundle_to_prompt_is_deterministic_and_complete():
    p = bundle_to_prompt(BUNDLE)
    assert "constrained judge" in p and "io_prep_rw" in p and "rw.c:91" in p
    assert "LEDGER" in p and "PREDICATE_SCHEMA" in p
    assert bundle_to_prompt(BUNDLE) == p  # deterministic


def test_parse_predicate_fenced():
    d = parse_predicate('prose\n```json\n{"terms": [{"class": "premise"}], "abstain": false}\n```\nmore')
    assert d["_parse_ok"] and len(d["terms"]) == 1


def test_parse_predicate_bare_and_abstain():
    d = parse_predicate('{"terms": [], "abstain": true}')
    assert d["_parse_ok"] and d["abstain"] is True


def test_parse_predicate_garbage():
    d = parse_predicate("no json here")
    assert d["_parse_ok"] is False and d["terms"] == []


def test_run_judge_uses_injected_poster_no_network():
    seen = {}
    def stub(url, headers, body):
        seen["url"] = url; seen["model"] = body["model"]; seen["max_tokens"] = body["max_tokens"]
        return {"model": "deepseek-v4-flash",
                "choices": [{"message": {"content": '{"terms": [{"class": "activation"}]}'}}],
                "usage": {"total_tokens": 42}}
    out = run_judge("deepseek-v4-flash", BUNDLE, base_url="https://x", api_key="k", poster=stub)
    assert seen["url"].endswith("/chat/completions") and seen["model"] == "deepseek-v4-flash"
    assert seen["max_tokens"] >= 8192  # room for reasoning tokens + JSON answer
    assert out["_parse_ok"] and out["_model"] == "deepseek-v4-flash" and out["_usage"]["total_tokens"] == 42
