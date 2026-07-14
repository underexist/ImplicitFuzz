# Multi-Model Cold-Judge Eval — Findings

**Date:** 2026-07-14
**Question:** Do independent LLM architectures, given the *same* frozen decontaminated
bundles, converge on the same implicit-predicate terms — and do the deterministic
validation guards fire model-independently on negative controls?

**Judges (per-model, no pooling):**
- `deepseek-v4-flash` — new run, DeepSeek OpenAI-compatible API, **no tools**.
- `deepseek-v4-pro` — new run, same API, **no tools**.
- `claude-opus-baseline` — **reused** `pilot/runs_formal/*.json` from a prior session (see confounds).

**Set:** the frozen 13-gate set — 10 positives
(`read_fixed_file, fixed_buffer, cancel_fixed_file, msg_ring_fixed_file,
filetable_slot, net_fixed_buffer, poll_polled, poll_double, link_timeout,
kiocb_flags`) + 3 negative controls (`neg_writer_omitted, neg_thin_slice,
neg_adversarial`).

> **This is a qualitative robustness probe, not a statistical result.** n=13,
> curated. The only axis varied here is the *model*; the gate set, bundles, and
> ground-truth labels are held fixed. Cross-vendor convergence is a directional
> robustness signal, nothing more.

---

## Pipeline & structural decontamination

1. **Server** (`build_bundles`, has facts DB + kernel source, no internet):
   ingested all `/tmp/io_uring_*.facts.jsonl` module facts + `rsrc_layout` into one
   SQLite DB (access_fact=5854, struct_layout_fact=4739), then `build_bundle` for
   each gate against `linux-6.1` source → 13 frozen bundles.
   **RED LINE upheld:** bundles carry `target_gate`, `field_clues`, curated
   `code_slices`, an access-fact `ledger`, `instructions`, and `predicate_schema` —
   **no ground truth**. GT lives only in `labels/`, applied by the harness *after*
   judging.
2. **Mac** (`run_multimodel judge`, has internet, no DB): each model × 13 bundles →
   one API call each, `temperature=0`, `response_format=json_object`, **no tools**.
   → `pilot/runs_multimodel/<model>/*.json`.
3. **Server** (`run_multimodel score`, has DB for the field-existence guard):
   `score_run` + aggregation → `pilot/runs_multimodel/summary.json`.

**Decontamination is structural, not sandboxed:** the DeepSeek judges receive only
the serialized bundle text over the API. They have no tool access, no filesystem,
no DB, and no path to the repo or the ground truth. This is a stronger guarantee
than a subagent sandbox (there is nothing to reach), and it is the reason the
DeepSeek runs are the clean arm of this comparison.

---

## 1. Per-model accuracy (10 positives; means, no pooling)

| model | exact P | exact R | relaxed-field P | relaxed-field R | abstain | parse_ok | schema_ok |
|---|---|---|---|---|---|---|---|
| deepseek-v4-flash | 0.20 | 0.20 | 0.30 | 0.30 | 0.10 | 1.00 | 1.00 |
| deepseek-v4-pro | 0.20 | 0.25 | 0.53 | 0.60 | 0.00 | 1.00 | 1.00 |
| claude-opus-baseline* | 0.36 | 0.70 | 0.68 | 0.90 | 0.00 | 1.00 | 1.00 |

\* confounded — see below.

- **Exact-key P/R is low for everyone**, including Claude. This reproduces the
  pilot's finding that exact `(class, field_ref, align_target)` matching is too
  strict. The **relaxed field-set** score (field_ref overlap, ignoring class and
  attribution text) is the usable proxy. **Human semantic scoring remains the
  primary metric and has not yet been done** (templates in `human_score_template`).
- **Model tiering is visible and monotone** on the relaxed proxy:
  pro (0.53/0.60) clearly beats flash (0.30/0.30). flash emits no field-overlap on
  6 of 10 positives (`cancel, kiocb_flags, link_timeout, net, poll_double,
  read_fixed_file`) and abstains on `net_fixed_buffer`.
