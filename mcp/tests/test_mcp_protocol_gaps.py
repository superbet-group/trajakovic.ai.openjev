"""2026-07-28 protocol surface (P02): extensions, prompts, completion, request _meta, resource_link, errors."""
from __future__ import annotations

import json
import os
import re
from dataclasses import replace

import mcp_types as types
import pytest
import rpc
import stubs
from mcp.server.extension import Extension, MethodBinding

from openjev_mcp import completion, prompts, resources
from openjev_mcp.config import load_config
from openjev_mcp.envelope import file_link, success_result
from openjev_mcp.server import build_server
from openjev_mcp.tools import dispatch

MODES = ("2026-07-28", "legacy")
YES_NO = {"state": "I was charged twice.", "claim": "Is this a billing issue?",
          "true_means": "about charges", "false_means": "anything else"}


def server(*, extensions=None, **kw):
    cfg = replace(load_config({}, transport="http"), retries=0, **kw)
    app = stubs.openjev_app(engine=stubs.StubEngine(answers=stubs.default_answers))
    return build_server(cfg, transport=stubs.asgi_transport(app), warm_limits=False, extensions=extensions)


async def open_session(s, mode):
    """Returns a request function with the right envelope for the era."""
    if mode == "legacy":
        res = await s.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                             "clientInfo": {"name": "t", "version": "1"}}, envelope=False)
        assert "result" in res
        await s.notify("notifications/initialized")
        return lambda m, p=None: s.request(m, p, envelope=False)
    return lambda m, p=None: s.request(m, p)


async def capabilities(s, mode) -> dict:
    if mode == "legacy":
        res = await s.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                             "clientInfo": {"name": "t", "version": "1"}}, envelope=False)
        return res["result"]["capabilities"]
    return (await s.request("server/discover"))["result"]["capabilities"]


class Dummy(Extension):
    identifier = "io.example/dummy"

    def __init__(self):
        self.calls: list[str] = []

        async def ping(ctx, params):
            return {"pong": True}

        self._ping = ping

    def settings(self):
        return {"flavour": "test"}

    def methods(self):
        return [MethodBinding("io.example/ping", types.RequestParams, self._ping)]

    async def intercept_tool_call(self, params, ctx, call_next):
        self.calls.append(params.name)
        res = await call_next(ctx)
        return res


