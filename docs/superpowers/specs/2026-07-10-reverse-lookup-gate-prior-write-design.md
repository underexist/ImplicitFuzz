# 反查设计 — gate → 前序写调用 (gate_prior_write_candidate)

**Date:** 2026-07-10
**Branch:** `phase2b-evidence-identity`
**Scope:** 研究内容一 · 状态门控视图的"先门控、后反查"派生环节 (purpose.md §3.2 / §4.2(2) 第34段)
**Status:** design approved, awaiting implementation plan (writing-plans)

---

## 1. 目标与边界

`gate_seed_candidate` 目前只是"门控骨架"——它标出"某分支被某对象字段门控",但**尚不是隐式依赖**。按设计的"先门控、后反查":拿门控里的被门控字段去证据库反查"谁写了这个字段",得到"设置该状态的前序写点",才把门控转成该分支的**隐式前序依赖候选**。本 spec 定义这一反查环节。

**v1 要证明的唯一命题:**

> 被门控字段能**跨 TU**反查到其 prior write site。

**不证明**完整 syscall 序列、不做 LLM 谓词推断、不做 fd 身份档、不做跨对象联合门控的完整反查(被引用对象存活 + release 不得前置)。这些留后续,v1 仅留数据位。

### 关键前置发现 (实测 rsrc.bc, 2026-07-10)

`io_uring/rsrc.c` → 1161 facts,其中 access facts **symbolic=180 / numeric_only=403**(~31% 符号化)。旗舰例子 `sqe->buf_index < ctx->nr_user_files` 里的 `ctx->nr_user_files` **不在符号字段集**,落在 403 条 numeric-only(byte-offset)里。已符号化且写读耦合的 `io_ring_ctx.*` 写字段有:`file_data`(W2/R6)、`buf_data`(W2/R7)、`rsrc_backup_node`(W8/R6)、`file_alloc_start/end`。

**结论(经用户决策):反查采用"符号字段为主 + 数值兜底"**,否则 `nr_user_files` 会在 gate 生成侧就丢失,反查再聪明也救不回来。数值兜底同时约束在同一 canonical struct type 内、且置信严格低于符号。

---

## 2. 架构选型

沿用"先门控、后反查":反查挂在 `gate_seed_candidate` **之后**,不直接从 `branch_fact` 起。

- **采用**:gate → 反查。复用已有门控骨架,语义连贯;代价是 gate 生成也要升级到字段键。
- **否决**:直接从 `branch_fact` 反查、绕过 gate 表。更省事但偏离设计闭环,且丢掉门控语义标注。

派生链新增一环:

```
branch_fact ─▶ gate_seed_candidate ─▶ gate_prior_write_candidate  (本 spec 新增)
   (已有)          (扩字段键)               (跨 TU 反查, 新)
```

---

## 3. 组件设计

### 组件 1 — 字段键归一化 (共用 helper)

新 helper,把一个 access_fact 映射到规范"字段键",供门控生成与反查共用。这是整个设计的枢纽:**同一个键既用于"该字段是否在别处被写(state carrier 判定)",也用于反查的 JOIN**,两处必须一致,否则 numeric-only 字段在 gate 侧丢失后反查无从补救。

字段键定义:

| 条件 | 键 | kind | confidence |
|---|---|---|---|
| 有 `access_path_symbolic` | `sym:<符号路径>`(如 `sym:io_ring_ctx.file_data`) | `symbolic` | 继承 access_fact 原 confidence |
| 无符号路径,但能恢复 (canonical struct type + byte-offset) | `num:<canon_struct>@<offset>`(如 `num:io_ring_ctx@120`) | `numeric` | 严格低于 symbolic(见规则 3) |
| 两者都拿不到(纯 offset、无结构体类型) | — 不产生键,不参与匹配 | — | — |

**三条硬规则(用户指定):**

