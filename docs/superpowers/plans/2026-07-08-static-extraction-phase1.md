# 静态抽取层 · 阶段一（SVF21 最小链路）+ 开工前支撑件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **本计划的任务不是严格 TDD 逐步骤格式**——按用户要求采用"分阶段工程路线图"：每个任务给出精确文件路径、完整代码/配置内容、验证命令与预期输出，但不强制"先写失败测试"的仪式化步骤（Track A7 的 Python ingestion 例外，它天然适合测试先行）。

**Goal:** 把 `docs/静态抽取层技术路线_v3.md` + `docs/静态抽取层技术路线_v3.1补丁.md` 中「阶段一：SVF21 最小链路」（v3 §12）与「开发前必须先落地的支撑件」（v3 §14）落成可执行、可验收的产物：schema/manifest/primitive_summary 契约文件、SQLite ingestion 骨架、SVF/LLVM21 Linux 工具链、一个跑通 bitcode→SVF→JSONL→SQLite 的最小 driver，并给出「SVF 在 LLVM 21.1.0 下 opaque pointer 处理成熟度」这一头号风险的实测结论。

**Architecture:** 双语言、进程边界通信（v3 §1.1）：`extraction/`（C++，基于 SVF+LLVM，Linux-only 构建）产出结构化事实 JSONL；`src/implicitfuzz/`（Python）消费 JSONL 写入 SQLite，传递闭包留给 SQL 递归 CTE（本计划不实现闭包查询，只落 ingestion）。两侧以 JSON Schema 定义的 fact 契约为唯一耦合点。

**Tech Stack:** LLVM 21.1.0（SVF `build.sh` pin 的精确 patch 版本）、SVF（固定 commit）、Z3（建议同一 clang++ 源码编译）、CMake、Python 3.8+、`jsonschema`、SQLite3（Python 内置 `sqlite3` 模块）、PyYAML（读 `primitive_summary.yaml`）。

## Global Constraints

- LLVM 主干版本精确锁定 **21.1.0**（不是 21.1.8），与编译内核用的 clang 版本必须一致，写入 manifest（v3 §2.1）。
- SVF 主干固定一个**已验证的具体 commit**（完整 SHA），不依赖会移动的 HEAD；升级需重跑全部 golden tests（v3.1 P1）。
- 优化等级默认 `-O2 -g`（v3 §3.3 默认倾向），仅当字段恢复率不达标才回退 `-Og`/`-O0`；本阶段（阶段一）允许 access path 仅为 numeric，不要求符号化命中率。
- `semantic_op`（不是 `op`）与 `access_kind` 是两个正交维度，字段命名与枚举值必须逐字匹配 v3.1 P2/v3 §5.4（见 Task A2）。
- `provenance` 是数组，`primary_provenance` 是单值主导来源（v3.1 P4）。
- `instruction_id` 只承诺 **build-local stable**，不得用来跨优化档对比；跨档对比一律用 `cross_opt_key`（v3.1 P7/P7.1）。
- `object_scope = formal_param` 或 `synthetic` 的对象**不得**被当作身份等价直接使用，只能作 relation evidence（v3.1 P6，正确性关键约束，ingestion 与 schema 都要体现）。
- "失败不丢事实"：任何置信度档位都必须产出 fact，并写明 `numeric_kind`；不得为了填满字段而伪造 GEP 偏移（v3 §5.4/§11）。
- Mac 环境已确认不适合编译/运行 SVF+LLVM21（`docs/svf-extraction-toolchain-notes.md` 记录了 dyld two-level namespace、libc++/libc++abi 拆分、libz3 ABI 不兼容段错误等一系列坑，未能在 macOS 上跑通）。**Track B 的所有任务必须在 Linux 服务器会话中执行**，Track A 的任务不依赖 LLVM/SVF，可在当前 Mac/worktree 会话完成。

---

## 总览：任务分轨

| 轨道 | 内容 | 执行环境 |
|---|---|---|
| Track A（A1–A8） | schema/manifest/primitive_summary 契约、SQLite ingestion、C++ 子项目骨架 | 当前会话（Mac/worktree），纯文本/Python，无需 LLVM/SVF |
| Track B（B1–B8） | LLVM21 工具链、SVF 构建、内核 bitcode、driver 实现与联调 | **必须**在 Linux 服务器会话执行 |

Track A 产出的 schema 是 Track B driver 输出 JSONL 的契约，因此 **A 必须先于 B**。B 内部任务顺序不可打乱（工具链→SVF→bitcode→driver→联调→manifest）。

---

## Track A：契约与骨架（本会话可完成）

### Task A1: 目标内核版本与优化档决策记录

**Files:**
- Create: `docs/kernel-target-decision.md`

**Interfaces:**
- Produces: 后续所有任务引用的 `kernel_version`、默认 `opt_level` 取值，供 A3 manifest schema 示例、B3/B4 使用。

- [ ] **写决策文档**

```markdown
# 内核目标版本与编译参数决策（阶段一）

## 决策
- 目标内核版本：**Linux v6.6**（LTS，`ZHYfeng/Generate_Linux_Kernel_Bitcode` 有现成 v6.6 支持脚本，降低 bitcode 生成的工程风险）。
- 目标子系统/TU：`io_uring`（与 v3/purpose.md 的研究目标一致）；阶段一先选 io_uring 内**一个自包含度较高、不深度依赖跨子系统符号**的单个 `.c` 文件作为 TU 级冒烟目标（v3 §3.5「TU 级」档位），具体文件在 B3 执行时依据实际内核树确定并回填本文档。
- 默认优化档：**`-O2 -g`**（v3 §3.3 默认倾向：贴近真实构建、别名分析精度/规模更好）。若阶段一在该档位下字段信息完全丢失（GEP 链信息被优化抹除到无法验证），回退 `-Og`，仍不行再回退 `-O0`，并将实际采用档位记录进 manifest。
- 阶段一**不要求**跑满 `-O0`/`-Og`/`-O2 -g` 三档对照矩阵（那是 v3 §12 阶段二的验收范围），只需单一默认档位跑通链路。

## 依据
- v3 §3.3：LLIF 在 Linux 5.14.11、`-O2 -g` 下已验证可恢复 container_of 字段，`-O2 -g` 并非不可行路线。
- v3 §3.2：`ZHYfeng/Generate_Linux_Kernel_Bitcode` 明确支持 v6.1/v6.6。

## 待回填（Track B 执行时更新本文件）
- [ ] 实际选定的 TU 文件路径
- [ ] 实际验证通过的 clang/LLVM patch 版本（预期 21.1.0）
- [ ] 若发生优化档回退，记录回退原因
```

- [ ] **提交**

```bash
git add docs/kernel-target-decision.md
git commit -m "docs: pin phase-1 kernel target (v6.6 io_uring) and default opt level (-O2 -g)"
```

---

### Task A2: Fact JSONL Schema（entry/call/access/alias/branch/gate_seed）

**Files:**
- Create: `extraction/schema/facts/common.schema.json`
- Create: `extraction/schema/facts/entry_fact.schema.json`
- Create: `extraction/schema/facts/call_fact.schema.json`
- Create: `extraction/schema/facts/access_fact.schema.json`
- Create: `extraction/schema/facts/alias_fact.schema.json`
- Create: `extraction/schema/facts/branch_fact.schema.json`
- Create: `extraction/schema/facts/gate_seed_fact.schema.json`

**Interfaces:**
- Produces: 供 A7 ingestion 校验使用的 JSON Schema 文件（`fact_type` 字段作为 JSONL 每行的判别字段）；供 B5 driver 实现时对照字段名/类型的权威契约。
- Consumes: 无（本任务是最底层契约定义）。

这是 driver（Track B）与 ingestion（Task A7）之间**唯一**的耦合点，字段名/枚举值必须逐字对应 v3 §5 + v3.1 全部补丁。

- [ ] **写公共字段 schema**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/schema/facts/common.schema.json",
  "title": "CommonFactEnvelope",
  "type": "object",
  "properties": {
    "fact_type": {
      "type": "string",
      "enum": ["entry_fact", "call_fact", "access_fact", "alias_fact", "branch_fact", "gate_seed_fact"]
    },
    "schema_version": { "type": "string", "const": "1.0.0" },
    "kernel_version": { "type": "string" },
    "llvm_version": { "type": "string", "description": "精确到 patch，如 21.1.0" },
    "opt_level": { "type": "string", "enum": ["-O0", "-Og", "-O2 -g"] },
    "bc_unit": { "type": "string" },
    "function": { "type": "string" },
    "source_location": {
      "type": ["object", "null"],
      "properties": {
        "spelling": { "type": "string", "description": "file:line，宏调用书写位置" },
        "expansion": { "type": "string", "description": "file:line，宏展开后位置，cross_opt_key 用这个分量" },
        "inlined_at": { "type": "array", "items": { "type": "string" } }
      },
      "required": ["spelling", "expansion", "inlined_at"]
    },
    "primary_provenance": {
      "type": "string",
      "enum": ["svf", "dwarf", "dwarf_partial", "btf", "btf_partial", "typecopilot", "container_of_heuristic", "numeric_fallback", "summary"]
    },
    "provenance": {
      "type": "array",
      "items": { "type": "string", "enum": ["svf", "dwarf", "dwarf_partial", "btf", "btf_partial", "typecopilot", "container_of_heuristic", "numeric_fallback", "summary"] },
      "minItems": 1
    },
    "confidence": { "type": "string", "enum": ["high", "medium_high", "medium", "medium_low", "low"] }
  },
  "required": ["fact_type", "schema_version", "kernel_version", "llvm_version", "opt_level", "bc_unit", "function", "primary_provenance", "provenance", "confidence"]
}
```

- [ ] **写 `entry_fact.schema.json`**（v3 §5.2，补 io_uring 异步入口枚举）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/schema/facts/entry_fact.schema.json",
  "allOf": [{ "$ref": "common.schema.json" }],
  "properties": {
    "entry_kind": {
      "type": "string",
      "enum": ["syscall", "file_op", "callback", "workqueue", "ioctl", "uring_cmd", "task_work", "io_worker", "sqpoll_thread", "completion_path", "timeout_callback", "cancel_path"]
    },
    "entry_symbol": { "type": "string" },
    "associated_syscall": { "type": ["string", "null"] }
  },
  "required": ["entry_kind", "entry_symbol"]
}
```

