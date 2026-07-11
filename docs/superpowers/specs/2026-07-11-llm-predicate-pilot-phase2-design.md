# LLM Constrained Predicate Inference — Pilot Phase 2 (cold-judge eval) 设计

**Date:** 2026-07-11
**Branch:** `phase2b-evidence-identity`
**Scope:** 研究内容一 · LLM 受限判读环节的**小样本可行性实验**(接 Phase 1 对账层)
**Status:** design approved; awaiting implementation plan (writing-plans)
**依据:** `docs/superpowers/specs/2026-07-11-llm-predicate-inference-pilot-design.md`(总 spec §4);`docs/ledger-reconciliation-phase1-findings.md`(Phase 1 就绪);`docs/llm-judge-feasibility-spike.md`(spike)

---

## 0. 目标与立论

Phase 1 证明了对账层能可靠对上 numeric-only 字段(`nr_user_files@160`)并拦住幻觉。Phase 2 要量的唯一问题:

> LLM 在**受限代码切片 + 事实账**约束下,能否**冷启动**稳定产出**有用**的状态门控谓词(尤其最难的 param-align),经三道校验后精度如何?

**性质:** 小样本、可审计的可行性**实验**,产出 findings 与定性信号(LLM 当主力还是辅助),**不追求统计显著**。

## 1. 关键方法学决策:冷判读(去污染)

判读员在**本对话之外**冷启动,避免"作者即判读员"的污染:
- **判读员 = 冷 subagent**:每个门控派一个全新 subagent,只喂 input bundle(**不给 ground-truth、不给本对话历史、不给 reconciliation 结果**),冷产出封闭谓词后返回。
- **ground-truth 作者 = 内联的我**(了解正确答案)→ 你复核关键样本。作者与判读员天然分离。
- 由此,连 READ_FIXED 也是**有效测量**(判读员冷),而非乐观上界。

## 2. 非目标(YAGNI)

外部 API;批量自动化;20+ 大评测;把验证过的谓词落 schema-bound `gate_seed_fact`;确定性装配/执行验证(研究内容二)。

## 3. 组件

### D. 输入包构建器 `pilot/context.py`
对每个目标门控(取自 `gate_seed_candidate`/`gate_prior_write_candidate` 或人工指定的门控标识),组装 **input bundle**:
- **代码切片**:门控分支所在函数 + 写侧函数的 6.1 源码行(按 `source_location` 取,**有界窗口**,如函数体 ± 若干行,防超长);
- **事实账子集**:这些函数的 `access_fact`(字段名/偏移/semantic_op/源位置);
- **门控标识**:目标 `{bc_unit, function, branch}` 与被门控字段线索(不含答案);
- **封闭输出 schema + 任务指令**(见 E)。
- **红线**:bundle **不含** ground-truth、**不含** reconciliation 结论、**不含**本对话历史。bundle 落盘(`pilot/bundles/<gate>.json`)供审计。

### E. 封闭谓词 schema `pilot/predicate_schema.json`(pilot-local,不入 extraction/schema/facts)
```
predicate = {
  target_gate: {bc_unit, function, branch_instruction_id},
  terms: [ { class: premise|activation|param_align,
             object: <object tag>,
             field_ref: "<struct>.<member>",
             relation: "<free text>",
             align_target: "<free text: 前序调用的哪个参数, param_align 用>",
             source: slice|slice+ledger|inferred,
             confidence: high|medium_high|medium|medium_low|low,
             uncertain: bool } ],
  abstain: bool,
  alt_candidates: [ ... ]      // 允许多候选
}
```

### F. 冷判读运行(harness 编排)
harness 对每门控派一个 subagent,prompt = bundle + schema + 指令(判读员职责:只依据切片+账推谓词,不得引用切片外字段,可 abstain/多候选)。subagent 返回结构化谓词;**原样落盘**(`pilot/runs/<gate>.json`),不改写。

### 三道校验 `pilot/validate.py`(确定性,复用 Phase 1)
对判读输出每个 term:
1. **字段存在性对账**:`reconcile_field(conn, LayoutIndex, struct, member)`(Phase 1)→ `confirmed_symbolic|confirmed_numeric|unconfirmed`;任一 `unconfirmed` → 该 term 判幻觉,整谓词标"未过校验一"并记因;
2. **schema 合法**:过 E 的 JSON Schema;
3. **可合成性预检**:确定性启发——activation/param_align 的对齐目标能否映射到可设置参数(sqe->fd / sqe->buf_index 等);不能则标不可合成。

### G. 评测记录与评分 `pilot/eval.py`
- **ground-truth** `pilot/labels/<gate>.json`:我起草(按 E 的谓词结构给"正确"项)→ 你复核。**与 harness 分离,永不进 bundle**。
- **评分(term 级)**:自动比对判读 terms vs ground-truth terms——按 `(class, field_ref, align_target)` 对齐,算 term 级 **precision/recall**;三道校验结果自动记。**主观正确性由你终审**(如 relation 语义是否等价),理由入 audit JSON。
- **audit 记录** `pilot/audit/<gate>.json`:`{bundle 引用, prompt, judge 原始输出, 三道校验结果, ground-truth, term precision/recall, 人工评分+理由}`。
- **汇总 findings**(markdown 表):逐门控记 judge 谓词是否正确、过几道校验、`unconfirmed` 拦截(含负例)、precision/recall;据此定性判 LLM 主力/辅助。

## 4. 门控集(3 核心 + 至多 2 可选)
| 门控 | 类 | 看点 |
|---|---|---|
| READ_FIXED fixed-file(`fd<nr_user_files`+file_table) | param_align(numeric-only) | 旗舰;冷判读能否推出跨调用对齐 |
| fixed-buffer(`buf_index<nr_user_bufs`) | param_align(numeric-only) | 第二 param-align,对称 |
| `io_kiocb.flags` 门控 | premise(symbolic) | 对照,符号直配应干净 |
| (可选 1–2) | premise/activation | 增样本;实现时与你定 |

## 5. 验收
- **harness 单测**(合成数据):context 构建(bundle 不含答案的红线断言)、三道校验(复用 Phase 1 三态 + schema + 可合成性)、评分(term precision/recall 计算)。
- **真实实验**:对门控集跑冷判读 → 三道校验 → 评分 → findings 表;**非 pass/fail 断言**,产出可审计记录。
- **回归**:`run_phase1_regression.sh` ALL PASS;`pytest` 全绿。
- **红线自检**:bundle 落盘中确认无 ground-truth / reconciliation 结论 / 对话历史泄漏。

## 6. 硬约束
- 判读员冷启动(冷 subagent,仅 bundle);ground-truth 与 harness 分离、永不进 bundle。
- pilot 谓词 schema 是 pilot-local(不入 facts 目录、不动 gate_seed_fact)。
- 不接外部 API;判读用本模型冷 subagent。
- 全程可审计:bundle/prompt/judge 输出/校验/评分理由逐门控落盘。

## 7. 文件结构(实现以计划为准)
- `src/implicitfuzz/pilot/__init__.py`、`context.py`、`predicate_schema.json`、`validate.py`、`eval.py`
- `pilot/labels/`(ground-truth)、`pilot/bundles/`、`pilot/runs/`、`pilot/audit/`(实验产物,可 gitignore 或选择性提交)
- `tests/test_pilot_context.py`、`test_pilot_validate.py`、`test_pilot_eval.py`
- `docs/llm-predicate-pilot-runbook.md` + findings
