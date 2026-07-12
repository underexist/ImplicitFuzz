#!/usr/bin/env bash
# Build an execprog-on-boot initramfs by overlaying our init + syz-execprog +
# prog onto the existing syzkaller-docker initramfs staging dir (which already
# has busybox + the dynamic syz-executor + its libraries).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
PROG=${1:?usage: build_initramfs.sh <prog.syz> <out.cpio.gz>}
OUT=${2:?}
SRC=/home/xujunru/syzkaller-docker/out/initramfs
# Reuse the staging dir's OWN syz-execprog + static-pie syz-executor -- the
# RPC-compatible pair the user's working initramfs already shipped. Do NOT
# overwrite them from other builds (revision RPC check / missing libstdc++).
STAGE=$(mktemp -d)
cp -a "$SRC"/. "$STAGE"/
[ -x "$STAGE/syz-execprog" ] || { echo "no syz-execprog in staging"; exit 1; }
[ -x "$STAGE/syz-executor" ] || { echo "no syz-executor in staging"; exit 1; }
cp "$ROOT/initramfs/init" "$STAGE/init"; chmod +x "$STAGE/init"
cp "$PROG" "$STAGE/repro.syz"
( cd "$STAGE" && find . | cpio -o -H newc 2>/dev/null | gzip -9 ) > "$OUT"
echo "built $OUT ($(du -h "$OUT" | cut -f1)) with prog $PROG"
rm -rf "$STAGE"