- [ ] **写 `call_fact.schema.json`**（v3 §5.3，上下文敏感字段预留但只填 `call_chain_hash`）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/schema/facts/call_fact.schema.json",
  "allOf": [{ "$ref": "common.schema.json" }],
  "properties": {
    "caller": { "type": "string" },
    "callee": { "type": "string" },
    "call_site": { "type": "string", "description": "instruction_id" },
    "is_indirect": { "type": "boolean" },
    "resolution_method": { "type": "string", "enum": ["direct", "mlta", "pta"] },
    "callee_candidates": { "type": "array", "items": { "type": "string" } },
    "context_depth": { "type": ["integer", "null"], "description": "阶段一预留字段，不填" },
    "call_chain_hash": { "type": "string", "description": "第一版唯一必填的弱上下文标识" },
    "entry_context": { "type": ["string", "null"], "description": "阶段一预留字段，不填" },
    "instruction_id": { "type": "string" },
    "svf_node_id": { "type": "string" }
  },
  "required": ["caller", "callee", "call_site", "is_indirect", "resolution_method", "call_chain_hash", "instruction_id", "svf_node_id"]
}
```

- [ ] **写 `access_fact.schema.json`**（v3 §5.4/§5.5/§5.6 + v3.1 P2/P5/P6/P7，本计划的核心 schema）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/schema/facts/access_fact.schema.json",
  "allOf": [{ "$ref": "common.schema.json" }],
  "properties": {
    "semantic_op": {
      "type": "string",
      "enum": ["alloc", "read", "write", "free", "free_async", "retain", "release", "escape"],
      "description": "free_async 专指 kfree_rcu/call_rcu 等延迟释放，不与 free 等价"
    },
    "access_kind": {
      "type": "string",
      "enum": ["direct_load", "direct_store", "memcpy", "memset", "atomic", "usercopy", "call_arg"],
      "description": "地址逃逸只记 semantic_op=escape，这里不设 address_escape"
    },
    "base_object": {
      "type": "object",
      "description": "object_id 标签联合类型，见 v3 §5.6",
      "properties": {
        "object_scope": { "type": "string", "enum": ["allocation_site", "global", "formal_param", "synthetic"] },
        "value": { "type": "string", "description": "allocation_site_id(instruction_id) | global_symbol(name) | \"function:arg_index\" | synthetic_stub_object(summary_name)，按 object_scope 解释" }
      },
      "required": ["object_scope", "value"]
    },
    "object_scope": { "type": "string", "enum": ["allocation_site", "global", "formal_param", "synthetic"] },
    "access_path_symbolic": { "type": ["string", "null"] },
    "numeric_kind": { "type": "string", "enum": ["gep_offsets", "byte_range", "whole_object", "unknown"] },
    "access_path_numeric": { "type": ["string", "null"], "description": "按 numeric_kind 解释；unknown 时可为空" },
    "field_type": { "type": ["string", "null"] },
    "access_path_recovery": { "type": "string", "enum": ["dwarf", "dwarf_partial", "btf", "btf_partial", "container_of_heuristic", "numeric_only"] },
    "instruction_id": { "type": "string", "description": "module_hash:function:bb_ordinal:inst_ordinal:ir_hash，build-local stable" },
    "svf_node_id": { "type": "string" },
    "cross_opt_key": {
      "type": "string",
      "description": "source_location.expansion + access_path_key + function + semantic_op；access_path_key 三级降级 symbolic_path||numeric_repr||source_snippet_hash（v3.1 P7.1）"
    }
  },
  "required": ["semantic_op", "access_kind", "base_object", "object_scope", "numeric_kind", "access_path_recovery", "instruction_id", "svf_node_id", "cross_opt_key"]
}
```

- [ ] **写 `alias_fact.schema.json`**（v3 §5.7，只存 points-to，不存全量两两 alias）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/schema/facts/alias_fact.schema.json",
  "allOf": [{ "$ref": "common.schema.json" }],
  "properties": {
    "object_id": {
      "type": "object",
      "properties": {
        "object_scope": { "type": "string", "enum": ["allocation_site", "global", "formal_param", "synthetic"] },
        "value": { "type": "string" }
      },
      "required": ["object_scope", "value"]
    },
    "points_to_set": { "type": "array", "items": { "type": "string" } },
    "relation_evidence": { "type": "string", "description": "产生该关系的指令 instruction_id 或值流边标识" },
    "pta_kind": { "type": "string", "enum": ["andersen", "flow_sensitive"] }
  },
  "required": ["object_id", "points_to_set", "relation_evidence", "pta_kind"]
}
```

- [ ] **写 `branch_fact.schema.json`** 与 `gate_seed_fact.schema.json`（v3 §5.8/v3.1 P12；阶段一 driver 不必产出这两类 fact，但 schema 先落地供阶段二直接复用，避免字段返工）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/schema/facts/branch_fact.schema.json",
  "allOf": [{ "$ref": "common.schema.json" }],
  "properties": {
    "branch_instruction_id": { "type": "string" },
    "condition_value": { "type": "string" },
    "control_deps": { "type": "array", "items": { "type": "string" } },
    "related_loads": { "type": "array", "items": { "type": "string" } },
    "slice_range": { "type": "string" }
  },
  "required": ["branch_instruction_id", "condition_value", "control_deps", "related_loads", "slice_range"]
}
```

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/schema/facts/gate_seed_fact.schema.json",
  "allOf": [{ "$ref": "common.schema.json" }],
  "properties": {
    "target_symbol": { "type": "string" },
    "gate_kind": { "type": "string" },
    "predicate_summary": { "type": "string" },
    "related_objects": { "type": "array", "items": { "type": "string" } },
    "related_access_facts": { "type": "array", "items": { "type": "string" } }
  },
  "required": ["target_symbol", "gate_kind", "predicate_summary", "related_objects", "related_access_facts"]
}
```

- [ ] **本地校验 schema 本身是合法 JSON**

```bash
python3 -c "
import json, glob
for f in sorted(glob.glob('extraction/schema/facts/*.schema.json')):
    json.load(open(f))
    print('OK', f)
"
```
Expected: 7 个文件全部打印 `OK`，无异常。

- [ ] **提交**

```bash
git add extraction/schema/facts/
git commit -m "feat(extraction): define JSONL fact schemas per v3/v3.1 spec"
```

---

### Task A3: 构建产物 manifest schema

**Files:**
- Create: `extraction/schema/manifest.schema.json`
- Create: `extraction/schema/manifest.example.json`

**Interfaces:**
- Consumes: A1 的 `kernel_version`/`opt_level` 决策。
- Produces: B8 要填充的 manifest 实例契约。

- [ ] **写 schema**（v3 §14.4：`.bc`/`.ll`/compile_commands/bc.list/debug info/kernel config/精确 clang+LLVM patch/svf_commit/opt_level/BTF/ID 生成规则；v3.1 P1 svf_commit 必须完整 SHA；v3 §3.3.1 BTF 字段）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/schema/manifest.schema.json",
  "title": "BuildManifest",
  "type": "object",
  "properties": {
    "manifest_version": { "type": "string", "const": "1.0.0" },
    "created_at": { "type": "string", "format": "date-time" },
    "host": { "type": "string", "enum": ["linux-x86_64", "linux-aarch64"], "description": "阶段一只允许 linux；Mac 不产出 manifest 实例" },
    "kernel_version": { "type": "string" },
    "kernel_config_path": { "type": "string" },
    "kernel_config_hash": { "type": "string" },
    "subsystem_scope": { "type": "string" },
    "bc_files": { "type": "array", "items": { "type": "string" } },
    "ll_files": { "type": "array", "items": { "type": "string" } },
    "compile_commands_json": { "type": "string" },
    "bc_list": { "type": "string" },
    "debug_info_kind": { "type": "string", "enum": ["dwarf", "dwarf_partial", "none"] },
    "btf_available": { "type": "boolean" },
    "pahole_version": { "type": ["string", "null"] },
    "vmlinux_btf_path": { "type": ["string", "null"] },
    "clang_version": { "type": "string", "description": "精确到 patch" },
    "llvm_version": { "type": "string", "const": "21.1.0" },
    "svf_commit": { "type": "string", "pattern": "^[0-9a-f]{40}$" },
    "opt_level": { "type": "string", "enum": ["-O0", "-Og", "-O2 -g"] },
    "id_generation_rule_version": { "type": "string", "description": "instruction_id/object_id 生成规则版本号，规则变更必须递增" }
  },
  "required": [
    "manifest_version", "created_at", "host", "kernel_version", "subsystem_scope",
    "bc_files", "debug_info_kind", "btf_available", "clang_version", "llvm_version",
    "svf_commit", "opt_level", "id_generation_rule_version"
  ]
}
```

- [ ] **写一份占位示例**（字段值全部是待 B8 回填的可识别占位符，用于 A7 ingestion 测试与人工核对字段名，不是"伪造真实数据"）

```json
{
  "manifest_version": "1.0.0",
  "created_at": "2026-07-08T00:00:00Z",
  "host": "linux-x86_64",
  "kernel_version": "v6.6",
  "kernel_config_path": "PENDING_TRACK_B",
  "kernel_config_hash": "PENDING_TRACK_B",
  "subsystem_scope": "io_uring (single TU, phase-1 smoke)",
  "bc_files": ["PENDING_TRACK_B"],
  "ll_files": [],
  "compile_commands_json": "PENDING_TRACK_B",
  "bc_list": "PENDING_TRACK_B",
  "debug_info_kind": "dwarf",
  "btf_available": false,
  "pahole_version": null,
  "vmlinux_btf_path": null,
  "clang_version": "PENDING_TRACK_B",
  "llvm_version": "21.1.0",
  "svf_commit": "0000000000000000000000000000000000000000",
  "opt_level": "-O2 -g",
  "id_generation_rule_version": "1.0.0"
}
```

- [ ] **校验**

```bash
python3 -m pip install --quiet jsonschema
python3 -c "
import json, jsonschema
schema = json.load(open('extraction/schema/manifest.schema.json'))
instance = json.load(open('extraction/schema/manifest.example.json'))
jsonschema.validate(instance, schema)
print('manifest example is schema-valid (with PENDING_TRACK_B placeholders)')
"
```
Expected: 打印成功信息，无 `ValidationError`。

- [ ] **提交**

```bash
git add extraction/schema/manifest.schema.json extraction/schema/manifest.example.json
git commit -m "feat(extraction): define build manifest schema"
```

---

### Task A4: `primitive_summary.yaml` schema 与首批条目

**Files:**
- Create: `extraction/schema/primitive_summary.schema.json`
- Create: `extraction/schema/primitive_summary.yaml`

**Interfaces:**
- Produces: B5 driver 在 open-world stub 场景下续接值流时读取的语义库；后续（阶段一之后）持续增补，本任务只要求覆盖 v3.1 P13 清单里**不依赖具体内核版本符号、可凭公开内核 API 知识先行编写**的核心条目，标注为 draft，待 Track B 有真实内核源码树时用 B4 交叉核对。

