# Execution-Verification Closed-Loop 最小原型 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用差分 KCOV 覆盖在 qemu 里证明 READ_FIXED fixed-buffer 门控谓词满足时到达 `io_import_fixed`、不满足时不到达——闭合"确定性装配→执行验证"最小环。

**Architecture:** 复用 `~/syzkaller-docker` 的 KCOV+io_uring 6.1 内核 + qemu + syz-execprog;新建一个**execprog-on-boot 极简 initramfs**(init 在 boot 时对烧入的 prog 跑 `syz-execprog -cover` 并把覆盖打到串口、然后 poweroff),避开 SSH/manager 复杂度;正/负两个 prog 差分,`analyze_cover.py` 符号化 PC + 求差 + 判据。

**Tech Stack:** qemu-system-x86_64 + KVM(`/dev/kvm` 世界可写,无 sudo)、syzkaller `syz-execprog`/`syz-executor`、busybox initramfs(cpio)、Python(覆盖分析)、`addr2line`/`nm`(符号化)。全部在服务器 `~/ImplicitFuzz/execverify/`。

## Global Constraints

- **复用 `~/syzkaller-docker` 环境,不重建 kernel/rootfs**(除非 smoke 证明必须):内核 `out/kernel/bzImage`(+ `kernel-src/vmlinux` 符号化)、`syz-execprog`(`~/syzkaller/bin/linux_amd64/`)、`syz-executor`(`~/syzkaller-docker/out/initramfs/syz-executor` 或 `~/syzkaller/bin/linux_amd64/`)、`qemu-system-x86_64`(`~/syzkaller/qemu-system-x86_64`)、`/dev/kvm`。
- **判据 = 差分覆盖**:目标函数 `io_import_fixed` 的 PC ∈(正 prog 覆盖 − 负 prog 覆盖)→ 门控谓词被执行验证。
- **覆盖即代理判据**(purpose §4.2(5) 已知别名风险;`CONFIG_KCOV_ENABLE_COMPARISONS` 未开,仅基本边覆盖)——findings 如实标注。
- **smoke 门前置**:环境 5–6 月前建,Task 1 先证还能 boot、Task 2 先证覆盖管线出数;不过则原型阻塞、如实报,不硬造结果。
- 无 sudo:qemu 用户态 + KVM;不碰宿主特权。每步产物(prog/串口日志/覆盖 PC/符号化/判据)落 `execverify/out/`。
- 服务器工作流:本地编辑 → scp → 服务器跑/`git commit -F`。qemu boot 用 `timeout` 包裹防挂死。

参考 spec:`docs/superpowers/specs/2026-07-12-exec-verification-prototype-design.md`。

---

### Task 1: 环境 liveness smoke（内核能否 boot）

**Files:** Create `execverify/RUNBOOK.md`(记录确认到的可用调用)

- [ ] **Step 1: 定位内核 + qemu + 一个已有 initramfs**

```bash
ssh -p 19999 xujunru@localhost 'D=/home/xujunru/syzkaller-docker/out; ls -la $D/kernel/bzImage $D/kernel/vmlinux $D/initramfs.cpio.gz; ls -la /home/xujunru/syzkaller/qemu-system-x86_64 /dev/kvm'
```
Expected: bzImage(或 vmlinux)、initramfs.cpio.gz、qemu、/dev/kvm 均存在。

- [ ] **Step 2: boot(60s 超时,抓串口)**

```bash
ssh -p 19999 xujunru@localhost 'cd /home/xujunru/syzkaller-docker && \
  timeout 60 /home/xujunru/syzkaller/qemu-system-x86_64 -enable-kvm -m 1024 -smp 1 \
  -kernel out/kernel/bzImage -initrd out/initramfs.cpio.gz \
  -nographic -serial mon:stdio -append "console=ttyS0 root=/dev/ram rdinit=/init nokaslr" \
  -no-reboot > /tmp/smoke_serial.log 2>&1; echo "exit=$?"; tail -20 /tmp/smoke_serial.log'
```
Expected: 串口出现 Linux boot 日志 + init 运行痕迹(如 `MANAGER_INIT_READY` 或网络配置行)。**若 KVM 报错**:去掉 `-enable-kvm` 重试(TCG 慢但可用);若内核 panic/不 boot,记录并转"环境修复"(超出本原型,如实报阻塞)。

- [ ] **Step 3: 记录可用调用到 RUNBOOK,提交**

把确认可用的 `qemu` 命令(内核路径、是否 `-enable-kvm`)写进 `execverify/RUNBOOK.md`。
```bash
git add execverify/RUNBOOK.md && git commit -F <msg>   # "docs(execverify): env-liveness smoke — qemu boots KCOV kernel"
```

---

### Task 2: execprog-on-boot 极简 initramfs + 覆盖管线 smoke

