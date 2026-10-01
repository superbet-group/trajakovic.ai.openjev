import json, math, re, sys
from render_lib import rnd
CAP = json.load(open("captured.json"))
SUITE = {c["id"]: c for c in json.load(open("/Users/trajakovic/Projects/Models/openjev/docs/mcp-skill-spec/tests/cases/00-spec-examples.json"))["cases"]}
ESCAPES = ("other", "none", "no_match", "not_stated", "empty")

def body(i): return CAP[i]["body"]
def ans(i): return body(i)["answers"]
def meta(i):
    b = body(i); st = CAP[i]["server_timing"] or ""
    m = re.search(r"total;dur=([0-9.]+)", st)
    return {"model": b.get("model"), "request_ids": [CAP[i]["request_id"]], "requests": 1,
            "latency_ms": CAP[i]["ms"], "server_ms": float(m.group(1)) if m else None,
            "input_tokens": b["usage"]["input_tokens"], "output_tokens": b["usage"]["output_tokens"], "warnings": []}

def derive(a, yes_at=0.8, no_at=0.2, min_p=0.6):
    t = a["type"]
    if t == "noul":
        p = a["noul"]; band = "yes" if p >= yes_at else "no" if p <= no_at else "grey"
        return {"type": "noul", "p": p, "band": band, "margin": abs(2 * p - 1)}
    if t == "choice":
        pr = a["probabilities"]; order = sorted(pr, key=lambda k: -pr[k]); top = a["choice"]
        p2 = pr[order[1]] if len(order) > 1 else 0.0
        ent = -sum(p * math.log(p) for p in pr.values() if p > 0)
        esc = top.startswith(ESCAPES) or top.startswith("other_")
        return {"type": "choice", "choice": top, "p_top": pr[top], "runner_up": order[1] if len(order) > 1 else None,
                "margin": pr[top] - p2, "probabilities": pr, "confidence": a["confidence"], "entropy": ent,
                "abstained": bool(esc or pr[top] < min_p)}
    if t == "score":
        pr = {int(k): v for k, v in a["probabilities"].items()}; s = a["score"]
        lvl = max(pr, key=lambda k: pr[k])
        spread = math.sqrt(sum(p * (k - s) ** 2 for k, p in pr.items()))
        big = [k for k, p in pr.items() if p >= 0.2]
        bim = any(abs(x - y) >= 2 for x in big for y in big)
        return {"type": "score", "score": s, "level": lvl, "level_label": a["legend"][str(lvl)],
                "probabilities": a["probabilities"], "confidence": a["confidence"], "spread": spread, "bimodal": bim}

def topn(pr, n):
    order = sorted(pr, key=lambda k: -pr[k])
    out = {k: pr[k] for k in order[:n]}
    if len(order) > n: out["_omitted_options"] = len(order) - n
    return out

def trim_answers(answers, n):
    out = {}
    for k, v in answers.items():
        v = dict(v)
        if "probabilities" in v and len(v["probabilities"]) > n: v["probabilities"] = topn(v["probabilities"], n)
        out[k] = v
    return out

