"""g10 errors, recovery, config profiles (T091-T100): claude -p provokes tool faults; ToolError code/retryable/path/hint are
checked on the tool_result, never a JSON-RPC error. Profiles unreachable/notopenjev/fault/ext are extra own MCP instances.
Data: cases/g10_errors.json (arguments are passed 'exactly as given', the wrong ones included)."""
import json

import pytest

import cl_assert as A
from cl_cases import case_from_data, run_case
from cl_env import CASES_DIR

DATA = CASES_DIR / "g10_errors.json"
READ = "ReadMcpResourceTool"


def _rpc_ok(t, call):
    """The tool fault reached Claude as a tools/call result with isError, never as a JSON-RPC error."""
    w = call.wire
    assert w is not None, f"{call.use.name}: no tools/call result on the wire (JSON-RPC error? {t.wire.errors()})"
    assert w.get("isError") is True and "structuredContent" not in w, f"{call.use.name}: wire result {str(w)[:300]}"


def _err(t, tool, code, n=1, **kw):
    A.precondition(t, [tool])
    calls = A.tool_called(t, tool)
    assert len(calls) >= n, f"{tool}: {len(calls)} calls, expected {n}"
    return calls, A.result_error(calls[0], code, **kw)


def _t091(t, ctx):
    calls, e = _err(t, "yes_no", "OJ_INVALID_INPUT", n=2, path=A.is_type(str), retryable=False, hint="claim")
    assert "claim" in (e["path"] or "") + e["message"], e   # the failing property is named; the path is the arguments object
    assert "claim" not in calls[0].use.input, calls[0].use.input
    _rpc_ok(t, calls[0])
    A.result_ok(calls[1])
    A.args_match(calls[1], "claim", A.is_type(str))


def _t092(t, ctx):
    (call,), e = _err(t, "batch", "OJ_INVALID_INPUT", path="out.jsonl", retryable=False, msg="relative", hint="absolute")
    A.args_match(call, "output_path", A.eq("out.jsonl"))
    _rpc_ok(t, call)


def _t093(t, ctx):
    (call,), e = _err(t, "batch", "OJ_INVALID_INPUT", path="/etc/hosts", retryable=False, msg="outside")
    A.args_match(call, "items_file.path", A.eq("/etc/hosts"))
    assert call.result.json is None or "results" not in (call.result.json or {}), call.result.text[:200]
    assert "localhost" not in call.result.text, "the outside file content leaked into the error"
    A.no_read(t)
    _rpc_ok(t, call)


def _t094(t, ctx):
    A.precondition(t, ["status", "yes_no"])
    for tool in ("status", "yes_no"):
        (call,) = A.tool_called(t, tool, times=1)
        e = A.result_error(call, "OJ_UNREACHABLE", retryable=True, hint="OPENJEV_BASE_URL")
        assert "127.0.0.1:" in e.get("hint", "") + e.get("message", ""), e
        _rpc_ok(t, call)


def _t095(t, ctx):
    (call,), e = _err(t, "yes_no", "OJ_NOT_FOUND", retryable=False, hint="OPENJEV_BASE_URL")
    _rpc_ok(t, call)


def _t096(t, ctx):
    A.precondition(t, ["recipe"])
    (call,) = A.tool_called(t, "recipe", times=1)
    A.result_ok(call)
    A.result_matches(call, "recipe", A.eq("command_gate"))
    A.result_matches(call, "degraded", A.eq(True))
    A.result_matches(call, "decision", A.eq("ask"))
    A.result_matches(call, "error.code", A.eq("OJ_UNREACHABLE"))
    A.result_matches(call, "error.retryable", A.eq(True))


def _t097(t, ctx):
    A.precondition(t, ["ask_image", "generate"])
    (img,) = A.tool_called(t, "ask_image", times=1)
    A.result_error(img, "OJ_INVALID_INPUT", path="options", retryable=False, msg="think")   # think is not in the ask_image options schema
    A.args_match(img, "options.think", A.eq(64))
    (gen,) = A.tool_called(t, "generate", times=1)
    A.args_match(gen, "model", A.eq("no-such-model"))
    A.result_error(gen, "OJ_UNKNOWN_MODEL", retryable=False)
    A.call_order(t, ["ask_image", "generate"])
    _rpc_ok(t, img)
    _rpc_ok(t, gen)


def _t098(t, ctx):
    (call,), e = _err(t, "lint", "OJ_INVALID_INPUT", retryable=False)
    assert "request" in (e.get("message", "") + e.get("hint", "") + str(e.get("path"))), e
    _rpc_ok(t, call)


FAULTS = (("503", "OJ_UNAVAILABLE", True), ("529", "OJ_OVERLOADED", True), ("401", "OJ_AUTH", False),
          ("sleep", "OJ_TIMEOUT", None), ("500", "OJ_SERVER", None))


def _t099(t, ctx):
    A.precondition(t, ["yes_no"])
    calls = A.tool_called(t, "yes_no")
    by = {}
    for c in calls:
        for kind, *_ in FAULTS:
            if f"#FAULT:{kind}" in str(c.use.input.get("state")):
                by.setdefault(kind, c)
    assert set(by) == {k for k, *_ in FAULTS}, f"fault kinds called: {sorted(by)}"
    for kind, code, retryable in FAULTS:
        e = A.result_error(by[kind], code, retryable=retryable)
        if kind == "503":
            assert e.get("retry_after_s") == 1, e
        _rpc_ok(t, by[kind])


def _t100(t, ctx):
    A.precondition(t, ["recipe", READ])
    rd = A.tool_called(t, READ, where={"uri": "openjev://recipes"})[0]
    j = A.result_ok(rd)
    rows = json.loads(j["contents"][0]["text"])
    rows = rows.get("recipes", rows) if isinstance(rows, dict) else rows
    ids = {r["id"] for r in rows}
    assert "cl_extra_check" in ids and "model_routing" in ids and len(ids) == 30, sorted(ids)
    ex = A.tool_called(t, "recipe", where={"recipe": A.eq("cl_extra_check")}, times=1)[0]
    A.result_ok(ex)
    A.result_matches(ex, "recipe", A.eq("cl_extra_check"))
    A.result_matches(ex, "decision", A.one_of("yes", "no"))
    A.result_matches(ex, "degraded", A.eq(False))
    A.result_matches(ex, "requests", A.ge(1))
    rt = A.tool_called(t, "recipe", where={"recipe": A.eq("model_routing")}, times=1)[0]
    A.result_ok(rt)
    A.result_matches(rt, "degraded", A.eq(False))
    A.result_matches(rt, "reason", A.contains("routing off"))
    A.result_matches(rt, "requests", A.eq(0))
    assert len(t.audit) <= 1, f"only cl_extra_check may read; model_routing must send none: {t.audit}"   # D5: OPENJEV_MCP_LOG


EXPECT = {"T091": _t091, "T092": _t092, "T093": _t093, "T094": _t094, "T095": _t095, "T096": _t096, "T097": _t097,
          "T098": _t098, "T099": _t099, "T100": _t100}
CASES = [case_from_data(DATA, tid, expect=(fn,)) for tid, fn in EXPECT.items()]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.id}-{c.slug}")
def test_case(case, case_ctx):
    run_case(case, case_ctx)
