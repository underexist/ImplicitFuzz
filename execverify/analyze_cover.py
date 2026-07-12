#!/usr/bin/env python3
"""Differential KCOV coverage oracle for the READ_FIXED fixed-buffer gate.

Parses covered kernel PCs from the coverfile section of two syz-execprog serial
logs (positive/negative), symbolizes the positive-minus-negative differential
against vmlinux via addr2line, and reports whether the target fixed-buffer path
(io_import_fixed / io_prep_rw fixed block) is reached ONLY by the positive prog.
Coverage is a proxy judge (purpose.md 4.2(5)); the differential controls noise.
"""
import re, subprocess, sys, json, collections

VMLINUX = "/home/xujunru/syzkaller-docker/kernel-src/vmlinux"
TARGET_HINTS = ("io_import_fixed", "io_prep_rw", "io_prep_rw_fixed", "io_import_iovec")
PC_RE = re.compile(r"0xffffffff8[0-9a-fA-F]+")
BEGIN, END = "EXECVERIFY_COVERFILE_BEGIN", "EXECVERIFY_DONE"


def coverfile_pcs(log):
    txt = open(log, errors="replace").read()
    b = txt.find(BEGIN)
    e = txt.find(END, b if b >= 0 else 0)
    section = txt[b:e] if b >= 0 and e >= 0 else txt
    return {int(m.group(0), 16) for m in PC_RE.finditer(section)}


def symbolize(pcs):
    if not pcs:
        return {}
    ps = sorted(pcs)
    out = subprocess.run(["addr2line", "-f", "-e", VMLINUX] + [hex(p) for p in ps],
                         capture_output=True, text=True).stdout.splitlines()
    return {ps[i]: (out[2 * i] if 2 * i < len(out) else "?") for i in range(len(ps))}


def main():
    pos_log, neg_log = sys.argv[1], sys.argv[2]
    P, N = coverfile_pcs(pos_log), coverfile_pcs(neg_log)
    diff = P - N
    sym = symbolize(diff)
    by_func = collections.Counter(sym.values())
    target_pcs = {hex(p): fn for p, fn in sym.items() if any(h in fn for h in TARGET_HINTS)}
    verified = len(target_pcs) > 0
    result = {
        "pos_pc": len(P), "neg_pc": len(N), "diff_pc": len(diff),
        "diff_top_functions": by_func.most_common(15),
        "target_hits": target_pcs,
        "gate_execution_verified": verified,
    }
    print(json.dumps(result, indent=2))
    return 0 if verified else 2


if __name__ == "__main__":
    raise SystemExit(main())
