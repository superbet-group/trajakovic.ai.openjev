"""The MCP server over the SDK: tools, resources, lifespan and the CLI (arch D.19, B.4)."""
from __future__ import annotations

import json
import logging
import socket
from dataclasses import replace

import httpx
import pytest
import rpc
import stubs
from mcp.shared.exceptions import MCPError

from openjev_mcp import CORE_TOOL_NAMES, PROTOCOL_VERSIONS, TOOL_NAMES, __version__
from openjev_mcp.config import load_config
from openjev_mcp.http import OpenJevClient
from openjev_mcp.schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS
from openjev_mcp.server import INSTRUCTIONS, build_server, main

MODES = ("2026-07-28", "legacy")
CLAIM = {"state": "I was charged twice.", "claim": "Is this a billing issue?",
         "true_means": "about charges", "false_means": "anything else"}


def config(**kw):
    return replace(load_config({}, transport="http"), retries=0, **kw)


def stub_server(**kw):
    return build_server(config(), transport=stubs.asgi_transport(stubs.openjev_app()), warm_limits=False, **kw)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def root_logging():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:], root.level = handlers, level


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_tools_list_in_both_eras(mode):
    async with rpc.mcp_client(stub_server(), mode) as client:
        tools = (await client.list_tools()).tools
    assert tuple(t.name for t in tools) == TOOL_NAMES
    core = [t for t in tools if t.name in CORE_TOOL_NAMES]
    assert [t.title for t in core] == ["Ask OpenJev", "Yes/no claim", "Classify", "Score on a scale",
                                       "Lint a request", "OpenJev status"]
    for t in tools:
        assert t.input_schema == INPUT_SCHEMAS[t.name] and t.output_schema == OUTPUT_SCHEMAS[t.name]
        a = t.annotations
        assert a.title == t.title and a.open_world_hint is False and t.description
    for t in core:
        assert t.annotations.destructive_hint is None and t.annotations.read_only_hint is True and t.annotations.idempotent_hint is (t.name != "ask")


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_yes_no_returns_structured_content(mode):
    async with rpc.mcp_client(stub_server(), mode) as client:
        res = await client.call_tool("yes_no", CLAIM)
    assert not res.is_error
    out = res.structured_content
    assert out["decision"] in ("yes", "no", "uncertain") and 0 <= out["p"] <= 1
    assert json.loads(res.content[0].text) == out


@pytest.mark.anyio
async def test_status_unreachable_is_a_tool_error_not_a_rpc_error():
    server = build_server(config(), transport=stubs.fault_transport(exc=httpx.ConnectError("refused")),
                          warm_limits=False)
    async with rpc.rpc_session(server) as s:
        res = await s.request("tools/call", {"name": "status", "arguments": {}})
    assert "error" not in res and res["result"]["isError"] is True
    assert json.loads(res["result"]["content"][0]["text"])["error"]["code"] == "OJ_UNREACHABLE"


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_resources_list_and_read(mode):
    async with rpc.mcp_client(stub_server(), mode) as client:
        listed = (await client.list_resources()).resources
        assert [str(r.uri) for r in listed] == [
            "openjev://schema", "openjev://limits", "openjev://recipes", "openjev://templates", "openjev://patterns",
            "openjev://guide/authoring"]
        tpls = (await client.list_resource_templates()).resource_templates
        assert [t.uri_template for t in tpls] == [
            "openjev://recipes/{id}", "openjev://templates/{id}", "openjev://audits/{question_hash}"]
        schema = await client.read_resource("openjev://schema")
        limits = await client.read_resource("openjev://limits")
    assert "$defs" in json.loads(schema.contents[0].text)
    assert schema.contents[0].mime_type == "application/schema+json"
    assert json.loads(limits.contents[0].text)


@pytest.mark.anyio
async def test_unknown_tool_and_resource_are_invalid_params():
    async with rpc.rpc_session(stub_server()) as s:
        tool = await s.request("tools/call", {"name": "nope", "arguments": {}})
        res = await s.request("resources/read", {"uri": "openjev://nope"})
    assert tool["error"]["code"] == -32602 and tool["error"]["message"] == "Unknown tool: nope"
    assert res["error"] == {"code": -32602, "message": "Resource not found", "data": {"uri": "openjev://nope"}}


