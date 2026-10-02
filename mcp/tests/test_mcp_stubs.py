import json

import httpx
import pytest

from openjev.engine import Overloaded, SchemaError, Upstream
from stubs import (StubEngine, asgi_transport, default_answers, fail_on_request_transport, fault_transport,
                   openjev_app, replay_transport, captured, overload_transport, slow_transport)

pytestmark = pytest.mark.anyio

BODY = {"model": "openjev-latest", "state": "hi", "questions": {"q": {"type": "noul", "instructions": "?"}}}
KEY = "sk-test"


def client_for(app):
    return httpx.AsyncClient(transport=asgi_transport(app), base_url="http://openjev")


async def post(app, body=BODY, headers=None):
    async with client_for(app) as c:
        return await c.post("/v1/systemone", json=body, headers=headers)


async def test_200_default_answers():
    r = await post(openjev_app())
    assert r.status_code == 200
    j = r.json()
    assert j["answers"]["q"] == {"type": "noul", "noul": 0.9}
    assert j["usage"] == {"input_tokens": 100, "output_tokens": 0}
    assert r.headers["x-request-id"].startswith("req_") and "server-timing" in r.headers


async def test_default_answers_shapes():
    qs = {"n": {"type": "noul"}, "c": {"type": "choice", "criteria": {"a": "x", "b": "y"}},
          "s": {"type": "score", "criteria": ["lo", "hi"]}}
    a = default_answers(qs, "s", {})
    assert a["c"]["choice"] == "a" and set(a["c"]["probabilities"]) == {"a", "b"} and "confidence" in a["c"]
    assert a["s"]["legend"] == {"0": "lo", "1": "hi"} and set(a["s"]["probabilities"]) == {"0", "1"} and "score" in a["s"]
    assert a["s"]["confidence"] == 0.7


async def test_stub_engine_records_calls():
    eng = StubEngine(input_tokens=7, thought_tokens=3)
    r = await post(openjev_app(engine=eng), {**BODY, "samples": 2})
    assert r.json()["usage"] == {"input_tokens": 7, "output_tokens": 3}
    assert eng.calls[0]["options"]["samples"] == 2


async def test_401_wrong_key_and_200_right_key():
    app = openjev_app(api_key=KEY)
    r = await post(app, headers={"authorization": "Bearer nope"})
    assert r.status_code == 401 and r.json()["detail"]["error_type"] == "authentication_error"
    assert (await post(app, headers={"authorization": f"Bearer {KEY}"})).status_code == 200


async def test_403_authentication_error_without_key():
    r = await post(openjev_app(api_key=KEY))
    assert r.status_code == 403 and r.json()["detail"]["error_type"] == "authentication_error"


async def test_403_permission_error_origin_secret():
    app = openjev_app(origin_secret="sec")
    r = await post(app)
    assert r.status_code == 403 and r.json()["detail"]["error_type"] == "permission_error"
    assert (await post(app, headers={"x-origin-secret": "sec"})).status_code == 200


async def test_413_max_body_bytes():
    r = await post(openjev_app(max_body_bytes=50))
    assert r.status_code == 413 and r.json()["detail"]["error_type"] == "api_usage_error"


