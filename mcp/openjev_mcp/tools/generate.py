"""generate: OpenAI-style chat passthrough on the local model, not for decisions (spec 2.17). The MCP layer guards
the MLX bugs: role required locally (12.2), an empty reply retried once (12.4), max_tokens clamped, MLX warnings."""
from __future__ import annotations

import re
from typing import Any

from openjev_mcp import schemas
from openjev_mcp.config import Config
from openjev_mcp.errors import invalid_input
from openjev_mcp.lint import Finding
from openjev_mcp.tools import ToolContext, ToolSpec

MAX_TOKENS = 8192
DEFAULT_TOKENS = 512
ROLES = ("system", "user", "assistant")
MLX_WARNING = "MLX backend: newlines are dropped from replies; tools, logprobs and image parts are ignored"

INPUT = {
    "type": "object", "additionalProperties": False, "required": ["messages"],
    "properties": {
        "messages": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["role", "content"],
            "properties": {"role": {"enum": list(ROLES)}, "content": {"type": "string"}}}},
        "max_tokens": {"type": "integer", "minimum": 1, "maximum": MAX_TOKENS, "default": DEFAULT_TOKENS},
        "response_format": {"type": "object"},
        "stop": {"type": "array", "items": {"type": "string"}},
        "model": {"type": "string"},
    },
}
OUTPUT = {
    "type": "object", "required": ["content", "finish_reason"],
    "properties": {
        "content": {"type": "string"}, "finish_reason": {"enum": ["stop", "length"]}, "usage": {"type": "object"},
        "retried": {"type": "boolean"}, "warnings": {"type": "array"},
    },
}


def _multi_token(stop: str) -> bool:
    # deviation: spec 2.17: no tokenizer here, so "longer than one token" is approximated as more than 3
    # characters or a space inside the text; the flag is advice, the stop string is passed either way
    return len(stop) > 3 or bool(re.search(r"\S\s+\S", stop))


def prepare(args: dict) -> tuple[dict, list[Finding]]:
    """Clamp max_tokens before validation (the schema caps at 8192; the server would clamp silently)."""
    n = args.get("max_tokens")
    if isinstance(n, int) and not isinstance(n, bool) and n > MAX_TOKENS:
        # deviation: W701 is new in P16 (spec 2.17 names the clamp but no lint code)
        return {**args, "max_tokens": MAX_TOKENS}, [Finding("W701", "max_tokens", f"{n} clamped to {MAX_TOKENS}",
                                                           f"the server clamps above {MAX_TOKENS} silently")]
    return args, []


def _completion(data: Any) -> tuple[str, str, dict, int | None]:
    choice = (data.get("choices") or [{}])[0] if isinstance(data, dict) else {}
    message = choice.get("message") if isinstance(choice, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    usage = data.get("usage") if isinstance(data, dict) and isinstance(data.get("usage"), dict) else {}
    finish = choice.get("finish_reason") if isinstance(choice, dict) else None
    return (content if isinstance(content, str) else "", "length" if finish == "length" else "stop", usage,
            usage.get("completion_tokens"))


async def generate(ctx: ToolContext, args: dict) -> dict:
    messages = args["messages"]
    for i, m in enumerate(messages):   # a missing role is a server 500 (bug 12.2): refuse before any request
        if not isinstance(m, dict) or m.get("role") not in ROLES:
            raise invalid_input(f"messages[{i}].role", f"messages[{i}].role is required: one of {', '.join(ROLES)}",
                                "the server answers 500 to a message without role")
        if not isinstance(m.get("content"), str):
            raise invalid_input(f"messages[{i}].content", f"messages[{i}].content must be a string")
    warnings: list[str] = []
    max_tokens = args.get("max_tokens", DEFAULT_TOKENS)
    if max_tokens > MAX_TOKENS:
        max_tokens = MAX_TOKENS
    body: dict[str, Any] = {"model": args.get("model") or ctx.config.chat_model, "max_tokens": max_tokens,
                            "messages": [{"role": m["role"], "content": m["content"]} for m in messages]}
    # deviation: spec 2.17: stream:false is not sent, the server default is non-streaming and the captured
    # ex-generate request has no stream key
    if args.get("response_format"):
        body["response_format"] = args["response_format"]
    if args.get("stop"):
        body["stop"] = list(args["stop"])
        long = [s for s in args["stop"] if _multi_token(s)]
        if long:
            warnings.append("stop strings longer than one token are unreliable: " + ", ".join(map(repr, long)))
    limits = await ctx.limits.get()
    if limits.backend in ("mlx", "unknown"):
        warnings.append(MLX_WARNING)
    timeout = max(ctx.config.timeout_ms, 60000)
    res = await ctx.client.chat(body, timeout_ms=timeout)
    content, finish, usage, tokens = _completion(res.data)
    retried = False
    if content == "" and tokens == 0:   # bug 12.4: an empty reply with no completion tokens, retry once
        retried = True
        res = await ctx.client.chat(body, timeout_ms=timeout)
        content, finish, usage, tokens = _completion(res.data)
        if content == "":
            warnings.append("the reply was empty after one retry")
    out: dict[str, Any] = {"content": content, "finish_reason": finish, "usage": usage, "retried": retried,
                           "warnings": warnings + [f.line() for f in ctx.warnings]}
    return out


def register(config: Config) -> ToolSpec:
    schemas.register_tool_schemas("generate", INPUT, OUTPUT)
    annotations = {"title": "Generate text", "readOnlyHint": True, "idempotentHint": False, "openWorldHint": False}
    return ToolSpec(
        "generate", "Generate text",
        "Draft short text on the local chat model (OpenAI-style passthrough). NOT for decisions: use ask, yes_no, "
        "classify or score for those. Guards the MLX bugs: role is required, an empty reply is retried once, "
        "max_tokens is clamped to 8192; on MLX newlines are dropped from replies.",
        schemas.INPUT_SCHEMAS["generate"], schemas.OUTPUT_SCHEMAS["generate"], annotations, generate, prepare)
