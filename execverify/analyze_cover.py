#!/usr/bin/env python3
"""Differential KCOV coverage oracle for the READ_FIXED fixed-buffer gate.

Parses covered kernel PCs from the coverfile section of two syz-execprog serial
logs (positive/negative) and, using the vmlinux that MATCHES the booted bzImage
(out/kernel/vmlinux -- NOT kernel-src/vmlinux, a different build), counts covered
PCs per target io_uring function via nm address ranges. The gate is verified if
the gate/fixed-path functions are covered MORE by the positive prog (predicate
satisfied) than the negative. Coverage is a proxy judge (purpose.md 4.2(5)).
"""
import re, subprocess, sys, json

VMLINUX = "/home/xujunru/syzkaller-docker/out/kernel/vmlinux"
TARGETS = ("io_prep_rw", "io_import_fixed", "io_read", "io_import_iovec",
           "io_submit_sqes", "io_sqe_buffers_register")
PC_RE = re.compile(r"0xffffffff8[0-9a-fA-F]+")
BEGIN, END = "EXECVERIFY_COVERFILE_BEGIN", "EXECVERIFY_DONE"


def coverfile_pcs(log):
    txt = open(log, errors="replace").read()
    b, e = txt.find(BEGIN), txt.find(END)
    section = txt[b:e] if b >= 0 and e >= 0 else txt
    return {int(m.group(0), 16) for m in PC_RE.finditer(section)}


def func_ranges(vmlinux, names):
    out = subprocess.run(["nm", "-n", vmlinux], capture_output=True, text=True).stdout.splitlines()
    syms = [(int(p[0], 16), p[2]) for l in out if len(p := l.split()) == 3 and p[1] in "tTwW"]
    ranges = {}
    for i, (a, n) in enumerate(syms):
        if n in names:
            hi = syms[i + 1][0] if i + 1 < len(syms) else a + 0x400
            ranges[n] = (a, hi)
    return ranges


def count_in(pcs, rng):
    a, hi = rng
    return sum(1 for p in pcs if a <= p < hi)


def main():
    pos_log, neg_log = sys.argv[1], sys.argv[2]
    P, N = coverfile_pcs(pos_log), coverfile_pcs(neg_log)
    ranges = func_ranges(VMLINUX, set(TARGETS))
    per_func = {}
    for fn in TARGETS:
        if fn not in ranges:
            continue
        cp, cn = count_in(P, ranges[fn]), count_in(N, ranges[fn])
        per_func[fn] = {"pos": cp, "neg": cn, "pos_gt_neg": cp > cn}
    # gate verified if the gate function is covered strictly more by positive
    gate = per_func.get("io_prep_rw", {})
    verified = bool(gate.get("pos_gt_neg"))
    result = {
        "vmlinux": VMLINUX, "pos_total_pc": len(P), "neg_total_pc": len(N),
        "per_function": per_func,
        "gate": "io_prep_rw", "gate_execution_verified": verified,
    }
    print(json.dumps(result, indent=2))
    return 0 if verified else 2


if __name__ == "__main__":
    raise SystemExit(main())
