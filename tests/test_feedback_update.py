from implicitfuzz.feedback.update import classify_outcome, update_confidence, rerank


def test_classify_outcome():
    assert classify_outcome(19, 0) == "verified"          # pos >> neg
    assert classify_outcome(0, 19) == "contradicted"      # opposite direction
    assert classify_outcome(8, 8) == "undecidable"        # pos ~= neg -> NOT demote
    assert classify_outcome(9, 8) == "undecidable"        # within margin
    assert classify_outcome(None, 5) == "undecidable"     # run failure


def test_update_confidence():
    assert update_confidence("high", "verified")[0] == "execution_verified"
    assert update_confidence("high", "contradicted")[0] == "rejected"
    assert update_confidence("medium", "undecidable") == ("medium",
        "undecidable: signals inseparable (pos~=neg) or run failure")
    assert update_confidence("low", "unsupported")[0] == "low"
    assert "no assembler template" in update_confidence("low", "unsupported")[1]


def test_rerank_orders_by_final_tier():
    view = [{"final_confidence": "low"}, {"final_confidence": "execution_verified"},
            {"final_confidence": "rejected"}, {"final_confidence": "high"}]
    order = [r["final_confidence"] for r in rerank(view)]
    assert order == ["execution_verified", "high", "low", "rejected"]
