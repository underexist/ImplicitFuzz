# LLM Predicate Formal Evaluation (scaled cold-judge) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 pilot 的冷判读实验放大加固到 15–20 门控 —— 加放宽自动评分键、人工语义评分模板、沙箱审计三个 harness 增量,加多样正样本 + 守卫压力/负控门控集,分批冷判读,产出量化 + 可审计 findings。

**Architecture:** 复用 pilot harness(schema/bundle/validate/audit);`eval.py` 加 3 个纯函数(Tasks 1–2,TDD);Task 3 作者式扩门控集(15–20 gate def + GT,含负控);Task 4 分批实验(bundle 内联进 prompt + 冷 subagent + 断言 tool_uses==0)+ 三评分 + 人工语义评分 + findings。

**Tech Stack:** Python ≥3.8 + sqlite3 + jsonschema + pytest;冷判读用 Agent 工具(bundle 内联,零工具);合并 DB 已有(`/tmp/pilot.db`:core+rw+rsrc facts + rsrc struct_layout)。

## Global Constraints

- 复用 pilot:`implicitfuzz.pilot.{validate,context,eval}`、`predicate_schema.json`;`reconcile.{layout,field_existence}`(Phase 1)。
- **判读沙箱**:bundle 内联进判读 prompt,判读**零工具即可完成**;审计断言 `tool_uses == 0`,违规=污染重跑。
- ground-truth 存 `pilot/labels/`,**与判读 prompt 分离,永不泄漏**(内联 prompt 红线自检)。
- 人工语义评分为主指标;放宽自动键为下界代理;exact-key 保留作参考——**三者对照报,不互相覆盖**。
- 负控靠"难门控 + 对抗 prompt"自然诱发;守卫 catch-rate 如实报(可能=0)。守卫机制已由 `tests/test_field_existence.py::test_unconfirmed_hallucination` 证明。
- pilot 谓词 schema 仍 pilot-local;不改 schema-bound fact;不接外部 API;不做自动切片发现(curated,如实记 caveat)。
- 分批执行(每批约 5 门控),分批交用户抽样复核。
- 服务器工作流:本地改 → scp → 服务器 pytest/`git commit -F`;实验在能访问 `/home/xujunru/linux-6.1` + `/tmp/pilot.db` 处跑。
- 收口:`run_phase1_regression.sh` ALL PASS;`pytest` 全绿。本地快跑:`PYTHONPATH=src <venv>/python -m pytest <f> -o addopts="" -q`。

参考 spec:`docs/superpowers/specs/2026-07-11-llm-predicate-formal-eval-design.md`。

---

### Task 1: 放宽自动评分键 `score_terms_relaxed`

**Files:**
- Modify: `src/implicitfuzz/pilot/eval.py`
- Test: `tests/test_pilot_eval.py`(追加)

**Interfaces:**
- Produces: `score_terms_relaxed(judge_terms: list[dict], truth_terms: list[dict]) -> dict`
  返回 `{"class_field": {precision,recall,matched}, "field_set": {precision,recall,matched}}`;
  `class_field` 键 = `(class, field_ref)`(忽略 align_target);`field_set` 键 = `field_ref`(忽略 class 与归属)。

- [ ] **Step 1: 写失败测试(追加到 tests/test_pilot_eval.py)**

```python
from implicitfuzz.pilot.eval import score_terms_relaxed


def _tr(cls, fr, align=""):
    return {"class": cls, "field_ref": fr, "align_target": align}


def test_score_terms_relaxed_rescues_align_target_text_mismatch():
    # same class+field, different free-text align_target -> exact key would miss,
    # relaxed class_field key matches.
    judge = [_tr("param_align", "io_ring_ctx.nr_user_files", "io_sqe_files_register.nr_args")]
    truth = [_tr("param_align", "io_ring_ctx.nr_user_files", "sqe->fd < prior register nr_args")]
    r = score_terms_relaxed(judge, truth)
    assert r["class_field"]["matched"] == 1
    assert r["class_field"]["precision"] == 1.0 and r["class_field"]["recall"] == 1.0


def test_score_terms_relaxed_field_set_ignores_class():
    judge = [_tr("premise", "io_ring_ctx.nr_user_bufs")]
    truth = [_tr("activation", "io_ring_ctx.nr_user_bufs")]
    r = score_terms_relaxed(judge, truth)
    assert r["class_field"]["matched"] == 0      # class differs
    assert r["field_set"]["matched"] == 1        # field overlaps
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_pilot_eval.py -k relaxed -v`
Expected: FAIL — `ImportError: cannot import name 'score_terms_relaxed'`