def fmt(obj, depth=0, maxdepth=2, ind=" "):
    one = json.dumps(obj, ensure_ascii=False)
    if depth >= maxdepth or len(one) <= 110 or not isinstance(obj, (dict, list)) or not obj:
        return one
    pad = ind * (depth + 1)
    if isinstance(obj, dict):
        items = [pad + json.dumps(k, ensure_ascii=False) + ": " + fmt(v, depth + 1, maxdepth) for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + ind * depth + "}"
    items = [pad + fmt(v, depth + 1, maxdepth) for v in obj]
    return "[\n" + ",\n".join(items) + "\n" + ind * depth + "]"

def block(obj, maxdepth=2):
    return "```json\n" + fmt(rnd(obj), 0, maxdepth) + "\n```"

def req_block(i):
    r = SUITE[i].get("request", {})
    return "```json\n" + fmt(r, 0, 2) + "\n```"

def resp_block(i, n=None):
    c = CAP[i]; b = c["body"]
    if isinstance(b, dict) and "answers" in b and n:
        b = dict(b); b["answers"] = trim_answers(b["answers"], n)
    head = f"Live: HTTP {c['status']}, {c['ms']} ms (idle server; repeated bodies hit the prefill cache, cold latencies are in 1.5). Case `00-spec-examples.json::{i}`."
    if n: head += f" Probabilities trimmed to the top {n}; `_omitted_options` counts the rest."
    return head + "\n\n" + block(b)

OUT = {}
def reg(name):
    def d(f): OUT[name] = f; return f
    return d

@reg("ask")
def _():
    return {"answers": {k: derive(v) for k, v in ans("ex-ask").items()}, "meta": meta("ex-ask"), "lint": {"warnings": []}}
@reg("yes_no")
def _():
    d = derive(ans("ex-yes-no")["q"]); return {"decision": d["band"].replace("grey", "uncertain"), "p": d["p"], "margin": d["margin"], "thresholds_used": {"yes_at": 0.8, "no_at": 0.2}, "meta": meta("ex-yes-no")}
@reg("classify")
def _():
    d = derive(ans("ex-classify-abstain")["q"])
    return {"label": None, "abstained": True, "reason": "escape option 'other' won (p=%.4f)" % d["p_top"], "top": d["choice"], "p_top": d["p_top"], "runner_up": d["runner_up"], "margin": d["margin"], "probabilities": d["probabilities"], "confidence": d["confidence"], "meta": meta("ex-classify-abstain")}
@reg("score")
def _():
    d = derive(ans("ex-score")["q"]); d.pop("type"); d["meta"] = meta("ex-score"); return d
@reg("filter")
def _():
    a = ans("ex-filter"); items = []
    kept, dropped, grey = [], [], []
    for k, v in a.items():
        p = v["noul"]; dec = "keep" if p >= 0.6 else "drop" if p <= 0.2 else "grey"
        items.append({"id": k, "p": p, "decision": dec}); {"keep": kept, "drop": dropped, "grey": grey}[dec].append(k)
    m = meta("ex-filter"); return {"kept": kept, "dropped": dropped, "grey": grey, "items": items, "meta": m}
@reg("batch")
def _():
    res = []; lat = 0
    for i, iid in enumerate(["t1", "t2", "t3"], 1):
        a = ans(f"ex-batch-{i}"); d = derive(a["dept"]); u = derive(a["urgent"])
        nr = d["p_top"] < 0.8 or u["band"] == "grey"
        res.append({"id": iid, "answers": {"dept": {"choice": d["choice"], "p_top": d["p_top"]}, "urgent": {"p": u["p"], "band": u["band"]}}, "needs_review": nr, "error": None})
        lat += CAP[f"ex-batch-{i}"]["ms"]
    return {"summary": {"n": 3, "ok": 3, "errors": 0, "needs_review": sum(r["needs_review"] for r in res),
                        "per_question": {"dept": {"counts": {"billing": 1, "technical": 1, "sales": 1}}, "urgent": {"yes": 1, "no": 2, "grey": 0}}},
            "results": res, "output_path": None, "audit_ids": [], "meta": {"model": body("ex-batch-1")["model"], "requests": 3, "latency_ms": lat}}
@reg("image")
def _():
    return {"answers": {k: derive(v) for k, v in ans("ex-image").items()}, "images": [{"source": "tests/data/hotdog.jpg", "sent_as": "image/jpeg", "bytes": 12860, "reencoded": False}], "meta": meta("ex-image")}
@reg("calibrate")
def _():
    rows = []
    for i in range(1, 8):
        cid = f"ex-cal-{i}"; rows.append({"id": cid, "label": SUITE[cid]["label"], "p": ans(cid)["escalate"]["noul"]})
    pos = [r["p"] for r in rows if r["label"]]; neg = [r["p"] for r in rows if not r["label"]]
    lo, hi = max(neg), min(pos)
    return {"model_resolved": "openjev-0.1", "n": 7, "question_hash": "sha256:<of the canonical questions JSON>",
            "per_question": {"escalate": {"type": "noul", "n_pos": len(pos), "n_neg": len(neg), "accuracy_at_0.5": 1.0,
              "separable": hi > lo, "max_negative": lo, "min_positive": hi, "gap": hi - lo, "t_fit": (hi + lo) / 2,
              "suggested_band": {"no_at": 0.05, "yes_at": 0.95}, "overlap_ids": [],
              "most_borderline": sorted(rows, key=lambda r: abs(r["p"] - 0.5))[0]["id"],
              "zero_error_upper_bound_95": round(3 / 7, 3)}},
            "items": rows, "warnings": ["n=7: smoke test only; 0 errors in 7 bounds the error rate at ~43% (rule of three)"]}
@reg("calibrate_call")
def _():
    ex=[]
    for i in range(1,8):
        cid=f"ex-cal-{i}"; ex.append({"id": cid, "state": SUITE[cid]["request"]["state"], "label": {"escalate": SUITE[cid]["label"]}})
    return {"tool": "openjev_calibrate", "arguments": {"questions": SUITE["ex-cal-1"]["request"]["questions"], "examples": ex, "options": {"samples": 1}}}
@reg("gate_deny")
def _():
    a = ans("ex-gate-deny"); v = derive(a["verdict"])
    sig = {k: round(a[k]["noul"], 4) for k in ("destructive", "exfiltrates", "remote_code", "weakens_security", "out_of_scope")}
    return {"decision": "deny", "reason": "remote_code=%.4f >= 0.85; risk=%.2f >= 2.3; verdict=deny (p=%.4f)" % (a["remote_code"]["noul"], a["risk"]["score"], v["p_top"]),
            "signals": {**sig, "risk": a["risk"]["score"], "verdict": v["choice"], "verdict_p": v["p_top"]},
            "thresholds_used": {"deny_hazard": 0.85, "ask_hazard": 0.4, "deny_risk": 2.3, "allow_risk": 1.0}, "degraded": False, "requests": 1, "meta": meta("ex-gate-deny")}
@reg("status")
def _():
    return {"healthy": True, "base_url": "http://127.0.0.1:8080", "decide_models": ["openjev-latest", "openjev-0.1"], "aliases_accepted": ["jev-latest", "jev-preview"],
            "chat_models": ["diffusiongemma-26b"], "resolved": {"openjev-latest": "openjev-0.1"}, "auth": "none", "backend": "unknown", "limit_source": "default",
            "latency_probe_ms": CAP["ex-yes-no"]["ms"], "limits": {"questions": 256, "choice_options": 255, "score_levels": 10, "images": 8, "image_bytes": 5242880, "prompt_tokens": 32768, "body_bytes": 67108864},
            "warnings": ["GET /v1/limits not available (404): limits are the documented defaults, limit-dependent lint findings are warnings, backend unknown"]}
@reg("generate")
def _():
    b = body("ex-generate"); return {"content": b["choices"][0]["message"]["content"], "finish_reason": "stop", "usage": b["usage"], "retried": False,
                                     "warnings": ["MLX backend: newlines are dropped from replies; tools, logprobs and image parts are ignored"]}

def render(src):
    def rep(m):
        kind, arg = m.group(1), m.group(2)
        if kind == "REQ": return req_block(arg)
        if kind == "RESP": return resp_block(arg)
        if kind == "RESPTOP":
            i, n = arg.split(":"); return resp_block(i, int(n))
        if kind == "OUT": return block(OUT[arg]())
        if kind == "V":  # V:id.q.field
            i, q, f = arg.split(".", 2); v = ans(i)[q]
            for part in f.split("."): v = v[part]
            return str(rnd(v))
        if kind == "MS": return str(CAP[arg]["ms"])
        raise KeyError(kind)
    out = re.sub(r"\{\{([A-Z]+):([^}]+)\}\}", rep, src)
    return out

if __name__ == "__main__":
    parts = sys.argv[1:-1]; dst = sys.argv[-1]
    src = "".join(open(p).read() for p in parts)
    res = render(src)
    left = re.findall(r"\{\{[^}]+\}\}", res)
    open(dst, "w").write(res)
    print("written", dst, len(res), "chars; unresolved:", left)