- [ ] **写 schema**（v3 §3.6 + v3.1 P3/P3.1）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/schema/primitive_summary.schema.json",
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "name_pattern": { "type": "string" },
      "param_roles": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": { "index": { "type": "integer" }, "role": { "type": "string" } },
          "required": ["index", "role"]
        }
      },
      "return_role": { "type": "string" },
      "lifecycle": { "type": "string", "enum": ["allocate", "free", "free_async", "retain", "release", "none"] },
      "is_async": { "type": "boolean" },
      "transfers_ownership": { "type": "boolean" },
      "retains_reference": { "type": "boolean" },
      "effects": {
        "type": "array",
        "items": {
          "type": "string",
          "enum": ["lock_acquire", "lock_release", "container_link", "container_unlink", "async_enqueue", "callback_register", "callback_invoke", "copy_from_user", "copy_to_user"]
        }
      },
      "effect_bindings": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "effect": { "type": "string" },
            "target_param": { "type": "integer" },
            "context_param": { "type": "integer" },
            "callback_param": { "type": "integer" }
          },
          "required": ["effect"]
        }
      },
      "confidence": { "type": "string", "enum": ["high", "medium", "low"] },
      "status": { "type": "string", "enum": ["draft", "validated"], "description": "draft = 未对照真实内核源码校验；Track B 用 B4 校验后改为 validated" }
    },
    "required": ["name_pattern", "param_roles", "return_role", "lifecycle", "is_async", "transfers_ownership", "retains_reference", "effects", "confidence", "status"]
  }
}
```

- [ ] **写首批条目**（覆盖 v3.1 P13 清单中的 alloc/free/cache、RCU、refcount、list、workqueue/task_work、锁、usercopy 骨干条目；`completion/cancel/timeout`、`xarray`、`llist`、`io-wq` 的具体符号名依赖 io_uring 实际源码，留给 B4 补充，此处只放 schema 允许的占位分组注释，不伪造条目）

```yaml
# extraction/schema/primitive_summary.yaml
# 阶段一首批条目：draft 状态，待 Track B (Task B4) 对照 Linux v6.6 源码校验后改为 validated。
# 覆盖范围对应 v3.1 P13 清单的前四类；task_work/io-wq/completion/cancel/timeout 的具体符号
# 名称依赖实际 io_uring 源码，本文件先占分组骨架，符号名由 B4 补齐。

- name_pattern: "kmalloc*"
  param_roles:
    - {index: 0, role: size}
  return_role: allocated_object
  lifecycle: allocate
  is_async: false
  transfers_ownership: true
  retains_reference: false
  effects: []
  effect_bindings: []
  confidence: high
  status: draft

- name_pattern: "kzalloc*"
  param_roles:
    - {index: 0, role: size}
  return_role: allocated_object
  lifecycle: allocate
  is_async: false
  transfers_ownership: true
  retains_reference: false
  effects: []
  effect_bindings: []
  confidence: high
  status: draft

- name_pattern: "vmalloc"
  param_roles:
    - {index: 0, role: size}
  return_role: allocated_object
  lifecycle: allocate
  is_async: false
  transfers_ownership: true
  retains_reference: false
  effects: []
  effect_bindings: []
  confidence: high
  status: draft

- name_pattern: "kmem_cache_alloc*"
  param_roles:
    - {index: 0, role: cache_handle}
  return_role: allocated_object
  lifecycle: allocate
  is_async: false
  transfers_ownership: true
  retains_reference: false
  effects: []
  effect_bindings: []
  confidence: high
  status: draft

- name_pattern: "kfree"
  param_roles:
    - {index: 0, role: freed_object}
  return_role: none
  lifecycle: free
  is_async: false
  transfers_ownership: false
  retains_reference: false
  effects: []
  effect_bindings: []
  confidence: high
  status: draft

- name_pattern: "kmem_cache_free"
  param_roles:
    - {index: 0, role: cache_handle}
    - {index: 1, role: freed_object}
  return_role: none
  lifecycle: free
  is_async: false
  transfers_ownership: false
  retains_reference: false
  effects: []
  effect_bindings: []
  confidence: high
  status: draft

- name_pattern: "kfree_rcu*"
  param_roles:
    - {index: 0, role: freed_object}
  return_role: none
  lifecycle: free_async
  is_async: true
  transfers_ownership: false
  retains_reference: false
  effects: []
  effect_bindings: []
  confidence: high
  status: draft

- name_pattern: "call_rcu"
  param_roles:
    - {index: 0, role: rcu_head}
    - {index: 1, role: callback}
  return_role: none
  lifecycle: free_async
  is_async: true
  transfers_ownership: false
  retains_reference: false
  effects: [callback_register]
  effect_bindings:
    - {effect: callback_register, callback_param: 1, context_param: 0}
  confidence: high
  status: draft

- name_pattern: "*_get"
  param_roles:
    - {index: 0, role: target_object}
  return_role: none
  lifecycle: retain
  is_async: false
  transfers_ownership: false
  retains_reference: true
  effects: []
  effect_bindings: []
  confidence: medium
  status: draft

- name_pattern: "*_put"
  param_roles:
    - {index: 0, role: target_object}
  return_role: none
  lifecycle: release
  is_async: false
  transfers_ownership: false
  retains_reference: false
  effects: []
  effect_bindings: []
  confidence: medium
  status: draft

- name_pattern: "refcount_inc*"
  param_roles:
    - {index: 0, role: target_object}
  return_role: none
  lifecycle: retain
  is_async: false
  transfers_ownership: false
  retains_reference: true
  effects: []
  effect_bindings: []
  confidence: high
  status: draft

- name_pattern: "refcount_dec*"
  param_roles:
    - {index: 0, role: target_object}
  return_role: none
  lifecycle: release
  is_async: false
  transfers_ownership: false
  retains_reference: false
  effects: []
  effect_bindings: []
  confidence: high
  status: draft

- name_pattern: "list_add*"
  param_roles:
    - {index: 0, role: new_entry}
    - {index: 1, role: container_list}
  return_role: none
  lifecycle: none
  is_async: false
  transfers_ownership: false
  retains_reference: true
  effects: [container_link]
  effect_bindings:
    - {effect: container_link, target_param: 0, context_param: 1}
  confidence: high
  status: draft

- name_pattern: "list_del*"
  param_roles:
    - {index: 0, role: entry}
  return_role: none
  lifecycle: none
  is_async: false
  transfers_ownership: false
  retains_reference: false
  effects: [container_unlink]
  effect_bindings:
    - {effect: container_unlink, target_param: 0}
  confidence: high
  status: draft

- name_pattern: "queue_work*"
  param_roles:
    - {index: 0, role: workqueue_handle}
    - {index: 1, role: work_item}
  return_role: none
  lifecycle: none
  is_async: true
  transfers_ownership: false
  retains_reference: true
  effects: [async_enqueue, callback_register]
  effect_bindings:
    - {effect: async_enqueue, target_param: 1, context_param: 0}
    - {effect: callback_register, callback_param: 1, context_param: 0}
  confidence: high
  status: draft

- name_pattern: "spin_lock*"
  param_roles:
    - {index: 0, role: lock_object}
  return_role: none
  lifecycle: none
  is_async: false
  transfers_ownership: false
  retains_reference: false
  effects: [lock_acquire]
  effect_bindings:
    - {effect: lock_acquire, target_param: 0}
  confidence: high
  status: draft

- name_pattern: "spin_unlock*"
  param_roles:
    - {index: 0, role: lock_object}
  return_role: none
  lifecycle: none
  is_async: false
  transfers_ownership: false
  retains_reference: false
  effects: [lock_release]
  effect_bindings:
    - {effect: lock_release, target_param: 0}
  confidence: high
  status: draft

- name_pattern: "mutex_lock*"
  param_roles:
    - {index: 0, role: lock_object}
  return_role: none
  lifecycle: none
  is_async: false
  transfers_ownership: false
  retains_reference: false
  effects: [lock_acquire]
  effect_bindings:
    - {effect: lock_acquire, target_param: 0}
  confidence: high
  status: draft

- name_pattern: "mutex_unlock"
  param_roles:
    - {index: 0, role: lock_object}
  return_role: none
  lifecycle: none
  is_async: false
  transfers_ownership: false
  retains_reference: false
  effects: [lock_release]
  effect_bindings:
    - {effect: lock_release, target_param: 0}
  confidence: high
  status: draft

- name_pattern: "copy_from_user"
  param_roles:
    - {index: 0, role: kernel_dst}
    - {index: 1, role: user_src}
    - {index: 2, role: size}
  return_role: none
  lifecycle: none
  is_async: false
  transfers_ownership: false
  retains_reference: false
  effects: [copy_from_user]
  effect_bindings:
    - {effect: copy_from_user, target_param: 0}
  confidence: high
  status: draft

- name_pattern: "copy_to_user"
  param_roles:
    - {index: 0, role: user_dst}
    - {index: 1, role: kernel_src}
    - {index: 2, role: size}
  return_role: none
  lifecycle: none
  is_async: false
  transfers_ownership: false
  retains_reference: false
  effects: [copy_to_user]
  effect_bindings:
    - {effect: copy_to_user, target_param: 1}
  confidence: high
  status: draft
```

- [ ] **校验**

```bash
python3 -m pip install --quiet pyyaml jsonschema
python3 -c "
import json, yaml, jsonschema
schema = json.load(open('extraction/schema/primitive_summary.schema.json'))
entries = yaml.safe_load(open('extraction/schema/primitive_summary.yaml'))
jsonschema.validate(entries, schema)
print(f'{len(entries)} primitive_summary entries are schema-valid, all status=draft')
"
```
Expected: 打印 `21 primitive_summary entries are schema-valid, all status=draft`。

- [ ] **提交**

```bash
git add extraction/schema/primitive_summary.schema.json extraction/schema/primitive_summary.yaml
git commit -m "feat(extraction): primitive_summary schema + first-pass draft entries"
```

---

### Task A5: 置信度模型与失败输出策略文档

**Files:**
- Create: `docs/confidence-model.md`

- [ ] **写文档**（照抄 v3 §11 表格并加落地说明，供 ingestion/后续查询层引用，不重复造轮子）

```markdown
# 置信度模型与失败输出策略

来源：`静态抽取层技术路线_v3.md` 第十一部分，字段命名按 `静态抽取层技术路线_v3.1补丁.md` P4 更新（`provenance` 为数组，`primary_provenance` 为单值主导来源）。

## 档位表

