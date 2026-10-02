"""ask, yes_no, classify, score, run_read and compute_timeout (spec 2.6-2.9, 6.7)."""
from __future__ import annotations

import json
import re

import httpx
import pytest
import stubs

from openjev_mcp.config import Config
from openjev_mcp.derive import Band
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache, default_limits
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools.dispatch import call_tool
from openjev_mcp.tools.read import compute_timeout, run_read, score_prepare
from openjev_mcp.validate import validate_output

pytestmark = pytest.mark.anyio

NOUL = {"type": "noul", "instructions": "Is this a billing issue?",
        "criteria": {"true": "about charges", "false": "anything else"}}


def noul(p):
    return {"type": "noul", "noul": p}


def choice(top, probs):
    return {"type": "choice", "choice": top, "probabilities": probs, "confidence": 0.9}


def level(i, n=3):
    probs = {str(k): (0.8 if k == i else 0.2 / (n - 1)) for k in range(n)}
    return {"type": "score", "score": i + 0.1, "legend": {str(k): f"L{k}" for k in range(n)},
            "probabilities": probs, "confidence": 0.8}


def scripted(*reads, retries=2):
    """A transport answering the n-th /v1/systemone call with reads[n] (a qid -> raw answer dict, or a
    callable of the body); the last repeats. Bodies are on .bodies."""
    n = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal n
        body = json.loads(request.content)
        transport.requests.append(request)
        transport.bodies.append(body)
        read = reads[min(n, len(reads) - 1)]
        n += 1
        answers = read(body) if callable(read) else read
        return httpx.Response(200, request=request, headers={
            "x-request-id": f"req_{n}", "server-timing": f"model;dur=1.0, server;dur={n * 10.0}, total;dur={n * 10.0}"},
            json={"model": "openjev-0.1", "answers": answers, "usage": {"input_tokens": 10 * n, "output_tokens": n}})

    transport = stubs.RecordingTransport(handler)
    transport.bodies = []
    return transport


def make_ctx(transport, *, progress=None, audit=None, **cfg) -> ToolContext:
    config = Config(base_url="http://oj.test:8080", **cfg)
    client = OpenJevClient(config, transport=transport)
    return ToolContext(config, client, LimitsCache(client), progress or ProgressEmitter.noop(), audit)


async def call(transport, tool, args, **kw):
    res = await call_tool(make_ctx(transport, **kw), tool, args)
    assert res["isError"] is False, res
    out = res["structuredContent"]
    assert validate_output(tool, out) == []
    return out


async def error_of(transport, tool, args):
    res = await call_tool(make_ctx(transport), tool, args)
    assert res["isError"] is True and "structuredContent" not in res
    return json.loads(res["content"][0]["text"])["error"]


# ask

ASK = {"state": "s", "questions": {"zeta": NOUL, "alpha": NOUL, "mid": NOUL}}


async def test_ask_answers_follow_request_order():
    t = scripted({"mid": noul(0.5), "alpha": noul(0.1), "zeta": noul(0.9)})
    out = await call(t, "ask", ASK)
    assert list(out["answers"]) == ["zeta", "alpha", "mid"]
    assert [a["band"] for a in out["answers"].values()] == ["yes", "no", "grey"]
    assert out["meta"]["requests"] == 1 and out["lint"] == {"warnings": []}
    assert "raw" not in out


async def test_ask_return_raw_and_thresholds():
    t = scripted({"zeta": noul(0.7), "alpha": noul(0.7), "mid": noul(0.7)})
    out = await call(t, "ask", {**ASK, "return_raw": True, "thresholds": {"yes_at": 0.6}})
    assert out["raw"]["answers"]["zeta"] == noul(0.7)
    assert out["answers"]["zeta"]["band"] == "yes"


async def test_ask_thresholds_inverted_rejected():
    err = await error_of(scripted({}), "ask", {**ASK, "thresholds": {"yes_at": 0.5, "no_at": 0.5}})
    assert err["code"] == "OJ_INVALID_INPUT" and "yes_at" in err["message"]


