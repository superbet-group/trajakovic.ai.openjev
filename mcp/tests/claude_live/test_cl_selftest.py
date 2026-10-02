"""Harness self-tests: no claude, no model, no OpenJev (loopback stubs only). Run with OJ_CLAUDE_SELFTEST=1 or OPENJEV_CLAUDE_LIVE=1."""
from __future__ import annotations

import json
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest

import cl_assert as A
import cl_cases
import cl_claude
import cl_env
import cl_results
from cl_cases import COVERAGE, GROUPS, Case, Ctx, case_from_data

PROBES = cl_env.FIXTURES / "probes"
EVENT_PROBES = ["init_isolated", "hook_pretooluse_allow", "hook_pretooluse_deny", "resource_read", "skill_agent_gates",
                "wildcard_allow_unlisted_uris", "batch_relpath_then_ok"]


def events(name):
    return json.loads((PROBES / f"{name}.json").read_text())


def transcript(name, wire=None):
    return cl_claude.parse(events(name), cl_claude.WireLog.load(wire) if wire else None, PROBES)


# ---- parsing ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", EVENT_PROBES)
def test_probe_parses(name):
    t = transcript(name)
    assert t.init["apiKeySource"] == "none" and t.subtype == "success" and not t.is_error
    assert t.model.startswith("claude-") and t.cost_usd > 0 and t.num_turns >= 1 and t.final_text
    assert {s["name"]: s["status"] for s in t.init["mcp_servers"]}["openjev"] == "connected"
    A.precondition(t, ["status", "batch"])
    assert sum(1 for x in t.init["tools"] if x.startswith("mcp__openjev__")) == 14


def test_payload_probes():
    pre = json.loads((PROBES / "payload_pretooluse_2.1.287.json").read_text())
    stop = json.loads((PROBES / "payload_stop_2.1.287.json").read_text())
    assert {"session_id", "transcript_path", "cwd", "hook_event_name", "tool_name", "tool_input", "tool_use_id"} <= set(pre)
    assert stop["hook_event_name"] == "Stop" and "last_assistant_message" in stop and "stop_hook_active" in stop


def test_init_isolated_has_no_calls():
    t = transcript("init_isolated")
    assert t.calls == [] and t.tools_called() == [] and t.final_text == "OK"
    A.no_tool_called(t)
    with pytest.raises(AssertionError, match="run_dir"):
        A.tool_called(t, "status")


def test_tool_result_string_error_and_list_with_link():
    t = transcript("batch_relpath_then_ok", PROBES / "wire_tasks_on_batch.jsonl")
    assert t.tools_called() == ["batch", "batch"]
    bad, good = t.of("batch")
    e = A.result_error(bad, "OJ_INVALID_INPUT", path="batch_output.jsonl", retryable=False, msg="relative path", hint="absolute")
    assert e["http_status"] is None and bad.result.links == []
    assert not good.result.is_error and good.result.links[0].startswith("[Resource link: batch_output.jsonl] file:///")   # F17
    assert good.result.json["status"]["ok"] == 2 and good.result.json["next_cursor"] is None
    A.result_ok(good)
    A.result_matches(good, "$.results[1].id", "b")
    A.result_matches(good, "len($.results)", 2)
    A.args_match(good, "$.items[0].id", "a")
    A.args_match(good, "$.output_path", A.regex(r"^/.*/batch_output\.jsonl$"))
    A.call_order(t, ["batch", "batch"])
    with pytest.raises(AssertionError):
        A.result_ok(bad)
    with pytest.raises(AssertionError):
        A.result_error(good, "OJ_INVALID_INPUT")
    with pytest.raises(AssertionError, match="OJ_INVALID_INPUT"):   # failure messages end with the transcript summary
        A.tool_called(t, "batch", times=3)
    assert A.tool_called(t, "batch", where={"$.output_path": A.regex("^batch_output")}, times=1) == [bad]


def test_resource_read_string_content():
    t = transcript("resource_read")
    (call,) = t.of("ReadMcpResourceTool")
    assert call.use.input["server"] == "openjev" and call.use.input["uri"].startswith("openjev://")
    doc = call.result.json
    assert doc["contents"][0]["uri"] == call.use.input["uri"] and json.loads(doc["contents"][0]["text"])
    A.args_match(call, "$.uri", A.regex("^openjev://"))


