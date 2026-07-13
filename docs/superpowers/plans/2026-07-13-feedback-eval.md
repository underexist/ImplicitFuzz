# 反馈闭环·小规模评估(冻结 MVP)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 量化置信度反馈闭环 MVP 在**冻结的 10 个真实 Phase 2 候选**上的忠实覆盖范围与反馈状态分布(coverage / 两层指标 / rerank 变化),不扩通用合成、不做统计校准。

**Architecture:** 新增纯 Python `pilot_adapter.py`(冷判读 gate → feedback 候选,完整签名严格可执行判定 + min-activation 静态档)+ `eval_report.py`(两层指标/分布/rerank 汇总,executor 注入以便单测);复用 `feedback/{update,assemble,loop}.py` 与 execverify harness;`update.py:rerank` 加确定性 tie-break。冻结快照记溯源,真实跑仅 2 个可执行者 boot。

**Tech Stack:** Python ≥3.8 + pytest;真实执行复用 `execverify/`(server, qemu-kvm/KCOV, `bzImage@b364f28d1fcf`)。

## Global Constraints

- **完整签名可执行判定**:`(operation, target_function, field_relation, template_family)` 四者同时匹配 `EXECUTABLE_GATES` 才可执行;仅函数名不足(`io_prep_rw` 同服务 READ_FIXED/WRITE_FIXED)。
- **family 来自 gate def `field_clues`**:含 `nr_user_files`→fixed_file;含 `nr_user_bufs`→fixed_buffer;否则 `no_family_template`。
- **static tier = min over activation-class terms**,映射 `high→high, medium_high/medium→medium, medium_low/low→low`(合取式:整体不高于最弱前提)。**无 activation term → 抛 `AdapterError`,不得静默给默认档。**
- **unsupported_reason**:family∈{fixed_file,fixed_buffer} 但无 EXECUTABLE_GATES 命中 → `op_not_covered`;family 为 None → `no_family_template`。
- **仅可执行候选** `gate_family` 设为模板家族(让 assemble 出 prog);不可执行者 `gate_family=None`(assemble 返回 None → loop 判 unsupported);`field_relation_family` 始终记真实族供报告。
- **两层指标**:全部真实候选(10)verified/unsupported;严格支持且执行(2)verified;第二层**必注明 frozen replay,非 held-out 泛化**。严禁只报第二层。
- **rerank 仅证"执行证据成为新排序键"**:全部 static-high → 2 升顶、8 保持;**不主张整体排序质量改善**;同档 tie-break 用 `candidate_id` 升序(确定性)。
- **冻结快照记溯源**:`source_commit / gate_ids / adapter_version / schema_version / generated_hash / generated_date`;**不因评测结果回改模板**。
- **控制项引用 24c33ce**:ff_mutant→contradicted、neg_* abstention 控制来自 `docs/confidence-feedback-findings.md`@24c33ce,**非本次 run**,报告显式标注。
- **边界(非目标)**:两家族、单内核单子系统(linux-6.1 io_uring)、离散单调更新;非概率校准/通用合成/per-op 模板/held-out。
- 服务器工作流:本地改 → scp/tar 同步 → 服务器 pytest;三边同步(见 [[feedback-three-way-sync]])。本地快跑 `PYTHONPATH=src <venv>/python -m pytest <f> -o addopts="" -q`。收口:`extraction/scripts/run_phase1_regression.sh` ALL PASS;`pytest` 全绿。

参考 spec:`docs/superpowers/specs/2026-07-13-feedback-eval-design.md`。10 个真实候选 gate_ids:`read_fixed_file, fixed_buffer, cancel_fixed_file, msg_ring_fixed_file, filetable_slot, net_fixed_buffer, poll_polled, poll_double, link_timeout, kiocb_flags`。

---

### Task 1: `pilot_adapter.py` —— gate → feedback 候选(严格可执行 + min-activation 档)

**Files:** Create `src/implicitfuzz/feedback/pilot_adapter.py`;Test `tests/test_pilot_adapter.py`

**Interfaces:**
- Produces:
  - `ADAPTER_VERSION = "1.0"`、`PREDICATE_SCHEMA_VERSION = "1.0"`
  - `EXECUTABLE_GATES: dict[tuple[str,str,str,str], dict]`、`TARGET_FN_DEFAULT_OPS: dict[str,set]`
  - `class AdapterError(ValueError)`
  - `adapt_gate(gate_id: str, gate_def: dict, run: dict) -> dict`
    - 返回候选 dict:`candidate_id, candidate_origin="replayed", static_confidence, param_align={"relation":"<"}, target_function, field_relation_family, field_relation, gate_family, operation, signal_function, executable(bool), unsupported_reason`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pilot_adapter.py