async def test_ask_w402_w403_emitted():
    qs = {"a": NOUL, "b": NOUL}
    t = scripted({"a": noul(0.9), "b": noul(0.9)})
    out = await call(t, "ask", {"state": "s", "questions": qs, "options": {"think": 2000, "sequential": True}})
    codes = [w["code"] for w in out["lint"]["warnings"]]
    assert "W402" in codes and "W403" in codes
    assert t.bodies[0]["think"] == 2000 and t.bodies[0]["sequential"] is True
    assert out["meta"]["warnings"] == []


async def test_ask_w403_not_for_score_heavy_sets():
    qs = {f"s{i}": {"type": "score", "instructions": "How bad is it?", "criteria": ["fine", "bad", "worse"]}
          for i in range(10)}
    t = scripted({q: level(1) for q in qs})
    out = await call(t, "ask", {"state": "s", "questions": qs, "options": {"sequential": True}})
    assert "W403" not in [w["code"] for w in out["lint"]["warnings"]]


async def test_think_and_sequential_with_images_refused_locally():
    t = stubs.fail_on_request_transport()
    ctx = make_ctx(t)
    for opts in ({"think": 256}, {"sequential": True}):
        with pytest.raises(ToolError) as ei:
            await run_read(ctx, state="s", questions={"q": NOUL}, options=opts, band=Band(),
                           images=["data:image/png;base64,AAAA"])
        assert ei.value.code == "OJ_INVALID_INPUT" and ei.value.path == next(iter(opts))
    assert not [r for r in t.requests if r.method == "POST"]


BAD_CHOICE = {"type": "choice", "instructions": "Which one?", "criteria": ["a", "b"]}


@pytest.mark.parametrize("lint_mode", ["warn", "off"])
async def test_lint_errors_block_without_a_request(lint_mode):
    t = stubs.fail_on_request_transport()
    with pytest.raises(ToolError) as ei:
        await run_read(make_ctx(t), state="s", questions={"q": BAD_CHOICE}, options=None, band=Band(),
                       lint_mode=lint_mode)
    err = ei.value
    assert err.code == "OJ_INVALID_INPUT" and err.path == "questions.q.criteria" and err.hint
    assert err.message.startswith("questions.q.criteria: ") and ". Fix: " in err.message
    assert not [r for r in t.requests if r.method == "POST"]


async def test_lint_errors_join_with_semicolons():
    with pytest.raises(ToolError) as ei:
        await run_read(make_ctx(stubs.fail_on_request_transport()), state="s", band=Band(), options=None,
                       questions={"a": BAD_CHOICE, "b": {**BAD_CHOICE, "criteria": []}})
    assert ei.value.message.count("; ") >= 1 and ei.value.path == "questions.a.criteria"


async def test_lint_off_only_hides_warnings():
    qs = {"zeta": NOUL, "alpha": NOUL, "mid": NOUL}
    answers = {q: noul(0.9) for q in qs}
    args = {"state": "s", "questions": qs, "options": {"think": 2000}}
    out = await call(scripted(answers), "ask", {**args, "lint": "off"})
    assert "lint" not in out and out["meta"]["warnings"] == []
    out = await call(scripted(answers), "ask", args)
    assert [w["code"] for w in out["lint"]["warnings"]] == ["W402"]
    run = await run_read(make_ctx(scripted(answers)), state="s", questions=qs, options=args["options"],
                         band=Band(), lint_mode="off")
    assert run.lint_warnings == []


async def test_ask_model_option_and_timeout_not_in_body():
    t = scripted({"zeta": noul(0.9), "alpha": noul(0.9), "mid": noul(0.9)})
    out = await call(t, "ask", {**ASK, "options": {"model": "jev-latest", "timeout_ms": 5000, "samples": 2}})
    assert t.bodies[0]["model"] == "jev-latest" and t.bodies[0]["samples"] == 2 and "timeout_ms" not in t.bodies[0]
    assert out["meta"]["timeout_ms_used"] == 5000
    await call(t, "ask", ASK, model="openjev-0.1")
    assert t.bodies[-1]["model"] == "openjev-0.1"


