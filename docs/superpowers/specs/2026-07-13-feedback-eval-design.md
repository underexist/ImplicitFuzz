# 反馈闭环·小规模评估(冻结 MVP)设计

**Date:** 2026-07-13
**Branch:** `phase2b-evidence-identity` (worktree `feedback-loop`)
**Base commit:** `24c33ce`(置信度反馈闭环 MVP 完成)
**Scope:** 把"闭环能力演示"推进为"闭环在**冻结的真实 Phase 2 候选集**上的忠实覆盖范围 + 反馈状态分布"的量化证据。
**Status:** design approved(含四处收紧);待写实现计划。
**依据:** `docs/confidence-feedback-findings.md`、`docs/research-progress-overview.md`、13 gate 冷判读集(`src/implicitfuzz/pilot/gates/*.json` + `pilot/runs_formal/*.json`)。

---

## 0. 定位(准确边界)
本轮**量化 MVP 在冻结真实候选集上的忠实覆盖范围与反馈状态分布**,**不**证明通用 prog 合成、**不**证明统计/概率校准。冻结 MVP:不为评测结果修改任何模板。

## 1. 候选集(冻结)
13 个冷判读 gate,全部 `abstain=false`。剔除 3 个负控(cold-judge 去污染控制,非依赖候选)后 = **10 个真实正候选**。

| 字段关系族 | gate | target_function | op | 严格可执行? |
|---|---|---|---|---|
| `fd < nr_user_files` | **read_fixed_file** | io_file_get_fixed | READ | ✅ |
| | cancel_fixed_file | io_sync_cancel | — | ❌ op_not_covered |
| | msg_ring_fixed_file | io_msg_ring | — | ❌ op_not_covered |
| | filetable_slot | __io_fixed_fd_install | — | ❌ op_not_covered |
| `buf_index < nr_user_bufs` | **fixed_buffer** | io_prep_rw | READ_FIXED | ✅ |
| | net_fixed_buffer | io_send_zc_prep | — | ❌ op_not_covered |
| `io_kiocb.flags` premise | poll_polled / poll_double / link_timeout / kiocb_flags | (各异) | — | ❌ no_family_template |
| **负控(单列,引用既有结果)** | neg_writer_omitted / neg_thin_slice / neg_adversarial | — | — | 不纳入本次 10-candidate run |

## 2. 四处收紧(评审要点)

### 2.1 EXECUTABLE_GATES 用完整签名(不只函数名)
可执行判定 = 四元组同时匹配,而非仅 `target_function`:
```
(operation, target_function, field_relation, template_family)
```
```python
EXECUTABLE_GATES = {
  ("READ",       "io_file_get_fixed", "fd < nr_user_files",       "fixed_file"):   {"signal_function": "io_read"},
  ("READ_FIXED", "io_prep_rw",        "buf_index < nr_user_bufs", "fixed_buffer"): {"signal_function": "io_prep_rw"},
}
```
必须 op(READ/READ_FIXED)、target_function、param 关系**同时**匹配才判可执行。**仅看 `io_prep_rw`/`io_file_get_fixed` 会把同函数的不同调用语境误判为可执行** —— 用完整签名杜绝(如 `io_prep_rw` 同时服务 READ_FIXED/WRITE_FIXED,仅凭函数名会把 WRITE 语境误判)。
**operation 的推导:** adapter 从 gate 的 activation `opcode == ...` term(如 fixed_buffer 的 `opcode == READ_FIXED || WRITE_FIXED`)或 target_function 的调用路径推导候选 op 集合;判可执行要求 `template.operation ∈ gate.operations`,**外加** target_function + field_relation + template_family 三者精确相等。无法确定 op 集合 → 不判可执行(保守)。不匹配 → `unsupported`,并给 `unsupported_reason`:
- `op_not_covered`:字段关系属 fd/buf 族但四元组不匹配(target fn/op 不在表内)→ cancel/msg_ring/net/filetable。
- `no_family_template`:非 fixed-file/buffer 族(flags)。

### 2.2 min activation confidence 的依据
`static_confidence = min` over the gate's **activation-class** terms,映射 `high→high, medium_high/medium→medium, medium_low/low→low`。
**依据:** activation predicate 是合取式(所有 activation 前提须同时成立才进入门控深处),整体置信**不高于最弱前提** → 取 min。
**硬约束:** 若候选**没有 activation term** → 报 `AdapterError`(schema/adapter 错误),**不得静默给默认档**。

### 2.3 指标分两层(避免"100% 验证率"错觉)
- **第一层 · 全部真实候选(10):** verified **2/10**、unsupported **8/10**、contradicted 0、undecidable 0。
- **第二层 · 严格支持且实际执行(2):** verified **2/2**。
- **必注明:** 这 2 个(read_fixed_file、fixed_buffer)**参与过模板开发**,故 2/2 是 **frozen replay(冻结重放)**,**不是 held-out 泛化结果**。两个数字并列呈现,严禁只报第二层。

