# execverify RUNBOOK — execution-verification prototype

Reuses the `~/syzkaller-docker` KCOV+io_uring env. Stable, verified invocations.

## Environment (Task 1 — env-liveness smoke, verified)

- **qemu (real system emulator):** `/usr/libexec/qemu-kvm` (qemu-kvm 9.1.0, host binary, no sudo; KVM via world-writable `/dev/kvm`).
  - ⚠️ `~/syzkaller/qemu-system-x86_64` is a symlink to **qemu-user** (`qemu-x86_64-static`) — rejects `-enable-kvm`; **do not use**.
- **Kernel (KCOV + io_uring, 6.1.0):** `~/syzkaller-docker/out/kernel/bzImage` (config: `CONFIG_KCOV=y`, `CONFIG_IO_URING=y`, `CONFIG_DEBUG_INFO_DWARF4=y`). Symbolization: `~/syzkaller-docker/kernel-src/vmlinux`.
- **syz tooling:** `~/syzkaller/bin/linux_amd64/syz-execprog`, `syz-executor`.

### Verified boot (kernel comes up)
```bash
timeout 60 /usr/libexec/qemu-kvm -machine q35 -enable-kvm -cpu host -m 2048 -smp 1 \
  -kernel ~/syzkaller-docker/out/kernel/bzImage -initrd <initramfs.cpio.gz> \
  -append "console=ttyS0 root=/dev/ram rdinit=/init nokaslr" \
  -nographic -no-reboot -display none -serial mon:stdio
```
Result: boots to `/init` (Linux version 6.1.0, `crng init done`, `Freeing unused kernel`). The stock `initramfs.cpio.gz` (manager/SSH init) panics without an `-device e1000` net device — expected; the prototype uses a custom **execprog-on-boot** initramfs (no networking) instead. See Task 2.
