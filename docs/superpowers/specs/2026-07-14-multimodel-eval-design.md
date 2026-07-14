# 多模型冷判读评估设计

**Date:** 2026-07-14
**Branch:** `phase2b-evidence-identity` (worktree `feedback-loop`)
**Base commit:** `c8b2df1`
**Scope:** 把 Phase 2 冷判读从"单模型可行性信号"推进为**跨厂商多模型 robustness 证据**,直接攻 `llm-predicate-formal-eval-findings.md` 自陈的"单模型"缺口。
**Status:** design approved;待写实现计划。
**依据:** `docs/llm-predicate-formal-eval-findings.md`(单模型基线,"credible primary-judge candidate"、显式要求 larger multi-model eval);`src/implicitfuzz/pilot/{context,validate,eval}.py`;13 门控冷判读集 + `runs_formal/` + `labels/`。

---

## 0. 定位(准确边界)
单模型正式评测已证:冷判读在 13 门控上产语义正确谓词、两道确定性守卫真拦幻觉、正样本零误报;但**n=13、单模型、单会话、curated 切片**。本轮**只攻"单模型"这一轴**:用**跨厂商**判读(Claude vs DeepSeek)看①独立架构是否收敛到相同谓词项、②确定性守卫是否**模型无关地**在负控上触发。**不**扩 n、**不**改 curated 切片、**不**做统计显著性主张。

## 1. 判读名单(2 厂商 / 3 判读点)
- **deepseek-v4-flash**(新跑,API)
- **deepseek-v4-pro**(新跑,API)
- **Claude Opus 基线**:复用已提交 `pilot/runs_formal/*.json`(免费)。**Confound:** 属先前会话/时间、且历史 bundle 未整体留存,报告显式标注(见 §5)。

## 2. 跨机流水线(由 internet/DB 分裂决定;并强化去污染)
服务器有 access-fact DB + 内核源但**无外网**;Mac 有外网但无 DB/源。故:

```
① 服务器(DB+源, 无网): build_bundles.py
     → build_bundle(conn, source_root, gate) for 13 gates
     → dump pilot/bundles/<gate>.json  (确定性, 无 GT)
② Mac(有网, 无 DB): judge_adapter.py + multimodel_eval(judge 阶段)
     → 对 {v4-flash, v4-pro} × 13 gates POST /chat/completions(无 tools)
     → 解析 JSON 谓词 → pilot/runs_multimodel/<model>/<gate>.json
③ 服务器(有 DB, field-existence 守卫需之): multimodel_eval(score 阶段)
     → 每 run: validate(schema + field_existence + synthesizability) + score(exact + relaxed) vs labels/
     → 复用 runs_formal 为 Claude 数据点 → 聚合指标
④ 报告 docs/llm-predicate-multimodel-eval-findings.md + 原始 run 落盘
```
Mac judge-runner **只见 bundle 文件**(无 DB/repo GT)→ 去污染结构性成立。

## 3. 复用(不改)
`context.build_bundle` / `_INSTRUCTIONS`(同一 prompt + predicate_schema,逐模型字节一致输入)、`gates/`+`labels/`(13 门控 = 10 正 + 3 负控,冻结 GT)、`validate.py`(schema + field_existence + synthesizability 守卫)、`eval.py`(`score_terms` exact + `score_terms_relaxed` class_field/field_set)。

## 4. 指标(逐模型报告,不 pooling 掩盖分歧)
1. **逐模型 vs GT:** 10 正样本上 exact(precision/recall)+ relaxed(class_field / field_set)。人工语义评分仍为主判据(proxies 为下界)。
2. **守卫触发一致性(核心新信号):** 3 负控上,field_existence / schema 守卫在**每个模型输出**上是否触发。目标:守卫**模型无关**——不论判读模型,编造字段/非法枚举都被拦。
3. **模型间一致性:** 每门控,跨模型的谓词项集合重叠(Jaccard / 交集),用 `_term_key` 与 relaxed key 两档。看独立架构是否收敛。
4. **弃权率:** 每模型 abstain / alt_candidates 分布。
5. **JSON 合规:** 每模型输出可解析为 predicate_schema 的比例(API 判读需容错解析:剥离 code-fence、抓首个 JSON 对象)。

