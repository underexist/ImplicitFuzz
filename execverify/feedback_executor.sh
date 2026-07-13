#!/usr/bin/env bash
# feedback_executor.sh <pos.syz> <neg.syz> <signal_func>
# Runs the differential KCOV harness for an assembled pos/neg prog pair and
# emits the analyze_cover.py JSON (signal_pos/signal_neg) on stdout. Reuses
# run_execprog.sh (build initramfs + qemu-kvm boot + coverfile capture) and
# analyze_cover.py (nm-range signal counting against out/kernel/vmlinux).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
POS=${1:?usage: feedback_executor.sh <pos.syz> <neg.syz> <signal_func>}
NEG=${2:?}
SIGNAL=${3:-io_read}
PROG2C=${PROG2C:-$HOME/syzkaller/bin/syz-prog2c}

# Self-check: dump the first SQE bytes so we can eyeball opcode(0x16) / iosqe
# flags(0x1) / fd_index before booting (prog2c method).
for P in "$POS" "$NEG"; do
  echo "### prog2c self-check: $P" >&2
  "$PROG2C" -prog "$P" 2>/dev/null | grep -iE "register|0x16|sqe|fd" | head -8 >&2 || true
done

bash "$ROOT/run_execprog.sh" "$POS" "fb_pos" >&2
bash "$ROOT/run_execprog.sh" "$NEG" "fb_neg" >&2
python3 "$ROOT/analyze_cover.py" "$ROOT/out/cover_fb_pos.log" "$ROOT/out/cover_fb_neg.log" "$SIGNAL"
