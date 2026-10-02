"""OpenJevClient: headers, bytes sent, retry policy, deadline, in-flight cap, cancellation."""
from __future__ import annotations

import asyncio
import hashlib
import os

import anyio
import httpx
import pytest
import stubs

from openjev_mcp import __version__, wire
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient

BASE = "http://oj.test:8080"
KEY = "sk-test-key"
BODY = wire.build_body("openjev-latest", "état ünï", {"q": {"type": "noul", "instructions": "ok?"}}, {"samples": 2})
OK_JSON = b'{"model":"openjev-0.1","answers":{},"usage":{"input_tokens":1,"output_tokens":0}}'
OK = (200, {"content-type": "application/json", "x-request-id": "req_ok",
            "server-timing": "model;dur=1.5, server;dur=3.0, total;dur=3.0"}, OK_JSON)
OVERLOADED = (529, {"retry-after": "3", "content-type": "application/json", "x-request-id": "req_o"},
              b'{"detail":{"error_type":"overloaded_error","message":"busy"}}')
TEXT_500 = (500, {"content-type": "text/plain"}, b"Internal Server Error")
BAD_400 = (400, {"content-type": "application/json"}, b'{"detail":"Too many score levels. Must have at most 10 levels."}')


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def cfg(**kw):
    kw.setdefault("base_url", BASE)
    kw.setdefault("api_key", KEY)
    kw.setdefault("origin_secret", "front-secret")
    return Config(**kw)


def client(transport, clock=None, *, jitter=0.1, **kw):
    clock = clock or Clock()
    return OpenJevClient(cfg(**kw), transport=transport, clock=clock, sleep=clock.sleep, jitter=lambda: jitter), clock


def assert_authed(transport):
    assert transport.requests
    for req in transport.requests:
        assert req.headers["authorization"] == f"Bearer {KEY}"
        assert req.headers["x-origin-secret"] == "front-secret"
        assert req.headers["user-agent"] == f"openjev-mcp/{__version__}"


@pytest.mark.anyio
async def test_auth_origin_and_agent_headers_on_post_and_get():
    t = stubs.fault_transport(sequence=[OK, (200, {}, b'{"status":"ok"}')])
    c, _ = client(t)
    await c.systemone(BODY, timeout_ms=1000)
    await c.get("/health")
    assert [r.method for r in t.requests] == ["POST", "GET"]
    assert [r.url.path for r in t.requests] == ["/v1/systemone", "/health"]
    assert_authed(t)
    await c.aclose()


@pytest.mark.anyio
async def test_no_auth_headers_when_not_configured():
    t = stubs.fault_transport(sequence=[OK])
    c = OpenJevClient(Config(base_url=BASE), transport=t)
    await c.systemone(BODY, timeout_ms=1000)
    assert "authorization" not in t.requests[0].headers and "x-origin-secret" not in t.requests[0].headers
    assert t.requests[0].headers["user-agent"].startswith("openjev-mcp/")


@pytest.mark.anyio
async def test_sent_bytes_are_wire_bytes_and_hash_matches():
    t = stubs.fault_transport(sequence=[OK])
    c, _ = client(t)
    res = await c.systemone(BODY, timeout_ms=1000)
    sent = t.requests[0].content
    assert sent == wire.body_bytes(BODY)
    assert b", " not in sent.split(b'"state"')[0] and "état".encode() in sent
    assert t.requests[0].headers["content-type"] == "application/json"
    assert res.body_hash == "sha256:" + hashlib.sha256(sent).hexdigest()


def test_http_py_posts_content_not_json():
    src = open(os.path.join(os.path.dirname(__file__), "..", "openjev_mcp", "http.py")).read()
    assert "content=" in src and "json=" not in src


@pytest.mark.anyio
async def test_success_result_fields_and_server_timing():
    t = stubs.fault_transport(sequence=[OK])
    c, clock = client(t)
    res = await c.systemone(BODY, timeout_ms=1000)
    assert res.status == 200 and res.data["model"] == "openjev-0.1"
    assert res.request_id == "req_ok" and res.attempts == 1 and res.retried is None
    assert res.server_timing == {"model_ms": 1.5, "server_ms": 3.0, "total_ms": 3.0}
    assert res.headers["x-request-id"] == "req_ok" and res.latency_ms >= 0
    assert clock.sleeps == []


@pytest.mark.anyio
async def test_get_has_no_body_hash_and_non_json_data_is_none():
    t = stubs.fault_transport(200, body=b"plain")
    c, _ = client(t)
    res = await c.get("/health")
    assert res.body_hash is None and res.data is None and res.server_timing is None


