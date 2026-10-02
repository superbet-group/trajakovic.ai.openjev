"""FaultUpstream (H2): a proxy in front of OpenJev that injects upstream faults for `/v1/systemone` bodies with `#FAULT:<kind>` in `state`."""
from __future__ import annotations

import json
import socket
import threading
import time

KINDS = ("401", "503", "529", "500", "sleep")
SLEEP_S = 30


def fault_kind(body: bytes) -> str | None:
    try:
        st = json.loads(body).get("state")
    except (ValueError, AttributeError):
        return None
    st = st if isinstance(st, str) else json.dumps(st) if st is not None else ""
    for k in KINDS:
        if f"#FAULT:{k}" in st:
            return k
    return None


class FaultUpstream:
    def __init__(self, upstream: str, sleep_s: float = SLEEP_S):
        self.upstream, self.sleep_s = upstream.rstrip("/"), sleep_s
        self.url = ""
        self._server = None
        self._thread = None

    def _app(self):
        import asyncio
        import httpx
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route

        client = httpx.AsyncClient(timeout=httpx.Timeout(None), follow_redirects=False)
        drop_req = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}
        drop_resp = {"content-length", "transfer-encoding", "connection", "content-encoding"}

        async def proxy(request):
            body = await request.body()
            if request.method == "POST" and request.url.path == "/v1/systemone":
                kind = fault_kind(body)
                if kind == "401":
                    return JSONResponse({"detail": {"error_type": "authentication_error", "message": "invalid API key"}}, 401)
                if kind == "503":
                    return JSONResponse({"detail": {"error_type": "api_error", "message": "inference backend unavailable"}}, 503,
                                        headers={"retry-after": "1"})
                if kind == "529":
                    return JSONResponse({"detail": {"error_type": "overloaded_error", "message": "OpenJev at capacity"}}, 529)
                if kind == "500":
                    return Response("Internal Server Error", 500, media_type="text/plain")
                if kind == "sleep":
                    await asyncio.sleep(self.sleep_s)
                    return Response("too late", 504, media_type="text/plain")
            headers = {k: v for k, v in request.headers.items() if k.lower() not in drop_req}
            url = self.upstream + request.url.path + (f"?{request.url.query}" if request.url.query else "")
            try:
                up = await client.request(request.method, url, content=body, headers=headers)
            except httpx.HTTPError as exc:
                return Response(f"fault proxy: upstream error: {exc}", 502, media_type="text/plain")
            return Response(up.content, up.status_code, {k: v for k, v in up.headers.items() if k.lower() not in drop_resp})

        return Starlette(routes=[Route("/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])])

    def start(self) -> "FaultUpstream":
        import uvicorn
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self._server = uvicorn.Server(uvicorn.Config(self._app(), host="127.0.0.1", port=port, log_level="error", lifespan="off"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        for _ in range(100):
            if self._server.started:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("FaultUpstream did not start")
        self.url = f"http://127.0.0.1:{port}"
        return self

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = self._server.force_exit = True
            self._thread.join(5)
            self._server = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()
