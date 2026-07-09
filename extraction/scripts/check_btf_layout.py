#!/usr/bin/env python3
"""Cross-check golden case struct.field layouts against BTF (reference only)."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


STRUCT_LINE_RE = re.compile(r"^\[\d+\]\s+STRUCT\s+'([^']+)'\s+")
MEMBER_LINE_RE = re.compile(
    r"^\t'([^']+)'\s+type_id=\d+\s+bits_offset=(\d+)\s*$"
)


@dataclass
class CaseCheck:
    case_id: str
    struct_name: str
    field_name: str
    dwarf_byte_offset: int | None
    btf_bits_offset: int | None
    btf_byte_offset: int | None
    status: str
    detail: str


def load_ground_truth(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_case(gt: dict) -> tuple[str, str, str, int | None]:
    case_id = gt.get("case_id", path_stem(gt))
    sym = gt.get("expected_access_path_symbolic")
    if not sym:
        sym = (gt.get("stage2_quality_goal") or {}).get("expect_access_path_symbolic")
    if sym and "." in sym:
        struct_name, field_name = sym.rsplit(".", 1)
    else:
        struct_name = gt.get("expected_struct_type") or gt.get("expected_base", "")
        field_name = gt.get("expected_field_name", "")
    byte_offset = gt.get("expected_byte_offset")
    if byte_offset is None:
        numeric = gt.get("expected_numeric")
        if isinstance(numeric, list) and len(numeric) == 1:
            byte_offset = int(numeric[0])
    return case_id, struct_name, field_name, byte_offset


def path_stem(gt: dict) -> str:
    return str(gt.get("case_id", "unknown"))


def load_btf_dump(btf_path: Path, dump_file: Path | None) -> str:
    if dump_file is not None:
        return dump_file.read_text(encoding="utf-8", errors="replace")
    if not btf_path.is_file():
        raise FileNotFoundError(f"BTF source not found: {btf_path}")
    if shutil.which("bpftool") is None:
        raise RuntimeError("bpftool not found in PATH")
    proc = subprocess.run(
        ["bpftool", "btf", "dump", "file", str(btf_path), "format", "raw"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "bpftool btf dump failed")
    return proc.stdout


def parse_struct_members(dump: str) -> dict[str, dict[str, int]]:
    structs: dict[str, dict[str, int]] = {}
    current: str | None = None
    for line in dump.splitlines():
        sm = STRUCT_LINE_RE.match(line)
        if sm:
            current = sm.group(1)
            structs[current] = {}
            continue
        mm = MEMBER_LINE_RE.match(line)
        if mm and current is not None:
            structs[current][mm.group(1)] = int(mm.group(2))
    return structs


def check_case(
    gt: dict, structs: dict[str, dict[str, int]]
) -> CaseCheck:
    case_id, struct_name, field_name, dwarf_byte_offset = resolve_case(gt)
    members = structs.get(struct_name)
    if members is None:
        return CaseCheck(
            case_id=case_id,
            struct_name=struct_name,
            field_name=field_name,
            dwarf_byte_offset=dwarf_byte_offset,
            btf_bits_offset=None,
            btf_byte_offset=None,
            status="missing_struct",
            detail=f"BTF has no STRUCT '{struct_name}'",
        )
    bits = members.get(field_name)
    if bits is None:
        return CaseCheck(
            case_id=case_id,
            struct_name=struct_name,
            field_name=field_name,
            dwarf_byte_offset=dwarf_byte_offset,
            btf_bits_offset=None,
            btf_byte_offset=None,
            status="missing_field",
            detail=f"BTF struct '{struct_name}' has no member '{field_name}'",
        )
    btf_byte = bits // 8
    if dwarf_byte_offset is None:
        return CaseCheck(
            case_id=case_id,
            struct_name=struct_name,
            field_name=field_name,
            dwarf_byte_offset=None,
            btf_bits_offset=bits,
            btf_byte_offset=btf_byte,
            status="btf_only",
            detail="no DWARF byte offset in ground truth",
        )
    if dwarf_byte_offset == btf_byte:
        return CaseCheck(
            case_id=case_id,
            struct_name=struct_name,
            field_name=field_name,
            dwarf_byte_offset=dwarf_byte_offset,
            btf_bits_offset=bits,
            btf_byte_offset=btf_byte,
            status="match",
            detail="DWARF and BTF byte offsets agree",
        )
    return CaseCheck(
        case_id=case_id,
        struct_name=struct_name,
        field_name=field_name,
        dwarf_byte_offset=dwarf_byte_offset,
        btf_bits_offset=bits,
        btf_byte_offset=btf_byte,
        status="mismatch",
        detail=(
            f"DWARF byte offset {dwarf_byte_offset} != "
            f"BTF byte offset {btf_byte} ({bits} bits)"
        ),
    )


def render_report(
    btf_source: str,
    env_notes: list[str],
    checks: list[CaseCheck],
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# BTF Layout Smoke Report",
        "",
        f"Generated: {now}",
        "",
        "## Environment",
        "",
        f"- BTF source: `{btf_source}`",
    ]
    for note in env_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## Cross-checks", ""])
    for chk in checks:
        sym = f"{chk.struct_name}.{chk.field_name}"
        dwarf = (
            f"{chk.dwarf_byte_offset} bytes"
            if chk.dwarf_byte_offset is not None
            else "n/a"
        )
        btf = (
            f"{chk.btf_byte_offset} bytes ({chk.btf_bits_offset} bits)"
            if chk.btf_bits_offset is not None
            else "n/a"
        )
        lines.append(
            f"- **{chk.case_id}** `{sym}`: "
            f"DWARF offset={dwarf}, BTF offset={btf} -> **{chk.status}**"
        )
        if chk.detail:
            lines.append(f"  - {chk.detail}")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- BTF is used as a layout reference only; facts still use "
            "`primary_provenance: dwarf`.",
            "- This check does not modify extractor output.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--btf",
        type=Path,
        default=Path("/sys/kernel/btf/vmlinux"),
        help="vmlinux or BTF file for bpftool (default: runtime vmlinux BTF)",
    )
    parser.add_argument(
        "--dump-file",
        type=Path,
        help="optional pre-dumped bpftool raw output (skip bpftool invocation)",
    )
    parser.add_argument(
        "--case",
        type=Path,
        action="append",
        required=True,
        help="ground_truth.json (repeatable)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="write markdown report (default: extraction/reports/btf-layout-smoke.md)",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    report_path = args.report or root / "extraction" / "reports" / "btf-layout-smoke.md"

    env_notes: list[str] = []
    if args.dump_file is not None:
        btf_source = str(args.dump_file)
    else:
        btf_source = str(args.btf)
        if not args.btf.is_file():
            env_notes.append(
                "BTF source missing; DWARF-based recovery remains primary. "
                "BTF integration deferred."
            )
            report = render_report(btf_source, env_notes, [])
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report, encoding="utf-8")
            print(f"BTF layout check DEFERRED: {args.btf} not found", file=sys.stderr)
            print(f"report: {report_path}")
            return 2

    try:
        dump = load_btf_dump(args.btf, args.dump_file)
    except (FileNotFoundError, RuntimeError) as exc:
        env_notes.append(str(exc))
        env_notes.append(
            "DWARF-based recovery remains primary. BTF integration deferred."
        )
        report = render_report(btf_source, env_notes, [])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        print(f"BTF layout check DEFERRED: {exc}", file=sys.stderr)
        print(f"report: {report_path}")
        return 2

    structs = parse_struct_members(dump)
    checks = [check_case(load_ground_truth(case), structs) for case in args.case]

    if args.btf == Path("/sys/kernel/btf/vmlinux"):
        env_notes.append("using runtime kernel BTF (/sys/kernel/btf/vmlinux)")
    env_notes.append(
        "linux-6.1 tree vmlinux has no .BTF section in current build "
        "(CONFIG_DEBUG_INFO_BTF not enabled)"
    )
    if shutil.which("pahole") is None:
        env_notes.append("pahole not installed (not required for this smoke check)")

    report = render_report(btf_source, env_notes, checks)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    failed = [c for c in checks if c.status not in ("match", "btf_only")]
    for chk in checks:
        sym = f"{chk.struct_name}.{chk.field_name}"
        dwarf = chk.dwarf_byte_offset if chk.dwarf_byte_offset is not None else "n/a"
        btf = chk.btf_byte_offset if chk.btf_byte_offset is not None else "n/a"
        print(f"{chk.case_id}: DWARF {sym} offset={dwarf} bytes, BTF offset={btf} bytes -> {chk.status}")

    print(f"report: {report_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
