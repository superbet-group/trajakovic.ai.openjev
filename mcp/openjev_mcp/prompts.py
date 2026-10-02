"""MCP prompts registry (spec 2.18): PROMPTS is filled by later packages; get() validates arguments."""
from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from openjev_mcp.tools import ToolContext


@dataclass(frozen=True)
class PromptArg:
    name: str
    description: str
    required: bool = False
    complete: bool = False          # a completer is registered for (prompt, name)


@dataclass(frozen=True)
class PromptSpec:
    name: str
    title: str
    description: str
    arguments: tuple[PromptArg, ...]
    build: Callable[[dict, ToolContext], Awaitable[list[dict]]]   # user-role PromptMessage dicts


class PromptError(ValueError):
    """Unknown prompt, missing required or unknown argument (-32602)."""


PROMPTS: list[PromptSpec] = []


def _ensure() -> None:
    """Register the phase-2/3 prompts once (lazy: batch_prompts imports this module, so a top-level import would cycle)."""
    from openjev_mcp import batch_prompts
    from openjev_mcp.tools import calibrate, compile as compile_tool
    for p in (batch_prompts.START_BATCH, batch_prompts.REVIEW_BATCH, compile_tool.AUTHOR_QUESTION,
              calibrate.AUDIT_QUESTION, calibrate.EXPLAIN_ANSWER):
        if p not in PROMPTS:
            PROMPTS.append(p)


def listing() -> list[dict]:
    _ensure()
    return [{"name": p.name, "title": p.title, "description": p.description,
             "arguments": [{"name": a.name, "description": a.description, "required": a.required}
                           for a in p.arguments]} for p in PROMPTS]


async def get(name: str, args: dict | None, ctx: ToolContext) -> dict:
    _ensure()
    spec = next((p for p in PROMPTS if p.name == name), None)
    if spec is None:
        raise PromptError(f"Unknown prompt: {name}")
    args = dict(args or {})
    known = {a.name for a in spec.arguments}
    if extra := sorted(set(args) - known):
        raise PromptError(f"Unknown argument: {', '.join(extra)}")
    if missing := [a.name for a in spec.arguments if a.required and args.get(a.name) in (None, "")]:
        raise PromptError(f"Missing required argument: {', '.join(missing)}")
    return {"description": spec.description, "messages": await spec.build(args, ctx)}


if not {"openjev_mcp.batch_prompts", "openjev_mcp.tools.calibrate", "openjev_mcp.tools.compile"} & set(sys.modules):
    _ensure()   # eager unless a prompt-defining module is itself mid-import (then _ensure runs lazily)