## 5. 去污染与诚实边界
- **结构性去污染(API):** DeepSeek 判读**无 tools**,仅得 bundle 文本 → 无法触达 repo/GT/DB,强于 subagent 沙箱审计。
- **RED LINE:** bundle 永不含 GT(`build_bundle` 保证;`labels/` 独立);评分在判读之后由 harness 做。
- **基线复用 confound:** Claude 基线来自先前会话,且 `audit_formal` 仅存 target_gate、历史 bundle 未整体留存 → **无法字节比对历史 bundle**;仅能声明"用同一确定性 builder + 同一 DB/源重建",报告显式标注此限。
- **仍非强/统计:** n=13、curated 切片不变;跨厂商收敛是**定性 robustness 信号**,非统计显著。
- **单一评分者:** 人工语义 GT 无独立校验者(沿用既有限制)。

## 6. 架构:复用为主 + 新增
**新建(判读侧,非 execverify):**
- `src/implicitfuzz/pilot/judge_adapter.py` —— model-agnostic 判读调用 + 容错 JSON 解析;deepseek backend 走 OpenAI-compatible `/chat/completions`。
- `src/implicitfuzz/pilot/multimodel_eval.py` —— 聚合:逐模型 vs GT、守卫一致性、模型间一致性、弃权。
- `scripts/build_bundles.py`(server 阶段①)、`scripts/run_multimodel.py`(Mac 阶段② judge / server 阶段③ score,同脚本分子命令)。

**落盘:** `pilot/bundles/*.json`、`pilot/runs_multimodel/<model>/*.json`、`docs/llm-predicate-multimodel-eval-findings.md`。
**凭据:** 读 `~/.config/implicitfuzz/llm.env`(`DEEPSEEK_API_KEY`/`DEEPSEEK_BASE_URL`),**永不入 git**。

## 7. 测试与验收
- **单测(纯 Python, 无网):** `judge_adapter` 的 JSON 容错解析(code-fence 剥离 / 抓首 JSON / 弃权;mock HTTP,不真调 API);`multimodel_eval` 聚合(逐模型 P/R、守卫一致性计数、模型间 Jaccard、弃权率)用**内存 fixture**(合成 judge 输出 + 合成 GT + stub validate),不依赖 DB/网。
- **真实跑:** ①服务器建 13 bundle;②Mac 跑 26 次 DeepSeek 判读;③服务器 validate+score+聚合;④报告。
- **凭据安全:** 密钥仅在 `~/.config/implicitfuzz/llm.env`;仓库无密钥;`git` 状态不含 llm.env。
- **回归:** `run_phase1_regression.sh` ALL PASS;`pytest` 全绿。
- **诚实:** 逐模型报告、守卫一致性单列、confound 标注、结构性去污染说明、非统计声明。

## 8. 明确不做(YAGNI)
扩 n / 自动切片发现、统计显著性、微调/few-shot、per-gate prompt 调参、把 DeepSeek 接入反馈闭环(本轮只评判读层)、除 Claude/DeepSeek 外的厂商(留 adapter seam)。

## 9. 文件结构(实现以计划为准)
- `src/implicitfuzz/pilot/judge_adapter.py` + `tests/test_judge_adapter.py`
- `src/implicitfuzz/pilot/multimodel_eval.py` + `tests/test_multimodel_eval.py`
- `scripts/build_bundles.py`(server)、`scripts/run_multimodel.py`(Mac judge 阶段 + server score 阶段可同脚本分子命令)
- `pilot/bundles/*.json`、`pilot/runs_multimodel/<model>/*.json`
- `docs/llm-predicate-multimodel-eval-findings.md`