- **Schema and parse compliance are perfect (1.00) for all models.** Because
  `response_format=json_object` forces valid JSON, `parse_ok` is uninformative here;
  `schema_ok` (conformance to the predicate schema) is the real compliance metric,
  and it is 1.00 across the board.

### Per-gate relaxed field-set P/R  (exact P/R in parens; `A`=abstained)

| gate | flash | pro | claude* |
|---|---|---|---|
| cancel_fixed_file | 0.00/0.00 (0.00/0.00) | 0.33/0.50 (0.00/0.00) | 0.50/1.00 (0.40/0.67) |
| filetable_slot | 1.00/1.00 (1.00/1.00) | 0.50/0.50 (0.50/0.50) | 1.00/1.00 (0.50/1.00) |
| fixed_buffer | 0.50/0.50 (0.00/0.00) | 0.50/0.50 (0.00/0.00) | 0.25/0.50 (0.00/0.00) |
| kiocb_flags | 0.00/0.00 (0.00/0.00) | 0.50/1.00 (0.00/0.00) | 0.50/1.00 (0.33/1.00) |
| link_timeout | 0.00/0.00 (0.00/0.00) | 1.00/1.00 (0.00/0.00) | 1.00/1.00 (0.50/1.00) |
| msg_ring_fixed_file | 0.50/0.50 (0.00/0.00) | 0.50/0.50 (0.00/0.00) | 0.67/1.00 (0.25/0.33) |
| net_fixed_buffer | 0.00/0.00 (0.00/0.00) `A` | 0.00/0.00 (0.00/0.00) | 0.25/0.50 (0.20/0.33) |
| poll_double | 0.00/0.00 (0.00/0.00) | 1.00/1.00 (0.50/1.00) | 1.00/1.00 (0.50/1.00) |
| poll_polled | 1.00/1.00 (1.00/1.00) | 1.00/1.00 (1.00/1.00) | 1.00/1.00 (0.50/1.00) |
| read_fixed_file | 0.00/0.00 (0.00/0.00) | 0.00/0.00 (0.00/0.00) | 0.67/1.00 (0.40/0.67) |

`read_fixed_file` is the one positive where **both** DeepSeek models score zero
field overlap while Claude recovers it — a candidate gate where the cold bundle may
under-specify the target for models outside the curating family, worth a manual look.

---

## 2. Negative-control guard consistency — **not model-invariant**

Guard fires when the predicate fails schema **or** field-existence. Since schema
passed everywhere, every firing below is the field-existence guard catching an
unconfirmable field (a term whose `field_ref` cannot be reconciled against the DB).

| negative control | flash | pro | claude* |
|---|---|---|---|
| neg_writer_omitted | PASS (did not fire) | **fired** | **fired** |
| neg_thin_slice | **fired** | **fired** | PASS (did not fire) |
| neg_adversarial | PASS (did not fire) | PASS (did not fire) | **fired** |

**No negative control tripped the guard for all three models**, and each fired for a
different subset. The reason is structural: the field-existence guard is
*deterministic given a predicate*, but the predicate differs per model, so its
firing is **model-dependent, not model-invariant**. The only genuinely
model-invariant guard here — schema conformance — did **not** discriminate on the
negatives (100% schema_ok). So the current guard battery does not give us a
model-independent rejection signal on these three negatives. Strengthening the
deterministic rejection (e.g. requiring synthesizability, or a slice-support check
that fires regardless of which fields the model names) is the actionable follow-up.

---

## 3. Cross-vendor / cross-model convergence (Jaccard on positives)

Mean field-ref Jaccard over the 10 positives (higher = more agreement on *which*
struct fields matter):

