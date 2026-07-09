# Opcode Entry Attribution + Branch/Gate Facts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline, in this session). Steps use checkbox (`- [ ]`) syntax.

**Goal:** (Part 1) Populate `entry_fact` by statically reading io_uring's `io_op_defs` const dispatch table → opcode→handler attribution, the prerequisite for syscall-centric evidence. (Part 2) Emit `branch_fact` for object-field-gated conditional branches and derive `gate_seed_fact` skeletons — the static half of the state-gate view (LLM fills predicate semantics later).

**Architecture:** Both are additive to the existing per-TU SVF extractor (`extraction/src/implicitfuzz-extract.cpp`, LLVM-21/SVF) and its SQLite ingestion. Part 1 reads a constant aggregate initializer (no pointer analysis). Part 2 adds `BranchInst`/`SwitchInst` handling + a bounded backward slice over the condition, reusing `buildAccessPathInfo` to tie branches to object fields; `gate_seed_fact` is a Python derivation over the evidence DB (like the existing evidence graph), keeping the extractor per-TU and letting SQL do the joining.

**Tech Stack:** LLVM 21.1.8, SVF-on-LLVM-21, C++17 extractor; Python 3.9 ingestion/derivation; JSON Schema (draft 2020-12) facts; SQLite.

## Global Constraints

- LLVM: **21.1.8**; kernel: **linux-6.1**; opt level: **-O2 -g**; SVF commit `9e04f986`. (Copy verbatim into any new manifest.)
- `schema_version` MUST remain `"1.0.0"`; schema changes are **additive only** (new optional fields / new enum values), never remove or tighten existing required fields.
- MUST NOT break existing regression: `extraction/scripts/run_phase1_regression.sh` (tiny + kernel case1/2/3 + BTF), `python3 -m pytest tests/` (17 tests), `ingest_phase1_smoke.py`, `build_evidence_graph_smoke.py`.
- Build via the wrapper scripts (they set the LLVM/SVF `LD_LIBRARY_PATH`); do not invoke `clang++`/the binary bare (the toolchain `.so`s are not on the default path).
- All work on remote server `/home/xujunru/ImplicitFuzz` (edit locally, `scp` up, build+run on server). Branch: `phase2b-evidence-identity`.

---

## Part 1 — Const dispatch-table reader → `entry_fact`

### Task 1.1: Extend `entry_fact` schema (additive)

**Files:**
- Modify: `extraction/schema/facts/entry_fact.schema.json`
- Modify: `src/implicitfuzz/ingestion/schema.sql` (entry_fact table)

**Interfaces:**
- Produces: `entry_fact` records may now carry `dispatch_table` (string|null), `dispatch_index` (integer|null), `dispatch_role` (string|null); `entry_kind` enum gains `"op_dispatch"`.

- [ ] **Step 1: Write the failing test** — a schema-validity check for a sample op_dispatch entry_fact.

Create `tests/test_entry_fact_schema.py`:

```python
import json
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "extraction" / "schema" / "facts"


def _validator():
    resources = []
    for p in sorted(SCHEMA_DIR.glob("*.schema.json")):
        s = json.loads(p.read_text())
        resources.append((s.get("$id") or p.as_uri(), Resource.from_contents(s)))
    registry = Registry().with_resources(resources)
    common = json.loads((SCHEMA_DIR / "common.schema.json").read_text())
    entry = json.loads((SCHEMA_DIR / "entry_fact.schema.json").read_text())
    merged = {**common, **entry}
    merged["properties"] = {**common["properties"], **entry.get("properties", {})}
    merged["required"] = list(set(common["required"]) | set(entry.get("required", [])))
    merged.pop("allOf", None)
    return jsonschema.Draft202012Validator(merged, registry=registry)


def test_op_dispatch_entry_fact_validates():
    rec = {
        "fact_type": "entry_fact", "schema_version": "1.0.0",
        "kernel_version": "linux-6.1", "llvm_version": "21.1.8", "opt_level": "-O2 -g",
        "bc_unit": "opdef.bc", "function": "io_op_defs",
        "entry_kind": "op_dispatch", "entry_symbol": "io_read",
        "associated_syscall": "READV", "dispatch_table": "io_op_defs",
        "dispatch_index": 1, "dispatch_role": "issue",
        "primary_provenance": "dwarf", "provenance": ["dwarf"], "confidence": "high",
    }
    _validator().validate(rec)  # must not raise
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd /home/xujunru/ImplicitFuzz && python3 -m pytest tests/test_entry_fact_schema.py -v`
Expected: FAIL — `dispatch_role`/`op_dispatch` rejected (enum + additionalProperties or the fields simply not in schema; jsonschema is permissive on unknown props by default, so the *real* failing assertion is `entry_kind: "op_dispatch"` not in enum).

