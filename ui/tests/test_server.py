"""Tests for ui/server.py against a fake OpenJev built from httpx.MockTransport.

Covers the /v1 passthrough (status, body, headers, Server-Timing, auth), upstream-down errors,
health shapes, SSE chunking and disconnect cleanup at the ASGI level, and static serving.
"""
import asyncio
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

_spec = importlib.util.spec_from_file_location("ojui_server", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

UP = "http://upstream.test"
MODELS = {"models": [{"name": "openjev-latest", "description": "d", "release_date": "2026-01-01"}]}


@pytest.fixture
def static_dir(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>ojui</title>")
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "main.js").write_text("export const x = 1;\n")
    return tmp_path


def make_client(handler, static_dir, **kw):
    app = server.create_app(UP + "/", static_dir=static_dir, transport=httpx.MockTransport(handler), **kw)
    return TestClient(app)


def test_passthrough_200_appends_upstream_timing(static_dir):
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["headers"] = req.headers
        seen["body"] = req.content
        return httpx.Response(200, json={"answers": {"a": {"type": "noul", "noul": 0.9}}},
                              headers={"server-timing": "model;dur=0.0, server;dur=2.8, total;dur=3.0",
                                       "x-request-id": "req_abc", "connection": "keep-alive"})

    with make_client(handler, static_dir) as c:
        r = c.post("/v1/systemone?x=1", content=b'{"model":"openjev-latest"}',
                   headers={"content-type": "application/json", "accept": "application/json"})
    assert r.status_code == 200
    assert r.json()["answers"]["a"]["noul"] == 0.9
    assert seen["url"] == UP + "/v1/systemone?x=1"
    assert seen["body"] == b'{"model":"openjev-latest"}'
    assert seen["headers"]["accept-encoding"] == "identity"
    assert "authorization" not in seen["headers"]
    st = r.headers["server-timing"]
    assert st.startswith("model;dur=0.0, server;dur=2.8, total;dur=3.0, upstream;dur=")
    assert float(r.headers["x-ojui-upstream-ms"]) >= 0
    assert r.headers["x-request-id"] == "req_abc"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-accel-buffering"] == "no"


def test_no_upstream_timing_header_is_only_upstream(static_dir):
    with make_client(lambda req: httpx.Response(200, json=MODELS), static_dir) as c:
        r = c.get("/v1/models")
    assert r.headers["server-timing"].startswith("upstream;dur=")
    assert "," not in r.headers["server-timing"]


def test_422_passes_through_unchanged(static_dir):
    body = {"detail": [{"loc": ["body", "questions", "q", "score", "criteria"], "msg": "bad", "type": "x"}]}
    raw = json.dumps(body).encode()
    with make_client(lambda req: httpx.Response(422, content=raw, headers={"content-type": "application/json"}),
                     static_dir) as c:
        r = c.post("/v1/systemone", content=b"{}")
    assert r.status_code == 422
    assert r.content == raw


def test_key_injected_and_client_auth_wins(static_dir):
    seen = []

    def handler(req):
        seen.append((req.headers.get("authorization"), req.headers.get("x-origin-secret")))
        return httpx.Response(200, json=MODELS)

    with make_client(handler, static_dir, api_key="sekret", origin_secret="orig") as c:
        c.get("/v1/models")
        c.get("/v1/models", headers={"authorization": "Bearer wrong"})
        cfg = c.get("/ui/api/config").json()
    assert seen == [("Bearer sekret", "orig"), ("Bearer wrong", "orig")]
    assert cfg["authConfigured"] is True and cfg["proxyBase"] == "" and cfg["openjevUrl"] == UP
    assert cfg["limits"]["scoreMaxLevels"] == 10


def test_upstream_down_is_502(static_dir):
    def handler(req):
        raise httpx.ConnectError("Connection refused", request=req)

    with make_client(handler, static_dir) as c:
        r = c.post("/v1/systemone", content=b"{}")
    assert r.status_code == 502
    d = r.json()["detail"]
    assert d["error_type"] == "upstream_unreachable"
    assert d["hint"] == "mise run startOpenJev"
    assert UP in d["message"]


def test_read_timeout_is_504(static_dir):
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    with make_client(handler, static_dir) as c:
        r = c.post("/v1/systemone", content=b"{}")
    assert r.status_code == 504
    assert r.json()["detail"]["error_type"] == "upstream_timeout"


def test_health_ok(static_dir):
    handler = lambda req: httpx.Response(200, json=MODELS, headers={"x-request-id": "req_1",
                                                                    "server-timing": "total;dur=0.1"})
    with make_client(handler, static_dir) as c:
        c.get("/v1/models")
        h = c.get("/ui/api/health").json()
    assert h["ok"] is True
    assert h["upstream"]["reachable"] is True and h["upstream"]["status"] == 200
    assert h["upstream"]["requestId"] == "req_1" and h["upstream"]["serverTiming"] == "total;dur=0.1"
    assert h["upstream"]["errorType"] is None
    assert h["models"][0]["name"] == "openjev-latest"
    assert h["proxy"]["requests"] == 1
    assert isinstance(h["checkedAt"], int)


def test_health_down_and_auth(static_dir):
    def down(req):
        raise httpx.ConnectError("refused", request=req)

    with make_client(down, static_dir) as c:
        h = c.get("/ui/api/health")
    assert h.status_code == 200
    h = h.json()
    assert h["ok"] is False and h["models"] == []
    assert h["upstream"]["reachable"] is False
    assert h["upstream"]["errorType"] == "upstream_unreachable"
    assert h["upstream"]["error"].startswith("ConnectError: ")

    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)

    with make_client(slow, static_dir) as c:
        assert c.get("/ui/api/health").json()["upstream"]["errorType"] == "upstream_timeout"

    denied = lambda req: httpx.Response(401, json={"detail": {"error_type": "authentication_error",
                                                              "message": "Invalid API key."}})
    with make_client(denied, static_dir) as c:
        h = c.get("/ui/api/health").json()
    assert h["ok"] is False and h["upstream"]["reachable"] is True
    assert h["upstream"]["errorType"] == "auth" and h["upstream"]["error"] == "Invalid API key."


