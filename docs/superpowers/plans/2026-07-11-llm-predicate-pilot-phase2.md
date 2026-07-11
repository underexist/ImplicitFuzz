# LLM Predicate Pilot Phase 2 (cold-judge eval) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一个小样本、可审计的 pilot,量 LLM 在受限切片+账约束下**冷启动**产出状态门控谓词的可行性——封闭谓词 schema + 输入包构建 + 三道校验(复用 Phase 1 对账)+ term 级评分,判读用冷 subagent。

**Architecture:** 确定性 Python 组件(Tasks 1–4,TDD 本地可跑):谓词 schema、bundle 构建、三道校验、评分;实验(Task 5)由编排者用 Agent 工具对每门控派**冷 subagent**(仅给 bundle),收判读→校验→评分→findings。ground-truth 由编排者起草、你复核,与 harness 分离、永不进 bundle。

**Tech Stack:** Python ≥3.8 + sqlite3 + jsonschema + pytest;冷判读用 Agent 工具(general-purpose subagent)。

## Global Constraints

- 判读员**冷启动**:每门控派全新 subagent,prompt 只含 bundle + schema + 指令;**不含 ground-truth、不含本对话历史、不含 reconciliation 结论**。
- ground-truth 存 `pilot/labels/`,**与 harness 分离,永不进 bundle**。
- pilot 谓词 schema 是 **pilot-local**(`src/implicitfuzz/pilot/predicate_schema.json`),**不入 `extraction/schema/facts/`、不动 schema-bound `gate_seed_fact`**;不改 `schema_version`。
- 复用 Phase 1:`implicitfuzz.reconcile.layout.LayoutIndex`、`implicitfuzz.reconcile.field_existence.reconcile_field(conn, layout, struct, member) -> FieldMatch{status,access_fact_id,offset}`。
- confidence 枚举:`high|medium_high|medium|medium_low|low`。
- 不接外部 API;不做批量/20+ 大评测;不落 gate_seed_fact;不碰研究内容二。
- 服务器工作流:本地改 → scp → 服务器 `pytest`/`git commit -F`;实验(切片读 6.1 源、派 subagent)在能访问 `/home/xujunru/linux-6.1` 处跑。
- 收口:`run_phase1_regression.sh` ALL PASS;`pytest` 全绿。
- 本地快跑单测:`PYTHONPATH=src <venv>/python -m pytest <file> -o addopts="" -q`(Tasks 1–4 纯 Python,需 jsonschema)。

参考 spec:`docs/superpowers/specs/2026-07-11-llm-predicate-pilot-phase2-design.md`。

---

### Task 1: 封闭谓词 schema + schema 校验(validate 的第 2 道)

**Files:**
- Create: `src/implicitfuzz/pilot/__init__.py`(空)
- Create: `src/implicitfuzz/pilot/predicate_schema.json`
- Create: `src/implicitfuzz/pilot/validate.py`(先只放 `validate_schema`)
- Test: `tests/test_pilot_validate.py`

**Interfaces:**
- Produces: `validate_schema(predicate: dict) -> bool`;`PREDICATE_SCHEMA_PATH: Path`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pilot_validate.py
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_pilot_validate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'implicitfuzz.pilot'`

- [ ] **Step 3: 实现**

`src/implicitfuzz/pilot/__init__.py`: 空。

`src/implicitfuzz/pilot/predicate_schema.json`:
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/pilot/predicate_schema.json",
  "type": "object",
  "properties": {
    "target_gate": {
      "type": "object",
      "properties": {
        "bc_unit": { "type": "string" },
        "function": { "type": "string" },
        "branch_instruction_id": { "type": "string" }
      },
      "required": ["function"]
    },
    "terms": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "class": { "enum": ["premise", "activation", "param_align"] },
          "object": { "type": "string" },
          "field_ref": { "type": "string" },
          "relation": { "type": "string" },
          "align_target": { "type": "string" },
          "source": { "enum": ["slice", "slice+ledger", "inferred"] },
          "confidence": { "enum": ["high", "medium_high", "medium", "medium_low", "low"] },
          "uncertain": { "type": "boolean" }
        },
        "required": ["class", "object", "field_ref", "relation", "source", "confidence", "uncertain"]
      }
    },
    "abstain": { "type": "boolean" },
    "alt_candidates": { "type": "array" }
  },
  "required": ["target_gate", "terms", "abstain"]
}
```