**Files:** Create `execverify/initramfs/init`、`execverify/build_initramfs.sh`

**Interfaces:** Produces 一个 `execverify/out/initramfs-exec.cpio.gz`,boot 即对 `/repro.syz` 跑 `syz-execprog -cover` 并把覆盖打到串口、poweroff。

- [ ] **Step 1: 写 execprog-on-boot init**

`execverify/initramfs/init`:
```sh
#!/bin/sh
BB=/bin/busybox
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
$BB mkdir -p /proc /sys /dev
$BB mount -t proc proc /proc 2>/dev/null
$BB mount -t sysfs sys /sys 2>/dev/null
$BB mount -t devtmpfs dev /dev 2>/dev/null
$BB mount -t debugfs debug /sys/kernel/debug 2>/dev/null
echo "=== EXECVERIFY_START ===" > /dev/console
# syz-execprog runs the baked-in prog with coverage; print raw cover PCs.
/syz-execprog -executor=/syz-executor -cover=1 -collide=0 -threaded=0 -repeat=1 -procs=1 -debug /repro.syz > /dev/console 2>&1
echo "=== EXECVERIFY_DONE ===" > /dev/console
$BB sleep 1
$BB poweroff -f 2>/dev/null
while true; do $BB sleep 3600; done
```

- [ ] **Step 2: 写 build_initramfs.sh(把 busybox+syz-execprog+syz-executor+prog+init 打成 cpio)**

`execverify/build_initramfs.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
PROG=${1:?usage: build_initramfs.sh <prog.syz> <out.cpio.gz>}
OUT=${2:?}
STAGE=$(mktemp -d)
SD=/home/xujunru/syzkaller-docker/out
SYZBIN=/home/xujunru/syzkaller/bin/linux_amd64
mkdir -p "$STAGE/bin" "$STAGE/proc" "$STAGE/sys" "$STAGE/dev"
# busybox from the existing initramfs staging dir
cp "$SD/initramfs/bin/busybox" "$STAGE/bin/busybox" 2>/dev/null || cp "$(command -v busybox)" "$STAGE/bin/busybox"
cp "$SYZBIN/syz-execprog" "$STAGE/syz-execprog"
cp "$SD/initramfs/syz-executor" "$STAGE/syz-executor" 2>/dev/null || cp "$SYZBIN/syz-executor" "$STAGE/syz-executor"
cp "$PROG" "$STAGE/repro.syz"
cp "$ROOT/initramfs/init" "$STAGE/init"; chmod +x "$STAGE/init" "$STAGE/syz-execprog" "$STAGE/syz-executor" "$STAGE/bin/busybox"
( cd "$STAGE" && find . | cpio -o -H newc 2>/dev/null | gzip -9 ) > "$OUT"
echo "built $OUT ($(du -h "$OUT" | cut -f1))"
rm -rf "$STAGE"
```

- [ ] **Step 3: 覆盖管线 smoke(trivial prog)**

```bash
ssh -p 19999 xujunru@localhost 'cd /home/xujunru/ImplicitFuzz/execverify && \
  printf "%s\n" "io_uring_setup(0x1, &(0x7f0000000000)={0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, [0x0]})" > /tmp/trivial.syz && \
  bash build_initramfs.sh /tmp/trivial.syz out/initramfs-exec.cpio.gz && \
  timeout 90 /home/xujunru/syzkaller/qemu-system-x86_64 -enable-kvm -m 1024 -smp 1 \
    -kernel /home/xujunru/syzkaller-docker/out/kernel/bzImage -initrd out/initramfs-exec.cpio.gz \
    -nographic -serial mon:stdio -append "console=ttyS0 root=/dev/ram rdinit=/init nokaslr" -no-reboot \
    > out/smoke_cover.log 2>&1; grep -c "0xffffffff8" out/smoke_cover.log | xargs echo "cover PC lines:"; \
  grep -E "EXECVERIFY_START|EXECVERIFY_DONE|cover" out/smoke_cover.log | head'
```
Expected: 串口出现 `EXECVERIFY_START` → syz-execprog 执行(打印覆盖,含内核 PC `0xffffffff8...`)→ `EXECVERIFY_DONE`。**确认覆盖 PC 的输出格式**(`-debug` 下 syz-execprog 打印每个 call 的 cover 或 `cover: <n>`;若只有计数无 PC,则改用 `-coverfile=/cover.out` 并在 init 里 `cat /cover.out > /dev/console`——RUNBOOK 记录实际可用形式)。**smoke 不出 PC 则阻塞、如实报。**

- [ ] **Step 4: 提交**

```bash
git add execverify/initramfs/init execverify/build_initramfs.sh execverify/RUNBOOK.md
git commit -F <msg>   # "feat(execverify): execprog-on-boot initramfs + coverage smoke"
```