- [ ] **Step 3: 实现(在 eval.py 追加)**

```python
def score_terms_relaxed(judge_terms: list[dict], truth_terms: list[dict]) -> dict:
    def _pr(jset, tset):
        matched = jset & tset
        precision = len(matched) / len(jset) if jset else 0.0
        recall = len(matched) / len(tset) if tset else 0.0
        return {"precision": precision, "recall": recall, "matched": len(matched)}

    jcf = {(t.get("class"), t.get("field_ref")) for t in judge_terms}
    tcf = {(t.get("class"), t.get("field_ref")) for t in truth_terms}
    jf = {t.get("field_ref") for t in judge_terms}
    tf = {t.get("field_ref") for t in truth_terms}
    return {"class_field": _pr(jcf, tcf), "field_set": _pr(jf, tf)}
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_pilot_eval.py -k relaxed -v`
Expected: PASS(2 passed)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/pilot/eval.py tests/test_pilot_eval.py
git commit -F <msg>   # "feat(pilot): relaxed auto scoring key (class-field + field-set)"
```

---

### Task 2: 人工评分模板 + 沙箱审计 `human_score_template` / `sandbox_audit`

**Files:**
- Modify: `src/implicitfuzz/pilot/eval.py`
- Test: `tests/test_pilot_eval.py`(追加)

**Interfaces:**
- Produces:
  - `human_score_template(judge_terms: list[dict]) -> dict`(待填的结构化人工评分模板)
  - `sandbox_audit(tool_uses: int) -> dict`(`{"clean": tool_uses == 0, "tool_uses": tool_uses}`)

- [ ] **Step 1: 写失败测试(追加)**

```python
from implicitfuzz.pilot.eval import human_score_template, sandbox_audit


def test_human_score_template_shape():
    judge = [{"class": "param_align", "field_ref": "io_ring_ctx.nr_user_files"},
             {"class": "premise", "field_ref": "io_ring_ctx.file_data"}]
    t = human_score_template(judge)
    assert t["recovered_required_terms"] is None
    assert t["param_align_correct"] == "n/a"
    assert t["has_wrong_term"] is None
    assert [v["field_ref"] for v in t["per_term_verdict"]] == \
        ["io_ring_ctx.nr_user_files", "io_ring_ctx.file_data"]
    assert all(v["verdict"] is None for v in t["per_term_verdict"])
    assert t["notes"] == ""


def test_sandbox_audit():
    assert sandbox_audit(0) == {"clean": True, "tool_uses": 0}
    assert sandbox_audit(2) == {"clean": False, "tool_uses": 2}
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_pilot_eval.py -k "human_score or sandbox" -v`
Expected: FAIL — `ImportError: cannot import name 'human_score_template'`

- [ ] **Step 3: 实现(在 eval.py 追加)**

```python
def human_score_template(judge_terms: list[dict]) -> dict:
    return {
        "recovered_required_terms": None,
        "param_align_correct": "n/a",
        "has_wrong_term": None,
        "per_term_verdict": [
            {"field_ref": t.get("field_ref"), "class": t.get("class"), "verdict": None}
            for t in judge_terms
        ],
        "notes": "",
    }


def sandbox_audit(tool_uses: int) -> dict:
    return {"clean": tool_uses == 0, "tool_uses": tool_uses}
```

- [ ] **Step 4: 运行确认通过 + 全套**

Run: `python3 -m pytest tests/test_pilot_eval.py -v && python3 -m pytest tests/ -q`
Expected: PASS(pilot eval 全绿;全套累计全绿)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/pilot/eval.py tests/test_pilot_eval.py
git commit -F <msg>   # "feat(pilot): human-score template + sandbox (tool_uses==0) audit"
```

---

### Task 3: 门控集扩到 15–20（数据作者 + 用户抽样复核；非 TDD）

**Files:**
- Create: `src/implicitfuzz/pilot/gates/*.json`（新增 ~12–15 正 + ~3–5 负控，含 pilot 已有 3 个共约 15–20）
- Create: `src/implicitfuzz/pilot/labels/*.json`（对应 GT；负控不设"必对"GT，其 label 记预期守卫行为）

- [ ] **Step 1: 定门控清单（覆盖矩阵）**

