"""filter: keep or drop many small items by one criterion, packed into one state per request (spec 2.10)."""
from __future__ import annotations

import json
from typing import Any

from openjev_mcp import schemas
from openjev_mcp.config import Config
from openjev_mcp.derive import Band
from openjev_mcp.errors import invalid_input
from openjev_mcp.tools import ToolContext, ToolSpec
from openjev_mcp.tools.read import run_read

ID_PATTERN = "^[A-Za-z0-9_.:-]{1,32}$"
RESERVED = ("exists", "best")
NONE_KEY = "none"
DEFAULT_LEVELS = ["irrelevant", "barely relevant", "somewhat relevant", "relevant", "highly relevant"]
EXISTS_TEXT = "Does any listed item meet the criterion?"
BEST_TEXT = "Which listed item meets the criterion best?"
NONE_TEXT = "none of the listed items meets the criterion"

INPUT = {
    "type": "object", "additionalProperties": False,
    "required": ["task", "items", "criterion"],
    "properties": {
        "task": {"type": "string", "description": "What the agent is doing; goes first in the state as 'TASK: ...'"},
        "items": {"type": "array", "minItems": 0, "maxItems": 5000,
                  "items": {"type": "object", "required": ["id", "text"],
                            "properties": {"id": {"type": "string", "pattern": ID_PATTERN}, "text": {"type": "string"}}}},
        "criterion": {"type": "string", "description": "Per-item question with {id}, e.g. 'Is log line {id} a real failure ...?'"},
        "true_means": {"type": "string"}, "false_means": {"type": "string"},
        "keep_at": {"type": "number", "default": 0.6}, "drop_at": {"type": "number", "default": 0.2},
        "grey": {"enum": ["keep", "drop", "review"], "default": "keep"},
        "pack_size": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
        "items_label": {"type": "string", "default": "ITEMS"},
        "pick_best": {"type": "object", "additionalProperties": False,
                      "properties": {"question": {"type": "string"}, "none_means": {"type": "string"}},
                      "description": "adds exists (noul) + best (choice over ids + none) per pack"},
        "graded": {"type": "object", "additionalProperties": False,
                   "properties": {"levels": {"type": "array", "minItems": 2, "maxItems": 10, "items": {"type": "string"}},
                                  "relevant_at": {"type": "number", "default": 2.5}},
                   "description": "score per item instead of noul, for ranking"},
        "options": {"$ref": "#/$defs/ReadOptions"},
    },
}
OUTPUT = {
    "type": "object", "required": ["kept", "dropped", "grey", "items", "meta"],
    "properties": {
        "kept": {"type": "array", "items": {"type": "string"}},
        "dropped": {"type": "array", "items": {"type": "string"}},
        "grey": {"type": "array", "items": {"type": "string"}},
        "items": {"type": "array", "items": {"type": "object", "required": ["id", "decision"], "properties": {
            "id": {"type": "string"}, "p": {"type": "number"}, "score": {"type": "number"},
            "decision": {"enum": ["keep", "drop", "grey"]}}}},
        "best": {"type": "object", "properties": {"id": {"type": ["string", "null"]}, "p": {"type": "number"},
                                                   "exists_p": {"type": "number"}}},
        "meta": {"$ref": "#/$defs/Meta"},
    },
}


def _esc(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)[1:-1]


def pack_state(task: str, items_label: str, items: list[dict]) -> str:
    """'TASK: <task>\\n<label>:\\n<id> <text>...'; task, label and texts are JSON-string-escaped so an item is
    exactly one line and its text cannot forge another item's line (spec 2.10, F3)."""
    lines = [f"TASK: {_esc(task)}", f"{_esc(items_label)}:"]
    lines += [f"{it['id']} {_esc(it['text'])}" for it in items]
    return "\n".join(lines)


def _questions(args: dict, pack: list[dict]) -> dict:
    crit = {k: args[f"{k}_means"] for k in ("true", "false") if args.get(f"{k}_means")}
    graded = args.get("graded")
    qs: dict[str, Any] = {}
    for it in pack:
        text = args["criterion"].replace("{id}", it["id"])
        if graded:
            qs[it["id"]] = {"type": "score", "instructions": text, "criteria": list(graded.get("levels") or DEFAULT_LEVELS)}
        else:
            qs[it["id"]] = {"type": "noul", "instructions": text, **({"criteria": dict(crit)} if crit else {})}
    best = args.get("pick_best")
    if best is not None:
        qs["exists"] = {"type": "noul", "instructions": best.get("question") or EXISTS_TEXT}
        ids = {it["id"]: f"item {it['id']}" for it in pack}
        qs["best"] = {"type": "choice", "instructions": best.get("question") or BEST_TEXT,
                      "criteria": {**ids, NONE_KEY: best.get("none_means") or NONE_TEXT}}
    return qs


