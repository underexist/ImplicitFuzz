# Ledger Reconciliation Layer (Pilot Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建 pilot 的 Phase 1 对账层——让"LLM 说出的字段(含 numeric-only 的 `ctx->nr_user_files`)能被事实账可靠对上",即 `name→(struct,offset)` 解析 + 三态字段存在性校验,数据源是抽取器安全 emit 的 struct-layout side-table。

**Architecture:** 抽取器加**opt-in flag** `-emit-struct-layout`,只读 DWARF 遍历导出 `struct_layout_fact`(不碰 SVF 热路径、默认关、回归不受影响);ingest 成 `struct_layout` 表;Python 侧 `reconcile/layout.py`(name→offset)+ `reconcile/field_existence.py`(三态校验:confirmed_symbolic / confirmed_numeric / unconfirmed)。

**Tech Stack:** C++/LLVM-21 + SVF(抽取器,服务器),Python ≥3.8 + sqlite3 + jsonschema + pytest。

## Global Constraints

- schema 只加不改;`struct_layout_fact` 是**新增 additive fact**,`schema_version` 恒 `"1.0.0"`;`validate_facts_schema.py` 自动 glob 发现,无需改它。
- struct-layout emit **默认关**(flag `-emit-struct-layout`,`cl::init(false)`);回归/golden 不传该 flag → 事实输出不变 → golden 计数不破。
- struct-layout emit **不进 SVF points-to / `getLLVMValue()` 段错误热路径**(坑#1);仅遍历 DWARF 元数据。
- 布局用被分析的 6.1 树自己的 DWARF(.bc 带 -g);**不用运行时 BTF**(nr_user_files 在运行时内核是 byte 120,6.1 是 160)。
- confidence 枚举:`high|medium_high|medium|medium_low|low`。numeric 确认置信复用 `field_key.lower_confidence`。
- 服务器工作流:本地改 → `scp -P 19999 ... xujunru@localhost:/home/xujunru/ImplicitFuzz/...` → 服务器 build/test/`git commit -F <file>`。C++ 构建用 RUNBOOK 的 LD 路径(见 Task 2)。
- 收口:`run_phase1_regression.sh` → `[phase1] ALL PASS`(flag 关);`python3 -m pytest tests/` 全绿。
- 参考 spec:`docs/superpowers/specs/2026-07-11-llm-predicate-inference-pilot-design.md`(Phase 1 = §3)。

Phase 2(LLM 判读 + 评测)是**独立后续计划**,不在本计划内。

---

### Task 1: struct_layout_fact schema + ingest 表

**Files:**
- Create: `extraction/schema/facts/struct_layout_fact.schema.json`
- Modify: `extraction/schema/facts/common.schema.json`（`fact_type` enum 追加 `struct_layout_fact`——注册新 fact 类型必需;additive enum 值,坑#2 允许"只加枚举值",对现有类型零语义改动）
- Modify: `src/implicitfuzz/ingestion/schema.sql`(加 `struct_layout_fact` 表)
- Modify: `src/implicitfuzz/ingestion/ingest.py`(`_JSON_COLUMNS` 加一项)
- Test: `tests/test_struct_layout_ingest.py`

**Interfaces:**
- Produces: ingest 后 SQLite 有 `struct_layout` 表,列 `struct_name, member_name, byte_offset(int), member_type` + common 信封列。

- [ ] **Step 1: 写 schema 文件**

`extraction/schema/facts/struct_layout_fact.schema.json`:
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/schema/facts/struct_layout_fact.schema.json",
  "allOf": [{ "$ref": "common.schema.json" }],
  "properties": {
    "struct_name": { "type": "string" },
    "member_name": { "type": "string" },
    "byte_offset": { "type": "integer", "minimum": 0 },
    "member_type": { "type": "string" }
  },
  "required": ["struct_name", "member_name", "byte_offset", "member_type"]
}
```

- [ ] **Step 2: 加 ingest 表 + 列映射**

`src/implicitfuzz/ingestion/schema.sql` 末尾加(镜像 entry_fact 表):
```sql
CREATE TABLE IF NOT EXISTS struct_layout_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  kernel_version TEXT NOT NULL,
  llvm_version TEXT NOT NULL,
  opt_level TEXT NOT NULL,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  struct_name TEXT NOT NULL,
  member_name TEXT NOT NULL,
  byte_offset INTEGER NOT NULL,
  member_type TEXT NOT NULL,
  source_location_json TEXT,
  primary_provenance TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  confidence TEXT NOT NULL,
  confidence_score REAL
);
```

`src/implicitfuzz/ingestion/ingest.py` 的 `_JSON_COLUMNS` 加一项(注意 table 名 = fact_type = `struct_layout_fact`):
```python
    "struct_layout_fact": {"source_location": "source_location_json", "provenance": "provenance_json"},