import json, pytest
from implicitfuzz.feedback.pilot_adapter import adapt_gate, AdapterError

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
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONPATH=src python3 -m pytest tests/test_pilot_adapter.py -o addopts="" -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'implicitfuzz.feedback.pilot_adapter'`

- [ ] **Step 3: 实现**

`src/implicitfuzz/feedback/pilot_adapter.py`:
```python
"""Adapt a pilot cold-judge gate (gate def + runs_formal predicate) into a
confidence-feedback candidate. Strict-faithful executability: executable only
if the full signature (operation, target_function, field_relation,
template_family) matches EXECUTABLE_GATES -- a function name alone is
insufficient (io_prep_rw serves READ_FIXED and WRITE_FIXED). Static tier = min
confidence over activation-class terms (conjunctive predicate: no stronger than
its weakest premise); a gate with no activation term is an AdapterError, never a
silent default."""

from __future__ import annotations
import re

ADAPTER_VERSION = "1.0"
PREDICATE_SCHEMA_VERSION = "1.0"  # pilot predicate_schema.json carries no version field

# Full-signature template registry. Key = (operation, target_function,
# field_relation, template_family); value = execution metadata.
EXECUTABLE_GATES = {
    ("READ", "io_file_get_fixed", "fd < nr_user_files", "fixed_file"):
        {"signal_function": "io_read"},
    ("READ_FIXED", "io_prep_rw", "buf_index < nr_user_bufs", "fixed_buffer"):
        {"signal_function": "io_prep_rw"},
}

# Op(s) a target_function is driven by when the predicate carries no explicit
# `opcode == ...` activation term (op-agnostic fixed-resource resolver).
TARGET_FN_DEFAULT_OPS = {"io_file_get_fixed": {"READ"}}

_CONF_MAP = {"high": "high", "medium_high": "medium", "medium": "medium",
             "medium_low": "low", "low": "low"}
_RANK = {"high": 0, "medium": 1, "low": 2}
_BOUND_RE = re.compile(r"([a-z_]+)\s*<\s*(?:ctx->)?(nr_user_files|nr_user_bufs)")


class AdapterError(ValueError):
    pass


def _canon_relation(rel: str):
    m = _BOUND_RE.search(rel)
    return "%s < %s" % (m.group(1), m.group(2)) if m else None


def _family_from_clues(clues):
    joined = " ".join(clues)
    if "nr_user_files" in joined:
        return "fixed_file"
    if "nr_user_bufs" in joined:
        return "fixed_buffer"
    return None


def _activation_bound(terms):
    for t in terms:
        if t["class"] == "activation":
            c = _canon_relation(t.get("relation", ""))
            if c:
                return c
    return None


def _opcode_ops(terms):
    for t in terms:
        if t["class"] == "activation" and "opcode" in t.get("field_ref", "") \
                and "opcode ==" in t.get("relation", ""):
            ops = set(re.findall(r"[A-Z_]*READ[A-Z_]*|[A-Z_]*WRITE[A-Z_]*", t["relation"]))
            return {o for o in ops if o} or None
    return None


def _static_tier(terms):
    acts = [t for t in terms if t["class"] == "activation"]
    if not acts:
        raise AdapterError("gate has no activation term; cannot derive static tier")
    mapped = [_CONF_MAP.get(t.get("confidence", ""), "low") for t in acts]
    return max(mapped, key=lambda c: _RANK[c])  # worst = min confidence


def adapt_gate(gate_id: str, gate_def: dict, run: dict) -> dict:
    terms = run.get("terms", [])
    target_fn = run.get("target_gate", {}).get("function", "")
    static = _static_tier(terms)  # raises AdapterError if no activation term
    family = _family_from_clues(gate_def.get("field_clues", []))
    bound = _activation_bound(terms)
    base = {
        "candidate_id": gate_id, "candidate_origin": "replayed",
        "static_confidence": static, "param_align": {"relation": "<"},
        "target_function": target_fn, "field_relation_family": family,
        "field_relation": bound,
    }
    if family is None:
        base.update(gate_family=None, operation=None, signal_function=None,
                    executable=False, unsupported_reason="no_family_template")
        return base
    ops = _opcode_ops(terms) or TARGET_FN_DEFAULT_OPS.get(target_fn) or set()
    match = None
    if bound:
        for op in ops:
            key = (op, target_fn, bound, family)
            if key in EXECUTABLE_GATES:
                match = (op, EXECUTABLE_GATES[key])
                break
    if match:
        op, meta = match
        base.update(gate_family=family, operation=op,
                    signal_function=meta["signal_function"],
                    executable=True, unsupported_reason=None)
    else:
        base.update(gate_family=None, operation=None, signal_function=None,
                    executable=False, unsupported_reason="op_not_covered")
    return base
