"""The `openjev` CLI: `check` runs a recipe, `filter` keeps the stdin lines that meet a criterion (spec 2.20).
Exit codes. check: 0 = the recipe's least severe decision, 1 = any other, 2 = error or degraded.
filter: 1 = something kept, 0 = nothing kept, 2 = error. Imports of httpx and the tools are lazy."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import anyio

from openjev_mcp import __version__

OK, HIT, ERR = 0, 1, 2


def _fail(msg: str) -> int:
    print(f"openjev: {msg}", file=sys.stderr)
    return ERR


def _inputs(path: str | None) -> dict:
    if not path:
        return {}
    raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("inputs must be a JSON object")
    return data


async def _check(args, inputs: dict, transport) -> int:
    from openjev_mcp.config import load_config
    from openjev_mcp.http import OpenJevClient
    from openjev_mcp.recipes.engine import load_builtin, load_recipe, run_recipe
    if os.path.isfile(args.recipe):
        with open(args.recipe, encoding="utf-8") as f:
            recipe = load_recipe(json.load(f))
    else:
        recipe = load_builtin(args.recipe)
    if args.unattended:
        inputs = {**inputs, "unattended": True}
    config = load_config()
    client = OpenJevClient(config, transport=transport)
    try:
        out = await run_recipe(recipe, inputs, client=client, config=config, profile=args.profile)
    finally:
        await client.aclose()
    print(json.dumps(out.to_dict(), indent=2, ensure_ascii=False))
    if out.degraded:
        return ERR
    return OK if out.decision == recipe.decisions[0] else HIT


async def _filter(args, lines: list[str], transport) -> int:
    from openjev_mcp.config import load_config
    from openjev_mcp.http import OpenJevClient
    from openjev_mcp.limits import LimitsCache
    from openjev_mcp.progress import ProgressEmitter
    from openjev_mcp.tools import ToolContext
    from openjev_mcp.tools import filter as ft
    items = [{"id": f"L{i}", "text": t} for i, t in enumerate(lines, 1)]
    crit = args.criterion if "{id}" in args.criterion else f"Line {{id}}: {args.criterion}"
    spec: dict[str, Any] = {"task": args.task, "items": items, "criterion": crit, "keep_at": args.gt,
                            "drop_at": min(0.2, args.gt / 2), "grey": "drop", "items_label": "LINES"}
    config = load_config()
    client = OpenJevClient(config, transport=transport)
    try:
        ctx = ToolContext(config, client, LimitsCache(client), ProgressEmitter.noop(), None)
        res = await ft.run_filter(ctx, spec)
    finally:
        await client.aclose()
    kept = set(res["kept"])
    for it in items:
        if it["id"] in kept:
            sys.stdout.write(it["text"] + "\n")
    return HIT if kept else OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openjev", description="OpenJev MCP companion CLI")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("version", help="print the version and exit")
    chk = sub.add_parser("check", help="run a recipe on inputs and print its JSON (exit 0 least severe, 1 other, 2 error)")
    chk.add_argument("recipe", help="a built-in recipe id, or a recipe .json file")
    chk.add_argument("--inputs", help="inputs JSON file, or - for stdin")
    chk.add_argument("--profile", help="the recipe's question/policy profile")
    chk.add_argument("--unattended", action="store_true", help="no human sees a fallback")
    flt = sub.add_parser("filter", help="keep the stdin lines meeting a criterion (exit 1 if any kept, 0 none, 2 error)")
    flt.add_argument("criterion", help="the question; {id} is the line id (L1..), added if absent")
    flt.add_argument("--gt", type=float, default=0.7, help="keep lines with p above this (default 0.7)")
    flt.add_argument("--task", default="select the lines that meet the criterion")
    return parser


def main(argv: list[str] | None = None, *, transport=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.version or args.command == "version":
        print(f"openjev-mcp {__version__}")
        return OK
    if args.command not in ("check", "filter"):
        parser.print_help()
        return OK
    try:
        if args.command == "check":
            return anyio.run(_check, args, _inputs(args.inputs), transport)
        lines = [ln for ln in sys.stdin.read().splitlines() if ln.strip()]
        return anyio.run(_filter, args, lines, transport) if lines else OK
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt):
            raise
        msg = getattr(e, "message", None) or str(e)
        return _fail(f"{getattr(e, 'code', type(e).__name__)}: {msg}")


if __name__ == "__main__":
    sys.exit(main())
