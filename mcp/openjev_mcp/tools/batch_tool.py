"""batch: one question set over many states, resumable JSONL, review queue, exports (spec 2.11).

Source rules, import, dry_run and exports live here; the read loop is batch.runner, the file is batch.store,
the cursor is batch.cursor. `images` (phase 3) are loaded once per call by openjev_mcp.images and sent with every row."""
from __future__ import annotations

import math
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from openjev_mcp import library, wire
from openjev_mcp.batch import cursor as cur
from openjev_mcp.batch import exporters, stats, store
from openjev_mcp.batch.importers import import_items, import_source
from openjev_mcp.batch.runner import BatchJob, run_batch
from openjev_mcp.envelope import file_link
from openjev_mcp.errors import ToolError, invalid_input
from openjev_mcp.images import MAX_SIDE_PX, load_images
from openjev_mcp.lint import estimate, lint_request
from openjev_mcp.paths import resolve_write
from openjev_mcp.schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS, register_tool_schemas
from openjev_mcp.tools import ToolContext, ToolSpec
from openjev_mcp.tools.ask_image import IMAGE_INFO, IMAGES, MAX_SIDE
from openjev_mcp.tools.read import _lint_error

SOURCE_HINT = "give exactly one of items, items_file or template"
EXPORT_EXT = {"csv": ".csv", "markdown": ".md", "ojui-batch": ".json"}
EXPORT_MIME = {"csv": "text/csv", "markdown": "text/markdown", "ojui-batch": "application/json"}
CHARS_PER_TOKEN = 3.6
SHARED_FACTOR = 5

_INPUT: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "description": "Give exactly one source of states: items, items_file, or template alone (its sample states); with "
                   "items or items_file, template supplies only questions. questions come from questions, else from an "
                   "ojui-batch items_file, else from template. Checked in code (OJ_INVALID_INPUT), not with a root oneOf.",
    "properties": {
        "items": {"type": "array", "minItems": 1, "maxItems": 500, "items": {
            "type": "object", "additionalProperties": False, "required": ["state"], "properties": {
                "id": {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{1,64}$", "description": "default: 1-based position as a string"},
                "state": {"$ref": "#/$defs/State"}}}},
        "items_file": {"type": "object", "additionalProperties": False, "required": ["path"], "properties": {
            "path": {"type": "string", "description": "file inside the allowed roots"},
            "also": {"type": "array", "maxItems": 7, "items": {"type": "string"}, "description": "more files merged after path with the UI merge rules"},
            "format": {"enum": ["auto", "jsonl", "json", "csv", "tsv", "lines", "blocks", "ojui-batch"], "default": "auto"},
            "delimiter": {"enum": ["auto", ",", "\t", ";"], "default": "auto"},
            "state_field": {"type": "string", "description": "CSV column or JSON key; '*' = the whole object; omitted = guessed (W602)"},
            "id_field": {"type": "string", "description": "omitted = 1-based row index"},
            "state_template": {"type": "string", "description": "e.g. 'Subject: {subject}\\n\\n{body}'; placeholders are column/key names and {id}; wins over state_field"},
            "array_key": {"type": "string", "description": "key of the array in a wrapped JSON file"},
            "encoding": {"enum": ["auto", "utf-8", "utf-16", "cp1252"], "default": "auto"}}},
        "template": {"type": "string", "pattern": "^[a-z0-9_]{1,64}$", "description": "id of openjev://templates/{id}"},
        "questions": {"$ref": "#/$defs/QuestionSet"},
        "options": {"$ref": "#/$defs/ReadOptions"},
        "images": {**IMAGES, "description": "1-8 images (path, url, data_url or base64+content_type, as ask_image), loaded and "
                                            "re-encoded once per call and sent with every row; options.think and sequential are refused (E022)"},
        "max_side_px": MAX_SIDE,
        "sampling": {"enum": ["fast", "server_default"], "default": "fast",
                     "description": "fast: samples 1 plus a regrey re-read; server_default: no samples field. An explicit options.samples wins."},
        "regrey_samples": {"type": "integer", "minimum": 0, "maximum": 32, "default": 4,
                           "description": "fast mode only: grey/below-review rows are re-read once with this many samples; 0 disables"},
        "thresholds": {"$ref": "#/$defs/Band"},
        "review_rule": {"type": "object", "additionalProperties": False, "properties": {
            "choice_p_below": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.8},
            "noul_grey": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number", "minimum": 0, "maximum": 1}, "default": [0.15, 0.85]},
            "score_spread_above": {"type": "number", "minimum": 0, "default": 0.6}}},
        "audit": {"type": "object", "additionalProperties": False, "properties": {
            "rate": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.03}, "seed": {"type": "integer", "default": 0}}},
        "concurrency": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1,
                        "description": "parallel reads for this call, capped by OPENJEV_MCP_MAX_INFLIGHT_BATCH; no speedup on MLX (W405)"},
        "output_path": {"type": "string", "description": ".jsonl inside the allowed roots: header record + one full row per item; the resumable source of truth"},
        "include_state": {"type": "boolean", "default": True, "description": "write the state text into each JSONL row (exports need it); false keeps only state_hash"},
        "export": {"type": "array", "maxItems": 3, "items": {"type": "object", "additionalProperties": False, "required": ["format", "path"], "properties": {
            "format": {"enum": ["csv", "markdown", "ojui-batch"]},
            "path": {"type": "string", "description": ".csv, .md or .json, created new inside the allowed roots"}}},
            "description": "written once, by the call that finishes the job (next_cursor null); needs output_path"},
        "detail": {"enum": ["compact", "full"], "default": "compact", "description": "inline results only; JSONL rows are always full"},
        "max_items": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 5000, "description": "rows taken from the source; the rest is dropped with W601"},
        "max_items_per_call": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
        "time_budget_s": {"type": "integer", "minimum": 5, "maximum": 600, "default": 120, "description": "no new row is started after this; in-flight rows finish"},
        "cursor": {"type": "string", "description": "next_cursor of the previous call (opaque)"},
        "resume": {"type": "boolean", "default": True, "description": "skip ids whose last row in output_path is ok (and error unless retry_errors); false requires a new output_path"},
        "retry_errors": {"type": "boolean", "default": False, "description": "re-run ids whose last row in output_path has status error"},
        "only_ids": {"type": "array", "minItems": 1, "maxItems": 500, "items": {"type": "string"}, "description": "run only these ids (single-row retry)"},
        "on_error": {"enum": ["record", "abort"], "default": "record"},
        "dry_run": {"type": "boolean", "default": False, "description": "no network: import report, 3 preview states, the first HTTP body, an estimate"},
        "max_inline_results": {"type": "integer", "minimum": 0, "maximum": 100, "default": 50},
    },
}