按类型 × 子系统列 15–20 门控:param_align(fixed-file/fixed-buffer/net fixed-buffer/msg_ring fixed-file …)、activation(状态位/计数边界)、premise(链/超时/poll 状态位)。子系统取已 ingest 的 core/rw/rsrc + 可补 ingest 的 net/poll/timeout/cancel/msg_ring bc(`/tmp/implicitfuzz-io_uring-all/bc/`)。清单写 `pilot/gates/_manifest.md`,标每门控 type+subsystem+是否负控。

- [ ] **Step 2: 补 ingest 所需 TU + 扩合并 DB**

对清单涉及但 DB 未含的 TU:抽取 facts + 该 TU struct_layout(`-emit-struct-layout`),ingest 进一个评测 DB(`/tmp/eval.db`)。命令模板见 Phase 1 plan Task 2 Step 6 与 pilot 数据准备(`extract -emit-struct-layout` + `ingest_jsonl`)。验证清单里所有 gate/writer 函数在 DB 有 access_fact。

- [ ] **Step 3: 起草 gate def + GT，本地校验**

为每门控写 gate def(slice_locations/ledger_functions/field_clues)+ GT 谓词。负控:构造更难/歧义门控,或在 gate def 里选易诱发编造字段的切片;其 label 记 `{"expected": "guard_should_fire_if_hallucinated", "true_predicate": <best-effort>}`。校验:
```bash
PYTHONPATH=src <venv>/python - <<'PY'
import json, glob, sqlite3
from implicitfuzz.pilot.validate import validate_schema
from implicitfuzz.reconcile.layout import LayoutIndex
from implicitfuzz.reconcile.field_existence import reconcile_field
conn=sqlite3.connect("/tmp/eval.db"); L=LayoutIndex(conn)
for f in sorted(glob.glob("src/implicitfuzz/pilot/labels/*.json")):
    p=json.load(open(f)); ok=validate_schema(p) if "terms" in p else True
    print(f.split("/")[-1], "schema", ok)
    for t in p.get("terms", []):
        s,m=t["field_ref"].rsplit(".",1)
        print("   ", t["field_ref"], reconcile_field(conn,L,s,m).status)
PY
```
正样本 GT 的 field_ref 应全部 confirmed;若某项 unconfirmed,修正 GT 或换门控(负控除外——负控允许引用缺失字段)。

- [ ] **Step 4: 交用户抽样复核**

把清单 + 全部 param_align GT + 全部负控 label 交用户复核(其余正样本抽查)。**用户确认后**才进入 Task 4。

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/pilot/gates src/implicitfuzz/pilot/labels
git commit -F <msg>   # "feat(pilot): 15-20 gate defs + ground-truth (diverse + guard-stress)"
```

---

### Task 4: 分批冷判读实验 + 三评分 + 人工评分 + findings（过程，非 TDD）

**Files:**
- Create: `docs/llm-predicate-formal-eval-findings.md`
- 产物:`pilot/bundles/`、`pilot/runs/`、`pilot/audit/`（实验落盘）

- [ ] **Step 1: 构建全部 bundle + 红线自检**

```bash
PYTHONPATH=src python3 - <<'PY'
import json, os, glob, sqlite3
from implicitfuzz.pilot.context import build_bundle
os.makedirs("pilot/bundles", exist_ok=True)
conn=sqlite3.connect("/tmp/eval.db")
for gf in sorted(glob.glob("src/implicitfuzz/pilot/gates/*.json")):
    gid=gf.split("/")[-1][:-5]
    gate=json.load(open(gf))
    b=build_bundle(conn,"/home/xujunru/linux-6.1",gate,radius=18)
    assert not any(k in b for k in ("ground_truth","reconciliation","labels","answer"))
    json.dump(b, open("pilot/bundles/%s.json"%gid,"w"), indent=2)
    print(gid, "OK")
