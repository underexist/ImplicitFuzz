# 置信度反馈闭环(最小版)Findings

**Date:** 2026-07-13
**Branch:** `phase2b-evidence-identity` (worktree `feedback-loop`)
**Kernel build identity:** `bzImage@b364f28d1fcf` (linux-6.1 io_uring, KCOV)
**Spec/Plan:** `docs/superpowers/specs/2026-07-13-confidence-feedback-loop-design.md` · `docs/superpowers/plans/2026-07-13-confidence-feedback-loop.md`

## 定位
把 execution verification 从"末端证明工具"升级为**系统内部反馈机制**:重放已提交候选 → 家族模板装配正负 prog → 复用 execverify(qemu-kvm/KCOV/差分覆盖)执行 → 按离散档单调升降校准置信 + 重排。单向流水线 → **推断—验证—校准**闭环。原始静态结果不可变,另存 calibrated view。

## 真实闭环跑(3 候选,一次覆盖三种更新动作)

驱动:`execverify/run_feedback_loop.py`(载候选 JSON → `feedback.assemble` 装配 → `feedback_executor.sh` 真实执行 → `feedback.loop.run_loop`)。
落盘:`feedback/calibrated/run-2026-07-13.json`(reranked,全溯源)。

| candidate | origin | static | 装配 | pos_cov / neg_cov (io_read) | outcome | **final** | 动作 |
|---|---|---|---|---|---|---|---|
| **ff_real** | replayed | high | fixed_file v1.0 (nr_args=2, fd_index 1 vs 5) | **19 / 0** | verified | `execution_verified` | **提档** |
| **flags_unsupported** | replayed | medium | — (非家族,未执行) | null / null | unsupported | `medium` | **维持** |
| **ff_mutant** | synthetic_negative_control | high | fixed_file v1.0 (方向翻转:pos=idx5, neg=idx1) | **0 / 19** | contradicted | `rejected` | **降档** |

重排(按 final 档降序):`ff_real`(execution_verified)首 → `flags_unsupported`(medium)→ `ff_mutant`(rejected)末。

### 三种更新动作逐条
1. **提档(verified→execution_verified)**:真实候选 `sqe->fd < nr_user_files`。claimed-satisfy(register 2 files,fd_index=1)io_read=19;claimed-violate(fd_index=5,OOB)io_read=0 → 声称方向成立,差分显著(margin=2 远超)。这是已验 param-align 差分(pa_pos/pa_neg)的闭环重放。
2. **维持(unsupported)**:`io_kiocb->flags` 前提门控无对应模板 → **未执行**,保持静态档 medium,原因标 `no assembler template`。与"执行了但不可判"(undecidable)语义区分。
3. **降档(contradicted→rejected)**:合成负控 mutant 故意声称 `fd >= nr_user_files` 才进 io_read。其 claimed-satisfy(fd_index=5)io_read=0、claimed-violate(fd_index=1)io_read=19 → **明确反方向证据** → contradicted → rejected。证明闭环具备**淘汰错误候选**的能力。

### 执行真实性佐证
两条 mutant boot 各产出真实 coverfile(`fb_pos` 4640 PCs / `fb_neg` 5046 PCs,均带 `EXECVERIFY_DONE` 标记,非超时空跑);signal 计数用 `out/kernel/vmlinux`(与 booted bzImage 匹配)nm ranges。差分方向由 fd_index 值单独决定(其余 SQE 前缀/opcode 0x16/iosqe flags 0x1 一致)。

## 诚实边界(与 spec 收紧一致)
- **`contradicted` 仅方向相反**:signal_neg − signal_pos ≥ margin 才判;`pos≈neg`(|diff|<margin)归 `undecidable`、**不降档**。
- **`undecidable` 未在真实跑中出现**,仅由单测覆盖规则(`classify_outcome(8,8)`/`(9,8)`/`(None,5)` → undecidable);将来真实运行自然产生时再纳入 findings。
- **合成负控标注**:`candidate_origin=synthetic_negative_control`,**不计入真实候选的准确率/校准统计**;它只证"闭环能淘汰错误候选",不主张任何真实依赖被推翻。
- **KCOV 是执行代理**:降档判词用 `contradicted`(反方向证据),不用 `falsified`。
- **calibrated view 不覆盖原始**:静态候选 JSON 不可变;view 另存,记全溯源(candidate_id/origin/static/execution_status/final/template_family/template_version/kernel_build_identity/signal_function/pos_coverage/neg_coverage/reason)→ 随时回答"为何提/降档",模板或内核变化后可重算。

## 复用与新建
- **复用**:execverify harness(`run_execprog.sh`/`build_initramfs.sh`/`analyze_cover.py`,qemu-kvm/KCOV)、Phase 1 档语义、已提交候选家族(fixed_file/fixed_buffer)。
- **新建**:`src/implicitfuzz/feedback/{update,assemble,loop}.py`(纯 Python 反馈核心,executor 注入以便单测)、`execverify/{feedback_executor.sh,run_feedback_loop.py}`(server 真实执行)、`feedback/candidates/*.json`、`feedback/calibrated/*.json`。

## 明确不做(YAGNI)
live-LLM、通用 prog 合成、fixed-file/buffer 以外模板、数值置信/收敛曲线(离散档先行)、单内核单子系统。