---

### Task 3: 正/负 READ_FIXED prog + 运行

**Files:** Create `execverify/progs/read_fixed_pos.syz`、`execverify/progs/read_fixed_neg.syz`、`execverify/run_execprog.sh`

- [ ] **Step 1: 写正 prog(register buffers + READ_FIXED 合法索引)**

`execverify/progs/read_fixed_pos.syz`(起草;Step 3 用 syz-execprog 校验并迭代到被接受):
```
r0 = memfd_create(&(0x7f0000000040)='a\x00', 0x0)
r1 = io_uring_setup(0x8, &(0x7f0000000000)={0x0, 0x0, 0x0, 0x2, 0x0, 0x0})
io_uring_register$IORING_REGISTER_BUFFERS(r1, 0x0, &(0x7f0000000100)=[{&(0x7f0000010000/0x1000)=nil, 0x1000}], 0x1)
io_uring_register$IORING_REGISTER_FILES(r1, 0x2, &(0x7f0000000200)=[r0], 0x1)
io_uring_enter(r1, 0x1, 0x1, 0x0, 0x0, 0x0)
```
(SQE 的 READ_FIXED/buf_index 由 syzkaller io_uring 描述里的提交 helper 表达;若该 syzkaller 版本无直接 READ_FIXED SQE pseudo-syscall,Step 3 迭代改用 `syz-prog2c` 可接受的写法或直接构造 sqe 内存。)

- [ ] **Step 2: 写负 prog(不 register buffers → 越界)**

`execverify/progs/read_fixed_neg.syz`:同上但**删除 `io_uring_register$IORING_REGISTER_BUFFERS` 行**(nr_user_bufs=0 → READ_FIXED 的 buf_index 检查早返回 -EFAULT)。其余(setup、files register、enter)与正 prog 逐字一致,确保差分只隔离 buffer 门控。

- [ ] **Step 3: 校验 prog 被 syz-execprog 接受**

在 VM 外用 `syz-execprog -debug` 干跑(不带 -cover,只验解析)或直接进 Task 2 harness 跑一次,确认 syz-execprog 不报 parse error、能执行到 io_uring_enter。**若 parse 失败,据报错迭代 prog 语法**(syzkaller prog 语法随版本变;以本机 `~/syzkaller/sys/linux/io_uring.txt` 描述为准)。RUNBOOK 记录最终可用 prog。

- [ ] **Step 4: 写 run_execprog.sh(build initramfs + boot + 抓串口)并跑正/负**

`execverify/run_execprog.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
PROG=${1:?}; TAG=${2:?}
bash "$ROOT/build_initramfs.sh" "$PROG" "$ROOT/out/initramfs-$TAG.cpio.gz"
timeout 120 /home/xujunru/syzkaller/qemu-system-x86_64 -enable-kvm -m 1024 -smp 1 \
  -kernel /home/xujunru/syzkaller-docker/out/kernel/bzImage -initrd "$ROOT/out/initramfs-$TAG.cpio.gz" \
  -nographic -serial mon:stdio -append "console=ttyS0 root=/dev/ram rdinit=/init nokaslr" -no-reboot \
  > "$ROOT/out/cover_$TAG.log" 2>&1 || true
echo "wrote $ROOT/out/cover_$TAG.log"
```
```bash
ssh -p 19999 xujunru@localhost 'cd /home/xujunru/ImplicitFuzz/execverify && \
  bash run_execprog.sh progs/read_fixed_pos.syz pos && \
  bash run_execprog.sh progs/read_fixed_neg.syz neg && \
  grep -c "0xffffffff8" out/cover_pos.log out/cover_neg.log'
```
Expected: 两个 log 都有覆盖 PC;正 log PC 数通常 ≥ 负 log(正走到更深)。

- [ ] **Step 5: 提交**

```bash
git add execverify/progs execverify/run_execprog.sh execverify/RUNBOOK.md
git commit -F <msg>   # "feat(execverify): positive/negative READ_FIXED progs + run wrapper"
```

---

### Task 4: 覆盖差分分析 + 判据 + findings

**Files:** Create `execverify/analyze_cover.py`、`docs/exec-verification-findings.md`

- [ ] **Step 1: 写 analyze_cover.py(解析 PC + 符号化 + 差分 + 判据)**

