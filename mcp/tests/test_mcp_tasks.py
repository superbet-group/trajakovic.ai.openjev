"""MCP Tasks extension (P12): task result for batch/calibrate only with the client capability on 2026-07-28."""
from __future__ import annotations

import json
from dataclasses import replace

import anyio
import mcp_types as types
import pytest
import rpc
import stubs
from mcp.server.extension import Extension
from mcp.shared.exceptions import MCPError

from openjev_mcp import tasks
from openjev_mcp.config import load_config
from openjev_mcp.server import build_server

CAP = {types.CLIENT_CAPABILITIES_META_KEY: {"extensions": {tasks.IDENTIFIER: {}}}}
DONE = {"ok": True, "n": 3}


class SlowBatch(Extension):
    """Test-local stand-in for the batch tool (P08 is not imported): slow, reports progress, honours cancel."""
    identifier = "io.example/slow-batch"

    def __init__(self, delay=0.3):
        self.delay, self.started, self.finished = delay, 0, 0

    async def intercept_tool_call(self, params, ctx, call_next):
        if params.name != "batch":
            return await call_next(ctx)
        self.started += 1
        if (params.arguments or {}).get("boom"):
            raise MCPError(types.INVALID_PARAMS, "boom")
        await ctx.session.report_progress(1, 3, "line 1 of 3")
        await anyio.sleep(self.delay)
        self.finished += 1
        return types.CallToolResult(content=[types.TextContent(text=json.dumps(DONE))], structured_content=DONE)


def build(*, on=True, delay=0.3, **kw):
    cfg = replace(load_config({}, transport="http"), retries=0, tasks=on)
    ext = tasks.factory(cfg)
    slow = SlowBatch(delay)
    app = stubs.openjev_app(engine=stubs.StubEngine(answers=stubs.default_answers))
    server = build_server(cfg, transport=stubs.asgi_transport(app), warm_limits=False,
                          extensions=[e for e in (ext, slow) if e is not None], **kw)
    return server, ext, slow


async def open_legacy(s):
    await s.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "1"}}, envelope=False)
    await s.notify("notifications/initialized")


def call(s, cap=True, **kw):
    meta = dict(CAP) if cap else {}
    return s.request("tools/call", {"name": "batch", "arguments": {}, "_meta": meta}, **kw)


async def poll(s, tid, until=("completed", "failed", "cancelled")):
    with anyio.fail_after(5):
        while True:
            res = (await s.request("tasks/get", {"taskId": tid}))["result"]
            if res["status"] in until:
                return res
            await anyio.sleep(0.02)


def test_factory_off_without_flag():
    assert tasks.factory(replace(load_config({}), tasks=False)) is None
    assert isinstance(tasks.factory(replace(load_config({}), tasks=True)), tasks.TasksExtension)


@pytest.mark.anyio
async def test_flag_off_synchronous_and_no_extensions():
    server, ext, _ = build(on=False)
    assert ext is None
    async with rpc.rpc_session(server) as s:
        caps = (await s.request("server/discover"))["result"]["capabilities"]
        res = (await call(s))["result"]
    assert "tasks" not in json.dumps(caps) and caps.get("extensions", {}).get(tasks.IDENTIFIER) is None
    assert res["structuredContent"] == DONE and "resultType" not in res or res.get("resultType") != "task"


@pytest.mark.anyio
async def test_task_result_poll_to_completed_matches_sync():
    server, ext, _ = build()
    async with rpc.rpc_session(server) as s:
        caps = (await s.request("server/discover"))["result"]["capabilities"]
        sync = (await call(s, cap=False))["result"]
        res = (await call(s))["result"]
        assert res["resultType"] == "task"
        task = res["task"]
        assert task["status"] == "working" and task["ttlMs"] > 0 and task["pollIntervalMs"] > 0
        mid = (await s.request("tasks/get", {"taskId": task["taskId"]}))["result"]
        assert mid["status"] == "working" and mid["statusMessage"] == "line 1 of 3" and "result" not in mid
        done = await poll(s, task["taskId"])
        again = (await s.request("tasks/get", {"taskId": task["taskId"]}))["result"]
        assert "error" not in await s.request("tasks/update", {"taskId": task["taskId"], "inputResponses": {}})
    assert tasks.IDENTIFIER in caps["extensions"]
    assert done["status"] == "completed" and done["result"]["structuredContent"] == sync["structuredContent"] == DONE
    assert again["result"] == done["result"]
    assert not [m for m in s.messages if m.get("method") == "notifications/progress"]
    await ext.aclose()


