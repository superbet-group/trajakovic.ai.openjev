"""Argument validation against the published input schemas, and the spec replays against the outputs."""
from __future__ import annotations

import json

import pytest

from openjev_mcp import CORE_TOOL_NAMES
from openjev_mcp.validate import validate_args, validate_output

SPEC_CALLS = {
    "ask": {
        "state": "Everything is down and we have a demo with our biggest client at noon.",
        "questions": {
            "urgent": {"type": "noul", "instructions": "Does the customer need a reply within the hour?"},
            "team": {"type": "choice", "instructions": "Which team should handle it?", "criteria": {"outage": "service down", "billing": "charges, refunds", "feature": "requests, how-to"}},
            "tone": {"type": "score", "instructions": "How upset is the customer?", "criteria": ["calm", "annoyed", "furious"]}}},
    "yes_no": {"state": "I was charged twice this month.", "claim": "Is this a billing issue?",
               "true_means": "about charges, refunds, invoices", "false_means": "anything else"},
    "classify": {"state": "Please help", "question": "Which team should own this ticket?",
                 "labels": {"billing": "charges, invoices, refunds, payment methods, plan pricing",
                            "technical": "bugs, errors, outages, integrations, performance, login problems",
                            "sales": "pre-purchase questions, quotes, upgrades, demos, enterprise pricing"}},
    "score": {
        "state": "ALERT prod-eu: primary Postgres refused all connections for 12 minutes; 100% of POST /charge requests returned 500; on-call not yet acknowledged.",
        "question": "How severe is the problem shown in this log or alert?",
        "levels": ["routine noise, no action", "minor, worth watching", "real problem, needs a human this week",
                   "service degraded or broken for users, needs a human now", "outage or data loss, page immediately"]},
    "lint": {"request": {"model": "openjev-latest", "state": "Checkout is down for every customer.",
                         "questions": {"sev": {"type": "score", "instructions": "How severe is this?", "criteria": {"0": "minor", "1": "major", "2": "critical"}},
                                       "team": {"type": "choice", "instructions": "Which team?", "criteria": {"payments": "payments", "platform": "platform"}}}}},
}

