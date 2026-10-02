"""OpenJev UI dev server: serves ui/static and proxies /v1/* to a running OpenJev.

Owns: the /ui/api/config and /ui/api/health endpoints, the streaming /v1 passthrough (JSON and
SSE on one code path, API key kept server-side), no-store static serving and the startup banner.
Started in the background by `mise run start` (log: .openjev-ui.log); see ui/README.md and ui/CONTRACT.md section 2.
"""
from __future__ import annotations

import argparse
import contextlib
import mimetypes
import os
import signal
import socket
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse
from starlette.staticfiles import StaticFiles
from uvicorn.server import HANDLED_SIGNALS

UI_VERSION = "0.5.0"
STATIC_DIR = Path(__file__).resolve().parent / "static"
START_HINT = "mise run start"
UPSTREAM_TIMEOUT = httpx.Timeout(connect=3.0, read=900.0, write=120.0, pool=10.0)
HEALTH_TIMEOUT = 2.5
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
              "content-length", "content-encoding",
              "date", "server"}  # uvicorn sets its own date/server; copying them would duplicate
LIMITS = {"maxImages": 8, "maxImageBytes": 5 * 1024 * 1024, "maxQuestions": 256,
          "stepsMax": 8, "samplesMax": 32, "thinkMax": 4096,
          "chatMaxTokensDefault": 1024, "chatMaxTokensCap": 8192,
          "choiceMaxOptions": 255, "scoreMaxLevels": 10}