async def test_meta_sums_over_requests():
    t = scripted({"q": noul(0.5)}, {"q": noul(0.5)})
    out = await call(t, "yes_no", CLAIM)
    meta = out["meta"]
    assert meta["requests"] == 2 and meta["request_ids"] == ["req_1", "req_2"]
    assert len(meta["body_hashes"]) == 2 and meta["body_hashes"][0] != meta["body_hashes"][1]
    assert meta["input_tokens"] == 30 and meta["output_tokens"] == 3
    assert meta["server_timing"] == {"model_ms": 2.0, "server_ms": 30.0, "total_ms": 30.0}
    assert meta["server_ms"] == 30.0 and meta["model"] == "openjev-0.1"
    assert list(meta)[:4] == ["model", "request_ids", "body_hashes", "requests"]


async def test_retry_is_noted_in_meta_warnings():
    ok = httpx.Response(200, json={"model": "openjev-0.1", "answers": {"q": noul(0.95)},
                                   "usage": {"input_tokens": 1, "output_tokens": 0}},
                        headers={"x-request-id": "req_r"})
    t = stubs.fault_transport(sequence=[(529, {"retry-after": "0", "content-type": "application/json",
                                               "x-request-id": "req_o"},
                                         b'{"detail":{"error_type":"overloaded_error","message":"busy"}}'), ok])
    ctx = make_ctx(t)
    ctx.limits._limits, ctx.limits._fetched = default_limits(), ctx.limits._clock()

    async def nap(_):
        return None

    ctx.client._sleep = nap
    res = await call_tool(ctx, "yes_no", CLAIM)
    assert res["isError"] is False
    assert res["structuredContent"]["meta"]["warnings"] == ["retried after OJ_OVERLOADED (HTTP 529): 2 attempts"]
    assert res["structuredContent"]["meta"]["requests"] == 1


async def test_audit_record_written(tmp_path):
    from openjev_mcp.audit import AuditLog
    path = tmp_path / "audit.jsonl"
    t = scripted({"q": noul(0.95)})
    await call(t, "yes_no", {**CLAIM, "state": "private"}, audit=AuditLog(str(path)))
    rec = json.loads(path.read_text().splitlines()[0])
    assert rec["tool"] == "yes_no" and rec["decision"] == "yes" and rec["request_id"] == "req_1"
    assert "state" not in rec and rec["answers"]["q"]["band"] == "yes"
    path2 = tmp_path / "audit2.jsonl"
    await call(t, "ask", {"state": "s", "questions": {"q": NOUL}}, audit=AuditLog(str(path2), log_states=True))
    rec = json.loads(path2.read_text().splitlines()[0])
    assert rec["tool"] == "ask" and rec["state"] == "s"


# timeouts

def test_compute_timeout_scaling():
    cfg = Config(timeout_ms=30000)
    est = {"latency_ms_idle_approx": 1000}
    assert compute_timeout(cfg, {}, est) == 30000
    assert compute_timeout(cfg, {}, {"latency_ms_idle_approx": 20000}) == 60000
    assert compute_timeout(cfg, {"think": 512}, est) == 120000                     # think floor
    assert compute_timeout(cfg, {"think": 4096}, {"latency_ms_idle_approx": 30000}) == 151440
    assert compute_timeout(cfg, {"think": 4096}, {"latency_ms_idle_approx": 300000}) == 600000   # cap
    assert compute_timeout(cfg, {"timeout_ms": 700}, est) == 700                   # explicit wins
    assert compute_timeout(cfg, {"timeout_ms": 5000, "think": 512}, est) == 5000
    assert compute_timeout(Config(timeout_ms=250000), {"think": 1}, est) == 250000


async def test_timeout_is_scaled_from_the_lint_estimate():
    t = scripted({"q": noul(0.9)})
    out = await call(t, "ask", {"state": "s", "questions": {"q": NOUL}, "options": {"think": 512}})
    assert out["meta"]["timeout_ms_used"] == 120000
    out = await call(t, "ask", {"state": "s", "questions": {"q": NOUL}})
    assert out["meta"]["timeout_ms_used"] == 30000


# progress