- [ ] **Step 3: Edit `entry_fact.schema.json`** — add enum value + optional fields.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://implicitfuzz.local/schema/facts/entry_fact.schema.json",
  "allOf": [{ "$ref": "common.schema.json" }],
  "properties": {
    "entry_kind": {
      "type": "string",
      "enum": ["syscall", "file_op", "callback", "workqueue", "ioctl", "uring_cmd", "task_work", "io_worker", "sqpoll_thread", "completion_path", "timeout_callback", "cancel_path", "op_dispatch"]
    },
    "entry_symbol": { "type": "string" },
    "associated_syscall": { "type": ["string", "null"] },
    "dispatch_table": { "type": ["string", "null"], "description": "const 分发表全局名,如 io_op_defs" },
    "dispatch_index": { "type": ["integer", "null"], "description": "表内下标 = opcode" },
    "dispatch_role": { "type": ["string", "null"], "description": "handler 角色(DWARF 成员名),如 issue/prep/cleanup" }
  },
  "required": ["entry_kind", "entry_symbol"]
}
```

- [ ] **Step 4: Add columns to `schema.sql` entry_fact table** (persist the new fields; `_row_for` drops keys not in table_columns, so columns are required to persist).

Add after `associated_syscall TEXT,`:
```sql
  dispatch_table TEXT,
  dispatch_index INTEGER,
  dispatch_role TEXT,
```

- [ ] **Step 5: Run test, verify pass**

Run: `python3 -m pytest tests/test_entry_fact_schema.py -v` → PASS.

- [ ] **Step 6: Commit** — `git add extraction/schema/facts/entry_fact.schema.json src/implicitfuzz/ingestion/schema.sql tests/test_entry_fact_schema.py && git commit`.

### Task 1.2: Extractor — read const dispatch tables → emit `entry_fact`

**Files:**
- Modify: `extraction/src/implicitfuzz-extract.cpp`
- Modify: `extraction/scripts/check_golden_facts.py` (extended for opdef case, OR a dedicated case — see Task 1.3)

**Interfaces:**
- Consumes: `DwarfStructIndex` (existing) — extend with `memberNameAt(structName, fieldIndex)`.
- Produces: `entry_fact` JSONL lines for each `(dispatchTableGlobal, elementIndex, functionPtrField)` with a non-null `Function*` target.

- [ ] **Step 1: Add `writeEntryFact` helper** (mirror `writeCallFact` envelope style) near `writeCallFact`:

```cpp
static void writeEntryFact(std::ofstream& out, const RunMetadata& runMeta,
                           const std::string& tableName, const Instruction* /*unused*/,
                           const std::string& entrySymbol, const std::string& role,
                           long long index, const std::string& opcodeName)
{
    // entry_fact has no instruction anchor; use the table global as "function".
    const SchemaConfidence confidence = schemaConfidenceFromScore(0.95);
    const std::vector<std::string> provenance = {"dwarf"};
    out << "{"
        << "\"fact_type\":\"entry_fact\","
        // commonEnvelopeJson needs an Instruction for source loc; emit a null-loc envelope variant.
        << commonEnvelopeNoInstJson(runMeta, tableName, "dwarf", provenance, confidence) << ","
        << "\"entry_kind\":\"op_dispatch\","
        << "\"entry_symbol\":\"" << jsonEscape(entrySymbol) << "\","
        << "\"associated_syscall\":" << (opcodeName.empty() ? "null" : "\"" + jsonEscape(opcodeName) + "\"") << ","
        << "\"dispatch_table\":\"" << jsonEscape(tableName) << "\","
        << "\"dispatch_index\":" << index << ","
        << "\"dispatch_role\":\"" << jsonEscape(role) << "\""
        << "}\n";
}
```

Note: `commonEnvelopeJson` currently takes an `Instruction&` for source location. Add a sibling `commonEnvelopeNoInstJson(runMeta, function, primaryProvenance, provenance, confidence)` that emits `"source_location":null` (schema allows null). Keep the existing one unchanged.

- [ ] **Step 2: Add DWARF member-name-by-index lookup.** In `DwarfStructIndex`, add:

```cpp
// Map a struct's field index -> DWARF member name (for labeling constant-aggregate fields).
std::optional<std::string> memberNameAt(const std::string& structName, unsigned fieldIndex) const;
```

Implement by indexing `DICompositeType` members per struct base name (the class already walks DWARF composite types for GEP symbolization; store `structName -> vector<memberName>` ordered by `DW_TAG_member` offset/index).

- [ ] **Step 3: Add the dispatch-table scan** in `main()`, after DWARF indexing, before/after the function loop (globals, not per-function):

```cpp
static const Function* asFunctionTarget(const Constant* c) {
    c = c->stripPointerCasts();
    return SVFUtil::dyn_cast<Function>(c);
}

