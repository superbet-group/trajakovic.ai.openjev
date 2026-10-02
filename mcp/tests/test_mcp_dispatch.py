"""The tool registry and the tools/call pipeline (arch D.13, E; spec 2.4, 2.5, 6.6)."""
from __future__ import annotations

import json
import logging

import anyio
import httpx
import pytest
import stubs

from openjev_mcp import CORE_TOOL_NAMES, TOOL_NAMES
from openjev_mcp.config import Config
from openjev_mcp.envelope import TEXT_ANNOTATIONS
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS
from openjev_mcp.tools import ToolContext, UnknownTool
from openjev_mcp.tools import dispatch
from openjev_mcp.tools.dispatch import TOOLS, call_tool, tools_for

pytestmark = pytest.mark.anyio

BASE = "http://oj.test:8080"
QS = {"q": {"type": "noul", "instructions": "Is this a billing issue?",
            "criteria": {"true": "about charges", "false": "anything else"}}}
ASK = {"state": "I was charged twice.", "questions": QS}


async def nap(_):
    return None


def make_ctx(transport, **cfg) -> ToolContext:
    config = Config(base_url=BASE, **cfg)
    client = OpenJevClient(config, transport=transport, sleep=nap, jitter=lambda: 0.0)
    return ToolContext(config, client, LimitsCache(client), ProgressEmitter.noop(), None)


def error_of(res: dict) -> dict:
    assert res["isError"] is True and "structuredContent" not in res
    assert len(res["content"]) == 1 and res["content"][0]["type"] == "text"
    assert res["content"][0]["annotations"] == TEXT_ANNOTATIONS
    body = json.loads(res["content"][0]["text"])
    assert list(body) == ["error"]
    return body["error"]


def test_registry_order_titles_and_annotations():
    tools_for("all")   # heals registry entries popped by the per-tool tests
    assert tuple(t.name for t in TOOLS) == TOOL_NAMES == (
        "ask", "yes_no", "classify", "score", "filter", "batch", "ask_image", "lint", "compile", "calibrate", "recipe",
        "status", "generate", "batch_results")
    titles = {t.name: t.title for t in TOOLS}
    assert [titles[n] for n in CORE_TOOL_NAMES] == ["Ask OpenJev", "Yes/no claim", "Classify", "Score on a scale",
                                                    "Lint a request", "OpenJev status"]
    for t in TOOLS:
        # deviation: P19: equality, not identity; the per-tool tests call register() again and replace the registry dicts
        assert t.input_schema == INPUT_SCHEMAS[t.name] and t.output_schema == OUTPUT_SCHEMAS[t.name]
        assert t.annotations["title"] == t.title and t.annotations["openWorldHint"] is False
        if t.name in CORE_TOOL_NAMES:
            assert "destructiveHint" not in t.annotations
            assert t.annotations == {"title": t.title, "readOnlyHint": True, "idempotentHint": t.name != "ask",
                                     "openWorldHint": False}
        assert 0 < len(t.description) < 600 and callable(t.handler)
    assert {t.name for t in TOOLS if t.prepare} >= {"classify", "score"}
    assert "no network" in next(t for t in TOOLS if t.name == "lint").description.lower()


def test_tools_for_all_is_phase_two_and_core_is_the_six_in_order():
    assert tuple(t.name for t in tools_for("all")) == TOOL_NAMES
    assert tuple(t.name for t in tools_for("core")) == CORE_TOOL_NAMES == (
        "ask", "yes_no", "classify", "score", "lint", "status")
    assert tuple(t.name for t in tools_for(Config(base_url=BASE, toolsets="core"))) == CORE_TOOL_NAMES
    assert tools_for("all") is tools_for("all")   # built once per process


async def test_unknown_tool_raises():
    t = stubs.fail_on_request_transport()
    with pytest.raises(UnknownTool, match="nope"):
        await call_tool(make_ctx(t), "nope", {})
    assert t.requests == []


@pytest.mark.parametrize("tool,args,path", [
    ("ask", {"state": "s"}, "arguments"),
    ("ask", {"questions": QS}, "arguments"),
    ("ask", {"state": 5, "questions": QS}, "state"),
    ("ask", {"state": "s", "questions": QS, "lint": "maybe"}, "lint"),
    ("ask", {"state": "s", "questions": QS, "extra": 1}, "arguments"),
    ("yes_no", {"state": "s", "claim": "no"}, "claim"),
    ("score", {"state": "s", "question": "How bad is it?", "levels": ["only one"]}, "levels"),
    ("classify", {"state": "s", "question": "Which team?", "labels": "billing"}, "labels"),
    ("lint", {"emit": ["pdf"]}, "emit.0"),
])
async def test_invalid_arguments_are_error_envelopes_never_exceptions(tool, args, path):
    t = stubs.fail_on_request_transport()
    err = error_of(await call_tool(make_ctx(t), tool, args))
    assert err["code"] == "OJ_INVALID_INPUT" and err["path"] == path and err["retryable"] is False
    assert err["message"] and err["hint"].startswith("expected: ")
    assert t.requests == []