def test_hook_error_text_and_denials():
    t = transcript("hook_pretooluse_deny")
    (call,) = t.of("Bash")
    assert call.result.is_error and call.result.json is None and call.result.error is None
    assert "PreToolUse:Bash hook error: openjev command_gate" in call.result.text   # F11
    A.denied(t, "Bash")
    assert A.denied(t, "Bash")[0]["tool_input"]["command"].startswith("git push --force")
    with pytest.raises(AssertionError):
        A.denied(t, "Read")
    allow = transcript("hook_pretooluse_allow")
    assert allow.permission_denials == [] and not allow.of("Bash")[0].result.is_error


def test_skills_probe_init():
    t = transcript("skill_agent_gates")
    loaded = [s for s in t.init["skills"] if s.startswith("openjev-skills:")]
    assert len(loaded) == 11
    assert t.of("recipe")[0].use.input["recipe"] == "command_gate"


def test_unlisted_uri_probe():
    t = transcript("wildcard_allow_unlisted_uris")
    uris = [c.use.input["uri"] for c in t.of("ReadMcpResourceTool")]
    assert any(u.startswith("file://") for u in uris)


# ---- wire log --------------------------------------------------------------------------------------------------------

def test_wirelog_probe_format_sse_and_join():
    w = cl_claude.WireLog.load(PROBES / "wire_tasks_on_batch.jsonl")
    assert [e["method"] for e in w.entries] == ["server/discover", "prompts/list", "resources/list", "tools/list", "tools/call", "tools/call"]
    assert w.headers(0)["mcp-protocol-version"] == "2026-07-28" and w.headers(4)["mcp-name"] == "batch"
    assert len(w.requests("tools/call", "batch")) == 2 and w.requests("tools/call", "status") == []
    disc = w.response("server/discover")[0]
    assert "io.modelcontextprotocol/tasks" in disc["capabilities"]["extensions"] and disc["result"] is disc
    assert w.requests("tools/list") and w.response("tools/list") == []   # the probe recorded this body cut at 4000 chars: unparseable, not an error
    t = transcript("batch_relpath_then_ok", PROBES / "wire_tasks_on_batch.jsonl")
    bad, good = t.of("batch")
    assert bad.wire["isError"] is True and bad.wire["resultType"] == "complete"
    assert good.wire["content"][0]["text"].startswith('{"status"')
    prog = w.progress(good.use.id)
    assert len(prog) == 1 and prog[0]["message"].startswith("1/2 ok=1") and w.progress(bad.use.id) == []
    assert w.result("toolu_nope") is None and w.errors() == []
    A.wire_called(t, "tools/call", "batch", params={"$.arguments.output_path": A.regex("batch_output")})
    A.wire_called(t, "server/discover")
    A.wire_header(t, "MCP-Protocol-Version", "2026-07-28")
    A.wire_header(t, "Mcp-Name", "batch", method="tools/call")
    with pytest.raises(AssertionError):
        A.wire_called(t, "resources/read")
    with pytest.raises(AssertionError):
        A.wire_jsonrpc_error(t, -32602)


def test_wirelog_errors_and_tap_format(tmp_path):
    p = tmp_path / "wire.jsonl"
    p.write_text(json.dumps({"method": "resources/read", "headers": {"Mcp-Method": "resources/read"}, "status": 200,
                             "req": {"jsonrpc": "2.0", "id": 7, "method": "resources/read", "params": {"uri": "openjev://nope"}},
                             "resp": [{"jsonrpc": "2.0", "id": 7, "error": {"code": -32602, "message": "Resource not found", "data": {"uri": "openjev://nope"}}}]}) + "\n")
    w = cl_claude.WireLog.load(p)
    assert w.errors()[0]["code"] == -32602 and w.errors()[0]["_method"] == "resources/read"
    t = cl_claude.parse([], w)
    assert A.wire_jsonrpc_error(t, -32602, "not found")["data"]["uri"] == "openjev://nope"


def _serve(app):
    import uvicorn
    s = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off"))
    th = threading.Thread(target=s.run, daemon=True)
    th.start()
    while not (s.started and s.servers):
        time.sleep(0.02)
    return s, th, s.servers[0].sockets[0].getsockname()[1]


