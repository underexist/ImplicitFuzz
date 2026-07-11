# LLM Constrained Predicate Inference — Formal Evaluation (scaled cold-judge) 设计

**Date:** 2026-07-11
**Branch:** `phase2b-evidence-identity`
**Scope:** 研究内容一 · LLM 受限判读环节的**正式评测**(pilot Phase 2 的放大 + 加固)
**Status:** design approved; awaiting implementation plan (writing-plans)
**依据:** `docs/llm-predicate-pilot-findings.md`(pilot 结论:冷判读语义正确、零字段引用幻觉、但 exact-key 评分悲观、守卫从未真实触发、判读仅指令级隔离);pilot spec/plan(`docs/superpowers/{specs,plans}/2026-07-11-llm-predicate-pilot-phase2*.md`)。

---

## 0. 目标
把 pilot 的定性信号升级为**更可信、可审计**的评测,回答:在 15–20 个门控上,冷 LLM 判读的语义准确率(尤其 param_align)如何?**字段存在性守卫能否在真实判读输出上真触发并拦下幻觉?** 据此把 pilot 的"credible primary-judge candidate"推进到有量化支撑的角色判定。**仍诚实标注 n=15–20、单模型、curated 切片。**

## 1. 架构:复用 pilot harness + 4 处增量
pilot 的 `src/implicitfuzz/pilot/{predicate_schema.json, context.py, validate.py, eval.py}`(封闭 schema、bundle 构建、三道校验、audit)**全部复用**。以下为增量。

### Δ1 门控集扩到 15–20(数据,主要工作量)
- **~12–15 多样正样本**:类型覆盖 param_align / activation / premise;子系统覆盖 rw、rsrc、net、poll、timeout、cancel、msg_ring 等。每门控:gate def(`slice_locations` / `ledger_functions` / `field_clues`)+ 人工 ground-truth 谓词。存 `pilot/gates/`、`pilot/labels/`。
- **~3–5 守卫压力/负控**:更难/更歧义门控(自然诱发幻觉)+ 少量对抗式 prompt 框架(抬高编造字段概率)。**目标:让字段存在性门在真实判读输出上真触发并被记录。**
- **诚实约束**:无法强制判读幻觉;负控靠"难门控 + 对抗 prompt"自然诱发,守卫 catch-rate **如实报**——若判读连难门控都不幻觉(catch-rate=0),该结果本身即结论(判读可靠 + 守卫作为保险仍必要)。守卫机制本身已由 Phase 1 `test_field_existence` 的 `unconfirmed` 用例证明。

### Δ2 放宽自动评分键(`eval.py` 加函数)
`score_terms_relaxed(judge_terms, truth_terms) -> dict`:按 **class 桶内的字段集覆盖**匹配(每个 class 桶内,判读引用字段集 vs GT 字段集的 precision/recall),**忽略 align_target 自由文本与字段归属差异**。作**下界代理**。exact-key(`score_terms`)保留作参考,不作主指标。

### Δ3 人工语义评分(主指标,结构化可审计)
每谓词结构化人工评分记录:
```
human_score = {
  recovered_required_terms: bool,      # 是否覆盖 GT 的必需项
  param_align_correct: "yes"|"no"|"n/a",
  has_wrong_term: bool,                # 是否有语义错误项(非仅额外有效项)
  per_term_verdict: ["correct"|"partial"|"wrong"|"valid_extra", ...],
  notes: str
}
```
- 我评(GT 作者角色,与冷判读天然分离)→ **用户抽样复核**(param_align + 负控必看,其余抽查)。入 audit。
- `eval.py` 加 `human_score_template(judge)`(按判读 terms 生成待填模板)与 audit 整合。

### Δ4 沙箱化冷判读(harness / 流程)
- **bundle 内联进判读 prompt**(不再让判读读文件)→ 判读零工具即可完成。
- **transcript 审计**:断言该判读 subagent `tool_uses == 0`;任何工具调用 = 协议违规,标该运行为**污染并重跑**。审计结果(tool_uses、是否达标)入 audit。
- `eval.py`/harness 加 `sandbox_audit(tool_uses:int) -> dict`(`{clean: tool_uses==0, tool_uses}`)。

## 2. 数据流(每门控)
```
gate def ──build_bundle──▶ bundle(代码切片+账+schema)
  ──内联进 prompt──▶ 冷 subagent(零工具)──▶ 判读谓词(原样落 pilot/runs/)
  ──三道校验(schema / field_existence[复用 Phase 1] / synthesizability)
  ──评分(exact 参考 + relaxed 代理 + 人工语义主指标)
  ──sandbox_audit(tool_uses==0)
  ──▶ audit 记录 + 汇总 findings
labels/ 全程与 bundle 分离,永不进 prompt。
```

## 3. 产物 / 判据(汇总 findings)
- **人工语义准确率**:总体 + param_align 子类 + 负控;
- **三评分对照**:人工 vs relaxed 自动键 vs exact-key(展示 relaxed 更贴近人工、exact 悲观);
- **守卫 catch-rate**:真实判读里 `unconfirmed` 触发次数 / 幻觉次数(如实,可能为 0);
- **沙箱审计**:零工具达标率;
- 诚实 caveat:n=15–20、单模型、单会话冷启动、curated 切片(非自动发现)。

## 4. 执行(分批)
15–20 个 subagent 判读**分批跑、分批给用户看**(如每批 5),而非一次性。GT 我先起草、用户抽样复核后才用。

## 5. 测试与验收
- **harness 单测**(合成数据):`score_terms_relaxed`(class 桶字段集覆盖)、`human_score_template`(模板结构)、`sandbox_audit`(tool_uses==0 判定)。
- **真实实验**:15–20 门控跑完 → 三道校验 + 三评分 + 沙箱审计 → 汇总 findings;非 pass/fail 断言,产可审计记录。
- **回归**:`run_phase1_regression.sh` ALL PASS;`pytest` 全绿。
- **红线自检**:每个内联 prompt 无 ground-truth / labels / reconciliation 泄漏。

## 6. 硬约束
- 判读员冷启动 + 沙箱化(bundle 内联、断言零工具);ground-truth 与 prompt 分离、永不泄漏。
- pilot 谓词 schema 仍 pilot-local,不动 schema-bound fact。
- 不接外部 API;不做自动切片发现(curated,如实记 caveat)。
- 全程可审计:bundle/判读/校验/三评分/沙箱审计/人工理由逐门控落盘。

## 7. 文件结构(实现以计划为准)
- `src/implicitfuzz/pilot/eval.py` — 加 `score_terms_relaxed`、`human_score_template`、`sandbox_audit`。
- `src/implicitfuzz/pilot/gates/*.json`、`labels/*.json` — 新增 15–20 门控 + GT。
- `tests/test_pilot_eval.py` — 加 relaxed/human-template/sandbox-audit 单测。
- `docs/llm-predicate-formal-eval-findings.md` + `pilot/{bundles,runs,audit}` 产物。