async def test_422_list_detail():
    r = await post(openjev_app(), {"model": "openjev-latest", "state": "x"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)


async def test_400_schema_error_string_detail():
    app = openjev_app(engine=StubEngine(fault=lambda q, s, o: SchemaError("bad criteria", ["body", "questions", "q"])))
    r = await post(app)
    assert r.status_code == 400 and r.json()["detail"] == "bad criteria"


async def test_529_overloaded():
    r = await post(openjev_app(engine=StubEngine(fault=lambda q, s, o: Overloaded("busy"))))
    assert r.status_code == 529 and r.headers["retry-after"] == "1"
    assert r.json()["detail"]["error_type"] == "overloaded_error"


async def test_503_connect_error():
    r = await post(openjev_app(engine=StubEngine(fault=lambda q, s, o: httpx.ConnectError("down"))))
    assert r.status_code == 503 and r.headers["retry-after"] == "2"
    assert r.json()["detail"]["error_type"] == "api_error"


async def test_400_upstream_rejected():
    r = await post(openjev_app(engine=StubEngine(fault=lambda q, s, o: Upstream("too long"))))
    assert r.status_code == 400 and "the model rejected this request" in r.json()["detail"]


async def test_unknown_model_400():
    r = await post(openjev_app(), {**BODY, "model": "nope"})
    assert r.status_code == 400 and "Unknown model" in r.json()["detail"]["message"]


async def test_health_and_models():
    async with client_for(openjev_app()) as c:
        r = await c.get("/health")
        assert (r.status_code, r.json()) == (200, {"status": "ok"})
        assert (await c.get("/v1/models")).status_code == 200


async def test_fault_transport_connect_error_and_timeout():
    for exc in (httpx.ConnectError("refused"), httpx.ReadTimeout("slow")):
        t = fault_transport(exc=exc)
        async with httpx.AsyncClient(transport=t, base_url="http://x") as c:
            with pytest.raises(type(exc)):
                await c.get("/health")
        assert len(t.requests) == 1


async def test_fault_transport_statuses():
    t = fault_transport(429, headers={"retry-after": "3"}, body=b'{"detail":{}}')
    async with httpx.AsyncClient(transport=t, base_url="http://x") as c:
        r = await c.post("/v1/systemone", json=BODY)
    assert r.status_code == 429 and r.headers["retry-after"] == "3"
    t = fault_transport(500, headers={"content-type": "text/plain"}, body="Internal Server Error")
    async with httpx.AsyncClient(transport=t, base_url="http://x") as c:
        r = await c.post("/v1/systemone", json=BODY)
    assert r.status_code == 500 and r.text == "Internal Server Error"
    assert r.headers["content-type"].startswith("text/plain")


async def test_fault_transport_sequence():
    ok = httpx.Response(200, json={"ok": True})
    t = fault_transport(sequence=[httpx.ConnectError("x"), (529, {"retry-after": "1"}, b"{}"), ok])
    async with httpx.AsyncClient(transport=t, base_url="http://x") as c:
        with pytest.raises(httpx.ConnectError):
            await c.get("/a")
        assert (await c.get("/b")).status_code == 529
        assert (await c.get("/c")).json() == {"ok": True}
        assert (await c.get("/d")).json() == {"ok": True}   # the last item repeats
    assert [r.url.path for r in t.requests] == ["/a", "/b", "/c", "/d"]


async def test_fail_on_request_transport_raises():
    t = fail_on_request_transport()
    async with httpx.AsyncClient(transport=t, base_url="http://x") as c:
        with pytest.raises(AssertionError, match="unexpected request"):
            await c.get("/health")
    assert len(t.requests) == 1


async def test_replay_serves_captured_yes_no():
    t = replay_transport(["ex-yes-no"])
    req = {"model": "openjev-latest", "samples": 1, "state": "I was charged twice this month.",
           "questions": {"q": {"type": "noul", "instructions": "Is this a billing issue?",
                               "criteria": {"true": "about charges, refunds, invoices", "false": "anything else"}}}}
    async with httpx.AsyncClient(transport=t, base_url="http://x") as c:
        r = await c.post("/v1/systemone", content=json.dumps(dict(reversed(req.items()))))   # key order is irrelevant
    assert r.status_code == 200
    assert r.json()["answers"]["q"]["noul"] > 0.99
    assert r.headers["x-request-id"].startswith("req_")
    assert r.headers["server-timing"].startswith("model;dur=")


async def test_replay_get_cases():
    t = replay_transport()
    async with httpx.AsyncClient(transport=t, base_url="http://x") as c:
        assert (await c.get("/health")).json() == {"status": "ok"}
        assert (await c.get("/v1/models")).status_code == 200


async def test_replay_unmatched_names_closest_case():
    t = replay_transport()
    async with httpx.AsyncClient(transport=t, base_url="http://x") as c:
        with pytest.raises(AssertionError, match=r"closest case id: \S+"):
            await c.post("/v1/systemone", json={"model": "openjev-latest", "state": "unrelated", "questions": {}})


async def test_overload_transport_then_delegates():
    t = overload_transport(2, asgi_transport(openjev_app()), retry_after=0.5)
    async with httpx.AsyncClient(transport=t, base_url="http://openjev") as c:
        codes = [(await c.post("/v1/systemone", json=BODY)).status_code for _ in range(3)]
        r = await c.post("/v1/systemone", json=BODY)
    assert codes == [529, 529, 200] and r.status_code == 200 and len(t.requests) == 4
    async with httpx.AsyncClient(transport=overload_transport([1], asgi_transport(openjev_app())), base_url="http://o") as c:
        first = await c.post("/v1/systemone", json=BODY)
        second = await c.post("/v1/systemone", json=BODY)
    assert (first.status_code, second.status_code, second.headers["retry-after"]) == (200, 529, "0.01")


async def test_slow_transport_delays():
    import time
    t0 = time.monotonic()
    async with httpx.AsyncClient(transport=slow_transport(0.15, asgi_transport(openjev_app())), base_url="http://o") as c:
        r = await c.post("/v1/systemone", json=BODY)
    assert r.status_code == 200 and time.monotonic() - t0 >= 0.14


def test_captured_returns_body():
    assert "answers" in captured("ex-ask")
    with pytest.raises(KeyError):
        captured("nope")