_OUTPUT: dict[str, Any] = {
    "type": "object", "required": ["status", "summary", "results", "next_cursor", "meta"],
    "properties": {
        "status": {"type": "object", "required": ["done", "total", "stopped_reason"], "properties": {
            "done": {"type": "integer", "description": "rows finished in this call"},
            "total": {"type": "integer", "description": "rows in the source after max_items"},
            "remaining": {"type": "integer"}, "ok": {"type": "integer"}, "errors": {"type": "integer"},
            "skipped": {"type": "integer", "description": "resumed rows not re-read"},
            "stopped_reason": {"enum": ["complete", "max_items_per_call", "time_budget", "backpressure", "error_abort", "dry_run"]},
            "elapsed_ms": {"type": "number"}, "eta_ms": {"type": ["number", "null"]}, "req_per_s": {"type": "number"},
            "effective_concurrency": {"type": "integer"}, "backoffs": {"type": "integer"},
            "input_tokens": {"type": "integer"}, "output_tokens": {"type": "integer"}}},
        "summary": {"type": "object", "properties": {
            "scope": {"enum": ["output_path", "call"], "description": "output_path: all rows in the file; call: this call only (no output_path)"},
            "n": {"type": "integer"}, "ok": {"type": "integer"}, "errors": {"type": "integer"},
            "needs_review": {"type": "integer"}, "audit": {"type": "integer"},
            "per_question": {"type": "object", "additionalProperties": {"$ref": "#/$defs/QuestionStats"}}}},
        "results": {"type": "array", "items": {"type": "object", "required": ["id", "status"], "properties": {
            "index": {"type": "integer"}, "id": {"type": "string"}, "status": {"enum": ["ok", "error", "skipped"]},
            "answers": {"type": "object", "description": "compact {choice, p_top} / {p, band} / {score, level}; full Answer objects with detail full"},
            "needs_review": {"type": "boolean"}, "audit": {"type": "boolean"},
            "error": {"oneOf": [{"$ref": "#/$defs/ToolError"}, {"type": "null"}]}}}},
        "review_queue": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "question": {"type": "string"},
            "reason": {"enum": ["choice_p_below", "noul_grey", "score_spread_above", "abstained", "error"]},
            "confidence": {"type": "number"}}},
            "description": "this call's rows, ascending confidence/margin; the whole file through batch_results"},
        "audit_ids": {"type": "array", "items": {"type": "string"}},
        "output_path": {"type": ["string", "null"]},
        "exports": {"type": "array", "items": {"type": "object", "properties": {
            "format": {"type": "string"}, "path": {"type": "string"}, "bytes": {"type": "integer"}}}},
        "import": {"type": "object", "description": "first call, dry_run, and any call without cursor", "properties": {
            "format": {"type": "string"}, "delimiter": {"type": ["string", "null"]}, "encoding": {"type": "string"},
            "state_field": {"type": ["string", "null"]}, "id_field": {"type": ["string", "null"]},
            "columns": {"type": "array", "items": {"type": "string"}}, "row_count": {"type": "integer"},
            "truncated": {"type": "boolean"}, "warnings": {"type": "array", "items": {"$ref": "#/$defs/LintFinding"}}}},
        "preview": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "state": {"$ref": "#/$defs/State"}}}},
        "first_body": {"type": "object", "description": "dry_run: the first POST /v1/systemone body (image data elided)"},
        "images": IMAGE_INFO,
        "estimate": {"type": "object", "properties": {
            "requests": {"type": "integer"}, "billed_reads": {"type": "integer"}, "input_tokens_approx": {"type": "integer"},
            "time_s_idle_approx": {"type": "number"}, "time_s_shared_approx": {"type": "number"}}},
        "next_cursor": {"type": ["string", "null"], "description": "null when every row is done"},
        "meta": {"$ref": "#/$defs/Meta"},
    },
}