| 档位 | 来源 | primary_provenance |
|---|---|---|
| high | GEP+DWARF/BTF 直接符号化，且来源一致 | `dwarf` / `btf` |
| medium_high | container_of 字节偏移还原且与人工标注一致 | `container_of_heuristic` |
| medium | TypeCopilot 对照参考恢复出的类型路径 | `typecopilot` |
| medium_low | 仅 debug metadata/BTF 得容器类型、无完整字段链 | `dwarf_partial` / `btf_partial` |
| low | 仅 numeric repr，或 numeric_kind=unknown | `numeric_fallback` |
| （正交） | 来自 primitive_summary 的语义 | `summary`（confidence 取 summary 自带档，见 primitive_summary.yaml 的 `confidence` 字段） |

## 失败输出策略（硬约束）

1. 任何一次访问识别，无论能恢复到哪个档位，**都必须产出一条 access_fact**，不允许因为符号化失败而丢弃事实。
2. `numeric_kind` 必填（`gep_offsets`/`byte_range`/`whole_object`/`unknown` 四选一），即便 `access_path_numeric` 为空。
3. 不得为了让某个字段"看起来完整"而伪造 GEP 偏移或字段名——宁可 `numeric_kind=unknown` + `confidence=low`，也不能编造。
4. `confidence` 与 `primary_provenance` 必须按上表一一对应，ingestion 层（Task A7）在写入前做一次一致性校验，不一致则拒绝写入并报错（不是静默降级）。

## 阶段一（本计划范围）的落地程度

阶段一 driver 允许 access path 仅为 numeric（v3 §12），因此阶段一产出的 access_fact 预期以 `numeric_fallback` / `low` 或 `dwarf` / `high`（若 DWARF 信息完整）为主，`container_of_heuristic`/`typecopilot`/`btf*` 档位是阶段二/三的工作，阶段一不强制产出。
```

- [ ] **提交**

```bash
git add docs/confidence-model.md
git commit -m "docs: confidence model and fail-does-not-drop-facts policy"
```

---

### Task A6: 性能三档运行模式配置

**Files:**
- Create: `extraction/schema/run_profile.schema.json`
- Create: `docs/performance-tiers.md`

- [ ] **写文档**（v3 §3.5，定义三档但不实现调度逻辑——阶段一只用 TU 级）

```markdown
# 性能三档运行模式

来源：`静态抽取层技术路线_v3.md` §3.5。SVF 全程序分析易爆内存，driver 通过 `--run-profile` 参数选择三档之一，行为差异在 Track B 的 driver 实现（Task B5）里落实：

| 档位 | 输入范围 | 用途 |
|---|---|---|
| `tu` | 单个编译单元的 `.bc` | golden tests / 冒烟测试，本计划（阶段一）唯一使用的档位 |
| `subsystem` | io_uring 全部 TU + primitive_summary 兜底 external | 阶段二/三主力档位，本计划不实现 |
| `closure` | 按需纳入跨子系统依赖的可达闭包 | 按需扩展，本计划不实现 |

阶段一 driver（Task B5）只需支持 `tu` 档位；`subsystem`/`closure` 的调度逻辑留空并在 CLI 里显式报 `NotImplemented`，不要写一半的调度代码占位。
```

- [ ] **写 run_profile schema**（供 manifest 记录本次运行用的是哪一档，供后续复现）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/schema/run_profile.schema.json",
  "type": "object",
  "properties": {
    "run_profile": { "type": "string", "enum": ["tu", "subsystem", "closure"] },
    "bc_inputs": { "type": "array", "items": { "type": "string" }, "minItems": 1 }
  },
  "required": ["run_profile", "bc_inputs"]
}
```

- [ ] **提交**

```bash
git add extraction/schema/run_profile.schema.json docs/performance-tiers.md
git commit -m "docs+schema: three-tier run profile (tu/subsystem/closure), phase-1 uses tu only"
```

---

### Task A7: SQLite ingestion schema + `ingest.py`

**Files:**
- Create: `src/implicitfuzz/ingestion/__init__.py`
- Create: `src/implicitfuzz/ingestion/schema.sql`
- Create: `src/implicitfuzz/ingestion/ingest.py`
- Test: `tests/test_ingestion.py`
- Modify: `requirements.txt`（新增 `jsonschema`, `PyYAML`）
- Modify: `setup.py`（`install_requires` 同步新增依赖）

**Interfaces:**
- Consumes: Task A2 的 6 个 fact schema 文件（路径 `extraction/schema/facts/*.schema.json`）。
- Produces: `ingest_jsonl(jsonl_path: str, db_path: str) -> dict[str, int]`（返回各 fact_type 写入行数，供测试断言与 B6 联调判断"是否产出了 access_fact"）。

这个任务是纯 Python、无 LLVM/SVF 依赖，天然适合测试先行，因此本任务保留 TDD 步骤颗粒度。

- [ ] **Step 1: 补依赖**

```diff
--- a/requirements.txt
+++ b/requirements.txt
@@
 # Core dependencies
 numpy>=1.24.0
 pytest>=7.4.0
 pytest-cov>=4.1.0
+jsonschema>=4.19.0
+PyYAML>=6.0
```

同步在 `setup.py` 的 `install_requires` 加入 `"jsonschema>=4.19.0"`。

- [ ] **Step 2: 写 SQLite schema**

```sql
-- src/implicitfuzz/ingestion/schema.sql
-- provenance/source_location 等复合结构存 JSON TEXT 列，闭包/递归查询留给上层 SQL CTE（不在本计划范围）。

CREATE TABLE IF NOT EXISTS entry_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version TEXT NOT NULL,
  kernel_version TEXT NOT NULL,
  llvm_version TEXT NOT NULL,
  opt_level TEXT NOT NULL,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  entry_kind TEXT NOT NULL,
  entry_symbol TEXT NOT NULL,
  associated_syscall TEXT,
  source_location_json TEXT,
  primary_provenance TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  confidence TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS call_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version TEXT NOT NULL,
  kernel_version TEXT NOT NULL,
  llvm_version TEXT NOT NULL,
  opt_level TEXT NOT NULL,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  caller TEXT NOT NULL,
  callee TEXT NOT NULL,
  call_site TEXT NOT NULL,
  is_indirect INTEGER NOT NULL,
  resolution_method TEXT NOT NULL,
  callee_candidates_json TEXT,
  call_chain_hash TEXT NOT NULL,
  instruction_id TEXT NOT NULL,
  svf_node_id TEXT NOT NULL,
  source_location_json TEXT,
  primary_provenance TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  confidence TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version TEXT NOT NULL,
  kernel_version TEXT NOT NULL,
  llvm_version TEXT NOT NULL,
  opt_level TEXT NOT NULL,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  semantic_op TEXT NOT NULL,
  access_kind TEXT NOT NULL,
  base_object_json TEXT NOT NULL,
  object_scope TEXT NOT NULL,
  access_path_symbolic TEXT,
  numeric_kind TEXT NOT NULL,
  access_path_numeric TEXT,
  field_type TEXT,
  access_path_recovery TEXT NOT NULL,
  instruction_id TEXT NOT NULL,
  svf_node_id TEXT NOT NULL,
  cross_opt_key TEXT NOT NULL,
  source_location_json TEXT,
  primary_provenance TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  confidence TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alias_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version TEXT NOT NULL,
  kernel_version TEXT NOT NULL,
  llvm_version TEXT NOT NULL,
  opt_level TEXT NOT NULL,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  object_id_json TEXT NOT NULL,
  points_to_set_json TEXT NOT NULL,
  relation_evidence TEXT NOT NULL,
  pta_kind TEXT NOT NULL,
  source_location_json TEXT,
  primary_provenance TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  confidence TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS branch_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version TEXT NOT NULL,
  kernel_version TEXT NOT NULL,
  llvm_version TEXT NOT NULL,
  opt_level TEXT NOT NULL,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  branch_instruction_id TEXT NOT NULL,
  condition_value TEXT NOT NULL,
  control_deps_json TEXT NOT NULL,
  related_loads_json TEXT NOT NULL,
  slice_range TEXT NOT NULL,
  source_location_json TEXT,
  primary_provenance TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  confidence TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_seed_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version TEXT NOT NULL,
  kernel_version TEXT NOT NULL,
  llvm_version TEXT NOT NULL,
  opt_level TEXT NOT NULL,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  target_symbol TEXT NOT NULL,
  gate_kind TEXT NOT NULL,
  predicate_summary TEXT NOT NULL,
  related_objects_json TEXT NOT NULL,
  related_access_facts_json TEXT NOT NULL,
  source_location_json TEXT,
  primary_provenance TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  confidence TEXT NOT NULL
);
```

- [ ] **Step 3: 写测试固件与测试**

```jsonl
```
先创建固件文件 `tests/fixtures/sample_facts.jsonl`：

```json
{"fact_type": "entry_fact", "schema_version": "1.0.0", "kernel_version": "v6.6", "llvm_version": "21.1.0", "opt_level": "-O2 -g", "bc_unit": "io_uring/io_uring.bc", "function": "io_uring_enter", "entry_kind": "syscall", "entry_symbol": "__do_sys_io_uring_enter", "associated_syscall": "io_uring_enter", "source_location": {"spelling": "io_uring/io_uring.c:3200", "expansion": "io_uring/io_uring.c:3200", "inlined_at": []}, "primary_provenance": "svf", "provenance": ["svf"], "confidence": "high"}
{"fact_type": "call_fact", "schema_version": "1.0.0", "kernel_version": "v6.6", "llvm_version": "21.1.0", "opt_level": "-O2 -g", "bc_unit": "io_uring/io_uring.bc", "function": "io_uring_enter", "caller": "io_uring_enter", "callee": "io_submit_sqes", "call_site": "modA:io_uring_enter:3:12:abc123", "is_indirect": false, "resolution_method": "direct", "callee_candidates": [], "call_chain_hash": "hash-0001", "instruction_id": "modA:io_uring_enter:3:12:abc123", "svf_node_id": "node-42"}
{"fact_type": "access_fact", "schema_version": "1.0.0", "kernel_version": "v6.6", "llvm_version": "21.1.0", "opt_level": "-O2 -g", "bc_unit": "io_uring/io_uring.bc", "function": "io_submit_sqes", "semantic_op": "read", "access_kind": "direct_load", "base_object": {"object_scope": "formal_param", "value": "io_submit_sqes:0"}, "object_scope": "formal_param", "access_path_symbolic": null, "numeric_kind": "unknown", "access_path_numeric": null, "field_type": null, "access_path_recovery": "numeric_only", "instruction_id": "modA:io_submit_sqes:5:3:def456", "svf_node_id": "node-99", "cross_opt_key": "io_uring/io_uring.c:3300|src_hash:xyz|io_submit_sqes|read", "source_location": {"spelling": "io_uring/io_uring.c:3300", "expansion": "io_uring/io_uring.c:3300", "inlined_at": []}, "primary_provenance": "numeric_fallback", "provenance": ["svf", "numeric_fallback"], "confidence": "low"}
```

