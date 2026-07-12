# Execution-Verification Closed-Loop 最小原型 设计

**Date:** 2026-07-12
**Branch:** `phase2b-evidence-identity`
**Scope:** 研究内容二入口 · "确定性装配→执行验证"的最小闭环(单门控)
**Status:** design approved; awaiting implementation plan (writing-plans)
**依据:** `docs/llm-predicate-formal-eval-findings.md`(复盘指出:执行验证闭环是当前最大未验证假设);purpose.md §4.2(4)(5)。

---

## 0. 立论
到目前为止,LLM 判读输出的真伪全靠**对账 + 人工语义评分**,**没有执行反馈**。purpose.md 的设计里,谓词的**终审是"送进真实内核执行、看是否经预期对象状态触达目标深层分支"**。本原型建这一环的最小版:用**差分 KCOV 覆盖**证明一个门控谓词满足时到达深层分支、不满足时不到达。**单门控、最小、复用现有环境。**

## 1. 复用的既有环境(不重建)
`~/syzkaller-docker`(用户 5–6 个月前搭建):
- KCOV+io_uring 的 Linux 6.1 内核:`out/kernel/bzImage`,`kernel-src/.config`(`CONFIG_KCOV=y`、`CONFIG_IO_URING=y`、`CONFIG_DEBUG_INFO_DWARF4=y`),`kernel-src/vmlinux`(符号化用);
- rootfs/initramfs(含 `syz-executor`)、`out/disk.img`;
- `syz-execprog`(`~/syzkaller/bin/linux_amd64/`)、本地 `qemu-system-x86_64`、世界可写 `/dev/kvm`(**无 sudo 可跑 KVM**);
- syzkaller `sys/linux/io_uring.txt` 描述(io_uring 系统调用可表达为 prog);
- 现成脚本 `scripts/{run-qemu.sh,run-syz-execprog.sh}`(initramfs 启动即在 VM 内跑 `syz-execprog -executor=/syz-executor ... /repro.syz`)。

## 2. 目标门控:READ_FIXED fixed-buffer
- 门控:`io_prep_rw`(rw.c:91)`if (req->buf_index >= ctx->nr_user_bufs) return -EFAULT;`
- 深层分支 = 通过边界检查后的 **`io_import_fixed`**(fixed-buffer 导入,rsrc.c)。
- 选它因为是最干净的单对象 param_align,且是反查/评测一路的旗舰。fixed-file 留作后续可加。

## 3. 组件

### A. Prog 装配(确定性,syzkaller prog 语法)
- **正 prog** `progs/read_fixed_pos.syz`:`io_uring_setup` → `io_uring_register(IORING_REGISTER_BUFFERS, N iovec)` → 提交 `IORING_OP_READ_FIXED` SQE(`buf_index < N`、合法 fd)→ `io_uring_enter`。**谓词满足。**
- **负 prog** `progs/read_fixed_neg.syz`:同结构但**不 register buffers**(→ `nr_user_bufs=0`,`buf_index>=0` 触发 -EFAULT),或 `buf_index` 越界。**谓词不满足。**
- 差分只应隔离**门控本身**:正/负仅在"是否 register + 索引是否越界"上不同,其余(setup、enter、fd)一致。

### B. 运行 wrapper `run_execprog.sh`(新,服务器)
把目标 prog 放到 initramfs 期望路径(`/repro.syz`)或经 `PROG=` 传入 → `run-qemu.sh` 启 qemu(KVM 加速)→ VM 内 `syz-execprog -executor=/syz-executor -cover=1 ...` 跑 prog → **抓 qemu 串口输出**(覆盖 PC / syz-execprog cover 输出)到文件。正、负各跑一次。

### C. 覆盖分析 `analyze_cover.py`(新)
- 从两次运行的串口输出解析覆盖 PC 集合(正 `P`、负 `Ng`);
- 用 `kernel-src/vmlinux` + `addr2line`(或 nm 地址区间)把 PC 符号化到函数;
- 计算差分 `P − Ng`,判定 **`io_import_fixed`(及 fixed 路径函数)的 PC 是否仅在 `P` 出现**;
- 输出判据:`gate_execution_verified = (target_func_pc in (P − Ng))`。

### D. smoke 门(第一步,先做,gated)
先 `boot + 跑一个平凡 prog(如单个 io_uring_setup)+ 确认串口有覆盖输出`——**验证 5–6 月前的 syzkaller-docker 环境仍可 boot 且覆盖管线出数**。若坏(qemu/镜像/initramfs 失效),先修/重建,**再进 A–C**。smoke 不过则原型阻塞,如实报。

## 4. 判据 / 产物
- **执行验证判据**:目标函数 PC ∈(正覆盖 − 负覆盖)→ 该门控谓词被真实内核执行验证(满足则到达、不满足则不到达)。
- 产物:`docs/exec-verification-findings.md`(判据结果 + 覆盖差分证据 + 环境/复现)+ 可复用的 `run_execprog.sh` / `analyze_cover.py` + 两个 prog。

## 5. 局限 / caveat
- **覆盖即代理判据**(purpose §4.2(5) 明确的已知别名风险):边覆盖只反映控制流痕迹,`io_import_fixed` 被覆盖≈到达该分支,但非状态门控被满足的严格证明;差分(正−负)缓解噪声。`CONFIG_KCOV_ENABLE_COMPARISONS` 未开,只有基本边覆盖。
- **单门控**(READ_FIXED fixed-buffer)、单内核构建、复用环境;非统计、非完整研究内容二。
- **环境 liveness**:依赖 5–6 月前的 syzkaller-docker;smoke 门前置。
- 无 sudo:qemu 用户态 + 世界可写 `/dev/kvm`;不碰宿主特权。

## 6. 硬约束
- 复用 `~/syzkaller-docker` 环境,不重建 kernel/rootfs(除非 smoke 证明必须)。
- 无外部特权;不改 ImplicitFuzz 已有 schema/对账/评测代码。
- 全程可审计:prog、串口输出、覆盖 PC、符号化、差分判据逐项落盘。

## 7. 文件结构(实现以计划为准)
- 新 `execverify/`:`progs/read_fixed_pos.syz`、`progs/read_fixed_neg.syz`、`run_execprog.sh`、`analyze_cover.py`、`RUNBOOK.md`。
- `docs/exec-verification-findings.md`。
- 实验产物(串口日志/覆盖)落 `execverify/out/`(可选择性提交)。
