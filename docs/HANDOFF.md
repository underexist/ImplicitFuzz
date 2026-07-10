# ImplicitFuzz 继续开发 — 交接提示词 (HANDOFF)

> 给接手本项目的新 agent。自包含:靠本文件即可接手,不重做已完成工作、不踩已知坑。
> 起手先读 `docs/phase2-status.md` 和 `docs/purpose.md`,跑一遍回归确认环境,再动手。

## 项目与目标
ImplicitFuzz 是"基于内核对象隐性依赖的智能化模糊测试"研究实现,目标子系统是 linux-6.1/io_uring。
核心方法(见 `docs/purpose.md`,务必先读):以**可追溯的访问证据库**为枢纽,派生两个视图——
① 显式依赖视图(生产者-消费者偏序),② 对象状态机视图/状态门控(隐式依赖);
静态分析产出高置信事实,LLM 在静态闭合不了处补候选并推门控谓词(受三道校验约束),
再由确定性装配 + 真实执行验证收口。当前工作全在"研究内容一"(证据库 + 两视图),尚未触及 LLM 层和研究内容二。

## 服务器与工作流(关键)
- 权威代码在服务器:`ssh xujunru@localhost -p 19999`,目录 `/home/xujunru/ImplicitFuzz`。
  内核源码 `/home/xujunru/linux-6.1`。LLVM 21.1.8 + SVF(on LLVM21)工具链只在服务器上。
- 若 SSH 报 "Connection refused",直接重试即可(偶发,不是隧道问题)。
- 本地 Mac 仓库 `/Users/junyuxu/Projects/ImplicitFuzz` 和 GitHub `github.com/underexist/ImplicitFuzz`
  已与服务器同步到同一分支同一 commit;但**服务器没登录 GitHub,不能从服务器 push**。
- 工作流:本地编辑文件副本 → `scp` 到服务器 → 在服务器上 build/test/commit。
  同步到 GitHub:服务器 commit 后,本地 `git fetch server && git merge --ff-only` 再 `git push origin`
  (本地已配 remote `server = ssh://xujunru@localhost:19999/home/xujunru/ImplicitFuzz`)。
- **别裸调 clang++/二进制**:工具链 .so 不在默认路径。用现成脚本(见"回归/验证"),它们处理了 LD_LIBRARY_PATH。
  手动构建时:
  `env -u LD_LIBRARY_PATH LD_LIBRARY_PATH="/home/xujunru/.local/opt/llvm21-rpm/usr/lib64:/home/xujunru/.local/opt/llvm21-rpm/usr/lib64/llvm21/lib64" cmake --build extraction/build -j"$(nproc)"`
  (cmake 还需 `export LLVM_DIR=.../llvm21/lib64/cmake/llvm` 和 `export SVF_DIR=/home/xujunru/implicitfuzz-toolchain/SVF/verify-llvm21-build4`)。
- 通过 SSH 提交时,commit message 含反引号/括号会被 shell 解析炸掉——用 `git commit -F <file>`,别用 heredoc 内联。

## 当前分支状态
分支 `phase2b-evidence-identity`(名字已过时,实际覆盖 2B/2C/2D + 地基升级),服务器/本地/GitHub 三处同步。
先跑 `git log --oneline -25` 看历史。**先读这些文档,不要重做已完成工作**:
- `docs/phase2-status.md` —— 鸟瞰总览 + 代码功能总结(端到端流水线、6 组件、能力快照)。**最先读这个。**
- `docs/purpose.md` —— 研究设计原文(立论、两个研究内容、技术路线)。
- `docs/phase1-findings.md` —— Phase 1 能力矩阵 + "Next Steps"清单(带 [done]/[next] 标记)。
- `docs/phase2c-object-identity-refinement.md` —— 对象身份 tier-1/tier-2。
- `docs/phase2d-state-gates.md` —— branch_fact + gate_seed_candidate 状态门控骨架。
- `docs/related-work-static-reuse.md` —— Unias/KallGraph 复用评估 + io_op_defs 是常量表的发现。
- `docs/superpowers/plans/2026-07-10-opcode-entry-and-branch-facts.md` —— 已执行的实现计划。
- `extraction/RUNBOOK.md` —— 所有回归/smoke 入口。

## 已完成(不要重做)
证据库现产出 5 类抽取事实 + 派生表:
- `access_fact`(字段级访问,~35% DWARF 符号化)、`call_fact`(直接 + 间接调用)、
  `alias_fact`(Andersen 多对象 points-to)、`entry_fact`(io_op_defs 49 个 opcode→handler)、
  `branch_fact`(条件/switch 分支 ↔ 被门控字段)。
- 派生:`evidence_node`/`evidence_edge`(state_write_read / object_identity[含 pointsto 交集] / lifecycle / explicit_dep 候选边)、
  `gate_seed_candidate`(状态门控静态骨架)。