async def test_arguments_none_and_non_object():
    t = stubs.fail_on_request_transport()
    assert error_of(await call_tool(make_ctx(t), "ask", None))["code"] == "OJ_INVALID_INPUT"
    assert error_of(await call_tool(make_ctx(t), "classify", ["x"]))["code"] == "OJ_INVALID_INPUT"
    assert error_of(await call_tool(make_ctx(t), "score", "x"))["path"] == "arguments"


async def test_classify_labels_array_becomes_object_with_w202_in_meta():
    app = stubs.openjev_app(engine=stubs.StubEngine())
    ctx = make_ctx(stubs.asgi_transport(app))
    args = {"state": "s", "question": "Which team owns this?", "labels": ["billing", "technical"]}
    original = json.loads(json.dumps(args))
    res = await call_tool(ctx, "classify", args)
    assert res["isError"] is False and args == original
    out = res["structuredContent"]
    assert any(w.startswith("W202 labels:") for w in out["meta"]["warnings"])
    assert [w.code for w in ctx.warnings] == ["W202"]
    assert app.state.engine.calls[0]["questions"]["q"]["criteria"] == {
        "billing": "billing", "technical": "technical", "other": "anything else, or too vague to tell"}


async def test_warnings_do_not_leak_between_calls():
    app = stubs.openjev_app()
    ctx = make_ctx(stubs.asgi_transport(app))
    await call_tool(ctx, "classify", {"state": "s", "question": "Which team?", "labels": ["a", "b"]})
    assert ctx.warnings
    res = await call_tool(ctx, "ask", ASK)
    assert ctx.warnings == [] and res["structuredContent"]["meta"]["warnings"] == []


async def test_score_object_levels_error_envelope():
    err = error_of(await call_tool(make_ctx(stubs.fail_on_request_transport()), "score", {
        "state": "s", "question": "How bad is it?", "levels": {"0": "a", "1": "b"}}))
    assert err["code"] == "OJ_INVALID_INPUT" and err["path"] == "levels" and err["hint"].startswith("E013")


async def test_success_envelope_through_the_real_app():
    ctx = make_ctx(stubs.asgi_transport(stubs.openjev_app()))
    res = await call_tool(ctx, "ask", ASK)
    assert res["isError"] is False
    assert res["content"] == [{"type": "text", "text": json.dumps(res["structuredContent"], separators=(",", ":"),
                                                                  ensure_ascii=False),
                               "annotations": TEXT_ANNOTATIONS}]
    assert res["structuredContent"]["answers"]["q"]["band"] == "yes"


async def test_lint_and_status_run_through_the_pipeline():
    res = await call_tool(make_ctx(stubs.fail_on_request_transport()), "lint", {"questions": QS, "state": "s"})
    assert res["isError"] is False and res["structuredContent"]["valid"] is True
    err = error_of(await call_tool(make_ctx(stubs.fail_on_request_transport()), "lint", {}))
    assert err["code"] == "OJ_INVALID_INPUT"


async def test_handler_exception_becomes_oj_internal(monkeypatch, caplog):
    async def boom(ctx, args):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(dispatch, "_BY_NAME", {**dispatch._BY_NAME, "yes_no": dispatch._BY_NAME["yes_no"].__class__(
        **{**dispatch._BY_NAME["yes_no"].__dict__, "handler": boom})})
    with caplog.at_level(logging.ERROR, logger="openjev_mcp"):
        res = await call_tool(make_ctx(stubs.fail_on_request_transport()), "yes_no",
                              {"state": "s", "claim": "Is this fine?"})
    err = error_of(res)
    assert err["code"] == "OJ_INTERNAL" and err["message"] == "internal error in openjev-mcp: RuntimeError"
    assert "secret detail" not in json.dumps(res)
    assert "Traceback" in caplog.text and "secret detail" in caplog.text