@pytest.mark.anyio
@pytest.mark.parametrize("reply,code,attempts", [
    (OVERLOADED, "OJ_OVERLOADED", 3),
    ((503, {"retry-after": "2"}, b'{"detail":{"error_type":"api_error","message":"down"}}'), "OJ_UNAVAILABLE", 3),
    ((429, {}, b"slow"), "OJ_RATE_LIMITED", 3),
    (TEXT_500, "OJ_SERVER", 2),
    (BAD_400, "OJ_REJECTED", 1),
    ((401, {}, b'{"detail":{"error_type":"authentication_error","message":"no"}}'), "OJ_AUTH", 1),
    ((403, {}, b'{"detail":{"error_type":"permission_error","message":"no"}}'), "OJ_FORBIDDEN", 1),
    ((413, {}, b'{"detail":{"error_type":"api_usage_error","message":"request body is larger than 9 bytes"}}'), "OJ_TOO_LARGE", 1),
    ((422, {}, b'{"detail":[{"loc":["body","samples"],"msg":"x","type":"t"}]}'), "OJ_VALIDATION", 1),
    ((404, {}, b'{"detail":"Not Found"}'), "OJ_NOT_FOUND", 1),
])
async def test_attempts_per_code(reply, code, attempts):
    t = stubs.fault_transport(sequence=[reply])
    c, clock = client(t, retries=2)
    with pytest.raises(ToolError) as ei:
        await c.systemone(BODY, timeout_ms=1000)
    assert ei.value.code == code
    assert len(t.requests) == attempts and len(clock.sleeps) == attempts - 1
    assert_authed(t)


@pytest.mark.anyio
async def test_500_with_images_is_bad_image_and_not_retried():
    t = stubs.fault_transport(sequence=[TEXT_500])
    c, _ = client(t)
    with pytest.raises(ToolError) as ei:
        await c.systemone(BODY, timeout_ms=1000, has_images=True)
    assert ei.value.code == "OJ_BAD_IMAGE" and len(t.requests) == 1


@pytest.mark.anyio
async def test_unreachable_is_retried_and_names_base_url():
    t = stubs.fault_transport(exc=httpx.ConnectError("refused"))
    c, clock = client(t, retries=2)
    with pytest.raises(ToolError) as ei:
        await c.systemone(BODY, timeout_ms=1000)
    err = ei.value
    assert err.code == "OJ_UNREACHABLE" and err.retryable and BASE in err.message
    assert len(t.requests) == 3 and clock.sleeps == [1.1, 1.1]


@pytest.mark.anyio
@pytest.mark.parametrize("think,attempts", [(0, 2), (64, 1)])
async def test_timeout_retry_once_unless_thinking(think, attempts):
    t = stubs.fault_transport(exc=httpx.ReadTimeout("slow"))
    c, _ = client(t, retries=2)
    with pytest.raises(ToolError) as ei:
        await c.systemone(BODY, timeout_ms=1500, think=think)
    err = ei.value
    assert err.code == "OJ_TIMEOUT" and BASE in err.message and "1500 ms" in err.message
    assert len(t.requests) == attempts


@pytest.mark.anyio
async def test_zero_retries_means_one_attempt_for_every_code():
    for reply in (OVERLOADED, TEXT_500):
        t = stubs.fault_transport(sequence=[reply])
        c, _ = client(t, retries=0)
        with pytest.raises(ToolError):
            await c.systemone(BODY, timeout_ms=1000)
        assert len(t.requests) == 1


@pytest.mark.anyio
async def test_client_side_timeout_fires_on_a_slow_transport():
    t = stubs.fault_transport(sequence=[OK], delay_s=2.0)
    c = OpenJevClient(cfg(retries=0), transport=t)
    with anyio.fail_after(1.5), pytest.raises(ToolError) as ei:
        await c.systemone(BODY, timeout_ms=100)
    assert ei.value.code == "OJ_TIMEOUT" and "100 ms" in ei.value.message


@pytest.mark.anyio
async def test_delay_is_retry_after_plus_jitter():
    t = stubs.fault_transport(sequence=[OVERLOADED])
    c, clock = client(t, retries=2, jitter=0.2)
    with pytest.raises(ToolError):
        await c.systemone(BODY, timeout_ms=1000)
    assert clock.sleeps == [3.2, 3.2]