DESCRIPTION = ("Run one question set over many states (inline items, a CSV/JSONL/JSON file or a library template) with "
               "bounded concurrency, a resumable JSONL output, a review queue, a seeded audit sample, per-question "
               "statistics and csv, markdown or ojui-batch exports. One call reads at most max_items_per_call rows; "
               "pass next_cursor back to continue. dry_run sends nothing and returns the import report, the first body "
               "and an estimate.")


class _Counting:
    """Client proxy counting the HTTP reads a call makes (retries and re-reads included)."""

    def __init__(self, inner, images: list[str] | None = None):
        self._inner, self.requests, self._images = inner, 0, images

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def systemone(self, body, *a, **kw):
        self.requests += 1
        if self._images:   # deviation: 2.11 images: the runner builds bodies without images; the same data URLs are added here, last key, as wire.build_body would
            body, kw["has_images"] = {**body, "images": self._images}, True
        return await self._inner.systemone(body, *a, **kw)


class _Source:
    def __init__(self, items, questions, options, report, header_source, run_source, title):
        self.items, self.questions, self.options, self.report = items, questions, options, report
        self.header_source, self.run_source, self.title = header_source, run_source, title


def _resolve(config, args: dict) -> _Source:
    has_items, has_file, tid = args.get("items") is not None, args.get("items_file") is not None, args.get("template")
    if has_items and has_file:
        raise invalid_input("items", "give items or items_file, not both", SOURCE_HINT)
    if not (has_items or has_file or tid):
        raise invalid_input("items", "no state source", SOURCE_HINT)
    tpl = None
    if tid:
        tpl = library.template(tid)
        if tpl is None:
            raise invalid_input("template", f"unknown template {tid!r}", "see openjev://templates for the ids")
    cap = args.get("max_items", 5000)
    questions, options = args.get("questions"), args.get("options")
    imported_q = imported_o = None
    if has_items:
        res = import_items(args["items"], max_items=cap)
        hdr = {"kind": "items"}
        run_src = {"kind": "items", "hash": wire.canonical_hash(args["items"])}
    elif has_file:
        spec = args["items_file"]
        res = import_source(spec, config, max_items=cap)
        imported_q, imported_o = res.questions, res.options
        rep = res.report
        hdr = {"kind": "items_file", "path": spec["path"], "format": rep["format"], "delimiter": rep["delimiter"],
               "state_field": rep["state_field"], "id_field": rep["id_field"], "row_count": rep["row_count"]}
        run_src = {"kind": "items_file", "spec": spec, "row_count": rep["row_count"]}
    else:
        res = import_items(tpl["states"], max_items=cap)
        hdr = {"kind": "template", "id": tid}
        run_src = {"kind": "template", "id": tid, "row_count": len(res.items)}
    hdr = {**hdr, "row_count": len(res.items)}
    if questions is None:
        questions = imported_q
        if questions is None and tpl is not None:
            questions = tpl["questions"]
            if options is None and imported_o is None:
                options = tpl.get("options")
        if options is None:
            options = imported_o
    if questions is None:
        raise invalid_input("questions", "no questions", "pass questions, an ojui-batch items_file or a template")
    if options is None:
        options = {}
    title = tpl["title"] if tpl else None
    return _Source(res.items, questions, dict(options), res.report, hdr, run_src, title)