async def test_handler_tool_error_passes_through(monkeypatch):
    from openjev_mcp.errors import ToolError

    async def refuse(ctx, args):
        raise ToolError("OJ_REJECTED", "no", http_status=400, hint="h", request_id="req_x")

    spec = dispatch._BY_NAME["yes_no"]
    monkeypatch.setattr(dispatch, "_BY_NAME", {**dispatch._BY_NAME, "yes_no": spec.__class__(
        **{**spec.__dict__, "handler": refuse})})
    err = error_of(await call_tool(make_ctx(stubs.fail_on_request_transport()), "yes_no",
                                   {"state": "s", "claim": "Is this fine?"}))
    assert err == {"code": "OJ_REJECTED", "message": "no", "http_status": 400, "path": None, "retryable": False,
                   "retry_after_s": None, "request_id": "req_x", "hint": "h"}


# error envelopes through the whole pipeline (spec 6.6 error matrix)

async def test_401_envelope():
    app = stubs.openjev_app(api_key="right")
    ctx = make_ctx(stubs.asgi_transport(app), api_key="wrong")
    err = error_of(await call_tool(ctx, "ask", ASK))
    assert err["code"] == "OJ_AUTH" and err["http_status"] == 401 and err["retryable"] is False
    assert err["request_id"] and "OPENJEV_API_KEY" in err["message"] + err.get("hint", "")
    assert app.state.engine.calls == []


async def test_529_after_retries_envelope():
    body = b'{"detail":{"error_type":"overloaded_error","message":"busy"}}'
    t = stubs.fault_transport(529, headers={"retry-after": "1", "content-type": "application/json",
                                            "x-request-id": "req_529"}, body=body)
    err = error_of(await call_tool(make_ctx(t, retries=2), "yes_no", {"state": "s", "claim": "Is this fine?"}))
    assert err["code"] == "OJ_OVERLOADED" and err["http_status"] == 529 and err["retryable"] is True
    assert err["retry_after_s"] == 1 and err["request_id"] == "req_529"
    assert len([r for r in t.requests if r.method == "POST"]) == 3


async def test_connect_refused_envelope():
    t = stubs.fault_transport(exc=httpx.ConnectError("refused"))
    err = error_of(await call_tool(make_ctx(t, retries=1), "ask", ASK))
    assert err["code"] == "OJ_UNREACHABLE" and err["retryable"] is True and err["http_status"] is None
    assert BASE in err["message"] and len([r for r in t.requests if r.method == "POST"]) == 2


async def test_missing_answer_key_envelope():
    t = stubs.fault_transport(200, headers={"content-type": "application/json"},
                              body=json.dumps({"model": "m", "answers": {}, "usage": {}}))
    err = error_of(await call_tool(make_ctx(t), "ask", ASK))
    assert err["code"] == "OJ_PROTOCOL" and err["message"] == "response lacks answers.q"


# cancellation

async def test_cancellation_propagates_and_returns_no_result():
    t = stubs.fault_transport(200, delay_s=5, headers={"content-type": "application/json"}, body=b"{}")
    ctx = make_ctx(t)
    results = []

    async def run():
        results.append(await call_tool(ctx, "ask", ASK))

    with anyio.fail_after(3):
        async with anyio.create_task_group() as tg:
            tg.start_soon(run)
            await anyio.sleep(0.1)
            tg.cancel_scope.cancel()
    assert results == [] and len(t.requests) == 1
    assert ctx.client._sem.value == ctx.config.max_inflight


async def test_cancelled_call_inside_scope_does_not_become_internal_error():
    t = stubs.fault_transport(200, delay_s=5, headers={"content-type": "application/json"}, body=b"{}")
    with anyio.move_on_after(0.1) as scope:
        res = await call_tool(make_ctx(t), "yes_no", {"state": "s", "claim": "Is this fine?"})
        pytest.fail(f"call_tool returned {res}")
    assert scope.cancelled_caught


async def test_new_tools_run_through_call_tool(tmp_path):
    t = stubs.fail_on_request_transport()
    ctx = make_ctx(t, roots=(str(tmp_path.resolve()),))
    res = await call_tool(ctx, "recipe", {"recipe": "command_gate", "dry_run": True,
                                          "inputs": {"task": "Fix the test.", "command": "pnpm test"}})
    assert res["isError"] is False and res["structuredContent"]["decision"] == "dry_run"
    bad = await call_tool(ctx, "filter", {"task": "t"})
    assert error_of(bad)["code"] == "OJ_INVALID_INPUT"
    res = await call_tool(ctx, "batch", {"items": [{"state": "s"}], "questions": QS, "dry_run": True})
    assert res["isError"] is False and res["structuredContent"]["status"]["stopped_reason"] == "dry_run"
    assert t.requests == []


def test_core_toolset_filters_in_the_same_order():
    cfg = Config(base_url=BASE, toolsets="core")
    assert [t.name for t in tools_for(cfg)] == [n for n in TOOL_NAMES if n in CORE_TOOL_NAMES]
