#!/usr/bin/env python3
"""Differential KCOV coverage oracle for a state-gate.

Usage: analyze_cover.py <pos.log> <neg.log> [signal_func]
Parses covered kernel PCs from the coverfile section of two syz-execprog serial
logs and, using the vmlinux matching the booted bzImage (out/kernel/vmlinux),
counts covered PCs per io_uring function via nm ranges. The gate is verified if
the signal function (io_prep_rw for fixed-buffer; io_read for fixed-file, the
op gated by io_file_get_fixed which is inlined) is covered more by the positive
prog (predicate satisfied). Coverage is a proxy judge (purpose.md 4.2(5)).
"""
import re, subprocess, sys, json

VMLINUX = "/home/xujunru/syzkaller-docker/out/kernel/vmlinux"
REPORT = ("io_prep_rw", "io_import_fixed", "io_read", "io_file_get_fixed",
          "io_submit_sqes", "io_issue_sqe", "io_sqe_buffers_register",
          "io_sqe_files_register")
PC_RE = re.compile(r"0xffffffff8[0-9a-fA-F]+")


def coverfile_pcs(log):
    txt = open(log, errors="replace").read()
    b, e = txt.find("EXECVERIFY_COVERFILE_BEGIN"), txt.find("EXECVERIFY_DONE")
    section = txt[b:e] if b >= 0 and e >= 0 else txt
    return {int(m.group(0), 16) for m in PC_RE.finditer(section)}


def ranges(names):
    out = subprocess.run(["nm", "-n", VMLINUX], capture_output=True, text=True).stdout.splitlines()
    syms = [(int(p[0], 16), p[2]) for l in out if len(p := l.split()) == 3 and p[1] in "tTwW"]
    r = {}
    for i, (a, n) in enumerate(syms):
        if n in names:
            r[n] = (a, syms[i + 1][0] if i + 1 < len(syms) else a + 0x400)
    return r


def main():
    pos_log, neg_log = sys.argv[1], sys.argv[2]
    signal = sys.argv[3] if len(sys.argv) > 3 else "io_prep_rw"
    P, N = coverfile_pcs(pos_log), coverfile_pcs(neg_log)
    rng = ranges(set(REPORT) | {signal})
    def cnt(pcs, fn):
        a, hi = rng[fn]
        return sum(1 for p in pcs if a <= p < hi)
    per = {fn: {"pos": cnt(P, fn), "neg": cnt(N, fn)} for fn in rng}
    sp, sn = per.get(signal, {}).get("pos", 0), per.get(signal, {}).get("neg", 0)
    verified = sp > sn
    print(json.dumps({"signal": signal, "signal_pos": sp, "signal_neg": sn,
                      "gate_execution_verified": verified,
                      "per_function": per}, indent=2))
    return 0 if verified else 2


if __name__ == "__main__":
    raise SystemExit(main())
