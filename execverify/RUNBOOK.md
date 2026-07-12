# execverify RUNBOOK — execution-verification prototype

Reuses the `~/syzkaller-docker` KCOV+io_uring env. Stable, verified invocations.

## Environment (Task 1 — verified)

- **qemu:** `/usr/libexec/qemu-kvm` (qemu-kvm 9.1.0, host, no sudo; KVM via world-writable `/dev/kvm`).
  - ⚠️ `~/syzkaller/qemu-system-x86_64` is a symlink to **qemu-user** (`qemu-x86_64-static`) — do not use.
- **Kernel (KCOV + io_uring, 6.1.0):** `~/syzkaller-docker/out/kernel/bzImage`. Symbolization: `~/syzkaller-docker/kernel-src/vmlinux`.
- **syz pair (RPC-compatible, static-pie — reuse the staging dir's OWN pair):** `~/syzkaller-docker/out/initramfs/{syz-execprog,syz-executor}`.
  - ⚠️ Do NOT mix binaries across builds: revision RPC check aborts, and other executors are dynamic (need `libstdc++.so.6`, absent).

## Coverage pipeline (Task 2 — verified end-to-end)

Custom **execprog-on-boot** initramfs (`initramfs/init`, built by `build_initramfs.sh <prog> <out.cpio.gz>`):
1. mounts proc/sys/dev/debugfs + tmpfs on /dev/shm,/tmp;
2. **brings up loopback** (`ifconfig lo 127.0.0.1 up`) — required: syz-execprog runs a local manager on 127.0.0.1 the executor dials back to;
3. runs `syz-execprog -executor=/syz-executor -sandbox=none -cover -coverfile=/tmp/cover -repeat=1 -procs=1 -threaded=0 -debug /repro.syz`;
4. coverage lands at **`/tmp/cover_prog1.0`** (suffix `_prog<N>.<call>`) as **raw kernel PCs** (`0xffffffff8...`), printed to serial;
5. `poweroff -f`.

Boot + capture:
```bash
timeout 70 /usr/libexec/qemu-kvm -machine q35 -enable-kvm -cpu host -m 2048 -smp 1 \
  -kernel ~/syzkaller-docker/out/kernel/bzImage -initrd execverify/out/initramfs-exec.cpio.gz \
  -append "console=ttyS0 root=/dev/ram rdinit=/init nokaslr" \
  -nographic -no-reboot -display none -serial mon:stdio > <serial.log> 2>&1
```
Smoke result: `getpid()` prog → `cover=15`, raw PCs in `/tmp/cover_prog1.0`, clean exit. Pipeline good.