写测试：

```python
# tests/test_ingestion.py
import sqlite3
from pathlib import Path

from implicitfuzz.ingestion.ingest import ingest_jsonl

FIXTURE = Path(__file__).parent / "fixtures" / "sample_facts.jsonl"


def test_ingest_jsonl_counts_per_fact_type(tmp_path):
    db_path = tmp_path / "facts.db"
    counts = ingest_jsonl(str(FIXTURE), str(db_path))
    assert counts == {"entry_fact": 1, "call_fact": 1, "access_fact": 1}


def test_ingest_jsonl_writes_rows_with_expected_fields(tmp_path):
    db_path = tmp_path / "facts.db"
    ingest_jsonl(str(FIXTURE), str(db_path))

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    row = conn.execute("SELECT * FROM access_fact").fetchone()
    assert row["semantic_op"] == "read"
    assert row["numeric_kind"] == "unknown"
    assert row["confidence"] == "low"
    assert row["object_scope"] == "formal_param"

    row = conn.execute("SELECT * FROM entry_fact").fetchone()
    assert row["entry_kind"] == "syscall"
    assert row["entry_symbol"] == "__do_sys_io_uring_enter"

    conn.close()


def test_ingest_jsonl_rejects_schema_invalid_row(tmp_path):
    bad_jsonl = tmp_path / "bad.jsonl"
    bad_jsonl.write_text('{"fact_type": "access_fact", "semantic_op": "not_a_real_op"}\n')
    db_path = tmp_path / "facts.db"

    import pytest
    with pytest.raises(Exception):
        ingest_jsonl(str(bad_jsonl), str(db_path))
```

- [ ] **Step 4: 实现 `ingest.py`**

```python
# src/implicitfuzz/ingestion/ingest.py
"""JSONL fact ingestion into SQLite, validated against extraction/schema/facts/*.schema.json."""
import json
import sqlite3
from pathlib import Path

import jsonschema

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _REPO_ROOT / "extraction" / "schema" / "facts"
_DDL_PATH = Path(__file__).resolve().parent / "schema.sql"

_JSON_COLUMNS = {
    "entry_fact": {"source_location": "source_location_json", "provenance": "provenance_json"},
    "call_fact": {"source_location": "source_location_json", "provenance": "provenance_json", "callee_candidates": "callee_candidates_json"},
    "access_fact": {"source_location": "source_location_json", "provenance": "provenance_json", "base_object": "base_object_json"},
    "alias_fact": {"source_location": "source_location_json", "provenance": "provenance_json", "object_id": "object_id_json", "points_to_set": "points_to_set_json"},
    "branch_fact": {"source_location": "source_location_json", "provenance": "provenance_json", "control_deps": "control_deps_json", "related_loads": "related_loads_json"},
    "gate_seed_fact": {"source_location": "source_location_json", "provenance": "provenance_json", "related_objects": "related_objects_json", "related_access_facts": "related_access_facts_json"},
}

_SCALAR_RENAME = {"schema_version", "kernel_version", "llvm_version", "opt_level", "bc_unit", "function", "primary_provenance", "confidence"}


def _load_schemas() -> dict[str, dict]:
    common = json.loads((_SCHEMA_DIR / "common.schema.json").read_text())
    schemas = {}
    for fact_type in _JSON_COLUMNS:
        raw = json.loads((_SCHEMA_DIR / f"{fact_type}.schema.json").read_text())
        merged = {**common, **raw}
        merged["properties"] = {**common["properties"], **raw.get("properties", {})}
        merged["required"] = list(set(common["required"]) | set(raw.get("required", [])))
        del merged["allOf"]
        schemas[fact_type] = merged
    return schemas


def _row_for(fact_type: str, record: dict) -> dict:
    json_cols = _JSON_COLUMNS[fact_type]
    row = {}
    for key, value in record.items():
        if key == "fact_type":
            continue
        if key in json_cols:
            row[json_cols[key]] = json.dumps(value)
        elif key == "is_indirect":
            row[key] = int(bool(value))
        else:
            row[key] = value
    return row


def ingest_jsonl(jsonl_path: str, db_path: str) -> dict:
    schemas = _load_schemas()
    conn = sqlite3.connect(db_path)
    conn.executescript(_DDL_PATH.read_text())

    counts: dict[str, int] = {}
    with open(jsonl_path) as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            fact_type = record.get("fact_type")
            if fact_type not in schemas:
                raise ValueError(f"line {line_no}: unknown fact_type {fact_type!r}")
            jsonschema.validate(record, schemas[fact_type])

            row = _row_for(fact_type, record)
            columns = ", ".join(row.keys())
            placeholders = ", ".join("?" for _ in row)
            conn.execute(
                f"INSERT INTO {fact_type} ({columns}) VALUES ({placeholders})",
                list(row.values()),
            )
            counts[fact_type] = counts.get(fact_type, 0) + 1

    conn.commit()
    conn.close()
    return counts
```

```python
# src/implicitfuzz/ingestion/__init__.py
from .ingest import ingest_jsonl

__all__ = ["ingest_jsonl"]
```

- [ ] **Step 5: 跑测试**

```bash
source venv/bin/activate
pip install -r requirements.txt
pytest tests/test_ingestion.py -v
```
Expected: 3 个测试全部 `PASSED`。

- [ ] **提交**

```bash
git add requirements.txt setup.py src/implicitfuzz/ingestion/ tests/test_ingestion.py tests/fixtures/sample_facts.jsonl
git commit -m "feat(ingestion): SQLite schema + schema-validated JSONL ingestion with tests"
```

---

### Task A8: `extraction/` C++ 子项目骨架（不编译，仅结构）

**Files:**
- Create: `extraction/CMakeLists.txt`
- Create: `extraction/src/main.cpp`（占位 entrypoint，Track B 时补实现，见 Task B5）
- Create: `extraction/README.md`

**Interfaces:**
- Produces: Track B（Task B5）直接在这个骨架上补 driver 逻辑，不必重新决定目录结构/CMake 链接方式。

- [ ] **写 README**（明确标注 Linux-only，链接 macOS 踩坑笔记，避免后来者重蹈覆辙）

```markdown
# extraction/

C++ 子项目：基于 SVF + LLVM 21.1.0 的内核 bitcode 静态抽取 driver。

**只能在 Linux 上构建和运行。** 已确认在 macOS 上因 dyld two-level namespace /
libc++·libc++abi 拆分 / libz3 ABI 不兼容等一系列问题跑不通，细节与教训见
`../docs/svf-extraction-toolchain-notes.md`。这些坑本身也是本任务 CMakeLists.txt
里"显式指定编译器、优先 rpath 而非 LD_LIBRARY_PATH、全程单一工具链编译所有 C++
组件"这几条约束的直接来源。

## 构建前置条件（Linux）

- LLVM 21.1.0（精确 patch，见 `docs/静态抽取层技术路线_v3.md` §2.1）：设置环境变量 `LLVM_DIR` 指向解压/安装好的 LLVM 21.1.0 目录。
- SVF：按 `docs/静态抽取层技术路线_v3.1补丁.md` P1 固定一个已验证 commit 构建，设置 `SVF_DIR` 指向其 `Release-build` 目录（或已安装前缀）。

## 构建

```bash
cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="$LLVM_DIR/bin/clang" \
  -DCMAKE_CXX_COMPILER="$LLVM_DIR/bin/clang++"
cmake --build build -j
```

## 运行

```bash
./build/extraction-driver --run-profile tu --bc-inputs path/to/unit.bc --out facts.jsonl
```
```

- [ ] **写 CMakeLists.txt**（显式编译器指定 + rpath 优先，翻译自 macOS 笔记「对 Linux 服务器实现的建议」，Linux 用 `$ORIGIN` 替代 macOS 的 `@loader_path`）

```cmake
cmake_minimum_required(VERSION 3.20)
project(implicitfuzz_extraction CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT DEFINED ENV{LLVM_DIR})
  message(FATAL_ERROR "LLVM_DIR must point to an LLVM 21.1.0 install (see docs/svf-extraction-toolchain-notes.md)")
endif()
if(NOT DEFINED ENV{SVF_DIR})
  message(FATAL_ERROR "SVF_DIR must point to a built SVF tree pinned per docs/静态抽取层技术路线_v3.1补丁.md P1")
endif()

set(LLVM_DIR_ENV $ENV{LLVM_DIR})
set(SVF_DIR_ENV $ENV{SVF_DIR})

find_package(LLVM REQUIRED CONFIG PATHS "${LLVM_DIR_ENV}/lib/cmake/llvm" NO_DEFAULT_PATH)
if(NOT LLVM_PACKAGE_VERSION MATCHES "^21\\.1\\.0")
  message(FATAL_ERROR "Expected LLVM 21.1.0 (per 静态抽取层技术路线_v3.md §2.1), found ${LLVM_PACKAGE_VERSION}. "
                       "If intentionally testing a different patch, this must be a separate experiment-matrix build, not the mainline.")
endif()

# SVF 未必导出标准 CMake package config；先尝试 find_package，找不到则退化为手动 include/lib 路径。
find_package(SVF QUIET PATHS "${SVF_DIR_ENV}/lib/cmake/svf" NO_DEFAULT_PATH)

add_executable(extraction-driver src/main.cpp)

if(SVF_FOUND)
  target_link_libraries(extraction-driver PRIVATE SVF::Svf)
else()
  message(WARNING "SVF CMake package not found under ${SVF_DIR_ENV}; falling back to manual include/lib paths. "
                   "Verify these against the actual SVF build layout on the Linux server and adjust before relying on this build.")
  target_include_directories(extraction-driver PRIVATE
    "${SVF_DIR_ENV}/include"
    "${LLVM_DIR_ENV}/include"
  )
  target_link_directories(extraction-driver PRIVATE
    "${SVF_DIR_ENV}/lib"
    "${LLVM_DIR_ENV}/lib"
  )
  target_link_libraries(extraction-driver PRIVATE Svf LLVMCore LLVMSupport LLVMIRReader)
endif()

# 全程单一工具链、优先 rpath 而非 LD_LIBRARY_PATH（macOS 笔记教训 #3/#6 的 Linux 版对应做法）。
set_target_properties(extraction-driver PROPERTIES
  BUILD_RPATH "${SVF_DIR_ENV}/lib;${LLVM_DIR_ENV}/lib;\$ORIGIN"
  INSTALL_RPATH "${SVF_DIR_ENV}/lib;${LLVM_DIR_ENV}/lib;\$ORIGIN"
  BUILD_WITH_INSTALL_RPATH TRUE
)
```