```

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONPATH=src python3 -m pytest tests/test_pilot_adapter.py -o addopts="" -q`
Expected: PASS(6 passed)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/feedback/pilot_adapter.py tests/test_pilot_adapter.py
git commit -F <msg>   # "feat(feedback): pilot->candidate adapter (full-signature executability, min-activation tier)"
```

---

### Task 2: `update.py:rerank` 确定性 tie-break

**Files:** Modify `src/implicitfuzz/feedback/update.py`(`rerank`);Test `tests/test_feedback_update.py`(追加)

**Interfaces:**
- Modifies: `rerank(view)` —— 主键 final 档降序、次键 `candidate_id` 升序(确定性);既有档序单测保持通过。

- [ ] **Step 1: 追加失败测试**

```python
# append to tests/test_feedback_update.py
from implicitfuzz.feedback.update import rerank  # (already imported at top; safe)


def test_rerank_tiebreak_by_candidate_id():
    view = [{"final_confidence": "high", "candidate_id": "zeta"},
            {"final_confidence": "high", "candidate_id": "alpha"},
            {"final_confidence": "execution_verified", "candidate_id": "mid"}]
    out = rerank(view)
    assert [r["candidate_id"] for r in out] == ["mid", "alpha", "zeta"]
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONPATH=src python3 -m pytest tests/test_feedback_update.py::test_rerank_tiebreak_by_candidate_id -o addopts="" -q`
Expected: FAIL — 现有 `rerank` 不排 `candidate_id`,顺序为 `mid, zeta, alpha`

- [ ] **Step 3: 实现**

`src/implicitfuzz/feedback/update.py` 替换 `rerank`:
```python
def rerank(view: list[dict]) -> list[dict]:
    # primary: final tier descending; secondary: candidate_id ascending (deterministic)
    return sorted(view, key=lambda r: (-TIER_ORDER.index(r["final_confidence"]),
                                       r.get("candidate_id", "")))
```

- [ ] **Step 4: 运行确认通过(含既有测试)**

Run: `PYTHONPATH=src python3 -m pytest tests/test_feedback_update.py tests/test_feedback_loop.py -o addopts="" -q`
Expected: PASS(既有 4 + 新 1;loop 测试仍绿——其档全互异,tie-break 不影响)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/feedback/update.py tests/test_feedback_update.py
git commit -F <msg>   # "feat(feedback): deterministic rerank tie-break by candidate_id"
```

---

### Task 3: 冻结快照构建 + 落盘

**Files:** Modify `src/implicitfuzz/feedback/pilot_adapter.py`(加 `build_snapshot`);Create `feedback/eval/frozen_candidates.json`;Test `tests/test_pilot_adapter.py`(追加)

**Interfaces:**
- Consumes: `adapt_gate`。
- Produces:
  - `build_snapshot(gate_ids, gates_dir, runs_dir, source_commit) -> dict`
    - 返回 `{source_commit, gate_ids(sorted), adapter_version, schema_version, candidates[], generated_hash}`;`generated_hash` = candidates 规范化 sha256(不含 date/commit,保证可复现)。

- [ ] **Step 1: 追加失败测试**

```python
# append to tests/test_pilot_adapter.py
import os
from implicitfuzz.feedback.pilot_adapter import build_snapshot, ADAPTER_VERSION

GATES_DIR = "src/implicitfuzz/pilot/gates"
RUNS_DIR = "pilot/runs_formal"


def test_build_snapshot_provenance_and_determinism():
    ids = ["read_fixed_file", "fixed_buffer", "cancel_fixed_file", "kiocb_flags"]
    snap = build_snapshot(ids, GATES_DIR, RUNS_DIR, source_commit="deadbee")
    assert snap["source_commit"] == "deadbee"
    assert snap["gate_ids"] == sorted(ids)
    assert snap["adapter_version"] == ADAPTER_VERSION
    assert "schema_version" in snap and len(snap["generated_hash"]) == 64
    # deterministic: same inputs -> same hash
    snap2 = build_snapshot(ids, GATES_DIR, RUNS_DIR, source_commit="other")
    assert snap["generated_hash"] == snap2["generated_hash"]   # hash excludes commit
    by = {c["candidate_id"]: c for c in snap["candidates"]}
    assert by["read_fixed_file"]["executable"] and by["fixed_buffer"]["executable"]
    assert by["cancel_fixed_file"]["unsupported_reason"] == "op_not_covered"
    assert by["kiocb_flags"]["unsupported_reason"] == "no_family_template"
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONPATH=src python3 -m pytest tests/test_pilot_adapter.py::test_build_snapshot_provenance_and_determinism -o addopts="" -q`
Expected: FAIL — `cannot import name 'build_snapshot'`