- 对象身份:tier-1 直接指针链 + tier-2 SVF points-to 精化(global/formal_param/allocation_site;
  ~14% access 达到 identity 级 allocation_site+global,26 文件语料)。

## 回归/验证(动代码后必须全绿)
- 服务器:`extraction/scripts/run_phase1_regression.sh` → 应打印 `[phase1] ALL PASS`(tiny + kernel case1/2/3/5 + BTF)。
- 服务器:`python3 -m pytest tests/` → 23 passed。
- 全链路 smoke:`python3 extraction/scripts/ingest_phase1_smoke.py` + `python3 extraction/scripts/build_evidence_graph_smoke.py`。
- 内核 bitcode 已预生成:`/tmp/io_uring_*.bc`(timeout/cancel/kbuf/opdef)和 `/tmp/implicitfuzz-io_uring-all/bc/*.bc`(全 26 文件)。

## 硬约束与坑(务必遵守)
1. **SVF 的 `getLLVMValue()`/`getObjectNode()` 只有 assert 保护**,Release 构建 assert 被编译掉 → 在真实大 TU 上直接段错误。
   调用前必须先 `hasLLVMValue()`/`has*()` 检查。(为此修过一次 io_uring.c 段错误。)
2. **Schema 只能加不能改**:`schema_version` 恒为 "1.0.0",只加可选字段/枚举值。`validate_facts_schema.py` 自动 glob 所有 schema。
3. **per-TU 是分析硬边界**:Andersen/points-to/间接调用解析都在单个 .bc 内;26 文件是并集不是 whole-program。
   跨 TU 闭合按设计是留给 LLM 的,别去堆重型 whole-program 静态(Unias/KallGraph 属条件-未来,见 related-work 文档)。
4. **formal_param 是"关系证据"不是身份级**(v3 §5.6):不能单独判为同一对象,必须经 points-to 精化到 allocation_site/global。
5. **`gate_seed_candidate` 是派生表,不是 schema 绑定的 `gate_seed_fact`**;后者留给 LLM 补完谓词后的成品。
6. golden 检查断言的是具体 fact 不是精确总数;但新增 fact 类型可能触发过宽的旧检查(修过 `svf_node_id` 那条,已限定到 call/access fact)。
7. 4-TU smoke DB 里 `entry_fact=0`,因为默认 ingest 集不含 opdef.c(entry_fact 由 kernel case5 单独验证)——不是 bug。
8. 遵守 `CLAUDE.md`;不要重做已 [done] 的 Phase 1/2A/2B/2C/2D。

## 设计架构定位(我们在哪)
```
访问证据库 (evidence DB) ........................ 已建
  ├─ 访问事实表 ................................. done
  ├─ 对象身份等价表 (fd/容器/访问路径 三档) ...... 部分 — 访问路径 + points-to(容器) 档 done;fd 档未做
  ├─ 入口元信息表 (entry) ....................... done(io_uring 49 opcode)
  └─ alias/分配释放/字段元信息 .................. present
显式依赖视图 (alloc≺read/write/free 偏序) ........ 薄 — 仅 alloc≺free,受 per-TU 限
对象状态机视图 / 状态门控 ........................ 静态骨架 done (branch_fact + gate_seed_candidate)
LLM 受限判读 + 反查 + 研究内容二 ................. 未开始  ← novelty 的另一半和真风险
```

## 下一步任务(建议按此推进)
1. **反查(纯静态/SQL)**:把 `gate_seed_candidate` 的被门控字段 join 到语料里对该字段的 write `access_fact` →
   得到"设置该状态的前序调用"候选,作为该分支的隐式前序依赖。单 TU 内可先做,跨 TU 是重点。
2. **LLM 受限判读(novelty 核心,项目真风险)**:先小规模验证可行性——在几个人工标好的门控上,
   让 LLM 在代码切片约束下推谓词(premise/activation/param-align 三类,尤其"参数对齐"类),
   经三道校验(字段存在性对账 fact 账 / schema / 可合成性)过滤幻觉。先量准确率再决定 LLM 是主力还是辅助。
3. (地基,低优先)fd-indirection 身份档;残余堆存回调的 whole-program 解析(KallGraph,条件触发)。

## 分支同步速查
- 本地 remotes:`origin` = GitHub(可 push),`server` = `ssh://xujunru@localhost:19999/home/xujunru/ImplicitFuzz`(不能被 push,因为它是 checked-out 非裸仓库)。
- 服务器 commit 后同步:本地 `git fetch server && git merge --ff-only server/phase2b-evidence-identity && git push origin phase2b-evidence-identity`。
- 核对三处一致:比对 `git rev-parse` / `git ls-remote origin` / 服务器 `git rev-parse HEAD` 的 SHA。