class _SSE(httpx.AsyncByteStream):
    def __init__(self, chunks, forever=False):
        self.chunks, self.forever, self.closed = chunks, forever, False

    async def __aiter__(self):
        for ch in self.chunks:
            yield ch
            await asyncio.sleep(0)
        while self.forever:
            yield b": keepalive\n\n"
            await asyncio.sleep(0.01)

    async def aclose(self):
        self.closed = True


async def _run_asgi(app, path, send_hook=None):
    """Drive the ASGI app directly so each http.response.body message is observable."""
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": [(b"content-type", b"application/json")],
             "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8090)}
    sent = []
    msgs = iter([{"type": "http.request", "body": b"{}", "more_body": False}])

    async def receive():
        try:
            return next(msgs)
        except StopIteration:
            await asyncio.sleep(3600)

    async def send(message):
        sent.append(message)
        if send_hook:
            send_hook(message)

    await app(scope, receive, send)
    return sent


def test_sse_arrives_in_multiple_chunks(static_dir):
    chunks = [b'data: {"choices":[{"delta":{"content":"He"}}]}\n\n',
              b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n', b"data: [DONE]\n\n"]
    stream = _SSE(chunks)
    handler = lambda req: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    app = server.create_app(UP, static_dir=static_dir, transport=httpx.MockTransport(handler))
    sent = asyncio.run(_run_asgi(app, "/v1/chat/completions"))
    start = sent[0]
    assert start["type"] == "http.response.start" and start["status"] == 200
    assert dict(start["headers"])[b"content-type"] == b"text/event-stream"
    bodies = [m["body"] for m in sent if m["type"] == "http.response.body" and m["body"]]
    assert bodies == chunks
    assert stream.closed


def test_client_disconnect_closes_upstream(static_dir):
    stream = _SSE([b"data: 1\n\n"], forever=True)
    handler = lambda req: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    app = server.create_app(UP, static_dir=static_dir, transport=httpx.MockTransport(handler))
    count = {"n": 0}

    def hook(message):
        if message["type"] == "http.response.body":
            count["n"] += 1
            if count["n"] >= 3:
                raise OSError("client went away")

    with pytest.raises(Exception):
        asyncio.run(_run_asgi(app, "/v1/chat/completions", hook))
    assert stream.closed


def test_static_index_and_js_mime(static_dir):
    with make_client(lambda req: httpx.Response(200), static_dir) as c:
        r = c.get("/")
        assert r.status_code == 200 and "ojui" in r.text
        assert r.headers["cache-control"] == "no-store"
        js = c.get("/js/main.js")
        assert js.status_code == 200
        assert js.headers["content-type"].startswith("text/javascript")
        assert js.headers["cache-control"] == "no-store"
        assert c.get("/nope/missing.js").status_code == 404


def test_missing_static_dir_is_a_clean_404(tmp_path):
    with make_client(lambda req: httpx.Response(200, json=MODELS), tmp_path / "nope") as c:
        r = c.get("/")
        assert r.status_code == 404 and "static directory" in r.text
        assert c.get("/v1/models").status_code == 200


def test_upstream_date_and_server_not_duplicated(static_dir):
    handler = lambda req: httpx.Response(200, json=MODELS, headers={"server": "uvicorn", "date": "x"})
    with make_client(handler, static_dir) as c:
        r = c.get("/v1/models")
    assert r.headers.get_list("date") != ["x"]
    assert len(r.headers.get_list("server")) <= 1


def test_cross_origin_and_rebinding_are_refused(static_dir):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=MODELS)

    with make_client(handler, static_dir, api_key="k") as c:
        assert c.post("/v1/systemone", content=b"{}", headers={"origin": "http://evil.test"}).status_code == 403
        assert c.post("/v1/systemone", content=b"{}", headers={"origin": "null"}).status_code == 403
        assert c.get("/v1/models", headers={"sec-fetch-site": "cross-site"}).status_code == 403
        assert c.get("/ui/api/health", headers={"origin": "http://evil.test"}).status_code == 403
        assert c.get("/v1/../health").status_code in (400, 404)
        assert c.get("/v1/a/%2E%2E/%2E%2E/health").status_code == 400
        assert not calls
        assert c.post("/v1/systemone", content=b"{}", headers={"origin": "http://testserver"}).status_code == 200
    app = server.create_app(UP, static_dir=static_dir, transport=httpx.MockTransport(handler),
                            allowed_hosts=server.LOOPBACK_HOSTS)
    with TestClient(app) as c:
        assert c.get("/v1/models", headers={"host": "attacker.test:8090"}).status_code == 403
        assert c.get("/v1/models", headers={"host": "127.0.0.1:8090"}).status_code == 200
        assert c.get("/v1/models", headers={"host": "[::1]:8090"}).status_code == 200