- [ ] **写占位 entrypoint**（Task B5 在此基础上填充真实逻辑，此处只保证骨架能表达 CLI 契约，不是"实现待补"的空话——它有明确、可编译（Linux 上）的行为）

```cpp
// extraction/src/main.cpp
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

// Phase-1 骨架：解析 --run-profile/--bc-inputs/--out，校验 run-profile=tu（Task A6 唯一支持的档位），
// 真正的 SVF 初始化与 VFG/ICFG 遍历逻辑在 Task B5 补入（照抄 svf-ex.cpp 的
// buildSVFModule → SVFIRBuilder → Andersen::createAndersenWaveDiff → SVFGBuilder().buildFullSVFG()
// → 自定义 worklist，见 静态抽取层技术路线_v3.md §4.1）。

struct Args {
    std::string run_profile;
    std::vector<std::string> bc_inputs;
    std::string out;
};

static bool parse_args(int argc, char** argv, Args& args) {
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--run-profile" && i + 1 < argc) {
            args.run_profile = argv[++i];
        } else if (arg == "--bc-inputs" && i + 1 < argc) {
            args.bc_inputs.push_back(argv[++i]);
        } else if (arg == "--out" && i + 1 < argc) {
            args.out = argv[++i];
        } else {
            std::fprintf(stderr, "unknown or incomplete argument: %s\n", arg.c_str());
            return false;
        }
    }
    return !args.run_profile.empty() && !args.bc_inputs.empty() && !args.out.empty();
}

int main(int argc, char** argv) {
    Args args;
    if (!parse_args(argc, argv, args)) {
        std::fprintf(stderr, "usage: extraction-driver --run-profile tu --bc-inputs <path>... --out <facts.jsonl>\n");
        return 2;
    }
    if (args.run_profile != "tu") {
        std::fprintf(stderr, "run-profile '%s' not implemented in phase 1 (only 'tu' is supported; see docs/performance-tiers.md)\n",
                     args.run_profile.c_str());
        return 3;
    }

    std::fprintf(stderr, "[phase-1 skeleton] would process %zu bc unit(s) -> %s (SVF pipeline not yet wired, see Task B5)\n",
                 args.bc_inputs.size(), args.out.c_str());
    return 1; // 明确以非零退出，避免被 B6 的联调脚本误判为"已产出 facts"
}
```

- [ ] **提交**

```bash
git add extraction/CMakeLists.txt extraction/src/main.cpp extraction/README.md
git commit -m "feat(extraction): C++ driver skeleton (CMake + CLI arg parsing), Linux-only build documented"
```

---

## Track B：Linux 服务器执行（工具链→构建→联调）

> 以下任务**必须**在 Linux 服务器的会话/终端执行，不要在当前 Mac worktree 尝试。执行前请切换到面向该服务器的会话（例如通过 SSH 或新开 Linux 会话），并把 Track A 产出的 `extraction/schema/`、`extraction/CMakeLists.txt`、`extraction/src/main.cpp`、`src/implicitfuzz/ingestion/` 同步到服务器上的仓库副本。

### Task B1: 安装 LLVM 21.1.0（Linux）

**Files:**
- Modify: `docs/kernel-target-decision.md`（回填实际 clang/LLVM patch 版本）

- [ ] **优先尝试发行版包管理器**

```bash
# Debian/Ubuntu 示例，具体按服务器实际发行版调整
sudo apt-get update
apt-cache search llvm-21 2>/dev/null || true
# 若有 llvm-21/clang-21 包，直接装：
# sudo apt-get install -y llvm-21 clang-21 lld-21
```

- [ ] **若发行版无对应包，用官方 GitHub Release 预编译包**（沿用 macOS 笔记的建议，避免包管理器静默源码编译；这次要精确取 **21.1.0** 而不是 21.1.8，因为 SVF `build.sh` pin 的是 21.1.0）

```bash
curl -L --http1.1 -C - --retry 10 --retry-all-errors \
  -o /tmp/llvm-21.1.0-linux.tar.xz \
  https://github.com/llvm/llvm-project/releases/download/llvmorg-21.1.0/LLVM-21.1.0-Linux-X64.tar.xz
mkdir -p ~/toolchains/llvm-21.1.0
tar -xf /tmp/llvm-21.1.0-linux.tar.xz -C ~/toolchains/llvm-21.1.0 --strip-components=1
export LLVM_DIR=~/toolchains/llvm-21.1.0
"$LLVM_DIR/bin/clang" --version
```
Expected: 输出包含 `clang version 21.1.0`。若 GitHub Release 没有精确的 21.1.0 Linux 资产（只有 21.1.8 等更新 patch），按 v3 §2.1 规则：不得默认混用，需从源码构建精确 21.1.0，或将该 patch 差异记录为独立实验矩阵条目（不进主干）。

- [ ] **回填决策文档**

在 `docs/kernel-target-decision.md` 的"待回填"部分填入实际验证通过的 `clang --version` 完整输出。

- [ ] **提交**

```bash
git add docs/kernel-target-decision.md
git commit -m "docs: record verified LLVM 21.1.0 toolchain on Linux"
```

---

### Task B2: 构建 SVF（固定 commit）+ Z3

**Files:**
- Modify: `extraction/schema/manifest.example.json`（回填 `svf_commit` 到一个真实值，仍是 example 用途）

- [ ] **克隆并固定 SVF commit**

```bash
git clone https://github.com/SVF-tools/SVF.git ~/toolchains/SVF
cd ~/toolchains/SVF
git rev-parse HEAD   # 记录这个 SHA，这就是本次 svf_commit
```

- [ ] **（推荐）用同一 LLVM 21.1.0 clang++ 从源码编译 Z3**，避免 macOS 笔记教训 #6（`libz3` 与自建二进制用不同 libc++ 导致运行时崩溃）在 Linux 上以其他形式复现——这是通用工程原则，不是 macOS 专属手段

```bash
git clone --branch z3-4.15.4 https://github.com/Z3Prover/z3.git ~/toolchains/z3-src
cd ~/toolchains/z3-src
python3 scripts/mk_make.py --prefix=~/toolchains/z3-4.15.0 \
  CXX="$LLVM_DIR/bin/clang++" CC="$LLVM_DIR/bin/clang"
cd build && make -j"$(nproc)" && make install
export Z3_DIR=~/toolchains/z3-4.15.0
```

- [ ] **构建 SVF，显式指定编译器（不依赖 PATH 顺序，macOS 笔记教训 #4，Linux 上同样适用）**

```bash
cd ~/toolchains/SVF
export LLVM_DIR=~/toolchains/llvm-21.1.0
cmake -S . -B Release-build \
  -DCMAKE_BUILD_TYPE=Release \
  -DSVF_ENABLE_ASSERTIONS=ON \
  -DBUILD_SHARED_LIBS=ON \
  -DCMAKE_C_COMPILER="$LLVM_DIR/bin/clang" \
  -DCMAKE_CXX_COMPILER="$LLVM_DIR/bin/clang++" \
  -DCMAKE_EXE_LINKER_FLAGS="-L$LLVM_DIR/lib -Wl,-rpath,$LLVM_DIR/lib" \
  -DCMAKE_SHARED_LINKER_FLAGS="-L$LLVM_DIR/lib -Wl,-rpath,$LLVM_DIR/lib" \
  -DCMAKE_BUILD_RPATH="\$ORIGIN/../lib;$Z3_DIR/lib;$LLVM_DIR/lib" \
  -DCMAKE_INSTALL_RPATH="\$ORIGIN/../lib;$Z3_DIR/lib;$LLVM_DIR/lib"
cmake --build Release-build -j"$(nproc)"
```
Expected: `ae`/`cfl`/`dvf`/`llvm2svf`/`mta`/`saber`/`svf-ex`/`wpa` 全部产出于 `Release-build/bin`（对齐 macOS 笔记里"完整编译成功"的验收标准，但这次预期不需要笔记里那一整套 macOS 专属 dyld 修法）。

- [ ] **冒烟验证 SVF 本身可运行（先不牵扯我们的 driver）**

```bash
./Release-build/bin/svf-ex --help 2>&1 | head -20
```
Expected: 打印 usage/help，无 `libc++abi: terminating`、无段错误。若仍出现类似 macOS 笔记教训 #6 的运行时崩溃，先检查是否所有组件（SVF 本身 + Z3 + 未来的 driver）都用同一个 `$LLVM_DIR/bin/clang++` 构建，这是最可能的根因。

- [ ] **回填 manifest example 与决策文档**

```bash
export SVF_DIR=~/toolchains/SVF/Release-build
```
把 `git -C ~/toolchains/SVF rev-parse HEAD` 的输出写入 `extraction/schema/manifest.example.json` 的 `svf_commit` 字段（替换占位的全零 SHA）。

- [ ] **提交**

```bash
git add extraction/schema/manifest.example.json
git commit -m "docs: record pinned SVF commit built against LLVM 21.1.0 on Linux"
```

---

### Task B3: 生成内核 bitcode（TU 级）

**Files:**
- Modify: `docs/kernel-target-decision.md`（回填实际选定的 TU 文件路径）

- [ ] **拉取内核源码（v6.6，per Task A1 决策）**

```bash
git clone --branch v6.6 --depth 1 https://github.com/torvalds/linux.git ~/kernel-src
cd ~/kernel-src
```

- [ ] **选定 TU 级冒烟目标**：在 `io_uring/` 下选一个不深度依赖跨子系统符号、体量较小的 `.c` 文件（例如 `io_uring/timeout.c` 或 `io_uring/cancel.c` 这类逻辑相对独立的文件；最终以实际内核树里的依赖复杂度为准，避免选到 `io_uring/io_uring.c` 这种拉入几乎全部子系统的核心文件，否则不是"TU 级"而是变相"闭包级"）。

- [ ] **生成 bitcode**（采用 v3 §3.2 二选一方案，这里选内核原生 `LLVM=1`，因为不需要额外安装 `wllvm`/`gllvm`）

```bash
export PATH="$LLVM_DIR/bin:$PATH"
make LLVM=1 defconfig
make LLVM=1 CC=clang CFLAGS_KERNEL="-O2 -g" io_uring/<选定文件>.o
# 内核构建产 .o 是原生目标文件；用 -emit-llvm 单独重跑该编译单元拿 .bc：
clang -emit-llvm -c -O2 -g $(</path/to/compile_commands_flags_for_that_file) \
  -o io_uring/<选定文件>.bc io_uring/<选定文件>.c
```
Expected: 产出的 `.bc` 文件可被 `llvm-dis` 正常反汇编：

```bash
"$LLVM_DIR/bin/llvm-dis" io_uring/<选定文件>.bc -o /tmp/check.ll
head -5 /tmp/check.ll   # 应看到 IR module 头部，无解析错误
```

