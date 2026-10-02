"""Live smoke: one `claude -p` run (cheap tier) through the own MCP instance to the real OpenJev. Not a catalogue case (D12).

    OPENJEV_CLAUDE_LIVE=1 .venv/bin/python -m pytest -q mcp/tests/claude_live/test_cl_smoke.py"""
import socket
import urllib.request

import cl_assert as A
import cl_env
import cl_results
from cl_cases import Case, run_case


def _expect(t, ctx):
    assert t.init["apiKeySource"] == "none"
    A.precondition(t, ["status"])
    (call,) = A.tool_called(t, "status", times=1)
    assert A.result_ok(call)["healthy"] is True
    assert call.wire is not None and not call.wire.get("isError"), "tools/call result not joined via claudecode/toolUseId"
    (e,) = t.wire.requests("tools/call", "status")
    assert e["headers"]["mcp-name"] == "status" and e["params"]["_meta"]["claudecode/toolUseId"] == call.use.id
    A.wire_called(t, "server/discover")


CASE = Case("SMOKE", "status", "smoke", "claude -> own MCP -> OpenJev", "spec 1 status; ARCHITECTURE 1.3",
            "Call the tool mcp__openjev__status exactly once with no arguments, then reply with the single word DONE.", (_expect,), ("status",),
            max_turns=3, timeout_s=180)


def _listening(port):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def test_smoke_status(case_ctx, instances):
    t = run_case(CASE, case_ctx)
    for f in ("argv.json", "claude.json", "wire.jsonl"):
        assert (t.run_dir / f).stat().st_size > 0, f
    (row,) = [r for r in cl_results.load() if r["id"] == "SMOKE"]
    assert row["pass"] and row["cost_usd"] > 0 and row["tools"] == ["status"] and row["tier"] == "cheap"
    ports = instances.ports()
    assert ports and all(8200 <= p <= 8299 for p in ports)
    instances.stop_all()
    assert not [p for p in ports if _listening(p)], "an own MCP instance still listens after stop_all"
    try:   # the user's MCP is never touched; just report that it is still up when it was
        urllib.request.urlopen("http://127.0.0.1:8100/health", timeout=2).read()
    except Exception:
        pass
