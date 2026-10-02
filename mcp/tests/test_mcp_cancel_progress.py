"""Cancellation and progress (spec 2.0.1 rules 7 and 8, TASKS 1.30), in both protocol eras."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import replace

import anyio
import pytest
import rpc
import stubs

from openjev_mcp.config import load_config
from openjev_mcp.http import OpenJevClient
from openjev_mcp.server import build_server

MODES = ("2026-07-28", "legacy")
QS = {"q": {"type": "noul", "instructions": "Is this a billing issue?",
            "criteria": {"true": "about charges", "false": "anything else"}}}
ASK = {"state": "I was charged twice.", "questions": QS}
GREY = {"state": "I was charged twice.", "claim": "Is this a billing issue?"}


def config():
    return replace(load_config({}, transport="http"), retries=0)


def grey(questions, state, options):
    return {qid: {"type": "noul", "noul": 0.5} for qid in questions}


@asynccontextmanager
async def session(server, mode):
    """Yields (rpc, envelope): the raw session in the era under test."""
    async with rpc.rpc_session(server) as s:
        if mode == "legacy":
            res = await s.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                 "clientInfo": {"name": "t", "version": "1"}}, envelope=False)
            assert res["result"]["protocolVersion"] == "2025-11-25"
            await s.notify("notifications/initialized")
        yield s, mode != "legacy"


def server_for(engine, **kw):
    return build_server(config(), transport=stubs.asgi_transport(stubs.openjev_app(engine=engine)),
                        warm_limits=False, **kw)


def progress(s, token=None):
    out = [m for m in s.messages if m.get("method") == "notifications/progress"]
    return out if token is None else [m for m in out if m["params"]["progressToken"] == token]


def about(s, id):
    return [m for m in s.messages if m.get("id") == id]


async def in_flight(engine, n=1):
    with anyio.fail_after(5):
        while len(engine.calls) < n:
            await anyio.sleep(0.01)


# --- cancellation ---------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_cancelled_request_gets_no_result_error_or_progress(mode):
    engine = stubs.StubEngine(delay_s=30)
    async with session(server_for(engine), mode) as (s, env):
        id = await s.start("tools/call", {"name": "ask", "arguments": ASK, "_meta": {"progressToken": "slow"}},
                           envelope=env)
        await in_flight(engine)
        await s.notify("notifications/cancelled", {"requestId": id, "reason": "test"})
        await anyio.sleep(0.5)
        assert about(s, id) == []
        assert progress(s, "slow") == []
        other = await s.request("tools/list", envelope=env)
        assert [t["name"] for t in other["result"]["tools"]][0] == "ask"
        assert about(s, id) == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_cancelling_one_request_leaves_the_others_running(mode):
    engine = stubs.StubEngine(delay_s=0.3)
    async with session(server_for(engine), mode) as (s, env):
        keep = await s.start("tools/call", {"name": "ask", "arguments": ASK}, envelope=env)
        drop = await s.start("tools/call", {"name": "ask", "arguments": ASK}, envelope=env)
        await in_flight(engine, 2)
        await s.notify("notifications/cancelled", {"requestId": drop})
        res = await s.response(keep)
        await anyio.sleep(0.5)
    assert res["result"]["structuredContent"]["answers"]["q"]["band"] in ("yes", "no", "grey")
    assert about(s, drop) == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_cancellation_releases_the_inflight_semaphore(mode):
    engine = stubs.StubEngine(delay_s=30)
    client = OpenJevClient(config(), transport=stubs.asgi_transport(stubs.openjev_app(engine=engine)), max_inflight=1)
    server = build_server(config(), client=client, warm_limits=False)
    try:
        async with session(server, mode) as (s, env):
            slow = await s.start("tools/call", {"name": "ask", "arguments": ASK}, envelope=env)
            await in_flight(engine)
            await s.notify("notifications/cancelled", {"requestId": slow})
            await anyio.sleep(0.2)
            engine.delay_s = 0
            started = time.monotonic()
            res = await s.response(await s.start("tools/call", {"name": "ask", "arguments": ASK}, envelope=env), 5)
            elapsed = time.monotonic() - started
            assert "error" not in res and not res["result"].get("isError"), res
            assert elapsed < 2
            assert about(s, slow) == []
    finally:
        await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_a_second_call_waits_for_the_slot_until_the_first_is_cancelled(mode):
    engine = stubs.StubEngine(delay_s=30)
    client = OpenJevClient(config(), transport=stubs.asgi_transport(stubs.openjev_app(engine=engine)), max_inflight=1)
    server = build_server(config(), client=client, warm_limits=False)
    try:
        async with session(server, mode) as (s, env):
            first = await s.start("tools/call", {"name": "ask", "arguments": ASK}, envelope=env)
            await in_flight(engine)
            second = await s.start("tools/call", {"name": "ask", "arguments": ASK}, envelope=env)
            await anyio.sleep(0.3)
            assert len(engine.calls) == 1 and about(s, second) == []
            engine.delay_s = 0
            await s.notify("notifications/cancelled", {"requestId": first})
            res = await s.response(second, 5)
            assert not res["result"].get("isError")
            assert about(s, first) == []
    finally:
        await client.aclose()


# --- progress -------------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_progress_with_a_token_has_progress_total_and_message(mode):
    async with session(server_for(stubs.StubEngine()), mode) as (s, env):
        id = await s.start("tools/call", {"name": "ask", "arguments": ASK, "_meta": {"progressToken": "tok-1"}},
                           envelope=env)
        res = await s.response(id)
    assert not res["result"].get("isError")
    notes = progress(s, "tok-1")
    assert notes
    for m in notes:
        p = m["params"]
        assert p["progress"] > 0 and p["total"] == 1 and p["message"]
    assert [m["params"]["progress"] for m in notes] == sorted({m["params"]["progress"] for m in notes})
    assert s.messages.index(notes[-1]) < s.messages.index(res)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_no_progress_without_a_token(mode):
    async with session(server_for(stubs.StubEngine()), mode) as (s, env):
        for name, args in (("ask", ASK), ("yes_no", GREY), ("status", {})):
            await s.request("tools/call", {"name": name, "arguments": args}, envelope=env)
    assert progress(s) == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_progress_increases_strictly_with_a_constant_total_and_at_most_once_a_second(mode):
    engine = stubs.StubEngine(answers=grey, delay_s=1.05)
    async with session(server_for(engine), mode) as (s, env):
        started = time.monotonic()
        id = await s.start("tools/call", {"name": "yes_no", "arguments": GREY, "_meta": {"progressToken": 7}},
                           envelope=env)
        res = await s.response(id, 15)
        elapsed = time.monotonic() - started
    assert res["result"]["structuredContent"]["decision"] == "uncertain" and len(engine.calls) == 2
    values = [m["params"] for m in progress(s, 7)]
    assert [p["progress"] for p in values] == [1, 2]
    assert {p["total"] for p in values} == {2}
    assert all(p["message"] for p in values)
    assert len(values) <= int(elapsed) + 1


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_progress_inside_one_second_is_dropped(mode):
    engine = stubs.StubEngine(answers=grey)
    async with session(server_for(engine), mode) as (s, env):
        started = time.monotonic()
        id = await s.start("tools/call", {"name": "yes_no", "arguments": GREY, "_meta": {"progressToken": "fast"}},
                           envelope=env)
        await s.response(id)
        elapsed = time.monotonic() - started
    assert len(engine.calls) == 2 and elapsed < 1
    assert [m["params"]["progress"] for m in progress(s, "fast")] == [1]