- [ ] **回填决策文档**

在 `docs/kernel-target-decision.md` 填入实际选定的 TU 路径与验证结果。

- [ ] **提交**

```bash
git add docs/kernel-target-decision.md
git commit -m "docs: record phase-1 TU-level bitcode target and generation result"
```

---

### Task B4: Golden case 1 目标选点与人工标注 ground truth

**Files:**
- Create: `extraction/golden/case1_gep/README.md`
- Create: `extraction/golden/case1_gep/ground_truth.json`
- Modify: `extraction/schema/primitive_summary.yaml`（把 B3 选定 TU 里实际用到的 primitive 从 `draft` 改 `validated`，或按需增补条目）

**Interfaces:**
- Produces: B6 联调时用来判断"driver 是否在正确位置产出了 access_fact"的 ground truth。

- [ ] **在 B3 选定的 TU 源码里找一个 `ctx->field` 形式的普通字段访问**（v3 §6.2 golden case 1 的定义：验证 GEP+DWARF→字段名），记录其准确源码位置与预期字段名/类型。

```json
{
  "case_id": "case1_gep",
  "kernel_version": "v6.6",
  "tu": "io_uring/<选定文件>.c",
  "target_source_location": "io_uring/<选定文件>.c:<行号>",
  "target_expression": "<例如 ctx->refs 或类似的普通字段访问，按实际选点填写>",
  "expected_semantic_op": "read",
  "expected_access_kind": "direct_load",
  "expected_field_name": "<字段名>",
  "expected_field_type": "<类型>",
  "expected_numeric_kind": "gep_offsets",
  "annotated_by": "human",
  "annotation_date": "PENDING_TRACK_B_EXECUTION_DATE"
}
```

- [ ] **写 README 说明标注方法**（供阶段二扩展 case 2/3/4a/4b/5/6 时复用同一套流程）

```markdown
# golden/case1_gep

阶段一冒烟用的最小 golden case：一次普通 `ctx->field` 读访问。

标注流程：
1. 在选定 TU 的源码里手工定位一处清晰、无宏展开歧义的字段读/写。
2. 记录源码行号、期望字段名/类型、期望 `semantic_op`/`access_kind`/`numeric_kind`。
3. `ground_truth.json` 是本 case 唯一的正确答案来源（v3 §6.3 强调的原则同样适用于 case 1：ground truth 只来自人工标注，不依赖任何外部工具自动产出）。

阶段一只要求 driver 对这个位置产出**任意置信度**的 access_fact（哪怕 numeric_kind=unknown、confidence=low）；
symbolic path 是否与本文件的 expected_field_name 一致，是阶段二 golden test 量化验收（v3 §13）的范围，阶段一不作为通过/失败判据。
```

- [ ] **提交**

```bash
git add extraction/golden/case1_gep/ extraction/schema/primitive_summary.yaml
git commit -m "test(golden): case1_gep target selection and human-annotated ground truth"
```

---

### Task B5: 实现 driver 最小抽取逻辑

**Files:**
- Modify: `extraction/src/main.cpp`

**Interfaces:**
- Consumes: Task A2 的 fact 字段契约（字段名/类型必须与 schema 逐字一致，否则 B6 的 ingestion 校验会拒绝）；Task A4 的 `primitive_summary.yaml`（open-world 场景下遇到未解析的 external call 时查询）。
- Produces: 一个 JSONL 文件，每行是一条 `entry_fact` 或 `call_fact` 或 `access_fact`（阶段一不要求产出 `alias_fact`/`branch_fact`/`gate_seed_fact`）。

- [ ] **按 v3 §4.1 官方标准用法搭建 SVF 初始化骨架，替换 Task A8 的占位逻辑**

```cpp
// extraction/src/main.cpp（在 Task A8 骨架基础上，parse_args 之后接入真实逻辑）
#include "SVF-LLVM/LLVMModule.h"
#include "SVF-LLVM/SVFIRBuilder.h"
#include "WPA/Andersen.h"
#include "SVFG/SVFGBuilder.h"
// ... 其余 include 按实际 SVF 版本的 svf-ex.cpp 头文件列表核对补齐，
// 不同 commit 的头文件路径可能有出入，以 Task B2 拉取的那个 svf_commit 的
// svf-llvm/tools/Example/svf-ex.cpp 为准逐一核对，而不是凭记忆假设路径。

#include <fstream>

// ...（parse_args 与 Args 结构体沿用 Task A8）

static int run_tu_profile(const Args& args) {
    std::vector<std::string> module_names(args.bc_inputs.begin(), args.bc_inputs.end());

    SVF::LLVMModuleSet::getLLVMModuleSet()->buildSVFModule(module_names);
    SVF::SVFIRBuilder builder;
    SVF::SVFIR* pag = builder.build();

    SVF::Andersen* ander = SVF::AndersenWaveDiff::createAndersenWaveDiff(pag);

    SVF::SVFGBuilder svfBuilder;
    SVF::SVFG* svfg = svfBuilder.buildFullSVFG(ander);

    std::ofstream out(args.out);
    if (!out) {
        std::fprintf(stderr, "cannot open output file: %s\n", args.out.c_str());
        return 4;
    }

    // 自定义 worklist：遍历 ICFG 的每条指令，识别函数入口 -> entry_fact，
    // 识别 call 指令 -> call_fact，识别 load/store/GEP -> access_fact（numeric-only 允许）。
    // 阶段一实现范围：
    //   - entry_fact：对每个有函数体的 SVFFunction 产出一条，entry_kind 先全部标 "syscall"
    //     占位（真实 entry_kind 判定依赖符号名匹配表，是阶段二工作，此处如实标注低置信度）。
    //   - call_fact：direct call 用 resolution_method="direct"；无法解析的 external call
    //     查 primitive_summary.yaml，查到则不产出 call_fact（当作值流延续，由 summary 语义处理），
    //     查不到则仍产出 call_fact，resolution_method="direct"，callee 填 declared 符号名，
    //     不因为是 external 就丢事实。
    //   - access_fact：GEP+load/store 优先尝试用 DWARF 调试信息解析字段名（numeric_kind=gep_offsets，
    //     access_path_recovery="dwarf"）；DWARF 缺失或解析失败则退化为
    //     numeric_kind="unknown"、access_path_recovery="numeric_only"、confidence="low"，
    //     但仍必须产出这条 fact（v3 §5.4「失败不丢事实」）。
    //
    // 这部分是本计划信息密度最高、最依赖实际 SVF API（SVFIR 节点遍历、ICFGNode 类型判别、
    // LLVMUtil 调试信息访问接口）的代码，需在真实 SVF 源码/头文件旁边实现，不能脱离
    // Task B2 拉取的具体 svf_commit 版本的头文件签名凭空写。实现时逐个用下面的验证步骤核对，
    // 而不是一次性写完再测。

    out.close();
    return 0;
}

int main(int argc, char** argv) {
    Args args;
    if (!parse_args(argc, argv, args)) {
        std::fprintf(stderr, "usage: extraction-driver --run-profile tu --bc-inputs <path>... --out <facts.jsonl>\n");
        return 2;
    }
    if (args.run_profile != "tu") {
        std::fprintf(stderr, "run-profile '%s' not implemented in phase 1\n", args.run_profile.c_str());
        return 3;
    }
    return run_tu_profile(args);
}
```

> **执行说明（不是占位）**：上面这一步的 worklist 主体（GEP/load/store 遍历、DWARF 访问、entry 识别）依赖具体 SVF commit 的确切类名/方法名，本计划不假装能在没有真实头文件的情况下把这段代码写到可编译——这是 Track B 执行者在 Linux 服务器上、对照 Task B2 拉到的 `svf-ex.cpp` 与该 commit 的头文件**现场核对签名**后填充的部分。验收标准不是"代码存在"，而是下面 Task B6 的端到端产出结果。

- [ ] **编译**

```bash
cd extraction
export LLVM_DIR=~/toolchains/llvm-21.1.0
export SVF_DIR=~/toolchains/SVF/Release-build
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="$LLVM_DIR/bin/clang" \
  -DCMAKE_CXX_COMPILER="$LLVM_DIR/bin/clang++"
cmake --build build -j"$(nproc)"
```
Expected: `build/extraction-driver` 生成，无链接错误。若出现 macOS 笔记里同类的符号缺失（`__cxa_*`/`std::__1::__hash_memory` 等），说明 Linux 上也遇到了库拆分问题，按同样思路（显式链接对应库、统一工具链）排查，而不是照抄 macOS 的 `-lc++abi` 参数（Linux 上是 glibc+libstdc++，符号缺失的具体表现会不同）。

- [ ] **提交**

```bash
git add extraction/src/main.cpp
git commit -m "feat(extraction): wire SVF pipeline (buildSVFModule -> SVFIR -> Andersen -> SVFG) and minimal fact worklist"
```

---

### Task B6: 端到端联调（bitcode → driver → JSONL → SQLite）

**Files:**
- Create: `docs/phase1-findings.md`

- [ ] **跑 driver**

```bash
cd extraction
./build/extraction-driver --run-profile tu \
  --bc-inputs ~/kernel-src/io_uring/<选定文件>.bc \
  --out /tmp/phase1_facts.jsonl
echo "exit code: $?"
wc -l /tmp/phase1_facts.jsonl
```
Expected: exit code 0；`/tmp/phase1_facts.jsonl` 行数 > 0。

- [ ] **校验产出的每一行都能通过 Task A2 的 schema**（不依赖 ingest.py，先单独跑一遍纯校验，定位问题时能和"写库失败"分开排查）

```bash
python3 -c "
import json, jsonschema
from pathlib import Path

schema_dir = Path('extraction/schema/facts')
common = json.loads((schema_dir / 'common.schema.json').read_text())
schemas = {}
for name in ['entry_fact', 'call_fact', 'access_fact']:
    raw = json.loads((schema_dir / f'{name}.schema.json').read_text())
    merged = {**common, **raw}
    merged['properties'] = {**common['properties'], **raw.get('properties', {})}
    merged['required'] = list(set(common['required']) | set(raw.get('required', [])))
    del merged['allOf']
    schemas[name] = merged

bad = 0
with open('/tmp/phase1_facts.jsonl') as f:
    for i, line in enumerate(f, 1):
        rec = json.loads(line)
        try:
            jsonschema.validate(rec, schemas[rec['fact_type']])
        except jsonschema.ValidationError as e:
            bad += 1
            print(f'line {i} INVALID: {e.message}')
print(f'{i} lines checked, {bad} invalid')
"
```
Expected: `N lines checked, 0 invalid`。若有 invalid，回到 Task B5 修正字段命名/类型，不要放宽 schema 迁就错误输出。