@pytest.fixture
def registries():
    snap = (list(prompts.PROMPTS), dict(completion.COMPLETERS))
    yield
    prompts.PROMPTS[:] = snap[0]
    completion.COMPLETERS.clear()
    completion.COMPLETERS.update(snap[1])


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_default_capabilities_have_no_extensions(mode):
    async with rpc.rpc_session(server()) as s:
        caps = await capabilities(s, mode)
    assert set(caps) - {"experimental"} == {"tools", "resources", "prompts", "completions"}   # legacy adds experimental {}
    assert "extensions" not in caps
    assert caps["prompts"] == {"listChanged": False} and caps["completions"] == {}


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_dummy_extension_is_advertised_answers_and_wraps_tools_call(mode):
    dummy = Dummy()
    async with rpc.rpc_session(server(extensions=[dummy])) as s:
        if mode == "legacy":
            caps = await capabilities(s, mode)
            await s.notify("notifications/initialized")
            req = lambda m, p=None: s.request(m, p, envelope=False)   # noqa: E731
        else:
            caps = await capabilities(s, mode)
            req = s.request
        pong = await req("io.example/ping")
        call = await req("tools/call", {"name": "status", "arguments": {}})
    if mode == "2026-07-28":
        assert caps["extensions"] == {"io.example/dummy": {"flavour": "test"}}
    else:   # the SDK's 2025-11-25 wire schema has no capabilities.extensions: advertised on 2026-07-28 only
        assert "extensions" not in caps
    assert pong["result"]["pong"] is True
    assert dummy.calls == ["status"] and call["result"]["isError"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_prompts_list_empty_with_cache_fields(mode):
    async with rpc.rpc_session(server()) as s:
        req = await open_session(s, mode)
        res = (await req("prompts/list"))["result"]
        tpl = (await req("resources/templates/list"))["result"]
    assert [x["name"] for x in res["prompts"]] == ["start_batch", "review_batch", "author_question", "audit_question", "explain_answer"]
    assert [t["uriTemplate"] for t in tpl["resourceTemplates"]] == ["openjev://recipes/{id}", "openjev://templates/{id}", "openjev://audits/{question_hash}"]
    if mode == "2026-07-28":
        assert (res["ttlMs"], res["cacheScope"]) == (3600000, "public")


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_prompt_and_completer_round_trip(mode, registries):
    async def build(args, ctx):
        return [{"role": "user", "content": {"type": "text", "text": f"hello {args['who']}"}}]

    prompts.PROMPTS.append(prompts.PromptSpec("greet", "Greet", "Say hello.",
                                              (prompts.PromptArg("who", "name", True, True),), build))
    completion.COMPLETERS[("ref/prompt", "greet", "who")] = lambda v, ctx: ["ann", "anna", "bob"]
    async with rpc.rpc_session(server()) as s:
        req = await open_session(s, mode)
        listed = (await req("prompts/list"))["result"]["prompts"]
        got = (await req("prompts/get", {"name": "greet", "arguments": {"who": "ann"}}))["result"]
        done = (await req("completion/complete", {"ref": {"type": "ref/prompt", "name": "greet"},
                                                  "argument": {"name": "who", "value": "an"}}))["result"]
        none = (await req("completion/complete", {"ref": {"type": "ref/prompt", "name": "greet"},
                                                  "argument": {"name": "other", "value": ""}}))["result"]
    assert listed[-1] == {"name": "greet", "title": "Greet", "description": "Say hello.",
                          "arguments": [{"name": "who", "description": "name", "required": True}]}
    assert got["messages"] == [{"role": "user", "content": {"type": "text", "text": "hello ann"}}]
    assert done["completion"] == {"values": ["ann", "anna"], "total": 2, "hasMore": False}
    assert none["completion"]["values"] == []


@pytest.mark.anyio
async def test_completion_caps_at_100():
    completion.COMPLETERS[("ref/resource", "x://{a}", "a")] = lambda v, ctx: [f"v{i}" for i in range(150)]
    try:
        out = completion.complete("ref/resource", "x://{a}", "a", "v", None)
    finally:
        del completion.COMPLETERS[("ref/resource", "x://{a}", "a")]
    assert len(out["values"]) == 100 and out["total"] == 150 and out["hasMore"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_prompts_get_errors_are_32602(mode, registries):
    async def build(args, ctx):
        return []

    prompts.PROMPTS.append(prompts.PromptSpec("p", "P", "d", (prompts.PromptArg("a", "x", True),), build))
    async with rpc.rpc_session(server()) as s:
        req = await open_session(s, mode)
        unknown = await req("prompts/get", {"name": "nope"})
        missing = await req("prompts/get", {"name": "p", "arguments": {}})
        extra = await req("prompts/get", {"name": "p", "arguments": {"a": "1", "zz": "2"}})
    for res in (unknown, missing, extra):
        assert res["error"]["code"] == -32602, res
    assert "Unknown prompt" in unknown["error"]["message"] and "zz" in extra["error"]["message"]


@pytest.mark.anyio
async def test_log_level_meta_debug_yields_no_log_notification():
    async with rpc.rpc_session(server()) as s:
        res = await s.request("tools/call", {"name": "status", "arguments": {},
                                             "_meta": {types.LOG_LEVEL_META_KEY: "debug"}})
    assert res["result"]["isError"] is False
    assert not [m for m in s.messages if m.get("method") == "notifications/message"]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_subscriptions_listen_is_an_error(mode):
    async with rpc.rpc_session(server()) as s:
        req = await open_session(s, mode)
        res = await req("subscriptions/listen", {"notifications": {}})
    # not implemented (resources.subscribe false, no list-changed streams): the SDK answers an error
    assert "error" in res and res["error"]["code"] == -32601, res   # recorded: method not found


@pytest.mark.anyio
async def test_traceparent_reaches_the_audit_record_only(tmp_path):
    log = tmp_path / "audit.jsonl"
    tp = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    async with rpc.rpc_session(server(log_path=str(log))) as s:
        res = await s.request("tools/call", {"name": "yes_no", "arguments": YES_NO,
                                             "_meta": {"traceparent": tp, "tracestate": "k=v"}})
    assert res["result"]["isError"] is False
    rec = json.loads(log.read_text().splitlines()[-1])
    assert rec["traceparent"] == tp and rec["tracestate"] == "k=v"


def test_resource_link_shape_and_order(tmp_path):
    f = tmp_path / "out.jsonl"
    f.write_text("{}\n")
    link = file_link(str(f), "application/x-ndjson")
    assert link == {"type": "resource_link", "uri": "file://" + os.path.realpath(f), "name": "out.jsonl",
                    "mimeType": "application/x-ndjson", "annotations": {"audience": ["user", "assistant"]}}
    res = success_result({"a": 1}, [link])
    assert [b["type"] for b in res["content"]] == ["text", "resource_link"]
    assert types.CallToolResult.model_validate(res).content[1].uri.startswith("file://")


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_tool_links_reach_the_wire_and_reset_per_call(mode, monkeypatch, tmp_path):
    f = tmp_path / "o.csv"
    f.write_text("x")
    spec = dispatch._BY_NAME["status"]

    async def handler(ctx, args):
        ctx.links.append(file_link(str(f), "text/csv"))
        return {"ok": True}

    monkeypatch.setitem(dispatch._BY_NAME, "status", replace(spec, handler=handler))
    async with rpc.rpc_session(server()) as s:
        req = await open_session(s, mode)
        res = (await req("tools/call", {"name": "status", "arguments": {}}))["result"]
        again = (await req("tools/call", {"name": "status", "arguments": {}}))["result"]
    assert [b["type"] for b in res["content"]] == ["text", "resource_link"]
    assert res["content"][1]["uri"] == "file://" + os.path.realpath(f) and res["content"][1]["name"] == "o.csv"
    assert len(again["content"]) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_unknown_resource_is_32602_with_uri(mode):
    async with rpc.rpc_session(server()) as s:
        req = await open_session(s, mode)
        res = await req("resources/read", {"uri": "openjev://nope"})
    assert res["error"]["code"] == -32602 and res["error"]["data"] == {"uri": "openjev://nope"}


@pytest.mark.anyio
async def test_reader_registry_serves_templated_uris(monkeypatch):
    async def reader(uri, ctx):
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": "hi"}]}

    monkeypatch.setattr(resources, "READERS", [(re.compile(r"^x://"), reader)])
    monkeypatch.setattr(resources, "TEMPLATES", [{"uriTemplate": "x://{id}", "name": "x", "mimeType": "text/plain"}])
    async with rpc.rpc_session(server()) as s:
        read = await s.request("resources/read", {"uri": "x://1"})
        tpl = await s.request("resources/templates/list")
    assert read["result"]["contents"][0]["text"] == "hi"
    assert tpl["result"]["resourceTemplates"][0]["uriTemplate"] == "x://{id}"


def test_specs_are_cached_and_core_toolset_filters():
    cfg = load_config({})
    assert dispatch.specs(cfg) is dispatch.specs(cfg)
    assert [t.name for t in dispatch.tools_for(replace(cfg, toolsets="core"))] == list(dispatch.CORE_TOOL_NAMES)
