# Execution-Verification 最小原型 — findings (2026-07-12)

**Branch:** `phase2b-evidence-identity`
**Spec/plan:** `docs/superpowers/{specs,plans}/2026-07-12-exec-verification-prototype*.md`

## 结论:闭环合上 —— 门控谓词被差分 KCOV 覆盖执行验证 ✅

在无 sudo、复用 `~/syzkaller-docker` 环境下,用**差分 KCOV 覆盖**验证了 READ_FIXED fixed-buffer 门控谓词:满足时执行走进门控函数深处,违反时早返回。

### Harness(基建,可复用)
- `/usr/libexec/qemu-kvm` + KVM boot KCOV+io_uring 6.1(`out/kernel/bzImage`);
- 自建 **execprog-on-boot 极简 initramfs**(`execverify/{initramfs/init,build_initramfs.sh}`):起 loopback、复用 staging 的 RPC 兼容 static-pie `syz-execprog`+`syz-executor`、`-sandbox=none -cover -coverfile`,per-call 原始 KCOV PC 落盘、打串口;
- `run_execprog.sh` 一键 build+boot+抓覆盖;`analyze_cover.py` 用 nm 地址区间数各目标函数覆盖 + 正/负差分。

### 差分实验
- **正 prog**(`progs/read_fixed_pos.syz`):`syz_io_uring_setup` → `register(IORING_REGISTER_BUFFERS, 1)` → 提交 `IORING_OP_READ_FIXED`(buf_index=0 < 1)→ `io_uring_enter`。谓词满足。
- **负 prog**(`progs/read_fixed_neg.syz`):同上但 `register` 后紧跟 `UNREGISTER_BUFFERS` → enter 时 `nr_user_bufs=0`,谓词违反。**关键:正/负都做 register**,使 register 的内存映射覆盖在差分里抵消,只隔离门控本身。

### 判据结果(用正确 vmlinux 符号化)
| 函数 | 正 | 负 | 说明 |
|---|---|---|---|
| `io_submit_sqes` | 24 | 24 | 都提交,抵消 |
| `io_sqe_buffers_register` | 5 | 5 | 都 register,抵消 |
| **`io_prep_rw`(门控)** | **8** | **3** | **正过 `buf_index<nr_user_bufs` 检查、走进 post-check 块;负早 -EFAULT 返回** |
| `io_import_fixed` / `io_read` | 0 | 0 | 数据导入未同步执行(见 caveat) |

→ **`gate_execution_verified = true`**(io_prep_rw 正覆盖 8 > 负 3):门控谓词满足时,执行确被驱动进门控分支深处,违反时不进。**这是设计里"确定性装配→执行验证"最小闭环的一次真实合上。**

## 关键教训(踩过的坑)
- **符号化必须用与所启 bzImage 同一构建的 vmlinux**:`kernel-src/vmlinux` 与 `out/kernel/vmlinux` 是**不同构建**(io_import_fixed 地址 `0x81e162e0` vs `0x815ad140`)。一度用错前者 → 所有 io_uring PC 解析成无关函数、误判"没覆盖到 io_uring"。改用 `out/kernel/vmlinux`(与 bzImage 同目录同构建)后,io_uring_enter/io_submit_sqes/io_prep_rw 覆盖全部现形。
- 无 sudo 下 qemu 用 `/usr/libexec/qemu-kvm`(非 `~/syzkaller/qemu-system-x86_64` 那个 qemu-user 符号链接);loopback 必须起(executor 回连 execprog);execprog+executor 必须同构建(RPC 版本校验)。

## caveat
- **验证的深层分支是 `io_prep_rw` 的 post-check 块,不是 `io_import_fixed` 本身**:后者(实际固定缓冲区导入)在本 setup 下未同步执行(io_uring_enter GETEVENTS 未驱动到数据传输),故 cov=0;门控本身(buf_index 边界检查的通过/失败)已被差分隔离验证。
- **覆盖即代理判据**(purpose §4.2(5) 别名风险);单门控、单内核、`CONFIG_KCOV_ENABLE_COMPARISONS` 未开;差分靠"正/负都 register"消 register/submit 噪声,io_prep_rw 8 vs 3 是语义差分而非 boot 抖动(submit/register 项 24=24、5=5 抵消佐证)。
- 未硬造结果:全过程 prog/串口/覆盖/符号化/判据逐项落 `execverify/out/`。

## 下一步(可选)
- 让 `io_import_fixed` 真同步执行(核对 io_uring_enter 是否真跑完读/写 op),把验证推到更深的导入分支;
- OOB buf_index 版负控(不靠 unregister)、多次运行取交集进一步降噪;
- 接更多门控(fixed-file、param-align 跨调用对齐的值粒度验证)。