1. **canonicalize struct type**:`num:` 键的 `struct_type` 必须归一化——剥掉 `struct` / `const` / `volatile` / `typedef` 别名等噪声与空白,避免同一类型不同拼法(`struct io_ring_ctx` vs `io_ring_ctx` vs `const struct io_ring_ctx`)被当成不同字段。归一化函数集中一处,单测覆盖各种拼法。
2. **numeric key 只在同一 canonical struct type 内匹配**,坚决**不做 offset-only 匹配**(不同结构体同偏移是不同字段)。struct type 恢复不了 → 该 access 不产生 numeric 键、直接不参与。
3. **numeric 键 confidence 固定低于 symbolic**,且结果里**强制暴露 `basis`**(`symbolic_field_key` / `numeric_field_key`),便于论文里解释"数值兜底是调试信息不足时的保守降级"。

字段键的数据来源:`access_path_symbolic`、`numeric_kind` / `access_path_numeric`(byte-offset)、以及可恢复的结构体类型(`field_type` / `base_object` / DWARF)。实现时先实测 numeric-only access 上结构体类型的可得性;若普遍拿不到,则 numeric 兜底命中率有限,如实记录。

### 组件 2 — gate_seed_candidate 扩到字段键 (兼容式扩列)

**保守语义,不让旧列漂移**(用户指定):

- **旧列**(`gated_fields_json` / 以及 `_state_fields` 所对应的语义)**继续只放 symbolic 字段**,保持向后兼容;旧查询、旧展示不受影响。
- **新增列**(派生表加列,不改 schema-bound fact,`schema_version` 恒 1.0.0):
  - `gated_field_keys_json` — 该门控的被门控字段键列表;
  - `state_field_keys_json` — 参与判定的 state-carrier 字段键列表;
  - 每个 key 元素结构:`{ "kind": "symbolic|numeric", "key": "...", "confidence": "...", "source_access_fact_id": <int> }`。
- **gate 生成逻辑以 field key 为准**:`_state_fields`(state carrier = 在某处被 write 的字段)与 gated field 匹配都改用字段键(符号 + 数值兜底);这样 numeric-only 门控分支(如 `nr_user_files`)也能生成 gate 骨架。旧列仍按 symbolic 子集回填。

### 组件 3 — gate_prior_write_candidate (反查派生表, 新)

对每个 `gate_seed_candidate` × 每个被门控字段键,**跨 TU** 找出所有 `write` semantic_op 且字段键相同的 access_fact,每条匹配产出一行。

派生表(derived,非 schema-bound):

```sql
CREATE TABLE IF NOT EXISTS gate_prior_write_candidate (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gate_seed_id INTEGER NOT NULL,            -- FK gate_seed_candidate.id
  field_key TEXT NOT NULL,                  -- 被门控字段键 (sym:.. / num:..)
  field_key_kind TEXT NOT NULL,             -- symbolic | numeric
  write_access_fact_id INTEGER NOT NULL,    -- FK access_fact.id (前序写点)
  write_function TEXT NOT NULL,             -- 写点 containing function
  write_bc_unit TEXT NOT NULL,
  write_source_location TEXT,               -- 源码位置 (file:line)
  write_base_object_json TEXT,              -- 写点 base_object / object_scope
  object_identity_edge_ids_json TEXT,       -- 命中的 object_identity 候选 (§3.2 对象归属留位)
  entry_attribution_json TEXT,              -- 组件 4: 可靠时的 opcode/role, 否则 null
  basis TEXT NOT NULL,                      -- symbolic_field_key | numeric_field_key
  confidence TEXT NOT NULL,                 -- symbolic→medium, numeric→low
  status TEXT NOT NULL,                     -- awaiting_llm_predicate_and_execution_verification
  UNIQUE(gate_seed_id, field_key, write_access_fact_id)
);
```