- [ ] **跑 ingestion（Task A7 的 `ingest_jsonl`）**

```bash
source venv/bin/activate
python3 -c "
from implicitfuzz.ingestion.ingest import ingest_jsonl
counts = ingest_jsonl('/tmp/phase1_facts.jsonl', '/tmp/phase1_facts.db')
print(counts)
"
```
Expected: 打印各 fact_type 的行数字典，`access_fact` 计数 > 0（这是 v3 §12 阶段一"输出最小 call_fact/access_fact"的达成判据）。

- [ ] **核对 golden case 1 目标是否命中**（Task B4 的 ground truth）

```bash
sqlite3 /tmp/phase1_facts.db "
SELECT function, semantic_op, access_kind, numeric_kind, confidence, source_location_json
FROM access_fact
WHERE source_location_json LIKE '%<golden case1 的源码行号>%';
"
```
Expected: 至少一行匹配（哪怕 `confidence=low`、`numeric_kind=unknown`，per v3 §5.4"失败不丢事实"，阶段一验收的是"有产出"而不是"符号化命中"）。

- [ ] **关键风险观测：opaque pointer 处理成熟度**（v3 §12 阶段一"头号观测目标"）

检查产出的 access_fact 中 `numeric_kind` 分布：

```bash
sqlite3 /tmp/phase1_facts.db "
SELECT numeric_kind, COUNT(*) FROM access_fact GROUP BY numeric_kind;
"
```

- 若相当比例是 `gep_offsets`（即便 `access_path_recovery=numeric_only`，只要 GEP 索引序列本身有效）：SVF 在 LLVM 21.1.0 下对该 TU 的字段敏感性基本保持，阶段一/阶段三**不需要**合并，按 v3 §12 原计划推进阶段二。
- 若几乎全部退化为 `whole_object`/`unknown`（即 GEP 链信息在 opaque pointer 下systematically 丢失）：命中 v3 §12 写明的最大未验证假设，需要按文档要求评估"降到 LLVM 16/18 + TypeCopilot"是否才是可行主干，阶段一与阶段三需要合并规划。

- [ ] **写发现文档**

```markdown
# 阶段一执行发现（Phase 1 Findings）

- 执行日期：<回填>
- svf_commit：<回填，来自 Task B2>
- llvm_version：21.1.0（或记录实际验证通过的版本，若非 21.1.0 需说明原因，见 v3 §2.1）
- opt_level：<回填，默认 -O2 -g，若回退需说明原因>
- TU：<回填，来自 Task B3>

## 端到端结果
- driver exit code：<回填>
- JSONL 行数：<回填>，其中 access_fact <回填> 条
- schema 校验：<回填 N/0 invalid>
- golden case 1 命中：<回填 是/否，命中的 confidence/numeric_kind>

## opaque pointer 成熟度观测（v3 §12 头号风险）
- numeric_kind 分布：<回填>
- 结论：<按上面判据回填「不需要合并阶段三」或「需要合并阶段三，理由...」>

## 下一步
<按结论回填：若不需要合并，指向 v3 §12 阶段二（golden tests × 优化矩阵）；
若需要合并，指向 v3 §12 阶段三（TypeCopilot 对照评估）提前介入>
```

- [ ] **提交**

```bash
git add docs/phase1-findings.md
git commit -m "docs: phase-1 end-to-end findings, opaque pointer maturity observation"
```

---

### Task B7: 写正式 manifest 实例

**Files:**
- Create: `extraction/manifests/phase1-smoke.json`

- [ ] **用 Task B6 实际产生的数据填充**（对照 Task A3 的 `manifest.schema.json`，不是 example 占位符版本，是这次真实运行的记录）

```bash
python3 -c "
import json, jsonschema, hashlib
from pathlib import Path

schema = json.loads(Path('extraction/schema/manifest.schema.json').read_text())
manifest = {
    'manifest_version': '1.0.0',
    'created_at': '<回填 ISO8601 时间戳>',
    'host': 'linux-x86_64',
    'kernel_version': 'v6.6',
    'kernel_config_path': '<回填>',
    'kernel_config_hash': '<回填 sha256sum 结果>',
    'subsystem_scope': 'io_uring/<选定文件>.c (phase-1 TU-level smoke)',
    'bc_files': ['<回填>'],
    'll_files': [],
    'compile_commands_json': '<回填>',
    'bc_list': '<回填>',
    'debug_info_kind': 'dwarf',
    'btf_available': False,
    'pahole_version': None,
    'vmlinux_btf_path': None,
    'clang_version': '<回填 clang --version 完整输出>',
    'llvm_version': '21.1.0',
    'svf_commit': '<回填 Task B2 记录的完整 SHA>',
    'opt_level': '-O2 -g',
    'id_generation_rule_version': '1.0.0',
}
jsonschema.validate(manifest, schema)
Path('extraction/manifests/phase1-smoke.json').write_text(json.dumps(manifest, indent=2))
print('manifest written and schema-valid')
"
```
Expected: 打印 `manifest written and schema-valid`，无 `ValidationError`。

- [ ] **提交**

```bash
git add extraction/manifests/phase1-smoke.json
git commit -m "docs: phase-1 smoke run manifest instance"
```

---

### Task B8: 升级 SVF commit 前置校验位（收尾）

**Files:**
- Create: `extraction/RUNBOOK.md`

- [ ] **写升级流程说明**（落实 v3.1 P1"升级 SVF commit 需重跑全部 golden tests 后才能替换主干 pin"这条约束，避免以后有人绕过）

```markdown
# extraction/ Runbook

## 升级 SVF pin 的流程（v3.1 P1 硬约束）

1. 不允许直接改 `SVF_DIR` 指向新 commit 就切主干。
2. 新 commit 先在独立分支/目录构建，跑一遍本计划 Task B6 的端到端流程 + 阶段二建立后的全部 golden tests。
3. 全部通过后，才更新 `docs/kernel-target-decision.md` 与 `extraction/manifests/*.json` 里的 `svf_commit`，并在提交信息里注明"升级 svf_commit: <旧SHA> -> <新SHA>，golden tests 全部通过"。

## 重跑本计划端到端冒烟（供后续验证工具链未漂移）

```bash
export LLVM_DIR=~/toolchains/llvm-21.1.0
export SVF_DIR=~/toolchains/SVF/Release-build
cd extraction && cmake --build build -j"$(nproc)"
./build/extraction-driver --run-profile tu --bc-inputs <TU.bc> --out /tmp/facts.jsonl
python3 -c "from implicitfuzz.ingestion.ingest import ingest_jsonl; print(ingest_jsonl('/tmp/facts.jsonl', '/tmp/facts.db'))"
```
```

- [ ] **提交**

```bash
git add extraction/RUNBOOK.md
git commit -m "docs: SVF commit upgrade runbook per v3.1 P1"
```

---

## Spec 覆盖自查（Self-Review）

| 来源条目 | 对应任务 |
|---|---|
| v3 §12 阶段一：锁 LLVM 21.1.0 + 已验证 SVF commit | B1, B2 |
| v3 §12 阶段一：照抄 svf-ex.cpp 搭最小 driver | A8, B5 |
| v3 §12 阶段一：TU 级小 bitcode | B3 |
| v3 §12 阶段一：最小 call_fact/access_fact（numeric 可） | B5, B6 |
| v3 §12 阶段一：打通 bitcode→SVF→JSONL→SQLite | B6 |
| v3 §12 阶段一：opaque pointer 成熟度头号观测目标 | B6（专门小节） |
| v3 §14.1 golden tests + 人工标注 | B4 |
| v3 §14.2 primitive_summary.yaml | A4 |
| v3 §14.3 instruction_id/object_id/object_scope 规则 | A2（access_fact/alias_fact schema） |
| v3 §14.4 构建产物 manifest | A3, B7 |
| v3 §14.5 置信度模型 + 失败输出策略 | A5 |
| v3 §14.6 性能三档运行模式 | A6 |
| v3 §14.7 SQLite ingestion schema | A7 |
| v3.1 P1 svf_commit pin + 升级流程 | B2, B8 |
| v3.1 P2 semantic_op 改名 | A2（access_fact schema 字段名） |
| v3.1 P3/P3.1 summary effects + effect_bindings | A4 |
| v3.1 P4 多 provenance | A2（common schema） |
| v3.1 P5 numeric_kind | A2（access_fact schema） |
| v3.1 P6 object_scope + formal_param 约束 | A2（access_fact/alias_fact schema，注释写明约束） |
| v3.1 P7/P7.1 instruction_id 作用域 + cross_opt_key 降级 | A2（access_fact schema） |
| v3.1 P8 source_location 宏栈 | A2（common schema） |
| v3.1 P9 golden case 4a/4b/5（阶段二范围，本计划只搭 case1） | 已在 B4 README 中注明，非本计划范围，留给阶段二 |
| v3.1 P11 BTF | A3（manifest schema 字段），驱动逻辑本身留阶段二 |
| v3.1 P12 branch/gate facts | A2（schema 先行落地，driver 实现留阶段二） |
| v3.1 P13 io_uring primitive 清单 | A4 |
| Mac 不适合本地编译 SVF/LLVM21（`svf-extraction-toolchain-notes.md`） | Global Constraints + A8 README + B1/B2/B5 引用 |

**未覆盖、明确留给阶段二/三（不在本计划范围内，按用户既定范围决策）：**
- v3 §3.3 三档优化矩阵对照跑法（golden tests × `-O0`/`-Og`/`-O2 -g`）——本计划只用单一默认档位冒烟。
- v3 §6.3 container_of 三元组还原、v3 §7 alloc wrapper 种子传播——golden case 2/3，阶段二范围。
- v3 §5.8/v3.1 P12 的 branch-local backward slice 实现——schema 已落地，driver 逻辑留阶段二。
- v3 §12 阶段三 TypeCopilot 对照评估——仅当 B6 的 opaque pointer 观测显示需要提前介入时才触发，否则按文档顺序留到阶段三。

---

## Execution Handoff

Plan 已保存到 `docs/superpowers/plans/2026-07-08-static-extraction-phase1.md`。两种执行方式：

1. **Subagent-driven（推荐用于 Track A）**：Track A 全部任务无 Linux 依赖，可在当前会话用 `superpowers:subagent-driven-development` 逐任务派发、逐任务 review。
2. **Track B 必须单独起 Linux 会话/环境执行**：不建议用当前 Mac 会话的 subagent 执行 Track B（会重蹈 `docs/svf-extraction-toolchain-notes.md` 的覆辙）。建议 Track A 全部完成并提交后，把整个 worktree 分支同步到 Linux 服务器，在那边新开一个会话，用 `superpowers:executing-plans` 或 `superpowers:subagent-driven-development` 接着执行 Task B1–B8。
