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
