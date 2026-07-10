# 反查 (gate → 前序写调用) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 派生 `gate_prior_write_candidate` —— 把 `gate_seed_candidate` 的被门控符号字段跨 TU 反查到其前序 `write` access_fact,得到该分支的隐式前序依赖候选(v1 纯 Python、纯符号)。

**Architecture:** 新增共用"字段键" helper(v1 符号,numeric 分支已实现但真实 v1 数据无 struct type 故返回 None);`gate_seed_candidate` 兼容式加两列存字段键;新增 `reverse_lookup.py` 派生跨 TU 反查表。三者均在 ingest 后的 SQLite 派生层,不碰 schema-bound fact。

**Tech Stack:** Python ≥3.8,sqlite3,pytest。src/ 布局,包 `implicitfuzz`(editable 安装,直接 import)。

## Global Constraints

- `schema_version` 恒 `"1.0.0"`;**只加派生表列,绝不改 schema-bound fact**。`gate_seed_candidate` / `gate_prior_write_candidate` 是派生表(非 schema-bound)。
- confidence 枚举只能取:`high | medium_high | medium | medium_low | low`。
- 派生表用 `CREATE TABLE IF NOT EXISTS` + `INSERT OR IGNORE`,幂等;派生表每次重建(smoke DB recreated),不需 ALTER 迁移。
- 字段键 v1:符号 access → `sym:<path>`;numeric-only 真实数据(`field_type` 为 null)→ `None`(不匹配)。numeric 分支代码实现且单测(用合成 struct type),但真实 v1 数据不触发——见 spec §0。
- YAGNI:不做 LLM 谓词、不做跨对象联合门控、syscall 归属只做"写点 function 恰为 entry_symbol handler"的精确匹配,否则 null。
- 服务器工作流:本地改 → `scp -P 19999 ... xujunru@localhost:/home/xujunru/ImplicitFuzz/...` → 服务器 `pytest` / 回归 / `git commit -F <file>`(commit msg 含反引号用文件,别内联)。权威仓库在服务器。
- 收口标准:`extraction/scripts/run_phase1_regression.sh` → `[phase1] ALL PASS`;`python3 -m pytest tests/` 全绿(现 23 + 新增)。

参考 spec:`docs/superpowers/specs/2026-07-10-reverse-lookup-gate-prior-write-design.md`。

---

### Task 1: 字段键 helper (field_key.py)

**Files:**
- Create: `src/implicitfuzz/evidence/field_key.py`
- Test: `tests/test_field_key.py`

**Interfaces:**
- Produces:
  - `class FieldKey` — frozen dataclass,字段 `kind: str`("symbolic"|"numeric")、`key: str`、`confidence: str`、`source_access_fact_id: int | None`。
  - `field_key_for_access(*, access_path_symbolic, field_type=None, numeric_kind=None, access_path_numeric=None, confidence="low", source_access_fact_id=None) -> FieldKey | None`
  - `canonicalize_struct_type(raw: str | None) -> str | None`
  - `lower_confidence(conf: str) -> str`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_field_key.py
from implicitfuzz.evidence.field_key import (
    FieldKey,
    field_key_for_access,
    canonicalize_struct_type,
    lower_confidence,
)


def test_symbolic_access_yields_sym_key():
    fk = field_key_for_access(
        access_path_symbolic="io_ring_ctx.file_data",
        confidence="medium",
        source_access_fact_id=7,
    )
    assert fk == FieldKey("symbolic", "sym:io_ring_ctx.file_data", "medium", 7)


def test_numeric_only_real_data_yields_none():
    # real v1 numeric-only facts carry no struct type (field_type is null)
    fk = field_key_for_access(
        access_path_symbolic=None,
        field_type=None,
        numeric_kind="gep_offsets",
        access_path_numeric="[128]",
        confidence="low",
    )
    assert fk is None


def test_numeric_with_struct_type_yields_num_key_lower_confidence():
    # post-v1 shape: once the extractor emits a struct type, the numeric
    # branch fires. confidence is strictly lowered vs the symbolic path.
    fk = field_key_for_access(
        access_path_symbolic=None,
        field_type="struct io_ring_ctx",
        numeric_kind="gep_offsets",
        access_path_numeric="[120]",
        confidence="high",
    )
    assert fk == FieldKey("numeric", "num:io_ring_ctx@[120]", "medium", None)