`src/implicitfuzz/pilot/validate.py`:
```python
"""Three-gate validation of a cold-judge predicate (schema gate here; the
field-existence and synthesizability gates are added in a later task)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

PREDICATE_SCHEMA_PATH = Path(__file__).parent / "predicate_schema.json"


def validate_schema(predicate: dict) -> bool:
    schema = json.loads(PREDICATE_SCHEMA_PATH.read_text())
    try:
        jsonschema.validate(predicate, schema)
        return True
    except jsonschema.ValidationError:
        return False
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_pilot_validate.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/pilot/__init__.py src/implicitfuzz/pilot/predicate_schema.json src/implicitfuzz/pilot/validate.py tests/test_pilot_validate.py
git commit -F <msg>   # "feat(pilot): closed predicate schema + schema-gate validation"
```

---

### Task 2: 输入包构建器 `context.py`(红线:不含答案)

**Files:**
- Create: `src/implicitfuzz/pilot/context.py`
- Test: `tests/test_pilot_context.py`

**Interfaces:**
- Consumes: `access_fact` 表(Phase 1 schema)。
- Produces:
  - `read_source_window(source_root: str, file_line: str, radius: int = 15) -> str`
  - `build_bundle(conn, source_root: str, gate: dict, radius: int = 15) -> dict`
    - `gate = {"target_gate": {...}, "slice_locations": [{"file_line": "io_uring/rw.c:1856", "label": "gate"}...], "ledger_functions": ["io_file_get_fixed", ...], "field_clues": ["fd", "ctx->nr_user_files"]}`
    - bundle 含 `target_gate / code_slices / ledger / field_clues / instructions / predicate_schema`;**不含** `ground_truth`/`reconciliation`/任何答案键。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pilot_context.py
import sqlite3

from implicitfuzz.pilot.context import read_source_window, build_bundle

_DDL = """
CREATE TABLE access_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  function TEXT, semantic_op TEXT, access_path_symbolic TEXT,
  numeric_kind TEXT, access_path_numeric TEXT
);
"""


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    conn.execute("INSERT INTO access_fact (function,semantic_op,access_path_symbolic,numeric_kind,access_path_numeric) "
                 "VALUES ('io_file_get_fixed','read','io_ring_ctx.file_data','gep_offsets','[816]')")
    conn.execute("INSERT INTO access_fact (function,semantic_op,access_path_symbolic,numeric_kind,access_path_numeric) "
                 "VALUES ('other_fn','read','x.y','gep_offsets','[0]')")
    return conn


def test_read_source_window(tmp_path):
    f = tmp_path / "rw.c"
    f.write_text("\n".join("line%d" % i for i in range(1, 21)) + "\n")
    win = read_source_window(str(tmp_path), "rw.c:10", radius=2)
    assert "line10" in win and "line8" in win and "line12" in win
    assert "line5" not in win