@pytest.mark.anyio
async def test_default_delay_is_one_second_plus_jitter():
    t = stubs.fault_transport(sequence=[TEXT_500])
    c, clock = client(t, retries=2, jitter=0.05)
    with pytest.raises(ToolError):
        await c.systemone(BODY, timeout_ms=1000)
    assert clock.sleeps == [1.05]


@pytest.mark.anyio
async def test_recovery_fills_retried():
    t = stubs.fault_transport(sequence=[OVERLOADED, OK])
    c, clock = client(t, retries=2)
    res = await c.systemone(BODY, timeout_ms=1000)
    assert res.attempts == 2 and res.retried == {"status": 529, "code": "OJ_OVERLOADED", "attempts": 2}
    assert len(t.requests) == 2 and clock.sleeps == [3.1]
    assert t.requests[0].content == t.requests[1].content


@pytest.mark.anyio
async def test_deadline_stops_further_attempts():
    t = stubs.fault_transport(sequence=[OVERLOADED])
    c, clock = client(t, retries=10, jitter=0.0)
    with pytest.raises(ToolError) as ei:
        await c.systemone(BODY, timeout_ms=1000, deadline_ms=7000)
    assert ei.value.code == "OJ_OVERLOADED"
    assert len(t.requests) == 3 and clock.sleeps == [3.0, 3.0]    # a third sleep would end past 7 s


@pytest.mark.anyio
async def test_deadline_bounds_each_attempt_timeout():
    t = stubs.fault_transport(sequence=[OK], delay_s=2.0)
    c = OpenJevClient(cfg(retries=0), transport=t)
    with anyio.fail_after(1.5), pytest.raises(ToolError) as ei:
        await c.systemone(BODY, timeout_ms=60000, deadline_ms=120)
    assert ei.value.code == "OJ_TIMEOUT"
    n = int(ei.value.message.split(" in ")[1].split(" ms")[0])
    assert 0 < n <= 120


@pytest.mark.anyio
async def test_inflight_cap_serialises_calls():
    live = peak = 0

    async def handler(request):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.05)
        live -= 1
        return httpx.Response(200, content=OK_JSON, request=request)

    for cap, expect in ((1, 1), (2, 2)):
        live = peak = 0
        c = OpenJevClient(cfg(), transport=httpx.MockTransport(handler), max_inflight=cap)
        async def one():
            await c.systemone(BODY, timeout_ms=1000)

        async with anyio.create_task_group() as tg:
            for _ in range(2):
                tg.start_soon(one)
        assert peak == expect


@pytest.mark.anyio
async def test_semaphore_not_held_during_retry_sleep():
    seen = []
    c: OpenJevClient

    async def sleep(s):
        seen.append(c._sem.value)

    t = stubs.fault_transport(sequence=[OVERLOADED, OK])
    c = OpenJevClient(cfg(), transport=t, max_inflight=1, sleep=sleep, jitter=lambda: 0.0)
    await c.systemone(BODY, timeout_ms=1000)
    assert seen == [1]


@pytest.mark.anyio
async def test_cancellation_mid_request_releases_semaphore():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(30)
        return httpx.Response(200, content=OK_JSON, request=request)

    c = OpenJevClient(cfg(), transport=httpx.MockTransport(handler), max_inflight=1)
    scope = anyio.CancelScope()

    async def first():
        with scope:
            await c.systemone(BODY, timeout_ms=60000)

    async with anyio.create_task_group() as tg:
        tg.start_soon(first)
        await anyio.sleep(0.05)
        assert c._sem.value == 0
        scope.cancel()
    assert scope.cancelled_caught and c._sem.value == 1
    with anyio.fail_after(2):
        res = await c.systemone(BODY, timeout_ms=1000)
    assert res.status == 200 and calls == 2


@pytest.mark.anyio
async def test_cancellation_during_retry_sleep_propagates():
    t = stubs.fault_transport(sequence=[OVERLOADED])
    c = OpenJevClient(cfg(retries=2), transport=t, max_inflight=1, jitter=lambda: 0.0)
    with anyio.move_on_after(0.1) as scope:
        await c.systemone(BODY, timeout_ms=1000)       # sleeps 3 s after the first 529
    assert scope.cancelled_caught and len(t.requests) == 1 and c._sem.value == 1


@pytest.mark.anyio
async def test_get_accepts_listed_statuses_only():
    t = stubs.fault_transport(404, body=b'{"detail":"Not Found"}')
    c, _ = client(t)
    res = await c.get("/v1/limits", ok=(200, 404))
    assert res.status == 404
    with pytest.raises(ToolError) as ei:
        await c.get("/v1/limits")
    assert ei.value.code == "OJ_NOT_FOUND" and len(t.requests) == 2
    assert_authed(t)


