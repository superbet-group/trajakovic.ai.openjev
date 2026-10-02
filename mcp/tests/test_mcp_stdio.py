"""stdio hygiene (spec 2.0.1 rule 10, TASKS 1.31): stdout is JSON-RPC only, the server sends no requests."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from contextlib import contextmanager

import pytest

import openjev_mcp

K = "io.modelcontextprotocol/"
ENVELOPE = {K + "protocolVersion": "2026-07-28", K + "clientCapabilities": {}}
INIT = {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}
WAIT_S = 20


class Proc:
    def __init__(self, popen: subprocess.Popen):
        self.popen = popen
        self.lines: queue.Queue[str] = queue.Queue()
        self.seen: list[dict] = []
        self.raw: list[str] = []
        self.err: list[str] = []
        threading.Thread(target=self._pump, args=(popen.stdout, self.lines.put), daemon=True).start()
        threading.Thread(target=self._pump, args=(popen.stderr, self.err.append), daemon=True).start()

    @staticmethod
    def _pump(stream, sink) -> None:
        for line in stream:
            sink(line)

    def send(self, message: dict) -> None:
        self.popen.stdin.write(json.dumps(message) + "\n")
        self.popen.stdin.flush()

    def reply(self, id: int) -> dict:
        while True:
            line = self.lines.get(timeout=WAIT_S)
            self.raw.append(line)
            msg = json.loads(line)
            self.seen.append(msg)
            if msg.get("id") == id and "method" not in msg:
                return msg

    def call(self, id: int, method: str, params: dict | None = None) -> dict:
        self.send({"jsonrpc": "2.0", "id": id, "method": method, **({"params": params} if params is not None else {})})
        return self.reply(id)

    def finish(self) -> int:
        self.popen.stdin.close()
        code = self.popen.wait(timeout=WAIT_S)
        while not self.lines.empty():
            line = self.lines.get()
            self.raw.append(line)
            self.seen.append(json.loads(line))
        return code


@contextmanager
def spawn():
    env = {**os.environ, "OPENJEV_BASE_URL": "http://127.0.0.1:9", "OPENJEV_MCP_RETRIES": "0"}
    for key in ("OPENJEV_MCP_TOKEN", "OPENJEV_MCP_TRANSPORT", "OPENJEV_MCP_LOG"):
        env.pop(key, None)
    popen = subprocess.Popen([sys.executable, "-m", "openjev_mcp", "--transport", "stdio"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    proc = Proc(popen)
    try:
        yield proc
    finally:
        if popen.poll() is None:
            popen.kill()
            popen.wait()
        for stream in (popen.stdin, popen.stdout, popen.stderr):
            stream.close()


def assert_clean(proc: Proc, code: int) -> None:
    assert code == 0, "".join(proc.err)
    assert proc.raw, "no output"
    for line, msg in zip(proc.raw, proc.seen):
        assert line.endswith("\n") and "\n" not in line[:-1]
        assert msg["jsonrpc"] == "2.0", line
        assert msg.get("method") != "notifications/message", line
        if "method" in msg:
            assert "id" not in msg, f"server sent a request: {line}"
            assert msg["method"].startswith("notifications/"), line
        else:
            assert ("result" in msg) != ("error" in msg), line


def test_legacy_session_keeps_stdout_clean():
    with spawn() as p:
        init = p.call(1, "initialize", INIT)["result"]
        assert init["protocolVersion"] == "2025-11-25" and init["serverInfo"]["name"] == "openjev-mcp"
        p.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = p.call(2, "tools/list")["result"]["tools"]
        assert [t["name"] for t in tools] == list(openjev_mcp.TOOL_NAMES)
        status = p.call(3, "tools/call", {"name": "status", "arguments": {}})["result"]
        assert status["isError"] is True
        assert json.loads(status["content"][0]["text"])["error"]["code"] == "OJ_UNREACHABLE"
        assert_clean(p, p.finish())


def test_modern_session_keeps_stdout_clean():
    with spawn() as p:
        found = p.call(1, "server/discover", {"_meta": ENVELOPE})["result"]
        assert found["supportedVersions"] == ["2026-07-28", "2025-11-25", "2025-06-18"]
        assert found["resultType"] == "complete"
        tools = p.call(2, "tools/list", {"_meta": ENVELOPE})["result"]
        assert [t["name"] for t in tools["tools"]][0] == "ask" and tools["ttlMs"] == 3600000
        status = p.call(3, "tools/call", {"name": "status", "arguments": {}, "_meta": ENVELOPE})["result"]
        assert status["isError"] is True
        assert_clean(p, p.finish())


def test_protocol_faults_stay_on_stdout_as_json_rpc():
    with spawn() as p:
        p.call(1, "server/discover", {"_meta": ENVELOPE})
        bad = p.call(2, "tools/call", {"name": "nope", "arguments": {}, "_meta": ENVELOPE})
        assert bad["error"]["code"] == -32602
        assert_clean(p, p.finish())


def test_process_exits_cleanly_when_stdin_closes_before_any_request():
    with spawn() as p:
        code = p.finish()
    assert code == 0, "".join(p.err)
    assert p.raw == []


def test_stdout_never_carries_diagnostics():
    with spawn() as p:
        p.call(1, "initialize", INIT)
        p.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        p.call(2, "tools/call", {"name": "ask", "arguments": {"state": "s"}})
        assert_clean(p, p.finish())
    for line in p.raw:
        json.loads(line)
