# 置信度反馈闭环(最小版)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建"推断—验证—校准"最小内部反馈闭环:重放已提交候选 + 模板化正负 prog(fixed-file/buffer 家族)+ 复用 execverify 执行 → 按离散档单调升降校准置信 + 重排,原始静态结果不可变。

**Architecture:** 纯 Python 反馈核心(update 规则 / assemble 模板 / loop 编排,executor 注入以便单测),真实闭环跑复用 execverify harness(qemu/KCOV/analyze_cover)。四态 outcome(verified/contradicted/undecidable/unsupported),离散档更新。

**Tech Stack:** Python ≥3.8 + pytest;真实执行复用 `execverify/`(server, qemu-kvm/KCOV)。

## Global Constraints

- **outcome 四态**:`verified / contradicted / undecidable / unsupported`。**不用 `falsified`**(KCOV 是执行代理,contradicted 更诚实)。
- **contradicted 仅方向相反**:signal_neg − signal_pos ≥ margin;`pos≈neg`(|diff|<margin)→ **undecidable,不降档**。
- **unsupported ≠ undecidable**:unsupported=无模板未执行;undecidable=执行了但不可判(noise/信号不可见/运行失败)。两者都维持静态档,原因不同。
- **档序**:`rejected < low < medium < high < execution_verified`。verified→execution_verified;contradicted→rejected;undecidable/unsupported→维持静态档+reason。
- **合成负控**:`candidate_origin=synthetic_negative_control`,**不计入真实候选准确率/校准统计**。
- **calibrated view 不覆盖原始**:原始静态候选不可变;view 另存,记全溯源(candidate_id/origin/static/execution_status/final/template_family/template_version/kernel_build_identity/signal_function/pos_coverage/neg_coverage/reason)。
- 复用 execverify(fixed-file: value-granularity fd_index;fixed-buffer: state via register/unregister);不接 live-LLM、不做通用合成、离散档(数值收敛留后续)。
- 服务器工作流:本地改 → scp → 服务器 pytest/`git commit -F`。本地快跑 `PYTHONPATH=src <venv>/python -m pytest <f> -o addopts="" -q`。
- 收口:`run_phase1_regression.sh` ALL PASS;`pytest` 全绿。

参考 spec:`docs/superpowers/specs/2026-07-13-confidence-feedback-loop-design.md`。

---

### Task 1: 反馈核心 `update.py`(outcome 分类 + 档更新 + 重排)

**Files:** Create `src/implicitfuzz/feedback/__init__.py`(空)、`src/implicitfuzz/feedback/update.py`;Test `tests/test_feedback_update.py`

**Interfaces:**
- Produces:
  - `TIER_ORDER = ["rejected","low","medium","high","execution_verified"]`
  - `classify_outcome(signal_pos, signal_neg, margin=2) -> str`(verified/contradicted/undecidable)
  - `update_confidence(static_tier, outcome) -> tuple[str,str]`(final_tier, reason);outcome ∈ 四态
  - `rerank(view: list[dict]) -> list[dict]`(按 final_confidence 降序)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_feedback_update.py
from implicitfuzz.feedback.update import classify_outcome, update_confidence, rerank


def test_classify_outcome():
    assert classify_outcome(19, 0) == "verified"          # pos >> neg
    assert classify_outcome(0, 19) == "contradicted"      # opposite direction
    assert classify_outcome(8, 8) == "undecidable"        # pos ~= neg -> NOT demote
    assert classify_outcome(9, 8) == "undecidable"        # within margin
    assert classify_outcome(None, 5) == "undecidable"     # run failure


def test_update_confidence():
    assert update_confidence("high", "verified")[0] == "execution_verified"
    assert update_confidence("high", "contradicted")[0] == "rejected"
    assert update_confidence("medium", "undecidable") == ("medium",
        "undecidable: signals inseparable (pos~=neg) or run failure")
    assert update_confidence("low", "unsupported")[0] == "low"
    assert "no assembler template" in update_confidence("low", "unsupported")[1]


