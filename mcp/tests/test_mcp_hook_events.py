"""openjev-hook stop, userprompt, posttooluse: decisions over stub answers, fail modes (timeout, unreachable, bad
JSON, 5xx), the stateless block cap, output shapes and the CLI as a subprocess. Fixtures are hand-built from the
Claude Code hooks reference (not recorded): a known gap."""
from __future__ import annotations

import json
import os
import subprocess
import time

import httpx
import pytest
import stubs

from openjev_mcp import claude_hooks as ch
from openjev_mcp.hook import decide_posttooluse, decide_stop, decide_userprompt, load_roster

pytestmark = pytest.mark.anyio

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures", "claude_code")
HOOK = os.path.join(stubs.REPO, ".venv", "bin", "openjev-hook")
ROSTER = os.path.join(FIX, "skill_roster.json")
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith(("OPENJEV_", "ANTHROPIC_"))}


def fx(name: str) -> dict:
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def stop_payload(transcript="stop_v2_transcript.jsonl", **extra) -> dict:
    return {**fx("stop_v2.json"), "transcript_path": os.path.join(FIX, transcript), **extra}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setenv("OPENJEV_MCP_RETRIES", "0")


def noul(p):
    return {"type": "noul", "noul": p}


def choice(label, labels, p=0.95):
    rest = (1 - p) / max(1, len(labels) - 1)
    return {"type": "choice", "choice": label, "confidence": p,
            "probabilities": {k: (p if k == label else rest) for k in labels}}


def score(v):
    return {"type": "score", "score": v, "legend": {}, "probabilities": {"0": 1.0}, "confidence": 0.9}


def served(fn):
    """A stub engine answering through fn(qid, question) -> raw answer."""
    engine = stubs.StubEngine(answers=lambda qs, state, opts: {q: fn(q, s) for q, s in qs.items()})
    return engine, stubs.asgi_transport(stubs.openjev_app(engine=engine))


def gate(verified=0.02, claims=0.99, nxt="continue"):
    def fn(q, spec):
        return {"verified": noul(verified), "claims": noul(claims)}.get(q) or choice(nxt, list(spec["criteria"]))
    return served(fn)


def fault(kind: str):
    if kind == "refused":
        return stubs.fault_transport(exc=httpx.ConnectError("refused")), {}
    if kind == "503":
        return stubs.fault_transport(503, headers={"content-type": "application/json"}, body=b'{"detail":"down"}'), {}
    if kind == "badjson":
        return stubs.fault_transport(200, body=b"{not json"), {}
    return stubs.fault_transport(200, body=b"{}", delay_s=3.0), {"timeout_ms": 200}


FAULTS = ["refused", "503", "badjson", "slow"]


# ---- parsing and shapes ----

def test_parse_events():
    s = ch.parse_stop(fx("stop_v2.json"))
    assert s.active is False and s.last_message.startswith("Fixed the off-by-one")
    assert ch.parse_stop({"stop_hook_active": True}).active is True
    assert ch.parse_userprompt(fx("userprompt_v2.json")).prompt.startswith("merge these")
    assert ch.parse_userprompt({"prompt_text": "hi"}).prompt == "hi"
    p = ch.parse_posttooluse(fx("posttooluse_v2_webfetch.json"))
    assert p.tool_name == "WebFetch" and "banana" in ch.response_source(p) and "ignore your previous" in ch.response_text(p.response)


@pytest.mark.parametrize("fn,payload", [
    (ch.parse_stop, {"hook_event_name": "PreToolUse"}), (ch.parse_stop, []),
    (ch.parse_userprompt, {"prompt": "  "}), (ch.parse_userprompt, {"hook_event_name": "Stop", "prompt": "x"}),
    (ch.parse_posttooluse, {"tool_response": "x"}), (ch.parse_posttooluse, "x"), (ch.parse_posttooluse, ch.BAD_JSON)])
def test_parse_rejects(fn, payload):
    with pytest.raises(ch.HookInputError):
        fn(payload)


def test_response_text_shapes():
    assert ch.response_text("abc ") == "abc"
    assert ch.response_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"
    assert ch.response_text({"content": [{"type": "text", "text": "c"}]}) == "c"
    assert json.loads(ch.response_text({"k": 1})) == {"k": 1}
    assert ch.response_text(None) == "" and len(ch.response_text("x" * 50000)) == 20000


