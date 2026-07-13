# ImplicitFuzz 研究进展总述 (2026-07-12, branch phase2b-evidence-identity)

**一句话:** 已建成一条从"依赖反查 → LLM 受限判读 → Phase 1 事实账对账 → Phase 2 冷判读评测 → 真实内核执行验证 → 参数对齐因果证明"的端到端链路;其中 `8993015` 补上最关键一环——不只证明生成结果与静态答案一致,更证明**推断出的隐式依赖确实控制真实内核路径**,并以双向控制严谨证明结果取决于跨调用关系 `fd_index < nr_args` 而非任一孤立参数值。

链路:`依赖反查 → LLM 推断 → Phase 1 对账 → Phase 2 pilot → 正式评测 → 执行验证 → 参数对齐因果证明`

---

## 1. 研究问题:隐式 syscall 依赖为何难发现和利用
内核模糊测试以系统调用序列为接口,能否触达深层代码取决于是否满足调用间依赖。**显式依赖**(句柄/fd 经参数返回值传递)可在接口层观测;**隐式依赖**——一组调用在参数/返回值上无数据传递,却因先后读写**同一内核对象的内部状态**而相互约束——其载体隐藏于内核实现内部,是现有方法的薄弱环节。

立论例(io_uring READ_FIXED):准入条件 `ctx->file_table != NULL ∧ sqe->buf_index < ctx->nr_user_files`,门控字段由前序 `io_uring_register(REGISTER_FILES, n)` 写入。此类门控的困难有二:① 比较操作数是**对象内部字段**(非当前调用参数),KCOV_CMP 回填不到;② 是**跨调用参数对齐约束**(提交索引须小于前序注册数),超出参数级变异能力。

现有四路线的局限:语法驱动止于单调用;资源依赖止于接口可观测数据流;状态感知(StateFuzz/SyzTrust)把状态作事后不透明反馈信号、不可读不可反向构造;LLM 端到端生成的修复停留规约整体、幻觉无可追溯约束。**两点根本局限**:表示层——多字段联合隐式依赖缺可追溯、可承载不确定性的表示;生成层——缺把不确定依赖转为确定构造、并字段级归因容错的机制。

## 2. ImplicitFuzz 提出了什么:三要素推断链路
以**可追溯访问证据库**为枢纽,派生两视图(显式依赖偏序 + 对象状态门控),把深层分支准入表达为**字段谓词合取(状态门控)**,并"先门控、后反查"得到隐式前序依赖。谓词分三类,对应隐式依赖三要素:
- **状态前提 (premise)**:前序调用把对象置于某状态(如已注册文件表);
- **调用顺序**:显式依赖偏序 + 反查证据库定位前序写调用;
- **参数对齐 (param_align)**:提交参数须与前序调用写入的值对齐(最难、最有价值)。

LLM 作**受限判读员**在两处介入(补候选关联、推门控谓词),受**三道校验**约束(字段存在性对账 / schema / 可合成性),产出带来源+置信、留待执行反馈淘汰修正。

## 3. 每个阶段证明了什么
| 阶段 | 承担的证据 | 关键结果 |
|---|---|---|
| **依赖反查** (`gate_prior_write_candidate`) | 门控→前序依赖可**静态跨 TU 派生** | 真实语料 19 条跨 TU 命中(io_kiocb.flags/iov_iter.count 等) |
| **LLM spike** | 定位真瓶颈 | 瓶颈是 numeric-only 字段的**字段存在性对账**,非 LLM 本身 |
| **Phase 1 对账层** | "事实账防幻觉"红利可落地 | name→offset 解析 + 三态校验;旗舰 `nr_user_files@160` **confirmed_numeric**、编造字段 **unconfirmed** 拦下 |
| **Phase 2 pilot + 正式评测** | LLM 受限判读**可信**、守卫**真拦幻觉** | 13 门控冷判读,沙箱 13/13 干净;param_align 语义全推对;**守卫恰在负控触发**(field-existence 拦编造结构字段、schema 拦非法枚举),10 正样本零误报;exact-key 评分悲观、人工语义为真信号 |
| **执行验证** | 隐式依赖**真控真实内核路径** | 4 差分全 verified;fixed-buffer(io_prep_rw 8>3)、fixed-file(io_read 19>0)、param-align 值粒度(同注册、fd_index 1vs5,19>0)、**计数翻转**(同 fd_index、nr_args 2vs1,19>0) |

