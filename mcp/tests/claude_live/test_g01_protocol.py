"""g01 Connection, discovery, protocol (T001-T008). OPENJEV_CLAUDE_LIVE=1 .venv/bin/python -m pytest mcp/tests/claude_live/test_g01_protocol.py -q"""
import json

import pytest

import cl_assert as A
from cl_cases import case_from_data, run_case
from cl_env import CASES_DIR
from openjev_mcp import CORE_TOOL_NAMES, TOOL_NAMES

DATA = CASES_DIR / "g01_protocol.json"
P = "mcp__openjev__"
PROMPTS = {"start_batch", "review_batch", "author_question", "audit_question", "explain_answer"}


def _compact(v):
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def _t001(t, ctx):
    A.precondition(t, TOOL_NAMES)
    ours = [x for x in t.init["tools"] if x.startswith(P)]
    assert len(ours) == 14 and set(ours) == {P + n for n in TOOL_NAMES}, f"openjev tools {ours}"
    cmds = {c for c in t.init["slash_commands"] if c.startswith(P)}
    assert cmds == {P + p for p in PROMPTS}, f"prompt slash commands {sorted(cmds)}"
    user = [p for p in t.init["plugins"] if not str(p.get("source", "")).endswith("@builtin")]
    assert not user, f"user plugins leaked into the isolated session: {user}"
    A.no_tool_called(t)


def _t002(t, ctx):
    ents = t.wire.entries
    assert ents and ents[0]["method"] == "server/discover", f"first request {ents[0]['method'] if ents else None}"
    reqs = [e for e in ents if e["req"] is not None]
    A.wire_header(t, "mcp-protocol-version", A.eq("2026-07-28"))
    bad = [(e["method"], e["headers"].get("mcp-method")) for e in reqs if e["headers"].get("mcp-method") != e["method"]]
    assert not bad, f"Mcp-Method != JSON-RPC method: {bad}"
    (e,) = A.wire_called(t, "tools/call", "status")
    assert e["headers"].get("mcp-name") == "status"
    A.result_ok(A.tool_called(t, "status", times=1)[0])


def _t003(t, ctx):
    A.tool_called(t, "status", times=1)
    (res,) = t.wire.response("tools/list")[:1]
    tools = res["tools"]
    assert [x["name"] for x in tools] == list(TOOL_NAMES), [x["name"] for x in tools]
    assert res.get("ttlMs") == 3600000 and res.get("cacheScope") == "public", {k: res.get(k) for k in ("ttlMs", "cacheScope")}
    by = {x["name"]: x.get("annotations") or {} for x in tools}
    assert by["batch"].get("readOnlyHint") is False, by["batch"]
    assert by["yes_no"].get("idempotentHint") is True, by["yes_no"]
    assert by["ask_image"].get("openWorldHint") is False, by["ask_image"]


def _t004(t, ctx):
    call = A.tool_called(t, "status", {"probe": A.eq(True)}, times=1)[0]
    out = A.result_ok(call)
    assert out["healthy"] is True
    assert isinstance(out["decide_models"], list) and out["decide_models"], out["decide_models"]
    assert isinstance(out["latency_probe_ms"], (int, float)) and out["latency_probe_ms"] > 0, out["latency_probe_ms"]
    if out["limit_source"] == "default":
        assert any("/v1/limits" in w for w in out["warnings"]), out["warnings"]


def _t005(t, ctx):
    ext = (t.wire.response("server/discover")[0].get("capabilities") or {}).get("extensions") or {}
    assert "io.modelcontextprotocol/tasks" in ext, f"discover extensions {ext}"
    call = A.tool_called(t, "batch", times=1)[0]
    (e,) = A.wire_called(t, "tools/call", "batch")
    caps = e["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"]
    assert "extensions" not in caps, f"client declared extensions {caps}"
    res = t.wire.result(call.use.id)
    assert res is not None and res.get("resultType") == "complete", f"wire result {str(res)[:300]}"
    assert not res.get("isError") and "task" not in res
    assert A.result_ok(call)["status"]["ok"] == 2


def _t006(t, ctx):
    ours = [x for x in t.init["tools"] if x.startswith(P)]
    assert [x[len(P):] for x in sorted(ours, key=lambda x: list(CORE_TOOL_NAMES).index(x[len(P):]) if x[len(P):] in CORE_TOOL_NAMES else 99)] == list(CORE_TOOL_NAMES) and len(ours) == 6, f"core tools {ours}"
    A.no_tool_called(t)
    (res,) = t.wire.response("tools/list")[:1] if t.wire.requests("tools/list") else (None,)
    if res is not None:
        assert [x["name"] for x in res["tools"]] == list(CORE_TOOL_NAMES), [x["name"] for x in res["tools"]]


def _t007(t, ctx):
    call = A.tool_called(t, "batch", times=1)[0]
    (e,) = A.wire_called(t, "tools/call", "batch")
    assert (e["params"].get("_meta") or {}).get("progressToken") is not None, "no progressToken in tools/call _meta"
    prog = t.wire.progress(call.use.id)
    assert prog, "no notifications/progress on the batch call"
    import re
    for p in prog:
        assert re.match(r"^\d+/\d+ ok=\d+ err=\d+", p.get("message") or ""), p
    vals = [p["progress"] for p in prog]
    assert all(b > a for a, b in zip(vals, vals[1:])), f"progress not increasing: {vals}"
    assert A.result_ok(call)["status"]["ok"] == 8


def _t008(t, ctx):
    call = A.tool_called(t, "yes_no", times=1)[0]
    A.result_ok(call)
    res = t.wire.result(call.use.id)
    assert res is not None, "wire result not joined via claudecode/toolUseId"
    assert res.get("resultType") == "complete" and res.get("isError") is False, {k: res.get(k) for k in ("resultType", "isError")}
    first = res["content"][0]
    assert first["type"] == "text" and first["text"] == _compact(res["structuredContent"]), f"text != compact(structuredContent): {first['text'][:200]}"
    assert (first.get("annotations") or {}).get("audience") == ["assistant"], first.get("annotations")
    (e,) = A.wire_called(t, "tools/call", "yes_no")
    assert e["params"]["_meta"]["claudecode/toolUseId"] == call.use.id


CASES = [case_from_data(DATA, tid, expect=(fn,)) for tid, fn in
         (("T001", _t001), ("T002", _t002), ("T003", _t003), ("T004", _t004), ("T005", _t005), ("T006", _t006), ("T007", _t007), ("T008", _t008))]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.id}-{c.slug}")
def test_case(case, case_ctx):
    run_case(case, case_ctx)