def test_output_shapes():
    assert ch.stop_output("r") == {"decision": "block", "reason": "r"}
    assert ch.userprompt_output("c") == {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "c"}}
    assert ch.posttooluse_output("c") == {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "c"}}
    assert ch.posttooluse_output("c", block_reason="b")["decision"] == "block"


def test_stop_turn_builds_the_report():
    t = ch.stop_turn(os.path.join(FIX, "stop_v2_transcript.jsonl"))
    assert t.task == "Fix off-by-one in paginate()" and t.prior_blocks == 0
    assert t.timeline == ("1. Read: src/pager.py -> def paginate(n): ...", "2. Edit: src/pager.py -> edited")
    assert t.final_message.startswith("Fixed the off-by-one")
    assert ch.stop_turn(None) is None and ch.stop_turn("/nonexistent") is None


def test_stop_turn_counts_blocks_and_skips_feedback_as_task():
    t = ch.stop_turn(os.path.join(FIX, "stop_v2_blocked_transcript.jsonl"))
    assert t.prior_blocks == 2 and t.task == "Fix off-by-one in paginate()"
    assert t.timeline[1] == "2. Bash: pytest -q -> ERROR 2 failed, 10 passed"


# ---- stop ----

async def test_stop_blocks_an_unverified_done_claim():
    engine, transport = gate()
    out = await decide_stop(stop_payload(), transport=transport)
    assert set(out) == {"decision", "reason"} and out["decision"] == "block"
    assert out["reason"].startswith(ch.BLOCK_MARKER)
    state = engine.calls[0]["state"]
    assert "Fix off-by-one in paginate()" in state and "2. Edit: src/pager.py" in state and "ready to merge" in state


async def test_stop_allows_when_verified():
    _, transport = gate(verified=0.97, claims=0.9, nxt="allow_stop")
    assert await decide_stop(stop_payload(), transport=transport) is None


async def test_stop_escalate_allows_with_a_system_message():
    _, transport = gate(verified=0.5, claims=0.5, nxt="escalate")
    out = await decide_stop(stop_payload(), transport=transport)
    assert set(out) == {"systemMessage"} and "human decision" in out["systemMessage"]


async def test_stop_block_cap_is_stateless():
    engine, transport = gate()
    # first stop, no marker: block
    assert (await decide_stop(stop_payload(), transport=transport))["decision"] == "block"
    # stop_hook_active with one marked block in the transcript: still below 2, block
    one = await decide_stop(stop_payload(stop_hook_active=True), max_blocks=2, transport=transport)
    assert one["decision"] == "block"          # marker not in this transcript: counts as 1
    n = len(engine.calls)
    # two marked blocks in the transcript: cap reached, allow without a read
    out = await decide_stop(stop_payload("stop_v2_blocked_transcript.jsonl", stop_hook_active=True), transport=transport)
    assert out is None and len(engine.calls) == n
    # a marked transcript but stop_hook_active false is a new stop: counts 0
    assert (await decide_stop(stop_payload("stop_v2_blocked_transcript.jsonl"), transport=transport))["decision"] == "block"
    assert await decide_stop(stop_payload(stop_hook_active=True), max_blocks=1, transport=transport) is None
    assert await decide_stop(stop_payload(), max_blocks=0, transport=transport) is None


async def test_stop_no_read_without_work_or_transcript():
    transport = stubs.fail_on_request_transport()
    no_tools = stop_payload("transcript.jsonl", last_assistant_message="done")      # no tool call in the turn
    assert await decide_stop(no_tools, transport=transport) is None
    assert await decide_stop({"hook_event_name": "Stop"}, transport=transport) is None
    assert await decide_stop(stop_payload(transcript_path="/nonexistent"), transport=transport) is None
    assert transport.requests == []


@pytest.mark.parametrize("kind", FAULTS)
async def test_stop_fails_open(kind):
    transport, extra = fault(kind)
    start = time.monotonic()
    assert await decide_stop(stop_payload(), transport=transport, **extra) is None
    assert time.monotonic() - start < 2.5


@pytest.mark.parametrize("payload", [[], "x", None, {"hook_event_name": "PreToolUse"}])
async def test_stop_bad_payload_fails_open(payload):
    assert await decide_stop(payload, transport=stubs.fail_on_request_transport()) is None


async def test_stop_bad_config_fails_open(monkeypatch):
    monkeypatch.setenv("OPENJEV_MCP_PORT", "nope")
    assert await decide_stop(stop_payload(), transport=stubs.fail_on_request_transport()) is None


# ---- userprompt ----

LABELS = ["pdf", "xlsx", "docx", "none"]


