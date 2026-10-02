"""batch_results: query, export and compare finished batch outputs (spec 2.21). Reads files only, never the network."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
from typing import Any

from openjev_mcp import schemas, wire
from openjev_mcp.batch import compare as cmp
from openjev_mcp.batch import exporters, stats, store
from openjev_mcp.config import Config
from openjev_mcp.envelope import file_link
from openjev_mcp.errors import invalid_input
from openjev_mcp.tools import ToolContext, ToolSpec

INLINE_CAP = 64 * 1024
COMPACT_STATE = 120
EXPORT_EXT = {"csv": (".csv", "text/csv"), "markdown": (".md", "text/markdown"), "ojui-batch": (".json", "application/json"),
              "jsonl": (".jsonl", "application/x-ndjson")}
PAGE_ARGS_EXCLUDED = ("cursor", "limit", "detail", "export")

INPUT = {
    "type": "object", "additionalProperties": False, "required": ["path"],
    "properties": {
        "path": {"type": "string", "description": "a batch output .jsonl inside the allowed roots (first line is the batch header)"},
        "view": {"enum": ["rows", "review", "stats"], "default": "rows"},
        "filter": {"type": "object", "additionalProperties": False, "properties": {
            "status": {"enum": ["ok", "error", "any"], "default": "any"},
            "min_confidence_below": {"type": "number", "minimum": 0, "maximum": 1,
                                     "description": "Playground low-confidence filter: keep rows where any question (or question, when given) is below"},
            "question": {"type": "string"},
            "choice": {"type": "string", "description": "with question: rows whose choice is this key"},
            "band": {"enum": ["yes", "no", "grey"], "description": "with question: noul band"},
            "needs_review": {"type": "boolean"}, "audit": {"type": "boolean"},
            "ids": {"type": "array", "maxItems": 500, "items": {"type": "string"}}}},
        "sort_by": {"enum": ["index", "value", "confidence"], "default": "index",
                    "description": "value: choice key, score expected level, noul p of sort_question; confidence: min confidence over questions, or of sort_question"},
        "sort_question": {"type": "string"},
        "order": {"enum": ["asc", "desc"], "default": "asc"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        "cursor": {"type": "string", "description": "next_cursor of the previous batch_results call"},
        "detail": {"enum": ["compact", "full"], "default": "compact"},
        "export": {"type": "object", "additionalProperties": False, "required": ["format"], "properties": {
            "format": {"enum": ["csv", "markdown", "ojui-batch", "jsonl"]},
            "path": {"type": "string", "description": "created new (.csv, .md, .json, .jsonl); omitted = inline, up to 64 KiB, truncated with a warning"},
            "filtered": {"type": "boolean", "default": False, "description": "export only the rows that pass filter"}}},
        "compare_to": {"type": "object", "additionalProperties": False, "required": ["path"], "properties": {
            "path": {"type": "string", "description": "a second batch output .jsonl over the same ids"},
            "question_map": {"type": "object", "additionalProperties": {"type": "string"},
                             "description": "question id in path -> question id in compare_to.path; default same ids"},
            "key_map": {"type": "object", "additionalProperties": {"type": "object", "additionalProperties": {"type": "string"}},
                        "description": "per question: option key in compare_to.path -> option key in path; keys not listed map to themselves"}}}}}

_NUM = {"type": ["number", "null"]}
OUTPUT = {
    "type": "object", "required": ["view", "meta"],
    "properties": {
        "view": {"type": "string"},
        "rows": {"type": "array", "items": {"type": "object"}, "description": "BatchRow (full) or compact rows; last row per id"},
        "review_queue": {"type": "array", "items": {"type": "object"}},
        "stats": {"type": "object", "properties": {
            "n": {"type": "integer"}, "ok": {"type": "integer"}, "errors": {"type": "integer"}, "needs_review": {"type": "integer"},
            "per_question": {"type": "object", "additionalProperties": {"$ref": "#/$defs/QuestionStats"}},
            "input_tokens": {"type": "integer"}, "output_tokens": {"type": "integer"},
            "latency_ms_p50": {"type": "number"}, "latency_ms_p95": {"type": "number"}}},
        "matched": {"type": "integer"},
        "export": {"type": "object", "properties": {
            "format": {"type": "string"}, "path": {"type": ["string", "null"]}, "inline": {"type": ["string", "null"]},
            "bytes": {"type": "integer"}, "truncated": {"type": "boolean"}}},
        "compare": {"type": "object", "properties": {
            "matched": {"type": "integer"}, "only_in_a": {"type": "array", "items": {"type": "string"}},
            "only_in_b": {"type": "array", "items": {"type": "string"}}, "max_jsd": _NUM,
            "per_question": {"type": "object", "additionalProperties": {"type": "object", "properties": {
                "jsd_mean": _NUM, "jsd_max": _NUM, "jsd_max_id": {"type": ["string", "null"]},
                "agreement": {"type": "number", "description": "share of ids with the same top answer (choice key, noul band, score level)"},
                "flipped_ids": {"type": "array", "items": {"type": "string"}}, "mean_abs_delta_p": _NUM,
                "jsd_reason": {"type": ["string", "null"], "description": "why jsd is null, e.g. option sets differ and no key_map"}}}}}},
        "next_cursor": {"type": ["string", "null"]},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "meta": {"$ref": "#/$defs/Meta"}}}


def load(path: str, config: Config) -> tuple[dict, dict[str, dict], list[str]]:
    """(header, last row per id in index order, warnings); the file must start with a batch header."""
    header, rows, _, warnings = store.read_output(path, config)
    if header is None:
        raise invalid_input("path", "path has no batch header; pass an output written by batch or batch_results",
                            "the first line must be the batch header record")
    return header, dict(sorted(rows.items(), key=lambda kv: kv[1].get("index", 0))), warnings


def infer_questions(rows: dict[str, dict]) -> dict[str, dict]:
    """The header holds only question_hash, so question types and option sets are read back from the answers."""
    # deviation: 2.21: questions are rebuilt from the answers (type, option keys, level count); instructions are not stored in the output
    qs: dict[str, dict] = {}
    for r in rows.values():
        for qid, a in (r.get("answers") or {}).items():
            q = qs.setdefault(qid, {"type": a.get("type"), "_keys": {}})
            for k in a.get("probabilities") or {}:
                q["_keys"].setdefault(k, None)
    out = {}
    for qid, q in qs.items():
        keys = list(q["_keys"])
        crit = ({k: "" for k in keys} if q["type"] == "choice" else
                [str(i) for i in range(len(keys))] if q["type"] == "score" else {})
        out[qid] = {"type": q["type"], **({"criteria": crit} if crit else {})}
    return out


def _conf(a: dict) -> float | None:
    return stats.answer_confidence(a)


def min_confidence(row: dict, question: str | None = None) -> float | None:
    answers = row.get("answers") or {}
    vals = [_conf(a) for q, a in answers.items() if question in (None, q)]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def _keep(row: dict, f: dict) -> bool:
    st = f.get("status", "any")
    if st != "any" and row.get("status") != st:
        return False
    if "ids" in f and row["id"] not in f["ids"]:
        return False
    if "needs_review" in f and bool(row.get("needs_review")) != f["needs_review"]:
        return False
    if "audit" in f and bool(row.get("audit")) != f["audit"]:
        return False
    q = f.get("question")
    ans = (row.get("answers") or {}).get(q) if q else None
    if q and ans is None:
        return False
    if "choice" in f and ans.get("choice") != f["choice"]:
        return False
    if "band" in f and ans.get("band") != f["band"]:
        return False
    if "min_confidence_below" in f:
        c = min_confidence(row, q)
        if c is None or not c < f["min_confidence_below"]:
            return False
    return True


def _check_filter(f: dict) -> None:
    if ("choice" in f or "band" in f) and not f.get("question"):
        raise invalid_input("filter.question", "filter.choice and filter.band need filter.question", "add filter.question")


def _value(row: dict, question: str) -> Any:
    a = (row.get("answers") or {}).get(question)
    if not a:
        return None
    return {"noul": a.get("p"), "choice": a.get("choice"), "score": a.get("score")}.get(a.get("type"))


def _sort(rows: list[dict], args: dict, questions: dict) -> list[dict]:
    by, desc = args.get("sort_by", "index"), args.get("order", "asc") == "desc"
    sq = args.get("sort_question")
    if sq and sq not in questions:
        raise invalid_input("sort_question", f"unknown question {sq!r}", "questions: " + ", ".join(questions))
    if by == "index":
        return sorted(rows, key=lambda r: r.get("index", 0), reverse=desc)
    sq = sq or next(iter(questions), None)
    key = (lambda r: _value(r, sq)) if by == "value" else (lambda r: min_confidence(r, args.get("sort_question")))
    keyed = sorted(((key(r), r) for r in rows if key(r) is not None), key=lambda t: t[1].get("index", 0))
    keyed.sort(key=lambda t: t[0], reverse=desc)   # stable: equal keys stay in index order either way
    return [r for _, r in keyed] + [r for r in rows if key(r) is None]   # rows without a value sort last


def _compact_answer(a: dict) -> dict:
    return {k: a[k] for k in {"noul": ("p", "band"), "choice": ("choice", "p_top"), "score": ("score", "level", "level_label")}
            .get(a.get("type"), ()) if k in a}


def _compact(row: dict) -> dict:
    out = {k: row[k] for k in ("index", "id", "status", "needs_review", "review_reasons") if k in row}
    state = row.get("state")
    if isinstance(state, str):
        out["state"] = state if len(state) <= COMPACT_STATE else state[:COMPACT_STATE - 1] + "…"
    out["answers"] = {q: _compact_answer(a) for q, a in (row.get("answers") or {}).items()}
    out["min_confidence"] = min_confidence(row)
    if row.get("error"):
        out["error"] = row["error"]
    return out


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[max(0, math.ceil(p * len(xs)) - 1)]


def _stats(rows: list[dict], questions: dict) -> dict:
    lat = [r["latency_ms"] for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    use = lambda k: sum((r.get("usage") or {}).get(k) or 0 for r in rows)
    out = {"n": len(rows), "ok": sum(r.get("status") == "ok" for r in rows), "errors": sum(r.get("status") == "error" for r in rows),
           "needs_review": sum(bool(r.get("needs_review")) for r in rows), "per_question": stats.per_question(rows, questions),
           "input_tokens": use("input_tokens"), "output_tokens": use("output_tokens")}
    if lat:
        out |= {"latency_ms_p50": _pct(lat, 0.5), "latency_ms_p95": _pct(lat, 0.95)}
    return out


def _args_key(args: dict) -> str:
    kept = {k: v for k, v in args.items() if k not in PAGE_ARGS_EXCLUDED}
    return hashlib.sha256(json.dumps(kept, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()[:16]


def _encode(offset: int, key: str) -> str:
    raw = json.dumps({"v": 1, "offset": offset, "args": key}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _offset(token: str | None, key: str) -> int:
    if not token:
        return 0
    bad = invalid_input("cursor", "invalid cursor", "drop the cursor and call again")
    try:
        d = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except (ValueError, binascii.Error, TypeError):
        raise bad from None
    if not (isinstance(d, dict) and d.get("v") == 1 and isinstance(d.get("offset"), int) and not isinstance(d["offset"], bool)
            and d["offset"] >= 0 and isinstance(d.get("args"), str)):
        raise bad
    if d["args"] != key:
        raise invalid_input("cursor", "cursor belongs to a different query", "drop the cursor or repeat the same path, view, filter and sort")
    return d["offset"]


def _export(ctx: ToolContext, spec: dict, header: dict, rows: list[dict], questions: dict, warnings: list[str]) -> dict:
    fmt = spec["format"]
    ext, mime = EXPORT_EXT[fmt]
    if fmt == "csv":
        text = exporters.to_csv(header, rows, questions)
    elif fmt == "markdown":
        text = exporters.to_markdown(header, rows, questions)
    elif fmt == "jsonl":
        text = exporters.to_jsonl(header, rows)
    else:
        title = os.path.splitext(os.path.basename(spec.get("path") or "batch"))[0]
        text = exporters.to_ojui_batch(header, rows, questions, header.get("options"), exported_at=store.now_iso(), title=title)
    size = len(text.encode("utf-8"))
    if spec.get("path"):
        path = exporters.write_new(spec["path"], text, ctx.config, ext)
        ctx.links.append(file_link(path, mime))
        return {"format": fmt, "path": path, "inline": None, "bytes": size, "truncated": False}
    cut = size > INLINE_CAP
    if cut:
        text = text.encode("utf-8")[:INLINE_CAP].decode("utf-8", errors="ignore")
        warnings.append(f"inline export truncated at {INLINE_CAP // 1024} KiB ({size} bytes); pass export.path to write the whole file")
    return {"format": fmt, "path": None, "inline": text, "bytes": size, "truncated": cut}


async def batch_results(ctx: ToolContext, args: dict) -> dict:
    header, all_rows, warnings = load(args["path"], ctx.config)
    questions = header.get("questions") or infer_questions(all_rows)
    f = args.get("filter") or {}
    _check_filter(f)
    rows = [r for r in all_rows.values() if _keep(r, f)]
    view, limit = args.get("view", "rows"), args.get("limit", 50)
    out: dict[str, Any] = {"view": view, "matched": len(rows), "next_cursor": None}
    key = _args_key(args)
    offset = _offset(args.get("cursor"), key)
    if view == "stats":
        out["stats"] = _stats(rows, questions)
    else:
        if view == "review":
            items = stats.review_queue(rows, questions, header.get("review_rule"))
            # sorting options apply to the rows view only; the queue is ascending confidence by definition
        else:
            items = _sort(rows, args, questions)
            if args.get("detail", "compact") == "compact":
                items = [_compact(r) for r in items]
        page = items[offset:offset + limit]
        out["review_queue" if view == "review" else "rows"] = page
        if offset + limit < len(items):
            out["next_cursor"] = _encode(offset + limit, key)
    if args.get("compare_to"):
        c = args["compare_to"]
        _, b_rows, b_warn = load(c["path"], ctx.config)
        warnings += b_warn
        out["compare"] = cmp.compare(all_rows, b_rows, c.get("question_map"), c.get("key_map"))
    if args.get("export"):
        e = args["export"]
        out["export"] = _export(ctx, e, header, rows if e.get("filtered") else list(all_rows.values()), questions, warnings)
    out["warnings"] = warnings
    out["meta"] = {"requests": 0, "warnings": list(warnings)}
    return out


def register(config: Config) -> ToolSpec:
    schemas.register_tool_schemas("batch_results", INPUT, OUTPUT)
    annotations = {"title": "Batch results", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
                   "openWorldHint": False}
    return ToolSpec("batch_results", "Batch results",
                    "Query, export and compare a finished or partial batch output without spending reads: review queue, "
                    "sorted and filtered rows, statistics, csv/markdown/ojui-batch/jsonl exports and Jensen-Shannon "
                    "comparison with a second output. Makes no network request.",
                    schemas.INPUT_SCHEMAS["batch_results"], schemas.OUTPUT_SCHEMAS["batch_results"], annotations, batch_results)