**分工一句话:** 静态对账证"字段可追溯 + 防幻觉";正式评测证"LLM 判读可信 + 守卫有效";真实执行证"推断出的依赖真控内核路径"。

## 4. 核心结论有多强
**已验证(强证据):**
- 门控字段(含 numeric-only)可被事实账**确定性对账**(单测 + 真实数据双证)。
- 冷判读在最难的 **param_align** 上产出语义正确谓词;**对账 + schema 双守卫在真实判读输出上真实拦下幻觉/非法**(负控触发、正样本零误报)。
- 推断出的隐式依赖(状态前提 + 参数对齐)**执行验证真控内核路径**;param-align 经**双向控制**(固定 nr_args 变 fd_index;固定 fd_index 变 nr_args,均 io_read 19→0)证明结果由跨调用关系 `fd_index < nr_args` 决定,非任一孤立参数值——这是研究内容二的核心实证。

**仍为可行性证据(未达强/统计):**
- LLM 判读:n=13、单模型、单会话冷启动、curated 切片 → "credible primary-judge candidate",最终角色未定(需 multi-model + 大评测)。
- 执行验证:**覆盖即代理判据**(别名风险)、单内核单子系统;`io_file_get_fixed` 被 inline(以 io_read 为门控效果 signal),`io_import_fixed` 深层导入未同步执行。
- 人工语义评分无独立校验者(GT 作者/评分员/判读模型同族)。

## 5. 下一阶段缺什么:置信度反馈闭环
当前是**单向推断流水线**,执行验证只作末端证明工具。缺的是把执行结果(通过/失败/不可判定)**反馈校准 LLM 依赖置信**:
```
LLM 候选依赖 → 静态证据评分 → 生成正负执行用例 → KCOV 验证 → 更新置信度/候选排序
```
这把 execution verification 从"论文末端证明"升级为**系统内部反馈机制**,把单向流水线升级成"**推断—验证—校准**"闭环——更接近论文核心贡献。multi-model eval 押后(增模型层稳健性/广度,不直接增方法本身)。

## 6. 置信度反馈闭环:已建成 + 小规模评估(2026-07-13)
**§5 的闭环已实现并冻结为 MVP**(`24c33ce`)。四态 outcome(verified/contradicted/undecidable/unsupported)、离散档单调升降、模板化正负 prog(fixed_file 值粒度 / fixed_buffer 状态)、executor 注入、calibrated view 不覆盖原始。三种更新动作已在真实 qemu/KCOV 上各验一例:真实候选 **verified→execution_verified**、合成负控 **contradicted→rejected**、非模板 **unsupported→维持**(详 `docs/confidence-feedback-findings.md`)。

**小规模评估**(`docs/confidence-feedback-eval.md`):把"能力演示"推进为"在冻结的 10 个真实 Phase 2 候选上的效果证据"。
- **忠实模板覆盖 2/10**;unsupported 8/10 = op_not_covered ×4(同字段关系族、目标 op 未覆盖)+ no_family_template ×4(flags 族)。
- **两层指标(并列,防"100% 验证率"错觉):** 全部真实候选 verified 2/10;严格支持且执行 verified 2/2——后者**注明为 frozen replay,非 held-out 泛化**(两候选参与过模板开发)。
- **rerank:** 10 候选原本齐平 static-high,执行证据把 2 个 execution-confirmed 升至顶档,成为**新的排序依据**;不主张整体排序质量改善(unsupported 无 ground truth),同档 candidate_id 确定性 tie-break。
- **边界严格保留:** 两家族、单内核单子系统、离散更新——**尚非概率校准**。评测未回改模板;冻结快照记 source_commit/hash/adapter/schema 溯源。

**定位:** 本轮量化的是 MVP 在真实候选集上的**忠实覆盖边界与反馈状态分布**,而非通用合成或统计校准效果。

---
*证据落盘:`docs/{phase2-status,ledger-reconciliation-phase1-findings,llm-predicate-pilot-findings,llm-predicate-formal-eval-findings,exec-verification-findings,exec-verification-summary}.md`;代码 `src/implicitfuzz/{evidence,reconcile,pilot}`、`extraction/`、`execverify/`;spec/plan 于 `docs/superpowers/`。*
