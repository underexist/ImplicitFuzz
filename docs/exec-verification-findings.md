# Execution-Verification 最小原型 — findings / status (2026-07-12)

**Branch:** `phase2b-evidence-identity`
**Spec/plan:** `docs/superpowers/{specs,plans}/2026-07-12-exec-verification-prototype*.md`

## 状态:harness 打通,oracle 未触发(诚实中间态)

### ✅ 已打通(硬基建,可复用)
在无 sudo、复用 `~/syzkaller-docker` 环境下,**执行验证 harness 端到端跑通**:
- `/usr/libexec/qemu-kvm` + KVM boot KCOV+io_uring 6.1 内核(`out/kernel/bzImage`);
- 自建 **execprog-on-boot 极简 initramfs**(`execverify/initramfs/init` + `build_initramfs.sh`):起 loopback、复用 staging 的 RPC 兼容 static-pie `syz-execprog`+`syz-executor`、`-sandbox=none -cover -coverfile`;
- **原始 KCOV PC 落盘**(`/tmp/cover_prog<N>.<call>`,per-call)、打串口、`analyze_cover.py` 解析+addr2line 符号化+差分。
- getpid smoke:`cover=15` 真实 PC;io_uring 正/负 prog 都能执行并产出 per-call 覆盖(pos/neg 各 ~2400 唯一 PC)。

### ✗ 未触发(实验层,需继续调)
差分 oracle 尚未把 `io_import_fixed` 从 `pos − neg` 里隔出来。逐 coverfile 符号化发现:**任何 coverfile 都不含 io_uring 函数**(连 io_uring_enter 那次调用的 188 PC 也解析成通用/setup 函数,无 `io_submit_sqes`/`io_read`/`io_import_fixed`)。
→ 结论:**手写的 READ_FIXED prog 没有真正执行到 io_uring 读路径**——SQE 提交/ring 装配没触发 read op。这需要 io_uring prog 内部调试(SQE 结构、fd 合法性、ring/setup 参数、submit 语义),属 syzkaller 专门经验。

### 两个已定位的实验设计问题(已部分修正)
1. **差分被 register 开销/跨 boot 噪声淹没**:初版负控"不 register"→ 差分=register 的 mm 覆盖(mlock/pmd/remap);改成"register+unregister"后 register mm 抵消,但**整程覆盖跨两次 VM boot 有 ~2400 PC 级非确定性**,门控信号(几个 io_import_fixed PC)被噪声埋没。正解:**只比 io_uring_enter 那一次调用的 per-call coverfile**,并用多次运行取交集降噪。
2. **prog 未触发 read op**(上面):最关键,需先让 io_uring_enter 真跑到 io_import_fixed,差分才有意义。

## 下一步(建议,交用户 syzkaller 经验)
1. 让 READ_FIXED prog 真执行读路径:核对 `syz_io_uring_submit` 是否正确写 SQE 到 sqes_ptr、`io_uring_enter(to_submit=1, GETEVENTS)` 是否提交、fd(0x3)是否可读、addr 是否在注册缓冲区内;可先用用户既有的可用 io_uring prog(如 `~/syzkaller-docker/out/kasan-cve-gate/initramfs/cve.syz` 是跑通的 WRITE_FIXED)验证覆盖里能出现 io_uring 函数,再改 READ_FIXED。
2. per-call 差分 + 多次运行取交集(降 boot 噪声)。
3. `io_import_fixed` 可能 inline → 退查 `io_prep_rw`/`io_read` 邻近 PC。

## caveat
harness 真,oracle 未证;覆盖即代理判据;单门控;跨 boot 非确定性需控。**未硬造任何"验证成功"结果。**