HINTS = {"start": START_HINT, "logs": "mise run logs",
         "status": "mise run status", "stop": "mise run stop"}

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("image/svg+xml", ".svg")


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def _upstream_error(status: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse({"detail": {"error_type": error_type, "message": message, "hint": START_HINT}},
                        status_code=status, headers={"cache-control": "no-store"})


def _detail_message(resp: httpx.Response) -> str:
    """The human message from an OpenJev error body (either detail shape), else the raw text."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500]
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    if detail is not None:
        return str(detail)
    return resp.text[:500]


class _NoStoreStatic:
    """Pure-ASGI wrapper (streaming-safe, unlike BaseHTTPMiddleware) adding cache-control: no-store."""

    def __init__(self, app, directory: Path):
        self.app, self.directory = app, Path(directory)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if not self.directory.is_dir():
            msg = (f"OpenJev UI: static directory {self.directory} does not exist yet. "
                   "The proxy (/v1/*, /ui/api/*) still works.").encode()
            await send({"type": "http.response.start", "status": 404,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                    (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": msg})
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


class _UpstreamStream(StreamingResponse):
    """A StreamingResponse that always closes the upstream response, even when the browser
    disconnects mid-stream (ASGI 2.4 raises ClientDisconnect and skips the background task)."""

    def __init__(self, *args, on_close, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._on_close()


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _host_only(value: str) -> str:
    """'127.0.0.1:8090' / '[::1]:8090' / 'http://x:1' -> bare lowercase host."""
    v = value.strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0]
    if v.startswith("["):
        return v[1:].split("]", 1)[0]
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def _forbidden(request: Request, allowed_hosts: set[str] | None) -> JSONResponse | None:
    """Refuse cross-site callers (any web page can POST to 127.0.0.1, and the proxy adds the API
    key) and, when bound to loopback, DNS-rebinding Host headers."""
    host = request.headers.get("host", "")
    reason = None
    if allowed_hosts is not None and _host_only(host) not in allowed_hosts:
        reason = f"Host {host!r} is not allowed (bind with UI_HOST to serve other names)"
    elif request.headers.get("sec-fetch-site") == "cross-site":
        reason = "cross-site requests are not allowed"
    else:
        origin = request.headers.get("origin")
        if origin and (origin == "null" or origin.split("://", 1)[-1].lower() != host.lower()):
            reason = f"Origin {origin!r} is not allowed"
    if reason is None:
        return None
    return JSONResponse({"detail": {"error_type": "forbidden_origin", "message": reason}},
                        status_code=403, headers={"cache-control": "no-store"})


def create_app(openjev_url: str, api_key: str = "", origin_secret: str = "",
               static_dir: Path = STATIC_DIR,
               transport: httpx.AsyncBaseTransport | None = None,
               allowed_hosts: set[str] | None = None) -> FastAPI:
    openjev_url = openjev_url.rstrip("/")
    started_at = time.time()
    counters = {"requests": 0}

    def auth_headers(client_auth: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if client_auth:
            headers["authorization"] = client_auth
        elif api_key:
            headers["authorization"] = f"Bearer {api_key}"
        if origin_secret:
            headers["x-origin-secret"] = origin_secret
        return headers

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = httpx.AsyncClient(transport=transport, timeout=UPSTREAM_TIMEOUT)
        try:
            yield
        finally:
            await app.state.client.aclose()

    app = FastAPI(title="OpenJev UI proxy", version=UI_VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    def client(request: Request) -> httpx.AsyncClient:
        c = getattr(request.app.state, "client", None)
        if c is None:  # app used without lifespan (e.g. a bare ASGITransport in a test)
            c = request.app.state.client = httpx.AsyncClient(transport=transport, timeout=UPSTREAM_TIMEOUT)
        return c

    @app.get("/ui/api/config")
    async def config():
        return JSONResponse({
            "uiVersion": UI_VERSION, "openjevUrl": openjev_url, "proxyBase": "",
            "authConfigured": bool(api_key),
            "defaults": {"model": "openjev-latest", "chatModel": "diffusiongemma-26b"},
            "limits": LIMITS, "hints": HINTS,
        }, headers={"cache-control": "no-store"})

    @app.get("/ui/api/health")
    async def health(request: Request):
        if (denied := _forbidden(request, allowed_hosts)) is not None:
            return denied
        up = {"url": openjev_url, "reachable": False, "status": None, "latencyMs": None,
              "requestId": None, "serverTiming": None, "error": None, "errorType": None}
        models: list = []
        t0 = time.perf_counter()
        try:
            resp = await client(request).get(f"{openjev_url}/v1/models", headers=auth_headers(),
                                             timeout=HEALTH_TIMEOUT)
            up.update(reachable=True, status=resp.status_code, latencyMs=_ms(t0),
                      requestId=resp.headers.get("x-request-id"),
                      serverTiming=resp.headers.get("server-timing"))
            if resp.status_code == 200:
                try:
                    models = resp.json().get("models", []) or []
                except (ValueError, AttributeError):
                    up.update(error="upstream returned a non-JSON /v1/models body", errorType="upstream_error")
            elif resp.status_code in (401, 403):
                up.update(error=_detail_message(resp), errorType="auth")
            else:
                up.update(error=_detail_message(resp), errorType="upstream_status")
        except httpx.TimeoutException as exc:
            kind = "upstream_unreachable" if isinstance(exc, httpx.ConnectTimeout) else "upstream_timeout"
            up.update(latencyMs=_ms(t0), error=f"{type(exc).__name__}: {exc}", errorType=kind)
        except httpx.ConnectError as exc:
            up.update(latencyMs=_ms(t0), error=f"{type(exc).__name__}: {exc}", errorType="upstream_unreachable")
        except httpx.HTTPError as exc:
            up.update(latencyMs=_ms(t0), error=f"{type(exc).__name__}: {exc}", errorType="upstream_error")
        ok = up["reachable"] and up["status"] == 200 and up["errorType"] is None
        return JSONResponse({
            "ok": ok, "checkedAt": int(time.time() * 1000), "upstream": up, "models": models,
            "authConfigured": bool(api_key),
            "proxy": {"uptimeS": round(time.time() - started_at, 1), "requests": counters["requests"]},
        }, headers={"cache-control": "no-store"})

    @app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy(path: str, request: Request):
        if (denied := _forbidden(request, allowed_hosts)) is not None:
            return denied
        if ".." in path.split("/"):  # httpx would normalise it out of the /v1 namespace
            return JSONResponse({"detail": {"error_type": "bad_path", "message": "'..' is not allowed in the path"}},
                                status_code=400, headers={"cache-control": "no-store"})
        counters["requests"] += 1
        t0 = time.perf_counter()
        url = f"{openjev_url}/v1/{path}"
        if request.url.query:
            url += "?" + request.url.query
        headers = {"accept-encoding": "identity"}
        for name in ("content-type", "accept"):
            if name in request.headers:
                headers[name] = request.headers[name]
        headers.update(auth_headers(request.headers.get("authorization")))
        body = await request.body()
        label = f"{request.method} /v1/{path}"
        c = client(request)
        upstream_req = c.build_request(request.method, url, headers=headers, content=body)
        t_send = time.perf_counter()
        try:
            resp = await c.send(upstream_req, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            _log(label, 502, _ms(t0), None, None, "upstream_unreachable")
            return _upstream_error(502, "upstream_unreachable",
                                   f"OpenJev is not reachable at {openjev_url}: {exc}")
        except httpx.TimeoutException as exc:
            _log(label, 504, _ms(t0), None, None, "upstream_timeout")
            return _upstream_error(504, "upstream_timeout", f"OpenJev timed out: {type(exc).__name__}: {exc}")
        except httpx.HTTPError as exc:
            _log(label, 502, _ms(t0), None, None, "upstream_error")
            return _upstream_error(502, "upstream_error", f"Proxy error talking to OpenJev: {type(exc).__name__}: {exc}")
        up_ms = _ms(t_send)

        out_headers: dict[str, str] = {}
        for k, v in resp.headers.multi_items():
            lk = k.lower()
            if lk in HOP_BY_HOP or lk.startswith("proxy-") or lk == "server-timing":
                continue
            out_headers[lk] = f"{out_headers[lk]}, {v}" if lk in out_headers and lk != "set-cookie" else v
        timing = ", ".join(resp.headers.get_list("server-timing"))
        out_headers["server-timing"] = f"{timing}, upstream;dur={up_ms:.1f}" if timing else f"upstream;dur={up_ms:.1f}"
        out_headers["x-ojui-upstream-ms"] = f"{up_ms:.1f}"
        out_headers["cache-control"] = "no-store"
        out_headers["x-accel-buffering"] = "no"
        rid = resp.headers.get("x-request-id")
        closed = {"done": False, "note": None}

        async def body_iter():
            if resp.is_stream_consumed:  # body already buffered (in-memory transports)
                yield resp.content
                return
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            except httpx.HTTPError as exc:  # upstream died mid-body; status is already sent
                closed["note"] = f"stream_error={type(exc).__name__}"

        async def close():
            if closed["done"]:
                return
            closed["done"] = True
            await resp.aclose()
            _log(label, resp.status_code, _ms(t0), up_ms, rid, closed["note"])

        return _UpstreamStream(body_iter(), status_code=resp.status_code, headers=out_headers,
                               background=BackgroundTask(close), on_close=close)

    app.mount("/", _NoStoreStatic(StaticFiles(directory=static_dir, html=True, check_dir=False), static_dir),
              name="static")
    return app


def _log(label: str, status: int, total_ms: float, up_ms: float | None, rid: str | None, note: str | None) -> None:
    parts = [label, str(status), f"{total_ms:.0f}ms"]
    if up_ms is not None:
        parts.append(f"up={up_ms:.0f}ms")
    if rid:
        parts.append(rid[:12] + "…" if len(rid) > 12 else rid)
    if note:
        parts.append(note)
    print(" ".join(parts), flush=True)


def _probe_upstream(openjev_url: str, api_key: str, origin_secret: str) -> tuple[bool, str]:
    headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
    if origin_secret:
        headers["x-origin-secret"] = origin_secret
    t0 = time.perf_counter()
    try:
        r = httpx.get(f"{openjev_url}/v1/models", headers=headers, timeout=HEALTH_TIMEOUT)
    except httpx.HTTPError as exc:
        return False, f"NOT reachable ({type(exc).__name__}) — start it with: {START_HINT}"
    ms = _ms(t0)
    if r.status_code == 200:
        try:
            n = len(r.json().get("models", []))
        except (ValueError, AttributeError):
            n = 0
        return True, f"reachable ({n} model{'s' if n != 1 else ''}, {ms:.0f} ms)"
    if r.status_code in (401, 403):
        return False, f"reachable but auth failed ({r.status_code}: {_detail_message(r)}) — set OPENJEV_API_KEY"
    return False, f"reachable but /v1/models returned {r.status_code}"


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _banner(ui_url: str, openjev_url: str, api_key: str, origin_secret: str, ok: bool, status: str) -> str:
    tty = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def c(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if tty else s

    auth = "Bearer key configured (OPENJEV_API_KEY)" if api_key else "none (set OPENJEV_API_KEY if the server needs one)"
    if origin_secret:
        auth += " + X-Origin-Secret"
    rows = [
        ("UI", c("1;36", ui_url)),
        ("OpenJev", openjev_url),
        ("auth", auth),
        ("upstream", c("32", status) if ok else c("31", status)),
        ("static", str(STATIC_DIR)),
    ]
    lines = ["", c("1", f"  OpenJev Playground  v{UI_VERSION}"), ""]
    lines += [f"  {c('2', k.ljust(9))} {v}" for k, v in rows]
    lines += ["", c("2", "  Ctrl+C to stop · proxied requests are logged below"), ""]
    return "\n".join(lines)


class _Server(uvicorn.Server):
    """uvicorn.Server that shuts down quietly on Ctrl+C / SIGTERM.

    A terminal Ctrl+C reaches this process twice (process group + mise forwarding). Stock uvicorn
    takes the second one as "force quit", which skips the lifespan shutdown, then re-raises the
    captured signals, which raises KeyboardInterrupt inside the loop and cancels the parked
    lifespan task (logged as an ERROR traceback). Here a repeat within 1 s is the same press,
    signals are never re-raised (so SIGTERM exits 0, not 143), and the lifespan always shuts down.
    """
    SAME_PRESS_S = 1.0

    def __init__(self, config: uvicorn.Config):
        super().__init__(config)
        self._first_signal_at = 0.0

    def handle_exit(self, sig, frame) -> None:
        now = time.monotonic()
        if not self.should_exit:
            self._first_signal_at = now
            self.should_exit = True
        elif sig == signal.SIGINT and now - self._first_signal_at >= self.SAME_PRESS_S:
            self.force_exit = True  # a real second Ctrl+C: stop waiting for open connections

    @contextlib.contextmanager
    def capture_signals(self):
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        original = {sig: signal.signal(sig, self.handle_exit) for sig in HANDLED_SIGNALS}
        try:
            yield
        finally:
            for sig, handler in original.items():
                signal.signal(sig, handler)
        # unlike uvicorn, no signal.raise_signal() here: main() returns normally

    async def _wait_tasks_to_complete(self) -> None:
        # log_level is "warning", so uvicorn's own "waiting for connections" notice is invisible;
        # say why shutdown is pending (not from handle_exit: stderr writes in a handler can re-enter)
        n = len(self.server_state.connections)
        if n and not self.force_exit:
            print(f"ojui: waiting for {n} open request(s) to finish; press Ctrl+C again to force quit",
                  file=sys.stderr, flush=True)
        await super()._wait_tasks_to_complete()

    async def shutdown(self, sockets=None) -> None:
        await super().shutdown(sockets)
        done = getattr(getattr(self, "lifespan", None), "shutdown_event", None)
        if self.force_exit and done is not None and not done.is_set():
            await self.lifespan.shutdown()  # uvicorn skips it on force_exit; run it so httpx closes


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="OpenJev UI: static SPA + proxy to OpenJev")
    ap.add_argument("--host", default=os.environ.get("UI_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("UI_PORT", "8090")))
    ap.add_argument("--open", action="store_true", help="open a browser tab after startup")
    args = ap.parse_args(argv)

    openjev_url = os.environ.get("OPENJEV_URL", "http://127.0.0.1:8080").rstrip("/")
    api_key = os.environ.get("OPENJEV_API_KEY", "")
    origin_secret = os.environ.get("OPENJEV_ORIGIN_SECRET", "")
    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    ui_url = f"http://{shown_host}:{args.port}"

    if not _port_free(args.host, args.port):
        sys.exit(f"Port {args.port} on {args.host} is already in use. Is the OpenJev UI already running (check: mise run status)? "
                 f"Or try UI_PORT={args.port + 1} mise run start")
    if not STATIC_DIR.is_dir():
        print(f"warning: static dir {STATIC_DIR} does not exist; only the proxy will work", file=sys.stderr)

    ok, status = _probe_upstream(openjev_url, api_key, origin_secret)
    print(_banner(ui_url, openjev_url, api_key, origin_secret, ok, status), flush=True)

    # Loopback bind: only loopback Host headers (blocks DNS rebinding). Other binds: the user opted in.
    allowed = LOOPBACK_HOSTS if args.host.lower() in LOOPBACK_HOSTS else None
    app = create_app(openjev_url, api_key=api_key, origin_secret=origin_secret, allowed_hosts=allowed)
    srv = _Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning"))
    if args.open:
        def _open_when_up() -> None:  # fire and forget: wait until the socket is bound, then open the tab
            for _ in range(100):
                if srv.started:
                    webbrowser.open(ui_url)
                    return
                time.sleep(0.1)
        threading.Thread(target=_open_when_up, daemon=True).start()
    try:
        srv.run()
    except KeyboardInterrupt:  # safety net: a signal landing after the handlers were restored
        pass
    if not srv.started:
        sys.exit(3)  # uvicorn's STARTUP_FAILURE (e.g. the bind failed); it already logged why
    print("\nojui: stopped", file=sys.stderr)


if __name__ == "__main__":
    main()