def picker(label, p=0.95):
    return served(lambda q, spec: choice(label, list(spec["criteria"]), p))


async def test_userprompt_injects_a_hint_above_threshold():
    engine, transport = picker("pdf")
    out = await decide_userprompt(fx("userprompt_v2.json"), roster=ROSTER, transport=transport)
    inner = out["hookSpecificOutput"]
    assert set(out) == {"hookSpecificOutput"} and inner["hookEventName"] == "UserPromptSubmit"
    assert "'pdf'" in inner["additionalContext"] and "Read, extract, merge" in inner["additionalContext"]
    assert "merge these three PDF" in engine.calls[0]["state"]


async def test_userprompt_threshold_gates_the_hint():
    _, transport = picker("pdf", p=0.7)
    assert await decide_userprompt(fx("userprompt_v2.json"), roster=ROSTER, transport=transport) is None
    assert await decide_userprompt(fx("userprompt_v2.json"), roster=ROSTER, threshold=0.65, transport=transport)


async def test_userprompt_none_gives_no_hint():
    _, transport = picker("none")
    assert await decide_userprompt(fx("userprompt_v2.json"), roster=ROSTER, transport=transport) is None


async def test_userprompt_roster_forms(tmp_path):
    (tmp_path / "r.json").write_text(json.dumps({"pdf": "PDF work"}))
    assert load_roster(str(tmp_path / "r.json")) == [{"id": "pdf", "description": "PDF work"}]
    for bad in ("{not json", "[]", "[1]", '{"a": 3}'):
        (tmp_path / "b.json").write_text(bad)
        transport = stubs.fail_on_request_transport()
        assert await decide_userprompt(fx("userprompt_v2.json"), roster=str(tmp_path / "b.json"), transport=transport) is None
        assert transport.requests == []
    assert await decide_userprompt(fx("userprompt_v2.json"), roster=str(tmp_path / "missing.json"),
                                   transport=stubs.fail_on_request_transport()) is None


@pytest.mark.parametrize("kind", FAULTS)
async def test_userprompt_fails_open(kind):
    transport, extra = fault(kind)
    start = time.monotonic()
    assert await decide_userprompt(fx("userprompt_v2.json"), roster=ROSTER, transport=transport, **extra) is None
    assert time.monotonic() - start < 2.5


@pytest.mark.parametrize("payload", [[], None, {"prompt": ""}, {"hook_event_name": "Stop", "prompt": "x"}])
async def test_userprompt_bad_payload_fails_open(payload):
    assert await decide_userprompt(payload, roster=ROSTER, transport=stubs.fail_on_request_transport()) is None


# ---- posttooluse ----

def screen_engine(injects, harm):
    return served(lambda q, spec: noul(injects) if q == "injects" else score(harm))


async def test_posttooluse_quarantines_an_injection():
    engine, transport = screen_engine(0.99, 3.0)
    out = await decide_posttooluse(fx("posttooluse_v2_webfetch.json"), transport=transport)
    assert out["decision"] == "block" and "QUARANTINE" in out["reason"]
    inner = out["hookSpecificOutput"]
    assert inner["hookEventName"] == "PostToolUse" and "do not follow" in inner["additionalContext"]
    assert "ignore your previous instructions" in engine.calls[0]["state"]
    assert "WebFetch result: https://recipes.example.com/banana-bread" in engine.calls[0]["state"]


async def test_posttooluse_passes_clean_content_and_skips_unmatched_tools():
    _, transport = screen_engine(0.02, 0.0)
    assert await decide_posttooluse(fx("posttooluse_v2_webfetch.json"), transport=transport) is None
    quiet = stubs.fail_on_request_transport()
    other = {**fx("posttooluse_v2_webfetch.json"), "tool_name": "Read"}
    assert await decide_posttooluse(other, transport=quiet) is None
    empty = {**fx("posttooluse_v2_webfetch.json"), "tool_response": ""}
    assert await decide_posttooluse(empty, transport=quiet) is None
    assert quiet.requests == []


async def test_posttooluse_screen_globs():
    _, transport = screen_engine(0.99, 3.0)
    mcp = {**fx("posttooluse_v2_webfetch.json"), "tool_name": "mcp__slack__read", "tool_input": {}}
    assert (await decide_posttooluse(mcp, transport=transport))["decision"] == "block"
    assert await decide_posttooluse(mcp, screen="WebFetch", transport=stubs.fail_on_request_transport()) is None
    assert (await decide_posttooluse(mcp, screen="Foo, mcp__slack__*", transport=transport))["decision"] == "block"