def test_wiretap_proxies_json_and_sse(tmp_path):
    import urllib.request
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route

    async def mcp(request):
        req = await request.json()
        if req["method"] == "tools/call":
            async def sse():
                yield b'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progressToken":1,"progress":1}}\n\n'
                yield b'event: message\ndata: {"jsonrpc":"2.0","id":%d,"result":{"content":[],"isError":false}}\n\n' % req["id"]
            return StreamingResponse(sse(), media_type="text/event-stream")
        return JSONResponse({"jsonrpc": "2.0", "id": req["id"], "result": {"tools": []}})

    srv, th, port = _serve(Starlette(routes=[Route("/mcp", mcp, methods=["POST"])]))
    try:
        from cl_servers import WireTap
        with WireTap(f"http://127.0.0.1:{port}", tmp_path / "wire.jsonl") as tap:
            def post(body):
                r = urllib.request.Request(tap.url, json.dumps(body).encode(), {"content-type": "application/json", "mcp-method": body["method"], "mcp-name": "x"})
                with urllib.request.urlopen(r, timeout=10) as resp:
                    return resp.status, resp.read().decode()
            assert post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[0] == 200
            status, text = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "x", "_meta": {"claudecode/toolUseId": "toolu_1", "progressToken": 1}}})
            assert status == 200 and "notifications/progress" in text
        w = cl_claude.WireLog.load(tmp_path / "wire.jsonl")
        assert [e["method"] for e in w.entries] == ["tools/list", "tools/call"] and w.headers(1)["mcp-name"] == "x"
        assert w.response("tools/list")[0]["tools"] == [] and w.result("toolu_1")["isError"] is False
        assert w.progress("toolu_1")[0]["progress"] == 1
    finally:
        srv.should_exit = True
        th.join(10)


def test_read_hooks_generic(tmp_path):
    (tmp_path / "hook_PreToolUse.out").write_text('{"hookSpecificOutput":{"permissionDecision":"allow"}}\nnot json\n')
    (tmp_path / "hook_PreToolUse.in").write_text('{"tool_name":"Bash"}\n')
    (tmp_path / "hook_Stop.out").write_text("")
    out, inn = cl_claude._read_hooks(tmp_path)
    assert out["PreToolUse"][0]["hookSpecificOutput"]["permissionDecision"] == "allow" and out["PreToolUse"][1] == {"_raw": "not json"}
    assert out["Stop"] == [] and inn["PreToolUse"][0]["tool_name"] == "Bash"


# ---- get() and predicates --------------------------------------------------------------------------------------------

DOC = {"answers": {"L1": {"p": 0.93, "band": "yes"}}, "results": [{"id": "a"}, {"id": "b"}], "probabilities": {"x": 0.5, "y": 0.3, "z": 0.2},
       "kept": ["L4"], "per_question": {"escalate": {"accuracy_at_0.5": 1.0}}, "none": None}


def test_get_paths():
    g = A.get
    assert g(DOC, "$.answers.L1.p") == 0.93 and g(DOC, "answers.L1.band") == "yes"
    assert g(DOC, "$.results[0].id") == "a" and g(DOC, "$.results[-1].id") == "b" and g(DOC, "$.results[*].id") == ["a", "b"]
    assert sorted(g(DOC, "$.probabilities.*")) == [0.2, 0.3, 0.5] and g(DOC, "len($.kept)") == 1 and g(DOC, "len($.probabilities)") == 3
    assert g(DOC, "$.per_question.escalate.accuracy_at_0.5") == 1.0 and g(DOC, "$.per_question['escalate']['accuracy_at_0.5']") == 1.0
    assert g(DOC, "$.nope") is A.MISSING and g(DOC, "$.results[5]") is A.MISSING and g(DOC, "len($.nope)") is A.MISSING and g(DOC, "$.none") is None
    assert g(DOC, "$") is DOC