@pytest.mark.anyio
async def test_cancel_stops_handler_and_stays_cancelled():
    server, ext, slow = build(delay=0.5)
    async with rpc.rpc_session(server) as s:
        tid = (await call(s))["result"]["task"]["taskId"]
        await anyio.sleep(0.05)
        assert "error" not in await s.request("tasks/cancel", {"taskId": tid})
        n = len(s.messages)
        await anyio.sleep(0.7)
        res = (await s.request("tasks/get", {"taskId": tid}))["result"]
        assert "error" not in await s.request("tasks/cancel", {"taskId": tid})   # idempotent
        extra = s.messages[n:]
    assert res["status"] == "cancelled" and "result" not in res
    assert slow.started == 1 and slow.finished == 0
    assert [m for m in extra if "method" in m] == []   # no further notifications after cancel
    await ext.aclose()


@pytest.mark.anyio
async def test_no_client_capability_is_synchronous():
    server, ext, _ = build(delay=0)
    async with rpc.rpc_session(server) as s:
        res = (await call(s, cap=False))["result"]
    assert res["structuredContent"] == DONE and res.get("resultType") != "task" and ext.tasks == {}


@pytest.mark.anyio
async def test_legacy_era_is_synchronous():
    server, ext, _ = build(delay=0)
    async with rpc.rpc_session(server) as s:
        await open_legacy(s)
        res = (await call(s, envelope=False))["result"]
        gone = await s.request("tasks/get", {"taskId": "x"}, envelope=False)
    assert res["structuredContent"] == DONE and ext.tasks == {}
    assert gone["error"]["code"] == types.METHOD_NOT_FOUND


@pytest.mark.anyio
async def test_other_tools_are_not_tasks():
    server, ext, _ = build()
    async with rpc.rpc_session(server) as s:
        res = (await s.request("tools/call", {"name": "status", "arguments": {}, "_meta": dict(CAP)}))["result"]
    assert res.get("resultType") != "task" and ext.tasks == {}


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["tasks/get", "tasks/update", "tasks/cancel"])
async def test_unknown_task_id_is_a_jsonrpc_error(method):
    server, _, _ = build()
    async with rpc.rpc_session(server) as s:
        res = await s.request(method, {"taskId": "nope"})
    assert res["error"]["code"] == types.INVALID_PARAMS


@pytest.mark.anyio
async def test_failed_task_carries_error_and_ttl_purges():
    server, ext, _ = build(delay=0)
    ext.ttl_ms = 50
    async with rpc.rpc_session(server) as s:
        res = (await s.request("tools/call", {"name": "batch", "arguments": {"boom": 1}, "_meta": dict(CAP)}))
        tid = res["result"]["task"]["taskId"]
        done = await poll(s, tid)
        await anyio.sleep(0.15)
        gone = await s.request("tasks/get", {"taskId": tid})
    assert done["status"] == "failed" and done["error"]["code"] == types.INVALID_PARAMS
    assert gone["error"]["code"] == types.INVALID_PARAMS
    await ext.aclose()


@pytest.mark.anyio
async def test_task_cap_falls_back_to_synchronous():
    server, ext, _ = build(delay=0.3)
    ext.max_running = 1   # cap = 4
    async with rpc.rpc_session(server) as s:
        kinds = [(await call(s))["result"].get("resultType") for _ in range(6)]
    assert kinds[:4] == ["task"] * 4 and "task" not in kinds[4:]
    await ext.aclose()