@pytest.mark.anyio
async def test_get_single_attempt_unless_retry():
    t = stubs.fault_transport(sequence=[(503, {"retry-after": "1"}, b'{"detail":{"error_type":"api_error","message":"x"}}')])
    c, clock = client(t, retries=2)
    with pytest.raises(ToolError) as ei:
        await c.get("/v1/models")
    assert ei.value.code == "OJ_UNAVAILABLE" and len(t.requests) == 1
    with pytest.raises(ToolError):
        await c.get("/v1/models", retry=True)
    assert len(t.requests) == 4 and len(clock.sleeps) == 2


@pytest.mark.anyio
async def test_get_unreachable():
    c, _ = client(stubs.fault_transport(exc=httpx.ConnectError("no")))
    with pytest.raises(ToolError) as ei:
        await c.get("/health")
    assert ei.value.code == "OJ_UNREACHABLE" and BASE in ei.value.message


@pytest.mark.anyio
async def test_against_the_real_app_with_a_stub_engine():
    app = stubs.openjev_app(api_key=KEY, origin_secret="front-secret")
    async with OpenJevClient(cfg(), transport=stubs.asgi_transport(app)) as c:
        res = await c.systemone(wire.build_body("openjev-latest", "s", {"q": {"type": "noul", "instructions": "i"}}),
                                timeout_ms=5000)
        assert res.status == 200 and res.data["answers"]["q"]["type"] == "noul"
        assert res.request_id.startswith("req_") and set(res.server_timing) == {"model_ms", "server_ms", "total_ms"}
        assert (await c.get("/health")).data == {"status": "ok"}
    bad = OpenJevClient(cfg(api_key="wrong"), transport=stubs.asgi_transport(app))
    with pytest.raises(ToolError) as ei:
        await bad.systemone(BODY, timeout_ms=5000)
    assert ei.value.code == "OJ_AUTH" and ei.value.http_status == 401 and ei.value.request_id


@pytest.mark.anyio
async def test_real_app_529_retries_then_recovers():
    from openjev.engine import Overloaded

    state = {"n": 0}

    def fault(q, s, o):
        state["n"] += 1
        return Overloaded("busy") if state["n"] < 3 else None

    app = stubs.openjev_app(engine=stubs.StubEngine(fault=fault))
    c, clock = client(stubs.asgi_transport(app), retries=2)
    res = await c.systemone(wire.build_body("openjev-latest", "s", {"q": {"type": "noul", "instructions": "i"}}),
                            timeout_ms=5000)
    assert res.attempts == 3 and clock.sleeps == [1.1, 1.1]
    assert res.retried["code"] == "OJ_OVERLOADED" and res.retried["status"] == 529


# --- batch pool and per-call retries (P05) ---

def gauge_transport(delay_s=0.03):
    """Counts requests in flight; .peak is the maximum."""
    state = {"now": 0, "peak": 0}

    async def handler(request):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        try:
            await asyncio.sleep(delay_s)
            return httpx.Response(200, headers={"content-type": "application/json"}, content=OK_JSON, request=request)
        finally:
            state["now"] -= 1

    transport = stubs.RecordingTransport(handler)
    transport.state = state
    return transport


@pytest.mark.anyio
async def test_batch_pool_has_its_own_cap():
    transport = gauge_transport()
    c = OpenJevClient(cfg(max_inflight_batch=3), transport=transport, max_inflight=1)
    async with c:
        async with anyio.create_task_group() as tg:
            for _ in range(8):
                tg.start_soon(lambda: c.systemone(BODY, timeout_ms=5000, pool="batch"))
    assert transport.state["peak"] == 3
    transport = gauge_transport()
    c = OpenJevClient(cfg(max_inflight_batch=3), transport=transport, max_inflight=1)
    async with c:
        async with anyio.create_task_group() as tg:
            for _ in range(4):
                tg.start_soon(lambda: c.systemone(BODY, timeout_ms=5000))
    assert transport.state["peak"] == 1


@pytest.mark.anyio
async def test_saturated_batch_pool_does_not_starve_the_default_pool():
    transport = gauge_transport(0.1)
    c = OpenJevClient(cfg(max_inflight_batch=1), transport=transport, max_inflight=1)
    async with c:
        done = []
        async with anyio.create_task_group() as tg:
            async def batch():
                await c.systemone(BODY, timeout_ms=5000, pool="batch")
                done.append("batch")

            async def gate():
                await c.systemone(BODY, timeout_ms=5000)
                done.append("gate")

            tg.start_soon(batch)
            tg.start_soon(batch)
            await anyio.sleep(0.02)
            tg.start_soon(gate)
    assert done.index("gate") < 3 and transport.state["peak"] == 2   # the gate ran beside the running batch read


