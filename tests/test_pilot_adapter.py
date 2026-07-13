import json, os, pytest
from implicitfuzz.feedback.pilot_adapter import adapt_gate, AdapterError

GATES_DIR = "src/implicitfuzz/pilot/gates"
RUNS_DIR = "pilot/runs_formal"

RFF_DEF = {"field_clues": ["fd", "ctx->nr_user_files", "ctx->file_table"]}
RFF_RUN = {"target_gate": {"function": "io_file_get_fixed"}, "terms": [
    {"class": "activation", "field_ref": "io_ring_ctx.nr_user_files",
     "relation": "(unsigned int)fd < ctx->nr_user_files (branch not-taken reaches file_table read)",
     "confidence": "high"}]}

FB_DEF = {"field_clues": ["req->buf_index", "ctx->nr_user_bufs", "ctx->user_bufs"]}
FB_RUN = {"target_gate": {"function": "io_prep_rw"}, "terms": [
    {"class": "activation", "field_ref": "io_kiocb.opcode",
     "relation": "opcode == READ_FIXED || WRITE_FIXED", "confidence": "high"},
    {"class": "activation", "field_ref": "io_kiocb.buf_index",
     "relation": "buf_index < ctx->nr_user_bufs", "confidence": "high"}]}

CANCEL_DEF = {"field_clues": ["sqe->fd", "ctx->nr_user_files", "ctx->file_table"]}
CANCEL_RUN = {"target_gate": {"function": "io_sync_cancel"}, "terms": [
    {"class": "activation", "field_ref": "io_cancel_data.flags",
     "relation": "(flags & CANCEL_FD) && (flags & CANCEL_FD_FIXED)", "confidence": "high"},
    {"class": "activation", "field_ref": "io_ring_ctx.nr_user_files",
     "relation": "fd < ctx->nr_user_files", "confidence": "high"}]}

FLAGS_DEF = {"field_clues": ["req->flags"]}
FLAGS_RUN = {"target_gate": {"function": "__io_req_complete_post"}, "terms": [
    {"class": "activation", "field_ref": "io_kiocb.flags",
     "relation": "(flags & IO_REQ_LINK_FLAGS) != 0", "confidence": "high"}]}


def test_read_fixed_file_executable():
    c = adapt_gate("read_fixed_file", RFF_DEF, RFF_RUN)
    assert c["executable"] is True
    assert c["gate_family"] == "fixed_file" and c["operation"] == "READ"
    assert c["signal_function"] == "io_read"
    assert c["field_relation"] == "fd < nr_user_files"
    assert c["static_confidence"] == "high" and c["unsupported_reason"] is None


def test_fixed_buffer_executable_opcode_derived_op():
    c = adapt_gate("fixed_buffer", FB_DEF, FB_RUN)
    assert c["executable"] is True and c["gate_family"] == "fixed_buffer"
    assert c["operation"] == "READ_FIXED" and c["signal_function"] == "io_prep_rw"
    assert c["field_relation"] == "buf_index < nr_user_bufs"


def test_same_relation_wrong_target_fn_is_op_not_covered():
    # cancel shares 'fd < nr_user_files' but io_sync_cancel is not templated
    c = adapt_gate("cancel_fixed_file", CANCEL_DEF, CANCEL_RUN)
    assert c["executable"] is False
    assert c["gate_family"] is None                       # -> assemble returns None
    assert c["field_relation_family"] == "fixed_file"     # true family recorded
    assert c["unsupported_reason"] == "op_not_covered"


def test_flags_gate_is_no_family_template():
    c = adapt_gate("kiocb_flags", FLAGS_DEF, FLAGS_RUN)
    assert c["executable"] is False and c["field_relation_family"] is None
    assert c["unsupported_reason"] == "no_family_template"


def test_min_activation_confidence():
    run = {"target_gate": {"function": "io_file_get_fixed"}, "terms": [
        {"class": "activation", "field_ref": "a", "relation": "fd < ctx->nr_user_files", "confidence": "high"},
        {"class": "activation", "field_ref": "b", "relation": "x", "confidence": "medium_low"}]}
    assert adapt_gate("g", RFF_DEF, run)["static_confidence"] == "low"


def test_no_activation_term_raises():
    run = {"target_gate": {"function": "io_file_get_fixed"},
           "terms": [{"class": "premise", "field_ref": "a", "relation": "x", "confidence": "high"}]}
    with pytest.raises(AdapterError):
        adapt_gate("g", RFF_DEF, run)


def test_build_snapshot_provenance_and_determinism():
    from implicitfuzz.feedback.pilot_adapter import build_snapshot, ADAPTER_VERSION
    ids = ["read_fixed_file", "fixed_buffer", "cancel_fixed_file", "kiocb_flags"]
    snap = build_snapshot(ids, GATES_DIR, RUNS_DIR, source_commit="deadbee")
    assert snap["source_commit"] == "deadbee"
    assert snap["gate_ids"] == sorted(ids)
    assert snap["adapter_version"] == ADAPTER_VERSION
    assert "schema_version" in snap and len(snap["generated_hash"]) == 64
    # deterministic: same inputs -> same hash (hash excludes commit)
    snap2 = build_snapshot(ids, GATES_DIR, RUNS_DIR, source_commit="other")
    assert snap["generated_hash"] == snap2["generated_hash"]
    by = {c["candidate_id"]: c for c in snap["candidates"]}
    assert by["read_fixed_file"]["executable"] and by["fixed_buffer"]["executable"]
    assert by["cancel_fixed_file"]["unsupported_reason"] == "op_not_covered"
    assert by["kiocb_flags"]["unsupported_reason"] == "no_family_template"
