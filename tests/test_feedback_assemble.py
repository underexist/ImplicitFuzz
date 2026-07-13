from implicitfuzz.feedback.assemble import assemble_progs, TEMPLATE_VERSION


def _cand(fam, relation=">"):
    return {"candidate_id": "c", "gate_family": fam, "param_align": {"relation": relation}}


def test_fixed_file_real_relation_satisfy_is_valid_index():
    a = assemble_progs({"gate_family": "fixed_file", "param_align": {"relation": "<"}})
    assert a["template_family"] == "fixed_file" and a["template_version"] == TEMPLATE_VERSION
    # claimed-satisfy (pos) = valid index 1 (< 2 registered); claimed-violate (neg) = OOB 5
    assert "@fd_index=0x1" in a["pos"] and "@fd_index=0x5" in a["neg"]
    assert "IORING_REGISTER_FILES" in a["pos"] and "IORING_REGISTER_FILES" in a["neg"]


def test_fixed_file_mutant_relation_flips_pos_neg():
    # synthetic negative control: claims fd_index >= nr_args -> pos uses OOB index
    a = assemble_progs({"gate_family": "fixed_file", "param_align": {"relation": ">="}})
    assert "@fd_index=0x5" in a["pos"] and "@fd_index=0x1" in a["neg"]


def test_non_family_is_unsupported():
    assert assemble_progs({"gate_family": "io_kiocb_flags_premise", "param_align": {"relation": "<"}}) is None