### 2.4 rerank 只证明"执行证据成为新的排序依据"
10 个候选**原本全部 static-high**(activation 均 high)。执行后:2 个可执行且 verified → `execution_verified`(顶档);8 个 unsupported → 保持 high。
- 只表述为:**执行证据把 2 个 execution-confirmed 候选与 8 个 merely-static-high 候选区分开,成为新的排序依据**。
- **不得**表述为"整体排序质量已改善" —— unsupported 候选无 ground truth 可比较。
- **同档 tie-break:** 用**确定性**次级键 `candidate_id` 升序(rerank 增加稳定次级排序键;保持既有档序主键)。

## 3. 组件(以复用为主 + 一个新 adapter)
```
pilot gate(gates/*.json + runs_formal/*.json)
  ├─ A pilot_adapter.py(新): gate → feedback candidate
  │     · executability = 完整签名匹配 EXECUTABLE_GATES(§2.1)
  │     · static_confidence = min(activation terms)(§2.2;无 activation → AdapterError)
  │     · 输出 candidate: {candidate_id(=gate id), candidate_origin="replayed",
  │       gate_family, static_confidence, param_align.relation, signal_function,
  │       target_function, operation, unsupported_reason?}
  ├─ B 冻结快照 feedback/eval/frozen_candidates.json(§4): adapter 输出(10 正候选)落盘
  ├─ C run_feedback_eval.py(新, server): 载快照 → loop.run_loop(真实 executor)
  │     → 仅 2 个可执行者 boot(4 次 qemu);8 个 unsupported 不执行
  │     → calibrated view + 报告数据
  └─ D 报告 docs/confidence-feedback-eval.md(§5)
```
**复用:** `feedback/{update,assemble,loop}.py`、execverify harness、Phase 2 冷判读产物。**新建:** `pilot_adapter.py`、`run_feedback_eval.py`、frozen snapshot、eval 报告、overview 更新。**rerank 改动:** `update.py:rerank` 增加 `candidate_id` 次级键(§2.4),向后兼容,补 tie-break 单测。

## 4. 冻结快照(可复现 + 溯源)
`feedback/eval/frozen_candidates.json` 顶层记:
```
source_commit(生成时 HEAD sha)、gate_ids(纳入的 10 个)、adapter_version、
schema_version(predicate_schema 版本)、generated_hash(候选内容规范化后 sha256)、generated_date
```
→ 评测可复现;模板/内核变化后可重算并对比。**不因评测结果回改模板。**

## 5. 报告(docs/confidence-feedback-eval.md)
1. **模板覆盖率:** 2/10 严格可执行;unsupported 原因分布(op_not_covered ×4、no_family_template ×4)。
2. **反馈状态分布(两层, §2.3):** 全部真实候选 verified 2/10 + unsupported 8/10;严格支持且执行 verified 2/2(注明 frozen replay,非泛化)。
3. **rerank before/after(§2.4):** static-high 齐平 → 执行证据区分出 2 个 execution_verified;tie-break candidate_id;明确不主张整体排序质量改善。
4. **控制项(单列,引用 24c33ce):** ff_mutant → contradicted(淘汰能力,来自 `docs/confidence-feedback-findings.md`@24c33ce,**非本次 run**);neg_* 为冷判读 abstention 控制。**明确标注引用来源,勿使读者误以为属于本次 10-candidate run。**
5. **边界声明(§6)。**

## 6. 边界(严格保留;非目标)
两个门控家族(fixed_file/fixed_buffer)、单内核单子系统(linux-6.1 io_uring)、离散单调更新。**非目标:** 概率/统计校准、通用 prog 合成、per-op 模板、held-out 泛化评测、跨对象/容器槽位。

## 7. 测试与验收
- **单测:** `test_pilot_adapter.py` —— 完整签名可执行判定(正/负例含"同函数不同 op"误判防护)、min-activation 映射、无 activation → AdapterError、unsupported_reason 分类;`rerank` tie-break 确定性单测。
- **eval smoke:** stub executor 跑 run_feedback_eval(不 boot),校验分布计数 + 两层指标 + 溯源字段。
- **真实跑(server):** 10 候选 → 2 boot → calibrated eval view + 报告数据;phase1 regression ALL PASS;pytest 全绿。
- **诚实:** 两层指标并列、2/2 标 frozen replay;rerank 不越界表述;控制项引用 24c33ce;快照记溯源;模板未因结果回改。

## 8. 文件结构(实现以计划为准)
- `src/implicitfuzz/feedback/pilot_adapter.py` + `tests/test_pilot_adapter.py`
- `execverify/run_feedback_eval.py`
- `feedback/eval/frozen_candidates.json`、`feedback/eval/calibrated-eval-<date>.json`
- `docs/confidence-feedback-eval.md`;更新 `docs/research-progress-overview.md`(补 24c33ce + 闭环结论 + eval 指针)
- `src/implicitfuzz/feedback/update.py`(rerank 次级键)
