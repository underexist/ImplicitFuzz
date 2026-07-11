from implicitfuzz.pilot.validate import validate_schema


def _good():
    return {
        "target_gate": {"bc_unit": "io_uring.bc", "function": "io_file_get_fixed",
                        "branch_instruction_id": "b1"},
        "terms": [{
            "class": "activation", "object": "io_ring_ctx",
            "field_ref": "io_ring_ctx.nr_user_files",
            "relation": "fd < ctx->nr_user_files", "align_target": "",
            "source": "slice", "confidence": "high", "uncertain": False,
        }],
        "abstain": False, "alt_candidates": [],
    }


def test_validate_schema_accepts_good():
    assert validate_schema(_good()) is True


def test_validate_schema_rejects_bad_class():
    p = _good()
    p["terms"][0]["class"] = "not_a_class"
    assert validate_schema(p) is False


def test_validate_schema_rejects_missing_terms():
    p = _good()
    del p["terms"]
    assert validate_schema(p) is False