```

- [ ] **Step 3: 写 ingest 测试**

`tests/test_struct_layout_ingest.py`:
```python
import json
import sqlite3
from pathlib import Path

from implicitfuzz.ingestion.ingest import ingest_jsonl


def _fact():
    return {
        "fact_type": "struct_layout_fact",
        "schema_version": "1.0.0",
        "kernel_version": "6.1",
        "llvm_version": "21.1.8",
        "opt_level": "-O2 -g",
        "bc_unit": "io_uring_rsrc.pre.bc",
        "function": "io_ring_ctx",
        "source_location": None,
        "primary_provenance": "dwarf",
        "provenance": ["dwarf"],
        "confidence": "high",
        "struct_name": "io_ring_ctx",
        "member_name": "nr_user_files",
        "byte_offset": 160,
        "member_type": "unsigned int",
    }


def test_struct_layout_fact_ingests(tmp_path):
    jsonl = tmp_path / "facts.jsonl"
    jsonl.write_text(json.dumps(_fact()) + "\n")
    db = tmp_path / "facts.db"
    counts = ingest_jsonl(str(jsonl), str(db))
    assert counts.get("struct_layout_fact") == 1

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT struct_name, member_name, byte_offset, member_type FROM struct_layout_fact"
    ).fetchone()
    assert row == ("io_ring_ctx", "nr_user_files", 160, "unsigned int")
```

- [ ] **Step 4: 运行确认通过**

Run(本地 venv 见下 or 服务器): `python3 -m pytest tests/test_struct_layout_ingest.py -v`
Expected: PASS(1 passed)。
本地快跑:`PYTHONPATH=src <venv>/python -m pytest tests/test_struct_layout_ingest.py -o addopts="" -v`(需 jsonschema)。

- [ ] **Step 5: 提交**

```bash
git add extraction/schema/facts/struct_layout_fact.schema.json src/implicitfuzz/ingestion/schema.sql src/implicitfuzz/ingestion/ingest.py tests/test_struct_layout_ingest.py
git commit -F <msg>   # "feat(schema): additive struct_layout_fact + ingest table"
```

---

### Task 2: 抽取器 struct-layout emit(C++,opt-in flag,服务器)

**Files:**
- Modify: `extraction/src/implicitfuzz-extract.cpp`(加 flag + `writeStructLayoutFacts` + main hook + DwarfStructIndex accessor + 日志)

**Interfaces:**
- Produces: 传 `-emit-struct-layout` 时,每 struct×member 一行 `struct_layout_fact`;不传则无输出(回归不变)。

- [ ] **Step 1: 加 CLI flag**（在 `JsonlOut` 的 `cl::opt` 附近,约 line 46）

```cpp
static llvm::cl::opt<bool> EmitStructLayout(
    "emit-struct-layout",
    llvm::cl::desc("Emit struct_layout_fact rows (offset->member) from DWARF; off by default"),
    llvm::cl::init(false));
```

- [ ] **Step 2: 给 DwarfStructIndex 加只读访问器**（class 内 public,约 line 992 之后）

```cpp
    const std::unordered_map<std::string, const DICompositeType*>& structs() const
    {
        return byName_;
    }