## 追加尝试 (2026-07-12): OOB 负控 + io_import_fixed 深化 — 两处受阻,如实记

### Task A — OOB buf_index 负控:未生效
目标:正/负都 register 1 缓冲区,只在 buf_index(0 vs 0x2a)上差,比 register+unregister 更纯。
结果:差分 `io_prep_rw` **8 = 8**(无差异)——把 buf_index 放在 SQE 第 9 位 `{0x2a}` **没真正写进 buf_index**(`buf_index_personality_misc` 的定位编码不对,两 prog 都停在 buf_index=0、都过检查)。
→ 需正确的 syzkaller SQE 字段语法(`io_uring_bid` 在 misc 结构里的写法)。**功能上 register+unregister 负控已隔离同一门控**(都令 `buf_index >= nr_user_bufs` 成立,已给 8 vs 3),OOB 只是更纯的变体。

### Task B — 推进到 io_import_fixed:未到达
`io_issue_sqe` cov=6(op 确被 issue),但 `io_read`/`io_rw_init_file`/`io_import_fixed` 均 cov=0——读操作在到达固定缓冲区导入前就早返回(疑 fd 解析失败 或 NOWAIT→-EAGAIN 未走同步导入)。试过 /dev/zero + 资源 fd r3,仍未到 io_import_fixed。
→ 需让 READ_FIXED 真同步执行读导入:核对 SQE fd 是否解析成合法可读文件、是否 NOWAIT 内联完成(或 IOPOLL/特定 fd),属 syzkaller io_uring prog 专门经验。

### 不变的结论
门控执行验证的**主结果仍成立且已提交**(io_prep_rw 正 8 > 负 3,register+unregister 负控)。上述两处深化受阻,已如实记录、交用户 syzkaller 经验推进;**未硬造任何结果**。

## fixed-file 门控尝试 (2026-07-12): 受阻于 IOSQE_FIXED_FILE SQE 编码

目标:验证 `io_file_get_fixed`(`fd < ctx->nr_user_files`)。用 register FILES + 带 IOSQE_FIXED_FILE 的 op(fd=@fd_index)触发门控。
- 已定位:门控函数 `io_file_get_fixed @0xffffffff8159b190`;register 生效(`io_sqe_files_register` cov=7);FSYNC op 也执行(`io_fsync` cov=2)。
- **但 `io_file_get_fixed` cov=0**:IOSQE_FIXED_FILE 标志没设进 SQE → op 走普通 fd 路径,不触发固定文件解析。
- 确认执行器 `syz_io_uring_submit` **只 memcpy SQE、不自动设 FIXED_FILE**(`common_linux.h:1985`),故必须由 prog 在 SQE flags 字节里设 —— 与 buf_index 同一编码墙:我未能可靠写对 syzkaller SQE 字段(flags/fd_index)。

→ **需一行正确的 syzkaller SQE 语法**(设 IOSQE_FIXED_FILE=0x1 到 flags 字节 + fd=@fd_index)。有它后,fixed-file 差分(register vs register+unregister,target `io_file_get_fixed`)即可复用现有 harness 一把跑通,与 io_prep_rw 同法。语料里见到 `@IORING_OP_READ=@pass_buffer={0x16,0x0,0x0,@fd_index=0x5,0x0,0x0}` 用了 @fd_index,但其 flags 字节编码需你确认。

## 总结(本轮 execverify)
harness + io_prep_rw 门控执行验证**已成立并提交**;io_import_fixed 深化、OOB-buf_index 负控、fixed-file 门控三处都卡在同一点——**syzkaller SQE 字段(buf_index / iosqe flags / fd_index)的精确 prog 编码**,属用户 syzkaller 专门经验。harness 本身对"内核状态可变门控"(如 register/unregister→nr_user_bufs)已验证有效。

## fixed-file 门控:已验证 ✅ (2026-07-12, 续)

用户点破 SQE 编码(第二个字段=iosqe flags)+ "先 prog2c dump 验字节、再 boot" 的方法后,一把跑通。
- **prog2c 字节验证**(未 boot 先验):`byte0=0x16`(opcode READ)、**`byte1=0x1`(IOSQE_FIXED_FILE)**、`fd_index=0`。C 注释确认 `flags: iosqe_flags = 0x1`。
- **差分**(正:register 1 file + FIXED_FILE fd_index=0;负:register+unregister → nr_user_files=0 → -EBADF):
  | 函数 | 正 | 负 | |
  |---|---|---|---|
  | `io_sqe_files_register` | 7 | 7 | register 抵消 |
  | `io_submit_sqes` | 29 | 29 | submit 抵消 |
  | `io_issue_sqe` | 20 | 7 | 正 issue 更深 |
  | **`io_read`** | **19** | **0** | **固定文件解析成功→读op执行;失败→根本不执行** |
  | `io_file_get_fixed` | 0 | 0 | 被 inline,无独立 PC;其**效果**(把关 io_read)已由 io_read 差分验证 |