要点:
- **跨 TU**:JOIN 不限 bc_unit(与 `state_write_read` 的 same-bc_unit 不同)——写在 rsrc、读在 rw,单 TU 必然断裂,跨 TU 正是本环节价值所在。
- **对象归属**:带上写点 `base_object` / `object_scope`,以及命中的 `object_identity_candidate` 边 id,为 purpose §3.2 "谓词项按对象归属分别反查"留数据位(v1 不做联合门控合取)。
- **confidence**:符号匹配 medium,数值匹配 low(规则 3);`basis` 强制暴露。
- **status**:统一 `awaiting_llm_predicate_and_execution_verification`——反查产出是候选,谓词语义与真伪由后续 LLM + 执行验证收口。

### 组件 4 — 前序"调用"归属 (v1 从简 + 防 overclaim)

v1 每行输出:`write_function` + `write_bc_unit` + `write_source_location` + **可选** entry 归属。**不做 syscall/opcode 级完整归属**。

**防 overclaim 规则(用户指定):**
- `entry_fact` 的 opcode/role **只在能明确匹配 handler function 时**附上(写点 function 本身就是某 opcode 的 handler);
- 若写点在 handler 的**深层 callee** 里、而当前没有可靠 call-chain / reachability 证据,**只报 function 名,不推断 opcode**(`entry_attribution_json = null`);
- 宁可少报,不做没有可达性证据支撑的 opcode 归属。

---

## 4. 跨 TU 与验收

### 运行形态
反查在**多 TU 合并 DB**上跑(ingest 多个 `.bc` facts 到同一 SQLite)。语料:至少 `rw` + `rsrc`(读侧 + 写侧),理想全 26-TU(`/tmp/implicitfuzz-io_uring-all/bc/`)。

### 端到端锚点
1. **旗舰**:`nr_user_files`(或 `sqe->buf_index < ctx->nr_user_files` 门控)反查到 rsrc 里 register 家族的写点。
2. **符号案例**:`io_ring_ctx.file_data`(W2/R6)一并跑通,证明符号主路径工作。

### 已知风险与降级
- 若 `nr_user_files` 连数值键都恢复不了(numeric-only access 上结构体类型缺失)→ 降级用 `file_data` 作展示锚点,并把 `nr_user_files` 记为"待符号化"的已知缺口。**实现第一步先实测确认 numeric-only access 的 struct type 可得性**,再决定旗舰锚点。

### 回归
- `extraction/scripts/run_phase1_regression.sh` → `[phase1] ALL PASS`。
- `python3 -m pytest tests/` → 现有 23 + 新增全绿。
- 新增单测:
  - 字段键归一化(symbolic / numeric / struct-type canonicalize 各拼法 / 拿不到键→不匹配);
  - gate_seed 数值扩展(numeric-only gated 分支能生成 gate;旧列仍只放 symbolic);
  - 反查 JOIN(符号跨 TU 命中 / 数值同 canonical struct 命中 / 不同 struct 同 offset **不**命中 / basis 与 confidence 正确)。

---

## 5. 不做 (YAGNI)

- 跨对象联合门控的完整反查(被引用对象存活 + release 不得前置)——只留 `object_identity_edge_ids_json` 数据位。
- LLM 谓词推断(premise/activation/param-align)——下一步,不在此 spec。
- syscall/opcode 级完整归属——组件 4 只做可靠 handler 匹配。
- fd-indirection 身份档。
- 改动 schema-bound fact(`schema_version` 恒 1.0.0,只加派生表列)。

---

## 6. 影响文件 (预估, 实现时以计划为准)

- `src/implicitfuzz/evidence/field_key.py` (新) — 字段键归一化 helper + struct type canonicalize。
- `src/implicitfuzz/evidence/gates.py` — 扩字段键、加新列。
- `src/implicitfuzz/evidence/reverse_lookup.py` (新) — `gate_prior_write_candidate` 派生。
- `tests/` — 三组新单测。
- `extraction/RUNBOOK.md` / `docs/phase2d-state-gates.md` — 文档更新。
- 服务器工作流:本地改 → scp → 服务器 build/test/`git commit -F`。
