"""Live hook/plugin smoke: two cheap `claude -p` runs (not catalogue cases, D12).

    OPENJEV_CLAUDE_LIVE=1 .venv/bin/python -m pytest -q mcp/tests/claude_live/test_cl_smoke_hooks.py"""
import subprocess


import cl_hooks as H
from cl_claude import run_claude
from cl_cases import Case


def test_smoke_pretooluse(case_ctx):
    case = Case("SMOKEH1", "smoke_pretooluse", "g08", "pretooluse", "ARCHITECTURE 1.3", "", (), (), tools="Bash", persist=True,
                hooks={"PreToolUse": {"matcher": "Bash", "args": "pretooluse --unattended --timeout-ms 8000"}})
    ctx = case_ctx(case, 1)
    subprocess.run(["git", "init", "-q"], cwd=ctx.cwd, check=True)
    t = run_claude("Run `git status` with the Bash tool exactly once, then reply DONE.", ctx=ctx, tools="Bash", persist=True, max_turns=3,
                   settings=H.settings_for(case, ctx))
    assert t.init["apiKeySource"] == "none"
    (p, *_) = H.hook_ran(t, "PreToolUse")
    assert p["hook_event_name"] == "PreToolUse" and p["tool_name"] == "Bash" and "git status" in p["tool_input"]["command"]
    line = H.hook_decision(t, "PreToolUse")
    assert line["hookSpecificOutput"]["permissionDecision"] in ("allow", "deny", "ask")
    assert (t.run_dir / "hook_PreToolUse.in").read_text().endswith("\n")
    assert (t.run_dir / "hook_PreToolUse.out").read_text().endswith("\n")


def test_smoke_plugin_loads(case_ctx):
    case = Case("SMOKEH2", "smoke_plugin", "g09", "skills", "ARCHITECTURE 1.2", "", (), (), skills=True)
    ctx = case_ctx(case, 1)
    t = run_claude("Reply OK", ctx=ctx, skills=True, plugin_dir=H.plugin_dir(ctx), max_turns=1)
    assert t.init["apiKeySource"] == "none"
    have = {s for s in t.init.get("skills", []) if s.startswith("openjev-skills:")}
    assert have == {f"openjev-skills:{n}" for n in H.skill_names()} and len(have) == 11, sorted(t.init.get("skills", []))