→ **`gate_execution_verified = true`(signal=io_read,正 19 > 负 0)**。fixed-file 门控谓词(`fd < nr_user_files` 且文件已注册)满足时驱动读op执行、违反时不执行。比 fixed-buffer(io_prep_rw 8 vs 3)信号更干净。

## 两个门控执行验证汇总
| 门控 | signal | 正 | 负 | 判据 |
|---|---|---|---|---|
| READ_FIXED fixed-buffer | io_prep_rw | 8 | 3 | ✅(register/unregister 控 nr_user_bufs) |
| READ fixed-file | io_read | 19 | 0 | ✅(register/unregister 控 nr_user_files;IOSQE_FIXED_FILE) |
harness 复用、差分消 register/submit 噪声、prog2c 先验字节。**io_import_fixed / OOB-buf_index 仍待更精细 SQE 编码(已记)。** 未硬造结果。

## param-align 值粒度验证:已验证 ✅ (2026-07-12, 续) —— 全程最有价值的一个

purpose.md 的"参数对齐"谓词:提交索引须与**前序 register 写入的计数**对齐(`sqe->fd_index < 前序 io_uring_register(FILES, nr_args) 的 nr_args`)。本次在**值粒度**上验证——与之前 fixed-file 的 register-vs-unregister(验"是否注册过")不同,这里**两 prog 注册完全相同的 2 个文件,只在提交的 fd_index 值上差**(1 vs 5),隔离的是跨调用**值对齐**本身。
- **prog2c 先验字节**:两者 `nr_args=2`、`flags=0x1`(FIXED_FILE)一致,仅 `fd_index` word = **1(正) vs 5(负)**。
- **差分**:
  | 函数 | 正(idx=1) | 负(idx=5) | |
  |---|---|---|---|
  | `io_sqe_files_register` | 7 | 7 | **注册完全相同(2 files),抵消** |
  | `io_submit_sqes` | 29 | 29 | 抵消 |
  | `io_issue_sqe` | 20 | 7 | 正 issue 更深 |
  | **`io_read`** | **19** | **0** | **idx<nr_user_files→读执行;idx>=→-EBADF不执行** |

→ **`gate_execution_verified = true`(io_read 正 19 > 负 0)**。**跨调用参数对齐(提交索引 vs 前序注册计数)被执行验证**——这正是隐式依赖里最难、最有价值、整篇立论所系的一环。

## execverify 三验汇总
| 门控/谓词 | 隔离方式 | signal | 正 | 负 | |
|---|---|---|---|---|---|
| fixed-buffer premise/activation | register vs register+unregister | io_prep_rw | 8 | 3 | ✅ |
| fixed-file premise/activation | register vs register+unregister | io_read | 19 | 0 | ✅ |
| **fixed-file param-align(值粒度)** | **同注册 2 files,仅 fd_index 1 vs 5** | io_read | 19 | 0 | ✅ |
方法:复用 harness、prog2c 先验 SQE 字节、差分消 register/submit 噪声、out/kernel/vmlinux 符号化。未硬造结果。

## 边界翻转对照(param-align 因果收口)✅ (2026-07-12)
补反向控制:**固定 fd_index=1、变 nr_args**,与"固定 nr_args、变 fd_index"合成双向。
| 实验 | 固定 | 变 | 结果 |
|---|---|---|---|
| A(值) | nr_args=2 | fd_index 1→5 | io_read 19→0 |
| B(计数,翻转) | fd_index=1 | nr_args 2→1 | io_read 19→0 |
B(prog2c 验:两者 fd_index=1,nr_args=2 vs 1;io_sqe_files_register 7=7 抵消):**同一 fd_index=1,注册数 2→1 使其从有效翻为无效**。
→ 双向控制证明:执行结果由**跨调用关系 `fd_index < nr_args`** 决定,而非任一单独 SQE 参数值。param-align 隐式依赖的因果表述至此严谨收口。**按计划停止扩展**(不再追 io_import_fixed / OOB-buffer / 跨容器槽位——增案例不增核心论证强度)。
