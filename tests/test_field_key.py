from implicitfuzz.evidence.field_key import (
    FieldKey,
    field_key_for_access,
    canonicalize_struct_type,
    lower_confidence,
)


def test_symbolic_access_yields_sym_key():
    fk = field_key_for_access(
        access_path_symbolic="io_ring_ctx.file_data",
        confidence="medium",
        source_access_fact_id=7,
    )
    assert fk == FieldKey("symbolic", "sym:io_ring_ctx.file_data", "medium", 7)


def test_numeric_only_real_data_yields_none():
    # real v1 numeric-only facts carry no struct type (field_type is null)
    fk = field_key_for_access(
        access_path_symbolic=None,
        field_type=None,
        numeric_kind="gep_offsets",
        access_path_numeric="[128]",
        confidence="low",
    )
    assert fk is None


def test_numeric_with_struct_type_yields_num_key_lower_confidence():
    # post-v1 shape: once the extractor emits a struct type, the numeric
    # branch fires. confidence is strictly lowered one notch vs symbolic.
    fk = field_key_for_access(
        access_path_symbolic=None,
        field_type="struct io_ring_ctx",
        numeric_kind="gep_offsets",
        access_path_numeric="[120]",
        confidence="high",
    )
    assert fk == FieldKey("numeric", "num:io_ring_ctx@[120]", "medium_high", None)


def test_canonicalize_strips_struct_const_volatile_noise():
    assert canonicalize_struct_type("struct io_ring_ctx") == "io_ring_ctx"
    assert canonicalize_struct_type("const struct io_ring_ctx *") == "io_ring_ctx"
    assert canonicalize_struct_type("volatile io_ring_ctx") == "io_ring_ctx"
    assert canonicalize_struct_type("io_ring_ctx") == "io_ring_ctx"
    assert canonicalize_struct_type(None) is None
    assert canonicalize_struct_type("  ") is None


def test_lower_confidence_drops_one_notch_and_floors_at_low():
    assert lower_confidence("high") == "medium_high"
    assert lower_confidence("medium_high") == "medium"
    assert lower_confidence("medium") == "medium_low"
    assert lower_confidence("medium_low") == "low"
    assert lower_confidence("low") == "low"
