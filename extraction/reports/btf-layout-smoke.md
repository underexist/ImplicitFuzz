# BTF Layout Smoke Report

Generated: 2026-07-09 20:12 UTC

## Environment

- BTF source: `/sys/kernel/btf/vmlinux`
- using runtime kernel BTF (/sys/kernel/btf/vmlinux)
- linux-6.1 tree vmlinux has no .BTF section in current build (CONFIG_DEBUG_INFO_BTF not enabled)
- pahole not installed (not required for this smoke check)

## Cross-checks

- **kernel_timeout_case1_gep** `io_timeout.off`: DWARF offset=8 bytes, BTF offset=8 bytes (64 bits) -> **match**
  - DWARF and BTF byte offsets agree
- **kernel_cancel_case2_struct_gep** `io_hash_bucket.list`: DWARF offset=8 bytes, BTF offset=8 bytes (64 bits) -> **match**
  - DWARF and BTF byte offsets agree

## Notes

- BTF is used as a layout reference only; facts still use `primary_provenance: dwarf`.
- This check does not modify extractor output.
