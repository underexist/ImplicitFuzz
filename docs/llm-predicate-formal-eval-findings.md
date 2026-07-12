# LLM Constrained Predicate Inference — Formal Evaluation findings (cold-judge, 13 gates)

**Date:** 2026-07-12
**Branch:** `phase2b-evidence-identity`
**Spec/plan:** `docs/superpowers/{specs,plans}/2026-07-11-llm-predicate-formal-eval*.md`
**产物(可审计):** `pilot/gates/`、`pilot/labels/`(ground-truth)、`pilot/runs_formal/`(判读原始输出)、`pilot/audit_formal/`(每门控 audit)

## 方法
- **13 门控**:10 正样本(5 param_align 跨 rw/rsrc/net/msg_ring/cancel;2 activation;3 premise,跨 io_uring/poll/timeout-link)+ 3 负控(守卫压力)。
- **判读员 = 冷 subagent**,每门控一个,**只读它自己那一个 bundle 文件**(代码切片 + 事实账 + schema),据其推理输出封闭谓词;**沙箱审计断言 tool_uses ≤ 1**(恰好读该 bundle、无越界)。ground-truth 由内联编排者起草、用户抽样复核,**与 bundle 分离**。
- 三道校验(schema / 字段存在性[复用 Phase 1 对账]/ 可合成性)+ 三评分(exact / relaxed / 人工语义)+ 沙箱审计。合并 DB:core+rw+rsrc+net+poll+timeout+msg_ring+cancel+filetable facts + rsrc struct_layout。

## 结果
| gate | 类 | 沙箱 | schema | 字段存在性 | unconf | exactR | relaxR | 人工 |
|---|---|---|---|---|---|---|---|---|
| read_fixed_file | param_align | ✓ | ✓ | pass | 0 | .67 | 1.00 | 正确 |
| fixed_buffer | param_align | ✓ | ✓ | pass | 0 | .00 | .00 | 正确 |
| net_fixed_buffer | param_align | ✓ | ✓ | pass | 0 | .33 | .33 | 正确 |
| msg_ring_fixed_file | param_align | ✓ | ✓ | pass | 0 | .33 | .33 | 正确 |
| cancel_fixed_file | param_align | ✓ | ✓ | pass | 0 | .67 | .67 | 正确 |
| filetable_slot | activation | ✓ | ✓ | pass | 0 | 1.00 | 1.00 | 正确 |
| poll_double | activation | ✓ | ✓ | pass | 0 | 1.00 | 1.00 | 正确 |
| kiocb_flags | premise | ✓ | ✓ | pass | 0 | 1.00 | 1.00 | 正确 |
| link_timeout | premise | ✓ | ✓ | pass | 0 | 1.00 | 1.00 | 正确 |
| poll_polled | premise | ✓ | ✓ | pass | 0 | 1.00 | 1.00 | 正确 |
| neg_thin_slice | NEG | ✓ | ✓ | pass | 0 | 1.00 | 1.00 | 保守未幻觉 |
| neg_writer_omitted | NEG | ✓ | ✓ | **FAIL** | **1** | .50 | .50 | 编造字段被守卫拦 |
| neg_adversarial | NEG | ✓ | **FAIL** | pass | 0 | .00 | .00 | 非法 source 被 schema 拦 |

## 关键发现
1. **沙箱 13/13 干净**:每个冷判读 `tool_uses = 1`(恰好读了自己那一个 bundle 文件),无一越界读其他文件/命令/网络。
2. **守卫在真实判读输出上触发,且恰在负控上**——这是 pilot 缺的证据:
   - **字段存在性门**在 `neg_writer_omitted`(写侧从切片/账移除)拦下判读**编造的结构字段** `io_uring_sync_cancel_reg.fd`;
   - **schema 门**在 `neg_adversarial`(对抗 prompt)拦下判读用了枚举外的 `source:"ledger"`。
   - **10 个正样本零守卫误报。** 即守卫在设计诱发压力处触发、在正常输入上不误伤。
3. **50 个 term 中仅 1 个 unconfirmed**:即使 `neg_adversarial` 在"尽量多列"下产出 8 项,引用的字段**都在语料里真实存在**(io_sr_msg.notif 等),没编造字段名——判读在字段层面**未泛化幻觉**。
4. **5 个 param_align 语义全部推对**(跨 5 子系统的跨调用对齐);判读常把对齐参数归到其**实际结构**(sqe / io_msg / io_cancel_data)而非 io_ring_ctx,比人工 GT 更细。
5. **exact-key 仍悲观**:`fixed_buffer` 语义正确却 exactR/relaxR 均 0.00(判读把分支条件归到 `io_kiocb.buf_index`,与 GT 的 `io_ring_ctx.nr_user_bufs` 键不匹配——**归属/评分粒度差异,非语义失败**);`read_fixed_file` 由 exact .67 经 relaxed 升到 1.00(align_target 文本差异被放宽键救回)。**人工语义仍是主指标**,与 pilot 一致。

## 定性判据
在 13 门控(10 正 + 3 负控,跨 5 子系统)上,冷 LLM 判读产出**语义正确**的门控谓词、含 5 个最难的 param_align,沙箱 100% 干净;**两道确定性守卫在设计诱发的负控上真实触发、在正样本上零误报**。The signal strengthens the pilot's read: treating the LLM as a credible primary judge for predicate inference, with ledger-reconciliation + schema validation as required guards that demonstrably fire on real fabricated/malformed output. Final role assignment and any accuracy claim beyond this small set require a larger, multi-model evaluation.

## 局限 / caveat
- **n=13、单模型、单会话冷启动、curated 切片**(非自动切片发现);结论为定性/机制信号,非统计结论。
- **沙箱是"读单一 bundle 文件 + 审计 tool_uses≤1"**(而非严格零工具内联)——在 13 门控规模上给出同等"判读只见该 bundle"的保证、成本低;findings 如实记此机制。
- **`neg_writer_omitted` 的 slice label 无意间泄露了实验意图**("register/writer side deliberately omitted"),判读据此标了不确定——该负控证明守卫拦幻觉有效,但其"自然诱发"不纯;正式规模应清洗切片标注。
- **exact-key 评分是悲观下界**;放宽键仅部分缓解字段归属差异;人工语义评分不可省。
- 守卫机制本身另有 Phase 1 单测(`unconfirmed` 用例)与本轮真实触发双重证据。

## 复现
gates/labels/bundles/runs/audit 逐门控落盘;13 个冷判读经 Agent 工具(读单一 bundle、tool_uses=1);校验/评分用 `implicitfuzz.pilot.{validate,eval}` + Phase 1 `reconcile`。无外部 API。
