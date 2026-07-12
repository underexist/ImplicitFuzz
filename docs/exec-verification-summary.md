# 执行验证闭环 — 正式总结 (execverify, 2026-07-12)

**定位:** 研究内容二("确定性装配→执行验证→置信度反馈")的入口最小闭环。补上整条链路此前最大的未验证假设——LLM 受限判读 / 事实账对账产出的门控谓词,能否被**真实内核执行**终审(经由预期对象状态触达目标深层分支)。

## 一、验证框架(可复用)
无 sudo 复用 `~/syzkaller-docker`:`/usr/libexec/qemu-kvm` + KVM boot KCOV+io_uring 6.1 内核 → 自建 **execprog-on-boot 极简 initramfs**(起 loopback、复用 staging RPC 兼容 static-pie `syz-execprog`+`syz-executor`、`-sandbox=none -cover -coverfile`)→ **per-call 原始 KCOV PC** 打串口 → `nm` 地址区间数各函数覆盖、正/负差分。`analyze_cover.py` 泛化到可配 signal 函数。

## 二、三类证据(4 个差分,全部 gate_execution_verified=true)
| 门控/谓词 | 隔离方式 | signal | 正 | 负 |
|---|---|---|---|---|
| fixed-buffer 前置状态门控 | register vs register+unregister | io_prep_rw | 8 | 3 |
| fixed-file 前置状态门控 | register vs register+unregister | io_read | 19 | 0 |
| param-align 值粒度(变索引) | 同注册 2 files,fd_index 1 vs 5 | io_read | 19 | 0 |
| param-align 计数翻转(变计数) | 同 fd_index=1,nr_args 2 vs 1 | io_read | 19 | 0 |
后两者双向控制 → 结果由跨调用关系 `fd_index < nr_args` 决定,非单一参数值。这正是 purpose.md 立论所系的"参数对齐"隐式依赖,被执行验证。

## 三、方法学(为什么可信)
- **差分消噪**:正/负仅在门控条件上差,register/submit 等公共覆盖抵消(io_sqe_*_register、io_submit_sqes 项 pos=neg 佐证),门控信号是语义差分而非跨-boot 抖动。
- **prog2c 先验字节**:boot 前用 `syz-prog2c` dump 最终写入内核的 SQE 字节(opcode/flags/fd_index)与 register nr_args,排除"测试输入没真正写进去"的疑问。
- **正确符号化**:必须用与所启 bzImage 同构建的 `out/kernel/vmlinux`(非 `kernel-src/vmlinux`,地址不同)。
- **未硬造**:每步 prog/串口/覆盖/字节/判据落 `execverify/`。

## 四、失败教训(踩坑清单)
1. `~/syzkaller/qemu-system-x86_64` 是 qemu-**user** 符号链接(拒 `-enable-kvm`)→ 用 `/usr/libexec/qemu-kvm`。
2. syz-execprog 起本地 manager,executor 回连 127.0.0.1 → **initramfs 必须起 loopback**。
3. execprog+executor **必须同构建**(RPC 版本校验);dynamic executor 缺 libstdc++ → 用 staging 的 static-pie 对。
4. coverfile 是 **per-call**(`/tmp/cover_prog<N>.<call>`);init 里 `head -30` 曾截断覆盖。
5. **符号化用错 vmlinux** → 误判"没覆盖到 io_uring"(耗时最久的坑)。
6. SQE 字段编码:第二个字段=iosqe flags(0x1=IOSQE_FIXED_FILE),先 prog2c 验字节再 boot。

## 五、适用边界(诚实)
- **验证的是门控效果(下游被 gate 的执行)**:`io_file_get_fixed` 被 inline(cov=0),故以其把关的 `io_read` 为 signal;`io_import_fixed` 深层导入在本 setup 未同步执行(异步 io-wq)。
- **覆盖即代理判据**(purpose §4.2(5) 别名风险);`CONFIG_KCOV_ENABLE_COMPARISONS` 未开;单内核、单子系统、非统计。
- harness 对"内核状态可变门控"(register/unregister、SQE 值)有效;需要精细 SQE 编码的更深分支(io_import_fixed 同步、OOB-buffer)按判断停止扩展——不增核心论证强度。

## 六、接回研究内容二
本块证明:门控谓词可被真实内核执行**终审**(满足→触达深层分支,违反→不触达),且跨调用参数对齐的因果关系可被差分覆盖隔离验证。这补齐了"确定性装配—执行验证—置信度反馈"闭环里"执行验证"环节的可行性证据,为后续把 LLM 判读/对账的不确定谓词交执行反馈淘汰/修正提供了落地基础。