@pytest.mark.anyio
async def test_unknown_tool_raises_mcp_error_on_the_client():
    async with rpc.mcp_client(stub_server(), "2026-07-28") as client:
        with pytest.raises(MCPError) as err:
            await client.call_tool("nope", {})
    assert err.value.code == -32602


@pytest.mark.anyio
async def test_discover_lists_three_versions_with_cache_hints():
    async with rpc.rpc_session(stub_server()) as s:
        res = (await s.request("server/discover"))["result"]
        listed = (await s.request("tools/list"))["result"]
    assert res["supportedVersions"] == list(PROTOCOL_VERSIONS)
    assert res["instructions"] == INSTRUCTIONS and INSTRUCTIONS.count(". ") + 1 == 3
    assert set(res["capabilities"]) == {"tools", "resources", "prompts", "completions"}
    assert (res["ttlMs"], res["cacheScope"]) == (3600000, "public")
    assert (listed["ttlMs"], listed["cacheScope"]) == (3600000, "public")
    assert res["_meta"]["io.modelcontextprotocol/serverInfo"]["version"] == __version__


@pytest.mark.anyio
async def test_no_logging():
    async with rpc.rpc_session(stub_server()) as s:
        for method in ("logging/setLevel",):
            res = await s.request(method, {"level": "info"})
            assert res["error"]["code"] == -32601, method


@pytest.mark.anyio
async def test_injected_client_is_not_closed_but_own_client_is():
    cfg = config()
    injected = OpenJevClient(cfg, transport=stubs.asgi_transport(stubs.openjev_app()))
    server = build_server(cfg, client=injected, warm_limits=False)
    async with server.lifespan(server) as state:
        assert state.client is injected
    assert not injected._http.is_closed
    await injected.aclose()
    own = build_server(cfg, transport=stubs.asgi_transport(stubs.openjev_app()), warm_limits=False)
    async with own.lifespan(own) as state:
        assert state.audit is None
    assert state.client._http.is_closed


@pytest.mark.anyio
async def test_lifespan_warms_limits_in_the_background():
    app = stubs.openjev_app()
    server = build_server(config(), transport=stubs.asgi_transport(app))
    async with server.lifespan(server) as state:
        import anyio
        with anyio.fail_after(5):
            while state.limits._limits is None:
                await anyio.sleep(0.01)


def test_version_exits_zero(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"openjev-mcp {__version__}\n"


def test_non_loopback_without_token_exits_two(capsys, monkeypatch, root_logging):
    monkeypatch.delenv("OPENJEV_MCP_TOKEN", raising=False)
    assert main(["--host", "0.0.0.0"]) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and "binding 0.0.0.0 needs OPENJEV_MCP_TOKEN" in cap.err


def test_bad_config_exits_two(capsys, monkeypatch):
    monkeypatch.setenv("OPENJEV_MCP_TIMEOUT_MS", "soon")
    assert main(["--port", str(free_port())]) == 2
    assert "OPENJEV_MCP_TIMEOUT_MS" in capsys.readouterr().err


def test_occupied_port_exits_three_with_hint(capsys, monkeypatch, root_logging):
    monkeypatch.delenv("OPENJEV_MCP_TOKEN", raising=False)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert main(["--transport", "http", "--host", "127.0.0.1", "--port", str(port)]) == 3
    cap = capsys.readouterr()
    assert cap.out == "" and f"127.0.0.1:{port}" in cap.err
    assert f"OPENJEV_MCP_PORT={port + 1} mise run start" in cap.err


def test_logging_goes_to_stderr(capsys, monkeypatch, root_logging):
    monkeypatch.setenv("OPENJEV_MCP_DEBUG", "1")
    monkeypatch.delenv("OPENJEV_MCP_TOKEN", raising=False)
    main(["--host", "0.0.0.0"])
    logging.getLogger("openjev_mcp").warning("hello")
    logging.getLogger("openjev_mcp").debug("detail")
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "openjev-mcp: WARNING hello" in cap.err and "openjev-mcp: DEBUG detail" in cap.err


def test_bind_localhost_listens_on_ipv4():
    import socket

    from openjev_mcp.server import _bind
    sock = _bind("localhost", 0)
    try:
        assert sock.family == socket.AF_INET and sock.getsockname()[0] == "127.0.0.1"
    finally:
        sock.close()
