# LLM Constrained Predicate Inference — Pilot Phase 2 findings (cold-judge)

**Date:** 2026-07-11
**Branch:** `phase2b-evidence-identity`
**Spec/plan:** `docs/superpowers/specs/2026-07-11-llm-predicate-pilot-phase2-design.md`;`docs/superpowers/plans/2026-07-11-llm-predicate-pilot-phase2.md`
**产物(可审计):** `pilot/gates/`、`pilot/labels/`(ground-truth)、`pilot/runs/`(判读原始输出)、`pilot/audit/`(每门控 audit 记录)

## 方法(去污染)
- **判读员 = 冷 subagent**,每门控一个,仅喂 input bundle(代码切片 + 事实账 + schema + 指令),**不给 ground-truth、不给本对话历史**;经"读单一 bundle 文件、只据其推理"约束。
- **ground-truth 作者 = 内联编排者**(知答案)→ 用户复核;labels 与 harness 分离,永不进 bundle。
- 三道校验(schema / 字段存在性[复用 Phase 1 对账]/ 可合成性)+ term 级评分 + 人工主观终审。
- 样本 n=3:2 个 param_align(numeric-only 旗舰)+ 1 个 premise 对照。**不追求统计显著,追求可审计 + 定性信号。**

## 结果
| Gate | 类型 | schema | 字段存在性 | 可合成 | 自动 P/R(exact key) | 主观 |
|---|---|---|---|---|---|---|
| read_fixed_file(`io_file_get_fixed`) | param_align | ✓ | PASS(4 num,1 sym,0 unconfirmed) | 3/5 | .40/.67 | 正确 |
| fixed_buffer(`io_prep_rw`) | param_align | ✓ | PASS(3 sym,2 num,0 unconfirmed) | 4/5 | .00/.00 | 正确 |
| kiocb_flags(`__io_req_complete_post`) | premise | ✓ | PASS(4 sym,0 unconfirmed) | 3/4 | .33/1.00 | 正确 |

## 关键发现
1. **零字段引用幻觉(经字段存在性门捕获)。** 在这 3 次冷判读中,判读引用的每个字段都被事实账对账确认(14 项:8 confirmed_numeric、6 confirmed_symbolic,0 unconfirmed),含 numeric-only 的 `nr_user_files@160`/`nr_user_bufs@164`/`file_table`。This validates the pilot's key design assumption on real cold-judge outputs: constrained LLM predicates can be checked against the fact ledger, including numeric-only fields recovered through reconciliation. 注:这是**字段存在性层面**的零幻觉,不等于语义层面无误。
2. **两个 param_align 冷启动均推对**(最难子任务):`sqe->fd < 前序 io_sqe_files_register 的 nr_args`;`sqe->buf_index ↔ 前序 io_sqe_buffers_register 注册的槽位`。
3. **判读还主动补了有效约束**(file_table 分配、user_bufs 非空、io_kiocb.link 非空、IO_DISARM_MASK),并用 `alt_candidates`/`uncertain` 标注不确定,符合"多候选 + 不确定退路"设计。

## 方法学结论(影响正式评测)
- **exact-key 自动评分过严、悲观。** For `fixed_buffer`: the judge captured the buffer-index constraint and registration-slot alignment, but the exact-key scorer compared against a GT keyed on `io_ring_ctx.nr_user_bufs`; this exposes a scoring/label-granularity mismatch rather than a semantic failure. 自动 P/R 是悲观下界,人工主观评分是真信号;正式评测应放宽评分键(涉及字段集合 / class+object 粒度),且 GT 归属需更谨慎。

## 定性判据
在这 3 门控(2 param_align + 1 premise)上,冷 LLM 判读产出语义正确的门控谓词、含最难的 param_align,且在这 3 次运行中零字段不可对账。The signal supports treating the LLM as a credible primary judge candidate for the next evaluation stage, with ledger reconciliation as a required hallucination guard. Final role assignment requires the planned 10–20 gate evaluation.

## 局限 / caveat
- **exact-key 评分是悲观下界**,语义正确可能被判 0(见 fixed_buffer);正式评测需放宽评分键 + 更谨慎 GT 归属。
- **冷判读是指令级隔离,非沙箱级**:冷 subagent 被要求只读单一 bundle,但技术上仍有文件系统访问;正式评测可加沙箱或审计判读 transcript 是否越界。
- **Bundle selection remains curated:** the pilot measures cold judgment given bounded, relevant slices, not automatic slice discovery. 后续真实 pipeline 里的切片构建本身是风险点。
- n=3、单模型、单会话冷启动;结论为定性信号,非统计结论。

## 复现
bundle/判读/校验/评分/主观理由逐门控落 `pilot/{bundles,runs,audit}` 与本 findings;合并 DB 由 core+rw+rsrc facts + rsrc struct_layout ingest。判读用冷 subagent(本模型),无外部 API。
