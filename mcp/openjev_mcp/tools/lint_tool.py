"""The lint tool (spec 2.13): the pure linter over a request, no I/O."""
from __future__ import annotations

from openjev_mcp import wire
from openjev_mcp.errors import invalid_input
from openjev_mcp.lint import lint_request, snippets
from openjev_mcp.tools import ToolContext

HINT = "request: a full /v1/systemone body; or questions with optional state, options, images"


async def lint(ctx: ToolContext, args: dict) -> dict:
    request, questions = args.get("request"), args.get("questions")
    if request is not None and questions is not None:
        raise invalid_input("request", "give either request or questions, not both", HINT)
    if request is None and questions is None:
        raise invalid_input("arguments", "give request, or questions", HINT)
    extras: dict = {}
    request_form = request is not None
    if request is None:
        options = dict(args.get("options") or {})
        model = options.pop("model", None) or ctx.config.model
        extras = {k: v for k, v in options.items() if k not in wire.OPTION_ORDER and k != "timeout_ms"}
        request = {**wire.build_body(model, args.get("state"), questions, options, args.get("images")), **extras}
        if args.get("state") is None:
            del request["state"]
    report = lint_request(request, limits=ctx.limits.peek(), profile=args.get("profile", "default"),
                          autofix=args.get("autofix", True),
                          require_state=request_form)
    out: dict = {"valid": report.valid, "errors": [f.to_dict() for f in report.errors],
                 "warnings": [f.to_dict() for f in report.warnings]}
    if report.fixed_request is not None:
        out["fixed_request"] = report.fixed_request
    out["estimate"] = report.estimate
    emit = args.get("emit")
    if emit:
        body = {k: v for k, v in (report.fixed_request or request).items() if k not in extras}
        built = snippets(body, ctx.config.base_url)
        out["snippets"] = {k: built[k] for k in ("body", "curl", "python") if k in emit}
        out["body_hash"] = wire.body_hash(wire.body_bytes(body))
    return out
