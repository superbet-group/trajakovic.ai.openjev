"""status: health and capability probe (spec 2.17)."""
from __future__ import annotations

from openjev_mcp import PROTOCOL_VERSIONS, __version__, wire
from openjev_mcp.errors import ToolError
from openjev_mcp.limits import ALIASES_ACCEPTED, BATCH_CAPS, CHAT_MODELS, default_limits
from openjev_mcp.recipes.registry import load_all
from openjev_mcp.tools import ToolContext

LOGS_BODIES_WARNING = ("the OpenJev server logs full request bodies (debug logging, 00-api-surface 12.6): "
                       "states and questions reach its log")
PROBE_QUESTION = {"q": {"type": "noul", "instructions": "Is this a status probe?",
                        "criteria": {"true": "a connectivity check", "false": "anything else"}}}


async def _probe(ctx: ToolContext) -> tuple[int | None, str | None, str | None]:
    """(latency_ms, resolved model, warning); a failed probe degrades, health stays true."""
    body = wire.build_body(ctx.config.model, "status probe", PROBE_QUESTION, {"samples": 1})
    try:
        res = await ctx.client.systemone(body, timeout_ms=ctx.config.timeout_ms)
    except ToolError as err:
        return None, None, f"probe failed ({err.code}): {err.message}"
    model = res.data.get("model") if isinstance(res.data, dict) else None
    return round(res.latency_ms), model if isinstance(model, str) else None, None


async def status(ctx: ToolContext, args: dict) -> dict:
    cfg = ctx.config
    await ctx.client.get("/health")
    warnings: list[str] = []
    auth_refused = False
    try:
        limits = await ctx.limits.refresh()
    except ToolError as err:
        if err.code not in ("OJ_AUTH", "OJ_FORBIDDEN"):
            raise
        auth_refused = True
        limits = default_limits([])
        warnings.append(f"limits unreadable ({err.code}): {err.message}" + (f". {err.hint}" if err.hint else ""))
    known = limits.known_models or ()
    decide = [n for n in known if n not in CHAT_MODELS]
    warnings += limits.warnings
    out: dict = {
        "healthy": True, "base_url": cfg.base_url, "decide_models": decide,
        "chat_models": [n for n in known if n in CHAT_MODELS], "aliases_accepted": list(ALIASES_ACCEPTED),
        "auth": "unknown" if auth_refused or any("(OJ_AUTH)" in w for w in warnings) else "bearer" if cfg.api_key else "none",
        "backend": limits.backend, "limit_source": limits.source,
        "limits": {**limits.values, "batch": {**BATCH_CAPS, "max_inflight_batch": cfg.max_inflight_batch}},
        "capabilities": {n: limits.capabilities(n) for n in decide},
        "latency_probe_ms": None,
        "mcp": {"server_version": __version__, "protocol_versions": list(PROTOCOL_VERSIONS),
                "batch_max_inflight": cfg.max_inflight_batch, "transport": cfg.transport},
    }
    if args.get("probe"):
        ms, model, warning = await _probe(ctx)
        out["latency_probe_ms"] = ms
        if model:
            out["resolved"] = {cfg.model: model}
        if warning:
            warnings.append(warning)
    warnings += load_all(cfg).warnings   # extra recipe files that failed to load
    if limits.logs_bodies:
        warnings.append(LOGS_BODIES_WARNING)
    out["warnings"] = warnings
    return out
