"""H2 self-tests: no claude, no model, no OpenJev (loopback stubs only). Run with OJ_CLAUDE_SELFTEST=1 or OPENJEV_CLAUDE_LIVE=1."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import cl_assert as A
import cl_claude
import cl_env
import cl_fault
import cl_hooks as H
from cl_cases import Case

PROBES = cl_env.FIXTURES / "probes"
CC = cl_env.REPO / "mcp" / "tests" / "fixtures" / "claude_code"


def ctx_for(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    return SimpleNamespace(run_dir=run, work=tmp_path / "work", base_url="http://127.0.0.1:1", cwd=tmp_path)


def case_with(hooks):
    return Case("H2", "h2", "g08", "f", "s", "p", (), (), hooks=hooks)


# ---- settings.json ------------------------------------------------------------------------------------------------------

def test_settings_shape(tmp_path):
    ctx = ctx_for(tmp_path)
    hooks = {"PreToolUse": {"matcher": "Bash", "args": "pretooluse --unattended --timeout-ms 8000", "env": {"OPENJEV_HOOK_TASK": "Summarise it"}},
             "PostToolUse": {"matcher": "Read", "args": "posttooluse --screen Read"},
             "Stop": {"matcher": "*", "args": "stop --max-blocks 1 --timeout-ms 2500"},
             "UserPromptSubmit": {"args": "userprompt --roster /x/r.json"}}
    p = H.settings_for(case_with(hooks), ctx)
    assert p == ctx.run_dir / "settings.json"
    s = json.loads(p.read_text())["hooks"]
    assert set(s) == set(hooks)
    pre = s["PreToolUse"][0]
    assert pre["matcher"] == "Bash" and pre["hooks"][0]["type"] == "command" and pre["hooks"][0]["timeout"] == 9
    cmd = pre["hooks"][0]["command"]
    assert f"tee -a {ctx.run_dir}/hook_PreToolUse.in" in cmd and f"tee -a {ctx.run_dir}/hook_PreToolUse.out" in cmd
    assert "OPENJEV_BASE_URL=http://127.0.0.1:1" in cmd and "OPENJEV_HOOK_TASK='Summarise it'" in cmd
    assert f"{cl_env.REPO}/.venv/bin/openjev-hook pretooluse --unattended --timeout-ms 8000" in cmd
    assert s["Stop"][0]["hooks"][0]["timeout"] == 4 and s["PostToolUse"][0]["hooks"][0]["timeout"] == 15
    assert "matcher" not in s["UserPromptSubmit"][0] and "hook_UserPromptSubmit.in" in s["UserPromptSubmit"][0]["hooks"][0]["command"]


def test_settings_tuple_form_and_bad_event(tmp_path):
    ctx = ctx_for(tmp_path)
    s = json.loads(H.settings_for(case_with({"PreToolUse": ("Bash", "pretooluse")}), ctx).read_text())
    assert s["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    with pytest.raises(ValueError):
        H.settings_for(case_with({"SessionStart": {"args": "x"}}), ctx)


# ---- wrapper over a fake hook command ----------------------------------------------------------------------------------

def run_wrapper(tmp_path, monkeypatch, fake_body, payload):
    fake = tmp_path / "fake-hook"
    fake.write_text(f"#!/bin/sh\n{fake_body}\n")
    fake.chmod(0o755)
    monkeypatch.setattr(H, "HOOK_BIN", fake)
    ctx = ctx_for(tmp_path)
    cmd = json.loads(H.settings_for(case_with({"PreToolUse": {"matcher": "Bash", "args": "x"}}), ctx).read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    r = subprocess.run(["sh", "-c", cmd], input=payload, capture_output=True, text=True, timeout=20)
    return ctx, r


def test_wrapper_passthrough(tmp_path, monkeypatch):
    ctx, r = run_wrapper(tmp_path, monkeypatch, 'cat >/dev/null; printf \'{"a":1}\'; exit 2', '{"tool_name":"Bash"}')
    assert r.returncode == 0 and r.stdout == '{"a":1}'
    assert (ctx.run_dir / "hook_PreToolUse.in").read_text() == '{"tool_name":"Bash"}\n'
    assert (ctx.run_dir / "hook_PreToolUse.out").read_text() == '{"a":1}\n'
    assert H.hook_lines(SimpleNamespace(hooks={"PreToolUse": [json.loads('{"a":1}')]}), "PreToolUse") == [{"a": 1}]


def test_wrapper_silent_hook_and_records_parse(tmp_path, monkeypatch):
    ctx, r = run_wrapper(tmp_path, monkeypatch, "cat >/dev/null", '{"tool_name":"Read"}')
    assert r.returncode == 0 and r.stdout == ""
    run2 = subprocess.run(["sh", "-c", json.loads((ctx.run_dir / "settings.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]],
                          input='{"tool_name":"Bash"}', capture_output=True, text=True, timeout=20)
    assert run2.returncode == 0
    out, inn = cl_claude._read_hooks(ctx.run_dir)
    assert out == {} or out == {"PreToolUse": []}
    assert inn["PreToolUse"] == [{"tool_name": "Read"}, {"tool_name": "Bash"}]
    t = SimpleNamespace(hooks=out, hooks_in=inn, subtype="success", num_turns=1, cost_usd=0, model="m", permission_denials=[], calls=[], final_text="", run_dir=ctx.run_dir)
    assert len(H.hook_ran(t, "PreToolUse")) == 2
    H.hook_silent(t, "PreToolUse")
    with pytest.raises(AssertionError):
        H.hook_ran(t, "Stop")


def test_wrapper_non_json_out(tmp_path, monkeypatch):
    ctx, _ = run_wrapper(tmp_path, monkeypatch, "cat >/dev/null; echo not-json", "{}")
    out, _ = cl_claude._read_hooks(ctx.run_dir)
    assert out["PreToolUse"] == [{"_raw": "not-json"}]


# ---- decision parsing ---------------------------------------------------------------------------------------------------

def fake_t(event, outs=(), ins=({},)):
    return SimpleNamespace(hooks={event: list(outs)}, hooks_in={event: list(ins)}, subtype="success", num_turns=1, cost_usd=0.0, model="m",
                           permission_denials=[], calls=[], final_text="", run_dir=Path("."))


def test_decision_pretooluse():
    t = fake_t("PreToolUse", [{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "openjev command_gate: x"}}])
    assert H.hook_decision(t, "PreToolUse", "deny", "command_gate")
    assert H.hook_decision(t, "PreToolUse", reason=lambda r: r.startswith("openjev"))
    for kw in ({"decision": "allow"}, {"reason": "nope"}):
        with pytest.raises(AssertionError, match="no line with"):
            H.hook_decision(t, "PreToolUse", **kw)


def test_decision_stop_posttooluse_userprompt():
    t = fake_t("Stop", [{"decision": "block", "reason": "[openjev done_gate] unverified"}])
    assert H.hook_decision(t, "Stop", "block", "done_gate")
    t = fake_t("PostToolUse", [{"decision": "block", "reason": "quarantine", "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "c"}}])
    assert H.hook_decision(t, "PostToolUse", "block")
    t = fake_t("UserPromptSubmit", [{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "use skill 'pdf'"}}])
    assert H.hook_decision(t, "UserPromptSubmit", "context", "'pdf'")
    with pytest.raises(AssertionError):
        H.hook_decision(fake_t("Stop", []), "Stop", "block")


@pytest.mark.parametrize("name,deny", [("hook_pretooluse_allow", False), ("hook_pretooluse_deny", True)])
def test_probe_hook_runs(name, deny):
    t = cl_claude.parse(json.loads((PROBES / f"{name}.json").read_text()), None, PROBES)
    errs = [c.result for c in t.calls if c.result and c.result.is_error]
    assert bool(errs) == deny
    if deny:
        assert "PreToolUse:Bash hook error" in errs[0].text and t.permission_denials


def test_cc_fixtures_parse_with_server_hooks():
    from openjev_mcp import claude_hooks as ch
    p = json.loads((CC / "pretooluse_v2_bash.json").read_text())
    assert p["tool_name"] == "Bash"
    out = ch.pretooluse_output("deny", "r") if hasattr(ch, "pretooluse_output") else None
    if out:
        t = fake_t("PreToolUse", [out], [p])
        assert H.hook_decision(t, "PreToolUse", "deny", "r") and H.payloads(t, "PreToolUse") == [p]


# ---- plugin dir ---------------------------------------------------------------------------------------------------------

def test_plugin_dir(tmp_path):
    ctx = ctx_for(tmp_path)
    root = H.plugin_dir(ctx)
    assert root == tmp_path / "work" / "plugin" / "openjev-skills"
    assert json.loads((root / ".claude-plugin" / "plugin.json").read_text()) == {"name": "openjev-skills", "version": "0.0.0"}
    links = sorted((root / "skills").iterdir())
    assert len(links) == 11 and all(p.is_symlink() and (p / "SKILL.md").exists() for p in links)
    assert [p.name for p in links] == H.skill_names()
    assert H.plugin_dir(ctx) == root and len(list((root / "skills").iterdir())) == 11


# ---- FaultUpstream ------------------------------------------------------------------------------------------------------

class Stub(BaseHTTPRequestHandler):
    def _go(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n)
        out = json.dumps({"stub": True, "path": self.path, "method": self.command, "body": body.decode()}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("x-stub", "1")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = _go

    def log_message(self, *a):
        pass


@pytest.fixture
def fault():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    f = cl_fault.FaultUpstream(f"http://127.0.0.1:{srv.server_port}", sleep_s=3).start()
    yield f
    f.stop()
    srv.shutdown()


def post(f, kind, timeout=10):
    return httpx.post(f.url + "/v1/systemone", json={"state": f"hello #FAULT:{kind}", "questions": {}}, timeout=timeout)


def test_fault_401(fault):
    r = post(fault, "401")
    assert r.status_code == 401 and r.json()["detail"]["error_type"] == "authentication_error"


def test_fault_503(fault):
    r = post(fault, "503")
    assert r.status_code == 503 and r.headers["retry-after"] == "1"


def test_fault_529(fault):
    r = post(fault, "529")
    assert r.status_code == 529 and r.json()["detail"]["error_type"] == "overloaded_error"


def test_fault_500(fault):
    r = post(fault, "500")
    assert r.status_code == 500 and r.headers["content-type"].startswith("text/plain") and r.text


def test_fault_sleep_client_times_out(fault):
    with pytest.raises(httpx.TimeoutException):
        post(fault, "sleep", timeout=1)


def test_fault_forwards(fault):
    r = httpx.post(fault.url + "/v1/systemone", json={"state": "plain"}, timeout=10)
    assert r.status_code == 200 and r.json()["path"] == "/v1/systemone" and r.headers["x-stub"] == "1" and '"plain"' in r.json()["body"]
    g = httpx.get(fault.url + "/health?x=1", timeout=10)
    assert g.json()["method"] == "GET" and g.json()["path"] == "/health?x=1"
    assert httpx.post(fault.url + "/v1/other", json={"state": "#FAULT:401"}, timeout=10).status_code == 200
    assert httpx.post(fault.url + "/v1/systemone", content=b"not json", timeout=10).status_code == 200


def test_fault_maps_like_server():
    from openjev_mcp import mapping
    cases = {"401": "OJ_AUTH", "503": "OJ_UNAVAILABLE", "529": "OJ_OVERLOADED", "500": "OJ_SERVER"}
    f = cl_fault.FaultUpstream("http://127.0.0.1:9").start()
    try:
        for k, code in cases.items():
            r = post(f, k)
            got = mapping._classify(r.status_code, r.headers, r.json() if "json" in r.headers["content-type"] else r.text,
                                    base_url=f.url, model=None, has_images=False, known_models=None)
            assert got[0] == code, (k, got)
    finally:
        f.stop()
