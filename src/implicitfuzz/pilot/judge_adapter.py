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