- [ ] **Step 3: 实现 + 落盘**

在 `pilot_adapter.py` 追加:
```python
import json, os, hashlib


def _hash_candidates(candidates):
    norm = json.dumps(candidates, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(norm.encode()).hexdigest()


def build_snapshot(gate_ids, gates_dir, runs_dir, source_commit):
    candidates = []
    for gid in sorted(gate_ids):
        gate_def = json.load(open(os.path.join(gates_dir, gid + ".json")))
        run = json.load(open(os.path.join(runs_dir, gid + ".json")))
        candidates.append(adapt_gate(gid, gate_def, run))
    return {
        "source_commit": source_commit,
        "gate_ids": sorted(gate_ids),
        "adapter_version": ADAPTER_VERSION,
        "schema_version": PREDICATE_SCHEMA_VERSION,
        "candidates": candidates,
        "generated_hash": _hash_candidates(candidates),
    }
```

落盘(10 真实候选,记 source_commit + date):
```bash
mkdir -p feedback/eval
PYTHONPATH=src python3 - <<'PY'
import json, subprocess, datetime
from implicitfuzz.feedback.pilot_adapter import build_snapshot
ids = ["read_fixed_file","fixed_buffer","cancel_fixed_file","msg_ring_fixed_file",
       "filetable_slot","net_fixed_buffer","poll_polled","poll_double",
       "link_timeout","kiocb_flags"]
sha = subprocess.run(["git","rev-parse","--short","HEAD"],capture_output=True,text=True).stdout.strip()
snap = build_snapshot(ids, "src/implicitfuzz/pilot/gates", "pilot/runs_formal", sha)
snap["generated_date"] = datetime.date.today().isoformat()
json.dump(snap, open("feedback/eval/frozen_candidates.json","w"), indent=2)
print("executable:", [c["candidate_id"] for c in snap["candidates"] if c["executable"]])
print("hash:", snap["generated_hash"][:12])
PY
```
Expected 输出:`executable: ['fixed_buffer', 'read_fixed_file']`(2 个)。

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONPATH=src python3 -m pytest tests/test_pilot_adapter.py -o addopts="" -q`
Expected: PASS(7 passed);且 `feedback/eval/frozen_candidates.json` 含 10 候选、2 executable。

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/feedback/pilot_adapter.py tests/test_pilot_adapter.py feedback/eval/frozen_candidates.json
git commit -F <msg>   # "feat(feedback): frozen candidate snapshot + provenance (10 real Phase 2 gates)"
```

---

### Task 4: `eval_report.py` —— 两层指标 / 分布 / rerank 汇总

**Files:** Create `src/implicitfuzz/feedback/eval_report.py`;Test `tests/test_eval_report.py`

**Interfaces:**
- Consumes: 冻结快照(dict)+ calibrated view(list,来自 `loop.run_loop`)。
- Produces:
  - `summarize(snapshot: dict, calibrated_view: list[dict]) -> dict`
    - 返回 `{coverage, distribution_real, layer1_all_real, layer2_executed, rerank}`。
    - `rerank.before` = 静态档降序(tie-break candidate_id)的 id 序;`rerank.after` = calibrated view 现有顺序的 id 序。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_eval_report.py
from implicitfuzz.feedback.loop import run_loop
from implicitfuzz.feedback.eval_report import summarize
from implicitfuzz.feedback.pilot_adapter import build_snapshot

GATES_DIR = "src/implicitfuzz/pilot/gates"
RUNS_DIR = "pilot/runs_formal"
IDS = ["read_fixed_file", "fixed_buffer", "cancel_fixed_file", "msg_ring_fixed_file",
       "filetable_slot", "net_fixed_buffer", "poll_polled", "poll_double",
       "link_timeout", "kiocb_flags"]