def test_canonicalize_strips_struct_const_volatile_noise():
    assert canonicalize_struct_type("struct io_ring_ctx") == "io_ring_ctx"
    assert canonicalize_struct_type("const struct io_ring_ctx *") == "io_ring_ctx"
    assert canonicalize_struct_type("volatile io_ring_ctx") == "io_ring_ctx"
    assert canonicalize_struct_type("io_ring_ctx") == "io_ring_ctx"
    assert canonicalize_struct_type(None) is None
    assert canonicalize_struct_type("  ") is None


def test_lower_confidence_drops_one_notch_and_floors_at_low():
    assert lower_confidence("high") == "medium_high"
    assert lower_confidence("medium_high") == "medium"
    assert lower_confidence("medium") == "medium_low"
    assert lower_confidence("medium_low") == "low"
    assert lower_confidence("low") == "low"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_field_key.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'implicitfuzz.evidence.field_key'`

- [ ] **Step 3: 实现**

```python
# src/implicitfuzz/evidence/field_key.py
"""Shared field-key normalization for gate derivation and reverse-lookup.

A *field key* is the canonical identity of an object field, used both to
decide "is this field written somewhere (a state carrier)" and to JOIN a
gated field to its prior writes. Symbolic access paths map to `sym:<path>`.
Numeric-only accesses map to `num:<canonical_struct>@<offset>` -- but only
when a struct type is available; in v1 real facts carry no struct type for
numeric-only accesses (see spec 决策修订 §0), so the numeric branch returns
None on real data and is exercised only by synthetic tests until the
follow-up extractor-symbolization upgrade lands.
"""

from __future__ import annotations

from dataclasses import dataclass

_CONFIDENCE_LADDER = ["low", "medium_low", "medium", "medium_high", "high"]
_NUMERIC_KINDS = {"gep_offsets", "byte_range"}


@dataclass(frozen=True)
class FieldKey:
    kind: str  # "symbolic" | "numeric"
    key: str
    confidence: str
    source_access_fact_id: int | None = None


def canonicalize_struct_type(raw: str | None) -> str | None:
    """Strip struct/const/volatile/pointer noise so one type has one spelling."""
    if not raw:
        return None
    tokens = raw.replace("*", " ").split()
    tokens = [t for t in tokens if t not in {"struct", "union", "enum", "const", "volatile"}]
    if not tokens:
        return None
    return tokens[-1]


def lower_confidence(conf: str) -> str:
    """One notch down the confidence ladder, floored at 'low'."""
    try:
        idx = _CONFIDENCE_LADDER.index(conf)
    except ValueError:
        return "low"
    return _CONFIDENCE_LADDER[max(0, idx - 1)]


