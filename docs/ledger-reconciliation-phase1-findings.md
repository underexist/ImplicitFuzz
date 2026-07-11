# Ledger Reconciliation (Pilot Phase 1) — findings

**Date:** 2026-07-11
**Branch:** `phase2b-evidence-identity`
**Spec/plan:** `docs/superpowers/specs/2026-07-11-llm-predicate-inference-pilot-design.md` §3；`docs/superpowers/plans/2026-07-11-ledger-reconciliation-phase1.md`
**Prereq:** `docs/llm-judge-feasibility-spike.md`（spike 定位到"字段存在性对账"是 make-or-break）

## 建了什么
pilot 的 Phase 1 对账层——让"LLM 说出的字段(含 numeric-only 的 `ctx->nr_user_files`)能被事实账可靠对上":
- `struct_layout_fact`(新增 additive fact)+ ingest 表：DWARF 的 offset→member 布局。
- 抽取器 **opt-in** `-emit-struct-layout`(默认关,只读 DWARF、不碰 SVF 段错误热路径,回归零影响);**递归进匿名 cacheline 对齐子结构**并按绝对偏移累加(否则 `nr_user_files` 藏在 `io_ring_ctx` 匿名组里会漏)。
- `reconcile/layout.py` `name→(struct,offset)` 解析器 + `reconcile/field_existence.py` 三态校验(`confirmed_symbolic` / `confirmed_numeric` / `unconfirmed`)。

## 真实数据结论(rsrc 语料,已跑)
| 字段 | 结果 | 说明 |
|---|---|---|
| `io_ring_ctx.nr_user_files` | **confirmed_numeric @160** | 旗舰 numeric-only 字段被 offset 救回 |
| `io_ring_ctx.nr_user_bufs` | **confirmed_numeric @164** | fixed-buffer 旗舰 |
| `io_ring_ctx.file_data` | confirmed_symbolic | 符号直配 |
| 编造字段 | **unconfirmed** | 幻觉被拦 |

→ **验证了 spike 结论**:纯符号按名对账会误杀 `nr_user_files`;加 name→offset 解析后,numeric-only 字段可靠确认,同时保留对幻觉的拦截。这解锁了 Phase 2 LLM 谓词的三道校验中最关键的"字段存在性对账"。

## 关键事实与坑(已确认)
- **offset 用 6.1 树自己的 DWARF**:`nr_user_files@160` 与账里数值写点一致;运行时 BTF 给的是 120(不同内核版本布局),**不能当 oracle**——已由 opt-in 抽取器 emit(读 .bc 的 -g DWARF)规避。
- `io_ring_ctx` 顶层成员 36 个,递归匿名子结构后 86 个,`nr_user_files`/`nr_user_bufs` 才现身。

## 已知精度待量项(留 Phase 2 findings)
`confirmed_numeric` 目前按"全账同偏移 numeric access_fact"匹配,**不限定目标 struct**——因为 numeric-only access 本身不带 struct 标注(spike 已知 `field_type=null`)。旗舰命中正确,但存在"不同 struct 同偏移"误确认的理论风险。Phase 2 评测时量其精度;若需收紧,后续加 base-struct 过滤(follow-up)。

## 验收
服务器 `pytest` 40 passed;`run_phase1_regression.sh` ALL PASS(flag 关);flag 开 rsrc emit 4739 条 struct_layout、schema-valid。三处同步于 `18e0d05`(服务器/本地/GitHub)。
