#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
PROG=${1:?usage: run_execprog.sh <prog.syz> <tag>}
TAG=${2:?}
bash "$ROOT/build_initramfs.sh" "$PROG" "$ROOT/out/initramfs-$TAG.cpio.gz" >/dev/null
timeout 80 /usr/libexec/qemu-kvm -machine q35 -enable-kvm -cpu host -m 2048 -smp 1 \
  -kernel /home/xujunru/syzkaller-docker/out/kernel/bzImage -initrd "$ROOT/out/initramfs-$TAG.cpio.gz" \
  -append "console=ttyS0 root=/dev/ram rdinit=/init nokaslr" -nographic -no-reboot -display none \
  -serial mon:stdio > "$ROOT/out/cover_$TAG.log" 2>&1 || true
echo "wrote $ROOT/out/cover_$TAG.log ($(sed -n '/COVERFILE_BEGIN/,/COVERFILE_END/p' "$ROOT/out/cover_$TAG.log" | grep -cE '0xffffffff8') PCs)"
