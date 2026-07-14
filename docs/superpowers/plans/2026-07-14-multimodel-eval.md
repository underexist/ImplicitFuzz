# 多模型冷判读评估 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用跨厂商判读(DeepSeek v4-flash/v4-pro API + 复用 Claude Opus 基线)在冻结的 13 门控冷判读集上量化 robustness:独立架构是否收敛到相同谓词项、确定性守卫是否模型无关地在负控触发。

**Architecture:** 复用 `context.build_bundle`/`validate.py`/`eval.py`;新增纯 Python `judge_adapter.py`(model-agnostic 判读调用 + 容错 JSON 解析,poster 注入以便单测)与 `multimodel_eval.py`(纯聚合函数);两个 CLI 脚本 `build_bundles.py`(server)与 `run_multimodel.py`(Mac judge / server score)。跨机:server 建 bundle → Mac 跑 API 判读(无 tools,结构性去污染)→ server validate+score。

**Tech Stack:** Python ≥3.8 + pytest + stdlib urllib(不引 requests);DeepSeek OpenAI-compatible API;facts DB(server)。

## Global Constraints

- **名单:** deepseek-v4-flash + deepseek-v4-pro(新跑,API)+ 复用 `pilot/runs_formal/*.json`(Claude Opus 基线;先前会话 confound,报告标注)。
- **跨机:** ① server `build_bundles`(有 DB+源,无网)→ `pilot/bundles/*.json`;② Mac `run_multimodel judge`(有网,无 DB,**无 tools**)→ `pilot/runs_multimodel/<model>/*.json`;③ server `run_multimodel score`(有 DB,field-existence 守卫需之)→ 聚合 → 报告。
- **RED LINE:** bundle 永不含 GT(`build_bundle` 保证;`labels/` 独立);评分在判读之后由 harness 做。
- **去污染:** DeepSeek 判读无 tools,仅得 bundle 文本 → 结构性成立(强于 subagent 沙箱)。
- **凭据:** 读 `~/.config/implicitfuzz/llm.env`(`DEEPSEEK_API_KEY`/`DEEPSEEK_BASE_URL=https://api.deepseek.com`),**永不入 git**;仓库无密钥。
- **指标(逐模型报告,不 pooling):** 10 正样本 exact+relaxed P/R;3 负控守卫触发一致性;模型间 per-term Jaccard;弃权率;schema/parse 合规率。人工语义评分仍主判据。
- **诚实:** n=13/curated 不变(只攻单模型轴);baseline-reuse confound + 无法字节比对历史 bundle,报告标注;跨厂商收敛=定性 robustness 信号,非统计。
- **13 门控:** 正 10 = `read_fixed_file, fixed_buffer, cancel_fixed_file, msg_ring_fixed_file, filetable_slot, net_fixed_buffer, poll_polled, poll_double, link_timeout, kiocb_flags`;负控 3 = `neg_writer_omitted, neg_thin_slice, neg_adversarial`。
- 单测纯 Python 无网无 DB(mock poster + 内存 fixture);本地快跑 `PYTHONPATH=src <venv>/python -m pytest <f> -o addopts="" -q`。收口 `run_phase1_regression.sh` ALL PASS + pytest 全绿。三边同步见 [[feedback-three-way-sync]]。

参考 spec:`docs/superpowers/specs/2026-07-14-multimodel-eval-design.md`。判读输出/GT/label 同构:`{target_gate, terms:[{class,object,field_ref,relation,align_target,source,confidence,uncertain}], abstain, alt_candidates}`。复用签名:`validate_schema(pred)->bool`、`validate_field_existence(conn,pred)->{passed,per_term}`、`validate_synthesizability(pred)->[{class,synthesizable}]`、`score_terms(jt,tt)->{precision,recall,matched,n_judge,n_truth}`、`score_terms_relaxed(jt,tt)->{class_field,field_set}`、`_term_key(t)=(class,field_ref,align_target.strip())`、`build_bundle(conn,source_root,gate,radius=15)->{target_gate,field_clues,code_slices,ledger,instructions,predicate_schema}`。

---

### Task 1: `judge_adapter.py` —— model-agnostic 判读调用 + 容错解析

**Files:** Create `src/implicitfuzz/pilot/judge_adapter.py`;Test `tests/test_judge_adapter.py`

