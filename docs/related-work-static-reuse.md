# 静态分析可复用工作评估 (Static Analysis Reuse Evaluation)

本文件记录对成熟静态分析工作/技术的复用评估,重点回答两个问题:**能力边界是什么**,以及**在本项目"访问证据库 → 双视图"架构里对应哪一层**。评估结论用于技术选型与开题相关工作论证,避免以后重新查证。

**贯穿性判断(先说结论):** 静态别名/调用图这一类工作,复用价值几乎都落在**对象身份地基**(证据库的对象身份等价表、显式依赖视图的 fd/容器档),而**不是**本项目的 novelty——字段级状态门控(`branch_fact` + 受限 LLM)。夯身份地基是安全的基础工作;论文的真风险在门控+LLM+验证闭环。选型时要清醒区分:某工作是帮你"夯地基"还是"上门控"。

---

## Unias — A Hybrid Alias Analysis (USENIX Security 2023, UC Riverside / seclab-ucr)

**是什么:** 面向 Linux 内核的混合别名分析。CFL-reachability(精确半)+ 基于类型的 shortcut edge(可扩展半),whole-kernel 规模(吃 `vmlinux.bc`),原始应用是全局变量 `ro_after_init` 保护。**建在 SVF 之上**(用 `SVFIR`/`GepStmt`/`getSVFStmtSet(SVFStmt::Gep)`),与本项目同源。仓库:`seclab-ucr/Unias`(另有 `learjet5/Unias-repro` 复现优化版)。

### 字段敏感性(实测源码,决定性问题)

`src/UniasAnalysis.cpp::processGEPedges` 的判定逻辑:

```cpp
if(gepedge->isVariantFieldGep() || !gepedge->isConstantOffset()){
    fdinsensitiveShortcuts[sttype].insert(edge);          // 字段不敏感,坍缩
}else{
    typebasedShortcuts[sttype][gepedge->accumulateConstantOffset()].insert(edge);  // 按 struct×常量偏移,字段敏感
}
```

- **常量字段偏移 → 字段敏感。** 区分 `ctx->file_table` 与 `ctx->nr_user_files`(同 struct 两个常量偏移)。→ **本项目的单对象状态门控它扛得住。**
- **变量/数组下标偏移 → 字段不敏感,坍缩成一档。** `file_table[fd]` 按下标取槽位会命中 `isVariantFieldGep()`。

**关键对齐:** Unias 坍缩的边界(数组下标/容器)**精确重合于本项目设计中已写明"静态别名在数组下标处断裂、交给 LLM 补"的那条线**(见 `docs/purpose.md` 研究内容一关于跨对象容器耦合的论述)。即 Unias 的能力上界与本项目的静态/LLM 分工分界一致——它不会在预期它能干的地方掉链子,也不替你解决你本就打算交给 LLM 的部分。**字段敏感性不构成否决理由。**

### 复用成本(校准后)

- **同源但有版本差:** Unias 是 SVF pass,但目标是较老的 SVF/LLVM 14.0.6。本项目是 SVF-on-LLVM-21.1.8。
- **API 可移植性已验证:** Unias 依赖的 SVF 原语(`isConstantOffset` / `accumulateConstantOffset` / `isVariantFieldGep` / `getSVFStmtSet`)在本项目的 SVF-21 构建里**全部存在**(`svf/include/SVFIR/SVFStatements.h:643/659/670`)。→ 把"类型 shortcut 边"技术**移植**到 SVF-21 是 API 可行的,不用换工具链。
- **硬伤(README caveat,方向对本项目不利):**
  - "Currently only works on **-O0**, no special handling for multiindices GEP"
  - "Updates about GEP edge **byteoffset** are fixing and coming soon"
  - 本项目整条链是 **-O2 -g**,产出 byte-offset GEP——正是他们承认"还在修、没做完"的那种。**你要的那部分正好是他们没交付的部分。**
- **真价值需要重投入:** Unias 的价值是 whole-kernel scale(跨 TU 闭合),要拿到它得有全内核 bitcode + 移植 shortcut 技术,是正经子项目,不是接库。

### 定位与结论

- **定位:跨 TU 的对象级身份**(喂证据库的 fd/容器身份档,缩小 LLM 补关联的面)。**给不了字段级门控。**
- **近期(零工程量,直接进开题):** 引 Unias 作为静态别名档的**方法学背书**——说明本项目三档身份判定里"容器/访问路径档"有贴内核的成熟工作支撑(SyzGen++ 之外的第二个支撑点),且其"常量字段敏感 + 数组下标坍缩"的能力线印证了本项目静态/LLM 分工的合理性。
- **中期(条件触发):** 若预实验显示跨 TU 对象身份召回过低、LLM 补关联负担过重,再考虑把类型 shortcut 边技术移植到 SVF-21 换 whole-kernel scale。别低估工作量。
- **不建议:** 现在就建 LLVM-14 + 全内核 -O0 bitcode 跑其 binary——你要的 -O2 那半他们没做完,投入产出比差。

---

## KallGraph — Redefining Indirect Call Analysis (IEEE S&P / Oakland 2025, UC Riverside)

**是什么:** Unias 同组(Guoren Li / Manu Sridharan / Zhiyun Qian)的续作,面向间接调用分析。指出纯类型分析(MLTA 那类,Unias 内部也用)既有 soundness 漏洞(漏目标)又过保守(假目标多),把类型方法转成**混合指针分析**(指针追踪 + 类型方法统一),在全内核(Linux 5.15 / 6.5)上给出**更小更准**的间接调用目标集。仓库:`seclab-ucr/KallGraph`。