async def test_progress_emitted_per_request_with_constant_total():
    sent = []

    async def send(progress, total, message):
        sent.append((progress, total, message))

    t = scripted({"q": noul(0.5)}, {"q": noul(0.5)})
    await call(t, "yes_no", CLAIM,
               progress=ProgressEmitter(send, min_interval_s=0))
    assert [(p, tot) for p, tot, _ in sent] == [(1, 2), (2, 2)]
    assert all(re.fullmatch(r"\d/2 ok=\d err=0 eta \d+s", m) for _, _, m in sent)
    sent.clear()
    await call(scripted({"zeta": noul(0.9), "alpha": noul(0.9), "mid": noul(0.9)}), "ask", ASK,
               progress=ProgressEmitter(send, min_interval_s=0))
    assert [(p, tot) for p, tot, _ in sent] == [(1, 1)]


# yes_no

CLAIM = {"state": "I was charged twice.", "claim": "Is this a billing issue?",
         "true_means": "about charges", "false_means": "anything else"}


async def test_yes_no_decisions_and_body():
    for p, decision in ((0.95, "yes"), (0.05, "no"), (0.8, "yes"), (0.2, "no"), (0.5, "uncertain")):
        t = scripted({"q": noul(p)}, {"q": noul(p)})
        out = await call(t, "yes_no", CLAIM)
        assert out["decision"] == decision and out["p"] == p and out["margin"] == pytest.approx(abs(2 * p - 1))
        assert out["thresholds_used"] == {"yes_at": 0.8, "no_at": 0.2}
    body = t.bodies[0]
    assert list(body["questions"]) == ["q"] and body["samples"] == 1
    assert body["questions"]["q"] == {"type": "noul", "instructions": "Is this a billing issue?",
                                      "criteria": {"true": "about charges", "false": "anything else"}}


async def test_yes_no_decided_first_read_has_no_reread():
    t = scripted({"q": noul(0.95)})
    out = await call(t, "yes_no", CLAIM)
    assert len(t.bodies) == 1 and out["meta"]["requests"] == 1


async def test_yes_no_grey_triggers_exactly_one_samples_4_reread():
    t = scripted({"q": noul(0.5)}, {"q": noul(0.93)}, {"q": noul(0.1)})
    out = await call(t, "yes_no", CLAIM)
    assert [b["samples"] for b in t.bodies] == [1, 4]
    assert [k for k in t.bodies[1] if k != "samples"] == [k for k in t.bodies[0] if k != "samples"]
    assert out["decision"] == "yes" and out["p"] == 0.93
    assert out["meta"]["requests"] == 2
    assert out["meta"]["warnings"] == ["grey first read: re-read once with samples 4"]


async def test_yes_no_still_grey_after_reread_is_uncertain_without_a_third_read():
    t = scripted({"q": noul(0.5)}, {"q": noul(0.6)})
    out = await call(t, "yes_no", CLAIM)
    assert len(t.bodies) == 2 and out["decision"] == "uncertain" and out["p"] == 0.6


async def test_yes_no_explicit_samples_disables_reread():
    t = scripted({"q": noul(0.5)}, {"q": noul(0.99)})
    out = await call(t, "yes_no", {**CLAIM, "options": {"samples": 2}})
    assert len(t.bodies) == 1 and t.bodies[0]["samples"] == 2 and out["decision"] == "uncertain"


async def test_yes_no_custom_band_and_criteria():
    t = scripted({"q": noul(0.7)})
    out = await call(t, "yes_no", {**CLAIM, "yes_at": 0.6})
    assert out["decision"] == "yes" and out["thresholds_used"] == {"yes_at": 0.6, "no_at": 0.2}
    assert t.bodies[0]["questions"]["q"]["criteria"] == {"true": "about charges", "false": "anything else"}
    out = await call(scripted({"q": noul(0.7)}), "yes_no", CLAIM, band_yes_at=0.9, band_no_at=0.5)
    assert out["decision"] == "uncertain" and out["thresholds_used"] == {"yes_at": 0.9, "no_at": 0.5}


async def test_yes_no_one_pole_warns_w102_in_meta():
    claim = {k: v for k, v in CLAIM.items() if k != "false_means"}
    out = await call(scripted({"q": noul(0.95)}), "yes_no", claim)
    assert any(w.startswith("W102 questions.q.criteria") for w in out["meta"]["warnings"])
    assert "lint" not in out


async def test_yes_no_inverted_band_rejected():
    err = await error_of(scripted({}), "yes_no", {**CLAIM, "yes_at": 0.4, "no_at": 0.45})
    assert err["code"] == "OJ_INVALID_INPUT"


