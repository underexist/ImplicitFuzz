"""Templated pos/neg prog assembly for verified gate families (reuses the
execverify READ FIXED_FILE value-granularity template and the fixed-buffer
state template). Non-family -> None (unsupported). pos = candidate's
claimed-satisfy case, neg = claimed-violate; the relation decides which index
goes where so a mutant (wrong direction) yields opposite-direction evidence."""

from __future__ import annotations

TEMPLATE_VERSION = "1.0"
FAMILIES = {"fixed_file", "fixed_buffer"}

_SETUP = (
    "r3 = openat(0xffffffffffffff9c, &AUTO='./file0\\x00', 0x42, 0x1a4)\n"
    "r0 = syz_io_uring_setup(0x100, &AUTO={0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, "
    "\"000000000000000000000000\", [0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0], "
    "[0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0]}, &AUTO=<r1=>0x0, &AUTO=<r2=>0x0)\n"
)
_ENTER = "io_uring_enter(r0, 0x1, 0x1, 0x1, 0x0, 0x0)\nr9 = syz_io_uring_complete(r1)\n"


def _fixed_file_prog(nfiles: int, fd_index: int) -> str:
    files = ", ".join(["r3"] * nfiles)
    return (
        _SETUP
        + "io_uring_register$IORING_REGISTER_FILES(r0, 0x2, &AUTO=[%s], 0x%x)\n" % (files, nfiles)
        + "syz_io_uring_submit(r1, r2, &AUTO=@IORING_OP_READ=@pass_buffer="
          "{0x16, 0x1, 0x0, @fd_index=0x%x, 0x0, 0x0})\n" % fd_index
        + _ENTER
    )


def _fixed_buffer_prog(register: bool, unregister: bool) -> str:
    p = _SETUP
    if register:
        p += ("io_uring_register$IORING_REGISTER_BUFFERS(r0, 0x0, "
              "&AUTO=[{&AUTO=&(0x7f0000002000)=\"\"/8192, 0x5000}], 0x1)\n")
    if unregister:
        p += "io_uring_register$IORING_UNREGISTER_BUFFERS(r0, 0x1, 0x0, 0x0)\n"
    p += ("syz_io_uring_submit(r1, r2, &AUTO=@IORING_OP_READ_FIXED="
          "{AUTO, 0x0, 0x3, 0x0, 0x0, 0x7f0000002000})\n") + _ENTER
    return p


def assemble_progs(candidate: dict) -> dict | None:
    fam = candidate.get("gate_family")
    if fam not in FAMILIES:
        return None
    relation = candidate.get("param_align", {}).get("relation", "<")
    if fam == "fixed_file":
        # value-granularity: fix nr_args=2, vary fd_index. satisfy < 2, violate >= 2.
        satisfy_idx, violate_idx = (1, 5) if relation == "<" else (5, 1)
        pos, neg = _fixed_file_prog(2, satisfy_idx), _fixed_file_prog(2, violate_idx)
    else:  # fixed_buffer state gate: register (satisfy) vs register+unregister (violate)
        sat_reg = (True, False) if relation == "<" else (True, True)
        vio_reg = (True, True) if relation == "<" else (True, False)
        pos = _fixed_buffer_prog(register=sat_reg[0], unregister=sat_reg[1])
        neg = _fixed_buffer_prog(register=vio_reg[0], unregister=vio_reg[1])
    return {"pos": pos, "neg": neg, "template_family": fam, "template_version": TEMPLATE_VERSION}