def test_build_bundle_has_slices_ledger_no_answers(tmp_path):
    (tmp_path / "io_uring").mkdir()
    (tmp_path / "io_uring" / "rw.c").write_text("\n".join("l%d" % i for i in range(1, 40)) + "\n")
    gate = {
        "target_gate": {"bc_unit": "rw.bc", "function": "io_file_get_fixed", "branch_instruction_id": "b1"},
        "slice_locations": [{"file_line": "io_uring/rw.c:20", "label": "gate"}],
        "ledger_functions": ["io_file_get_fixed"],
        "field_clues": ["fd", "ctx->nr_user_files"],
    }
    bundle = build_bundle(_db(), str(tmp_path), gate, radius=3)
    assert bundle["target_gate"]["function"] == "io_file_get_fixed"
    assert any("l20" in s["text"] for s in bundle["code_slices"])
    # ledger scoped to the gate's functions only
    funcs = {row["function"] for row in bundle["ledger"]}
    assert funcs == {"io_file_get_fixed"}
    # RED LINE: no answer leakage
    for forbidden in ("ground_truth", "reconciliation", "labels", "answer"):
        assert forbidden not in bundle
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_pilot_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'implicitfuzz.pilot.context'`

- [ ] **Step 3: 实现**

```python
# src/implicitfuzz/pilot/context.py
"""Build the cold-judge input bundle for a gate: code slice + ledger subset +
gate id + field clues + task instructions + predicate schema. RED LINE: the
bundle must never contain ground-truth, reconciliation results, or any answer.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from implicitfuzz.pilot.validate import PREDICATE_SCHEMA_PATH

_INSTRUCTIONS = (
    "You are a constrained judge. Using ONLY the code slice and the access-fact "
    "ledger below, infer the state-gate predicate for reaching the target branch. "
    "Output JSON conforming to predicate_schema. Classify each term as premise / "
    "activation / param_align; tag its object; put the referenced field in "
    "field_ref as '<struct>.<member>'. Do NOT reference fields absent from the "
    "slice/ledger. You may abstain or give alt_candidates if unsure."
)


def read_source_window(source_root: str, file_line: str, radius: int = 15) -> str:
    path_str, _, line_str = file_line.rpartition(":")
    line = int(line_str)
    src = Path(source_root) / path_str
    lines = src.read_text(errors="replace").splitlines()
    lo = max(0, line - 1 - radius)
    hi = min(len(lines), line + radius)
    return "\n".join(f"{i + 1}: {lines[i]}" for i in range(lo, hi))


def build_bundle(conn: sqlite3.Connection, source_root: str, gate: dict,
                 radius: int = 15) -> dict:
    conn.row_factory = sqlite3.Row
    code_slices = [
        {"label": loc.get("label", ""), "file_line": loc["file_line"],
         "text": read_source_window(source_root, loc["file_line"], radius)}
        for loc in gate.get("slice_locations", [])
    ]
    ledger = []
    for fn in gate.get("ledger_functions", []):
        for row in conn.execute(
            "SELECT function, semantic_op, access_path_symbolic, numeric_kind, "
            "access_path_numeric FROM access_fact WHERE function = ? ORDER BY id",
            (fn,),
        ).fetchall():
            ledger.append(dict(row))
    return {
        "target_gate": gate["target_gate"],
        "field_clues": gate.get("field_clues", []),
        "code_slices": code_slices,
        "ledger": ledger,
        "instructions": _INSTRUCTIONS,
        "predicate_schema": json.loads(PREDICATE_SCHEMA_PATH.read_text()),
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_pilot_context.py -v`
Expected: PASS(2 passed)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/pilot/context.py tests/test_pilot_context.py
git commit -F <msg>   # "feat(pilot): cold-judge input bundle builder (no-answer red line)"
```

---

### Task 3: 三道校验补全 `validate.py`(字段存在性 + 可合成性)

**Files:**
- Modify: `src/implicitfuzz/pilot/validate.py`
- Test: `tests/test_pilot_validate.py`(追加)

**Interfaces:**
- Consumes: `reconcile_field`、`LayoutIndex`(Phase 1)。
- Produces:
  - `validate_field_existence(conn, predicate: dict) -> dict`(`{"passed": bool, "per_term": [{"field_ref","status","access_fact_id"}]}`)
  - `validate_synthesizability(predicate: dict) -> list[dict]`(`[{"class","synthesizable": bool}]`)

- [ ] **Step 1: 写失败测试(追加)**

```python
# tests/test_pilot_validate.py — 追加
import sqlite3

from implicitfuzz.pilot.validate import (
    validate_field_existence, validate_synthesizability,
)

_ACC_DDL = """
CREATE TABLE struct_layout_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  struct_name TEXT, member_name TEXT, byte_offset INTEGER, member_type TEXT
);
CREATE TABLE access_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  semantic_op TEXT, access_path_symbolic TEXT, numeric_kind TEXT, access_path_numeric TEXT
);
"""


def _acc_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_ACC_DDL)
    conn.execute("INSERT INTO struct_layout_fact (struct_name,member_name,byte_offset,member_type) "
                 "VALUES ('io_ring_ctx','nr_user_files',160,'unsigned int')")
    conn.execute("INSERT INTO access_fact (semantic_op,access_path_symbolic,numeric_kind,access_path_numeric) "
                 "VALUES ('write',NULL,'gep_offsets','[160]')")
    return conn


def _term(cls, field_ref, relation="", align_target=""):
    return {"class": cls, "object": "io_ring_ctx", "field_ref": field_ref,
            "relation": relation, "align_target": align_target,
            "source": "slice", "confidence": "high", "uncertain": False}


def test_field_existence_confirms_numeric_and_flags_hallucination():
    conn = _acc_db()
    pred = {"terms": [_term("activation", "io_ring_ctx.nr_user_files"),
                      _term("premise", "io_ring_ctx.bogus_field")], "abstain": False}
    res = validate_field_existence(conn, pred)
    assert res["passed"] is False  # one term unconfirmed
    by = {r["field_ref"]: r["status"] for r in res["per_term"]}
    assert by["io_ring_ctx.nr_user_files"] == "confirmed_numeric"
    assert by["io_ring_ctx.bogus_field"] == "unconfirmed"


def test_synthesizability_heuristic():
    pred = {"terms": [
        _term("param_align", "io_ring_ctx.nr_user_files", align_target="sqe->fd"),
        _term("param_align", "io_ring_ctx.nr_user_files", align_target="nothing settable"),
        _term("premise", "io_ring_ctx.file_data"),
    ], "abstain": False}
    res = validate_synthesizability(pred)
    assert res[0]["synthesizable"] is True   # sqe->fd
    assert res[1]["synthesizable"] is False
    assert res[2]["synthesizable"] is True   # premise always synthesizable
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_pilot_validate.py -k "field_existence or synthesizability" -v`
Expected: FAIL — `ImportError: cannot import name 'validate_field_existence'`

- [ ] **Step 3: 实现(在 validate.py 追加)**

```python
import sqlite3

from implicitfuzz.reconcile.layout import LayoutIndex
from implicitfuzz.reconcile.field_existence import reconcile_field

_SETTABLE_PARAMS = ("sqe->fd", "sqe->buf_index", "buf_index", "->fd")


def _split_field_ref(field_ref: str):
    if "." not in field_ref:
        return None
    struct, member = field_ref.rsplit(".", 1)
    if not struct or not member:
        return None
    return struct, member


def validate_field_existence(conn: sqlite3.Connection, predicate: dict) -> dict:
    layout = LayoutIndex(conn)
    per_term = []
    for t in predicate.get("terms", []):
        parts = _split_field_ref(t.get("field_ref", ""))
        if parts is None:
            per_term.append({"field_ref": t.get("field_ref"), "status": "unconfirmed",
                             "access_fact_id": None})
            continue
        m = reconcile_field(conn, layout, parts[0], parts[1])
        per_term.append({"field_ref": t["field_ref"], "status": m.status,
                         "access_fact_id": m.access_fact_id})
    if per_term:
        passed = all(r["status"] != "unconfirmed" for r in per_term)
    else:
        passed = bool(predicate.get("abstain", False))
    return {"passed": passed, "per_term": per_term}


def validate_synthesizability(predicate: dict) -> list[dict]:
    out = []
    for t in predicate.get("terms", []):
        if t.get("class") in ("activation", "param_align"):
            hay = (t.get("relation", "") + " " + t.get("align_target", "")).lower()
            ok = any(p in hay for p in _SETTABLE_PARAMS)
        else:
            ok = True  # premise: satisfied by inserting the prior call
        out.append({"class": t.get("class"), "synthesizable": ok})
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_pilot_validate.py -v`
Expected: PASS(原 3 + 新 2 = 5 passed)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/pilot/validate.py tests/test_pilot_validate.py
git commit -F <msg>   # "feat(pilot): field-existence (reuse Phase 1) + synthesizability gates"
```

---

### Task 4: 评分与 audit 记录 `eval.py`

**Files:**
- Create: `src/implicitfuzz/pilot/eval.py`
- Test: `tests/test_pilot_eval.py`

**Interfaces:**
- Produces:
  - `score_terms(judge_terms: list[dict], truth_terms: list[dict]) -> dict`(`{"precision","recall","matched","n_judge","n_truth"}`,按 `(class, field_ref, align_target)` 键)
  - `build_audit_record(gate_id, bundle, judge_output, validation, score, human_score=None, rationale="") -> dict`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pilot_eval.py
from implicitfuzz.pilot.eval import score_terms, build_audit_record


def _t(cls, fr, align=""):
    return {"class": cls, "field_ref": fr, "align_target": align}


def test_score_terms_precision_recall():
    judge = [_t("activation", "io_ring_ctx.nr_user_files"),
             _t("param_align", "io_ring_ctx.nr_user_files", "sqe->fd"),
             _t("premise", "io_ring_ctx.bogus")]           # wrong
    truth = [_t("activation", "io_ring_ctx.nr_user_files"),
             _t("param_align", "io_ring_ctx.nr_user_files", "sqe->fd"),
             _t("premise", "io_ring_ctx.file_data")]        # judge missed
    s = score_terms(judge, truth)
    assert s["matched"] == 2
    assert abs(s["precision"] - 2 / 3) < 1e-9
    assert abs(s["recall"] - 2 / 3) < 1e-9


def test_build_audit_record_shape():
    rec = build_audit_record(
        gate_id="read_fixed_file",
        bundle={"target_gate": {"function": "io_file_get_fixed"}},
        judge_output={"terms": []},
        validation={"field_existence": {"passed": True}},
        score={"precision": 1.0, "recall": 1.0},
        human_score="correct", rationale="param_align aligns with register nr_args",
    )
    for k in ("gate_id", "bundle", "judge_output", "validation", "score",
              "human_score", "rationale"):
        assert k in rec
    assert rec["gate_id"] == "read_fixed_file"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_pilot_eval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'implicitfuzz.pilot.eval'`

- [ ] **Step 3: 实现**

```python
# src/implicitfuzz/pilot/eval.py
"""Term-level scoring of a cold-judge predicate against ground-truth, plus the
auditable per-gate record. Ground-truth is supplied separately by the caller
and MUST never be part of the input bundle."""

from __future__ import annotations


def _term_key(t: dict):
    return (t.get("class"), t.get("field_ref"), (t.get("align_target") or "").strip())


def score_terms(judge_terms: list[dict], truth_terms: list[dict]) -> dict:
    jk = {_term_key(t) for t in judge_terms}
    tk = {_term_key(t) for t in truth_terms}
    matched = jk & tk
    precision = len(matched) / len(jk) if jk else 0.0
    recall = len(matched) / len(tk) if tk else 0.0
    return {"precision": precision, "recall": recall, "matched": len(matched),
            "n_judge": len(jk), "n_truth": len(tk)}


def build_audit_record(gate_id, bundle, judge_output, validation, score,
                       human_score=None, rationale=""):
    return {
        "gate_id": gate_id,
        "bundle": bundle,
        "judge_output": judge_output,
        "validation": validation,
        "score": score,
        "human_score": human_score,
        "rationale": rationale,
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_pilot_eval.py -v`
Expected: PASS(2 passed)

- [ ] **Step 5: 全套 + 提交**

```bash
python3 -m pytest tests/    # 全绿(Phase1/2 累计)
git add src/implicitfuzz/pilot/eval.py tests/test_pilot_eval.py
git commit -F <msg>   # "feat(pilot): term-level scoring + auditable per-gate record"
```

---

### Task 5: 冷判读实验 + ground-truth + findings（过程,非 TDD）

**Files:**
- Create: `src/implicitfuzz/pilot/labels/*.json`（ground-truth,编排者起草→用户复核）
- Create: `docs/llm-predicate-pilot-runbook.md`、`docs/llm-predicate-pilot-findings.md`
- 产物目录:`pilot/bundles/`、`pilot/runs/`、`pilot/audit/`（实验落盘,可选择性提交）

**过程(在能访问 `/home/xujunru/linux-6.1` + 已 ingest 的 DB 处执行):**

- [ ] **Step 1: 确定门控集与切片定位**

3 核心门控(spec §4):READ_FIXED fixed-file(`io_file_get_fixed`,`io_uring/io_uring.c:1856`,写侧 `io_sqe_files_register`)、fixed-buffer(`io_import_fixed`/边界 `io_uring/rsrc.c`,写侧 register)、`io_kiocb.flags` 门控。为每门控写 `gate` 定义(`slice_locations` / `ledger_functions` / `field_clues`),存 `pilot/gates/<id>.json`。

- [ ] **Step 2: 起草 ground-truth,交用户复核**

编排者(了解正确答案)按谓词 schema 为每门控写"正确"谓词到 `pilot/labels/<id>.json`(尤其 READ_FIXED / fixed-buffer 的 param_align:`sqe->fd`/`sqe->buf_index` 与前序 register 的 `nr_args` 对齐)。**提交前请用户复核关键样本**;labels 目录**不进 bundle**。

- [ ] **Step 3: 构建 bundle**

```bash
# 服务器,已 ingest rsrc+rw(+io_uring)facts 与 rsrc_layout 到一个 DB
PYTHONPATH=src python3 -c "
import json, sqlite3
from implicitfuzz.pilot.context import build_bundle
conn=sqlite3.connect('/tmp/pilot.db')
for gid in ['read_fixed_file','fixed_buffer','kiocb_flags']:
    gate=json.load(open('src/implicitfuzz/pilot/gates/%s.json'%gid))
    b=build_bundle(conn,'/home/xujunru/linux-6.1',gate)
    json.dump(b, open('pilot/bundles/%s.json'%gid,'w'), indent=2)
    assert not any(k in b for k in ('ground_truth','reconciliation','labels'))
print('bundles built, no-answer red line OK')
"
```

- [ ] **Step 4: 冷判读(每门控派一个 subagent)**

编排者用 **Agent 工具**对每门控派一个 **general-purpose subagent**,prompt = `pilot/bundles/<id>.json` 的内容 + "按 predicate_schema 输出 JSON"。subagent 冷启动(无本对话历史、无 labels)。**原样保存**返回到 `pilot/runs/<id>.json`。

- [ ] **Step 5: 校验 + 评分 + audit**

```bash
PYTHONPATH=src python3 -c "
import json, sqlite3
from implicitfuzz.pilot.validate import validate_schema, validate_field_existence, validate_synthesizability
from implicitfuzz.pilot.eval import score_terms, build_audit_record
conn=sqlite3.connect('/tmp/pilot.db')
for gid in ['read_fixed_file','fixed_buffer','kiocb_flags']:
    bundle=json.load(open('pilot/bundles/%s.json'%gid))
    judge=json.load(open('pilot/runs/%s.json'%gid))
    truth=json.load(open('src/implicitfuzz/pilot/labels/%s.json'%gid))
    val={'schema':validate_schema(judge),
         'field_existence':validate_field_existence(conn,judge),
         'synthesizability':validate_synthesizability(judge)}
    sc=score_terms(judge.get('terms',[]), truth.get('terms',[]))
    rec=build_audit_record(gid,bundle,judge,val,sc)
    json.dump(rec, open('pilot/audit/%s.json'%gid,'w'), indent=2)
    print(gid, 'schema',val['schema'], 'fx',val['field_existence']['passed'], 'P',round(sc['precision'],2),'R',round(sc['recall'],2))
"
```

- [ ] **Step 6: 人工终审 + findings**

编排者对每门控补主观正确性评分 + 理由入 audit;写 `docs/llm-predicate-pilot-findings.md`:逐门控 judge 谓词对不对、过几道校验、`unconfirmed` 拦截(含负例)、term P/R;**定性**判 LLM 主力/辅助。**提交前请用户复核 findings**。

- [ ] **Step 7: 回归 + 提交**

```bash
extraction/scripts/run_phase1_regression.sh   # ALL PASS
python3 -m pytest tests/                        # 全绿
git add src/implicitfuzz/pilot/gates src/implicitfuzz/pilot/labels docs/llm-predicate-pilot-runbook.md docs/llm-predicate-pilot-findings.md
git commit -F <msg>   # "feat(pilot): cold-judge experiment on 3 gates + findings"
```

---

## Self-Review

- **Spec 覆盖:** 冷判读(§1)→ Task 5 Step 4 + Global Constraints;D 输入包(§3.D)→ Task 2;E 谓词 schema(§3.E)→ Task 1;三道校验(§3.validate)→ Task 1(schema)+ Task 3(字段存在性复用 Phase 1 + 可合成性);G 评分/audit(§3.G)→ Task 4 + Task 5;门控集(§4)→ Task 5 Step 1;ground-truth 分离(§1/§3.G)→ Task 5 Step 2 + context 红线断言(Task 2)。✅
- **占位符:** 无 TBD;每步含完整代码/命令;Task 5 是显式过程(非 TDD),各步有具体命令与产物。`<msg>` 指 commit 信息文件,内容已给。
- **类型一致:** `validate_schema`/`validate_field_existence`/`validate_synthesizability`/`build_bundle`/`read_source_window`/`score_terms`/`build_audit_record` 跨 Task 一致;复用 `reconcile_field(conn, layout, struct, member)` 与 `LayoutIndex(conn)` 签名与 Phase 1 一致;term 键 `(class, field_ref, align_target)` 评分与 schema 字段一致。✅
- **已知开放点:** 可合成性启发(`_SETTABLE_PARAMS`)是粗启发,Task 5 findings 记录其误判;门控 4–5 可选样本实现时与用户定。
