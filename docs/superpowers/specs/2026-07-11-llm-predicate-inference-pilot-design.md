# LLM Constrained Predicate Inference Pilot with Ledger Reconciliation — 设计

**Date:** 2026-07-11
**Branch:** `phase2b-evidence-identity`
**Scope:** 研究内容一 · 状态门控视图的 LLM 受限判读环节 + 其前置的事实账对账层(purpose.md §4.2(3) 三道校验)
**Status:** design approved; awaiting implementation plan (writing-plans)
**依据:** `docs/llm-judge-feasibility-spike.md`(可行性 spike 结论)

---

## 0. 立论(为什么对账层是第一阶段)

spike(2026-07-11)在旗舰 READ_FIXED 门控上证明:**LLM 本身不是当前最大阻塞**——它能在代码切片+账约束下正确推出含最难 param-align 的完整谓词、无切片外幻觉。**真正决定 pipeline 成败的是"LLM 说出的字段能否被事实账可靠对上"**:旗舰的 `nr_user_files` 在账里是 `num:io_ring_ctx@[160]`(无符号),纯按名对账会把正确谓词误杀为幻觉。故本 pilot 把 **name→offset 事实账对账层作为 Phase 1**,LLM 判读作为 Phase 2 挂其后。

**坑(已记录):** 布局必须用被分析的 6.1 树自己的 DWARF(.bc 带 -g);运行时 BTF 把 `nr_user_files` 放在 byte 120,而 6.1 是 160——不同内核版本布局不同,运行时 BTF 不能当 oracle。

## 1. 目标与非目标

**目标:** 在小标注集(~3–5 门控)上验证链路"LLM 封闭-schema 谓词 → 健壮事实账对账(处理 numeric-only)→ 三道校验",量出精确率/幻觉拦截,定 LLM 主力还是辅助。

**非目标(留 follow-up):** API 自动化批量判读;10–20 门控大评测;把验证过的谓词落 schema-bound `gate_seed_fact`;确定性装配与执行验证(研究内容二)。

## 2. 架构(阶段化;计划里 Phase 1 全绿再 Phase 2)

```
Phase 1  对账层(确定性, 无 LLM, 全单测)  ← make-or-break
  A. struct-layout side-table (抽取器安全 emit)
  B. name→(struct,offset) 解析器 (Python)
  C. 字段存在性对账校验器 (Python)  ← spike 卡住旗舰的那一步, 现对 numeric-only 健壮
Phase 2  LLM 受限判读(小样本, 内联判读)
  D. 切片+账上下文构建器 (确定性)
  E. 封闭谓词 schema (pilot-local)
  F. LLM 判读运行 (判读员=本模型)
  三道校验: ①字段存在性(用C) ②schema合法(E) ③可合成性预检
  G. 评测 harness → findings 表
```

## 3. Phase 1 组件

### A. struct-layout side-table(抽取器安全 emit)
复用抽取器已有的 `DwarfStructIndex`,新增一段**只读 DWARF 遍历**,对其解析到的每个 struct 导出布局行,写到一份**新增** side JSONL。

- 新增 fact schema `extraction/schema/facts/struct_layout_fact.schema.json`(**additive**,`schema_version` 恒 1.0.0,`validate_facts_schema.py` 自动 glob):
  字段 `struct_name`(canonical,去 struct/const/volatile 噪声)、`member_name`、`byte_offset`(int)、`member_type`、`bit_offset`(可选,位域)。