def _stub_executor(pos, neg, sig):
    # both executable templates satisfy pos deeper than neg
    return (19, 0)


def test_summarize_two_layer_and_rerank():
    snap = build_snapshot(IDS, GATES_DIR, RUNS_DIR, "testsha")
    view = run_loop(snap["candidates"], _stub_executor, kernel_build_identity="k")
    s = summarize(snap, view)
    assert s["coverage"] == {"executable": 2, "total": 10,
                             "unsupported_reasons": {"op_not_covered": 4, "no_family_template": 4}}
    assert s["layer1_all_real"] == {"verified": 2, "unsupported": 8, "total": 10}
    assert s["layer2_executed"]["verified"] == 2 and s["layer2_executed"]["total"] == 2
    assert "frozen replay" in s["layer2_executed"]["note"]
    assert s["distribution_real"] == {"verified": 2, "contradicted": 0,
                                      "undecidable": 0, "unsupported": 8}
    # rerank: two verified promote to top (execution_verified), tie-broken by id
    assert s["rerank"]["after"][:2] == ["fixed_buffer", "read_fixed_file"]
    # before: all static-high, ordered by candidate_id
    assert s["rerank"]["before"] == sorted(IDS)
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONPATH=src python3 -m pytest tests/test_eval_report.py -o addopts="" -q`
Expected: FAIL — `No module named 'implicitfuzz.feedback.eval_report'`

- [ ] **Step 3: 实现**

`src/implicitfuzz/feedback/eval_report.py`:
```python
"""Summarize a confidence-feedback eval run into two-layer metrics, the
feedback-state distribution over the real candidate set, and the rerank
before/after. Two layers are always reported together: all real candidates
(honest coverage) AND strictly-supported-and-executed (frozen replay, NOT
held-out generalization). rerank shows execution evidence becoming the new sort
key; it does NOT claim overall ranking-quality improvement (unsupported
candidates have no ground truth)."""

from __future__ import annotations
from implicitfuzz.feedback.update import TIER_ORDER


def summarize(snapshot: dict, calibrated_view: list[dict]) -> dict:
    cands = snapshot["candidates"]
    total = len(cands)
    executable_ids = {c["candidate_id"] for c in cands if c["executable"]}

    reasons = {}
    for c in cands:
        if not c["executable"]:
            reasons[c["unsupported_reason"]] = reasons.get(c["unsupported_reason"], 0) + 1

    dist = {"verified": 0, "contradicted": 0, "undecidable": 0, "unsupported": 0}
    for r in calibrated_view:
        dist[r["execution_status"]] += 1

    ex_view = [r for r in calibrated_view if r["candidate_id"] in executable_ids]
    layer2 = {
        "verified": sum(1 for r in ex_view if r["execution_status"] == "verified"),
        "total": len(ex_view),
        "note": "frozen replay (both participated in template development); NOT held-out generalization",
    }
    before = [c["candidate_id"] for c in sorted(
        cands, key=lambda c: (-TIER_ORDER.index(c["static_confidence"]), c["candidate_id"]))]
    return {
        "coverage": {"executable": len(executable_ids), "total": total,
                     "unsupported_reasons": reasons},
        "distribution_real": dist,
        "layer1_all_real": {"verified": dist["verified"],
                            "unsupported": dist["unsupported"], "total": total},
        "layer2_executed": layer2,
        "rerank": {"before": before,
                   "after": [r["candidate_id"] for r in calibrated_view]},
    }
```

- [ ] **Step 4: 运行确认通过 + 全套**

Run: `PYTHONPATH=src python3 -m pytest tests/test_eval_report.py -o addopts="" -q && PYTHONPATH=src python3 -m pytest tests/ -o addopts="" -q`
Expected: PASS(eval_report 1 passed;全套全绿)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/feedback/eval_report.py tests/test_eval_report.py
git commit -F <msg>   # "feat(feedback): eval summarizer (two-layer metrics, distribution, rerank)"
```

---

### Task 5: 真实跑(server)+ 报告 + overview 更新(过程)

**Files:** Create `execverify/run_feedback_eval.py`、`docs/confidence-feedback-eval.md`、`feedback/eval/calibrated-eval-2026-07-13.json`;Modify `docs/research-progress-overview.md`

- [ ] **Step 1: 写 server 驱动 `execverify/run_feedback_eval.py`**