PY
```

- [ ] **Step 2: 分批冷判读(每批约 5,bundle 内联进 prompt)**

对每门控,编排者用 **Agent 工具**派一个 general-purpose subagent,prompt = **内联的 bundle JSON 全文** + 判读指令 + "仅据本 prompt 推理,禁止任何工具调用(读文件/命令/网络);最终消息只输出谓词 JSON"。分批(~5/批)。每个 subagent 返回后:
- 原样存 `pilot/runs/<gid>.json`;
- 从完成通知的 `usage.tool_uses` 取工具调用数,`sandbox_audit(tool_uses)` → 若 `clean=False` 标污染并**重跑该门控**。

- [ ] **Step 3: 三道校验 + 三评分 + 人工模板**

```bash
PYTHONPATH=src python3 - <<'PY'
import json, glob, sqlite3, os
from implicitfuzz.pilot.validate import validate_schema, validate_field_existence, validate_synthesizability
from implicitfuzz.pilot.eval import score_terms, score_terms_relaxed, human_score_template, build_audit_record
conn=sqlite3.connect("/tmp/eval.db"); os.makedirs("pilot/audit", exist_ok=True)
for rf in sorted(glob.glob("pilot/runs/*.json")):
    gid=rf.split("/")[-1][:-5]
    judge=json.load(open(rf)); truth=json.load(open("src/implicitfuzz/pilot/labels/%s.json"%gid))
    val={"schema":validate_schema(judge),"field_existence":validate_field_existence(conn,judge),
         "synthesizability":validate_synthesizability(judge)}
    sc={"exact":score_terms(judge.get("terms",[]),truth.get("terms",[])),
        "relaxed":score_terms_relaxed(judge.get("terms",[]),truth.get("terms",[]))}
    rec=build_audit_record(gid,{"target_gate":judge["target_gate"]},judge,val,sc)
    rec["human_score"]=human_score_template(judge.get("terms",[]))   # 待人工填
    json.dump(rec, open("pilot/audit/%s.json"%gid,"w"), indent=2)
    fx=val["field_existence"]; unconf=sum(1 for r in fx["per_term"] if r["status"]=="unconfirmed")
    print(gid,"schema",val["schema"],"fx_pass",fx["passed"],"unconfirmed",unconf,
          "relaxed_cf_R",round(sc["relaxed"]["class_field"]["recall"],2))
PY
```

- [ ] **Step 4: 人工语义评分（编排者填 → 用户抽样复核）**

编排者对每门控填 audit 的 `human_score`(recovered_required_terms / param_align_correct / has_wrong_term / per_term_verdict / notes)。**把 param_align + 负控的评分交用户抽样复核**。

- [ ] **Step 5: 汇总 findings**

写 `docs/llm-predicate-formal-eval-findings.md`:人工语义准确率(总体 + param_align 子类 + 负控)、**三评分对照表**(人工 vs relaxed class_field/field_set vs exact)、**守卫 catch-rate**(真实判读 unconfirmed 触发 / 幻觉数,如实,可能 0)、**沙箱审计**(零工具达标率)。诚实 caveat:n=15–20、单模型、单会话冷启动、curated 切片。**提交前交用户复核 findings**(措辞不过度宣称,延续 pilot findings 的克制)。

- [ ] **Step 6: 回归 + 提交**

```bash
extraction/scripts/run_phase1_regression.sh   # ALL PASS
python3 -m pytest tests/                        # 全绿
git add pilot/runs pilot/audit docs/llm-predicate-formal-eval-findings.md
git commit -F <msg>   # "feat(pilot): formal cold-judge eval on 15-20 gates + findings"
```

---

## Self-Review

- **Spec 覆盖:** Δ1 门控集(正+负控)→ Task 3;Δ2 放宽键 → Task 1;Δ3 人工语义评分 → Task 2(模板)+ Task 4 Step 4;Δ4 沙箱(内联+断言零工具+审计)→ Global Constraints + Task 4 Step 2 + `sandbox_audit`(Task 2);三评分对照 → Task 4 Step 3/5;守卫 catch-rate → Task 4 Step 3/5;分批 → Task 4 Step 2;抽样复核 → Task 3 Step 4 + Task 4 Step 4;红线 → Task 4 Step 1。✅
- **占位符:** 无 TBD;Tasks 1–2 完整代码/命令;Tasks 3–4 为显式过程,含可跑命令。`<msg>` 指 commit 信息文件。
- **类型一致:** `score_terms_relaxed`(class_field/field_set)、`human_score_template`、`sandbox_audit` 跨 Task 一致;复用 `score_terms`/`validate_*`/`build_audit_record`/`reconcile_field`/`LayoutIndex`/`build_bundle` 签名与 pilot/Phase1 一致。✅
- **已知开放点:** 负控无法强制判读幻觉——Task 4 如实报 catch-rate(可能 0=判读可靠);评测 DB(`/tmp/eval.db`)按 Task 3 清单补 ingest,门控涉及的 TU 必须先抽取+ingest 才能进实验。