# classify

LABELS = {"billing": "charges, invoices, refunds", "technical": "bugs, errors, outages",
          "sales": "quotes, upgrades, demos"}
CLS = {"state": "s", "question": "Which team owns this?", "labels": LABELS}


def probs(**kw):
    return kw


async def test_classify_adds_escape_and_picks_label():
    t = scripted({"q": choice("billing", probs(billing=0.9, technical=0.05, sales=0.03, other=0.02))})
    out = await call(t, "classify", CLS)
    crit = t.bodies[0]["questions"]["q"]["criteria"]
    assert list(crit) == ["billing", "technical", "sales", "other"]
    assert crit["other"] == "anything else, or too vague to tell"
    assert out["label"] == "billing" and out["abstained"] is False and "reason" not in out
    assert out["top"] == "billing" and out["runner_up"] == "technical" and out["p_top"] == 0.9
    assert out["margin"] == pytest.approx(0.85) and out["confidence"] == 0.9
    assert out["meta"]["warnings"] == []


@pytest.mark.parametrize("key", ["other", "none", "no_match_here", "not_stated"])
async def test_classify_skips_escape_when_a_label_already_is_one(key):
    labels = {"billing": "charges, invoices, refunds", key: "something else entirely"}
    t = scripted({"q": choice("billing", {"billing": 0.9, key: 0.1})})
    await call(t, "classify", {**CLS, "labels": labels})
    assert list(t.bodies[0]["questions"]["q"]["criteria"]) == ["billing", key]


async def test_classify_escape_win_with_declared_label_abstains():
    labels = {"none_of_these": "nothing fits", "billing": "charges, invoices, refunds"}
    t = scripted({"q": choice("none_of_these", {"none_of_these": 0.9, "billing": 0.1})})
    out = await call(t, "classify", {**CLS, "labels": labels})
    assert out["abstained"] is True and out["label"] is None
    assert out["reason"] == "escape option 'none_of_these' won (p=0.9000)"


async def test_classify_custom_escape():
    t = scripted({"q": choice("unclear", probs(billing=0.1, technical=0.1, sales=0.1, unclear=0.7))})
    out = await call(t, "classify", {**CLS, "escape": {"label": "unclear", "description": "cannot tell"}})
    assert t.bodies[0]["questions"]["q"]["criteria"]["unclear"] == "cannot tell"
    assert out["abstained"] is True and out["reason"] == "escape option 'unclear' won (p=0.7000)"


async def test_classify_escape_false_warns_w201_and_sends_no_escape():
    t = scripted({"q": choice("sales", probs(billing=0.1, technical=0.1, sales=0.8))})
    out = await call(t, "classify", {**CLS, "escape": False})
    assert list(t.bodies[0]["questions"]["q"]["criteria"]) == ["billing", "technical", "sales"]
    assert any(w.startswith("W201 questions.q.criteria") for w in out["meta"]["warnings"])
    assert out["label"] == "sales"


async def test_classify_abstains_below_min_p():
    t = scripted({"q": choice("billing", probs(billing=0.5, technical=0.3, sales=0.1, other=0.1))})
    out = await call(t, "classify", CLS)
    assert out["abstained"] is True and out["label"] is None and out["top"] == "billing"
    assert out["reason"] == "p_top 0.5000 < min_p 0.6"
    out = await call(t, "classify", {**CLS, "min_p": 0.4})
    assert out["abstained"] is False and out["label"] == "billing"
    out = await call(t, "classify", {**CLS, "min_p": 0.55})
    assert out["reason"] == "p_top 0.5000 < min_p 0.55"


async def test_classify_key_outside_the_set_is_protocol_error():
    t = scripted({"q": choice("legal", probs(billing=0.1, technical=0.1, sales=0.1, other=0.1, legal=0.6))})
    err = await error_of(t, "classify", CLS)
    assert err["code"] == "OJ_PROTOCOL" and "legal" in err["message"]