`execverify/analyze_cover.py`:
```python
#!/usr/bin/env python3
"""Differential KCOV coverage oracle for the READ_FIXED fixed-buffer gate.

Parses covered kernel PCs from two syz-execprog serial logs (positive/negative),
symbolizes them against vmlinux via addr2line, and checks whether the target
function (io_import_fixed) is reached ONLY by the positive prog -- i.e. the gate
predicate, when satisfied, gates that deep branch. Coverage is a proxy judge
(purpose.md 4.2(5)); the differential controls for noise.
"""
import re, subprocess, sys, json
VMLINUX = "/home/xujunru/syzkaller-docker/kernel-src/vmlinux"
TARGET = "io_import_fixed"
PC_RE = re.compile(r"0xffffffff8[0-9a-fA-F]+")

def pcs(log):
    s = set()
    for m in PC_RE.finditer(open(log, errors="replace").read()):
        s.add(int(m.group(0), 16))
    return s

def symbolize(pc_set):
    if not pc_set:
        return {}
    args = ["addr2line", "-f", "-e", VMLINUX] + [hex(p) for p in sorted(pc_set)]
    out = subprocess.run(args, capture_output=True, text=True).stdout.splitlines()
    # addr2line -f emits pairs: funcname, file:line
    m = {}
    ps = sorted(pc_set)
    for i, p in enumerate(ps):
        fn = out[2 * i] if 2 * i < len(out) else "?"
        m[p] = fn
    return m

def main():
    pos_log, neg_log = sys.argv[1], sys.argv[2]
    P, N = pcs(pos_log), pcs(neg_log)
    diff = P - N
    sym_diff = symbolize(diff)
    target_pcs = [hex(p) for p, fn in sym_diff.items() if TARGET in fn]
    verified = len(target_pcs) > 0
    result = {
        "pos_pc": len(P), "neg_pc": len(N), "diff_pc": len(diff),
        "target": TARGET, "target_pcs_in_diff": target_pcs,
        "gate_execution_verified": verified,
    }
    print(json.dumps(result, indent=2))
    return 0 if verified else 2

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 跑分析**

```bash
ssh -p 19999 xujunru@localhost 'cd /home/xujunru/ImplicitFuzz/execverify && \
  python3 analyze_cover.py out/cover_pos.log out/cover_neg.log'
```
Expected: JSON;`gate_execution_verified: true` 且 `target_pcs_in_diff` 非空 = 门控谓词被执行验证(io_import_fixed 只在正 prog 覆盖)。**若 false**:检查 (a) 正 prog 是否真到达(SQE 是否真的 READ_FIXED、fd 是否合法);(b) 符号化是否命中(addr2line 是否解出 io_import_fixed——该函数可能被 inline,退而查 io_prep_rw/io_read 邻近 PC);据实迭代 prog,或如实记录"覆盖代理未能区分"这一负结果。

- [ ] **Step 3: 写 findings(克制)**

`docs/exec-verification-findings.md`:环境(复用 syzkaller-docker KCOV 内核)、正/负 prog、差分覆盖数、判据结果(verified 与否)、`target_pcs_in_diff` 证据;caveat:覆盖即代理(别名风险)、单门控、单内核、KCOV_COMPARISONS 未开、io_import_fixed 可能 inline 需邻近 PC 近似。**提交前交用户复核。**

- [ ] **Step 4: 回归 + 提交**

```bash
# ImplicitFuzz 的 python 单测不受影响(execverify 是独立目录),仍跑一遍确认
ssh -p 19999 xujunru@localhost 'cd /home/xujunru/ImplicitFuzz && python3 -m pytest tests/ -q 2>&1 | tail -1'
git add execverify/analyze_cover.py docs/exec-verification-findings.md execverify/RUNBOOK.md
git commit -F <msg>   # "feat(execverify): differential coverage oracle + findings"
```

---

## Self-Review

- **Spec 覆盖:** 复用环境 → Global Constraints + Task 1/2;目标门控 READ_FIXED fixed-buffer → Task 3;组件 A(prog 装配)→ Task 3;B(运行 wrapper)→ Task 2(initramfs)+ Task 3(run_execprog.sh);C(覆盖分析)→ Task 4;D(smoke 门)→ Task 1 + Task 2 Step 3;差分判据 → Task 4;coverage-proxy caveat → Global + Task 4 findings。✅
- **占位符:** Tasks 含完整脚本/命令;prog 起草处明确标注"Step 3 用 syz-execprog 校验并迭代"(外部环境语法不确定的诚实处理,非占位)。`<msg>` = commit 信息文件。
- **类型一致:** `build_initramfs.sh <prog> <out>`、`run_execprog.sh <prog> <tag>`、`analyze_cover.py <pos_log> <neg_log>`、target=`io_import_fixed`、PC 正则跨 Task 一致。✅
- **已知开放/风险点:**(1)环境 5–6 月前建,Task 1/2 是 make-or-break 环境 spike,坏了如实报阻塞;(2)syzkaller prog 语法与 io_uring READ_FIXED SQE 表达随版本变,Task 3 需据 syz-execprog 报错迭代;(3)`io_import_fixed` 可能被 inline,Task 4 退用邻近 PC/函数近似;(4)覆盖是代理判据,负结果(未验证)也是诚实产物,不硬造。