def test_predicates():
    assert A.eq(1)(1) and not A.eq(1)(2) and not A.eq(None)(A.MISSING) and A.eq(None)(None)
    assert A.approx(1.0, 0.01)(1.005) and not A.approx(1.0, 0.001)(1.005) and not A.approx(1)("1")
    assert A.gt(1)(2) and not A.gt(1)(1) and A.ge(1)(1) and A.lt(1)(0) and A.le(1)(1) and not A.gt(1)(None) and not A.gt(1)(True)
    assert A.between(0, 1)(0) and A.between(0, 1)(1) and not A.between(0, 1)(1.1)
    assert A.one_of("a", "b")("a") and A.one_of(["a", "b"])("b") and not A.one_of("a")("c")
    assert A.contains("x")("axb") and A.contains(1)([1, 2]) and A.contains("k")({"k": 1}) and not A.contains("x")(None)
    assert A.regex(r"^\d+/\d+")("1/2 ok=1") and not A.regex("z")("a") and not A.regex("z")(None)
    assert A.is_type(str)("a") and A.is_type(int, float)(1.5) and not A.is_type(dict)([])
    assert A.exists("x") and A.exists(0) and not A.exists(None) and not A.exists(A.MISSING) and A.exists() is A.exists
    assert A.absent(None) and A.absent(A.MISSING) and not A.absent(0) and A.absent() is A.absent
    assert A.set_eq(["a", "b"])(["b", "a"]) and not A.set_eq(["a"])(["a", "b"])
    assert A.sums_to(1, 0.02)([0.5, 0.3, 0.2]) and A.sums_to(1, 0.02)([0.5, 0.49]) and not A.sums_to(1, 0.02)([0.5, 0.2]) and not A.sums_to()([])


def test_weak_and_final_text():
    assert getattr(A.final_text_contains, "weak", False)

    @A.weak
    def prose(t, ctx): ...
    assert prose.weak is True
    t = cl_claude.Transcript(final_text="The score is 0.93", run_dir=Path("rd"))
    A.final_text_contains(t, "0.93", "score")
    A.final_text_contains(t, "zzz", "score", any_of=True)
    with pytest.raises(AssertionError, match="rd"):
        A.final_text_contains(t, "zzz")


def test_no_read_and_cursor_chain_and_jsonl(tmp_path):
    t = transcript("batch_relpath_then_ok")
    A.no_read(t)
    t.audit = [{"tool": "yes_no"}]
    with pytest.raises(AssertionError, match="audit"):
        A.no_read(t)
    t.audit = []
    mk = lambda cur, nxt: cl_claude.Call(cl_claude.ToolUse("i", "mcp__openjev__batch", "batch", {"cursor": cur} if cur else {}),
                                         cl_claude.ToolResult("i", False, "", {"next_cursor": nxt, "meta": {"requests": 0}}, None, []), None, t)
    t.calls = [mk(None, "c1"), mk("c1", "c2"), mk("c2", None)]
    assert len(A.cursor_chain(t, "batch")) == 3
    A.no_read(t, t.calls[0])
    t.calls[1].use.input["cursor"] = "other"
    with pytest.raises(AssertionError, match="cursor"):
        A.cursor_chain(t, "batch")
    f = tmp_path / "o.jsonl"
    f.write_text('{"header":1}\n{"id":"a","status":"ok"}\n{"id":"b","status":"ok"}\n')
    assert len(A.jsonl_rows(f, n=2, ids=["a", "b"])[1]) == 2 and len(A.jsonl_rows(f"file://{f}")[1]) == 2
    with pytest.raises(AssertionError):
        A.jsonl_rows(f, n=3)
    f.write_text('{"header":1}\n{"id":"a","status":"ok"}\n{"id":"a","status":"ok"}\n')
    with pytest.raises(AssertionError, match="duplicate"):
        A.jsonl_rows(f)


# ---- argv, env, render -----------------------------------------------------------------------------------------------

def opts(**kw):
    return cl_claude.ClaudeOpts(**{"prompt": "hi {x}", "model": "haiku", "max_turns": 3, "budget_usd": 0.25, "mcp_url": "http://127.0.0.1:1/mcp", **kw})