@pytest.mark.anyio
async def test_unknown_pool_is_a_programming_error():
    c, _ = client(stubs.fault_transport(sequence=[OK]))
    async with c:
        with pytest.raises(ValueError):
            await c.systemone(BODY, timeout_ms=5000, pool="other")


@pytest.mark.anyio
@pytest.mark.parametrize("retries,calls", [(0, 1), (1, 2), (None, 3)])
async def test_retries_override_per_call(retries, calls):
    transport = stubs.fault_transport(sequence=[OVERLOADED])
    c, clock = client(transport, retries=2)
    async with c:
        with pytest.raises(ToolError) as e:
            await c.systemone(BODY, timeout_ms=5000, retries=retries)
    assert e.value.code == "OJ_OVERLOADED" and len(transport.requests) == calls


@pytest.mark.anyio
async def test_retries_zero_also_disables_retry_once_codes():
    transport = stubs.fault_transport(sequence=[TEXT_500, OK])
    c, _ = client(transport, retries=2)
    async with c:
        with pytest.raises(ToolError) as e:
            await c.systemone(BODY, timeout_ms=5000, retries=0)
    assert e.value.code == "OJ_SERVER" and len(transport.requests) == 1


# ---------------------------------------------------------------- chat (P16)

CHAT_OK = (200, {"content-type": "application/json", "x-request-id": "req_chat"},
           b'{"choices":[{"message":{"content":"4"},"finish_reason":"stop"}],"usage":{"completion_tokens":1}}')
CHAT_BODY = {"model": "diffusiongemma-26b", "max_tokens": 16, "messages": [{"role": "user", "content": "2+2?"}]}


@pytest.mark.anyio
async def test_chat_posts_wire_bytes_to_the_chat_path_with_auth():
    t = stubs.fault_transport(sequence=[CHAT_OK])
    c, _ = client(t)
    res = await c.chat(CHAT_BODY, timeout_ms=5000)
    req = t.requests[0]
    assert (req.method, req.url.path) == ("POST", "/v1/chat/completions")
    assert req.content == wire.body_bytes(CHAT_BODY) and res.body_hash == wire.body_hash(req.content)
    assert res.data["choices"][0]["message"]["content"] == "4" and res.request_id == "req_chat"
    assert_authed(t)


@pytest.mark.anyio
async def test_chat_maps_openai_errors_and_does_not_retry_a_404():
    err = (404, {"content-type": "application/json", "x-request-id": "req_n"},
           b'{"error":{"message":"The model does not exist","type":"invalid_request_error","code":"model_not_found"}}')
    t = stubs.fault_transport(sequence=[err])
    c, _ = client(t)
    with pytest.raises(ToolError) as e:
        await c.chat(CHAT_BODY, timeout_ms=5000)
    assert e.value.code == "OJ_UNKNOWN_MODEL" and e.value.http_status == 404 and e.value.request_id == "req_n"
    assert len(t.requests) == 1


@pytest.mark.anyio
async def test_chat_retries_529_then_recovers():
    over = (529, {"retry-after": "1", "content-type": "application/json"},
            b'{"error":{"message":"busy","type":"overloaded_error"}}')
    t = stubs.fault_transport(sequence=[over, CHAT_OK])
    c, clock = client(t)
    res = await c.chat(CHAT_BODY, timeout_ms=5000)
    assert res.attempts == 2 and res.retried["code"] == "OJ_OVERLOADED" and clock.sleeps == [1.1]


@pytest.mark.anyio
async def test_chat_retries_zero_is_one_attempt():
    t = stubs.fault_transport(sequence=[TEXT_500])
    c, _ = client(t)
    with pytest.raises(ToolError):
        await c.chat(CHAT_BODY, timeout_ms=5000, retries=0)
    assert len(t.requests) == 1


@pytest.mark.anyio
async def test_chat_deadline_bounds_the_attempt():
    t = stubs.fault_transport(sequence=[CHAT_OK], delay_s=0.5)
    c, _ = client(t)
    with pytest.raises(ToolError) as e:
        await c.chat(CHAT_BODY, timeout_ms=5000, deadline_ms=50, retries=0)
    assert e.value.code == "OJ_TIMEOUT"
