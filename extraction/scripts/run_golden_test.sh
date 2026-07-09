#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT}/build"
TESTDATA="${ROOT}/testdata"
OUT_JSONL="${ROOT}/build/golden-facts.jsonl"

LLVM_BIN="/home/xujunru/.local/opt/llvm21-rpm/usr/lib64/llvm21/bin"
LLVM_LIB="/home/xujunru/.local/opt/llvm21-rpm/usr/lib64"
LLVM21_LIB="/home/xujunru/.local/opt/llvm21-rpm/usr/lib64/llvm21/lib64"
SVF_BUILD="/home/xujunru/implicitfuzz-toolchain/SVF/verify-llvm21-build4"
Z3_LIB="/home/xujunru/implicitfuzz-toolchain/z3-install/lib64"

export LLVM_DIR="/home/xujunru/.local/opt/llvm21-rpm/usr/lib64/llvm21/lib64/cmake/llvm"
export SVF_DIR="${SVF_BUILD}"

LLVM_LD_PATH="${LLVM_LIB}:${LLVM21_LIB}"
RUN_LD_PATH="${LLVM_LD_PATH}:${Z3_LIB}:${SVF_BUILD}/lib"

mkdir -p "${BUILD_DIR}"

python3 "${ROOT}/scripts/validate_primitive_summary.py" \
  --yaml "${ROOT}/schema/primitive_summary.yaml" \
  --schema "${ROOT}/schema/primitive_summary.schema.json" \
  --json-out "${ROOT}/schema/primitive_summary.json"

if [[ ! -x "${BUILD_DIR}/implicitfuzz-extract" ]]; then
  cmake -S "${ROOT}" -B "${BUILD_DIR}" -DCMAKE_BUILD_TYPE=Release
  cmake --build "${BUILD_DIR}" -j"$(nproc)"
fi

env -u LD_LIBRARY_PATH \
  LD_LIBRARY_PATH="${LLVM_LD_PATH}" \
  "${LLVM_BIN}/clang-21" -c -emit-llvm -g -O0 -Xclang -disable-O0-optnone \
  "${TESTDATA}/input.c" -o "${TESTDATA}/input.bc"

env -u LD_LIBRARY_PATH \
  LD_LIBRARY_PATH="${RUN_LD_PATH}" \
  "${BUILD_DIR}/implicitfuzz-extract" \
  -jsonl-out "${OUT_JSONL}" \
  -primitive-summary "${ROOT}/schema/primitive_summary.json" \
  "${TESTDATA}/input.bc"

python3 "${ROOT}/scripts/check_golden_facts.py" "${OUT_JSONL}"

python3 "${ROOT}/scripts/validate_facts_schema.py" \
  "${OUT_JSONL}" \
  "${ROOT}/schema/facts"

python3 "${ROOT}/scripts/report_call_summary.py" \
  "${OUT_JSONL}" \
  --report "${BUILD_DIR}/call-summary-tiny.md" \
  >/dev/null