```

- [ ] **Step 3: 写 emit 函数**（放在 `scanDispatchTables` 之前,约 line 1941）

```cpp
// Emit one struct_layout_fact per (struct, member) from DWARF: canonical
// struct name, member name, byte offset, member type. Read-only DWARF
// traversal (DebugInfoFinder-built index) -- deliberately off the SVF
// points-to / getLLVMValue() hot path (坑#1). Gated by -emit-struct-layout.
static void writeStructLayoutFacts(std::ofstream& out, const RunMetadata& runMeta,
                                   const DwarfStructIndex& dwarfIndex,
                                   uint64_t& structLayoutFactCount)
{
    const SchemaConfidence confidence = schemaConfidenceFromScore(0.98);
    const std::vector<std::string> provenance = {"dwarf"};
    for (const auto& entry : dwarfIndex.structs())
    {
        const std::string& structName = entry.first;
        const DICompositeType* composite = entry.second;
        if (!composite || structName.empty())
            continue;
        for (Metadata* element : composite->getElements())
        {
            const auto* member = dyn_cast_or_null<DIDerivedType>(element);
            if (!member || member->getTag() != dwarf::DW_TAG_member)
                continue;
            if (member->getName().empty())
                continue;
            const uint64_t byteOffset = member->getOffsetInBits() / 8;
            const std::string memberName = member->getName().str();
            const std::string memberType =
                renderDwarfTypeNameKeepTypedef(member->getBaseType());
            out << "{"
                << "\"fact_type\":\"struct_layout_fact\","
                << commonEnvelopeNoInstJson(runMeta, structName, "dwarf", provenance,
                                            confidence)
                << ","
                << "\"struct_name\":\"" << jsonEscape(structName) << "\","
                << "\"member_name\":\"" << jsonEscape(memberName) << "\","
                << "\"byte_offset\":" << byteOffset << ","
                << "\"member_type\":\"" << jsonEscape(memberType) << "\""
                << "}\n";
            ++structLayoutFactCount;
        }
    }
}
```

- [ ] **Step 4: main 里挂钩**（在 `factCount += entryFactCount;`,约 line 2152 之后)

```cpp
    uint64_t structLayoutFactCount = 0;
    if (EmitStructLayout())
        writeStructLayoutFacts(out, runMeta, dwarfIndex, structLayoutFactCount);
    factCount += structLayoutFactCount;
```

并在 entry_fact 日志(约 line 2342)之后加一行:
```cpp
    std::cout << "[implicitfuzz-extract] struct_layout_fact (opt-in): "
              << structLayoutFactCount << "\n";
```

- [ ] **Step 5: scp + 构建 + 回归(flag 关,必须不变)**

```bash
scp -P 19999 extraction/src/implicitfuzz-extract.cpp xujunru@localhost:/home/xujunru/ImplicitFuzz/extraction/src/implicitfuzz-extract.cpp
ssh -p 19999 xujunru@localhost 'bash -lc "
ROOT=/home/xujunru/ImplicitFuzz; EX=\$ROOT/extraction
LLVM_LIB=/home/xujunru/.local/opt/llvm21-rpm/usr/lib64
LLVM21_LIB=\$LLVM_LIB/llvm21/lib64
export LLVM_DIR=\$LLVM21_LIB/cmake/llvm
export SVF_DIR=/home/xujunru/implicitfuzz-toolchain/SVF/verify-llvm21-build4
env -u LD_LIBRARY_PATH LD_LIBRARY_PATH=\"\$LLVM_LIB:\$LLVM21_LIB\" cmake --build \$EX/build -j\$(nproc)
\$EX/scripts/run_phase1_regression.sh 2>&1 | tail -4
"'
```
Expected: 构建成功;`[phase1] ALL PASS`(flag 默认关,facts 不变,golden 不破)。

- [ ] **Step 6: 真实数据校验(flag 开)**

```bash
ssh -p 19999 xujunru@localhost 'bash -lc "
EX=/home/xujunru/ImplicitFuzz/extraction
LLVM_LIB=/home/xujunru/.local/opt/llvm21-rpm/usr/lib64
RUN_LD=\$LLVM_LIB:\$LLVM_LIB/llvm21/lib64:/home/xujunru/implicitfuzz-toolchain/z3-install/lib64:/home/xujunru/implicitfuzz-toolchain/SVF/verify-llvm21-build4/lib
env -u LD_LIBRARY_PATH LD_LIBRARY_PATH=\$RUN_LD \$EX/build/implicitfuzz-extract \
  -emit-struct-layout -jsonl-out /tmp/rsrc_layout.facts.jsonl \
  -primitive-summary \$EX/schema/primitive_summary.json /tmp/io_uring_rsrc.bc >/dev/null 2>&1