def field_key_for_access(
    *,
    access_path_symbolic: str | None,
    field_type: str | None = None,
    numeric_kind: str | None = None,
    access_path_numeric: str | None = None,
    confidence: str = "low",
    source_access_fact_id: int | None = None,
) -> FieldKey | None:
    if access_path_symbolic:
        return FieldKey(
            "symbolic", f"sym:{access_path_symbolic}", confidence, source_access_fact_id
        )
    struct = canonicalize_struct_type(field_type)
    if struct is None or numeric_kind not in _NUMERIC_KINDS or not access_path_numeric:
        return None  # v1 real numeric-only data lands here (no struct type)
    return FieldKey(
        "numeric",
        f"num:{struct}@{access_path_numeric}",
        lower_confidence(confidence),
        source_access_fact_id,
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_field_key.py -v`
Expected: PASS(5 passed)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/evidence/field_key.py tests/test_field_key.py
git commit -F <commit-msg-file>   # msg: "feat(evidence): shared field-key helper (symbolic v1, numeric branch reserved)"
```

---

### Task 2: gate_seed_candidate 加字段键列 (gates.py)

**Files:**
- Modify: `src/implicitfuzz/evidence/gates.py`
- Modify: `tests/test_gate_seeds.py`

**Interfaces:**
- Consumes: `field_key_for_access`, `FieldKey`(Task 1)。
- Produces: `gate_seed_candidate` 新增列 `gated_field_keys_json`、`state_field_keys_json`;每列是 `[{"kind","key","confidence","source_access_fact_id"}, ...]` 的 JSON。旧列 `gated_fields_json` 语义不变(仍只放 symbolic 路径字符串)。

- [ ] **Step 1: 写失败测试(在 test_gate_seeds.py 追加)**

先把 `_setup` 里的 `access_fact` 建表补上 field_key 需要的列(其余测试不受影响):

```python
# tests/test_gate_seeds.py — 替换 _setup 中的 access_fact 建表
        CREATE TABLE access_fact (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          bc_unit TEXT NOT NULL,
          function TEXT NOT NULL,
          semantic_op TEXT NOT NULL,
          access_path_symbolic TEXT,
          base_object_json TEXT,
          instruction_id TEXT,
          field_type TEXT,
          numeric_kind TEXT,
          access_path_numeric TEXT,
          confidence TEXT
        );
```

`_access` 插入语句相应补默认列值:

```python
def _access(conn, *, function, op, symbolic, iid, base='{"object_scope":"formal_param","value":"f:0"}'):
    conn.execute(
        "INSERT INTO access_fact (bc_unit, function, semantic_op, access_path_symbolic, "
        "base_object_json, instruction_id, field_type, numeric_kind, access_path_numeric, confidence) "
        "VALUES ('u.bc', ?, ?, ?, ?, ?, NULL, 'gep_offsets', NULL, 'medium')",
        (function, op, symbolic, base, iid),
    )
```

新增断言测试:

```python
def test_gate_seed_populates_field_key_columns():
    conn = sqlite3.connect(":memory:")
    _setup(conn)
    _access(conn, function="io_register", op="write", symbolic="io_ring_ctx.file_data", iid="w1")
    _access(conn, function="io_rw", op="read", symbolic="io_ring_ctx.file_data", iid="r1")
    _branch(conn, function="io_rw", branch_id="b1", related_loads=["r1"])
    create_gate_seed_table(conn)

    assert derive_gate_seed_candidates(conn) == 1
    row = conn.execute(
        "SELECT gated_fields_json, gated_field_keys_json, state_field_keys_json "
        "FROM gate_seed_candidate"
    ).fetchone()
    # old column: unchanged (symbolic path strings only)
    assert "io_ring_ctx.file_data" in row[0]
    # new columns: field-key dicts with sym: key
    gated_keys = json.loads(row[1])
    assert gated_keys[0]["kind"] == "symbolic"
    assert gated_keys[0]["key"] == "sym:io_ring_ctx.file_data"
    state_keys = json.loads(row[2])
    assert any(k["key"] == "sym:io_ring_ctx.file_data" for k in state_keys)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_gate_seeds.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such column: gated_field_keys_json`

- [ ] **Step 3: 修改 gates.py**

3a. `GATE_SEED_DDL` 在 `status TEXT NOT NULL,` 后、`UNIQUE(...)` 前加两列:

```python
  status TEXT NOT NULL,
  gated_field_keys_json TEXT NOT NULL DEFAULT '[]',
  state_field_keys_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE(bc_unit, function, branch_instruction_id)
```

3b. 顶部加 import:

```python
from implicitfuzz.evidence.field_key import FieldKey, field_key_for_access
```

3c. 替换 `_state_fields` 为 `_state_field_keys`(state carrier = 被 write 的字段,按字段键):

```python
def _state_field_keys(conn: sqlite3.Connection) -> dict[str, FieldKey]:
    """Field keys of fields written somewhere (candidate state carriers)."""
    out: dict[str, FieldKey] = {}
    for row in conn.execute(
        "SELECT id, access_path_symbolic, field_type, numeric_kind, "
        "access_path_numeric, confidence FROM access_fact WHERE semantic_op = 'write'"
    ).fetchall():
        fk = field_key_for_access(
            access_path_symbolic=row["access_path_symbolic"],
            field_type=row["field_type"],
            numeric_kind=row["numeric_kind"],
            access_path_numeric=row["access_path_numeric"],
            confidence=row["confidence"] or "low",
            source_access_fact_id=row["id"],
        )
        if fk is not None and fk.key not in out:
            out[fk.key] = fk
    return out
```

3d. `derive_gate_seed_candidates`:把 `state_fields = _state_fields(conn)` 换成 `state_field_keys = _state_field_keys(conn)`(空则 return 0);扩展 `access_by_id` 查询列;用字段键判定门控并收集字段键。替换 loop 内的字段收集与 INSERT:

```python
    state_field_keys = _state_field_keys(conn)
    if not state_field_keys:
        return 0

    access_by_id = {
        row["instruction_id"]: row
        for row in conn.execute(
            "SELECT id, instruction_id, access_path_symbolic, base_object_json, "
            "field_type, numeric_kind, access_path_numeric, confidence "
            "FROM access_fact WHERE instruction_id IS NOT NULL"
        ).fetchall()
    }
```

loop 内收集(替换原 gated_fields/related_objects/related_access_facts 三个列表的填充块):

```python
        gated_fields: list[str] = []            # old column: symbolic strings
        gated_keys: dict[str, FieldKey] = {}    # new column: field keys
        related_objects: list[str] = []
        related_access_facts: list[str] = []
        for load_id in related:
            acc = access_by_id.get(load_id)
            if acc is None:
                continue
            fk = field_key_for_access(
                access_path_symbolic=acc["access_path_symbolic"],
                field_type=acc["field_type"],
                numeric_kind=acc["numeric_kind"],
                access_path_numeric=acc["access_path_numeric"],
                confidence=acc["confidence"] or "low",
                source_access_fact_id=acc["id"],
            )
            if fk is None or fk.key not in state_field_keys:
                continue
            gated_keys[fk.key] = fk
            related_access_facts.append(load_id)
            if acc["access_path_symbolic"]:
                gated_fields.append(acc["access_path_symbolic"])
            base = acc["base_object_json"]
            if base and base not in related_objects:
                related_objects.append(base)

        if not gated_keys:
            continue

        # dedup old-column symbolic fields, preserve order
        seen: set[str] = set()
        gated_fields = [f for f in gated_fields if not (f in seen or seen.add(f))]

        def _key_dicts(keys) -> str:
            return json.dumps(
                [
                    {
                        "kind": k.kind,
                        "key": k.key,
                        "confidence": k.confidence,
                        "source_access_fact_id": k.source_access_fact_id,
                    }
                    for k in keys
                ],
                sort_keys=True,
            )

        gated_field_keys_json = _key_dicts(list(gated_keys.values()))
        state_field_keys_json = _key_dicts(
            [state_field_keys[key] for key in gated_keys]
        )
```

INSERT 语句加两列(列名与 VALUES 各加两项):

```python
        conn.execute(
            """
            INSERT OR IGNORE INTO gate_seed_candidate (
              bc_unit, function, branch_instruction_id, gate_kind,
              predicate_summary, gated_fields_json, related_objects_json,
              related_access_facts_json, confidence, status,
              gated_field_keys_json, state_field_keys_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                br["bc_unit"], br["function"], br["branch_instruction_id"], "premise",
                predicate_summary,
                json.dumps(gated_fields, sort_keys=True),
                json.dumps(related_objects, sort_keys=True),
                json.dumps(related_access_facts, sort_keys=True),
                "low", "static_skeleton_awaiting_llm_predicate",
                gated_field_keys_json, state_field_keys_json,
            ),
        )
```

注:`predicate_summary` 仍可用 `gated_fields`;若某门控只有 numeric 键(v1 不会发生),`gated_fields` 可能为空——v1 中 gated_keys 非空即含 symbolic,故 `gated_fields` 非空,保持原摘要逻辑不变。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_gate_seeds.py -v`
Expected: PASS(原 3 + 新 1 = 4 passed)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/evidence/gates.py tests/test_gate_seeds.py
git commit -F <commit-msg-file>   # "feat(evidence): gate_seed_candidate carries field keys (compat-extended)"
```

---

### Task 3: 反查派生 (reverse_lookup.py)

**Files:**
- Create: `src/implicitfuzz/evidence/reverse_lookup.py`
- Test: `tests/test_reverse_lookup.py`

**Interfaces:**
- Consumes: `field_key_for_access`(Task 1);`gate_seed_candidate.gated_field_keys_json`(Task 2)。
- Produces:
  - `create_reverse_lookup_table(conn)`
  - `derive_gate_prior_write_candidates(conn) -> int`
  - `summarize_reverse_lookup(conn) -> dict[str, int]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_reverse_lookup.py
import json
import sqlite3

from implicitfuzz.evidence.gates import derive_gate_seed_candidates
from implicitfuzz.evidence.reverse_lookup import (
    derive_gate_prior_write_candidates,
    summarize_reverse_lookup,
)

_SCHEMA = """
CREATE TABLE access_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  semantic_op TEXT NOT NULL,
  access_path_symbolic TEXT,
  base_object_json TEXT,
  instruction_id TEXT,
  field_type TEXT,
  numeric_kind TEXT,
  access_path_numeric TEXT,
  confidence TEXT,
  source_location_json TEXT
);
CREATE TABLE branch_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  branch_instruction_id TEXT NOT NULL,
  condition_value TEXT,
  related_loads_json TEXT NOT NULL
);
CREATE TABLE entry_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bc_unit TEXT NOT NULL,
  function TEXT NOT NULL,
  entry_symbol TEXT NOT NULL,
  dispatch_index INTEGER,
  dispatch_role TEXT
);
"""


def _acc(conn, *, bc, fn, op, sym, iid, loc=None):
    conn.execute(
        "INSERT INTO access_fact (bc_unit, function, semantic_op, access_path_symbolic, "
        "base_object_json, instruction_id, field_type, numeric_kind, access_path_numeric, "
        "confidence, source_location_json) "
        "VALUES (?, ?, ?, ?, '{\"object_scope\":\"global\",\"value\":\"g\"}', ?, "
        "NULL, 'gep_offsets', NULL, 'medium', ?)",
        (bc, fn, op, sym, iid, loc),
    )


def _cross_tu_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    # write side in rsrc.bc, gated read side in rw.bc -> cross-TU
    _acc(conn, bc="rsrc.bc", fn="io_register", op="write", sym="io_ring_ctx.file_data",
         iid="w1", loc=json.dumps({"spelling": "rsrc.c:120", "expansion": "rsrc.c:120", "inlined_at": []}))
    _acc(conn, bc="rw.bc", fn="io_rw", op="read", sym="io_ring_ctx.file_data", iid="r1")
    conn.execute(
        "INSERT INTO branch_fact (bc_unit, function, branch_instruction_id, condition_value, related_loads_json) "
        "VALUES ('rw.bc', 'io_rw', 'b1', 'icmp', ?)",
        (json.dumps(["r1"]),),
    )
    derive_gate_seed_candidates(conn)
    return conn


def test_reverse_lookup_matches_write_cross_tu():
    conn = _cross_tu_db()
    assert derive_gate_prior_write_candidates(conn) == 1
    row = conn.execute(
        "SELECT field_key, field_key_kind, write_function, write_bc_unit, "
        "write_source_location, basis, confidence, status, entry_attribution_json "
        "FROM gate_prior_write_candidate"
    ).fetchone()
    assert row[0] == "sym:io_ring_ctx.file_data"
    assert row[1] == "symbolic"
    assert row[2] == "io_register"
    assert row[3] == "rsrc.bc"          # cross-TU: differs from gate's rw.bc
    assert row[4] == "rsrc.c:120"       # parsed from source_location_json.expansion
    assert row[5] == "symbolic_field_key"
    assert row[6] == "medium"
    assert row[7] == "awaiting_llm_predicate_and_execution_verification"
    assert row[8] is None               # io_register not an entry_symbol handler -> no opcode


def test_reverse_lookup_no_write_no_row():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    _acc(conn, bc="rw.bc", fn="io_rw", op="read", sym="io_ring_ctx.file_data", iid="r1")
    conn.execute(
        "INSERT INTO branch_fact (bc_unit, function, branch_instruction_id, condition_value, related_loads_json) "
        "VALUES ('rw.bc', 'io_rw', 'b1', 'icmp', ?)",
        (json.dumps(["r1"]),),
    )
    derive_gate_seed_candidates(conn)   # no write anywhere -> no gate seed
    assert derive_gate_prior_write_candidates(conn) == 0


def test_reverse_lookup_attaches_opcode_only_for_handler_write():
    conn = _cross_tu_db()
    # write function IS a registered opcode handler -> attribution attached
    conn.execute(
        "INSERT INTO entry_fact (bc_unit, function, entry_symbol, dispatch_index, dispatch_role) "
        "VALUES ('rsrc.bc', 'io_register', 'io_register', 22, 'issue')"
    )
    derive_gate_prior_write_candidates(conn)
    row = conn.execute(
        "SELECT entry_attribution_json FROM gate_prior_write_candidate"
    ).fetchone()
    attr = json.loads(row[0])
    assert attr[0] == {"entry_symbol": "io_register", "opcode": 22, "role": "issue"}


def test_summary_counts():
    conn = _cross_tu_db()
    derive_gate_prior_write_candidates(conn)
    summ = summarize_reverse_lookup(conn)
    assert summ["gate_prior_write_candidates"] == 1
    assert summ["gates_with_prior_write"] == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_reverse_lookup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'implicitfuzz.evidence.reverse_lookup'`

- [ ] **Step 3: 实现**

```python
# src/implicitfuzz/evidence/reverse_lookup.py
"""Reverse-lookup: gate gated-field -> prior write access_fact (cross-TU).

Implements the "先门控、后反查" step (purpose.md §3.2). For each
gate_seed_candidate gated field key, find every `write` access_fact whose
field key matches -- crossing TU boundaries, since the writing call is
typically in a different .bc than the gated read (e.g. write in rsrc.c,
gated read in rw.c). Each match becomes a prior-write dependency candidate,
left for later LLM predicate inference + execution verification.

Derived table (not schema-bound). v1 matches on symbolic field keys only.
"""

from __future__ import annotations

import json
import sqlite3

from implicitfuzz.evidence.field_key import field_key_for_access

REVERSE_LOOKUP_DDL = """
CREATE TABLE IF NOT EXISTS gate_prior_write_candidate (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gate_seed_id INTEGER NOT NULL,
  field_key TEXT NOT NULL,
  field_key_kind TEXT NOT NULL,
  write_access_fact_id INTEGER NOT NULL,
  write_function TEXT NOT NULL,
  write_bc_unit TEXT NOT NULL,
  write_source_location TEXT,
  write_base_object_json TEXT,
  object_identity_edge_ids_json TEXT,
  entry_attribution_json TEXT,
  basis TEXT NOT NULL,
  confidence TEXT NOT NULL,
  status TEXT NOT NULL,
  UNIQUE(gate_seed_id, field_key, write_access_fact_id)
);
"""

_STATUS = "awaiting_llm_predicate_and_execution_verification"


def create_reverse_lookup_table(conn: sqlite3.Connection) -> None:
    conn.executescript(REVERSE_LOOKUP_DDL)
    conn.commit()


def _source_location(source_location_json: str | None) -> str | None:
    if not source_location_json:
        return None
    try:
        loc = json.loads(source_location_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(loc, dict):
        return loc.get("expansion") or loc.get("spelling")
    return None


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _entry_attribution(conn: sqlite3.Connection, write_function: str) -> str | None:
    """Opcode/role ONLY when the write function is itself a dispatch handler.

    Anti-overclaim: a write buried in a handler's deep callee (function not an
    entry_symbol) gets no opcode -- there is no reliable call-chain evidence.
    """
    if not _has_table(conn, "entry_fact"):
        return None
    rows = conn.execute(
        "SELECT entry_symbol, dispatch_index, dispatch_role FROM entry_fact "
        "WHERE entry_symbol = ?",
        (write_function,),
    ).fetchall()
    if not rows:
        return None
    attrs = [
        {"entry_symbol": r[0], "opcode": r[1], "role": r[2]}
        for r in rows
    ]
    return json.dumps(attrs, sort_keys=True)


def _writes_by_field_key(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    index: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute(
        "SELECT id, function, bc_unit, access_path_symbolic, field_type, "
        "numeric_kind, access_path_numeric, confidence, base_object_json, "
        "source_location_json FROM access_fact WHERE semantic_op = 'write'"
    ).fetchall():
        fk = field_key_for_access(
            access_path_symbolic=row["access_path_symbolic"],
            field_type=row["field_type"],
            numeric_kind=row["numeric_kind"],
            access_path_numeric=row["access_path_numeric"],
            confidence=row["confidence"] or "low",
            source_access_fact_id=row["id"],
        )
        if fk is None:
            continue
        index.setdefault(fk.key, []).append(row)
    return index


def derive_gate_prior_write_candidates(conn: sqlite3.Connection) -> int:
    conn.row_factory = sqlite3.Row
    create_reverse_lookup_table(conn)
    if not _has_table(conn, "gate_seed_candidate"):
        return 0

    writes = _writes_by_field_key(conn)
    if not writes:
        return 0

    inserted = 0
    for gate in conn.execute(
        "SELECT id, gated_field_keys_json FROM gate_seed_candidate ORDER BY id"
    ).fetchall():
        try:
            keys = json.loads(gate["gated_field_keys_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        for k in keys:
            key = k.get("key")
            kind = k.get("kind")
            if not key or key not in writes:
                continue
            basis = "numeric_field_key" if kind == "numeric" else "symbolic_field_key"
            confidence = "low" if kind == "numeric" else "medium"
            for w in writes[key]:
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT OR IGNORE INTO gate_prior_write_candidate (
                      gate_seed_id, field_key, field_key_kind,
                      write_access_fact_id, write_function, write_bc_unit,
                      write_source_location, write_base_object_json,
                      object_identity_edge_ids_json, entry_attribution_json,
                      basis, confidence, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        gate["id"], key, kind,
                        w["id"], w["function"], w["bc_unit"],
                        _source_location(w["source_location_json"]),
                        w["base_object_json"],
                        None,
                        _entry_attribution(conn, w["function"]),
                        basis, confidence, _STATUS,
                    ),
                )
                if conn.total_changes > before:
                    inserted += 1
    conn.commit()
    return inserted


def summarize_reverse_lookup(conn: sqlite3.Connection) -> dict[str, int]:
    conn.row_factory = sqlite3.Row
    total = conn.execute(
        "SELECT COUNT(*) c FROM gate_prior_write_candidate"
    ).fetchone()["c"]
    gates = conn.execute(
        "SELECT COUNT(DISTINCT gate_seed_id) c FROM gate_prior_write_candidate"
    ).fetchone()["c"]
    cross_tu = conn.execute(
        "SELECT COUNT(*) c FROM gate_prior_write_candidate p "
        "JOIN gate_seed_candidate g ON g.id = p.gate_seed_id "
        "WHERE p.write_bc_unit != g.bc_unit"
    ).fetchone()["c"]
    return {
        "gate_prior_write_candidates": total,
        "gates_with_prior_write": gates,
        "cross_tu_prior_writes": cross_tu,
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_reverse_lookup.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: 提交**

```bash
git add src/implicitfuzz/evidence/reverse_lookup.py tests/test_reverse_lookup.py
git commit -F <commit-msg-file>   # "feat(evidence): gate_prior_write_candidate cross-TU reverse-lookup (symbolic v1)"
```

---

### Task 4: 全链路 smoke + 真实合并 DB 验收 + 文档 + 回归

**Files:**
- Create: `extraction/scripts/build_reverse_lookup_smoke.py`
- Modify: `extraction/RUNBOOK.md`(加反查 smoke 入口)、`docs/phase2d-state-gates.md`(加反查环节说明)

**Interfaces:**
- Consumes: `implicitfuzz.ingestion.ingest.ingest_jsonl`、`derive_gate_seed_candidates`、`derive_gate_prior_write_candidates`、`summarize_reverse_lookup`。

- [ ] **Step 1: 写 smoke 脚本**

```python
#!/usr/bin/env python3
"""Reverse-lookup smoke: ingest multiple TU facts -> gate seeds -> reverse lookup.

Demonstrates cross-TU gate->prior-write reverse lookup on a merged DB
(e.g. io_uring rw.c gated read + rsrc.c prior write).
"""
import argparse
import sqlite3
from pathlib import Path

from implicitfuzz.ingestion.ingest import ingest_jsonl
from implicitfuzz.evidence.gates import derive_gate_seed_candidates
from implicitfuzz.evidence.reverse_lookup import (
    derive_gate_prior_write_candidates,
    summarize_reverse_lookup,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", action="append", required=True,
                    help="one or more JSONL fact files (repeat)")
    ap.add_argument("--db", default="/tmp/reverse_lookup_smoke.db")
    args = ap.parse_args()

    db = args.db
    Path(db).unlink(missing_ok=True)
    for facts in args.facts:
        counts = ingest_jsonl(facts, db)
        print(f"ingested {facts}: {counts}")

    conn = sqlite3.connect(db)
    seeds = derive_gate_seed_candidates(conn)
    rows = derive_gate_prior_write_candidates(conn)
    summ = summarize_reverse_lookup(conn)
    print(f"gate_seed_candidates derived: {seeds}")
    print(f"gate_prior_write_candidate rows: {rows}")
    print(f"summary: {summ}")

    # show cross-TU prior writes for human inspection
    conn.row_factory = sqlite3.Row
    for r in conn.execute(
        "SELECT p.field_key, p.write_function, p.write_bc_unit, g.bc_unit AS gate_bc, "
        "p.write_source_location FROM gate_prior_write_candidate p "
        "JOIN gate_seed_candidate g ON g.id = p.gate_seed_id "
        "WHERE p.write_bc_unit != g.bc_unit LIMIT 20"
    ).fetchall():
        print(f"  CROSS-TU {r['field_key']}  write {r['write_function']}@{r['write_bc_unit']} "
              f"({r['write_source_location']})  gated-in {r['gate_bc']}")
    conn.close()

    assert summ["cross_tu_prior_writes"] >= 1, "expected >=1 cross-TU prior write"
    print("reverse-lookup smoke OK:", db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 服务器上生成 rsrc + rw facts(若尚无)**

在服务器 `/home/xujunru/ImplicitFuzz` 用抽取器生成两 TU 的 facts(env LD 路径见下;`rw` bitcode 取 `/tmp/implicitfuzz-io_uring-all/bc/io_uring_rw.bc`,`rsrc` 取 `/tmp/io_uring_rsrc.bc`):

```bash
ROOT=/home/xujunru/ImplicitFuzz; EX=$ROOT/extraction; BUILD=$EX/build
LLVM_LIB=/home/xujunru/.local/opt/llvm21-rpm/usr/lib64
LLVM21_LIB=$LLVM_LIB/llvm21/lib64
SVF_BUILD=/home/xujunru/implicitfuzz-toolchain/SVF/verify-llvm21-build4
Z3_LIB=/home/xujunru/implicitfuzz-toolchain/z3-install/lib64
RUN_LD=$LLVM_LIB:$LLVM21_LIB:$Z3_LIB:$SVF_BUILD/lib
for name in rsrc rw; do
  bc=/tmp/io_uring_${name}.bc
  [ -f "$bc" ] || bc=/tmp/implicitfuzz-io_uring-all/bc/io_uring_${name}.bc
  env -u LD_LIBRARY_PATH LD_LIBRARY_PATH="$RUN_LD" \
    "$BUILD/implicitfuzz-extract" -jsonl-out /tmp/io_uring_${name}.facts.jsonl \
    -primitive-summary "$EX/schema/primitive_summary.json" "$bc"
done
```

- [ ] **Step 3: 运行 smoke,确认跨 TU 命中**

Run:
```bash
python3 extraction/scripts/build_reverse_lookup_smoke.py \
  --facts /tmp/io_uring_rsrc.facts.jsonl --facts /tmp/io_uring_rw.facts.jsonl
```
Expected tail: `reverse-lookup smoke OK: /tmp/reverse_lookup_smoke.db`,且至少一行 `CROSS-TU ...`(理想含 `io_ring_ctx.file_data`)。**若无跨 TU 命中**:打印全部 `gate_prior_write_candidate` 排查——记录哪些符号字段既被门控又在另一 TU 有写;据此在文档里确定实际展示锚点(spec §4 允许锚点实测确定)。

- [ ] **Step 4: 文档更新**

在 `extraction/RUNBOOK.md` 的 Entry Points 表加一行:

```markdown
| `extraction/scripts/build_reverse_lookup_smoke.py` | gate→prior-write 跨 TU 反查 smoke | No(需多 TU facts JSONL) |
```

在 `docs/phase2d-state-gates.md` 末尾加一节,说明 `gate_prior_write_candidate` 是"先门控、后反查"的产物、跨 TU、v1 纯符号、status 待 LLM+执行验证(引用 spec)。

- [ ] **Step 5: 全回归**

Run:
```bash
extraction/scripts/run_phase1_regression.sh
python3 -m pytest tests/
```
Expected: `[phase1] ALL PASS`;pytest `23 + 9 新增 = 32 passed`(Task1 5 + Task2 1 + Task3 4 -减去 gate 既有;实际以 collected 数为准,全绿即可)。

- [ ] **Step 6: 提交**

```bash
git add extraction/scripts/build_reverse_lookup_smoke.py extraction/RUNBOOK.md docs/phase2d-state-gates.md
git commit -F <commit-msg-file>   # "feat(evidence): reverse-lookup smoke + cross-TU validation + docs"
```

---

## Self-Review

- **Spec 覆盖**:组件1→Task1;组件2→Task2;组件3→Task3;组件4→Task3 的 `_entry_attribution`(防 overclaim:仅 entry_symbol 精确匹配);跨 TU+验收→Task4;numeric 三规则→Task1(canonicalize + lower_confidence + basis 暴露,v1 返回 None);兼容式扩列→Task2(旧列不变)。✅
- **占位符**:无 TBD/TODO;每步含完整代码或精确命令。✅
- **类型一致**:`field_key_for_access` / `FieldKey` / `canonicalize_struct_type` / `lower_confidence` 跨 Task1/2/3 命名一致;列名(`gated_field_keys_json`、`gate_prior_write_candidate`、`entry_symbol`/`dispatch_index`/`dispatch_role`)与 ingest DDL 一致。✅
- **已知开放点**:Task4 展示锚点若 `file_data` 未跨 TU 命中,则实测选定并记文档(spec §4 已授权);不阻塞机制落地。