**技术栈与 I/O(读源码确认):**
- **LLVM 14.0.6 + SVF-2.5**(仓库内附 patched 版)。和 Unias 同版本;与本项目 SVF-on-LLVM-21 有版本差。
- 输入:whole-kernel 的 per-file `.bc` 列表(`bc.list`),用 **MLTA 的 IRDumper** 编内核 IR 生成。
- 输出:一张 **callgraph 文件**(函数级)。→ **比 Unias 更"可导入":按"调用点→被调函数名"对齐,函数名+源码位置跨 LLVM 版本/优化档稳定**,当外部 oracle 用比 Unias 的 GV 查询接口顺手得多。

**定位:** 全内核间接调用解析 → 喂证据库的**调用图完整性**、以及(对 io_uring 关键的)**syscall/opcode → handler 归属**。同样是**地基层(调用图),不是门控层**。

### ⚠️ io_uring 特定发现:最关键的那条间接调用不需要 KallGraph

调查中实测 io_uring 的 IR,得到一个改变结论的事实:

- `io_issue_sqe` 确实通过 `io_op_defs[opcode]` 做间接调用(`->prep`/`->issue` 派发)。在 `io_uring.c` 里 `@io_op_defs = external ... [0 x %struct.io_op_def]`——**external、size 0,per-TU 解不出**(= 之前实测 io_uring.c 33 条间接调用 0 解析的原因)。
- 但在 `opdef.c` 的 bitcode 里,`io_op_defs` 是**完全可读的编译期常量初始化器**:`[49 x { i8,i8,i16,[4xi8], ptr(name), ptr(prep), ptr(issue), ptr(prep_async), ptr(cleanup), ptr(fail) }]`,49 个 opcode 逐条列明 handler(`@io_nop_prep/@io_nop`、`@io_prep_rw/@io_read`、`@io_prep_rw/@io_write`、`@io_sendmsg_prep/@io_sendmsg` …)。

**结论:io_uring 里最有价值的那条间接调用(opcode 派发 = entry_fact 的骨架 = syscall 中心证据库的前提)是一张 const 静态表,直接读初始化器就能精确、无假阳地拿到全部 49 个 opcode→handler 映射。不需要 KallGraph、不需要 whole-kernel bitcode、不需要指针分析,用现有 LLVM-21 栈单读 `opdef.c` 即可。** 这本质是 DIFUZE/Syzkaller 静态分析里"读 op/ioctl 分发表"的标准套路。

由此 KallGraph 对 io_uring 的边际价值收窄为:只覆盖**残余间接调用**——存在堆对象里的回调(io_wq work、poll、task_work 等)。而这些里,tracepoint(`__traceiter_*`,运行时注册)静态原则上解不出,其余大多正是你设计里划给 LLM 补关联的部分。

### 结论

- **近期不建议为 io_uring 复用 KallGraph。** 关键路径(opcode 归属)用 const 表读取更便宜更准;残余回调要么静态解不出、要么按设计归 LLM。
- **本次调查真正 surface 出的近期高价值动作,不是"用 KallGraph",而是给抽取器加一个 const 分发表读取器**:读 `opdef.c` 的 `io_op_defs` 初始化器 → 产出 49 条 opcode→(prep/issue/cleanup)handler 映射 → 填 `entry_fact`(`entry_kind` 枚举里已有 `uring_cmd` 等)→ 解锁 access_fact 到 opcode 的归属。小、精确、LLVM-21 原生、在关键路径上(两个视图都是 syscall 实例图)。`call_fact.resolution_method` 枚举现为 `direct/mlta/pta`,const 表解析需新增一个取值(如 `const_table`)或复用 `mlta`。
- **KallGraph 作为条件-未来选项**:若将来要覆盖堆存回调(io_wq/poll/task_work)这一残余带、且不想全交给 LLM,它是 SoTA 且输出可直接导入;但要付 whole-kernel bitcode + LLVM-14/SVF-2.5 的成本(与 Unias 共享,见下)。

---

## 综合:三者对比与共享前置成本

| 工作 | 层级 | 对 io_uring 的近期价值 | 复用成本 |
|---|---|---|---|
| **Unias** (别名) | 对象身份档(fd/容器) | 低(方法学背书为主);-O2 byte-offset 是其 WIP | 高:whole-kernel bc + 移植 shortcut 技术到 SVF-21 |
| **KallGraph** (间接调用) | 调用图 / opcode 归属 | 低(关键那条用 const 表更省);只补残余回调 | 高:whole-kernel bc(MLTA IRDumper)+ LLVM-14/SVF-2.5 |
| **const 分发表读取器**(本项目自建) | entry_fact / opcode 归属 | **高,在关键路径上** | **低:单读 opdef.c,LLVM-21 原生** |

**共享前置成本提示:** Unias 与 KallGraph 都需要"全内核 LLVM-14 bitcode(经 MLTA IRDumper)"这同一个重前置。若将来决定走 whole-kernel 路线,**一次 bitcode 投入可同时喂两者**(它们共享 LLVM-14/SVF/MLTA 工具链),届时一起上更划算。但当下两者都非关键路径。

**总判断(呼应本文件开头):** 这一类 UCR/seclab 的成熟内核静态工作,复用价值都在"夯对象身份/调用图地基",而本项目 novelty 在字段级状态门控(`branch_fact` + 受限 LLM)。对 io_uring 而言,地基上真正卡关且高价值的两件事——**opcode→handler 归属**、**跨 TU 对象身份**——前者用 const 表读取即可低成本解决(不需要这些重工具),后者才是这些工具的用武之地、但属条件-未来。**结论不变:近期该往"上门控"走,地基只补 opcode 归属这一件低成本关键项。**