python3 \$EX/scripts/validate_facts_schema.py /tmp/rsrc_layout.facts.jsonl \$EX/schema/facts | tail -2
grep -o \"{[^}]*\\\"struct_name\\\":\\\"io_ring_ctx\\\"[^}]*\\\"member_name\\\":\\\"nr_user_files\\\"[^}]*}\" /tmp/rsrc_layout.facts.jsonl | head -1
"'
```
Expected: schema validation OK;打印一行含 `io_ring_ctx` / `nr_user_files` / `byte_offset:160`。**若 offset 非 160**:核对是否读了正确 6.1 DWARF(非 BTF);以账里 `ctx->nr_user_files=` 写点偏移为准记录实际值到 findings。

- [ ] **Step 7: 提交(服务器)**

```bash
ssh -p 19999 xujunru@localhost 'cd /home/xujunru/ImplicitFuzz && git add extraction/src/implicitfuzz-extract.cpp && git commit -F /tmp/<msg>'
# msg: "feat(extract): opt-in -emit-struct-layout struct_layout_fact from DWARF"
```

---

### Task 3: name→(struct,offset) 解析器 (layout.py)

**Files:**
- Create: `src/implicitfuzz/reconcile/__init__.py`(空)
- Create: `src/implicitfuzz/reconcile/layout.py`
- Test: `tests/test_reconcile_layout.py`

**Interfaces:**
- Consumes: `struct_layout` 表(Task 1);`canonicalize_struct_type`(已有 `implicitfuzz.evidence.field_key`)。
- Produces:
  - `class LayoutIndex` — 构造 `LayoutIndex(conn)`;方法 `offset_of(struct_name: str, member_name: str) -> int | None`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_reconcile_layout.py
import sqlite3

from implicitfuzz.reconcile.layout import LayoutIndex

_DDL = """
CREATE TABLE struct_layout_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  struct_name TEXT NOT NULL,
  member_name TEXT NOT NULL,
  byte_offset INTEGER NOT NULL,
  member_type TEXT NOT NULL
);
"""


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    conn.execute("INSERT INTO struct_layout_fact (struct_name, member_name, byte_offset, member_type) "
                 "VALUES ('io_ring_ctx','nr_user_files',160,'unsigned int')")
    conn.execute("INSERT INTO struct_layout_fact (struct_name, member_name, byte_offset, member_type) "
                 "VALUES ('io_ring_ctx','file_data',816,'struct io_rsrc_data *')")
    return conn


def test_offset_of_resolves_member():
    idx = LayoutIndex(_db())
    assert idx.offset_of("io_ring_ctx", "nr_user_files") == 160


def test_offset_of_canonicalizes_struct_name():
    # caller may pass "struct io_ring_ctx"; layout stores canonical "io_ring_ctx"
    idx = LayoutIndex(_db())
    assert idx.offset_of("struct io_ring_ctx", "nr_user_files") == 160


def test_offset_of_unknown_returns_none():
    idx = LayoutIndex(_db())
    assert idx.offset_of("io_ring_ctx", "no_such_member") is None
    assert idx.offset_of("no_such_struct", "x") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_reconcile_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'implicitfuzz.reconcile'`

- [ ] **Step 3: 实现**

`src/implicitfuzz/reconcile/__init__.py`: 空文件。

`src/implicitfuzz/reconcile/layout.py`:
```python
"""name -> (struct, byte offset) resolution over the struct_layout table.

Backs the field-existence reconciliation of numeric-only fields: a field
referenced by name (io_ring_ctx.nr_user_files) is resolved to its byte
offset via DWARF struct layout, so it can be matched against numeric-only
access_facts (which carry only an offset, no symbol).
"""

from __future__ import annotations

import sqlite3

from implicitfuzz.evidence.field_key import canonicalize_struct_type


class LayoutIndex:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._by_key: dict[tuple[str, str], int] = {}
        for struct_name, member_name, byte_offset in conn.execute(
            "SELECT struct_name, member_name, byte_offset FROM struct_layout_fact"
        ).fetchall():
            canon = canonicalize_struct_type(struct_name) or struct_name
            self._by_key.setdefault((canon, member_name), byte_offset)

    def offset_of(self, struct_name: str, member_name: str) -> int | None:
        canon = canonicalize_struct_type(struct_name) or struct_name
        return self._by_key.get((canon, member_name))
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_reconcile_layout.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/reconcile/__init__.py src/implicitfuzz/reconcile/layout.py tests/test_reconcile_layout.py
git commit -F <msg>   # "feat(reconcile): struct-layout name->offset resolver"
```