def test_argv_profile(tmp_path):
    a = cl_claude.argv(opts(), tmp_path)
    assert a[:3] == [cl_env.CLAUDE_BIN, "-p", "hi {x}"] and "--bare" not in a and "--safe-mode" not in a
    assert json.loads((tmp_path / "mcp.json").read_text()) == {"mcpServers": {"openjev": {"type": "http", "url": "http://127.0.0.1:1/mcp"}}}
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--exclude-dynamic-system-prompt-sections"):
        assert flag in a
    assert a[a.index("--setting-sources") + 1] == "" and a[a.index("--tools") + 1] == ""
    assert a[a.index("--permission-mode") + 1] == "dontAsk" and a[a.index("--allowedTools") + 1] == "mcp__openjev"
    assert a[a.index("--max-budget-usd") + 1] == "0.25" and a[a.index("--max-turns") + 1] == "3" and a[a.index("--output-format") + 1] == "json"
    full = cl_claude.argv(opts(skills=True, persist=True, settings=Path("/s.json"), plugin_dir=Path("/p"), debug_file=Path("/d.log"),
                               tools="Bash,Read", allow=("mcp__openjev", "Bash(echo *)"), cli=("--verbose",)), tmp_path)
    assert "--disable-slash-commands" not in full and "--no-session-persistence" not in full and full[-1] == "--verbose"
    assert full[full.index("--settings") + 1] == "/s.json" and full[full.index("--plugin-dir") + 1] == "/p" and full[full.index("--debug-file") + 1] == "/d.log"
    assert full[full.index("--allowedTools") + 1] == "mcp__openjev,Bash(echo *)" and full[full.index("--tools") + 1] == "Bash,Read"


def test_strip_env():
    env = {k: "x" for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                            "CLAUDE_CODE_USE_FOUNDRY", "PATH", "HOME")}
    assert sorted(cl_env.strip_env(env)) == ["HOME", "PATH"]


def make_ctx(tmp_path, case=None, **seed):
    case = case or Case("T999", "x", "g00", "f", "s", "p", (), (), args={"items": [{"id": "a", "state": "{cwd}/x.csv"}], "n": 2, "who": "me"})
    return Ctx(case=case, attempt=1, run_id="r", work=tmp_path / "work", cwd=tmp_path / "cwd", run_dir=tmp_path / "rd", data=tmp_path / "work/data",
               fixtures=cl_env.FIXTURES, repo=cl_env.REPO, base_url="http://x", instances=None, seed=seed)


def test_render_json_prompts_survive(tmp_path):
    ctx = make_ctx(tmp_path, out=str(tmp_path / "o.jsonl"), n=3)
    r = ctx.render('Call batch with {"output_path":"{seed:out}","cwd":"{cwd}","n":{seed:n}} and {unknown} {} {"a":{"b":1}} data={data}')
    assert r == f'Call batch with {{"output_path":"{tmp_path}/o.jsonl","cwd":"{tmp_path}/cwd","n":3}} and {{unknown}} {{}} {{"a":{{"b":1}}}} data={tmp_path}/work/data'
    assert ctx.render("args={args}") == f'args={{"items":[{{"id":"a","state":"{tmp_path}/cwd/x.csv"}}],"n":2,"who":"me"}}'
    assert ctx.render("{arg:who}|{arg:n}|{arg:items}") == f'me|2|[{{"id":"a","state":"{tmp_path}/cwd/x.csv"}}]'
    assert ctx.render("{work} {fixtures} {repo}").split() == [str(tmp_path / "work"), str(cl_env.FIXTURES), str(cl_env.REPO)]
    with pytest.raises(KeyError):
        ctx.render("{arg:nope}")
    with pytest.raises(KeyError):
        ctx.render("{seed:nope}")


def test_case_from_data(tmp_path):
    p = tmp_path / "g99.json"
    p.write_text(json.dumps({"group": "g99", "cases": {"T900": {"slug": "s", "feature": "f", "spec": "sp", "prompt": "p {args}", "primary": ["batch"],
                                                              "covers": ["tool:batch"], "allow": ["mcp__openjev", "Bash"], "args": {"k": 1}, "max_turns": 6},
                                                      "T901": {"slug": "bad", "feature": "f", "spec": "s", "prompt": "p", "oops": 1}}}))
    c = case_from_data(p, "T900", expect=lambda t, c: None, tier="strong")
    assert c.group == "g99" and c.primary == ("batch",) and c.allow == ("mcp__openjev", "Bash") and c.tier == "strong" and c.max_turns == 6 and c.args == {"k": 1}
    assert c.timeout_s == 180 and c.profile == "default" and len(c.expect) == 1 and c.covers == ("tool:batch",)
    with pytest.raises(TypeError, match="oops"):
        case_from_data(p, "T901", expect=())
    assert cl_cases.load_group(p)["group"] == "g99"


