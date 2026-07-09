#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXTRACTION="${ROOT}/extraction"
BUILD_DIR="${EXTRACTION}/build"
GROUND_TRUTH="${EXTRACTION}/golden/kernel_timeout_case1/ground_truth.json"

BC="${1:-/tmp/io_uring_timeout.bc}"
OUT="${2:-/tmp/io_uring_timeout.facts.jsonl}"

LLVM_LIB="/home/xujunru/.local/opt/llvm21-rpm/usr/lib64"
LLVM21_LIB="/home/xujunru/.local/opt/llvm21-rpm/usr/lib64/llvm21/lib64"
SVF_BUILD="/home/xujunru/implicitfuzz-toolchain/SVF/verify-llvm21-build4"
Z3_LIB="/home/xujunru/implicitfuzz-toolchain/z3-install/lib64"

LLVM_LD_PATH="${LLVM_LIB}:${LLVM21_LIB}"
RUN_LD_PATH="${LLVM_LD_PATH}:${Z3_LIB}:${SVF_BUILD}/lib"

if [[ ! -f "${BC}" ]]; then
  echo "error: bitcode not found: ${BC}" >&2
  echo "hint: generate it from linux-6.1 io_uring/timeout.c (see extraction/RUNBOOK.md)" >&2
  exit 1
fi

python3 "${EXTRACTION}/scripts/validate_primitive_summary.py" \
  --yaml "${EXTRACTION}/schema/primitive_summary.yaml" \
  --schema "${EXTRACTION}/schema/primitive_summary.schema.json" \
  --json-out "${EXTRACTION}/schema/primitive_summary.json" >/dev/null

if [[ ! -x "${BUILD_DIR}/implicitfuzz-extract" ]]; then
  cmake -S "${EXTRACTION}" -B "${BUILD_DIR}" -DCMAKE_BUILD_TYPE=Release
  env -u LD_LIBRARY_PATH \
    LD_LIBRARY_PATH="${LLVM_LD_PATH}" \
    cmake --build "${BUILD_DIR}" -j"$(nproc)"
fi

env -u LD_LIBRARY_PATH \
  LD_LIBRARY_PATH="${RUN_LD_PATH}" \
  "${BUILD_DIR}/implicitfuzz-extract" \
  -jsonl-out "${OUT}" \
  -primitive-summary "${EXTRACTION}/schema/primitive_summary.json" \
  "${BC}"

python3 "${EXTRACTION}/scripts/validate_facts_schema.py" \
  "${OUT}" \
  "${EXTRACTION}/schema/facts"

python3 "${EXTRACTION}/scripts/check_kernel_timeout_case1.py" \
  "${OUT}" \
  "${GROUND_TRUTH}"
