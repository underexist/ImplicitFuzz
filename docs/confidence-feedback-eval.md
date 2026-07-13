# 反馈闭环·小规模评估(冻结 MVP)Report

**Date:** 2026-07-13
**Kernel build identity:** `bzImage@b364f28d1fcf`(linux-6.1 io_uring, KCOV)
**Frozen snapshot:** `feedback/eval/frozen_candidates.json` — source_commit `963eb2e`, generated_hash `c694ba3aadaf`, adapter_version 1.0, schema_version 1.0
**Run artifact:** `feedback/eval/calibrated-eval-2026-07-13.json`
**Spec/Plan:** `docs/superpowers/specs/2026-07-13-feedback-eval-design.md` · `docs/superpowers/plans/2026-07-13-feedback-eval.md`

## 定位
量化置信度反馈闭环 MVP(完成于 `24c33ce`)在**冻结的 10 个真实 Phase 2 冷判读候选**上的**忠实覆盖范围与反馈状态分布**。**不**证明通用 prog 合成、**不**证明统计/概率校准。冻结 MVP:评测未修改任何模板。

## 候选集(冻结,10 个真实正候选)
13 个非弃权冷判读 gate,剔除 3 个负控(去污染控制,非依赖候选)= 10 个真实候选。可执行性由**完整签名** `(operation, target_function, field_relation, template_family)` 严格判定,仅函数名不足。

| # | candidate | target_function | field_relation_family | 严格可执行 | unsupported_reason |
|---|---|---|---|---|---|
| 1 | **read_fixed_file** | io_file_get_fixed | fixed_file | ✅ READ | — |
| 2 | **fixed_buffer** | io_prep_rw | fixed_buffer | ✅ READ_FIXED | — |
| 3 | cancel_fixed_file | io_sync_cancel | fixed_file | ❌ | op_not_covered |
| 4 | msg_ring_fixed_file | io_msg_ring | fixed_file | ❌ | op_not_covered |
| 5 | filetable_slot | __io_fixed_fd_install | fixed_file | ❌ | op_not_covered |
| 6 | net_fixed_buffer | io_send_zc_prep | fixed_buffer | ❌ | op_not_covered |
| 7 | poll_polled | io_arm_poll_handler | (flags) | ❌ | no_family_template |
| 8 | poll_double | io_poll_remove_entries | (flags) | ❌ | no_family_template |
| 9 | link_timeout | io_prep_async_work | (flags) | ❌ | no_family_template |
| 10 | kiocb_flags | __io_req_complete_post | (flags) | ❌ | no_family_template |

## 1. 模板覆盖率
**严格可执行 = 2/10。** unsupported 原因分布:`op_not_covered` ×4(fd/buf 字段关系族相同但目标函数/op 不在模板表内——执行它们需要 per-op prog,即被冻结的通用合成器)、`no_family_template` ×4(flags 族,无对应模板)。

## 2. 反馈状态分布(两层,并列报告)
> 两层必须并列,避免"100% 验证率"错觉。

- **第一层 · 全部真实候选(10):** verified **2/10**、unsupported **8/10**、contradicted 0、undecidable 0。
- **第二层 · 严格支持且实际执行(2):** verified **2/2**。
  - **注:** read_fixed_file、fixed_buffer **参与过模板开发**,故 2/2 是 **frozen replay(冻结重放),不是 held-out 泛化结果**。

执行差分(真实 qemu/KCOV boot,`bzImage@b364f28d1fcf`):

| candidate | signal | pos_cov | neg_cov | outcome | final |
|---|---|---|---|---|---|
| read_fixed_file | io_read | 19 | 0 | verified | execution_verified |
| fixed_buffer | io_prep_rw | 8 | 3 | verified | execution_verified |

两者均为已验差分的闭环重放:read_fixed_file 为 param-align 值粒度(fd_index 1 vs 5),fixed_buffer 为状态门控(register vs register+unregister,差分较小但方向明确,margin 5 ≥ 2)。

## 3. rerank before/after
10 个候选**原本全部 static-high**(activation 置信均 high;static tier = min over activation terms)。

| 序 | before(静态档,tie-break candidate_id) | after(执行后校准) |
|---|---|---|
| 1 | cancel_fixed_file | **fixed_buffer** ⬆ |
| 2 | filetable_slot | **read_fixed_file** ⬆ |
| 3 | fixed_buffer | cancel_fixed_file |
| 4 | kiocb_flags | filetable_slot |
| 5 | link_timeout | kiocb_flags |
| … | …(其余 static-high) | …(其余保持 high) |

**解读(严格限定):** 执行证据成为**新的排序依据**——把 2 个 execution-confirmed 候选与 8 个 merely-static-high 候选区分开,升至顶档 `execution_verified`。
**不主张**整体排序质量已改善:8 个 unsupported 候选无 ground truth 可比较;同档使用确定性 tie-break(candidate_id 升序)。

## 4. 控制项(引用 `24c33ce`,非本次 run)
以下来自 `docs/confidence-feedback-findings.md`@24c33ce 的既有结果,**不属于本次 10-candidate run**,仅供参照闭环的完整能力:
- **ff_mutant**(合成负控,`synthetic_negative_control`)→ **contradicted → rejected**(claimed-satisfy io_read=0、claimed-violate io_read=19,反方向证据),证明闭环具备淘汰错误候选能力;**不计入本轮真实统计**。
- **neg_writer_omitted / neg_thin_slice / neg_adversarial**:冷判读 abstention 去污染控制,非依赖候选,不纳入本轮候选集。
- **undecidable**:本轮真实运行未出现,仅由单测覆盖规则(pos≈neg / 运行失败 → undecidable、不降档);将来真实产生时再纳入。

## 5. 边界(严格保留;非目标)
- 两个门控家族(fixed_file / fixed_buffer);单内核单子系统(linux-6.1 io_uring);离散单调更新。
- **非目标:** 概率/统计校准、通用 prog 合成、per-op 模板、held-out 泛化评测、跨对象/容器槽位。

## 溯源与可复现
冻结快照记 `source_commit / gate_ids / adapter_version / schema_version / generated_hash / generated_date`;run artifact 记 kernel_build_identity + 每候选 pos/neg 覆盖 + reason。模板或内核变化后可重算并对比。**评测未因结果回改模板。**

## 结论
在冻结的 10 候选真实集上,MVP 的**忠实模板覆盖为 2/10**;严格支持的 2 个候选均 execution-verified(frozen replay);其余 8 个被诚实标为 unsupported(4 op_not_covered + 4 no_family_template)并保持静态档,闭环**未产生任何虚假降档**。这是"闭环在真实候选集上的效果证据",其增量在于**量化了忠实覆盖边界与反馈状态分布**,而非扩展工程功能或主张统计校准。