static void scanDispatchTables(std::ofstream& out, const RunMetadata& runMeta,
                               Module& mod, const DwarfStructIndex& dwarfIndex,
                               uint64_t& entryFactCount)
{
    for (GlobalVariable& gv : mod.globals()) {
        if (!gv.isConstant() || !gv.hasInitializer()) continue;
        const Constant* init = gv.getInitializer();
        const auto* arr = SVFUtil::dyn_cast<ConstantArray>(init);
        if (!arr) continue;                       // dispatch tables are arrays of structs
        const auto* elemStruct = SVFUtil::dyn_cast<StructType>(arr->getType()->getElementType());
        if (!elemStruct) continue;
        // require >=1 function-pointer-typed field with a real Function target somewhere
        const std::string tableName = gv.getName().str();
        const std::string structName = structTypeBaseName(elemStruct);  // existing helper
        for (unsigned e = 0; e < arr->getNumOperands(); ++e) {
            const auto* elem = SVFUtil::dyn_cast<ConstantStruct>(arr->getOperand(e));
            if (!elem) continue;
            std::string opcodeName;               // best-effort: a C-string field named "name"
            for (unsigned f = 0; f < elem->getNumOperands(); ++f) {
                if (const Function* fn = asFunctionTarget(elem->getOperand(f))) {
                    std::string role = dwarfIndex.memberNameAt(structName, f).value_or("field" + std::to_string(f));
                    writeEntryFact(out, runMeta, tableName, nullptr, fn->getName().str(),
                                   role, (long long)e, opcodeName);
                    ++entryFactCount;
                }
            }
        }
    }
}
```

Call `scanDispatchTables(...)` once per module in `main()`; add `uint64_t entryFactCount = 0;` and a summary print `"entry_fact (const dispatch table) hits: N"`.

- [ ] **Step 4: Build + smoke on opdef.c**

Run (via the case5 script from Task 1.3, or manually with the run-kernel env): extract `io_uring_opdef.bc`. Expected log: `entry_fact (const dispatch table) hits: >= 49` (49 opcodes × ≥1 non-null fnptr each; realistically ~120 across prep/issue/cleanup/fail).

- [ ] **Step 5: Verify a specific mapping** with a one-liner over the JSONL:
`io_op_defs` index 1 role `issue` → `io_read`; index 1 role `prep` → `io_prep_rw`; index 0 role `issue` → `io_nop`.

- [ ] **Step 6: Commit.**

### Task 1.3: Golden case5 (opdef.c const table) + regression wiring

**Files:**
- Create: `extraction/scripts/run_kernel_case5.sh` (mirror `run_kernel_case4`… none exists; mirror `run_kernel_case3.sh`)
- Create: `extraction/golden/kernel_opdef_dispatch_case5/ground_truth.json`
- Create: `extraction/scripts/check_kernel_opdef_case5.py`
- Modify: `extraction/scripts/run_phase1_regression.sh` (add case5 line)
- Modify: `extraction/RUNBOOK.md`, `docs/phase1-findings.md`

**Interfaces:**
- Consumes: prebuilt `/tmp/implicitfuzz-io_uring-all/bc/io_uring_opdef.bc` (already exists) or regenerate per manifest.

- [ ] **Step 1: Write `check_kernel_opdef_case5.py`** asserting: ≥49 distinct `dispatch_index`; `(index=0, role=issue)→io_nop`; `(index=1, role=issue)→io_read`; `(index=2, role=issue)→io_write`; schema-valid.
- [ ] **Step 2: Run it against the case5 extractor output, verify FAIL first** (extractor not yet run / file absent), then wire `run_kernel_case5.sh` to extract + validate + check.
- [ ] **Step 3: Add `ground_truth.json`** capturing the asserted mappings.
- [ ] **Step 4: Add case5 to `run_phase1_regression.sh`**, run full regression → ALL PASS.
- [ ] **Step 5: Update docs** (findings + RUNBOOK: new case5 line, entry_fact now populated).
- [ ] **Step 6: Commit.**

### Task 1.4: Ingestion + evidence-DB join (opcode attribution usable)

**Files:**
- Modify: `extraction/scripts/ingest_phase1_smoke.py` (include opdef facts; assert entry_fact rows)
- (Optional) Modify: `src/implicitfuzz/evidence/graph.py` — a query/helper `attribute_dispatch_callsites(conn)` that joins io_uring.c's unresolved indirect dispatch `call_fact` to `entry_fact.dispatch_table` candidate sets (leaves per-TU extraction untouched; the join lives in the DB layer per design §"声明式查询").

**Interfaces:**
- Produces: `entry_fact` rows in SQLite; a query that, given an unresolved `is_indirect` dispatch call site, returns the candidate handler set from `entry_fact` where `dispatch_table='io_op_defs'`.

- [ ] **Step 1: Write pytest** in `tests/test_ingestion.py` (or new file): ingest a 3-row entry_fact JSONL, assert rows land with `dispatch_index`/`dispatch_role` columns populated.
- [ ] **Step 2: Verify FAIL** (columns/handling), then confirm ingestion works after Task 1.1's schema.sql columns (should already pass — this test guards it).
- [ ] **Step 3: (Optional, if time) add `attribute_dispatch_callsites`** with a unit test over an in-memory DB (call_fact unresolved dispatch + entry_fact candidates → joined candidate set). Skip if deferring the join to Part 2's consumer.
- [ ] **Step 4: Commit.**

---

## Part 2 — `branch_fact` + `gate_seed_fact` skeleton

### Task 2.1: Extractor — `BranchInst`/`SwitchInst` → `branch_fact`

**Files:**
- Modify: `extraction/src/implicitfuzz-extract.cpp`
- Modify: `extraction/testdata/input.c` (add a field-gated branch for the tiny golden) + `check_golden_facts.py`

**Interfaces:**
- Consumes: `buildAccessPathInfo` (existing) to resolve a related load's field/base_object.
- Produces: `branch_fact` with `branch_instruction_id`, `condition_value` (string render), `control_deps` (successor BB labels), `related_loads` (instruction_ids of LoadInsts feeding the condition), `slice_range`.

- [ ] **Step 1: Tiny golden — add an unambiguous field-gated branch.** In `input.c`, add to `accumulate` or a new fn:

```c
static int gated(struct Node *n) {
    if (n->flags != 0)      // branch gated on Node.flags
        return n->value;
    return 0;
}
```
call it once from `main` (`sum += gated(b);`).

- [ ] **Step 2: Write the failing golden assertion** in `check_golden_facts.py`: there exists a `branch_fact` in function `gated` whose `related_loads` includes an access resolving to symbolic `Node.flags`. (Cross-reference branch_fact.related_loads → access_fact by instruction_id.)

- [ ] **Step 3: Run golden, verify FAIL** (no branch_fact emitted yet).

- [ ] **Step 4: Implement `writeBranchFact` + emission.** In the instruction loop, after the call/load/store handling:

```cpp
if (const BranchInst* br = SVFUtil::dyn_cast<BranchInst>(&inst)) {
    if (!br->isConditional()) { /* skip */ }
    else emitBranchFact(out, runMeta, func, inst, ordinal, br->getCondition(),
                        successorLabels(br), dwarfIndex, primitiveIndex, wrapperIndex,
                        ander, pag, llvmMS, branchFactCount);
} else if (const SwitchInst* sw = SVFUtil::dyn_cast<SwitchInst>(&inst)) {
    emitBranchFact(out, runMeta, func, inst, ordinal, sw->getCondition(),
                   successorLabels(sw), ...);
}
```

`emitBranchFact` computes a **bounded backward slice** from the condition to collect feeding `LoadInst`s (reuse the `kPointerTraceMaxDepth`-style bounded DFS convention already in the file):

```cpp
// Collect LoadInsts reachable backward from `cond` within depth K, over data operands.
static void collectConditionLoads(const Value* cond, unsigned depth,
                                  std::unordered_set<const Value*>& seen,
                                  std::vector<const LoadInst*>& loads) {
    if (depth == 0 || !seen.insert(cond).second) return;
    if (const auto* li = SVFUtil::dyn_cast<LoadInst>(cond)) { loads.push_back(li); return; }
    if (const auto* op = SVFUtil::dyn_cast<Instruction>(cond))
        for (const Use& u : op->operands())
            collectConditionLoads(u.get(), depth - 1, seen, loads);
}
```

For each collected load, its `instruction_id` (compute via the same ordinal scheme — NOTE: ordinals are per-function-position; to reference the load's id we must have recorded it. Simplest: recompute `instructionId` from the load's own enclosing position by a pre-pass map `Instruction* -> instruction_id` built in the function loop, OR store the load's pointer-address-based id which matches how access_fact ids are formed). Emit `related_loads` = those ids. `condition_value` = `llvmValueToString(cond)` (truncated). `control_deps` = successor block labels. `slice_range` = `expansion(firstLoad)..expansion(branch)`.

- [ ] **Step 5: Run golden, verify PASS** (branch_fact in `gated`, related_loads → Node.flags).

- [ ] **Step 6: Schema-validate** tiny + kernel outputs (`validate_facts_schema.py` auto-includes branch_fact). Run full `run_phase1_regression.sh` → ALL PASS.

- [ ] **Step 7: Commit.**

### Task 2.2: Derive `gate_seed_fact` (Python, over the evidence DB)

**Files:**
- Create: `src/implicitfuzz/evidence/gates.py`
- Modify: `extraction/scripts/build_evidence_graph_smoke.py` (also derive gate seeds)
- Create/modify: `tests/test_gate_seeds.py`

**Interfaces:**
- Consumes: ingested `branch_fact` (related_loads_json), `access_fact` (instruction_id, access_path_symbolic, base_object_json, semantic_op), and `state_write_read_candidate` edges (a gated field is "state" if it is also written somewhere = has a write access_fact).
- Produces: `gate_seed_fact` rows: `target_symbol` (the branch's function), `gate_kind` (`"premise"` default), `predicate_summary` (static render, e.g. `"branch on {Node.flags} (icmp ne ...)"`), `related_objects` (base_objects of the gated fields), `related_access_facts` (the field-load instruction_ids), plus provenance/confidence marking these as **static skeletons awaiting LLM predicate inference**.

- [ ] **Step 1: Write pytest** — in-memory DB with one branch_fact whose related_load resolves to a field that is ALSO written elsewhere → `derive_gate_seeds` emits one gate_seed_fact with that field in `related_access_facts` and its base_object in `related_objects`; a branch on a never-written (read-only/const) field → no gate seed (not a state gate).

- [ ] **Step 2: Verify FAIL** (function missing).

- [ ] **Step 3: Implement `derive_gate_seeds(conn)`** — join branch_fact.related_loads → access_fact; keep only fields that have ≥1 `semantic_op='write'` access somewhere (state field, i.e. participates in write→read coupling); emit gate_seed_fact skeleton. Write results as `gate_seed_fact` rows (ingest via the same schema path, or insert directly — match how the evidence graph persists).

- [ ] **Step 4: Verify PASS.**

- [ ] **Step 5: Wire into `build_evidence_graph_smoke.py`**; run smoke on the 4-TU DB, report gate_seed count. Manually sanity-check one seed (e.g. a `req->flags`/`ctx->file_data`-gated branch).

- [ ] **Step 6: Run full regression + pytest → ALL PASS. Commit.**

### Task 2.3: Docs

- [ ] Update `docs/phase1-findings.md` Next Steps (mark branch/gate done), add a `docs/phase2d-state-gates.md` describing the static skeleton + the LLM-fills-predicate boundary (cross-ref `docs/purpose.md` 研究内容一 and the READ_FIXED example). Commit.

---

## Self-Review notes

- **Spec coverage:** Part 1 ⇒ 访问证据库的"入口元信息表" + opcode 归属; Part 2 ⇒ 对象状态机视图的静态骨架(门控锚点),LLM 谓词推断留待后续。Both are 研究内容一.
- **Non-obvious risks flagged in-task:** (a) `commonEnvelopeJson` needs an Instruction — added a null-loc variant for entry_fact. (b) branch_fact `related_loads` referencing load instruction_ids requires a stable `Instruction*→id` map within the function pass (noted in Task 2.1 Step 4). (c) generic dispatch-table detection could match unintended const arrays — golden asserts specifically on `io_op_defs`; if noise appears on other TUs, tighten the "≥1 fnptr field with a Function target" criterion.
- **Type consistency:** `dispatch_index` integer everywhere; `dispatch_role` = DWARF member name string; entry_kind `"op_dispatch"` consistent across schema/extractor/golden.