SPEC_OUTPUTS = {
    "ask": {
        "answers": {
            "urgent": {"type": "noul", "p": 0.997, "band": "yes", "margin": 0.9941},
            "team": {"type": "choice", "choice": "outage", "p_top": 1.0, "runner_up": "billing", "margin": 1.0, "probabilities": {"outage": 1.0, "billing": 3.3e-06, "feature": 6.1e-07}, "confidence": 1.0, "entropy": 5.5e-05, "abstained": False},
            "tone": {"type": "score", "score": 1.9997, "level": 2, "level_label": "furious", "probabilities": {"0": 8e-05, "1": 0.00017, "2": 0.9997}, "confidence": 0.9977, "spread": 0.0221, "bimodal": False}},
        "meta": {"model": "openjev-0.1", "request_ids": ["req_dd8fa2d300b8f75c7d6ec8e89e51081b"], "requests": 1,
                 "latency_ms": 1894, "server_ms": 1886.3, "input_tokens": 165, "output_tokens": 0, "warnings": []},
        "lint": {"warnings": []}},
    "yes_no": {
        "decision": "yes", "p": 0.9995, "margin": 0.9989, "thresholds_used": {"yes_at": 0.8, "no_at": 0.2},
        "meta": {"model": "openjev-0.1", "request_ids": ["req_b2b5c9d9ba1486c129c1d7a6e32fa581"], "requests": 1,
                 "latency_ms": 44, "server_ms": 43.6, "input_tokens": 95, "output_tokens": 0, "warnings": []}},
    "classify": {
        "label": None, "abstained": True, "reason": "escape option 'other' won (p=0.9996)", "top": "other",
        "p_top": 0.9996, "runner_up": "technical", "margin": 0.9992,
        "probabilities": {"billing": 5.8e-05, "technical": 0.00034, "sales": 8.2e-06, "other": 0.9996},
        "confidence": 0.9972,
        "meta": {"model": "openjev-0.1", "request_ids": ["req_50d508b7bfe524ed27d55d5c6f8ef5ed"], "requests": 1,
                 "latency_ms": 172, "server_ms": 171.1, "input_tokens": 147, "output_tokens": 0, "warnings": []}},
    "score": {
        "score": 3.9365, "level": 4, "level_label": "outage or data loss, page immediately",
        "probabilities": {"0": 0.00017, "1": 0.0011, "2": 0.0022, "3": 0.0551, "4": 0.9414},
        "confidence": 0.8515, "spread": 0.2696, "bimodal": False,
        "meta": {"model": "openjev-0.1", "request_ids": ["req_8037e14ecb431dcd2475a93651ee1ba2"], "requests": 1,
                 "latency_ms": 171, "server_ms": 170.4, "input_tokens": 172, "output_tokens": 0, "warnings": []}},
    "lint": {
        "valid": False,
        "errors": [{"code": "E013", "path": "questions.sev.criteria", "message": "score criteria is an object; the server returns 422 'Input should be a valid list'", "fix": "use a list ordered lowest first", "autofixed": True}],
        "warnings": [
            {"code": "W304", "path": "questions.sev.criteria", "message": "levels 'minor/major/critical' carry no observable evidence", "fix": "describe each level, e.g. 'critical: outage or data loss for all users'", "rule": "R9"},
            {"code": "W202", "path": "questions.team.criteria", "message": "option descriptions equal their keys", "fix": "describe what inputs of each option look like", "rule": "R6"},
            {"code": "W201", "path": "questions.team.criteria", "message": "no escape option; a choice cannot abstain", "fix": "add \"other\": \"anything else, or too vague to tell\"", "rule": "R5"},
            {"code": "W106", "path": "questions.team.instructions", "message": "'Which team?' does not say for what", "fix": "'Which team should own this?'", "rule": "R2"}],
        "fixed_request": {"model": "openjev-latest", "state": "Checkout is down for every customer.",
                          "questions": {"sev": {"type": "score", "instructions": "How severe is this?", "criteria": ["minor", "major", "critical"]},
                                        "team": {"type": "choice", "instructions": "Which team?", "criteria": {"payments": "payments", "platform": "platform", "other": "anything else, or too vague to tell"}}}},
        "estimate": {"questions": 2, "chunks": 1, "input_tokens_approx": 160, "latency_ms_idle_approx": 300, "billed_reads": 1}},
    "status": {
        "healthy": True, "base_url": "http://127.0.0.1:8080",
        "decide_models": ["openjev-latest", "openjev-0.1"], "aliases_accepted": ["jev-latest", "jev-preview"],
        "chat_models": ["diffusiongemma-26b"], "resolved": {"openjev-latest": "openjev-0.1"},
        "auth": "none", "backend": "unknown", "limit_source": "default", "latency_probe_ms": 44,
        "limits": {"questions": 256, "choice_options": 255, "score_levels": 10, "images": 8, "image_bytes": 5242880,
                   "prompt_tokens": 32768, "body_bytes": 67108864},
        "warnings": ["GET /v1/limits not available (404): limits are the documented defaults, limit-dependent lint findings are warnings, backend unknown"]},
}

MINIMAL = {
    "ask": {"state": "s", "questions": {"q": {"type": "noul", "instructions": "abc"}}},
    "yes_no": {"state": "s", "claim": "abc"},
    "classify": {"state": "s", "question": "abc", "labels": {"a": "x"}},
    "score": {"state": "s", "question": "abc", "levels": ["a", "b"]},
    "lint": {},
    "status": {},
}


def test_fixtures_cover_every_tool():
    assert set(MINIMAL) == set(SPEC_OUTPUTS) == set(CORE_TOOL_NAMES)


@pytest.mark.parametrize("tool", CORE_TOOL_NAMES)
def test_minimal_args_valid(tool):
    assert validate_args(tool, MINIMAL[tool]) is None


@pytest.mark.parametrize("tool", ["lint", "status"])
def test_none_args_are_empty_object(tool):
    assert validate_args(tool, None) is None


def test_none_args_missing_required():
    err = validate_args("ask", None)
    assert err.code == "OJ_INVALID_INPUT" and err.path == "arguments" and "state" in err.message


@pytest.mark.parametrize("tool", sorted(SPEC_CALLS))
def test_spec_example_calls_valid(tool):
    assert validate_args(tool, SPEC_CALLS[tool]) is None


@pytest.mark.parametrize("tool", CORE_TOOL_NAMES)
def test_spec_example_outputs_valid(tool):
    assert validate_output(tool, SPEC_OUTPUTS[tool]) == []


