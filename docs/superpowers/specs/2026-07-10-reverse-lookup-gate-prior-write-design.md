# 反查设计 — gate → 前序写调用 (gate_prior_write_candidate)

**Date:** 2026-07-10
**Branch:** `phase2b-evidence-identity`
**Scope:** 研究内容一 · 状态门控视图的"先门控、后反查"派生环节 (purpose.md §3.2 / §4.2(2) 第34段)
**Status:** design approved (v1 = 符号-only, 见 §0 决策修订); awaiting implementation plan

---

## 0. 决策修订 (2026-07-10, 写计划前实测数据后)

原设计拟"符号字段为主 + 数值兜底"。**写计划前实测 `rsrc.facts.jsonl` 推翻了数值兜底的前提**:

- 403 条 numeric-only access **全部** `field_type = null`、`access_path_recovery = numeric_only`,`base_object.value` 只带函数局部/合成标签(`io_rsrc_refs_drop:0`、`minimal_stub`、per-TU allocation-site id),**没有任何 struct type 名**,只有 byte-offset(如 `[128]`)。
- 因此设计中的数值键 `num:<struct_type>@<offset>` **无法在 Python 侧构造**——struct type 根本不在 facts 里,要拿到得改 C++ 抽取器。
- 且跨 TU 数值匹配本身很弱:allocation-site id 是 per-TU build-local(跨 `.bc` 不匹配),只有 `global` 按名字匹配,而 `nr_user_files` 是 ctx 堆字段不是 global。

**用户决策:先出符号 v1,再接抽取器符号化升级。** 故本 spec 的 v1 范围收窄为:

- **v1 = 纯 Python、纯符号(`access_path_symbolic`)的跨 TU 反查。** 验收锚点改用已符号化字段 `io_ring_ctx.file_data`(W2/R6)。
- **字段键 helper 保留 numeric 分支的位置,但 v1 对 numeric-only access 返回 `None`(不参与匹配)**,并注释指向后续升级。三条 numeric 规则(canonicalize struct type / 只同类型内匹配 / 置信低于符号 + 暴露 basis)作为**后续升级的约定**保留在 §3,不在 v1 落地。
- **旗舰 `nr_user_files` 移到紧接的后续计划:抽取器符号化升级(C++)**——把 rsrc 写侧 `io_ring_ctx.nr_user_files` 等从 numeric-only 提升为符号字段;届时纯符号反查自然覆盖旗舰例子,无需数值兜底。见 §7。

下文 §1–§6 描述完整目标形态;凡与本节冲突处,**以本节为准(v1 范围)**。

---

## 1. 目标与边界

`gate_seed_candidate` 目前只是"门控骨架"——标出"某分支被某对象字段门控",但**尚不是隐式依赖**。按"先门控、后反查":拿被门控字段去证据库反查"谁写了这个字段",得到"设置该状态的前序写点",才把门控转成该分支的**隐式前序依赖候选**。本 spec 定义这一反查环节。

**v1 要证明的唯一命题:** 被门控(符号)字段能**跨 TU**反查到其 prior write site。

**不证明**完整 syscall 序列、不做 LLM 谓词推断、不做 fd 身份档、不做跨对象联合门控完整反查(被引用对象存活 + release 不得前置)。这些留后续,v1 仅留数据位。

---

## 2. 架构选型

沿用"先门控、后反查":反查挂在 `gate_seed_candidate` **之后**,不直接从 `branch_fact` 起。复用已有门控骨架、语义连贯。派生链新增一环:

```
branch_fact ─▶ gate_seed_candidate ─▶ gate_prior_write_candidate  (本 spec 新增)
   (已有)      (加字段键列, 兼容式)         (跨 TU 符号反查, 新)
```

否决:直接从 `branch_fact` 反查绕过 gate 表——更省事但偏离设计闭环、丢门控语义标注。

---

## 3. 组件设计

### 组件 1 — 字段键归一化 (共用 helper)

新 helper `field_key_for_access(...)`,把一个 access_fact 映射到规范"字段键",供门控生成与反查共用。**同一个键既判"字段是否在别处被写(state carrier)",也做反查 JOIN**,两处必须一致。

| 条件 | 键 | kind | confidence | v1 |
|---|---|---|---|---|
| 有 `access_path_symbolic` | `sym:<符号路径>`(如 `sym:io_ring_ctx.file_data`) | `symbolic` | 继承 access_fact 原 confidence | **落地** |
| 无符号路径(numeric-only) | `num:<canon_struct>@<offset>` | `numeric` | 严格低于 symbolic | **v1 返回 None**(facts 无 struct type,见 §0);留后续 |

**numeric 三规则(后续升级约定,v1 不执行):** ① canonicalize struct type:剥 `struct/const/volatile/typedef` 噪声,归一化集中一处、单测覆盖;② numeric key 只在同一 canonical struct type 内匹配,**绝不 offset-only**;③ numeric confidence 固定低于 symbolic,结果强制暴露 `basis`,供论文解释"调试信息不足时的保守降级"。

v1 行为:符号→`sym:` 键;numeric-only→`None`(不产生键、不参与匹配)。

### 组件 2 — gate_seed_candidate 扩到字段键 (兼容式扩列)

**保守语义,旧列不漂移:**

- **旧列**(`gated_fields_json` 等)继续只放 symbolic 字段,向后兼容;旧查询/展示不受影响。
- **新增列**(派生表加列,不改 schema-bound fact,`schema_version` 恒 1.0.0):
  - `gated_field_keys_json` — 被门控字段键列表;
  - `state_field_keys_json` — 参与判定的 state-carrier 字段键列表;
  - 每个 key 元素:`{ "kind": "symbolic|numeric", "key": "...", "confidence": "...", "source_access_fact_id": <int> }`。
