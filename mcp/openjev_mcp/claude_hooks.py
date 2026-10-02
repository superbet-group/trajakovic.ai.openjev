"""Claude Code hook payloads (hooks reference, major version 2): the only module that knows its field names."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

SUPPORTED_CLAUDE_CODE = ("2",)          # major versions with a recorded fixture in tests
DECISIONS = ("allow", "ask", "deny")


class HookInputError(ValueError): ...


BAD_JSON = object()                    # stands for a stdin that was not JSON; every parse_* rejects it


@dataclass(frozen=True)
class PreToolUseEvent:
    tool_name: str
    command: str | None
    cwd: str | None
    session_id: str | None
    transcript_path: str | None
    permission_mode: str | None
    raw: dict


def _opt(payload: dict, key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def parse_pretooluse(payload: Any) -> PreToolUseEvent:
    if not isinstance(payload, dict):
        raise HookInputError("hook input must be a JSON object")
    name = payload.get("hook_event_name")
    if name is not None and name != "PreToolUse":
        raise HookInputError(f"hook_event_name is {name!r}, expected 'PreToolUse'")
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or not tool:
        raise HookInputError("tool_name is missing")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        raise HookInputError("tool_input is missing or not an object")
    command = tool_input.get("command") if tool == "Bash" else None
    return PreToolUseEvent(tool_name=tool, command=command if isinstance(command, str) else None,
                           cwd=_opt(payload, "cwd"), session_id=_opt(payload, "session_id"),
                           transcript_path=_opt(payload, "transcript_path"),
                           permission_mode=_opt(payload, "permission_mode"), raw=payload)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p["text"] for p in content
                         if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str))
    return ""


def last_user_prompt(transcript_path: str | None, *, max_bytes: int = 262144) -> str | None:
    try:
        if not transcript_path:
            return None
        with open(transcript_path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - max_bytes))
            data = f.read()
        lines = data.split(b"\n")
        if size > max_bytes:
            lines = lines[1:]                     # the first line is cut mid-way
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            msg = entry.get("message")
            msg = msg if isinstance(msg, dict) else {}
            if entry.get("type") != "user" and msg.get("role") != "user":
                continue
            text = _text(msg.get("content", entry.get("content"))).strip()
            if text:
                return text
    except Exception:
        return None
    return None


def pretooluse_output(decision: str, reason: str) -> dict:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


# ---- Stop, UserPromptSubmit, PostToolUse (hooks reference, major version 2) ----

BLOCK_MARKER = "[openjev done_gate]"     # in every block reason; the transcript echoes it back, which counts blocks
MAX_TIMELINE = 60


@dataclass(frozen=True)
class StopEvent:
    active: bool                          # stop_hook_active: Claude is already continuing because a Stop hook blocked
    transcript_path: str | None
    last_message: str | None
    cwd: str | None
    raw: dict


@dataclass(frozen=True)
class PromptEvent:
    prompt: str
    transcript_path: str | None
    cwd: str | None
    raw: dict


@dataclass(frozen=True)
class PostToolEvent:
    tool_name: str
    tool_input: dict
    response: Any
    transcript_path: str | None
    cwd: str | None
    raw: dict


def _event(payload: Any, expected: str) -> dict:
    if not isinstance(payload, dict):
        raise HookInputError("hook input must be a JSON object")
    name = payload.get("hook_event_name")
    if name is not None and name != expected:
        raise HookInputError(f"hook_event_name is {name!r}, expected {expected!r}")
    return payload


def parse_stop(payload: Any) -> StopEvent:
    p = _event(payload, "Stop")
    return StopEvent(active=p.get("stop_hook_active") is True, transcript_path=_opt(p, "transcript_path"),
                     last_message=_opt(p, "last_assistant_message"), cwd=_opt(p, "cwd"), raw=p)


def parse_userprompt(payload: Any) -> PromptEvent:
    p = _event(payload, "UserPromptSubmit")
    # deviation: hooks reference: the prompt field is "prompt" (older docs) or "prompt_text" (current docs); both read
    prompt = p.get("prompt") if isinstance(p.get("prompt"), str) else p.get("prompt_text")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HookInputError("prompt is missing or empty")
    return PromptEvent(prompt=prompt, transcript_path=_opt(p, "transcript_path"), cwd=_opt(p, "cwd"), raw=p)


def parse_posttooluse(payload: Any) -> PostToolEvent:
    p = _event(payload, "PostToolUse")
    tool = p.get("tool_name")
    if not isinstance(tool, str) or not tool:
        raise HookInputError("tool_name is missing")
    tin = p.get("tool_input")
    return PostToolEvent(tool_name=tool, tool_input=tin if isinstance(tin, dict) else {},
                         response=p.get("tool_response"), transcript_path=_opt(p, "transcript_path"),
                         cwd=_opt(p, "cwd"), raw=p)


def response_text(resp: Any, *, limit: int = 20000) -> str:
    """The text of a tool_response: a string, content blocks, or an object (its text-like fields, else its JSON)."""
    def walk(x: Any) -> str:
        if isinstance(x, str):
            return x
        if isinstance(x, list):
            return "\n".join(t for t in (walk(i) for i in x) if t)
        if isinstance(x, dict):
            if isinstance(x.get("text"), str):
                return x["text"]
            for k in ("result", "content", "output", "stdout"):
                if k in x:
                    return walk(x[k])
            return json.dumps(x, ensure_ascii=False)
        return "" if x is None else str(x)
    return walk(resp).strip()[:limit]


def response_source(ev: PostToolEvent) -> str:
    target = next((ev.tool_input[k] for k in ("url", "uri", "path", "file_path", "query") if isinstance(ev.tool_input.get(k), str)), "")
    return f"{ev.tool_name} result" + (f": {target}" if target else "")


@dataclass(frozen=True)
class TurnReport:
    task: str | None
    timeline: tuple[str, ...]
    final_message: str | None
    prior_blocks: int


def _squash(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _step(name: str, tin: Any) -> str:
    tin = tin if isinstance(tin, dict) else {}
    for k in ("command", "file_path", "path", "url", "pattern", "query"):
        if isinstance(tin.get(k), str):
            return f"{name}: {_squash(tin[k], 160)}"
    return name


def stop_turn(transcript_path: str | None, *, max_bytes: int = 524288) -> TurnReport | None:
    """The last turn of a transcript (everything after the last real user prompt) as a done_gate report; None when unreadable."""
    try:
        if not transcript_path:
            return None
        with open(transcript_path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - max_bytes))
            lines = f.read().split(b"\n")
        if size > max_bytes:
            lines = lines[1:]
        entries = []
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                entries.append(e)
        marked = [BLOCK_MARKER in json.dumps(e) and e.get("type") != "assistant" for e in entries]
        start, task = 0, None
        for i in range(len(entries) - 1, -1, -1):
            msg = entries[i].get("message")
            msg = msg if isinstance(msg, dict) else {}
            if marked[i] or (entries[i].get("type") != "user" and msg.get("role") != "user"):
                continue
            text = _text(msg.get("content", entries[i].get("content"))).strip()
            if text:
                start, task = i + 1, text
                break
        steps: list[str] = []
        pending: dict[str, int] = {}
        final = None
        for e in entries[start:]:
            msg = e.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use":
                    steps.append(f"{len(steps) + 1}. {_step(str(part.get('name', 'tool')), part.get('input'))}")
                    pending[str(part.get("id"))] = len(steps) - 1
                elif part.get("type") == "tool_result" and str(part.get("tool_use_id")) in pending:
                    k = pending.pop(str(part.get("tool_use_id")))
                    out = _squash(_text(part.get("content")), 120)
                    steps[k] += (" -> ERROR " if part.get("is_error") else " -> ") + out
                elif part.get("type") == "text" and e.get("type") == "assistant" and part.get("text", "").strip():
                    final = part["text"].strip()
        prior = sum(1 for m in marked[start:] if m)
        return TurnReport(task, tuple(steps[-MAX_TIMELINE:]), final, prior)
    except Exception:
        return None


def stop_output(reason: str) -> dict:
    return {"decision": "block", "reason": reason}


def system_message_output(message: str) -> dict:
    return {"systemMessage": message}


def userprompt_output(context: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}


def posttooluse_output(context: str, *, block_reason: str | None = None) -> dict:
    out: dict = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}
    if block_reason is not None:
        out = {"decision": "block", "reason": block_reason, **out}
    return out
