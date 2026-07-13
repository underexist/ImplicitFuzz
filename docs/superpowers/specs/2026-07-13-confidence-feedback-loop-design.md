# 置信度反馈闭环(最小版)设计

**Date:** 2026-07-13
**Branch:** `phase2b-evidence-identity`
**Scope:** 研究内容二 · "确定性装配—执行验证—置信度反馈"闭环的最小内部反馈机制
**Status:** design approved (含三处收紧); awaiting implementation plan
**依据:** `docs/research-progress-overview.md`(§5 缺口);`docs/exec-verification-summary.md`(已验 harness + 4 差分);Phase 1/2 置信档与候选。

---

## 0. 定位
把 execution verification 从"末端证明工具"升级为**系统内部反馈机制**:执行结果反过来校准候选依赖的置信档 + 重排。单向流水线 → **推断—验证—校准**闭环。**最小、可审计、科学表述谨慎。**

## 1. 范围决策(已定)
- **模板化已验门控 + 重放已提交候选**:复用 execverify 的 fixed-file/fixed-buffer 家族作 prog 模板,从候选 param_align 项参数化 register 数/索引;不接 live-LLM、不做通用 prog 合成。
- **离散档 + 单调升降**:静态档(对账+LLM 置信→high/med/low)之上,执行结果提/降档。

## 2. Outcome 枚举(四态,收紧要点)
`verified / contradicted / undecidable / unsupported`。**不用 `falsified`**——KCOV 是执行代理,`contradicted` 更诚实。
- **verified**:执行按候选**声称方向**成立(claimed-satisfy 用例 signal 显著 > claimed-violate 用例)。
- **contradicted**:执行出现**明确反方向证据**(claimed-satisfy 用例 signal 显著 < claimed-violate 用例)。仅"方向相反"才判 contradicted。
- **undecidable**:模板支持且**实际执行了**,但信号不可分(`|pos−neg|` 在噪声内,即 `pos≈neg`)、信号不可见或运行失败 → 无法判断。**pos≈neg 归此,不降档。**
- **unsupported**:装配器**无对应模板、尚未执行**(非 fixed-file/buffer 家族)。与 undecidable 语义不同(未执行 vs 执行了不可判),必须分开记原因。

## 3. 置信档更新规则(反馈核心)
| outcome | 动作 | final 档 |
|---|---|---|
| verified | 提档 | `execution_verified`(顶) |
| contradicted | 降档 | `rejected` |
| undecidable | 维持 + flag(原因:noise/invisible/run-fail) | 静态档 |
| unsupported | 维持 + flag(原因:no template) | 静态档 |

## 4. 合成负控(收紧要点)
为演示 `contradicted→降档`,从**已验证谓词生成可执行 mutant**:
```
真实关系: fd_index < nr_args
错误候选(mutant): 声称 fd_index >= nr_args 才进入 io_read
```
其用例应现明确反方向证据:声称的正样本(fd_index>=nr_args)`io_read=0`、声称的负样本(fd_index<nr_args)`io_read=19` → claimed-pos < claimed-neg → **contradicted**。
- **只证闭环具备淘汰错误候选的能力**;`candidate_origin: synthetic_negative_control`,**不计入真实候选的准确率/校准统计**。
- 若 mutant 用例只得 `pos≈neg`(无信号)→ 归 undecidable,**不直接降档**。

## 5. calibrated view(收紧要点:不覆盖原始)
原始静态结果**不可变**;另生成 calibrated view,记最小溯源:
```
candidate_id, candidate_origin, static_confidence, execution_status,
final_confidence, template_family, template_version, kernel_build_identity,
signal_function, pos_coverage, neg_coverage, evidence_run_manifest, reason
```
→ 随时回答"为何提/降档";模板或内核变化后可重算。

## 6. 架构:复用为主 + 新反馈核心
```
候选(重放已提交, 带谓词 + 静态档; + 合成负控)
  ├─ A assemble.py: 家族模板 → 参数化 register 数/索引, emit (pos_prog, neg_prog); 非家族 → unsupported
  ├─ B execute(复用 execverify): run_execprog + analyze_cover(signal), prog2c 先验字节
  ├─ C update.py: classify_outcome(signal_pos, signal_neg, margin) → 四态; update_confidence(static, outcome) → final 档 + reason
  └─ D loop.py: 编排 + 重排(按 final 档) + 持久化 calibrated view(原始不变)
```
- **复用**:execverify harness(qemu/KCOV/run_execprog/analyze_cover/prog2c)、Phase 1 档语义、已提交候选。
- **新建**:`src/implicitfuzz/feedback/{assemble,update,loop}.py` + `calibrated/`(view 落盘)+ findings。

## 7. 最小演示(覆盖三种更新动作即可)
- 真实候选 `verified`→提档(fixed-file/buffer param-align,io_read 正>负);
- 合成负控 `contradicted`→降档(mutant,反方向证据);
- 真实非模板候选 `unsupported`→维持+标记。
`undecidable` **仅用单元测试覆盖规则**(pos≈neg → undecidable、不降档);将来真实运行自然产生时再纳入 findings。

## 8. 测试与验收
- **单测(纯 Python)**:`classify_outcome`(verified/contradicted/undecidable 的 sign+margin 判定,含 pos≈neg→undecidable)、`update_confidence`(四态→档映射)、`assemble`(家族参数化 + 非家族→unsupported)、重排。
- **真实闭环跑**:3 候选(真实 verified / 合成负控 contradicted / 真实 unsupported)→ 复用 execverify 执行 → calibrated view + findings 表(静态档→outcome→final 档 + 重排 + 溯源)。
- **回归**:`run_phase1_regression.sh` ALL PASS;`pytest` 全绿。
- **诚实**:未硬造;合成负控标注、不计真实统计;`contradicted` 仅方向相反;calibrated 不覆盖原始。

## 9. 明确不做(YAGNI)
live-LLM、通用 prog 合成、fixed-file/buffer 以外模板、数值置信/收敛曲线(离散档先行)、单内核单子系统、跨对象/容器槽位。

## 10. 文件结构(实现以计划为准)
- `src/implicitfuzz/feedback/{__init__,assemble,update,loop}.py`
- `tests/test_feedback_{update,assemble,loop}.py`
- `feedback/candidates/*.json`(重放候选 + 合成负控,标 origin)、`feedback/calibrated/*.json`(view)
- `docs/confidence-feedback-findings.md`