# ---- slots and ports -------------------------------------------------------------------------------------------------

def test_slot_semaphore(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("OJ_CLAUDE_SLOTS", "2")
    with cl_env.slot() as a, cl_env.slot() as b:
        assert {a, b} == {0, 1}
        t0 = time.monotonic()
        with pytest.raises(RuntimeError, match="no claude slot"):
            with cl_env.slot(wait_s=1.2):
                pass
        assert time.monotonic() - t0 >= 1.0
    with cl_env.slot() as c:   # both released: a slot is free again
        assert c in (0, 1)
    assert (tmp_path / "openjev-claude-live-slots" / "slot-0.lock").exists()


def test_slot_waits_then_proceeds(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    out = []

    def third():
        with cl_env.slot(slots=2, poll_s=0.1) as s:
            out.append((s, time.monotonic()))
    with cl_env.slot(slots=2) as a, cl_env.slot(slots=2) as b:
        th = threading.Thread(target=third)
        th.start()
        time.sleep(0.6)
        assert not out   # waiting
        released = time.monotonic()
    th.join(10)
    assert out and out[0][1] >= released - 0.01 and out[0][0] in (0, 1)


def test_free_port_skips_bound_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        bound = s.getsockname()[1]
        p = cl_env.free_port(bound, bound + 20)
        assert p != bound and bound <= p <= bound + 20
        assert cl_env.free_port(bound, bound + 20, skip={p}) not in (bound, p)
        with pytest.raises(RuntimeError):
            cl_env.free_port(bound, bound)


def test_run_id_shape():
    assert cl_env.run_id() == cl_env.run_id() and len(cl_env.run_id()) > 5


# ---- run_case (fake run_claude) --------------------------------------------------------------------------------------

@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    monkeypatch.setattr(cl_env, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(cl_env, "_RUN_ID", "selftest")
    calls = []

    class Inst:
        def tail(self, n=200): return "tail"

    class Insts:
        def get(self, profile="default"): return Inst()

    def factory(case, attempt):
        cwd, rd = tmp_path / "cwd" / f"{case.id}-a{attempt}", cl_results.run_root() / "runs" / f"{case.id}-a{attempt}"
        shutil.rmtree(cwd, ignore_errors=True)
        shutil.rmtree(rd, ignore_errors=True)
        cwd.mkdir(parents=True)
        rd.mkdir(parents=True)
        for f in ("argv.json", "claude.json", "wire.jsonl", "stderr.txt"):
            (rd / f).write_text("x")
        return Ctx(case, attempt, "selftest", tmp_path, cwd, rd, tmp_path, cl_env.FIXTURES, cl_env.REPO, "http://x", Insts())

    def script(*names):
        seq = list(names)

        def fake(prompt, *, ctx, **kw):
            calls.append((ctx.attempt, prompt, kw))
            t = transcript(seq.pop(0))
            t.run_dir, t.cwd = ctx.run_dir, ctx.cwd
            return t
        monkeypatch.setattr(cl_cases, "run_claude", fake)
    return factory, script, calls


def _case(**kw):
    return Case("T998", "fake", "g00", "f", "s", kw.pop("prompt", "go {args}"), kw.pop("expect", (lambda t, c: A.tool_called(t, "batch", times=2),)), ("batch",), args={"a": 1}, **kw)


def test_run_case_pass_prunes_and_logs(fake_env):
    factory, script, calls = fake_env
    script("batch_relpath_then_ok")
    t = cl_cases.run_case(_case(), factory)
    rows = cl_results.load()
    assert len(rows) == 1 and rows[0]["pass"] and rows[0]["tools"] == ["batch", "batch"] and rows[0]["cost_usd"] > 0 and rows[0]["run_dir"] == "runs/T998-a1"
    assert calls[0][1] == 'go {"a":1}' and sorted(p.name for p in t.run_dir.iterdir()) == ["argv.json", "claude.json", "wire.jsonl"]
    assert not t.cwd.exists()


def test_run_case_retries_once_when_primary_not_called(fake_env):
    factory, script, calls = fake_env
    script("init_isolated", "batch_relpath_then_ok")
    cl_cases.run_case(_case(), factory)
    rows = cl_results.load()
    assert [r["attempt"] for r in rows] == [1, 2] and rows[0]["error_class"] == "model_choice" and not rows[0]["pass"]
    assert rows[1]["pass"] and rows[1]["retry_of"] == 1 and rows[1]["retry_reason"] == "model_did_not_call" and [c[0] for c in calls] == [1, 2]


def test_run_case_no_second_retry_and_assertion_not_retried(fake_env):
    factory, script, calls = fake_env
    script("init_isolated", "init_isolated")
    with pytest.raises(AssertionError, match="model_choice"):
        cl_cases.run_case(_case(), factory)
    assert [r["attempt"] for r in cl_results.load()] == [1, 2] and not cl_results.load()[1]["pass"]
    factory2, script2, calls2 = fake_env
    n0 = len(calls2)
    script2("batch_relpath_then_ok")
    with pytest.raises(AssertionError, match="assertion"):
        cl_cases.run_case(_case(expect=(lambda t, c: A.tool_called(t, "batch", times=5),)), factory2)
    assert len(calls2) - n0 == 1 and cl_results.load()[-1]["error_class"] == "assertion"
    ev = [p for p in cl_results.run_root().glob("runs/T998-a1/*")]
    assert any(p.name == "mcp-tail.txt" for p in ev)   # failing run dir keeps evidence


def test_run_case_denied_primary_is_not_retried(fake_env):
    factory, script, calls = fake_env
    script("hook_pretooluse_deny")
    cl_cases.run_case(Case("T997", "den", "g00", "f", "s", "p", (lambda t, c: A.denied(t, "Bash"),), ("mcp__nothing",)), factory)
    assert len(calls) == 1


def test_run_case_precondition_is_harness_error(fake_env, monkeypatch):
    factory, script, calls = fake_env
    script("init_isolated")
    orig = cl_cases.run_claude

    def bad(prompt, *, ctx, **kw):
        t = orig(prompt, ctx=ctx, **kw)
        t.init = {**t.init, "apiKeySource": "ANTHROPIC_API_KEY"}
        return t
    monkeypatch.setattr(cl_cases, "run_claude", bad)
    with pytest.raises(A.HarnessError, match="apiKeySource"):
        cl_cases.run_case(_case(), factory)
    assert len(calls) == 1 and cl_results.load()[0]["error_class"] == "harness"


def test_results_summary():
    rows = [{"id": "T001", "group": "g01", "pass": False, "duration_ms": 100, "cost_usd": 0.1, "error_class": "model_choice", "run_dir": "runs/T001-a1"},
            {"id": "T001", "group": "g01", "pass": True, "duration_ms": 300, "cost_usd": 0.2, "retry_of": 1, "run_dir": "runs/T001-a2"},
            {"id": "T002", "group": "g01", "pass": False, "duration_ms": 200, "cost_usd": 0.05, "error_class": "assertion", "failure": "x", "run_dir": "runs/T002-a1"}]
    s = cl_results.summarize(rows)
    assert s["groups"] == {"g01": {"pass": 1, "fail": 1}} and s["cost_usd"] == 0.35 and s["retries"] == 1 and s["p50_ms"] == 200
    assert s["failing"] == [{"id": "T002", "error_class": "assertion", "failure": "x", "run_dir": "runs/T002-a1"}]


# ---- instances (no process spawned) ----------------------------------------------------------------------------------

def test_profiles_and_env(tmp_path, monkeypatch):
    import cl_servers
    assert set(cl_servers.PROFILES) == {"default", "core", "tasks", "ext", "unreachable", "notopenjev", "fault"}
    monkeypatch.setenv("OPENJEV_MCP_TOKEN", "leak")
    inst = cl_servers.Instances(tmp_path / "w", "http://127.0.0.1:8080/")
    env = inst._env("core", cl_servers.PROFILES["core"](inst))
    assert env["OPENJEV_MCP_TOOLSETS"] == "core" and env["OPENJEV_MCP_ROOTS"] == str(tmp_path / "w") and "OPENJEV_MCP_TOKEN" not in env
    assert env["OPENJEV_BASE_URL"] == "http://127.0.0.1:8080" and env["OPENJEV_MCP_LOG"].endswith("mcp-core.audit.jsonl")
    un = cl_servers.PROFILES["unreachable"](inst)
    assert un["OPENJEV_MCP_RETRIES"] == "0" and un["OPENJEV_BASE_URL"].startswith("http://127.0.0.1:83")
    with pytest.raises(KeyError):
        inst.get("nope")
    monkeypatch.setattr(cl_env, "FIXTURES", tmp_path / "nofix")
    with pytest.raises(RuntimeError, match="recipe_extra"):
        cl_servers.PROFILES["ext"](inst)


def test_audit_mark_and_since(tmp_path):
    from cl_servers import McpInstance
    log = tmp_path / "a.jsonl"
    i = McpInstance("default", 1, "u", "r", log, tmp_path, tmp_path / "o.log", None)
    assert i.audit_mark() == 0 and i.audit_since(0) == []
    log.write_text('{"tool":"ask"}\n')
    m = i.audit_mark()
    with open(log, "a") as f:
        f.write('{"tool":"yes_no"}\nbroken\n')
    assert [r["tool"] for r in i.audit_since(m)] == ["yes_no"] and i.tail(1) == ""


# ---- catalogue (D8) --------------------------------------------------------------------------------------------------

def test_coverage_vocabulary():
    from openjev_mcp import TOOL_NAMES
    assert {f"tool:{t}" for t in TOOL_NAMES} <= COVERAGE
    n = lambda pre: sum(1 for k in COVERAGE if k.startswith(pre))
    assert (n("tool:"), n("resource:"), n("template:"), n("prompt:"), n("recipe:"), n("hook:"), n("skill:"), n("profile:")) == (14, 6, 3, 5, 29, 4, 6, 6)
    assert {"file://", "skills:loaded", "jsonrpc:-32602", "loop:batch_cursor", "loop:batch_resume", "error:OJ_SERVER", "error:OJ_INVALID_INPUT:cursor",
            "template:openjev://audits/{question_hash}", "recipe:command_gate"} <= COVERAGE
    assert [(g, lo, hi) for g, (_, lo, hi) in GROUPS.items()][0] == ("g01", 1, 8) and GROUPS["g10"][2] == 100 and len(GROUPS) == 10
    assert sum(hi - lo + 1 for _, lo, hi in GROUPS.values()) == 100


def _present():
    return {g: cs for g, cs in cl_cases.load_catalogue().items()}


def test_every_case_has_a_deterministic_check():
    for g, cs in _present().items():
        for c in cs:
            assert c.expect and any(not getattr(fn, "weak", False) for fn in c.expect), f"{c.id}: only weak expectations"


def test_catalogue_groups():
    for g, cs in _present().items():
        name, lo, hi = GROUPS[g]
        ids = [c.id for c in cs]
        assert len(set(ids)) == len(ids), f"{g}: duplicate ids"
        assert all(lo <= int(i[1:]) <= hi and i.startswith("T") for i in ids), f"{g}: ids outside T{lo:03d}-T{hi:03d}: {ids}"
        assert 8 <= len(cs) <= 14, f"{g}: {len(cs)} cases"
        assert {c.group for c in cs} == {g}
        data = json.loads((cl_env.CASES_DIR / f"{g}_{name}.json").read_text())
        assert set(data["cases"]) == set(ids), f"{g}: data ids != CASES ids"
        for c in cs:
            assert set(c.covers) <= COVERAGE, f"{c.id}: unknown covers {sorted(set(c.covers) - COVERAGE)}"


def test_catalogue_global():
    cat = _present()
    missing = [g for g in GROUPS if g not in cat]
    if missing:
        pytest.skip(f"groups not written yet: {missing}")
    ids = sorted(c.id for cs in cat.values() for c in cs)
    assert ids == [f"T{i:03d}" for i in range(1, 101)]
    union = {k for cs in cat.values() for c in cs for k in c.covers}
    assert COVERAGE <= union, f"uncovered: {sorted(COVERAGE - union)}"