**Interfaces:**
- Produces:
  - `bundle_to_prompt(bundle: dict) -> str`
  - `parse_predicate(raw: str) -> dict`(含 `_parse_ok`;失败 terms=[]、`_raw` 保留)
  - `run_judge(model, bundle, *, base_url=None, api_key=None, poster=_urllib_poster) -> dict`(注入 poster 以便单测)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_judge_adapter.py
from implicitfuzz.pilot.judge_adapter import bundle_to_prompt, parse_predicate, run_judge

BUNDLE = {
    "target_gate": {"function": "io_prep_rw"}, "field_clues": ["buf_index"],
    "code_slices": [{"label": "gate", "file_line": "rw.c:91", "text": "91: if (x)"}],
    "ledger": [{"function": "io_prep_rw", "semantic_op": "read"}],
    "instructions": "You are a constrained judge. Output JSON.",
    "predicate_schema": {"type": "object"},
}


def test_bundle_to_prompt_is_deterministic_and_complete():
    p = bundle_to_prompt(BUNDLE)
    assert "constrained judge" in p and "io_prep_rw" in p and "rw.c:91" in p
    assert "LEDGER" in p and "PREDICATE_SCHEMA" in p
    assert bundle_to_prompt(BUNDLE) == p  # deterministic


def test_parse_predicate_fenced():
    d = parse_predicate('prose\n```json\n{"terms": [{"class": "premise"}], "abstain": false}\n```\nmore')
    assert d["_parse_ok"] and len(d["terms"]) == 1


def test_parse_predicate_bare_and_abstain():
    d = parse_predicate('{"terms": [], "abstain": true}')
    assert d["_parse_ok"] and d["abstain"] is True


def test_parse_predicate_garbage():
    d = parse_predicate("no json here")
    assert d["_parse_ok"] is False and d["terms"] == []


def test_run_judge_uses_injected_poster_no_network():
    seen = {}
    def stub(url, headers, body):
        seen["url"] = url; seen["model"] = body["model"]
        return {"model": "deepseek-v4-flash",
                "choices": [{"message": {"content": '{"terms": [{"class": "activation"}]}'}}],
                "usage": {"total_tokens": 42}}
    out = run_judge("deepseek-v4-flash", BUNDLE, base_url="https://x", api_key="k", poster=stub)
    assert seen["url"].endswith("/chat/completions") and seen["model"] == "deepseek-v4-flash"
    assert out["_parse_ok"] and out["_model"] == "deepseek-v4-flash" and out["_usage"]["total_tokens"] == 42
```

- [ ] **Step 2: 运行确认失败** — `PYTHONPATH=src python3 -m pytest tests/test_judge_adapter.py -o addopts="" -q` → `ModuleNotFoundError`

- [ ] **Step 3: 实现** `src/implicitfuzz/pilot/judge_adapter.py`:
```python
"""Model-agnostic cold-judge caller for the multi-model eval. Serializes a
decontaminated bundle to one prompt, POSTs to an OpenAI-compatible endpoint
(DeepSeek) with NO tools (structural decontamination: the judge cannot reach the
repo, DB, or ground truth), and tolerantly parses the predicate JSON.
Credentials come from the environment; never hard-coded."""

from __future__ import annotations
import json, os, re, urllib.request


def bundle_to_prompt(bundle: dict) -> str:
    parts = [bundle["instructions"], "",
             "TARGET_GATE: " + json.dumps(bundle["target_gate"]),
             "FIELD_CLUES: " + json.dumps(bundle.get("field_clues", [])), ""]
    for s in bundle.get("code_slices", []):
        parts += ["CODE SLICE [%s] %s:" % (s.get("label", ""), s.get("file_line", "")),
                  s.get("text", ""), ""]
    parts += ["ACCESS-FACT LEDGER (JSON):", json.dumps(bundle.get("ledger", []), indent=1), "",
              "PREDICATE_SCHEMA (JSON):", json.dumps(bundle["predicate_schema"]), "",
              "Output ONLY the predicate JSON object conforming to PREDICATE_SCHEMA."]
    return "\n".join(parts)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _first_json_object(text: str):
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_predicate(raw: str) -> dict:
    text = raw.strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    obj = _first_json_object(text)
    if obj is not None:
        try:
            d = json.loads(obj)
            d.setdefault("terms", [])
            d.setdefault("abstain", False)
            d["_parse_ok"] = True
            return d
        except json.JSONDecodeError:
            pass
    return {"_parse_ok": False, "terms": [], "abstain": False, "_raw": raw[:800]}