async def test_classify_array_labels_warn_w202():
    t = scripted({"q": choice("billing", probs(billing=0.9, technical=0.05, other=0.05))})
    out = await call(t, "classify", {**CLS, "labels": ["billing", "technical"]})
    assert t.bodies[0]["questions"]["q"]["criteria"]["billing"] == "billing"
    lines = out["meta"]["warnings"]
    assert lines[0].startswith("W202 labels:") and len([w for w in lines if w.startswith("W202")]) == 2


async def test_classify_multi_label_sends_one_noul_per_label():
    t = scripted({"billing": noul(0.9), "technical": noul(0.7), "sales": noul(0.05)})
    out = await call(t, "classify", {**CLS, "multi_label": True})
    qs = t.bodies[0]["questions"]
    assert list(qs) == ["billing", "technical", "sales"] and {q["type"] for q in qs.values()} == {"noul"}
    assert qs["billing"]["instructions"] == "Does the text fit the label 'billing' (charges, invoices, refunds)?"
    assert out["label"] == "billing" and out["abstained"] is False and out["top"] == "billing"
    assert out["runner_up"] == "technical" and out["margin"] == pytest.approx(0.2)
    assert out["probabilities"] == {"billing": 0.9, "technical": 0.7, "sales": 0.05}
    assert out["labels_multi"] == {"billing": {"p": 0.9, "band": "yes"}, "technical": {"p": 0.7, "band": "grey"},
                                   "sales": {"p": 0.05, "band": "no"}}


async def test_classify_multi_label_abstains_when_nothing_fits():
    t = scripted({"billing": noul(0.1), "technical": noul(0.3), "sales": noul(0.05)})
    out = await call(t, "classify", {**CLS, "multi_label": True})
    assert out["abstained"] is True and out["label"] is None and out["reason"] == "p_top 0.3000 < min_p 0.6"


# score

LEVELS = ["fine", "minor", "major"]
SC = {"state": "s", "question": "How severe is it?", "levels": LEVELS}


async def test_score_fields_and_body():
    t = scripted({"q": level(2)})
    out = await call(t, "score", SC)
    assert t.bodies[0]["questions"]["q"] == {"type": "score", "instructions": "How severe is it?", "criteria": LEVELS}
    assert out["score"] == 2.1 and out["level"] == 2 and out["level_label"] == "L2"
    assert out["bimodal"] is False and "type" not in out and out["spread"] >= 0


async def test_score_one_based_shifts_score_and_level():
    t = scripted({"q": level(2)})
    plain = await call(t, "score", SC)
    shifted = await call(t, "score", {**SC, "one_based": True})
    assert shifted["score"] == pytest.approx(plain["score"] + 1) and shifted["level"] == plain["level"] + 1
    assert shifted["level_label"] == plain["level_label"]
    assert shifted["probabilities"] == plain["probabilities"]


async def test_score_label_falls_back_to_the_levels_sent():
    raw = level(1)
    del raw["legend"]
    out = await call(scripted({"q": raw}), "score", SC)
    assert out["level_label"] == "minor"


async def test_score_object_levels_rejected_with_the_e013_hint():
    with pytest.raises(ToolError) as ei:
        score_prepare({**SC, "levels": {"0": "a", "1": "b"}})
    assert ei.value.code == "OJ_INVALID_INPUT" and ei.value.path == "levels"
    assert ei.value.hint == "E013: use a list ordered lowest first"
    t = stubs.fail_on_request_transport()
    err = await error_of(t, "score", {**SC, "levels": {"0": "a", "1": "b"}})
    assert err["code"] == "OJ_INVALID_INPUT" and err["hint"].startswith("E013") and t.requests == []
    assert score_prepare(SC) == (SC, [])


async def test_server_answer_missing_the_key_is_protocol_error():
    err = await error_of(scripted({"other": noul(0.5)}), "yes_no", CLAIM)
    assert err["code"] == "OJ_PROTOCOL" and err["message"] == "response lacks answers.q"


async def test_read_tools_refresh_limits_so_results_do_not_depend_on_call_history():
    app = stubs.openjev_app()
    for _ in range(2):
        t = stubs.asgi_transport(app)
        res = await call_tool(make_ctx(t), "yes_no", {**CLAIM, "options": {"model": "jev-1.13.0"}})
        assert res["isError"] is True
        assert json.loads(res["content"][0]["text"])["error"]["path"] == "model"
