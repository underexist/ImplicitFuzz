#!/usr/bin/env python3
"""Render small Phase 2B evidence graph visualizations as SVG + HTML."""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = Path("/tmp/phase1_facts.db")
DEFAULT_OUT = ROOT / "docs" / "visualizations" / "phase2b"

COLORS = {
    "bg": "#f8fafc",
    "ink": "#0f172a",
    "muted": "#64748b",
    "line": "#94a3b8",
    "fact": "#dbeafe",
    "fact_border": "#2563eb",
    "edge": "#ecfeff",
    "edge_border": "#0891b2",
    "candidate": "#fef3c7",
    "candidate_border": "#d97706",
    "warn": "#fee2e2",
    "warn_border": "#dc2626",
    "ok": "#dcfce7",
    "ok_border": "#16a34a",
    "purple": "#ede9fe",
    "purple_border": "#7c3aed",
}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def source_line(source_location_json: str | None) -> str:
    if not source_location_json:
        return ""
    try:
        data = json.loads(source_location_json)
    except json.JSONDecodeError:
        return ""
    spelling = data.get("spelling") or ""
    if not spelling:
        return ""
    path, _, line = spelling.rpartition(":")
    return f"{Path(path).name}:{line}" if path else spelling


class Svg:
    def __init__(self, width: int, height: int, title: str):
        self.width = width
        self.height = height
        self.parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            f'<rect width="{width}" height="{height}" fill="{COLORS["bg"]}"/>',
        ]
        self.text(28, 36, title, size=22, weight="700")

    def text(
        self,
        x: int,
        y: int,
        text: str,
        *,
        size: int = 13,
        fill: str | None = None,
        weight: str = "400",
        anchor: str = "start",
    ) -> None:
        fill = fill or COLORS["ink"]
        self.parts.append(
            f'<text x="{x}" y="{y}" font-family="Inter, Arial, sans-serif" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{esc(text)}</text>'
        )

    def line(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        color: str | None = None,
        width: int = 2,
        dash: bool = False,
        arrow: bool = False,
    ) -> None:
        color = color or COLORS["line"]
        dash_attr = ' stroke-dasharray="6 5"' if dash else ""
        marker = ' marker-end="url(#arrow)"' if arrow else ""
        self.parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
            f'stroke-width="{width}"{dash_attr}{marker}/>'
        )

    def arrow_defs(self) -> None:
        self.parts.insert(
            2,
            """
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L0,6 L9,3 z" fill="#64748b"/>
  </marker>
</defs>
""",
        )

    def box(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        title: str,
        lines: list[str],
        *,
        fill: str,
        stroke: str,
    ) -> None:
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="2"/>'
        )
        self.text(x + 14, y + 25, title, size=14, weight="700")
        for i, line in enumerate(lines):
            self.text(x + 14, y + 48 + i * 18, line, size=12, fill=COLORS["muted"])

    def pill(self, x: int, y: int, label: str, *, fill: str, stroke: str) -> None:
        width = max(100, 9 * len(label) + 28)
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="32" rx="16" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="1.5"/>'
        )
        self.text(x + width // 2, y + 21, label, size=12, anchor="middle", weight="600")

    def save(self, path: Path) -> None:
        self.parts.append("</svg>")
        path.write_text("\n".join(self.parts), encoding="utf-8")


def fetch_counts(conn: sqlite3.Connection) -> tuple[dict[str, int], dict[str, int]]:
    conn.row_factory = sqlite3.Row
    nodes = {
        row["node_kind"]: row["count"]
        for row in conn.execute(
            "SELECT node_kind, COUNT(*) AS count FROM evidence_node GROUP BY node_kind"
        )
    }
    edges = {
        row["edge_kind"]: row["count"]
        for row in conn.execute(
            "SELECT edge_kind, COUNT(*) AS count FROM evidence_edge GROUP BY edge_kind"
        )
    }
    return nodes, edges


def render_summary(conn: sqlite3.Connection, out: Path) -> None:
    nodes, edges = fetch_counts(conn)
    items = [("access nodes", nodes.get("access", 0))] + [
        (name.replace("_", " "), count) for name, count in sorted(edges.items())
    ]
    svg = Svg(980, 520, "Phase 2B evidence graph summary")
    max_count = max(count for _, count in items)
    left = 260
    top = 90
    bar_max = 610
    for i, (label, count) in enumerate(items):
        y = top + i * 64
        width = int(bar_max * count / max_count)
        fill = COLORS["fact"] if i == 0 else COLORS["candidate"]
        stroke = COLORS["fact_border"] if i == 0 else COLORS["candidate_border"]
        svg.text(36, y + 24, label, size=14, fill=COLORS["ink"])
        svg.parts.append(
            f'<rect x="{left}" y="{y}" width="{width}" height="34" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
        )
        svg.text(left + width + 12, y + 23, str(count), size=14, weight="700")
    svg.text(
        36,
        475,
        "Candidate edges are auditable seeds, not final dependency claims.",
        size=13,
        fill=COLORS["muted"],
    )
    svg.save(out / "00_phase2b_counts.svg")


def render_pipeline(out: Path) -> None:
    svg = Svg(1180, 420, "Static extraction progress: from facts to candidate graph")
    svg.arrow_defs()
    boxes = [
        (40, "LLVM bitcode", ["linux-6.1 io_uring", "LLVM 21.1.8 + SVF"]),
        (275, "Phase 1 facts", ["call_fact / access_fact", "DWARF + summary"]),
        (510, "SQLite store", ["753 access facts", "189 call facts"]),
        (745, "Phase 2A graph", ["753 access nodes", "state + lifecycle candidates"]),
        (980, "Phase 2B candidates", ["identity candidates", "explicit dependency seeds"]),
    ]
    y = 125
    for x, title, lines in boxes:
        svg.box(x, y, 175, 112, title, lines, fill=COLORS["fact"], stroke=COLORS["fact_border"])
    for x, _, _ in boxes[:-1]:
        svg.line(x + 175, y + 56, x + 235, y + 56, arrow=True)
    svg.box(
        392,
        295,
        400,
        72,
        "Current boundary",
        ["No final alias closure yet; no branch/gate facts yet; no LLM or syzkaller generation yet."],
        fill=COLORS["warn"],
        stroke=COLORS["warn_border"],
    )
    svg.save(out / "01_pipeline_overview.svg")


def render_lifecycle(conn: sqlite3.Connection, out: Path) -> None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT
          e.source_fact_id,
          e.target_fact_id,
          e.confidence,
          s.function AS source_function,
          s.access_kind AS source_kind,
          s.source_location_json AS source_loc,
          t.function AS target_function,
          t.access_kind AS target_kind,
          t.source_location_json AS target_loc
        FROM evidence_edge e
        JOIN access_fact s ON s.id = e.source_fact_id
        JOIN access_fact t ON t.id = e.target_fact_id
        WHERE e.edge_kind = ?
          AND s.function = ?
        LIMIT 1
        """,
        ("explicit_dependency_candidate", "io_provide_buffers"),
    ).fetchone()
    svg = Svg(1120, 430, "Local graph: io_provide_buffers lifecycle candidate")
    svg.arrow_defs()
    if row is None:
        svg.text(50, 120, "No io_provide_buffers explicit dependency candidate found.", fill=COLORS["warn_border"])
        svg.save(out / "02_lifecycle_io_provide_buffers.svg")
        return
    svg.box(
        60,
        135,
        260,
        118,
        f"access_fact #{row['source_fact_id']}",
        [
            "semantic_op = alloc",
            f"kind = {row['source_kind']}",
            f"function = {row['source_function']}",
            f"loc = {source_line(row['source_loc'])}",
        ],
        fill=COLORS["ok"],
        stroke=COLORS["ok_border"],
    )
    svg.box(
        430,
        135,
        260,
        118,
        "explicit_dependency_candidate",
        [
            "kind = alloc_before_free",
            f"confidence = {row['confidence']}",
            "status = requires identity refinement",
        ],
        fill=COLORS["candidate"],
        stroke=COLORS["candidate_border"],
    )
    svg.box(
        800,
        135,
        260,
        118,
        f"access_fact #{row['target_fact_id']}",
        [
            "semantic_op = free",
            f"kind = {row['target_kind']}",
            f"function = {row['target_function']}",
            f"loc = {source_line(row['target_loc'])}",
        ],
        fill=COLORS["warn"],
        stroke=COLORS["warn_border"],
    )
    svg.line(320, 194, 430, 194, arrow=True)
    svg.line(690, 194, 800, 194, arrow=True)
    svg.text(70, 330, "Use this slide to explain: Phase 2B can already expose alloc/free producer-consumer seeds.", fill=COLORS["muted"])
    svg.save(out / "02_lifecycle_io_provide_buffers.svg")


def render_state_timeout(conn: sqlite3.Connection, out: Path) -> None:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
          e.source_fact_id,
          e.target_fact_id,
          e.confidence,
          s.function AS source_function,
          s.source_location_json AS source_loc,
          t.function AS target_function,
          t.source_location_json AS target_loc
        FROM evidence_edge e
        JOIN access_fact s ON s.id = e.source_fact_id
        JOIN access_fact t ON t.id = e.target_fact_id
        WHERE e.edge_kind = ?
          AND json_extract(e.detail_json, ?) = ?
        ORDER BY e.target_fact_id
        LIMIT 2
        """,
        ("state_write_read_candidate", "$.symbolic_field", "io_timeout.off"),
    ).fetchall()
    svg = Svg(1120, 460, "Local graph: io_timeout.off state candidates")
    svg.arrow_defs()
    if not rows:
        svg.text(50, 120, "No io_timeout.off state candidate found.", fill=COLORS["warn_border"])
        svg.save(out / "03_state_io_timeout_off.svg")
        return
    first = rows[0]
    svg.box(
        60,
        170,
        275,
        118,
        f"write fact #{first['source_fact_id']}",
        [
            "field = io_timeout.off",
            "semantic_op = write",
            f"function = {first['source_function']}",
            f"loc = {source_line(first['source_loc'])}",
        ],
        fill=COLORS["ok"],
        stroke=COLORS["ok_border"],
    )
    for i, row in enumerate(rows):
        x = 735
        y = 95 + i * 160
        svg.box(
            x,
            y,
            300,
            118,
            f"read fact #{row['target_fact_id']}",
            [
                "field = io_timeout.off",
                "semantic_op = read",
                f"function = {row['target_function']}",
                f"loc = {source_line(row['target_loc'])}",
            ],
            fill=COLORS["fact"],
            stroke=COLORS["fact_border"],
        )
        svg.line(335, 229, x, y + 59, arrow=True)
        svg.text(465, y + 50, "state_write_read_candidate", size=12, fill=COLORS["candidate_border"], weight="700")
        svg.text(465, y + 70, f"confidence = {row['confidence']}", size=12, fill=COLORS["muted"])
    svg.text(65, 385, "This is a state-coupling seed, not an explicit dependency yet.", fill=COLORS["muted"])
    svg.save(out / "03_state_io_timeout_off.svg")