- ingest 成 `struct_layout` 表(每 struct×member 一行)。
- **安全性:** 不进 SVF points-to / `getLLVMValue()` 热路径(坑#1);仅在 DWARF 元数据上遍历。遵守"先 `has*()` 检查"若触及 SVF 值。
- **去重:** 同一 (struct_name, member_name, byte_offset) 跨 TU 可重复出现,ingest/消费去重;冲突(同名同偏移不同类型)记为告警不致命。

### B. name→(struct,offset) 解析器(Python)
`src/implicitfuzz/reconcile/layout.py`:输入字段引用(如 `io_ring_ctx.nr_user_files` 或 `(struct=io_ring_ctx, member=nr_user_files)`),查 `struct_layout` 表返回 `byte_offset`(160)。struct_name 归一化复用 Phase 2A 的 `canonicalize_struct_type`(已有 `field_key.py`)。解析不到 → None(下游判 unconfirmed)。

### C. 字段存在性对账校验器(Python)· 核心
`src/implicitfuzz/reconcile/field_existence.py`:对谓词引用的每个字段,按序:
1. **符号直配**:access_fact 里有 `access_path_symbolic == 该字段`(如 `io_ring_ctx.file_data`)→ `confirmed_symbolic`,附匹配 access_fact id。
2. **数值经解析**:否则用 B 解析成 `(struct, offset)`;在 access_fact 里找 base 对应该 struct、`numeric_kind ∈ {gep_offsets, byte_range}` 且偏移等于 `offset` 的记录 → `confirmed_numeric`(置信标低一档,复用 `lower_confidence`),附匹配 id。
3. 两步都不中 → `unconfirmed`(判幻觉,谓词被三道校验拦下)。

**关键行为(spike 要求验证的):** 旗舰 `nr_user_files` 走第 2 步 → `confirmed_numeric`(offset 160 匹配 rsrc 写);编造字段(如 `io_ring_ctx.nonexistent_field`)两步皆不中 → `unconfirmed`,被拦。

> base↔struct 对应:v1 用符号前缀/字段类型判 base 所属 struct;numeric-only access 的 struct 归属若拿不到(spike 已知 numeric-only access `field_type=null`),则该字段的数值确认受限——如实标注,并在 findings 记录覆盖率(这直接量化"对账层能救回多少 numeric-only 字段")。

## 4. Phase 2 组件

### D. 切片+账上下文构建器(确定性)
`src/implicitfuzz/pilot/context.py`:对目标门控(来自 `gate_seed_candidate` / `gate_prior_write_candidate`),组装:①代码切片——门控分支所在函数 + 写侧函数的源码行(从 6.1 树按 source_location 取,行窗口有界);②账子集——这些函数的 access_fact。输出结构化 context(供判读员消费)。

### E. 封闭谓词 schema(pilot-local)
`src/implicitfuzz/pilot/predicate_schema.json`(**pilot-local JSON Schema,不入 extraction/schema/facts,不动 schema-bound gate_seed_fact**):
```
predicate = {
  target_gate: {bc_unit, function, branch_instruction_id},
  terms: [ {
    class: "premise" | "activation" | "param_align",
    object: <object tag>,                 // 谓词项所属对象实例
    field_ref: "<struct>.<member>",       // 供对账
    relation: "<free text, e.g. fd < nr_user_files>",
    source: "slice" | "slice+ledger" | "inferred",
    confidence: "high|medium_high|medium|medium_low|low",
    uncertain: bool
  } ... ],
  abstain: bool,                          // 允许"不确定"退路
  alt_candidates: [ ... ]                 // 允许多候选
}
```

### F. LLM 判读运行(判读员 = 本模型)
判读员对小标注集每个门控,读 D 的 context,按 E 输出谓词;允许 abstain / 多候选。feasibility 阶段**不接外部 API**。

### 三道校验
① **字段存在性对账**——每个 term 的 `field_ref` 过 C;有 `unconfirmed` 的 term 判幻觉丢弃(整谓词标不通过并记因)。② **schema 合法**——过 E 的 JSON Schema。③ **可合成性预检**——确定性启发:activation/param_align 的 relation 能否映射到一个可设置的参数(如 `sqe->fd` / `sqe->buf_index`)且其对齐目标是前序调用的具体参数;不能则标不可合成。

### G. 评测 harness
`src/implicitfuzz/pilot/eval.py`:跑 D→F→三道校验,逐门控记:谓词是否匹配人工标(term 级精确/召回)、过没过三道、`unconfirmed` 拦截了哪些(含对编造字段的负例)。产出 findings 表(markdown)。

## 5. 小标注集(~3–5 门控)
| 门控 | 类型 | 看点 |
|---|---|---|
| READ_FIXED fixed-file(`fd<nr_user_files` + file_table) | param_align(numeric-only) | 旗舰;C 能否救回 nr_user_files |
| fixed-buffer(`buf_index<nr_user_bufs`) | param_align(numeric-only) | 第二个 param-align;对称验证 |
| `io_kiocb.flags` 门控 | premise(symbolic) | 对照;符号直配应干净 |
| (按需 1–2) | premise/activation | 增样本 |

ground-truth 谓词:我先标 + 你复核(spec 落地后单列一份 `pilot/labels/*.json`)。

## 6. 文件结构
- `extraction/src/implicitfuzz-extract.cpp` — 加 struct-layout emit(安全,只读 DWARF)。
- `extraction/schema/facts/struct_layout_fact.schema.json`(新,additive)。
- `src/implicitfuzz/ingestion/schema.sql` + `ingest.py` — `struct_layout` 表(自动发现)。
- `src/implicitfuzz/reconcile/layout.py`、`reconcile/field_existence.py`(新)。
- `src/implicitfuzz/pilot/context.py`、`pilot/predicate_schema.json`、`pilot/eval.py`、`pilot/labels/`(新)。
- `tests/test_struct_layout_ingest.py`、`test_reconcile_layout.py`、`test_field_existence.py`、`test_pilot_eval.py`(新)。
- `docs/llm-predicate-pilot-runbook.md` + findings(新)。

## 7. 测试与验收
- **Phase 1 全单测:** struct_layout ingest(schema 校验 + 表);解析器(命中/未命中/struct 归一化);对账校验器**三态**——`confirmed_symbolic`(file_data)、`confirmed_numeric`(nr_user_files@160)、`unconfirmed`(编造字段被拦)。
- **Phase 2:** eval harness 用合成 context/谓词单测(校验流程正确);真实 LLM 跑是"量准"实验,产出 findings 表(非 pass/fail 断言)。
- **回归:** `run_phase1_regression.sh` → `[phase1] ALL PASS`(含新 struct_layout emit 不破坏旧 golden);`pytest` 全绿。
- **端到端验收:** 在真实 rsrc 语料上,C 能把旗舰 `io_ring_ctx.nr_user_files` 判为 `confirmed_numeric`(offset 160 匹配),把编造字段判 `unconfirmed`;pilot 在旗舰门控上复现 spike 结论(谓词对、param_align 项经对账确认)。

## 8. 硬约束
- schema 只加不改;`struct_layout_fact` 是新增 additive fact;pilot 谓词 schema 是 pilot-local(不入 facts 目录、不动 gate_seed_fact)。
- struct-layout emit 不进 SVF 段错误热路径;触及 SVF 值先 `has*()`(坑#1)。
- per-TU 边界不变;struct_layout 跨 TU 是并集去重。
- 判读员 feasibility 阶段不接外部 API。
