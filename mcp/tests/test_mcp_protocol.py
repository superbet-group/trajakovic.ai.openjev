"""Protocol conformance over the JSON-RPC surface (spec 2.0.1 rules 1-6, TASKS 1.26-1.29, 1.3)."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace

import httpx
import pytest
import rpc
import stubs

from openjev_mcp import PROTOCOL_VERSIONS, SERVER_NAME, TOOL_NAMES, __version__
from openjev_mcp.batch import store
from openjev_mcp.config import load_config
from openjev_mcp.server import INSTRUCTIONS, build_server

K = "io.modelcontextprotocol/"
SERVER_INFO = K + "serverInfo"
MODES = ("2026-07-28", "legacy")
LEGACY_VERSIONS = ("2025-06-18", "2025-11-25")
SCHEMA_URI = "openjev://schema"
STATIC = (("tools/list", None), ("resources/list", None), ("server/discover", None),
          ("resources/read", {"uri": SCHEMA_URI}))
QS = {"q": {"type": "noul", "instructions": "Is this a billing issue?",
            "criteria": {"true": "about charges", "false": "anything else"}}}
CALLS = {
    "ask": {"state": "I was charged twice.", "questions": QS},
    "yes_no": {"state": "I was charged twice.", "claim": "Is this a billing issue?",
               "true_means": "about charges", "false_means": "anything else"},
    "classify": {"state": "I was charged twice.", "question": "Which team?", "labels": ["billing", "tech"]},
    "score": {"state": "I was charged twice.", "question": "How urgent is it?", "levels": ["low", "high"]},
    "lint": {"request": {"model": "openjev-latest", "state": "s", "questions": QS}},
    "status": {},
    "filter": {"task": "Find failures.", "criterion": "Is log line {id} a real failure?",
               "items": [{"id": "a", "text": "ERROR boom"}, {"id": "b", "text": "INFO ok"}]},
    "batch": {"items": [{"id": "1", "state": "I was charged twice."}], "questions": QS},
    "recipe": {"recipe": "command_gate", "inputs": {"task": "Fix the test.", "command": "pnpm test"}},
}
PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==")
CALLS["ask_image"] = {"images": [{"base64": PNG, "content_type": "image/png"}], "state": "A pixel.", "questions": QS}
CALLS["compile"] = {"intent": "tell me if a support email is angry"}
CALLS["calibrate"] = {"questions": QS, "options": {"samples": 1},
                      "examples": [{"id": f"e{i}", "state": f"text {i}", "label": {"q": i % 2 == 0}} for i in range(6)]}
CALLS["generate"] = {"messages": [{"role": "user", "content": "hi"}]}
ROOT = os.path.realpath(tempfile.mkdtemp(prefix="oj-proto-"))
OUT = os.path.join(ROOT, "out.jsonl")
with open(OUT, "w") as f:   # header only: batch_results reads it without the model
    f.write(json.dumps(store.make_header(run_id="r", question_hash="h")) + "\n")
CALLS["batch_results"] = {"path": OUT, "view": "stats"}
CALLS = {n: CALLS[n] for n in TOOL_NAMES}   # spec 2.5 order


def config(**kw):
    return replace(load_config({}, transport="http"), retries=0, roots=(ROOT,), **kw)


answers = stubs.default_answers   # already wire-shaped (index-keyed legend and probabilities)


class _ChatTransport(httpx.AsyncBaseTransport):
    """The stub app plus a canned /v1/chat/completions (the stub engine has no chat route)."""
    def __init__(self, inner):
        self.inner = inner

    async def handle_async_request(self, request):
        if request.url.path != "/v1/chat/completions":
            return await self.inner.handle_async_request(request)
        return httpx.Response(200, json={"id": "c", "object": "chat.completion", "model": "diffusiongemma-26b", "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}})


def stub_server(app=None, **cfg):
    app = app or stubs.openjev_app(engine=stubs.StubEngine(answers=answers))
    return build_server(config(**cfg), transport=_ChatTransport(stubs.asgi_transport(app)), warm_limits=False)


async def legacy(s, version):
    res = await s.request("initialize", {"protocolVersion": version, "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "1"}}, envelope=False)
    if "result" in res:
        await s.notify("notifications/initialized")
    return res


def walk(node, path=""):
    yield path, node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}/{i}")


# --- rule 1: server/discover, revisions -----------------------------------------------------------

@pytest.mark.anyio
async def test_discover_answers_versions_capabilities_server_info_instructions():
    async with rpc.rpc_session(stub_server()) as s:
        res = (await s.request("server/discover"))["result"]
    assert res["supportedVersions"] == ["2026-07-28", "2025-11-25", "2025-06-18"] == list(PROTOCOL_VERSIONS)
    assert res["capabilities"]["tools"] == {"listChanged": False}
    assert res["capabilities"]["resources"] == {"listChanged": False, "subscribe": False}
    assert res["capabilities"]["prompts"] == {"listChanged": False} and res["capabilities"]["completions"] == {}
    assert "extensions" not in res["capabilities"]
    assert res["_meta"][SERVER_INFO]["name"] == SERVER_NAME == "openjev-mcp"
    assert res["_meta"][SERVER_INFO]["version"] == __version__
    assert res["instructions"] == INSTRUCTIONS
    sentences = [x for x in res["instructions"].split(". ") if x]
    assert len(sentences) == 3 and "status" in sentences[1] and "never execute anything" in sentences[2]


@pytest.mark.anyio
async def test_unsupported_modern_version_is_32022():
    async with rpc.rpc_session(stub_server()) as s:
        res = await s.request("tools/list", {"_meta": {K + "protocolVersion": "2099-01-01",
                                                       K + "clientCapabilities": {}}}, envelope=False)
    assert res["error"]["code"] == -32022
    assert res["error"]["data"] == {"supported": ["2026-07-28"], "requested": "2099-01-01"}


@pytest.mark.anyio
@pytest.mark.parametrize("version", LEGACY_VERSIONS)
async def test_legacy_initialize_negotiates_the_requested_version(version):
    async with rpc.rpc_session(stub_server()) as s:
        init = (await legacy(s, version))["result"]
        listed = (await s.request("tools/list", envelope=False))["result"]
    assert init["protocolVersion"] == version
    assert init["serverInfo"]["name"] == SERVER_NAME and init["instructions"] == INSTRUCTIONS
    assert init["capabilities"]["tools"] == {"listChanged": False}
    assert [t["name"] for t in listed["tools"]] == list(TOOL_NAMES)
    assert not {"ttlMs", "cacheScope", "resultType", "nextCursor"} & set(listed)
    assert "_meta" not in listed or SERVER_INFO not in listed["_meta"]


@pytest.mark.anyio
async def test_legacy_unknown_version_gets_a_counter_offer():
    async with rpc.rpc_session(stub_server()) as s:
        res = await legacy(s, "1999-01-01")
    assert res["result"]["protocolVersion"] == "2025-11-25"


@pytest.mark.anyio
@pytest.mark.parametrize("version", LEGACY_VERSIONS)
async def test_legacy_tool_call_works_without_envelope(version):
    async with rpc.rpc_session(stub_server()) as s:
        await legacy(s, version)
        res = (await s.request("tools/call", {"name": "yes_no", "arguments": CALLS["yes_no"]}, envelope=False))["result"]
    assert not res.get("isError") and res["structuredContent"]["decision"] in ("yes", "no", "uncertain")
    assert "resultType" not in res


# --- rule 2: per-request _meta --------------------------------------------------------------------

@pytest.mark.anyio
async def test_modern_request_without_client_capabilities_is_32602():
    async with rpc.rpc_session(stub_server()) as s:
        await s.request("server/discover")
        res = await s.request("tools/list", {"_meta": {K + "protocolVersion": rpc.MODERN}}, envelope=False)
    assert res["error"]["code"] == -32602 and "clientCapabilities" in res["error"]["message"]


@pytest.mark.anyio
async def test_modern_request_without_protocol_version_is_32602():
    async with rpc.rpc_session(stub_server()) as s:
        await s.request("server/discover")
        res = await s.request("tools/list", {"_meta": {K + "clientCapabilities": {}}}, envelope=False)
    assert res["error"]["code"] == -32602 and "protocolVersion" in res["error"]["message"]


@pytest.mark.anyio
async def test_every_modern_result_has_result_type_and_server_info_meta():
    async with rpc.rpc_session(stub_server()) as s:
        results = [(await s.request(m, p))["result"] for m, p in STATIC]
        results.append((await s.request("resources/read", {"uri": "openjev://limits"}))["result"])
        for name, args in CALLS.items():
            results.append((await s.request("tools/call", {"name": name, "arguments": args}))["result"])
        results.append((await s.request("tools/call", {"name": "ask", "arguments": {}}))["result"])
    assert len(results) == len(STATIC) + 1 + len(CALLS) + 1
    for res in results:
        assert res["resultType"] == "complete"
        assert res["_meta"][SERVER_INFO] == {"name": SERVER_NAME, "version": __version__}


# --- rule 5: caching ------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("method,params", STATIC)
async def test_static_results_cache_for_an_hour_publicly(method, params):
    async with rpc.rpc_session(stub_server()) as s:
        res = (await s.request(method, params))["result"]
    assert (res["ttlMs"], res["cacheScope"]) == (3600000, "public")


@pytest.mark.anyio
async def test_limits_resource_caches_for_a_minute_privately():
    async with rpc.rpc_session(stub_server()) as s:
        res = (await s.request("resources/read", {"uri": "openjev://limits"}))["result"]
    assert (res["ttlMs"], res["cacheScope"]) == (60000, "private")


@pytest.mark.anyio
async def test_lists_are_one_page_in_a_fixed_order():
    async with rpc.rpc_session(stub_server()) as s:
        tools = (await s.request("tools/list"))["result"]
        resources = (await s.request("resources/list"))["result"]
    assert "nextCursor" not in tools and "nextCursor" not in resources
    assert [t["name"] for t in tools["tools"]] == list(TOOL_NAMES)
    assert [r["uri"] for r in resources["resources"]] == [
        SCHEMA_URI, "openjev://limits", "openjev://recipes", "openjev://templates", "openjev://patterns",
        "openjev://guide/authoring"]


# --- rule 3: statelessness ------------------------------------------------------------------------

async def static_snapshot(s, **kw):
    return [(await s.request(m, p, **kw))["result"] for m, p in STATIC]


@pytest.mark.anyio
async def test_two_sessions_of_one_server_get_identical_results():
    server = stub_server()
    async with rpc.rpc_session(server) as a:
        first = await static_snapshot(a)
    async with rpc.rpc_session(server) as b:
        second = await static_snapshot(b)
    assert first == second


@pytest.mark.anyio
async def test_a_server_that_served_calls_answers_like_a_fresh_one():
    fresh = stub_server()
    async with rpc.rpc_session(fresh) as a:
        baseline = await static_snapshot(a)
    used = stub_server()
    async with rpc.rpc_session(used) as b:
        for name, args in CALLS.items():
            await b.request("tools/call", {"name": name, "arguments": args})
        await b.request("tools/call", {"name": "ask", "arguments": {}})
        await b.request("resources/read", {"uri": "openjev://limits"})
        assert await static_snapshot(b) == baseline


@pytest.mark.anyio
async def test_legacy_and_modern_sessions_list_the_same_tools():
    server = stub_server()
    async with rpc.rpc_session(server) as modern:
        m = (await modern.request("tools/list"))["result"]["tools"]
    async with rpc.rpc_session(server) as old:
        await legacy(old, "2025-11-25")
        o = (await old.request("tools/list", envelope=False))["result"]["tools"]
    assert m == o


# --- rule 6 / 1.29: schemas and the validation path -----------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_schema_walk_over_the_wire(mode):
    async with rpc.mcp_client(stub_server(), mode) as client:
        tools = (await client.list_tools()).tools
    assert tuple(t.name for t in tools) == TOOL_NAMES
    for t in tools:
        for label, schema in (("inputSchema", t.input_schema), ("outputSchema", t.output_schema)):
            where = f"{t.name}.{label}"
            assert schema["type"] == "object", where
            assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema", where
            assert not {"oneOf", "anyOf", "allOf"} & set(schema), where
            for path, node in walk(schema):
                assert not (isinstance(node, dict) and "$ref" in node), f"{where}{path}"


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("args", [
    {"state": "s"},
    {"questions": QS},
    {"state": "s", "questions": "not an object"},
    {"state": 5, "questions": QS},
    {"state": "s", "questions": QS, "extra": 1},
    {"state": "s", "questions": QS, "lint": "maybe"},
], ids=["missing-questions", "missing-state", "wrong-type", "wrong-state-type", "extra", "bad-enum"])
async def test_invalid_arguments_are_a_tool_error_never_32602(mode, args):
    async with rpc.mcp_client(stub_server(), mode) as client:
        res = await client.call_tool("ask", args)
    assert res.is_error is True and res.structured_content is None
    assert json.loads(res.content[0].text)["error"]["code"] == "OJ_INVALID_INPUT"


@pytest.mark.anyio
@pytest.mark.parametrize("name,args", [("ask", {}), ("yes_no", {"state": "s"}), ("classify", {"state": "s", "question": "q?", "labels": "x"}),
                                       ("score", {"state": "s", "question": "q?", "levels": ["one"]}),
                                       ("lint", {"emit": ["pdf"]}), ("status", {"probe": "yes"})])
async def test_invalid_arguments_over_raw_rpc_are_results(name, args):
    async with rpc.rpc_session(stub_server()) as s:
        res = await s.request("tools/call", {"name": name, "arguments": args})
    assert "error" not in res, res
    assert res["result"]["isError"] is True
    assert json.loads(res["result"]["content"][0]["text"])["error"]["code"] == "OJ_INVALID_INPUT"


@pytest.mark.anyio
async def test_arguments_must_be_an_object_when_present():
    async with rpc.rpc_session(stub_server()) as s:
        res = await s.request("tools/call", {"name": "status", "arguments": []})
    assert res["error"]["code"] == -32602


@pytest.mark.anyio
async def test_unknown_tool_is_32602():
    async with rpc.rpc_session(stub_server()) as s:
        res = await s.request("tools/call", {"name": "nope", "arguments": {}})
    assert res["error"]["code"] == -32602 and "nope" in res["error"]["message"]


@pytest.mark.anyio
async def test_unknown_resource_is_32602_with_the_uri():
    async with rpc.rpc_session(stub_server()) as s:
        res = await s.request("resources/read", {"uri": "openjev://nope"})
    assert res["error"]["code"] == -32602 and res["error"]["data"] == {"uri": "openjev://nope"}


@pytest.mark.anyio
async def test_tools_list_order_matches_tool_names_in_both_eras():
    async with rpc.rpc_session(stub_server()) as s:
        modern = [t["name"] for t in (await s.request("tools/list"))["result"]["tools"]]
    async with rpc.rpc_session(stub_server()) as s:
        await legacy(s, "2025-11-25")
        old = [t["name"] for t in (await s.request("tools/list", envelope=False))["result"]["tools"]]
    assert modern == old == list(TOOL_NAMES)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", TOOL_NAMES)
async def test_text_block_parses_to_structured_content(mode, name):
    async with rpc.mcp_client(stub_server(), mode) as client:
        res = await client.call_tool(name, CALLS[name])
    assert not res.is_error, res.content
    assert res.structured_content
    assert len(res.content) == 1
    assert json.loads(res.content[0].text) == res.structured_content
    assert res.content[0].annotations.audience == ["assistant"]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_resource_templates_list_has_the_three_templates(mode):
    async with rpc.mcp_client(stub_server(), mode) as client:
        res = await client.list_resource_templates()
    assert [t.uri_template for t in res.resource_templates] == [
        "openjev://recipes/{id}", "openjev://templates/{id}", "openjev://audits/{question_hash}"]
    for t in res.resource_templates:
        assert t.name and t.title and t.mime_type == "application/json" and t.annotations.priority in (0.5, 0.7)


@pytest.mark.anyio
async def test_core_toolset_lists_the_six_in_order():
    from openjev_mcp import CORE_TOOL_NAMES
    server = build_server(config(toolsets="core"), transport=stubs.asgi_transport(stubs.openjev_app()), warm_limits=False)
    async with rpc.rpc_session(server) as s:
        names = [t["name"] for t in (await s.request("tools/list"))["result"]["tools"]]
    assert names == list(CORE_TOOL_NAMES)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_new_tool_results_validate_over_the_wire(mode):
    from openjev_mcp.validate import validate_output
    async with rpc.mcp_client(stub_server(), mode) as client:
        for name in ("filter", "batch", "recipe", "batch_results"):
            res = await client.call_tool(name, CALLS[name])
            assert not res.is_error, (name, res.content)
            assert validate_output(name, res.structured_content) == [], name


@pytest.mark.anyio
@pytest.mark.parametrize("on", (True, False))
async def test_tasks_extension_capability_follows_the_flag(on):
    from openjev_mcp import tasks
    async with rpc.rpc_session(stub_server(tasks=on)) as s:
        caps = (await s.request("server/discover"))["result"]["capabilities"]
    assert ("extensions" in caps and tasks.IDENTIFIER in caps["extensions"]) is on


@pytest.mark.anyio
async def test_prompts_list_in_order():
    async with rpc.rpc_session(stub_server()) as s:
        names = [p["name"] for p in (await s.request("prompts/list"))["result"]["prompts"]]
    assert names == ["start_batch", "review_batch", "author_question", "audit_question", "explain_answer"]
