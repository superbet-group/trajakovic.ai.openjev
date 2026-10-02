"""Streamable HTTP transport: /mcp, /health, Host/Origin, bearer token, statelessness (arch B.2, B.5, D.20)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import replace

import httpx
import pytest
import rpc
import stubs

from openjev_mcp import PROTOCOL_VERSIONS, TOOL_NAMES, __version__
from openjev_mcp.config import load_config
from openjev_mcp.http_app import security_settings
from openjev_mcp.server import build_server

TOKEN = "s3cret-token"
CLAIM = {"state": "I was charged twice.", "claim": "Is this a billing issue?",
         "true_means": "about charges", "false_means": "anything else"}
LEGACY_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "t", "version": "1"}}}


def config(**kw):
    return replace(load_config({}, transport="http"), retries=0, **kw)


def server_for(cfg):
    return build_server(cfg, transport=stubs.asgi_transport(stubs.openjev_app()), warm_limits=False)


def client_for(**kw):
    cfg = config(**kw)
    return rpc.http_app_client(server_for(cfg), cfg)


def messages(resp: httpx.Response) -> list[dict]:
    if resp.headers["content-type"].startswith("text/event-stream"):
        return [json.loads(line[5:]) for line in resp.text.splitlines() if line.startswith("data:") and line[5:].strip()]
    return [resp.json()]


def result_of(resp: httpx.Response) -> dict:
    return next(m for m in messages(resp) if "result" in m or "error" in m)


async def post_modern(c, method, params=None, name=None, **headers):
    return await c.post("/mcp", json=rpc.modern_body(method, params), headers=rpc.modern_headers(method, name, **headers))


@pytest.mark.anyio
async def test_health_body_and_no_auth_needed():
    async with client_for(token=TOKEN, base_url="http://user:pw@oj.test:8080/x") as c:
        res = await c.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "name": "openjev-mcp", "version": __version__,
                          "transport": "streamable-http", "protocol_versions": list(PROTOCOL_VERSIONS),
                          "openjev_base_url": "http://oj.test:8080/x"}
    assert "pw" not in res.text and TOKEN not in res.text


@pytest.mark.anyio
async def test_modern_post_lists_tools_and_calls_a_tool():
    async with client_for() as c:
        listed = await post_modern(c, "tools/list")
        called = await post_modern(c, "tools/call", {"name": "yes_no", "arguments": CLAIM}, name="yes_no")
    assert listed.status_code == 200 and "mcp-session-id" not in listed.headers
    body = result_of(listed)["result"]
    assert [t["name"] for t in body["tools"]] == list(TOOL_NAMES) and body["resultType"] == "complete"
    assert called.status_code == 200 and "mcp-session-id" not in called.headers
    assert result_of(called)["result"]["structuredContent"]["decision"] in ("yes", "no", "uncertain")


@pytest.mark.anyio
async def test_missing_mcp_method_and_wrong_mcp_name_are_400():
    async with client_for() as c:
        headers = rpc.modern_headers("tools/list")
        del headers["mcp-method"]
        no_method = await c.post("/mcp", json=rpc.modern_body("tools/list"), headers=headers)
        wrong_name = await post_modern(c, "tools/call", {"name": "yes_no", "arguments": CLAIM}, name="status")
    for res in (no_method, wrong_name):
        assert res.status_code == 400 and result_of(res)["error"]["code"] == -32020


@pytest.mark.anyio
async def test_accept_missing_is_406():
    async with client_for() as c:
        headers = rpc.modern_headers("tools/list")
        headers["accept"] = "application/json"
        res = await c.post("/mcp", json=rpc.modern_body("tools/list"), headers=headers)
    assert res.status_code == 406


@pytest.mark.anyio
async def test_bad_origin_is_403_and_bad_host_is_421():
    async with client_for() as c:
        evil_origin = await post_modern(c, "tools/list", origin="https://evil.example")
        evil_host = await post_modern(c, "tools/list", host="evil.example:8100")
        local_origin = await post_modern(c, "tools/list", origin="http://localhost:3000")
    assert evil_origin.status_code == 403
    assert evil_host.status_code == 421
    assert local_origin.status_code == 200


@pytest.mark.anyio
async def test_extra_allowed_host_and_origin_from_config():
    async with client_for(allowed_hosts=("mcp.lan:*",), allowed_origins=("https://app.lan",)) as c:
        host = await post_modern(c, "tools/list", host="mcp.lan:8100")
        origin = await post_modern(c, "tools/list", origin="https://app.lan")
    assert host.status_code == 200 and origin.status_code == 200


def test_security_settings_for_a_non_loopback_bind():
    s = security_settings(config(host="10.0.0.5", token=TOKEN, allowed_hosts=("x:*",)))
    assert s.enable_dns_rebinding_protection
    assert s.allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*", "10.0.0.5:*", "x:*"]
    assert s.allowed_origins == ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    assert "[fe80::1]:*" in security_settings(config(host="fe80::1", token=TOKEN)).allowed_hosts


@pytest.mark.anyio
async def test_bearer_token_gates_mcp_only():
    async with client_for(token=TOKEN) as c:
        none = await post_modern(c, "tools/list")
        wrong = await post_modern(c, "tools/list", authorization="Bearer nope")
        basic = await post_modern(c, "tools/list", authorization=f"Basic {TOKEN}")
        right = await post_modern(c, "tools/list", authorization=f"Bearer {TOKEN}")
        health = await c.get("/health")
    for res in (none, wrong, basic):
        assert res.status_code == 401 and res.json() == {"error": "unauthorized"}
        assert res.headers["www-authenticate"] == 'Bearer realm="openjev-mcp"'
    assert right.status_code == 200 and health.status_code == 200


@pytest.mark.anyio
async def test_legacy_initialize_then_tools_list_is_stateless():
    base = {"accept": rpc.ACCEPT, "content-type": "application/json"}
    async with client_for() as c:
        init = await c.post("/mcp", json=LEGACY_INIT, headers=base)
        assert init.status_code == 200 and "mcp-session-id" not in init.headers
        assert result_of(init)["result"]["protocolVersion"] == "2025-06-18"
        listed = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                              headers={**base, "mcp-protocol-version": "2025-06-18"})
    assert listed.status_code == 200 and "mcp-session-id" not in listed.headers
    tools = result_of(listed)["result"]["tools"]
    assert [t["name"] for t in tools] == list(TOOL_NAMES)
    assert "ttlMs" not in result_of(listed)["result"]


@pytest.mark.anyio
async def test_get_mcp_is_405():
    async with client_for() as c:
        res = await c.get("/mcp", headers={"accept": "text/event-stream", "mcp-protocol-version": rpc.MODERN})
    assert res.status_code == 405


@pytest.mark.anyio
async def test_progress_arrives_on_sse_when_the_request_has_a_token():
    params = {"name": "yes_no", "arguments": CLAIM, "_meta": {"progressToken": "tok"}}
    async with client_for() as c:
        res = await post_modern(c, "tools/call", params, name="yes_no")
        plain = await post_modern(c, "tools/call", {"name": "yes_no", "arguments": CLAIM}, name="yes_no")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    msgs = messages(res)
    progress = [m for m in msgs if m.get("method") == "notifications/progress"]
    assert progress and progress[0]["params"]["progressToken"] == "tok"
    assert "result" in msgs[-1] and "mcp-session-id" not in res.headers
    assert not any(m.get("method") == "notifications/progress" for m in messages(plain))


def spare_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_process_smoke():
    port = spare_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENJEV_MCP_")}
    env["OPENJEV_BASE_URL"] = "http://127.0.0.1:9"
    proc = subprocess.Popen([sys.executable, "-m", "openjev_mcp", "--transport", "http", "--port", str(port)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10
        while True:
            try:
                health = httpx.get(f"{url}/health", timeout=1)
                break
            except httpx.TransportError:
                assert proc.poll() is None and time.monotonic() < deadline, "server did not come up"
                time.sleep(0.1)
        assert health.status_code == 200 and health.json()["status"] == "ok"
        listed = httpx.post(f"{url}/mcp", json=rpc.modern_body("tools/list"),
                            headers=rpc.modern_headers("tools/list"), timeout=5)
        assert [t["name"] for t in result_of(listed)["result"]["tools"]] == list(TOOL_NAMES)
        status = httpx.post(f"{url}/mcp", json=rpc.modern_body("tools/call", {"name": "status", "arguments": {}}),
                            headers=rpc.modern_headers("tools/call", "status"), timeout=15)
        err = json.loads(result_of(status)["result"]["content"][0]["text"])["error"]
        assert err["code"] == "OJ_UNREACHABLE"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        for pipe in (proc.stdout, proc.stderr):
            pipe.close()