def _urllib_poster(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def run_judge(model: str, bundle: dict, *, base_url=None, api_key=None, poster=_urllib_poster) -> dict:
    base_url = base_url or os.environ["DEEPSEEK_BASE_URL"]
    api_key = api_key or os.environ["DEEPSEEK_API_KEY"]
    body = {"model": model, "temperature": 0, "max_tokens": 4096,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": bundle_to_prompt(bundle)}], "stream": False}
    resp = poster(base_url.rstrip("/") + "/chat/completions",
                  {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}, body)
    pred = parse_predicate(resp["choices"][0]["message"]["content"])
    pred["_model"] = resp.get("model", model)
    pred["_usage"] = resp.get("usage")
    return pred
```

- [ ] **Step 4: 运行确认通过**(5 passed)
- [ ] **Step 5: 提交** — `git commit` "feat(pilot): model-agnostic judge adapter (deepseek, tolerant JSON parse)"

---

### Task 2: `multimodel_eval.py` —— 打分 + 纯聚合

**Files:** Create `src/implicitfuzz/pilot/multimodel_eval.py`;Test `tests/test_multimodel_eval.py`

**Interfaces:**
- Consumes: `validate_*`、`score_terms`、`score_terms_relaxed`、`_term_key`。
- Produces:
  - `score_run(judge_output, truth, conn=None) -> dict`(conn=None 跳过 field_existence)
  - `aggregate_models(scored_by_model) -> dict`(正样本逐模型均值 P/R + 弃权/parse/schema 率)
  - `guard_consistency(scored_by_model, negative_gate_ids) -> dict`
  - `inter_model_agreement(runs_by_model) -> dict`(per-gate exact/field Jaccard)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_multimodel_eval.py
from implicitfuzz.pilot.multimodel_eval import (
    score_run, aggregate_models, guard_consistency, inter_model_agreement)

TRUTH = {"terms": [{"class": "activation", "field_ref": "io_ring_ctx.nr_user_files", "align_target": ""}]}
GOOD = {"terms": [{"class": "activation", "field_ref": "io_ring_ctx.nr_user_files", "align_target": ""}],
        "abstain": False, "_parse_ok": True}
BADSCHEMA = {"terms": [{"class": "not_a_class", "field_ref": "x.y"}], "abstain": False, "_parse_ok": True}


def test_score_run_no_conn_skips_field_existence():
    s = score_run(GOOD, TRUTH, conn=None)
    assert s["field_existence"] is None
    assert s["exact"]["precision"] == 1.0 and s["exact"]["recall"] == 1.0
    assert s["schema"] in (True, False)  # depends on real schema; smoke


def test_aggregate_models_means_and_rates():
    scored = {"m": {
        "g1": {"exact": {"precision": 1.0, "recall": 1.0}, "relaxed": {"field_set": {"precision": 1.0, "recall": 1.0}},
               "abstain": False, "parse_ok": True, "schema": True},
        "g2": {"exact": {"precision": 0.0, "recall": 0.0}, "relaxed": {"field_set": {"precision": 0.0, "recall": 0.0}},
               "abstain": True, "parse_ok": True, "schema": False}}}
    a = aggregate_models(scored)["m"]
    assert a["n_gates"] == 2 and a["exact_precision"] == 0.5
    assert a["abstain_rate"] == 0.5 and a["schema_ok_rate"] == 0.5


def test_guard_consistency_fires_on_schema_or_field_fail():
    scored = {"m": {
        "neg1": {"schema": False, "field_existence": {"passed": True}},
        "neg2": {"schema": True, "field_existence": {"passed": False}},
        "neg3": {"schema": True, "field_existence": {"passed": True}}}}
    g = guard_consistency(scored, ["neg1", "neg2", "neg3"])["m"]
    assert g["neg1"] is True and g["neg2"] is True and g["neg3"] is False


def test_inter_model_agreement_jaccard():
    runs = {"a": {"g": {"terms": [{"class": "activation", "field_ref": "s.x", "align_target": ""}]}},
            "b": {"g": {"terms": [{"class": "activation", "field_ref": "s.x", "align_target": ""},
                                   {"class": "premise", "field_ref": "s.y", "align_target": ""}]}}}
    per = inter_model_agreement(runs)["g"]
    assert per["field_jaccard"] == 0.5  # {s.x} ∩ {s.x,s.y} / ∪ = 1/2
```

- [ ] **Step 2: 运行确认失败** — `ModuleNotFoundError`
- [ ] **Step 3: 实现** `src/implicitfuzz/pilot/multimodel_eval.py`:
```python
"""Aggregate multi-model cold-judge runs: per-model precision/recall vs ground
truth, guard-fire consistency on negative controls, inter-model term agreement,
and abstention/compliance rates. Aggregation is pure (testable offline);
score_run's field-existence guard is the only part needing the DB (conn=None
skips it)."""

from __future__ import annotations
from implicitfuzz.pilot.validate import (validate_schema, validate_field_existence,
                                         validate_synthesizability)
from implicitfuzz.pilot.eval import score_terms, score_terms_relaxed, _term_key


def score_run(judge_output: dict, truth: dict, conn=None) -> dict:
    terms, tterms = judge_output.get("terms", []), truth.get("terms", [])
    fe = validate_field_existence(conn, judge_output) if conn is not None else None
    return {
        "schema": validate_schema(judge_output),
        "field_existence": fe,
        "synthesizability": validate_synthesizability(judge_output),
        "exact": score_terms(terms, tterms),
        "relaxed": score_terms_relaxed(terms, tterms),
        "abstain": bool(judge_output.get("abstain", False)),
        "parse_ok": bool(judge_output.get("_parse_ok", True)),
        "n_terms": len(terms),
    }


def aggregate_models(scored_by_model: dict) -> dict:
    out = {}
    for model, gates in scored_by_model.items():
        n = len(gates) or 1
        def _mean(path):
            tot = 0.0
            for s in gates.values():
                d = s
                for k in path:
                    d = d[k]
                tot += d
            return tot / n
        out[model] = {
            "n_gates": len(gates),
            "exact_precision": _mean(["exact", "precision"]),
            "exact_recall": _mean(["exact", "recall"]),
            "relaxed_field_precision": _mean(["relaxed", "field_set", "precision"]),
            "relaxed_field_recall": _mean(["relaxed", "field_set", "recall"]),
            "abstain_rate": sum(1 for s in gates.values() if s["abstain"]) / n,
            "parse_ok_rate": sum(1 for s in gates.values() if s["parse_ok"]) / n,
            "schema_ok_rate": sum(1 for s in gates.values() if s["schema"]) / n,
        }
    return out


def guard_consistency(scored_by_model: dict, negative_gate_ids) -> dict:
    out = {}
    for model, gates in scored_by_model.items():
        fired = {}
        for gid in negative_gate_ids:
            s = gates.get(gid)
            if s is None:
                fired[gid] = None
                continue
            fe = s.get("field_existence")
            fired[gid] = (not s["schema"]) or (fe is not None and not fe["passed"])
        out[model] = fired
    return out


def _jaccard(sets):
    sets = [s for s in sets if s]
    if len(sets) < 2:
        return None
    union = set.union(*sets)
    return len(set.intersection(*sets)) / len(union) if union else None


def inter_model_agreement(runs_by_model: dict) -> dict:
    models = list(runs_by_model)
    gate_ids = set().union(*[set(g) for g in runs_by_model.values()]) if models else set()
    per_gate = {}
    for gid in gate_ids:
        exact_sets, field_sets = [], []
        for m in models:
            terms = runs_by_model[m].get(gid, {}).get("terms", [])
            exact_sets.append({_term_key(t) for t in terms})
            field_sets.append({t.get("field_ref") for t in terms})
        per_gate[gid] = {"exact_jaccard": _jaccard(exact_sets),
                         "field_jaccard": _jaccard(field_sets)}
    return per_gate
```

- [ ] **Step 4: 运行确认通过 + 全套**(4 passed;pytest 全绿)
- [ ] **Step 5: 提交** — "feat(pilot): multi-model eval aggregation (P/R, guard consistency, inter-model Jaccard)"

---

### Task 3: `scripts/build_bundles.py`(server)—— 建冻结 bundle

**Files:** Create `scripts/build_bundles.py`;Test `tests/test_build_bundles.py`

**Interfaces:** Produces `build_all(conn, source_root, gates_dir, out_dir, gate_ids) -> list[str]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_build_bundles.py
import json, sqlite3, sys, os
sys.path.insert(0, "scripts")
from build_bundles import build_all

_DDL = ("CREATE TABLE access_fact (id INTEGER PRIMARY KEY AUTOINCREMENT, function TEXT, "
        "semantic_op TEXT, access_path_symbolic TEXT, numeric_kind TEXT, access_path_numeric TEXT);")


def test_build_all_writes_bundles_no_gt(tmp_path):
    conn = sqlite3.connect(":memory:"); conn.executescript(_DDL)
    conn.execute("INSERT INTO access_fact (function,semantic_op,access_path_symbolic,numeric_kind,access_path_numeric)"
                 " VALUES ('io_prep_rw','read','io_ring_ctx.nr_user_bufs','gep_offsets','[8]')")
    src = tmp_path / "src"; (src / "io_uring").mkdir(parents=True)
    (src / "io_uring" / "rw.c").write_text("\n".join("l%d" % i for i in range(1, 40)) + "\n")
    gates = tmp_path / "gates"; gates.mkdir()
    json.dump({"target_gate": {"function": "io_prep_rw"},
               "slice_locations": [{"file_line": "io_uring/rw.c:20", "label": "gate"}],
               "ledger_functions": ["io_prep_rw"], "field_clues": ["buf_index"]},
              open(gates / "fixed_buffer.json", "w"))
    out = tmp_path / "bundles"
    w = build_all(conn, str(src), str(gates), str(out), ["fixed_buffer"])
    assert w == ["fixed_buffer"]
    b = json.load(open(out / "fixed_buffer.json"))
    assert b["target_gate"]["function"] == "io_prep_rw" and b["ledger"]
    assert "label" not in b and "truth" not in b   # RED LINE: no GT
```

- [ ] **Step 2: 运行确认失败** — import error
- [ ] **Step 3: 实现** `scripts/build_bundles.py`:
```python
#!/usr/bin/env python3
"""Server-side: build the decontaminated cold-judge bundle for each gate and
dump it to <out-dir>/<gate>.json. RED LINE: bundles never contain ground truth
(build_bundle guarantees this). Usage:
  python3 scripts/build_bundles.py --db <facts.db> --source-root <kernel-src> \\
      --gates-dir src/implicitfuzz/pilot/gates --out-dir pilot/bundles [gate_id ...]"""
import argparse, json, os, sqlite3, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from implicitfuzz.pilot.context import build_bundle


def build_all(conn, source_root, gates_dir, out_dir, gate_ids):
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for gid in gate_ids:
        gate = json.load(open(os.path.join(gates_dir, gid + ".json")))
        bundle = build_bundle(conn, source_root, gate)
        assert "label" not in bundle and "truth" not in bundle, "GT leaked into bundle!"
        json.dump(bundle, open(os.path.join(out_dir, gid + ".json"), "w"), indent=1)
        written.append(gid)
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--gates-dir", default="src/implicitfuzz/pilot/gates")
    ap.add_argument("--out-dir", default="pilot/bundles")
    ap.add_argument("gate_ids", nargs="*")
    a = ap.parse_args()
    ids = a.gate_ids or [f[:-5] for f in sorted(os.listdir(a.gates_dir)) if f.endswith(".json")]
    conn = sqlite3.connect(a.db)
    w = build_all(conn, a.source_root, a.gates_dir, a.out_dir, ids)
    print("wrote %d bundles -> %s" % (len(w), a.out_dir))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行确认通过**(1 passed)
- [ ] **Step 5: 提交** — "feat(pilot): build_bundles CLI (decontaminated frozen bundles)"

---

### Task 4: `scripts/run_multimodel.py`(judge + score 子命令)

**Files:** Create `scripts/run_multimodel.py`;Test `tests/test_run_multimodel.py`

**Interfaces:** Produces `judge_stage(bundles_dir, models, out_root, runner) -> dict`(runner 注入,默认 `run_judge`)、`score_stage(runs_root, labels_dir, models, negatives, conn, extra_runs=None) -> dict`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_run_multimodel.py
import json, sys
sys.path.insert(0, "scripts")
from run_multimodel import judge_stage


def test_judge_stage_writes_per_model_runs(tmp_path):
    bundles = tmp_path / "bundles"; bundles.mkdir()
    json.dump({"instructions": "x", "target_gate": {}, "predicate_schema": {}},
              open(bundles / "g1.json", "w"))
    calls = []
    def fake_runner(model, bundle):
        calls.append((model, bundle.get("target_gate")))
        return {"terms": [{"class": "premise"}], "abstain": False, "_parse_ok": True, "_model": model}
    out = tmp_path / "runs"
    res = judge_stage(str(bundles), ["deepseek-v4-flash", "deepseek-v4-pro"], str(out), runner=fake_runner)
    assert res["deepseek-v4-flash"]["g1"] == "ok" and len(calls) == 2
    saved = json.load(open(out / "deepseek-v4-flash" / "g1.json"))
    assert saved["terms"][0]["class"] == "premise"
```

- [ ] **Step 2: 运行确认失败** — import error
- [ ] **Step 3: 实现** `scripts/run_multimodel.py`:
```python
#!/usr/bin/env python3
"""Two-stage multi-model eval CLI.
  judge (Mac, needs internet + ~/.config/implicitfuzz/llm.env):
     for each model x bundle -> run_judge -> <out>/<model>/<gate>.json
  score (server, needs facts DB for field-existence guard):
     load runs + labels + optional Claude runs_formal -> score_run + aggregate."""
import argparse, json, os, sqlite3, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from implicitfuzz.pilot.judge_adapter import run_judge
from implicitfuzz.pilot.multimodel_eval import (
    score_run, aggregate_models, guard_consistency, inter_model_agreement)


def judge_stage(bundles_dir, models, out_root, runner=run_judge):
    gate_ids = [f[:-5] for f in sorted(os.listdir(bundles_dir)) if f.endswith(".json")]
    status = {}
    for model in models:
        mdir = os.path.join(out_root, model)
        os.makedirs(mdir, exist_ok=True)
        status[model] = {}
        for gid in gate_ids:
            bundle = json.load(open(os.path.join(bundles_dir, gid + ".json")))
            out = runner(model, bundle)
            json.dump(out, open(os.path.join(mdir, gid + ".json"), "w"), indent=1)
            status[model][gid] = "ok"
    return status


def _load_runs(runs_root, model):
    mdir = os.path.join(runs_root, model)
    return {f[:-5]: json.load(open(os.path.join(mdir, f)))
            for f in sorted(os.listdir(mdir)) if f.endswith(".json")}


def score_stage(runs_root, labels_dir, models, negatives, conn, extra_runs=None):
    labels = {f[:-5]: json.load(open(os.path.join(labels_dir, f)))
              for f in os.listdir(labels_dir) if f.endswith(".json")}
    runs_by_model = {m: _load_runs(runs_root, m) for m in models}
    if extra_runs:
        runs_by_model.update(extra_runs)
    positives = [g for g in labels if g not in negatives]
    scored = {m: {g: score_run(runs.get(g, {"terms": []}), labels[g], conn)
                  for g in labels}
              for m, runs in runs_by_model.items()}
    return {
        "per_model": aggregate_models({m: {g: s[g] for g in positives} for m, s in scored.items()}),
        "guard_consistency": guard_consistency(scored, negatives),
        "inter_model_agreement": inter_model_agreement(runs_by_model),
        "models": list(runs_by_model),
    }


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("judge")
    j.add_argument("--bundles-dir", default="pilot/bundles")
    j.add_argument("--out-root", default="pilot/runs_multimodel")
    j.add_argument("--models", nargs="+", default=["deepseek-v4-flash", "deepseek-v4-pro"])
    s = sub.add_parser("score")
    s.add_argument("--runs-root", default="pilot/runs_multimodel")
    s.add_argument("--labels-dir", default="src/implicitfuzz/pilot/labels")
    s.add_argument("--db", required=True)
    s.add_argument("--models", nargs="+", default=["deepseek-v4-flash", "deepseek-v4-pro"])
    s.add_argument("--negatives", nargs="+",
                   default=["neg_writer_omitted", "neg_thin_slice", "neg_adversarial"])
    s.add_argument("--baseline-runs-formal", default="pilot/runs_formal")
    s.add_argument("--out", default="pilot/runs_multimodel/summary.json")
    a = ap.parse_args()
    if a.cmd == "judge":
        st = judge_stage(a.bundles_dir, a.models, a.out_root)
        print(json.dumps(st, indent=1))
    else:
        conn = sqlite3.connect(a.db)
        extra = None
        if a.baseline_runs_formal and os.path.isdir(a.baseline_runs_formal):
            extra = {"claude-opus-baseline": {
                f[:-5]: json.load(open(os.path.join(a.baseline_runs_formal, f)))
                for f in os.listdir(a.baseline_runs_formal) if f.endswith(".json")}}
        summary = score_stage(a.runs_root, a.labels_dir,
                              a.models + (["claude-opus-baseline"] if extra else []),
                              a.negatives, conn, extra_runs=extra)
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        json.dump(summary, open(a.out, "w"), indent=2)
        print(json.dumps(summary["per_model"], indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行确认通过 + 全套**(1 passed;pytest 全绿)
- [ ] **Step 5: 提交** — "feat(pilot): run_multimodel CLI (judge + score stages)"

---

### Task 5: 真实跨机跑 + 报告(过程,用户复核)

**Files:** Create `pilot/bundles/*.json`、`pilot/runs_multimodel/<model>/*.json`、`pilot/runs_multimodel/summary.json`、`docs/llm-predicate-multimodel-eval-findings.md`

- [ ] **Step 1: server 建 bundle** —— 先在 server 定位 facts DB + kernel source-root(查既有 pilot 生成方式 / RUNBOOK;冷判读 `runs_formal` 当初即由 `build_bundle` 生成)。`python3 scripts/build_bundles.py --db <facts.db> --source-root <kernel-src>` → `pilot/bundles/*.json`(13 个)。抽查一个 bundle:有 slices+ledger、无 GT。
- [ ] **Step 2: 同步 bundle → Mac**(scp/tar)。
- [ ] **Step 3: Mac 跑判读** —— `source ~/.config/implicitfuzz/llm.env && PYTHONPATH=src python3 scripts/run_multimodel.py judge --models deepseek-v4-flash deepseek-v4-pro`。产 `pilot/runs_multimodel/{deepseek-v4-flash,deepseek-v4-pro}/*.json`(26 个)。抽查:JSON 可解析、terms 非空/弃权合理。
- [ ] **Step 4: 同步 runs → server;server 打分聚合** —— `python3 scripts/run_multimodel.py score --db <facts.db>` → `summary.json`(per_model P/R、guard_consistency、inter_model_agreement,含 claude-opus-baseline)。
- [ ] **Step 5: 写报告** `docs/llm-predicate-multimodel-eval-findings.md` —— 逐模型 P/R(exact+relaxed,人工语义为主判据)、3 负控守卫触发一致性表、模型间 Jaccard、弃权/合规率;confound 标注(baseline 先前会话、无法字节比对历史 bundle);结构性去污染说明;非统计声明;定位(只攻单模型轴)。**提交前交用户复核。**
- [ ] **Step 6: 回归 + 提交 + 三边同步** —— `run_phase1_regression.sh` ALL PASS;`pytest` 全绿;`git add` bundles/runs/summary/report;commit "feat(pilot): cross-vendor multi-model cold-judge eval run + report";三边同步(见 [[feedback-three-way-sync]])。**密钥不入 git(检查 `git status` 无 llm.env)。**

---

## Self-Review

- **Spec 覆盖:** 名单/复用基线 → Global + Task 4(extra_runs claude-opus-baseline);跨机流水线 → Task 3/5(server build)+ Task 4/5(Mac judge / server score);无 tools 结构性去污染 → Task 1(run_judge 无 tools)+ Task 5 报告;RED LINE 无 GT → Task 3(assert);指标(P/R、守卫一致性、Jaccard、弃权、合规)→ Task 2 + Task 4;逐模型不 pooling → aggregate_models 按 model;容错解析 → Task 1;凭据环境变量不入 git → Task 1 + Task 5 Step6;诚实/confound → Task 5 报告。✅
- **占位符:** Tasks 1–4 完整代码/测试/命令;Task 5 过程 + 具体产物 + 用户复核门;唯一"发现"项 = server 的 facts DB / source-root 路径(Task 5 Step1 显式为发现步,因 tunnel 当时不可达;冷判读既有产物证明其存在)。
- **类型一致:** `bundle_to_prompt`/`parse_predicate`/`run_judge`/`score_run`/`aggregate_models`/`guard_consistency`/`inter_model_agreement`/`judge_stage`/`score_stage`、judge 输出字段(terms/abstain/_parse_ok/_model)、runner 签名 `(model,bundle)->pred` 跨 Task 一致。✅
- **已知开放点:** DeepSeek 用 `response_format=json_object`,故 parse_ok 近 100%、schema_ok 才是合规判据(报告注明);人工语义评分需人工填(沿用 `human_score_template`);baseline 无法字节比对(已标注)。
