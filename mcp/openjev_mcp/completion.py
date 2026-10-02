"""completion/complete registry: prefix matches from COMPLETERS keyed (ref_type, ref, argument)."""
from __future__ import annotations

from collections.abc import Callable

from openjev_mcp import batch_prompts, library
from openjev_mcp.recipes import registry
from openjev_mcp.tools import ToolContext

MAX_VALUES = 100
COMPLETERS: dict[tuple[str, str, str], Callable[[str, ToolContext], list[str]]] = {
    **batch_prompts.COMPLETERS,
    ("ref/resource", "openjev://templates/{id}", "id"): lambda value, ctx: library.template_ids(),
    ("ref/resource", "openjev://recipes/{id}", "id"): lambda value, ctx: registry.load_all(ctx.config).ids(),
}


def complete(ref_type: str, ref: str, argument: str, value: str, ctx: ToolContext) -> dict:
    fn = COMPLETERS.get((ref_type, ref, argument))
    hits = [v for v in fn(value, ctx) if v.startswith(value)] if fn else []
    return {"values": hits[:MAX_VALUES], "total": len(hits), "hasMore": len(hits) > MAX_VALUES}
