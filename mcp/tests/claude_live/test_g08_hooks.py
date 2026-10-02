"""g08 hooks: PreToolUse, Stop, UserPromptSubmit, PostToolUse (T073-T082). Hooks run .venv/bin/openjev-hook against :8080 via cl_hooks."""
import shutil
import subprocess

import pytest

import cl_assert as A
import cl_hooks as H
from cl_cases import case_from_data, run_case
from cl_env import CASES_DIR, FIXTURES, REPO
from openjev_mcp import claude_hooks as ch

DATA = CASES_DIR / "g08_hooks.json"
CC = REPO / "mcp" / "tests" / "fixtures" / "claude_code"
UP_PROMPT = "merge these three PDF files into one and drop page 2"


def _git(ctx):
    subprocess.run(["git", "init", "-q"], cwd=ctx.cwd, check=True)


def _read_setup(ctx):
    (ctx.cwd / "note.txt").write_text("hello from the note\n", encoding="utf-8")


def _inj_setup(ctx):
    shutil.copy(FIXTURES / "injection_note.txt", ctx.cwd / "injection_note.txt")


def _bash(t):
    (c,) = A.tool_called(t, "Bash", times=1)
    return c


def _t073(t, ctx):
    H.hook_decision(t, "PreToolUse", "allow", reason="allow rule")
    c = _bash(t)
    assert c.result is not None and not c.result.is_error, f"Bash did not run: {c.result}"   # ran though not allowed (F9)


def _t074(t, ctx):
    H.hook_decision(t, "PreToolUse", "deny")
    c = _bash(t)
    assert c.result.is_error and "openjev command_gate" in c.result.text, c.result.text
    A.denied(t, "Bash")


def _t075(t, ctx):
    line = H.hook_decision(t, "PreToolUse", "deny", reason=lambda r: "deny rule" in r and "base64" in r)
    assert line["hookSpecificOutput"]["permissionDecisionReason"].startswith("openjev command_gate:")
    c = _bash(t)
    assert c.result.is_error and "openjev command_gate" in c.result.text, c.result.text
    A.no_read(t)


def _t076(t, ctx):
    H.hook_silent(t, "PreToolUse")
    assert any(p.get("tool_name") == "Read" for p in H.payloads(t, "PreToolUse"))
    (c,) = A.tool_called(t, "Read", times=1)
    assert c.result is not None and not c.result.is_error and "hello from the note" in c.result.text, c.result


def _t077(t, ctx):
    H.hook_silent(t, "PreToolUse")                      # defer applies to rule allows too
    c = _bash(t)
    A.denied(t, "Bash")                                 # dontAsk decides (contrast T073)
    assert c.result is None or c.result.is_error, c.result


def _t078(t, ctx):
    (p, *_) = H.hook_ran(t, "PreToolUse")
    ev = ch.parse_pretooluse(p)
    assert ev.tool_name == "Bash" and ev.command == "pwd", ev
    assert ev.cwd and ev.cwd.rstrip("/").endswith(ctx.cwd.name) and ev.cwd == str(ctx.cwd) or ev.cwd == str(ctx.cwd.resolve()), (ev.cwd, ctx.cwd)
    from pathlib import Path
    assert ev.transcript_path and Path(ev.transcript_path).exists(), ev.transcript_path
    got = ch.last_user_prompt(ev.transcript_path)
    assert got and "Run `pwd` with the Bash tool" in got, got


def _t079(t, ctx):
    lines = [l for l in H.hook_lines(t, "Stop") if l.get("decision") == "block"]
    assert len(lines) == 1, f"expected one Stop block, saw {H.hook_lines(t, 'Stop')}"
    assert ch.BLOCK_MARKER in lines[0]["reason"] and "[openjev done_gate]" in lines[0]["reason"]
    assert t.num_turns >= 2, t.num_turns
    stops = H.payloads(t, "Stop")
    assert len(stops) >= 2 and stops[-1].get("stop_hook_active") is True, [s.get("stop_hook_active") for s in stops]
    assert H.hook_lines(t, "Stop")[-1].get("decision") != "block" or len(lines) == 1   # the later stop is allowed (--max-blocks 1)


def _t080(t, ctx):
    H.hook_ran(t, "Stop")
    blocks = [l for l in H.hook_lines(t, "Stop") if l.get("decision") == "block"]
    assert not blocks, blocks
    _bash(t)


def _t081(t, ctx):
    (p, *_) = H.hook_ran(t, "UserPromptSubmit")
    assert p.get("prompt") == UP_PROMPT, p
    line = H.hook_decision(t, "UserPromptSubmit")
    assert "pdf" in line["hookSpecificOutput"]["additionalContext"]


def _t082(t, ctx):
    (p, *_) = H.hook_ran(t, "PostToolUse")
    assert p.get("tool_name") == "Read", p
    H.hook_decision(t, "PostToolUse", "block")


def _roster_hooks():
    return {"UserPromptSubmit": {"args": f"userprompt --roster {CC / 'skill_roster.json'} --timeout-ms 8000"}}


CASES = [
    case_from_data(DATA, "T073", expect=(_t073,), setup=_git),
    case_from_data(DATA, "T074", expect=(_t074,), setup=_git),
    case_from_data(DATA, "T075", expect=(_t075,), setup=_git),
    case_from_data(DATA, "T076", expect=(_t076,), setup=lambda c: (_git(c), _read_setup(c))),
    case_from_data(DATA, "T077", expect=(_t077,), setup=_git),
    case_from_data(DATA, "T078", expect=(_t078,), setup=_git),
    case_from_data(DATA, "T079", expect=(_t079,), setup=_git),
    case_from_data(DATA, "T080", expect=(_t080,), setup=_git),
    case_from_data(DATA, "T081", expect=(_t081,), setup=_git, hooks=_roster_hooks()),
    case_from_data(DATA, "T082", expect=(_t082,), setup=lambda c: (_git(c), _inj_setup(c))),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.id}-{c.slug}")
def test_case(case, case_ctx):
    run_case(case, case_ctx)
