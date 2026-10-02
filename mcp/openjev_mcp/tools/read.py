"""The read tools ask, yes_no, classify, score over one run_read pipeline (spec 2.6-2.9)."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from openjev_mcp import wire
from openjev_mcp.audit import read_record
from openjev_mcp.derive import Band, derive_answers, is_escape
from openjev_mcp.errors import ToolError, invalid_input
from openjev_mcp.http import HttpResult
from openjev_mcp.lint import Finding, estimate, lint_request
from openjev_mcp.tools import ToolContext

TIMEOUT_THINK_FLOOR_MS = 120000
TIMEOUT_CAP_MS = 600000
THINK_MS = 15
REREAD_SAMPLES = 4
ESCAPE_LABEL = "other"
ESCAPE_TEXT = "anything else, or too vague to tell"
SCORE_HINT = "E013: use a list ordered lowest first"
SCORE_OBJECT = "levels is an object; the server returns 422 'Input should be a valid list'"
SUM_KEYS = ("model_ms", "server_ms", "total_ms")


@dataclass
class ReadOutcome:
    raw: dict                              # last server body
    answers: dict                          # derived, request order
    meta: dict                             # Meta (spec 2.2) over every request made
    lint_warnings: list[Finding]
    results: list[HttpResult]
    body: dict = field(default_factory=dict)   # last body sent


def compute_timeout(config, options: Mapping[str, Any], estimate: Mapping[str, Any]) -> int:
    if options.get("timeout_ms") is not None:
        return options["timeout_ms"]
    think = options.get("think") or 0
    ms = max(config.timeout_ms, 3 * (estimate.get("latency_ms_idle_approx") or 0) + THINK_MS * think)
    if think > 0:
        ms = max(ms, TIMEOUT_THINK_FLOOR_MS)
    return min(ms, TIMEOUT_CAP_MS)


def _lint_error(errors: list[Finding]) -> ToolError:
    parts = [f"{f.path}: {f.message}" + (f". Fix: {f.fix}" if f.fix else "") for f in errors]
    return invalid_input(errors[0].path, "; ".join(parts), errors[0].fix)


def _refuse_images(options: Mapping[str, Any], images: list | None) -> None:
    if not images:
        return
    for key in ("think", "sequential"):
        if options.get(key):
            raise invalid_input(key, f"{key} needs a text state; the server returns 400 with images",
                                f"drop {key} for image reads, or convert the UI to text and {key} on that")


def _usage(data: Any, key: str) -> int:
    usage = data.get("usage") if isinstance(data, dict) else None
    value = usage.get(key) if isinstance(usage, dict) else None
    return value if isinstance(value, int) else 0


def _dedupe(lines: list[str]) -> list[str]:
    return list(dict.fromkeys(lines))


def _meta(results: list[HttpResult], bodies: list[dict], chunks: int, timeout_ms: int, warnings: list[str]) -> dict:
    last = results[-1].data
    model = last.get("model") if isinstance(last, dict) else None
    meta: dict[str, Any] = {"model": model if isinstance(model, str) else bodies[-1].get("model"),
                            "request_ids": [r.request_id for r in results if r.request_id],
                            "body_hashes": [r.body_hash for r in results if r.body_hash],
                            "requests": len(results),
                            "latency_ms": round(sum(r.latency_ms for r in results))}
    timings = [r.server_timing for r in results if r.server_timing]
    if timings:
        sums = {k: round(sum(t[k] for t in timings if k in t), 1) for k in SUM_KEYS if any(k in t for t in timings)}
        if "total_ms" in sums:
            meta["server_ms"] = sums["total_ms"]
        meta["server_timing"] = sums
    meta.update(input_tokens=sum(_usage(r.data, "input_tokens") for r in results),
                output_tokens=sum(_usage(r.data, "output_tokens") for r in results),
                chunks_estimate=chunks, timeout_ms_used=timeout_ms, warnings=warnings)
    return meta


async def run_read(ctx: ToolContext, *, state: Any, questions: dict, options: Mapping[str, Any] | None,
                   band: Band, lint_mode: str = "warn", profile: str = "default", images: list | None = None,
                   reread: Callable[[dict], dict | None] | None = None, lint_in_meta: bool = True) -> ReadOutcome:
    """`reread` maps the derived answers of a read to option overrides for one more read, or None.
    lint_in_meta: lint warnings go to meta.warnings (tools without a lint field)."""
    options = dict(options or {})
    _refuse_images(options, images)
    model = options.get("model") or ctx.config.model
    body = wire.build_body(model, state, questions, options, images)
    limits = await ctx.limits.get()
    report = lint_request(body, limits=limits, profile=profile, autofix=False)
    if report.errors:
        raise _lint_error(report.errors)
    lint_warnings = report.warnings if lint_mode != "off" else []
    total = 2 if reread else 1
    bodies: list[dict] = []
    results: list[HttpResult] = []
    notes: list[str] = []
    timeout_used = 0
    known = limits.known_models
    answers: dict = {}
    while True:
        timeout = compute_timeout(ctx.config, options, estimate(body))
        timeout_used = max(timeout_used, timeout)
        res = await ctx.client.systemone(body, timeout_ms=timeout, think=body.get("think") or 0,
                                         has_images=bool(images), known_models=known)
        bodies.append(body)
        results.append(res)
        if res.retried:
            notes.append(f"retried after {res.retried['code']} (HTTP {res.retried['status']}): "
                         f"{res.retried['attempts']} attempts")
        done = len(results)
        eta = round(res.latency_ms * (total - done) / 1000)
        await ctx.progress.emit(done, total, f"{done}/{total} ok={done} err=0 eta {eta}s")
        raw = res.data.get("answers") if isinstance(res.data, dict) else None
        answers = derive_answers(raw, questions, band)
        extra = reread(answers) if reread and len(results) == 1 else None
        if not extra:
            break
        notes.append(f"grey first read: re-read once with {', '.join(f'{k} {v}' for k, v in extra.items())}")
        options = {**options, **extra}
        body = wire.build_body(model, state, questions, options, images)
    lines = [*notes, *(f.line() for f in ctx.warnings)]
    if lint_in_meta:
        lines += [f.line() for f in lint_warnings]
    meta = _meta(results, bodies, report.estimate["chunks"], timeout_used, _dedupe(lines))
    return ReadOutcome(res.data, answers, meta, lint_warnings, results, body)


def _audit(ctx: ToolContext, tool: str, out: ReadOutcome, decision: str | None = None) -> None:
    if ctx.audit:
        ctx.audit.write(read_record(tool, out.body, out, log_states=ctx.audit.log_states, decision=decision))


def _band(ctx: ToolContext, thresholds: Mapping[str, Any] | None = None) -> Band:
    cfg = ctx.config
    t = thresholds or {}
    yes_at = t.get("yes_at", cfg.band_yes_at)
    no_at = t.get("no_at", cfg.band_no_at)
    if not no_at < yes_at:
        raise invalid_input("thresholds", f"yes_at {yes_at} must be greater than no_at {no_at}",
                            "set no_at below yes_at")
    return Band(yes_at, no_at, t.get("choice_min_p", Band.choice_min_p))


async def ask(ctx: ToolContext, args: dict) -> dict:
    out = await run_read(ctx, state=args["state"], questions=args["questions"], options=args.get("options"),
                         band=_band(ctx, args.get("thresholds")), lint_mode=args.get("lint", "warn"),
                         lint_in_meta=False)
    _audit(ctx, "ask", out)
    res: dict[str, Any] = {"answers": out.answers, "meta": out.meta}
    if args.get("lint", "warn") != "off":
        res["lint"] = {"warnings": [f.to_dict() for f in out.lint_warnings]}
    if args.get("return_raw"):
        res["raw"] = out.raw
    return res


async def yes_no(ctx: ToolContext, args: dict) -> dict:
    band = _band(ctx, {k: args[k] for k in ("yes_at", "no_at") if k in args})
    question: dict[str, Any] = {"type": "noul", "instructions": args["claim"]}
    criteria = {k: args[f"{k}_means"] for k in ("true", "false") if args.get(f"{k}_means")}
    if criteria:
        question["criteria"] = criteria
    options = dict(args.get("options") or {})
    auto = options.get("samples") is None
    if auto:
        options["samples"] = 1

    def again(answers: dict) -> dict | None:
        return {"samples": REREAD_SAMPLES} if auto and answers["q"]["band"] == "grey" else None

    out = await run_read(ctx, state=args["state"], questions={"q": question}, options=options, band=band,
                         reread=again)
    ans = out.answers["q"]
    decision = "uncertain" if ans["band"] == "grey" else ans["band"]
    _audit(ctx, "yes_no", out, decision)
    return {"decision": decision, "p": ans["p"], "margin": ans["margin"],
            "thresholds_used": {"yes_at": band.yes_at, "no_at": band.no_at}, "meta": out.meta}


def classify_prepare(args: dict) -> tuple[dict, list[Finding]]:
    labels = args.get("labels")
    if isinstance(labels, list) and all(isinstance(x, str) for x in labels):
        finding = Finding("W202", "labels", "labels given as an array; each label is its own description",
                          "send {label: description of what an input with this label looks like}")
        return {**args, "labels": {x: x for x in labels}}, [finding]
    return args, []


def _escape(labels: Mapping[str, str], escape: Any) -> tuple[dict[str, str], set[str]]:
    """(criteria, escape keys): the escape option is added unless a label already is one."""
    criteria = dict(labels)
    if escape is False or any(is_escape(str(k).lower()) for k in labels):
        return criteria, set()
    if isinstance(escape, Mapping):
        label, text = escape["label"], escape["description"]
    else:
        label, text = ESCAPE_LABEL, ESCAPE_TEXT
    criteria.setdefault(label, text)
    return criteria, {label}


def _top_two(probs: Mapping[str, float]) -> tuple[str, float, str | None, float]:
    ranked = sorted(probs.items(), key=lambda kv: -kv[1])
    runner, p2 = ranked[1] if len(ranked) > 1 else (None, 0.0)
    return ranked[0][0], ranked[0][1], runner, p2


async def classify(ctx: ToolContext, args: dict) -> dict:
    labels, min_p = args["labels"], args.get("min_p", Band.choice_min_p)
    band = Band(ctx.config.band_yes_at, ctx.config.band_no_at, min_p)
    if args.get("multi_label"):
        return await _multi_label(ctx, args, band)
    criteria, escapes = _escape(labels, args.get("escape"))
    out = await run_read(ctx, state=args["state"], options=args.get("options"), band=band,
                         questions={"q": {"type": "choice", "instructions": args["question"], "criteria": criteria}})
    ans = out.answers["q"]
    top = ans["choice"]
    if top not in criteria:
        raise ToolError("OJ_PROTOCOL", f"answers.q: choice {top!r} is not one of the {len(criteria)} labels sent")
    res: dict[str, Any] = {"label": top, "abstained": False}
    if top in escapes or is_escape(top):
        res.update(label=None, abstained=True, reason=f"escape option '{top}' won (p={ans['p_top']:.4f})")
    elif ans["p_top"] < min_p:
        res.update(label=None, abstained=True, reason=f"p_top {ans['p_top']:.4f} < min_p {min_p:g}")
    res.update(top=top, p_top=ans["p_top"], runner_up=ans["runner_up"], margin=ans["margin"],
               probabilities=ans["probabilities"], confidence=ans["confidence"], meta=out.meta)
    _audit(ctx, "classify", out, res["label"])
    return res


async def _multi_label(ctx: ToolContext, args: dict, band: Band) -> dict:
    labels, min_p = args["labels"], band.choice_min_p
    questions = {label: {"type": "noul", "instructions": f"Does the text fit the label '{label}' ({text})?",
                         "criteria": {"true": text, "false": f"the text does not fit '{label}'"}}
                 for label, text in labels.items()}
    out = await run_read(ctx, state=args["state"], questions=questions, options=args.get("options"), band=band)
    probs = {label: out.answers[label]["p"] for label in labels}
    top, p_top, runner, p2 = _top_two(probs)
    res: dict[str, Any] = {"label": top, "abstained": False}
    if p_top < min_p:
        res.update(label=None, abstained=True, reason=f"p_top {p_top:.4f} < min_p {min_p:g}")
    res.update(top=top, p_top=p_top, runner_up=runner, margin=p_top - p2, probabilities=probs,
               labels_multi={label: {"p": out.answers[label]["p"], "band": out.answers[label]["band"]}
                             for label in labels},
               meta=out.meta)
    _audit(ctx, "classify", out, res["label"])
    return res


def score_prepare(args: dict) -> tuple[dict, list[Finding]]:
    if isinstance(args.get("levels"), dict):
        raise invalid_input("levels", SCORE_OBJECT, hint=SCORE_HINT)
    return args, []


async def score(ctx: ToolContext, args: dict) -> dict:
    levels = args["levels"]
    out = await run_read(ctx, state=args["state"], options=args.get("options"),
                         band=Band(ctx.config.band_yes_at, ctx.config.band_no_at),
                         questions={"q": {"type": "score", "instructions": args["question"], "criteria": levels}})
    ans = {k: v for k, v in out.answers["q"].items() if k != "type"}
    if args.get("one_based"):
        ans["score"] += 1
        ans["level"] += 1
    _audit(ctx, "score", out, str(ans["level"]))
    return {**ans, "meta": out.meta}