- gate 生成逻辑以 field key 为准。v1 中字段键只会是 `symbolic`(numeric 分支返回 None),故新列在 v1 仅含 symbolic key——但**结构就位**,后续升级只需 helper 开始产出 `num:` 键即自动生效。

### 组件 3 — gate_prior_write_candidate (反查派生表, 新)

对每个 `gate_seed_candidate` × 每个被门控字段键,**跨 TU** 找出所有 `write` semantic_op 且字段键相同的 access_fact,每条匹配产出一行:

```sql
CREATE TABLE IF NOT EXISTS gate_prior_write_candidate (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gate_seed_id INTEGER NOT NULL,
  field_key TEXT NOT NULL,
  field_key_kind TEXT NOT NULL,             -- symbolic (v1) | numeric (后续)
  write_access_fact_id INTEGER NOT NULL,
  write_function TEXT NOT NULL,
  write_bc_unit TEXT NOT NULL,
  write_source_location TEXT,               -- 从 source_location_json.expansion 取
  write_base_object_json TEXT,
  object_identity_edge_ids_json TEXT,       -- §3.2 对象归属留位 (v1 可空)
  entry_attribution_json TEXT,              -- 组件 4: 可靠时的 opcode/role, 否则 null
  basis TEXT NOT NULL,                      -- symbolic_field_key | numeric_field_key
  confidence TEXT NOT NULL,                 -- symbolic→medium
  status TEXT NOT NULL,                     -- awaiting_llm_predicate_and_execution_verification
  UNIQUE(gate_seed_id, field_key, write_access_fact_id)
);
```

要点:**跨 TU**——JOIN 不限 bc_unit(与 `state_write_read` 的 same-bc_unit 不同),写在 rsrc、读在 rw,单 TU 必然断裂;**对象归属**——带写点 `base_object_json` + 命中的 object_identity 边 id(v1 留位);**confidence**——符号 medium;**basis** 强制暴露;**status** 统一 `awaiting_llm_predicate_and_execution_verification`。

### 组件 4 — 前序"调用"归属 (v1 从简 + 防 overclaim)

v1 每行输出:`write_function` + `write_bc_unit` + `write_source_location` + **可选** entry 归属。**防 overclaim**:`entry_fact` 的 opcode/role 只在写点 function **本身就是某 opcode handler** 时附上;若写点在 handler **深层 callee** 且无可靠 call-chain/reachability,**只报 function、不推断 opcode**(`entry_attribution_json = null`)。宁少报不虚报。

---

## 4. 跨 TU 与验收

- **运行形态**:反查在**多 TU 合并 DB** 上跑。语料:至少 `rw` + `rsrc`,理想全 26-TU(`/tmp/implicitfuzz-io_uring-all/bc/`)。
- **端到端锚点 (v1)**:`io_ring_ctx.file_data`(rsrc 内 W2/R6,符号)——若某 TU 内它门控某分支,反查应命中 rsrc 写点;若单 TU 内读写同现,用 rw+rsrc 合并 DB 演示跨 TU。**具体锚点在实现第一步用真实合并 DB 实测确定**(哪个符号字段既被门控又在另一 TU 有写)。
- **回归**:`run_phase1_regression.sh` → `[phase1] ALL PASS`;`pytest tests/` → 现有 23 + 新增全绿。
- **新增单测**:字段键归一化(symbolic 命中 / numeric-only→None);gate_seed 字段键列(symbolic key 入新列、旧列不变);反查 JOIN(符号跨 TU 命中 / 无匹配写不产行 / basis+confidence 正确 / source_location 从 json 正确取)。

---

## 5. 不做 (YAGNI)

跨对象联合门控完整反查(留 `object_identity_edge_ids_json` 位)、LLM 谓词推断、syscall/opcode 级完整归属(组件 4 只做可靠 handler 匹配)、fd 身份档、改 schema-bound fact。

---

## 6. 影响文件 (v1, 实现以计划为准)

- `src/implicitfuzz/evidence/field_key.py` (新) — 字段键 helper(v1 符号;numeric 分支 return None + canonicalize stub 留位)。
- `src/implicitfuzz/evidence/gates.py` — 加 `gated_field_keys_json` / `state_field_keys_json` 列,gate 逻辑走字段键。
- `src/implicitfuzz/evidence/reverse_lookup.py` (新) — `gate_prior_write_candidate` 派生 + summary。
- `tests/test_field_key.py`, `tests/test_reverse_lookup.py` (新);`tests/test_gate_seeds.py` 加新列断言。
- `extraction/RUNBOOK.md` / `docs/phase2d-state-gates.md` — 文档更新。

---

## 7. 后续计划 (紧接 v1, 不在本 spec 落地)

**抽取器符号化升级 (C++, 服务器)**:让抽取器对 rsrc 写侧 `io_ring_ctx.nr_user_files`/`file_table` 等 numeric-only 字段做 DWARF byte-offset→member 恢复,提升为符号字段(现失败多因 base 指针 struct type 在 -O2 / opaque-pointer 下丢失)。产出后,§4 的纯符号反查自然覆盖旗舰 `sqe->buf_index < ctx->nr_user_files` 门控,无需数值兜底。**注意坑#1**:SVF `getLLVMValue()`/`getObjectNode()` 只有 assert 保护,Release 段错误——先 `has*()` 检查。此升级风险/耗时高于 v1,故排在反查逻辑落地并单测通过之后。
