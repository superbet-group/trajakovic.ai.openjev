"""Replays of the spec examples through call_tool (spec 6.6 Replay, 6.7, arch G.3)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
import stubs

from openjev_mcp.config import Config
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools.dispatch import call_tool
from openjev_mcp.validate import validate_output

pytestmark = pytest.mark.anyio

CASES, CAPTURED = stubs._load_cases()
CASE = {c["id"]: c for c in CASES}


def r4(obj: Any) -> Any:
    if isinstance(obj, float):
        return round(obj, 4)
    if isinstance(obj, dict):
        return {k: r4(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [r4(v) for v in obj]
    return obj


def ctx_for(transport) -> ToolContext:
    config = Config(base_url="http://oj.test:8080")
    client = OpenJevClient(config, transport=transport)
    return ToolContext(config, client, LimitsCache(client), ProgressEmitter.noop(), None)


def meta_of(case_id: str, **extra) -> dict:
    cap = CAPTURED[case_id]
    return {"model": "openjev-0.1", "request_ids": [cap["request_id"]], "requests": 1,
            "input_tokens": cap["body"]["usage"]["input_tokens"],
            "output_tokens": cap["body"]["usage"]["output_tokens"], **extra}


async def replay(case_id: str, tool: str, args: dict) -> tuple[dict, dict, Any]:
    transport = stubs.replay_transport([case_id])
    res = await call_tool(ctx_for(transport), tool, args)
    assert res["isError"] is False, res
    out = res["structuredContent"]
    assert validate_output(tool, out) == []
    assert len(res["content"]) == 1 and res["content"][0]["type"] == "text"
    assert json.loads(res["content"][0]["text"]) == out
    assert res["content"][0]["annotations"] == {"audience": ["assistant"]}
    posts = [r for r in transport.requests if r.method == "POST"]
    assert len(posts) == 1
    cap = CAPTURED[case_id]
    meta = out["meta"]
    assert isinstance(meta["latency_ms"], (int, float))
    assert meta["request_ids"] == [cap["request_id"]]
    assert meta["body_hashes"] == ["sha256:" + hashlib.sha256(posts[0].content).hexdigest()]
    assert meta["server_timing"]["total_ms"] == pytest.approx(float(cap["server_timing"].split("total;dur=")[1]))
    assert meta["server_ms"] == meta["server_timing"]["total_ms"]
    assert meta["timeout_ms_used"] >= 30000 and meta["chunks_estimate"] >= 1
    return out, meta, transport


def body_args(case_id: str) -> dict:
    req = CASE[case_id]["request"]
    options = {k: req[k] for k in ("think", "sequential", "samples") if k in req}
    args = {"state": req["state"], "questions": req["questions"]}
    return {**args, "options": options} if options else args


def expect(out: dict, expected: dict) -> None:
    got = {k: v for k, v in out.items() if k not in ("meta", "lint")}
    assert r4(got) == r4({k: v for k, v in expected.items() if k != "meta"})
    meta = out["meta"]
    for key, value in expected["meta"].items():
        assert r4(meta[key]) == r4(value), key


async def test_ex_ask():
    out, meta, _ = await replay("ex-ask", "ask", body_args("ex-ask"))
    expect(out, {
        "answers": {
            "urgent": {"type": "noul", "p": 0.997, "band": "yes", "margin": 0.9941},
            "team": {"type": "choice", "choice": "outage", "p_top": 1.0, "runner_up": "billing", "margin": 1.0,
                     "probabilities": {"outage": 1.0, "billing": 3.3e-06, "feature": 6.1e-07}, "confidence": 1.0,
                     "entropy": 5.5e-05, "abstained": False},
            "tone": {"type": "score", "score": 1.9997, "level": 2, "level_label": "furious",
                     "probabilities": {"0": 8e-05, "1": 0.00017, "2": 0.9997}, "confidence": 0.9977,
                     "spread": 0.0221, "bimodal": False}},
        "meta": {**meta_of("ex-ask", server_ms=1886.3), "warnings": []}})
    assert list(out["answers"]) == ["urgent", "team", "tone"]
    assert "W101" in [w["code"] for w in out["lint"]["warnings"]]
    assert meta["server_timing"] == {"model_ms": 0.0, "server_ms": 1886.3, "total_ms": 1886.3}


async def test_ex_think():
    out, meta, _ = await replay("ex-think", "ask", body_args("ex-think"))
    answer = out["answers"]["allowed"]
    assert r4(answer) == {"type": "noul", "p": 0.0, "band": "no", "margin": 1.0}
    assert answer["p"] == pytest.approx(2.1362151537290525e-06)
    assert meta["output_tokens"] == 235 and meta["input_tokens"] == 648
    assert meta["timeout_ms_used"] == 120000
    assert meta["warnings"] == []


async def test_ex_sequential():
    out, meta, _ = await replay("ex-sequential", "ask", body_args("ex-sequential"))
    assert list(out["answers"]) == ["verb", "env", "wait"]
    assert r4(out["answers"]["verb"]) == {
        "type": "choice", "choice": "scale", "p_top": 0.9993, "runner_up": "rollback", "margin": 0.9987,
        "probabilities": {"restart": 0.0001, "scale": 0.9993, "rollback": 0.0006, "logs": 0.0, "delete": 0.0},
        "confidence": 0.996, "entropy": 0.0064, "abstained": False}
    assert out["answers"]["env"]["choice"] == "prod" and out["answers"]["env"]["abstained"] is False
    assert r4(out["answers"]["wait"]) == {"type": "noul", "p": 0.9999, "band": "yes", "margin": 0.9999}
    assert meta["warnings"] == []
    w403 = [w for w in out["lint"]["warnings"] if w["code"] == "W403"]
    assert len(w403) == 1 and "3 noul/choice" in w403[0]["message"]


async def test_ex_yes_no():
    out, _, transport = await replay("ex-yes-no", "yes_no", {
        "state": "I was charged twice this month.", "claim": "Is this a billing issue?",
        "true_means": "about charges, refunds, invoices", "false_means": "anything else"})
    expect(out, {"decision": "yes", "p": 0.9995, "margin": 0.9989, "thresholds_used": {"yes_at": 0.8, "no_at": 0.2},
                 "meta": {**meta_of("ex-yes-no", server_ms=43.6), "warnings": []}})
    assert json.loads(transport.requests[-1].content)["samples"] == 1


async def test_ex_classify_abstain():
    out, _, _ = await replay("ex-classify-abstain", "classify", {
        "state": "Please help", "question": "Which team should own this ticket?",
        "labels": {"billing": "charges, invoices, refunds, payment methods, plan pricing",
                   "technical": "bugs, errors, outages, integrations, performance, login problems",
                   "sales": "pre-purchase questions, quotes, upgrades, demos, enterprise pricing"}})
    expect(out, {
        "label": None, "abstained": True, "reason": "escape option 'other' won (p=0.9996)", "top": "other",
        "p_top": 0.9996, "runner_up": "technical", "margin": 0.9992,
        "probabilities": {"billing": 5.8e-05, "technical": 0.00034, "sales": 8.2e-06, "other": 0.9996},
        "confidence": 0.9972,
        "meta": {**meta_of("ex-classify-abstain", server_ms=171.1), "warnings": []}})
    assert out["label"] is None


async def test_ex_score():
    out, _, _ = await replay("ex-score", "score", {
        "state": CASE["ex-score"]["request"]["state"],
        "question": "How severe is the problem shown in this log or alert?",
        "levels": CASE["ex-score"]["request"]["questions"]["q"]["criteria"]})
    expect(out, {
        "score": 3.9365, "level": 4, "level_label": "outage or data loss, page immediately",
        "probabilities": {"0": 0.00017, "1": 0.0011, "2": 0.0022, "3": 0.0551, "4": 0.9414},
        "confidence": 0.8515, "spread": 0.2696, "bimodal": False,
        "meta": {**meta_of("ex-score", server_ms=170.4), "warnings": []}})
