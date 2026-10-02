"""start_batch and review_batch prompt specs (spec 2.18 prompts table). P14 registers them in prompts.PROMPTS and COMPLETERS."""
from __future__ import annotations

from openjev_mcp import library, wire
from openjev_mcp.batch import stats
from openjev_mcp.errors import ToolError
from openjev_mcp.prompts import PromptArg, PromptError, PromptSpec
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools.batch_results import infer_questions, load

REVIEW_LIMIT = 20
QUEUE_STATE = 200


def _text(text: str) -> dict:
    return {"role": "user", "content": {"type": "text", "text": text}}


def _resource(uri: str, body: object) -> dict:
    return {"role": "user", "content": {"type": "resource", "resource": {"uri": uri, "mimeType": "application/json",
                                                                          "text": wire.dumps_text(body)}}}


def complete_template(value: str, ctx: ToolContext) -> list[str]:
    return library.template_ids()


COMPLETERS = {("ref/prompt", "start_batch", "template"): complete_template}


async def _start(args: dict, ctx: ToolContext) -> list[dict]:
    tid = args["template"]
    tpl = library.template(tid)
    if tpl is None:
        raise PromptError(f"Unknown template: {tid}")
    src = f"items_path {args['items_path']!r}" if args.get("items_path") else "the template's example states (or your own items / items_path)"
    out = args.get("output_path") or "<a new .jsonl path inside the allowed roots>"
    plan = (f"Run the batch template {tid!r} over {src}.\n\n"
            f"1. Call `batch` with the template and your items and `dry_run: true` first. It reads nothing from the model.\n"
            f"2. Confirm the import mapping (which column is the state, which the id) and the estimate (requests, tokens, time) with the user.\n"
            f"3. Run `batch` again without `dry_run` and with `output_path: {out}`. While `next_cursor` is not null, call again with that cursor.\n"
            f"4. After an interruption call `batch` again without a cursor and with `resume: true`; finished rows are not re-read.\n"
            f"5. When it finishes, call `batch_results` with `view: \"review\"` to work the rows that need a human look.\n\n"
            f"The template follows.")
    return [_text(plan), _resource(f"openjev://templates/{tid}", tpl)]


async def _review(args: dict, ctx: ToolContext) -> list[dict]:
    raw = args.get("limit")
    try:
        limit = REVIEW_LIMIT if raw in (None, "") else int(raw)
    except (TypeError, ValueError):
        raise PromptError(f"limit must be an integer: {raw!r}") from None
    if not 1 <= limit <= 500:
        raise PromptError("limit must be between 1 and 500")
    try:
        header, rows, warnings = load(args["output_path"], ctx.config)
    except ToolError as err:
        raise PromptError(f"{err.message}: {args['output_path']}") from None
    questions = header.get("questions") or infer_questions(rows)
    queue = stats.review_queue(rows.values(), questions, header.get("review_rule"))
    for e in queue:
        s = rows[e["id"]].get("state")
        if isinstance(s, str):
            e["state"] = s if len(s) <= QUEUE_STATE else s[:QUEUE_STATE - 1] + "…"
    body = {"output_path": args["output_path"], "n": len(rows), "needs_review": len(queue), "review_queue": queue[:limit],
            "per_question": stats.per_question(rows.values(), questions), "warnings": warnings}
    text = (f"Work the review queue of {args['output_path']}: {len(queue)} of {len(rows)} rows need review, the {min(limit, len(queue))} "
            f"least confident are attached, lowest confidence first.\n\n"
            f"For each entry read its state, the flagged question and the reason, then decide: accept the model's answer, correct it, or "
            f"reword the question if many rows share one reason. Use `batch_results` with `view: \"review\"` and its `next_cursor` for the "
            f"rest, `filter` (`min_confidence_below`, `question`, `choice`) and `sort_by: \"confidence\"` to look at one slice, and "
            f"`export` to hand rows to a human. `per_question` shows where the model is unsure overall.")
    return [_text(text), _resource("openjev://batch-review", body)]


START_BATCH = PromptSpec(
    "start_batch", "Start a batch", "Plan and run a batch job from a library template: dry run, confirm, run, resume, review.",
    (PromptArg("template", "template id (openjev://templates/{id})", required=True, complete=True),
     PromptArg("items_path", "file with the items to classify (csv, jsonl, json, txt)"),
     PromptArg("output_path", "new .jsonl file the batch writes its rows to")), _start)

REVIEW_BATCH = PromptSpec(
    "review_batch", "Review a batch", "Work the review queue of a finished or partial batch output.",
    (PromptArg("output_path", "batch output .jsonl", required=True),
     PromptArg("limit", f"queue entries to attach (default {REVIEW_LIMIT})")), _review)
