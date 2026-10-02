"""Hook settings, the skills plugin dir and hook assertions (H2). Records: <run_dir>/hook_<Event>.in/.out, newline-terminated."""
from __future__ import annotations

import json
import math
import re
import shlex
from pathlib import Path

import cl_assert as A
import cl_env

EVENTS = ("PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit")
HOOK_BIN = cl_env.REPO / ".venv" / "bin" / "openjev-hook"
SKILLS_SRC = cl_env.REPO / "mcp" / "skills"
DEFAULT_TIMEOUT_S = 15


def _spec(v) -> dict:
    """Accept {"matcher","args","env"} or the tuple form (matcher, args[, env]); UserPromptSubmit may give (args,) / a str."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        return {"args": v}
    v = tuple(v)
    if len(v) == 1:
        return {"args": v[0]}
    return {"matcher": v[0], "args": v[1], **({"env": v[2]} if len(v) > 2 else {})}


def timeout_s(args: str) -> int:
    """`timeout` >= --timeout-ms + 1000 ms, rounded up to whole seconds (spec 1 Hook and recipe)."""
    m = re.search(r"--timeout-ms[ =](\d+)", args)
    return math.ceil((int(m[1]) + 1000) / 1000) if m else DEFAULT_TIMEOUT_S


def command(event: str, spec: dict, ctx) -> str:
    """tee -a .in | env .. openjev-hook <args> | tee -a .out, then a newline per record; the whole thing exits 0, hook stdout unchanged."""
    d = Path(ctx.run_dir)
    fin, fout = shlex.quote(str(d / f"hook_{event}.in")), shlex.quote(str(d / f"hook_{event}.out"))
    env = {"OPENJEV_BASE_URL": ctx.base_url, **(spec.get("env") or {})}
    envs = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items())
    return (f"( tee -a {fin} | env {envs} {shlex.quote(str(HOOK_BIN))} {spec.get('args', '')} | tee -a {fout} ); "
            f"echo >> {fin}; echo >> {fout}; exit 0")


def settings_for(case, ctx) -> Path:
    """Write <run_dir>/settings.json from case.hooks and return its path."""
    hooks = {}
    for event, v in (case.hooks or {}).items():
        if event not in EVENTS:
            raise ValueError(f"unknown hook event {event!r}; known: {EVENTS}")
        s = _spec(v)
        entry = {"hooks": [{"type": "command", "command": command(event, s, ctx), "timeout": timeout_s(s.get("args", ""))}]}
        if event != "UserPromptSubmit":
            entry = {"matcher": s.get("matcher", "*"), **entry}
        hooks[event] = [entry]
    p = Path(ctx.run_dir) / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"hooks": hooks}, indent=1), encoding="utf-8")
    return p


def plugin_dir(ctx) -> Path:
    """<work>/plugin/openjev-skills with a skills/<name> symlink per mcp/skills/* (F14); built once per session."""
    root = Path(ctx.work) / "plugin" / "openjev-skills"
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    pj = root / ".claude-plugin" / "plugin.json"
    if not pj.exists():
        pj.write_text(json.dumps({"name": "openjev-skills", "version": "0.0.0"}), encoding="utf-8")
    for src in sorted(p for p in SKILLS_SRC.iterdir() if p.is_dir()):
        link = root / "skills" / src.name
        if not link.is_symlink():
            link.symlink_to(src.resolve())
    return root


def skill_names() -> list[str]:
    return sorted(p.name for p in SKILLS_SRC.iterdir() if p.is_dir())


# ---- assertions ---------------------------------------------------------------------------------------------------------

def hook_lines(t, event: str) -> list[dict]:
    """Parsed .out lines of the event (non-JSON lines as {"_raw": line})."""
    return list((t.hooks or {}).get(event, []))


def payloads(t, event: str) -> list[dict]:
    """Parsed .in payloads (what Claude Code sent the hook)."""
    return list((t.hooks_in or {}).get(event, []))


def hook_ran(t, event: str) -> list[dict]:
    p = payloads(t, event)
    if not p:
        A.fail(t, f"hook {event} did not run (no payload in hook_{event}.in)")
    return p


def hook_silent(t, event: str) -> None:
    """The hook ran and printed nothing."""
    hook_ran(t, event)
    if out := hook_lines(t, event):
        A.fail(t, f"hook {event} was expected silent, printed: {out}")


def _decision_of(event: str, line: dict) -> tuple[str | None, str | None]:
    h = line.get("hookSpecificOutput") if isinstance(line.get("hookSpecificOutput"), dict) else {}
    if event == "PreToolUse":
        return h.get("permissionDecision"), h.get("permissionDecisionReason")
    if event in ("Stop", "PostToolUse"):
        return line.get("decision"), line.get("reason")
    if event == "UserPromptSubmit":
        c = h.get("additionalContext")
        return ("context" if c else None), c
    return None, None


def hook_decision(t, event: str, decision: str | None = None, reason=None) -> dict:
    """First .out line of the event with a decision (PreToolUse permissionDecision, Stop/PostToolUse decision,
    UserPromptSubmit additionalContext -> "context"); `decision`/`reason` filter (reason: substring or predicate)."""
    hook_ran(t, event)
    seen = []
    for line in hook_lines(t, event):
        d, r = _decision_of(event, line)
        seen.append((d, r))
        if d is None or (decision is not None and d != decision):
            continue
        if reason is not None and not (reason(r) if callable(reason) else reason in (r or "")):
            continue
        return line
    A.fail(t, f"hook {event}: no line with decision={decision!r} reason={reason!r}; saw {seen}")