async def test_posttooluse_uncertain_band_adds_a_note_without_blocking():
    _, transport = screen_engine(0.5, 0.0)
    out = await decide_posttooluse(fx("posttooluse_v2_webfetch.json"), transport=transport)
    assert "decision" not in out and "uncertain" in out["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("kind", FAULTS)
async def test_posttooluse_fails_to_uncertain_with_a_note(kind):
    transport, extra = fault(kind)
    start = time.monotonic()
    out = await decide_posttooluse(fx("posttooluse_v2_webfetch.json"), transport=transport, **extra)
    assert "decision" not in out
    note = out["hookSpecificOutput"]["additionalContext"]
    assert "uncertain" in note and "unavailable" in note and "untrusted" in note
    assert time.monotonic() - start < 2.5


@pytest.mark.parametrize("payload", [[], None, {"tool_response": "x"}])
async def test_posttooluse_bad_payload_has_no_opinion(payload):
    assert await decide_posttooluse(payload, transport=stubs.fail_on_request_transport()) is None


# ---- the CLI as a process ----

def run_cli(*args, stdin, env=None, timeout=30):
    return subprocess.run([HOOK, *args], input=stdin, capture_output=True, text=True, timeout=timeout,
                          env={**CLEAN_ENV, **(env or {})}, cwd=stubs.REPO)


DEAD = {"OPENJEV_BASE_URL": "http://127.0.0.1:9", "OPENJEV_MCP_RETRIES": "0"}


def test_cli_stop_fails_open_when_unreachable():
    res = run_cli("stop", "--timeout-ms", "3000", stdin=json.dumps(stop_payload()), env=DEAD)
    assert (res.returncode, res.stdout) == (0, "")


def test_cli_stop_bad_json_and_cap():
    assert run_cli("stop", stdin="{nope") .stdout == ""
    res = run_cli("stop", "--max-blocks", "1", stdin=json.dumps(stop_payload(stop_hook_active=True)), env=DEAD)
    assert (res.returncode, res.stdout) == (0, "")


def test_cli_userprompt_fails_open():
    args = ("userprompt", "--roster", ROSTER, "--timeout-ms", "3000")
    res = run_cli(*args, stdin=open(os.path.join(FIX, "userprompt_v2.json")).read(), env=DEAD)
    assert (res.returncode, res.stdout) == (0, "")
    assert run_cli(*args, stdin="[[").stdout == ""
    assert run_cli("userprompt", stdin="{}").returncode == 2        # --roster is required


def test_cli_posttooluse_unreachable_gives_the_uncertain_note():
    res = run_cli("posttooluse", "--timeout-ms", "3000", stdin=open(os.path.join(FIX, "posttooluse_v2_webfetch.json")).read(), env=DEAD)
    out = json.loads(res.stdout)
    assert res.returncode == 0 and "uncertain" in out["hookSpecificOutput"]["additionalContext"]
    assert len(res.stdout.strip().splitlines()) == 1
    other = {**fx("posttooluse_v2_webfetch.json"), "tool_name": "Read"}
    assert run_cli("posttooluse", stdin=json.dumps(other), env=DEAD).stdout == ""
    assert run_cli("posttooluse", stdin="{nope").stdout == ""


def test_cli_timeout_backstop_holds_with_a_hung_server():
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)                                                 # accepts connections, never answers
    try:
        start = time.monotonic()
        res = run_cli("posttooluse", "--timeout-ms", "1500", stdin=open(os.path.join(FIX, "posttooluse_v2_webfetch.json")).read(),
                      env={**DEAD, "OPENJEV_BASE_URL": f"http://127.0.0.1:{srv.getsockname()[1]}", "OPENJEV_MCP_TIMEOUT_MS": "60000"})
        assert time.monotonic() - start < 5
        assert "uncertain" in json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"]
    finally:
        srv.close()


def test_new_subcommands_have_help():
    for sub in ("stop", "userprompt", "posttooluse"):
        res = subprocess.run([HOOK, sub, "--help"], capture_output=True, text=True)
        assert res.returncode == 0 and "--timeout-ms" in res.stdout


def test_hook_import_stays_light():
    code = ("import sys; import openjev_mcp.hook; "
            "bad = [m for m in ('mcp', 'httpx', 'openjev_mcp.http', 'openjev_mcp.tools') if m in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)")
    assert subprocess.run([os.path.join(stubs.REPO, ".venv", "bin", "python"), "-c", code]).returncode == 0