---

### Task 4: 三态字段存在性对账校验器 (field_existence.py)

**Files:**
- Create: `src/implicitfuzz/reconcile/field_existence.py`
- Test: `tests/test_field_existence.py`

**Interfaces:**
- Consumes: `LayoutIndex`(Task 3);`access_fact` 表;`struct_layout` 表。
- Produces:
  - `@dataclass FieldMatch { status: str, access_fact_id: int | None, offset: int | None }`,`status ∈ {"confirmed_symbolic","confirmed_numeric","unconfirmed"}`。
  - `reconcile_field(conn, layout: LayoutIndex, struct_name: str, member_name: str) -> FieldMatch`。

**对账语义:** ① access_fact 有 `access_path_symbolic == "<struct>.<member>"` → confirmed_symbolic;② 否则经 layout 解析 offset,找 access_fact `numeric_kind ∈ {gep_offsets,byte_range}` 且 `access_path_numeric` 含该 offset(格式 `[160]`)→ confirmed_numeric;③ 否则 unconfirmed。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_field_existence.py
import sqlite3

from implicitfuzz.reconcile.layout import LayoutIndex
from implicitfuzz.reconcile.field_existence import reconcile_field

_DDL = """
CREATE TABLE struct_layout_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  struct_name TEXT, member_name TEXT, byte_offset INTEGER, member_type TEXT
);
CREATE TABLE access_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  semantic_op TEXT, access_path_symbolic TEXT,
  numeric_kind TEXT, access_path_numeric TEXT
);
"""


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    conn.execute("INSERT INTO struct_layout_fact (struct_name,member_name,byte_offset,member_type) "
                 "VALUES ('io_ring_ctx','nr_user_files',160,'unsigned int')")
    # symbolic field present
    conn.execute("INSERT INTO access_fact (semantic_op,access_path_symbolic,numeric_kind,access_path_numeric) "
                 "VALUES ('write','io_ring_ctx.file_data','gep_offsets','[816]')")
    # numeric-only write at offset 160 (nr_user_files, no symbol)
    conn.execute("INSERT INTO access_fact (semantic_op,access_path_symbolic,numeric_kind,access_path_numeric) "
                 "VALUES ('write',NULL,'gep_offsets','[160]')")
    return conn


def test_confirmed_symbolic():
    conn = _db()
    m = reconcile_field(conn, LayoutIndex(conn), "io_ring_ctx", "file_data")
    assert m.status == "confirmed_symbolic"
    assert m.access_fact_id is not None


def test_confirmed_numeric_via_offset():
    conn = _db()
    m = reconcile_field(conn, LayoutIndex(conn), "io_ring_ctx", "nr_user_files")
    assert m.status == "confirmed_numeric"
    assert m.offset == 160
    assert m.access_fact_id is not None