载 `feedback/eval/frozen_candidates.json` → `loop.run_loop(candidates, executor, kernel_build_identity)`(executor 复用 Task 4 findings 的 `make_executor()`,即 `run_feedback_loop.py` 中调 `feedback_executor.sh` 的闭包;可 import 复用)→ `eval_report.summarize(snap, view)` → 落 `feedback/eval/calibrated-eval-<date>.json`(含 view + summary)。仅 2 个 executable boot(4 次 qemu),8 个 unsupported 不 boot。

- [ ] **Step 2: 跑闭环(server)**

```bash
cd ~/ImplicitFuzz
PYTHONPATH=src python3 execverify/run_feedback_eval.py \
  --snapshot feedback/eval/frozen_candidates.json \
  --out feedback/eval/calibrated-eval-$(date +%Y-%m-%d).json
```
Expected:coverage 2/10;layer1 verified 2/unsupported 8;layer2 verified 2/2;distribution verified 2、unsupported 8、contradicted 0、undecidable 0;rerank after 前二 = `fixed_buffer, read_fixed_file`(执行证据升顶)。

- [ ] **Step 3: 写报告 `docs/confidence-feedback-eval.md`**

含 §5 五节:模板覆盖率(2/10 + 原因分布 op_not_covered×4/no_family_template×4)、两层指标(并列,layer2 标 frozen replay 非泛化)、rerank before/after(static-high 齐平→2 升顶;标注不主张整体排序质量改善;tie-break candidate_id)、**控制项引用 24c33ce**(ff_mutant→contradicted、neg_* abstention 控制,显式注明来自 `docs/confidence-feedback-findings.md`@24c33ce 非本次 run)、边界声明(两家族/单内核子系统/离散/非概率校准)。附冻结快照溯源(source_commit/hash/adapter_version/schema_version)。

- [ ] **Step 4: 更新 `docs/research-progress-overview.md`**

补:置信度反馈闭环 MVP 完成于 `24c33ce`(推断—验证—校准,三更新动作已验)+ 本轮小规模评估结论(2/10 忠实覆盖、状态分布、rerank 语义)+ 指针到 `docs/confidence-feedback-eval.md`、`docs/confidence-feedback-findings.md`。

- [ ] **Step 5: 回归 + 提交(提交前交用户复核报告)**

```bash
extraction/scripts/run_phase1_regression.sh   # ALL PASS
PYTHONPATH=src python3 -m pytest tests/         # 全绿
git add execverify/run_feedback_eval.py feedback/eval/calibrated-eval-*.json \
  docs/confidence-feedback-eval.md docs/research-progress-overview.md
git commit -F <msg>   # "feat(feedback): small-scale eval run on frozen 10-candidate set + report"
```
**提交前把报告(覆盖率/两层指标/rerank/控制项引用)交用户复核。** 之后三边同步(见 [[feedback-three-way-sync]])。

---

## Self-Review

- **Spec 覆盖:** §2.1 完整签名 → Task 1(EXECUTABLE_GATES 四元组 + `test_same_relation_wrong_target_fn_is_op_not_covered`);§2.2 min-activation + AdapterError → Task 1(`test_min_activation_confidence`/`test_no_activation_term_raises`);§2.3 两层指标 → Task 4(`layer1_all_real`/`layer2_executed` + note);§2.4 rerank 语义 + tie-break → Task 2 + Task 4(`rerank.before/after`);§4 快照溯源 → Task 3(source_commit/gate_ids/adapter_version/schema_version/generated_hash/date);§5 报告五节 + 控制项引用 24c33ce → Task 5;§6 边界 → Task 5 报告;family from clues → Task 1;不改模板 → 全程(仅新增 adapter/report,assemble 未动)。✅
- **占位符:** 无 TBD;Tasks 1–4 完整代码 + 命令 + 期望;Task 5 显式过程 + 具体产物 + 用户复核门。`<msg>` = commit 信息文件。
- **类型一致:** `adapt_gate(gate_id,gate_def,run)`、`build_snapshot(gate_ids,gates_dir,runs_dir,source_commit)`、`summarize(snapshot,calibrated_view)`、候选字段(executable/gate_family/field_relation_family/unsupported_reason/static_confidence)、`run_loop`/`rerank` 签名跨 Task 一致;executor `(pos,neg,sig)->(sp,sn)` 与既有一致。✅
- **已知开放点:** 仅 2 个门控家族严格可执行(read_fixed_file/fixed_buffer),故第二层为 frozen replay(已在 layer2.note + 报告显式标注,非泛化);static tier 经数据核验 10 候选均 high(activation 全 high),rerank-before 齐平——正是执行证据作为区分键的场景;真实执行依赖 execverify(server)。