def _short(a: dict) -> dict:
    kind = a.get("type")
    if kind == "noul":
        return {"p": a["p"], "band": a["band"]}
    if kind == "choice":
        return {"choice": a["choice"], "p_top": a["p_top"]}
    if kind == "score":
        return {"score": a["score"], "level": a["level"]}
    return a


def _result(r: dict, full: bool) -> dict:
    out = {k: r[k] for k in ("index", "id", "status") if k in r}
    if r.get("answers"):
        out["answers"] = r["answers"] if full else {q: _short(a) for q, a in r["answers"].items()}
    out["needs_review"] = bool(r.get("needs_review"))
    if r.get("audit"):
        out["audit"] = True
    if full and r.get("review_reasons"):
        out["review_reasons"] = r["review_reasons"]
    out["error"] = r.get("error")
    return out


def _r4(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, dict):
        return {k: _r4(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_r4(x) for x in v]
    return v


def _summary(scope: str, rows: list[dict], questions: dict) -> dict:
    return {"scope": scope, "n": len(rows), "ok": sum(r.get("status") == "ok" for r in rows),
            "errors": sum(r.get("status") == "error" for r in rows), "needs_review": sum(bool(r.get("needs_review")) for r in rows),
            "audit": sum(bool(r.get("audit")) for r in rows), "per_question": _r4(stats.per_question(rows, questions))}


def _estimate(src: _Source, model: str, options: dict, sampling: str, rows: list) -> dict:
    """Playground formula: ceil((chars JSON(state) + chars JSON(questions)) / 3.6) per row, x samples (x2 with think)."""
    samples, think = options.get("samples") or 1, options.get("think") or 0
    q_chars = len(wire.dumps_text(src.questions))
    billed = samples if samples > 1 else 1
    tokens = idle_ms = 0
    for it in rows:
        tokens += math.ceil((len(wire.dumps_text(it.state)) + q_chars) / CHARS_PER_TOKEN) * billed * (2 if think > 0 else 1)
        idle_ms += estimate(wire.build_body(model, it.state, src.questions, {**options, "samples": samples}))["latency_ms_idle_approx"]
    idle = round(idle_ms / 1000, 1)
    return {"requests": len(rows), "billed_reads": len(rows) * billed, "input_tokens_approx": tokens,
            "time_s_idle_approx": idle, "time_s_shared_approx": round(idle * SHARED_FACTOR, 1)}


def _check_exports(ctx: ToolContext, args: dict) -> list[dict]:
    exports = args.get("export") or []
    if exports and not args.get("output_path"):
        raise invalid_input("export", "export needs output_path", "pass output_path (a .jsonl file) with export")
    for i, e in enumerate(exports):
        try:
            resolve_write(e["path"], ctx.config, exts=frozenset({EXPORT_EXT[e["format"]]}), new_only=True)
        except ToolError as err:
            raise invalid_input(f"export.{i}.path", err.message, err.hint) from None
    return exports


def _write_exports(ctx: ToolContext, exports: list[dict], out: store.OutputHandle, src: _Source, args: dict) -> list[dict]:
    rows = sorted(out.rows.values(), key=lambda r: r.get("index", 0))
    title = src.title or Path(args["output_path"]).stem
    done = []
    for e in exports:
        fmt = e["format"]
        if fmt == "csv":
            text = exporters.to_csv(out.header, rows, src.questions)
        elif fmt == "markdown":
            text = exporters.to_markdown(out.header, rows, src.questions)
        else:
            text = exporters.to_ojui_batch(out.header, rows, src.questions, src.options, exported_at=store.now_iso(), title=title)
        try:
            path = exporters.write_new(e["path"], text, ctx.config, EXPORT_EXT[fmt])
        except FileExistsError:
            raise invalid_input(f"export.{exports.index(e)}.path", "file already exists; export files are created new only", "choose a new path") from None
        ctx.links.append(file_link(path, EXPORT_MIME[fmt]))
        done.append({"format": fmt, "path": path, "bytes": len(text.encode("utf-8"))})
    return done


def _lines(ctx: ToolContext, *groups) -> list[str]:
    seen: dict[str, None] = {}
    for g in groups:
        for w in g:
            seen[w if isinstance(w, str) else w.line()] = None
    return list(seen)


async def batch(ctx: ToolContext, args: dict) -> dict:
    cfg = ctx.config
    exports = _check_exports(ctx, args)
    src = _resolve(cfg, args)
    items, questions, options = src.items, src.questions, src.options
    only = args.get("only_ids")
    sampling, regrey = args.get("sampling", "fast"), args.get("regrey_samples", 4)
    conc, dry = args.get("concurrency", 1), bool(args.get("dry_run"))
    chosen = [it for it in items if it.id in set(only)] if only else items
    first_options = dict(options)
    if sampling == "fast" and first_options.get("samples") is None:
        first_options["samples"] = 1
    model = options.get("model") or cfg.model
    first = (chosen or items)[0]
    loaded = []
    if args.get("images"):
        for key in ("think", "sequential"):
            if options.get(key):
                raise invalid_input(f"options.{key}", f"E022: {key} needs a text state; the server returns 400 with images",
                                    f"drop {key} for image reads, or drop the images")
        loaded = await load_images(args["images"], cfg, max_side_px=args.get("max_side_px", MAX_SIDE_PX))
    urls = [i.data_url for i in loaded]
    first_body = wire.build_body(model, first.state, questions, first_options, urls)
    # lint once, before the output file exists (a typo'd question set must not leave a header-only file);
    # the runner's own first-row lint is a cache hit of the same checks
    limits = ctx.limits.peek() if dry else await ctx.limits.get()
    report = lint_request({**first_body, "concurrency": conc, "sampling": sampling, "regrey_samples": regrey},
                          limits=limits, autofix=False)
    if report.errors:
        raise _lint_error(report.errors)
    warn = list(report.warnings)
    imp = {k: v for k, v in src.report.items()}
    show_import = dry or (args.get("cursor") is None and src.header_source["kind"] == "items_file")
    if dry:
        est = _estimate(src, model, options, sampling, chosen)
        n = len(chosen)
        shown = {**first_body, "images": [f"{i.sent_as};base64,<{i.bytes} bytes elided>" for i in loaded]} if loaded else first_body
        return {"status": {"done": 0, "total": len(items), "remaining": n, "ok": 0, "errors": 0, "skipped": 0, "stopped_reason": "dry_run"},
                "summary": _summary("call", [], questions), "results": [], "review_queue": [], "audit_ids": [],
                "output_path": None, "import": imp, "preview": [{"id": it.id, "state": it.state} for it in items[:3]],
                "first_body": shown, "estimate": est, "next_cursor": None,
                "meta": {"model": model, "requests": 0, "latency_ms": 0,
                         **({"warnings": w} if (w := _lines(ctx, ctx.warnings, warn)) else {})},
                **({"images": [i.info() for i in loaded]} if loaded else {})}
    qh = store.question_hash(questions)
    run_source = {**src.run_source, "images": wire.canonical_hash(urls)} if urls else src.run_source   # a changed image is another job
    rid = store.run_id(qh, run_source, src.options, sampling, regrey)
    ahash = cur.args_hash(args)
    token = cur.decode(args["cursor"]) if args.get("cursor") else None
    if token:   # mismatch first, so changed arguments report as such rather than as "another job"
        cur.check(token, run_id=rid, args_hash=ahash, out_size=token["out"]["bytes"])
        if token["offset"] > len(items):
            raise invalid_input("cursor", "invalid cursor", "drop the cursor and call again with resume:true")
    out = None
    if args.get("output_path"):
        header = store.make_header(run_id=rid, question_hash=qh, options=options, sampling=sampling,
                                   thresholds=args.get("thresholds"), review_rule=args.get("review_rule"),
                                   audit=args.get("audit"), source=src.header_source, questions=src.questions)
        out = store.open_output(args["output_path"], header, resume=args.get("resume", True), config=cfg)
    counter = _Counting(ctx.client, urls)
    try:
        if token:
            cur.check(token, run_id=rid, args_hash=ahash, out_size=out.size() if out else None)
        skip: set[str] = set()
        if out is not None and args.get("resume", True):
            retry = args.get("retry_errors", False)
            skip = {i for i, r in out.rows.items() if r.get("status") == "ok" or (r.get("status") == "error" and not retry)}
        job = BatchJob(items=items, questions=questions, options=options, sampling=sampling, regrey_samples=regrey,
                       thresholds=args.get("thresholds") or {}, review_rule=args.get("review_rule") or {},
                       audit=args.get("audit") or {}, concurrency=conc, output=out, include_state=args.get("include_state", True),
                       max_items_per_call=args.get("max_items_per_call", 25), time_budget_s=args.get("time_budget_s", 120),
                       on_error=args.get("on_error", "record"), start_offset=token["offset"] if token else 0,
                       skip_ids=skip, only_ids=only)
        try:
            run = await run_batch(replace(ctx, client=counter), job)
        except BaseException:
            if out is not None and out.created and out.lines <= 1:
                out.close()
                os.unlink(out.path)   # a call that failed before any row leaves no header-only file
            raise
        done = [r for r in run.rows if r["status"] != "skipped"]
        written: list[dict] = []
        if run.next_offset is None and exports:
            written = _write_exports(ctx, exports, out, src, args)
        all_rows = sorted(out.rows.values(), key=lambda r: r.get("index", 0)) if out is not None else done
        summary = _summary("output_path" if out is not None else "call", all_rows, questions)
        nxt = None if run.next_offset is None else cur.encode(rid, run.next_offset, ahash, run.out_bytes, run.out_lines)
        store_warnings = list(out.warnings) if out is not None else []
        out_path = str(out.path) if out is not None else None
    finally:
        if out is not None:
            out.close()
    if out_path:
        ctx.links.insert(0, file_link(out_path, "application/x-ndjson"))
    cap_inline = args.get("max_inline_results", 50)
    # deviation: 2.11 results: rows skipped by a resume are counted in status.skipped, not listed (they would crowd out the new rows)
    results = [_result(r, args.get("detail") == "full") for r in done[:cap_inline]]
    notes = [f"results: first {cap_inline} of {len(done)} rows inline; read the rest with batch_results"] if len(done) > cap_inline else []
    queue = [{k: v for k, v in e.items() if v is not None} for e in stats.review_queue(done, questions, args.get("review_rule"))]
    lines = _lines(ctx, ctx.warnings, warn, run.lint_warnings, run.warnings, store_warnings, notes)
    ok_row = next((r for r in done if r.get("model")), None)
    meta = {"model": ok_row["model"] if ok_row else model, "requests": counter.requests,
            "latency_ms": round(sum(r.get("latency_ms") or 0 for r in done))}
    if lines:
        meta["warnings"] = lines
    res: dict[str, Any] = {"status": run.status, "summary": summary, "results": results, "review_queue": queue,
                           "audit_ids": [r["id"] for r in done if r.get("audit")], "output_path": out_path}
    if written:
        res["exports"] = written
    if show_import:
        res["import"] = imp
    if loaded:
        res["images"] = [i.info() for i in loaded]
    res.update(next_cursor=nxt, meta=meta)
    return res


def register(config) -> ToolSpec:
    register_tool_schemas("batch", _INPUT, _OUTPUT)
    annotations = {"title": "Batch read", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
                   "openWorldHint": bool(config.fetch)}   # image URLs are fetched only with OPENJEV_MCP_FETCH=on
    return ToolSpec("batch", "Batch read", DESCRIPTION, INPUT_SCHEMAS["batch"], OUTPUT_SCHEMAS["batch"], annotations, batch)