def test_unconfirmed_hallucination():
    conn = _db()
    m = reconcile_field(conn, LayoutIndex(conn), "io_ring_ctx", "nonexistent_field")
    assert m.status == "unconfirmed"
    assert m.access_fact_id is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_field_existence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'implicitfuzz.reconcile.field_existence'`

- [ ] **Step 3: 实现**

`src/implicitfuzz/reconcile/field_existence.py`:
```python
"""Three-state field-existence reconciliation against the access_fact ledger.

The design's anti-hallucination red line (purpose.md §4.2(3)): every field a
predicate references must exist in the fact ledger, else it is a
hallucination. Robust to numeric-only fields -- resolves the name to a byte
offset via the struct layout and matches numeric access_facts on that offset.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from implicitfuzz.reconcile.layout import LayoutIndex

_NUMERIC_KINDS = ("gep_offsets", "byte_range")


@dataclass(frozen=True)
class FieldMatch:
    status: str  # confirmed_symbolic | confirmed_numeric | unconfirmed
    access_fact_id: int | None = None
    offset: int | None = None


def reconcile_field(
    conn: sqlite3.Connection,
    layout: LayoutIndex,
    struct_name: str,
    member_name: str,
) -> FieldMatch:
    symbolic_path = f"{struct_name}.{member_name}"
    row = conn.execute(
        "SELECT id FROM access_fact WHERE access_path_symbolic = ? ORDER BY id LIMIT 1",
        (symbolic_path,),
    ).fetchone()
    if row is not None:
        return FieldMatch("confirmed_symbolic", access_fact_id=row[0])

    offset = layout.offset_of(struct_name, member_name)
    if offset is not None:
        needle = f"[{offset}]"
        row = conn.execute(
            "SELECT id FROM access_fact "
            "WHERE numeric_kind IN (?, ?) AND access_path_numeric = ? "
            "ORDER BY id LIMIT 1",
            (_NUMERIC_KINDS[0], _NUMERIC_KINDS[1], needle),
        ).fetchone()
        if row is not None:
            return FieldMatch("confirmed_numeric", access_fact_id=row[0], offset=offset)

    return FieldMatch("unconfirmed")
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_field_existence.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: 全套 + 真实数据验收(服务器)**

在服务器上把 struct_layout(flag-on 抽取)+ rsrc access facts ingest 到一个 DB,验证旗舰对账:
```bash
ssh -p 19999 xujunru@localhost 'cd /home/xujunru/ImplicitFuzz && python3 - <<PY
import sqlite3
from implicitfuzz.ingestion.ingest import ingest_jsonl
from implicitfuzz.reconcile.layout import LayoutIndex
from implicitfuzz.reconcile.field_existence import reconcile_field
db="/tmp/reconcile_accept.db"
import os; os.path.exists(db) and os.remove(db)
ingest_jsonl("/tmp/io_uring_rsrc.facts.jsonl", db)     # numeric-only nr_user_files write
ingest_jsonl("/tmp/rsrc_layout.facts.jsonl", db)       # struct layout (flag-on)
conn=sqlite3.connect(db); L=LayoutIndex(conn)
print("nr_user_files:", reconcile_field(conn, L, "io_ring_ctx", "nr_user_files"))
print("file_data:", reconcile_field(conn, L, "io_ring_ctx", "file_data"))
print("bogus:", reconcile_field(conn, L, "io_ring_ctx", "nonexistent_field"))
PY'
```
Expected: `nr_user_files` → `confirmed_numeric offset=160`;`file_data` → `confirmed_symbolic`;`bogus` → `unconfirmed`。这复现 spike 里"对账层救回 numeric-only 旗舰字段、拦住编造字段"的结论。
再跑 `python3 -m pytest tests/`(服务器)→ 全绿。

- [ ] **Step 6: 提交**

```bash
git add src/implicitfuzz/reconcile/field_existence.py tests/test_field_existence.py
git commit -F <msg>   # "feat(reconcile): three-state field-existence checker (symbolic/numeric/hallucination)"
```

---

## Self-Review

- **Spec 覆盖(Phase 1 = spec §3):** A. struct-layout emit → Task 2(+schema/ingest Task 1);B. name→offset 解析 → Task 3;C. 三态对账校验 → Task 4。additive schema / opt-in flag / 不碰 SVF 热路径 / 6.1-DWARF-not-BTF / numeric 置信降档 → Global Constraints + 各 Task。端到端验收(nr_user_files confirmed_numeric、编造字段 unconfirmed)→ Task 4 Step 5。✅
- **占位符:** 无 TBD;每步含完整代码/精确命令;commit msg 用 `<msg>` 占位仅指"填入信息文件",内容已给。✅
- **类型一致:** `LayoutIndex.offset_of` / `reconcile_field` / `FieldMatch{status,access_fact_id,offset}` / `struct_layout_fact` 列名 / `canonicalize_struct_type` 跨 Task 一致;access_fact numeric 匹配用 `[offset]` 格式(与真实账 `access_path_numeric="[160]"` 一致,见 spike 数据)。✅
- **已知开放点:** struct base↔struct_name 归属对 numeric-only access 受限(numeric-only `field_type=null`)——Task 4 v1 的 confirmed_numeric 用"全账同偏移 numeric 匹配"(不限定 base struct),可能过宽;spec §3.C 已标注为覆盖率/精度待量项,Phase 2 findings 记录。若需收紧,后续加 base-struct 过滤(follow-up)。