def render_identity_iokiocb(conn: sqlite3.Connection, out: Path) -> None:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
          e.source_fact_id,
          e.target_fact_id,
          e.confidence,
          s.function AS function,
          s.access_path_symbolic AS source_symbolic,
          t.access_path_symbolic AS target_symbolic
        FROM evidence_edge e
        JOIN access_fact s ON s.id = e.source_fact_id
        JOIN access_fact t ON t.id = e.target_fact_id
        WHERE e.edge_kind = ?
          AND json_extract(e.detail_json, ?) = ?
          AND s.function = ?
        LIMIT 8
        """,
        ("object_identity_candidate", "$.object_prefix", "io_kiocb", "io_disarm_next"),
    ).fetchall()
    svg = Svg(1160, 500, "Local graph: weak object identity candidates for io_kiocb")
    svg.arrow_defs()
    center_x, center_y = 520, 215
    svg.box(
        center_x,
        center_y,
        300,
        112,
        "object_identity_candidate",
        [
            "prefix = io_kiocb",
            "scope = synthetic/minimal_stub",
            "confidence = low",
            "not final alias closure",
        ],
        fill=COLORS["candidate"],
        stroke=COLORS["candidate_border"],
    )
    left_fields = []
    right_fields = []
    for row in rows:
        left_fields.append(row["source_symbolic"])
        right_fields.append(row["target_symbolic"])
    left_fields = list(dict.fromkeys(left_fields))[:4]
    right_fields = list(dict.fromkeys(right_fields))[:5]
    for i, field in enumerate(left_fields):
        y = 95 + i * 80
        svg.box(60, y, 250, 58, field, [f"function = {rows[0]['function']}"], fill=COLORS["fact"], stroke=COLORS["fact_border"])
        svg.line(310, y + 29, center_x, center_y + 56, arrow=True, dash=True)
    for i, field in enumerate(right_fields):
        y = 65 + i * 76
        svg.box(880, y, 230, 54, field, [f"function = {rows[0]['function']}"], fill=COLORS["purple"], stroke=COLORS["purple_border"])
        svg.line(center_x + 300, center_y + 56, 880, y + 27, arrow=True, dash=True)
    svg.text(65, 455, "Use this slide to explain why Phase 2B is conservative: current base_object is still synthetic.", fill=COLORS["muted"])
    svg.save(out / "04_identity_io_kiocb.svg")


def render_index(out: Path) -> None:
    cards = [
        ("00_phase2b_counts.svg", "总览：Phase 2B 节点/候选边数量"),
        ("01_pipeline_overview.svg", "路线：从 bitcode 到候选依赖图"),
        ("02_lifecycle_io_provide_buffers.svg", "局部图：io_provide_buffers alloc/free 显式依赖候选"),
        ("03_state_io_timeout_off.svg", "局部图：io_timeout.off 写后读状态候选"),
        ("04_identity_io_kiocb.svg", "局部图：io_kiocb 弱对象身份候选"),
    ]
    body = [
        "<!doctype html>",
        '<html lang="zh-CN"><meta charset="utf-8">',
        "<title>ImplicitFuzz Phase 2B Visuals</title>",
        "<style>body{font-family:Inter,Arial,sans-serif;margin:32px;background:#f8fafc;color:#0f172a} .card{margin:28px 0;padding:18px;background:white;border:1px solid #e2e8f0;border-radius:10px;box-shadow:0 1px 2px #0001} img{max-width:100%;border:1px solid #e2e8f0;border-radius:8px} code{background:#e2e8f0;padding:2px 5px;border-radius:4px}</style>",
        "<h1>ImplicitFuzz Phase 2B 可视化</h1>",
        "<p>这些图用于组会讲解当前进度。注意：candidate edge 是候选证据，不是最终依赖结论。</p>",
    ]
    for filename, title in cards:
        body.append(f'<section class="card"><h2>{esc(title)}</h2><p><code>{esc(filename)}</code></p><img src="{esc(filename)}" alt="{esc(title)}"></section>')
    body.append("</html>")
    (out / "index.html").write_text("\n".join(body), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(args.db))
    try:
        render_summary(conn, args.out)
        render_pipeline(args.out)
        render_lifecycle(conn, args.out)
        render_state_timeout(conn, args.out)
        render_identity_iokiocb(conn, args.out)
        render_index(args.out)
    finally:
        conn.close()
    print(f"wrote visuals to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