SERVER_PY = Path(__file__).resolve().parents[1] / "server.py"


def _spawn(upstream="http://127.0.0.1:1"):
    """Start the real dev server (default upstream down: port 1 refuses at once) and wait until it listens."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "UI_PORT": str(port), "UI_HOST": "127.0.0.1",
           "OPENJEV_URL": upstream, "PYTHONUNBUFFERED": "1"}
    p = subprocess.Popen([sys.executable, str(SERVER_PY)], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 15
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return p, port
        except OSError:
            if p.poll() is not None or time.monotonic() > deadline:
                p.kill()
                _, err = p.communicate()
                pytest.fail(f"server did not start: {err.decode(errors='replace')}")
            time.sleep(0.05)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("sigs", [
    pytest.param([signal.SIGINT], id="sigint_once"),
    pytest.param([signal.SIGINT, signal.SIGINT], id="sigint_twice"),  # terminal + mise forwarding
    pytest.param([signal.SIGTERM], id="sigterm"),
])
def test_signal_shutdown_is_clean(sigs):
    p, port = _spawn()
    try:
        # use the lifespan client once, as in a real session (502: the upstream is down)
        assert httpx.post(f"http://127.0.0.1:{port}/v1/systemone", json={}, timeout=5).status_code == 502
        for i, sig in enumerate(sigs):
            if i:
                time.sleep(0.05)
            p.send_signal(sig)
        out, err = p.communicate(timeout=15)
    finally:
        if p.poll() is None:
            p.kill()
            p.communicate()
    assert p.returncode == 0, err.decode(errors="replace")
    assert b"Traceback" not in err, err.decode(errors="replace")
    assert b"CancelledError" not in err
    assert b"ojui: stopped" in err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_signal_during_open_request_hints_then_force_quits():
    # upstream sends headers, then stalls: the proxied request stays open across the shutdown
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    stop = threading.Event()

    def stall():
        conn, _ = srv.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\n\r\n")
        stop.wait(30)
        conn.close()

    threading.Thread(target=stall, daemon=True).start()
    p, port = _spawn(f"http://127.0.0.1:{srv.getsockname()[1]}")
    client = socket.create_connection(("127.0.0.1", port))
    try:
        client.sendall(b"POST /v1/systemone HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 2\r\n"
                       b"Content-Type: application/json\r\n\r\n{}")
        time.sleep(1)
        p.send_signal(signal.SIGINT)  # terminal + mise forwarding: one press
        p.send_signal(signal.SIGINT)
        time.sleep(2)
        assert p.poll() is None  # still waiting for the open request
        p.send_signal(signal.SIGINT)  # a real second press
        out, err = p.communicate(timeout=15)
    finally:
        stop.set()
        client.close()
        srv.close()
        if p.poll() is None:
            p.kill()
            p.communicate()
    assert p.returncode == 0, err.decode(errors="replace")
    assert b"waiting for 1 open request" in err, err.decode(errors="replace")
    assert b"Traceback" not in err, err.decode(errors="replace")
