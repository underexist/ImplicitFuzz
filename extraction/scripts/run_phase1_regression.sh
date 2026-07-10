#!/usr/bin/env bash
# Phase 1 final regression: tiny + kernel case1/2/3 + optional BTF layout smoke.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXTRACTION="${ROOT}/extraction"
SCRIPTS="${EXTRACTION}/scripts"

BTF_PATH="${BTF_PATH:-/sys/kernel/btf/vmlinux}"
SVF_DIR="${SVF_DIR:-/home/xujunru/implicitfuzz-toolchain/SVF}"

BC_TIMEOUT="${BC_TIMEOUT:-/tmp/io_uring_timeout.bc}"
BC_CANCEL="${BC_CANCEL:-/tmp/io_uring_cancel.bc}"
BC_KBUF="${BC_KBUF:-/tmp/io_uring_kbuf.bc}"
BC_OPDEF="${BC_OPDEF:-/tmp/io_uring_opdef.bc}"

OUT_TIMEOUT="${OUT_TIMEOUT:-/tmp/io_uring_timeout.facts.jsonl}"
OUT_CANCEL="${OUT_CANCEL:-/tmp/io_uring_cancel.facts.jsonl}"
OUT_KBUF="${OUT_KBUF:-/tmp/io_uring_kbuf.facts.jsonl}"
OUT_OPDEF="${OUT_OPDEF:-/tmp/io_uring_opdef.facts.jsonl}"
OUT_TINY="${OUT_TINY:-${EXTRACTION}/build/golden-facts.jsonl}"

FAILURES=0
BTF_STATUS="skipped"

log_step() {
  echo ""
  echo "================================================================"
  echo "$1"
  echo "================================================================"
}

count_facts() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    grep -cve '^[[:space:]]*$' "${path}" || true
  else
    echo "n/a"
  fi
}

svf_commit() {
  if git -C "${SVF_DIR}" rev-parse --short HEAD 2>/dev/null; then
    return 0
  fi
  echo "unknown"
}

run_step() {
  local name="$1"
  shift
  log_step "${name}"
  if "$@"; then
    echo "[phase1] PASS: ${name}"
  else
    echo "[phase1] FAIL: ${name}" >&2
    FAILURES=$((FAILURES + 1))
  fi
}

log_step "Phase 1 regression (ImplicitFuzz extraction)"
echo "ROOT=${ROOT}"
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "LLVM: 21.1.8 (see docs/svf-extraction-toolchain-notes.md)"
echo "Kernel tree: linux-6.1"
echo "SVF commit: $(svf_commit)"

run_step "tiny golden" bash "${SCRIPTS}/run_golden_test.sh"

if [[ ! -f "${BC_TIMEOUT}" ]]; then
  echo "[phase1] FAIL: missing bitcode ${BC_TIMEOUT}" >&2
  FAILURES=$((FAILURES + 1))
else
  run_step "kernel case1 (timeout.c)" \
    bash "${SCRIPTS}/run_kernel_case1.sh" "${BC_TIMEOUT}" "${OUT_TIMEOUT}"
fi

if [[ ! -f "${BC_CANCEL}" ]]; then
  echo "[phase1] FAIL: missing bitcode ${BC_CANCEL}" >&2
  FAILURES=$((FAILURES + 1))
else
  run_step "kernel case2 (cancel.c)" \
    bash "${SCRIPTS}/run_kernel_case2.sh" "${BC_CANCEL}" "${OUT_CANCEL}"
fi

if [[ ! -f "${BC_KBUF}" ]]; then
  echo "[phase1] FAIL: missing bitcode ${BC_KBUF}" >&2
  FAILURES=$((FAILURES + 1))
else
  run_step "kernel case3 (kbuf.c)" \
    bash "${SCRIPTS}/run_kernel_case3.sh" "${BC_KBUF}" "${OUT_KBUF}"
fi

if [[ ! -f "${BC_OPDEF}" ]]; then
  echo "[phase1] FAIL: missing bitcode ${BC_OPDEF}" >&2
  FAILURES=$((FAILURES + 1))
else
  run_step "kernel case5 (opdef.c const dispatch table)" \
    bash "${SCRIPTS}/run_kernel_case5.sh" "${BC_OPDEF}" "${OUT_OPDEF}"
fi

log_step "BTF layout smoke (optional)"
if [[ -f "${BTF_PATH}" ]]; then
  if python3 "${SCRIPTS}/check_btf_layout.py" \
    --btf "${BTF_PATH}" \
    --case "${EXTRACTION}/golden/kernel_timeout_case1/ground_truth.json" \
    --case "${EXTRACTION}/golden/kernel_cancel_case2/ground_truth.json" \
    --report "${EXTRACTION}/reports/btf-layout-smoke.md"; then
    BTF_STATUS="match"
    echo "[phase1] PASS: BTF layout smoke"
  else
    rc=$?
    if [[ "${rc}" -eq 2 ]]; then
      BTF_STATUS="deferred"
      echo "[phase1] SKIP: BTF layout smoke deferred (exit ${rc})"
    else
      BTF_STATUS="failed"
      echo "[phase1] FAIL: BTF layout smoke (exit ${rc})" >&2
      FAILURES=$((FAILURES + 1))
    fi
  fi
else
  BTF_STATUS="skipped: ${BTF_PATH} not found"
  echo "BTF smoke skipped: ${BTF_PATH} not found"
  echo "[phase1] SKIP: BTF layout smoke"
fi

echo ""
echo "================================================================"
echo "Phase 1 regression snapshot"
echo "================================================================"
echo "tiny facts:          $(count_facts "${OUT_TINY}")"
echo "timeout facts:       $(count_facts "${OUT_TIMEOUT}")"
echo "cancel facts:        $(count_facts "${OUT_CANCEL}")"
echo "kbuf facts:          $(count_facts "${OUT_KBUF}")"
echo "opdef facts:         $(count_facts "${OUT_OPDEF}")"
echo "BTF smoke:           ${BTF_STATUS}"
echo "SVF commit:          $(svf_commit)"

if [[ "${FAILURES}" -gt 0 ]]; then
  echo ""
  echo "[phase1] FAILED (${FAILURES} step(s))" >&2
  exit 1
fi

echo ""
echo "[phase1] ALL PASS"
exit 0
