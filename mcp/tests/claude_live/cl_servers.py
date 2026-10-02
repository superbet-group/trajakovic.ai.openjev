"""Own openjev-mcp instances (profiles of ARCHITECTURE 1.2) and the per-run WireTap reverse proxy. Terminates only Popen handles it started."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cl_env


@dataclass
class McpInstance:
    profile: str
    port: int
    url: str                       # http://127.0.0.1:<port>/mcp
    root_url: str                  # http://127.0.0.1:<port>
    log: Path                      # OPENJEV_MCP_LOG (read audit JSONL)
    audit_dir: Path
    stdout_log: Path
    proc: subprocess.Popen

    def audit_mark(self) -> int:
        return self.log.stat().st_size if self.log.exists() else 0

    def audit_since(self, mark: int) -> list[dict]:
        if not self.log.exists():
            return []
        with open(self.log, "rb") as fh:
            fh.seek(mark)
            data = fh.read().decode("utf-8", "replace")
        out = []
        for line in data.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def tail(self, n: int = 200) -> str:
        try:
            return "\n".join(self.stdout_log.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
        except OSError:
            return ""

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/health", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def _default(inst: "Instances") -> dict:
    return {}


def _ext(inst: "Instances") -> dict:
    src, dst = cl_env.FIXTURES / "recipe_extra", inst.work / "recipe_extra"
    if not src.is_dir() or not any(src.glob("*.json")):
        raise RuntimeError(f"profile ext needs {src}/*.json (owned by group G10): not found")
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    return {"OPENJEV_MCP_RECIPES": str(dst), "OPENJEV_MCP_ROUTING": "off"}


def _unreachable(inst: "Instances") -> dict:
    dead = cl_env.free_port(8300, 8399, skip=inst.dead_ports)   # outside the instance range, nobody listens
    inst.dead_ports.add(dead)
    return {"OPENJEV_BASE_URL": f"http://127.0.0.1:{dead}", "OPENJEV_MCP_RETRIES": "0"}


def _notopenjev(inst: "Instances") -> dict:
    return {"OPENJEV_BASE_URL": inst.get("default").root_url}


def _fault(inst: "Instances") -> dict:
    import cl_fault   # H2, lazy (D6)
    f = cl_fault.FaultUpstream(inst.base_url)
    f.start()
    inst.closers.append(f.stop)
    return {"OPENJEV_BASE_URL": f.url, "OPENJEV_MCP_RETRIES": "1", "OPENJEV_MCP_TIMEOUT_MS": "2000"}


PROFILES: dict[str, Callable[["Instances"], dict]] = {
    "default": _default, "core": lambda i: {"OPENJEV_MCP_TOOLSETS": "core"}, "tasks": lambda i: {"OPENJEV_MCP_TASKS": "on"},
    "ext": _ext, "unreachable": _unreachable, "notopenjev": _notopenjev, "fault": _fault,
}


class Instances:
    """Session object behind the `mcp` fixture: lazy per-profile instances rooted at `work`."""

    def __init__(self, work: Path, base_url: str | None = None):
        self.work = Path(work)
        self.base_url = (base_url or cl_env.BASE_URL).rstrip("/")
        self.items: dict[str, McpInstance] = {}
        self.closers: list[Callable[[], None]] = []
        self.dead_ports: set[int] = set()

    def get(self, profile: str = "default") -> McpInstance:
        if profile in self.items and self.items[profile].proc.poll() is None:
            return self.items[profile]
        if profile not in PROFILES:
            raise KeyError(f"unknown profile {profile!r}; known: {sorted(PROFILES)}")
        self.items[profile] = self._spawn(profile, PROFILES[profile](self))
        return self.items[profile]

    def ports(self) -> list[int]:
        return [i.port for i in self.items.values()]

    def _env(self, profile: str, extra: dict) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith("OPENJEV_MCP_")}
        env.update({"OPENJEV_BASE_URL": self.base_url, "OPENJEV_MCP_ROOTS": str(self.work), "OPENJEV_MCP_AUDIT_DIR": str(self.work / "audits"),
                    "OPENJEV_MCP_LOG": str(self.work / f"mcp-{profile}.audit.jsonl")})
        env.update(extra)
        return env

    def _spawn(self, profile: str, extra: dict) -> McpInstance:
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / "audits").mkdir(exist_ok=True)
        env, skip = self._env(profile, extra), set(self.dead_ports)
        pinned = os.environ.get("OJ_LIVE_MCP_PORT") if profile == "default" else None
        for attempt in range(3):
            port = int(pinned) if pinned and attempt == 0 else cl_env.free_port(skip=skip)
            out = self.work / f"mcp-{profile}.stdout.log"
            fh = open(out, "w")
            proc = subprocess.Popen([cl_env.PY, "-m", "openjev_mcp", "--transport", "http", "--port", str(port)], cwd=str(self.work), env=env,
                                    stdout=fh, stderr=subprocess.STDOUT)
            root = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and proc.poll() is None and not _healthy(root):
                time.sleep(0.2)
            fh.close()
            if proc.poll() is None and _healthy(root):
                return McpInstance(profile, port, root + "/mcp", root, Path(env["OPENJEV_MCP_LOG"]), self.work / "audits", out, proc)
            if proc.poll() is None:
                proc.kill()
                proc.wait(5)
            if proc.returncode == 3:   # port taken between the bind test and the spawn: next port
                skip.add(port)
                continue
            raise RuntimeError(f"harness: openjev-mcp ({profile}) did not come up on {port} (rc={proc.returncode}):\n"
                               + "\n".join(out.read_text(errors="replace").splitlines()[-30:]))
        raise RuntimeError(f"harness: openjev-mcp ({profile}) kept hitting taken ports: {sorted(skip)}")

    def stop_all(self) -> None:
        for i in self.items.values():
            i.stop()
        self.items.clear()
        for c in reversed(self.closers):
            try:
                c()
            except Exception:
                pass
        self.closers.clear()


# ---- WireTap -------------------------------------------------------------------------------------------------------

def parse_sse(text: str) -> list[dict]:
    """Every `data:` line (one JSON-RPC message each; the server writes single-line data fields)."""
    out = []
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                out.append(json.loads(line[5:].strip()))
            except ValueError:
                pass
    return out


def parse_resp(body: str) -> list[dict]:
    """A response body as a list of JSON-RPC messages: SSE `data:` lines or one JSON body."""
    if not body.strip():
        return []
    if body.lstrip().startswith(("event:", "data:", ":", "id:")):
        return parse_sse(body)
    try:
        v = json.loads(body)
    except ValueError:
        return []
    return v if isinstance(v, list) else [v]


class WireTap:
    """Per-run reverse proxy in front of an instance: records method, mcp-* headers, request JSON, status and response messages."""

    def __init__(self, upstream: str, out: Path):
        self.upstream, self.out = upstream.rstrip("/"), Path(out)
        self.url = ""
        self._lock = threading.Lock()
        self._server = None
        self._thread = None

    def record(self, entry: dict) -> None:
        with self._lock, open(self.out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _app(self):
        import httpx
        from starlette.applications import Starlette
        from starlette.responses import Response, StreamingResponse
        from starlette.routing import Route

        client = httpx.AsyncClient(timeout=httpx.Timeout(None), follow_redirects=False)
        drop_req = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}
        drop_resp = {"content-length", "transfer-encoding", "connection", "content-encoding"}

        async def proxy(request):
            body = await request.body()
            headers = {k: v for k, v in request.headers.items() if k.lower() not in drop_req}
            headers["accept-encoding"] = "identity"
            try:
                req = json.loads(body) if body else None
            except ValueError:
                req = None
            first = req[0] if isinstance(req, list) and req else req
            entry = {"t": time.time(), "http": request.method, "path": request.url.path,
                     "method": first.get("method") if isinstance(first, dict) else None,
                     "headers": {k: v for k, v in request.headers.items() if k.lower().startswith("mcp-")}, "req": req}
            url = self.upstream + request.url.path + (f"?{request.url.query}" if request.url.query else "")
            try:
                up = await client.send(client.build_request(request.method, url, content=body, headers=headers), stream=True)
            except httpx.HTTPError as exc:
                entry.update(status=502, resp=[], error=str(exc))
                self.record(entry)
                return Response(f"tap: upstream error: {exc}", status_code=502)
            chunks: list[bytes] = []

            async def gen():
                try:
                    async for c in up.aiter_raw():
                        chunks.append(c)
                        yield c
                finally:
                    await up.aclose()
                    entry.update(status=up.status_code, resp=parse_resp(b"".join(chunks).decode("utf-8", "replace")))
                    self.record(entry)

            return StreamingResponse(gen(), status_code=up.status_code, headers={k: v for k, v in up.headers.items() if k.lower() not in drop_resp})

        return Starlette(routes=[Route("/{path:path}", proxy, methods=["GET", "POST", "DELETE", "OPTIONS", "PUT"])])

    def __enter__(self) -> "WireTap":
        import uvicorn
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.out.touch()
        self._server = uvicorn.Server(uvicorn.Config(self._app(), host="127.0.0.1", port=0, log_level="error", lifespan="off"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        for _ in range(200):
            if self._server.started and self._server.servers:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("harness: WireTap did not start")
        port = self._server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/mcp"
        return self

    def __exit__(self, *exc) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(10)