def _check(args: dict) -> None:
    ids = [it["id"] for it in args["items"]]
    seen: set[str] = set()
    for i, id_ in enumerate(ids):
        if id_ in seen:
            raise invalid_input(f"items.{i}.id", f"duplicate item id {id_!r}", "ids must be unique")
        seen.add(id_)
        if args.get("pick_best") is not None and id_ in (*RESERVED, NONE_KEY):
            raise invalid_input(f"items.{i}.id", f"id {id_!r} is reserved with pick_best", "rename the item")
    if not args.get("graded") and not args.get("keep_at", 0.6) > args.get("drop_at", 0.2):
        raise invalid_input("keep_at", "keep_at must be greater than drop_at", "set drop_at below keep_at")


def _sum(metas: list[dict]) -> dict:
    out: dict[str, Any] = {"model": metas[-1].get("model"), "request_ids": [], "body_hashes": [], "requests": 0,
                           "latency_ms": 0, "input_tokens": 0, "output_tokens": 0, "chunks_estimate": 0,
                           "timeout_ms_used": 0, "warnings": []}
    for m in metas:
        out["request_ids"] += m.get("request_ids", [])
        out["body_hashes"] += m.get("body_hashes", [])
        for k in ("requests", "latency_ms", "input_tokens", "output_tokens", "chunks_estimate"):
            out[k] += m.get(k, 0)
        out["timeout_ms_used"] = max(out["timeout_ms_used"], m.get("timeout_ms_used", 0))
        out["warnings"] += [w for w in m.get("warnings", []) if w not in out["warnings"]]
    if any("server_ms" in m for m in metas):
        out["server_ms"] = round(sum(m.get("server_ms", 0) for m in metas), 1)
    return out


async def run_filter(ctx: ToolContext, args: dict) -> dict:
    """Reusable by the `openjev filter` CLI with a hand-built ToolContext. Packs run sequentially."""
    items = args["items"]
    if not items:
        meta = {"model": (args.get("options") or {}).get("model") or ctx.config.model, "request_ids": [],
                "requests": 0, "latency_ms": 0, "input_tokens": 0, "output_tokens": 0, "warnings": []}
        return {"kept": [], "dropped": [], "grey": [], "items": [], "meta": meta}
    _check(args)
    size = args.get("pack_size", 10)
    keep_at, drop_at = args.get("keep_at", 0.6), args.get("drop_at", 0.2)
    policy = args.get("grey", "keep")
    graded = args.get("graded")
    relevant_at = (graded or {}).get("relevant_at", 2.5)
    band = Band(keep_at, drop_at)
    rows: list[dict] = []
    metas: list[dict] = []
    best: dict | None = None
    packs = [items[i:i + size] for i in range(0, len(items), size)]
    for n, pack in enumerate(packs):
        out = await run_read(ctx, state=pack_state(args["task"], args.get("items_label", "ITEMS"), pack),
                             questions=_questions(args, pack), options=args.get("options"), band=band)
        metas.append(out.meta)
        for it in pack:
            ans = out.answers[it["id"]]
            if graded:
                rows.append({"id": it["id"], "score": ans["score"],
                             "decision": "keep" if ans["score"] >= relevant_at else "drop"})
            else:
                p = ans["p"]
                rows.append({"id": it["id"], "p": p,
                             "decision": "keep" if p >= keep_at else "drop" if p <= drop_at else "grey"})
        if args.get("pick_best") is not None:
            cand = {"id": None if out.answers["best"]["choice"] == NONE_KEY else out.answers["best"]["choice"],
                    "p": out.answers["best"]["p_top"], "exists_p": out.answers["exists"]["p"]}
            # the best item over packs: the highest exists_p, then the highest p
            if best is None or (cand["exists_p"], cand["p"]) > (best["exists_p"], best["p"]):
                best = cand
        await ctx.progress.emit(n + 1, len(packs), f"{n + 1}/{len(packs)} packs")
    grey = [r["id"] for r in rows if r["decision"] == "grey"]
    kept = [r["id"] for r in rows if r["decision"] == "keep" or (r["decision"] == "grey" and policy == "keep")]
    dropped = [r["id"] for r in rows if r["decision"] == "drop" or (r["decision"] == "grey" and policy == "drop")]
    res: dict[str, Any] = {"kept": kept, "dropped": dropped, "grey": grey, "items": rows, "meta": _sum(metas)}
    if best is not None:
        res["best"] = best
    return res


def register(config: Config) -> ToolSpec:
    schemas.register_tool_schemas("filter", INPUT, OUTPUT)
    # grey items: listed in `grey` always; the `grey` policy also adds them to kept (keep) or dropped (drop), review leaves them
    annotations = {"title": "Filter many items", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}
    return ToolSpec(
        "filter", "Filter many items",
        "Keep or drop many small items (log lines, files, hunks, findings) by one criterion: items are packed into "
        "one state per request with one yes/no question per item id. pick_best also returns the single best item; "
        "graded scores each item instead.",
        schemas.INPUT_SCHEMAS["filter"], schemas.OUTPUT_SCHEMAS["filter"], annotations, _handler)


async def _handler(ctx: ToolContext, args: dict) -> dict:
    return await run_filter(ctx, args)