def test_rerank_orders_by_final_tier():
    view = [{"final_confidence": "low"}, {"final_confidence": "execution_verified"},
            {"final_confidence": "rejected"}, {"final_confidence": "high"}]
    order = [r["final_confidence"] for r in rerank(view)]
    assert order == ["execution_verified", "high", "low", "rejected"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_feedback_update.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'implicitfuzz.feedback'`

- [ ] **Step 3: 实现**

`src/implicitfuzz/feedback/__init__.py`: 空。

`src/implicitfuzz/feedback/update.py`:
```python
"""Confidence-feedback update rule. KCOV is an execution proxy, so a demotion is
'contradicted' (opposite-direction evidence), never 'falsified'; pos~=neg is
'undecidable' (no demotion). unsupported = no template (not executed)."""

from __future__ import annotations

TIER_ORDER = ["rejected", "low", "medium", "high", "execution_verified"]


def classify_outcome(signal_pos, signal_neg, margin: int = 2) -> str:
    if signal_pos is None or signal_neg is None:
        return "undecidable"
    if signal_pos - signal_neg >= margin:
        return "verified"
    if signal_neg - signal_pos >= margin:
        return "contradicted"
    return "undecidable"


def update_confidence(static_tier: str, outcome: str) -> tuple[str, str]:
    if outcome == "verified":
        return "execution_verified", "execution verified: claimed-satisfy covers deeper than claimed-violate"
    if outcome == "contradicted":
        return "rejected", "contradicted: opposite-direction execution evidence"
    if outcome == "unsupported":
        return static_tier, "unsupported: no assembler template for gate_family"
    return static_tier, "undecidable: signals inseparable (pos~=neg) or run failure"


def rerank(view: list[dict]) -> list[dict]:
    return sorted(view, key=lambda r: TIER_ORDER.index(r["final_confidence"]), reverse=True)
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_feedback_update.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/feedback/__init__.py src/implicitfuzz/feedback/update.py tests/test_feedback_update.py
git commit -F <msg>   # "feat(feedback): four-state outcome classify + discrete-tier update + rerank"
```

---

### Task 2: 模板化装配 `assemble.py`

**Files:** Create `src/implicitfuzz/feedback/assemble.py`;Test `tests/test_feedback_assemble.py`

**Interfaces:**
- Produces:
  - `TEMPLATE_VERSION = "1.0"`;`FAMILIES = {"fixed_file","fixed_buffer"}`
  - `assemble_progs(candidate: dict) -> dict | None`
    - 返回 `{"pos": <syz prog str>, "neg": <syz prog str>, "template_family": str, "template_version": str}`;非家族 → `None`(unsupported)。
    - candidate 含 `gate_family`、`param_align={"relation": "<"|">="}`。pos = 候选**声称满足**用例,neg = **声称违反**用例。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_feedback_assemble.py
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_feedback_assemble.py -v`
Expected: FAIL — `ModuleNotFoundError` / `cannot import name 'assemble_progs'`

- [ ] **Step 3: 实现**

`src/implicitfuzz/feedback/assemble.py`:
```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_feedback_assemble.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/feedback/assemble.py tests/test_feedback_assemble.py
git commit -F <msg>   # "feat(feedback): templated pos/neg prog assembly (fixed-file/buffer; non-family unsupported)"
```

---

### Task 3: 编排 `loop.py`(执行注入 + calibrated view + 原始不可变)

**Files:** Create `src/implicitfuzz/feedback/loop.py`;Test `tests/test_feedback_loop.py`

**Interfaces:**
- Consumes: `assemble_progs`、`classify_outcome`、`update_confidence`、`rerank`。
- Produces:
  - `run_loop(candidates: list[dict], executor, kernel_build_identity: str) -> list[dict]`
    - `executor(pos_prog, neg_prog, signal_func) -> tuple[int|None, int|None]`(注入;真实=execverify,单测=stub)。
    - 返回 calibrated view(已按 final 档重排),**不改动 candidates 原始 dict**。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_feedback_loop.py
import copy
from implicitfuzz.feedback.loop import run_loop


def _cands():
    return [
        {"candidate_id": "ff_real", "candidate_origin": "replayed", "gate_family": "fixed_file",
         "static_confidence": "high", "param_align": {"relation": "<"}, "signal_function": "io_read"},
        {"candidate_id": "ff_mutant", "candidate_origin": "synthetic_negative_control",
         "gate_family": "fixed_file", "static_confidence": "high",
         "param_align": {"relation": ">="}, "signal_function": "io_read"},
        {"candidate_id": "flags", "candidate_origin": "replayed", "gate_family": "io_kiocb_flags_premise",
         "static_confidence": "medium", "param_align": {"relation": "<"}, "signal_function": "io_read"},
    ]


def _stub_executor(pos, neg, sig):
    # fd_index=1 (valid) reaches io_read; fd_index=5 (OOB) does not.
    def cov(prog):
        return 19 if "@fd_index=0x1" in prog else 0
    return cov(pos), cov(neg)


def test_loop_three_update_actions_and_immutability():
    cands = _cands(); original = copy.deepcopy(cands)
    view = run_loop(cands, _stub_executor, kernel_build_identity="bzImage@abc123")
    by = {r["candidate_id"]: r for r in view}
    assert by["ff_real"]["execution_status"] == "verified"
    assert by["ff_real"]["final_confidence"] == "execution_verified"
    assert by["ff_mutant"]["execution_status"] == "contradicted"
    assert by["ff_mutant"]["final_confidence"] == "rejected"
    assert by["ff_mutant"]["candidate_origin"] == "synthetic_negative_control"
    assert by["flags"]["execution_status"] == "unsupported"
    assert by["flags"]["final_confidence"] == "medium"          # static held
    # provenance present
    assert by["ff_real"]["kernel_build_identity"] == "bzImage@abc123"
    assert by["ff_real"]["pos_coverage"] == 19 and by["ff_real"]["neg_coverage"] == 0
    # rerank: execution_verified first, rejected last
    assert view[0]["candidate_id"] == "ff_real" and view[-1]["candidate_id"] == "ff_mutant"
    # RED LINE: original candidates unchanged
    assert cands == original
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_feedback_loop.py -v`
Expected: FAIL — `ModuleNotFoundError` / `cannot import name 'run_loop'`

- [ ] **Step 3: 实现**

`src/implicitfuzz/feedback/loop.py`:
```python
"""Confidence-feedback loop orchestrator. For each candidate: assemble pos/neg
progs, execute (injected executor), classify the outcome, update the discrete
tier, and build a calibrated view WITHOUT mutating the original candidate."""

from __future__ import annotations

from implicitfuzz.feedback.assemble import assemble_progs
from implicitfuzz.feedback.update import classify_outcome, update_confidence, rerank


def run_loop(candidates: list[dict], executor, kernel_build_identity: str) -> list[dict]:
    view = []
    for c in candidates:
        asm = assemble_progs(c)
        if asm is None:
            outcome, sp, sn, tf, tv = "unsupported", None, None, None, None
        else:
            sp, sn = executor(asm["pos"], asm["neg"], c.get("signal_function"))
            outcome = classify_outcome(sp, sn)
            tf, tv = asm["template_family"], asm["template_version"]
        final, reason = update_confidence(c["static_confidence"], outcome)
        view.append({
            "candidate_id": c["candidate_id"],
            "candidate_origin": c.get("candidate_origin", "replayed"),
            "static_confidence": c["static_confidence"],
            "execution_status": outcome,
            "final_confidence": final,
            "template_family": tf,
            "template_version": tv,
            "kernel_build_identity": kernel_build_identity,
            "signal_function": c.get("signal_function"),
            "pos_coverage": sp,
            "neg_coverage": sn,
            "reason": reason,
        })
    return rerank(view)
```

- [ ] **Step 4: 运行确认通过 + 全套**

Run: `python3 -m pytest tests/test_feedback_loop.py -v && python3 -m pytest tests/ -q`
Expected: PASS(loop 1 passed;全套累计全绿)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/feedback/loop.py tests/test_feedback_loop.py
git commit -F <msg>   # "feat(feedback): loop orchestrator (injected executor, immutable-original calibrated view)"
```

---

### Task 4: 真实闭环跑 + findings（过程,复用 execverify）

**Files:** Create `src/implicitfuzz/feedback/candidates/{ff_real,ff_mutant,flags_unsupported}.json`、`execverify/feedback_executor.sh`、`docs/confidence-feedback-findings.md`

- [ ] **Step 1: 写 3 个候选 JSON**

`feedback/candidates/ff_real.json`(真实,relation `<`)、`ff_mutant.json`(`candidate_origin=synthetic_negative_control`,relation `>=`)、`flags_unsupported.json`(`gate_family=io_kiocb_flags_premise`)。字段同 Task 3 测试的 `_cands()`。

- [ ] **Step 2: 写真实 executor 包装(server, 复用 execverify)**

`execverify/feedback_executor.sh <pos.syz> <neg.syz> <signal_func>`:对 pos/neg 各 `run_execprog.sh`(build initramfs + qemu-kvm boot + 抓覆盖),再 `analyze_cover.py <pos.log> <neg.log> <signal>` 取 signal_pos/signal_neg,打印两数。**先对每个 prog 用 `syz-prog2c` dump 字节自检(register nr_args / fd_index)**,再 boot。

- [ ] **Step 3: 跑闭环(server)**

用 loop.run_loop 载 3 候选 + 真实 executor(调 feedback_executor.sh 解析出 sp/sn)+ kernel_build_identity(`bzImage` sha)→ 生成 calibrated view 落 `feedback/calibrated/run-<date>.json`。
Expected:`ff_real`→verified→execution_verified;`ff_mutant`→contradicted→rejected(标 synthetic_negative_control);`flags_unsupported`→unsupported→维持 medium。重排:ff_real 首、ff_mutant 末。

- [ ] **Step 4: 写 findings + 回归 + 提交**

`docs/confidence-feedback-findings.md`:三种更新动作各一例 + calibrated view 表(静态档→outcome→final 档 + 溯源)+ 诚实标注(合成负控不计真实统计;contradicted 仅方向相反;calibrated 不覆盖原始;undecidable 由单测覆盖、真实运行自然产生时再纳入)。**提交前交用户复核 findings。**
```bash
extraction/scripts/run_phase1_regression.sh   # ALL PASS
python3 -m pytest tests/                        # 全绿
git add src/implicitfuzz/feedback/candidates execverify/feedback_executor.sh feedback/calibrated docs/confidence-feedback-findings.md
git commit -F <msg>   # "feat(feedback): real closed-loop run on 3 candidates + findings"
```

---

## Self-Review

- **Spec 覆盖:** 四态 outcome → Task 1(classify + update);contradicted 仅方向相反/pos≈neg→undecidable → Task 1 测试;unsupported≠undecidable → Task 1/3;档更新规则 → Task 1;模板化装配(family 参数化/非家族 unsupported)→ Task 2;loop 编排 + 执行注入 + calibrated view 不覆盖原始 → Task 3;合成负控标注不计统计 → Task 3/4;溯源字段 → Task 3;三更新动作演示 + undecidable 仅单测 → Task 4 + Task 1;复用 execverify → Task 4。✅
- **占位符:** 无 TBD;Tasks 1–3 完整代码/命令;Task 4 显式过程 + 具体产物。`<msg>` = commit 信息文件。
- **类型一致:** `classify_outcome`/`update_confidence`/`rerank`/`assemble_progs`/`run_loop`、TIER_ORDER、四态字符串、calibrated view 字段跨 Task 一致;executor 签名 `(pos,neg,sig)->(sp,sn)` Task 3/4 一致。✅
- **已知开放点:** fixed_buffer 模板用 state(register/unregister)而非 value-granularity(buf_index SQE 编码不可靠,已记);真实 executor 的覆盖解析依赖 execverify(server);margin 默认 2(io_read 19 vs 0 远超,稳)。