def test_status_probe_and_1_2_fields():
    assert validate_args("status", {"probe": True}) is None
    out = dict(SPEC_OUTPUTS["status"], capabilities={"openjev-0.1": {"images": True, "steps": True, "samples": True, "think": True, "sequential": True, "max_prompt_tokens": None, "max_choices": 255}},
               mcp={"server_version": "1.3.0", "protocol_versions": ["2026-07-28"], "batch_max_inflight": 4})
    assert validate_output("status", out) == []
    assert validate_args("lint", {"questions": {}, "emit": ["body", "curl", "python"]}) is None
    meta = dict(SPEC_OUTPUTS["yes_no"]["meta"], body_hashes=["sha256:ab"], server_timing={"model_ms": 1, "server_ms": 2, "total_ms": 3}, timeout_ms_used=30000, chunks_estimate=1)
    assert validate_output("yes_no", dict(SPEC_OUTPUTS["yes_no"], meta=meta)) == []


def test_output_errors_reported():
    errs = validate_output("yes_no", {"decision": "maybe", "p": 1})
    assert errs and any(e.startswith("decision:") for e in errs)
    assert any("required" in e for e in errs)


def invalid(tool, args):
    err = validate_args(tool, args)
    assert err is not None and err.code == "OJ_INVALID_INPUT" and err.retryable is False
    assert err.hint.startswith("expected: ") and len(err.hint) <= len("expected: ") + 1024
    return err


@pytest.mark.parametrize("tool,args,path", [
    ("ask", [1], "arguments"),
    ("ask", "text", "arguments"),
    ("ask", {"questions": MINIMAL["ask"]["questions"]}, "arguments"),
    ("ask", {**MINIMAL["ask"], "unknown": 1}, "arguments"),
    ("status", {"x": 1}, "arguments"),
    ("ask", {**MINIMAL["ask"], "questions": {"team": {"type": "choice", "instructions": "abc", "criteria": ["a", "b"]}}}, "questions.team.criteria"),
    ("ask", {**MINIMAL["ask"], "questions": {"t": {"type": "noul", "instructions": "abc", "criteria": {"yes": "a"}}}}, "questions.t.criteria"),
    ("ask", {**MINIMAL["ask"], "questions": {"t": {"type": "choice", "instructions": "abc", "criteria": {"a": "x"}}}}, "questions.t.criteria"),
    ("ask", {**MINIMAL["ask"], "questions": {"t": {"type": "nope", "instructions": "abc"}}}, "questions.t"),
    ("ask", {**MINIMAL["ask"], "questions": {"bad id": {"type": "noul", "instructions": "abc"}}}, "questions"),
    ("ask", {**MINIMAL["ask"], "questions": {}}, "questions"),
    ("ask", {**MINIMAL["ask"], "options": {"samples": 33}}, "options.samples"),
    ("ask", {**MINIMAL["ask"], "options": {"think": "x"}}, "options.think"),
    ("ask", {**MINIMAL["ask"], "lint": "loud"}, "lint"),
    ("yes_no", {"state": 5, "claim": "abc"}, "state"),
    ("yes_no", {"state": "s", "claim": "no"}, "claim"),
    ("classify", {**MINIMAL["classify"], "labels": ["a"]}, "labels"),
    ("classify", {**MINIMAL["classify"], "labels": {}}, "labels"),
    ("classify", {**MINIMAL["classify"], "escape": {"label": "x"}}, "escape"),
    ("score", {**MINIMAL["score"], "levels": {"0": "a", "1": "b"}}, "levels"),
    ("score", {**MINIMAL["score"], "levels": ["a"]}, "levels"),
    ("score", {**MINIMAL["score"], "levels": ["a", 3]}, "levels.1"),
    ("lint", {"emit": ["body", "body"]}, "emit"),
    ("lint", {"profile": "loose"}, "profile"),
])
def test_invalid_args_path(tool, args, path):
    assert invalid(tool, args).path == path


def test_classify_escape_false_valid():
    assert validate_args("classify", {**MINIMAL["classify"], "escape": False}) is None
    assert validate_args("classify", {**MINIMAL["classify"], "escape": True}) is not None


def test_hint_is_failing_subschema_and_capped():
    err = invalid("score", {**MINIMAL["score"], "levels": ["a"]})
    assert json.loads(err.hint[len("expected: "):])["minItems"] == 2
    big = invalid("ask", {"questions": {}})
    assert big.hint.endswith("...") and len(big.hint) == len("expected: ") + 1024


def test_message_capped():
    err = invalid("yes_no", {"state": "s", "claim": "x" * 5000, "yes_at": "y" * 5000})
    assert len(err.message) <= 500


def test_pure():
    args = json.loads(json.dumps(SPEC_CALLS["ask"]))
    before = json.dumps(args)
    validate_args("ask", args)
    assert json.dumps(args) == before