| model set | mean field Jaccard | mean exact Jaccard |
|---|---|---|
| flash + pro (clean: identical frozen bundles, both no-tools) | **0.43** | 0.19 |
| flash + claude* | 0.35 | 0.13 |
| pro + claude* | 0.56 | 0.27 |
| flash + pro + claude* (3-way) | 0.25 | 0.08 |

Per-gate flash-vs-pro (the **clean** cross-model comparison — both saw byte-identical
frozen bundles and neither had tools):

| gate | field Jaccard |
|---|---|
| fixed_buffer | 1.00 |
| msg_ring_fixed_file | 1.00 |
| poll_polled | 1.00 |
| read_fixed_file | 0.50 |
| filetable_slot | 0.33 |
| cancel_fixed_file / kiocb_flags / link_timeout / poll_double | 0.00 |
| net_fixed_buffer | n/a (flash abstained) |

**Convergence is partial.** Three positives (`fixed_buffer`, `msg_ring_fixed_file`,
`poll_polled`) show two independent DeepSeek tiers landing on exactly the same fields
from the same bundle — a real, if narrow, robustness signal. But four positives show
zero overlap between the two DeepSeek models, driven mostly by flash emitting wrong
or empty fields. `pro + claude` is the highest-agreement pair (0.56), consistent with
pro being the stronger model and tracking the curating-family baseline more closely.

---

## Confounds & honesty caveats

1. **Baseline reuse (largest confound).** `claude-opus-baseline` is reused from a
   prior session's `runs_formal`. We **cannot byte-compare the historical bundles**
   it saw against the frozen bundles built here, so its bundles may differ
   (different slice radius, ledger contents, or instructions). Claude also belongs
   to the model family that **curated the gates and labels**, so its higher scores
   partly reflect in-family familiarity, not just capability. Treat every Claude
   number as directional context, not a controlled comparison.
2. **The clean arm is flash-vs-pro only.** Both DeepSeek runs used byte-identical
   frozen bundles, the same API, `temperature=0`, and no tools. All cross-*vendor*
   claims inherit confound (1).
3. **Not statistical.** n=13, curated single-axis. No CIs, no significance.
4. **Human semantic scoring not yet done.** It remains the primary metric; the P/R
   here are automated proxies (relaxed field-set is the usable one).
5. **json_object masks a metric.** `parse_ok=1.00` is forced by
   `response_format=json_object`; `schema_ok` is the meaningful compliance number.
6. **Reasoning-budget fix.** DeepSeek v4 spends completion budget on reasoning
   tokens; the initial `max_tokens=4096` was fully consumed by reasoning and
   truncated the JSON (`parse_ok=False`). Raising it to 16384 fixed this
   (`pilot/judge_adapter.py`); the reported runs all completed within budget.

## Bottom line

Given decontaminated, tool-free bundles, two independent DeepSeek tiers **partially**
converge with each other and with the (confounded) Claude baseline on which struct
fields carry the implicit predicate — strong on a minority of gates
(`fixed_buffer`, `msg_ring_fixed_file`, `poll_polled`), absent on others. Predicate
quality tracks model capability (pro > flash). The deterministic guards are **not**
model-invariant on the current negatives: the only model-invariant guard (schema)
does not discriminate, and the discriminating guard (field-existence) is
predicate-dependent. Robustness of the implicit-predicate signal across vendors is
therefore **suggestive but not established**; the actionable next steps are (a) a
controlled re-run of the Claude arm on the *same* frozen bundles to remove confound
(1), (b) human semantic scoring, and (c) a stronger model-invariant rejection guard.

---

### Artifacts
- `pilot/bundles/*.json` — 13 frozen decontaminated bundles (no GT).
- `pilot/runs_multimodel/{deepseek-v4-flash,deepseek-v4-pro}/*.json` — 26 judge outputs.
- `pilot/runs_multimodel/summary.json` — per-model aggregates, guard consistency,
  inter-model agreement, per-gate `_detail`.
- Baseline reused from `pilot/runs_formal/*.json` (prior session; confounded).
