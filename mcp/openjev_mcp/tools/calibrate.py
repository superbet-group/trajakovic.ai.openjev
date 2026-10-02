"""calibrate (spec 2.15): labelled examples -> accuracy, separation, fitted thresholds, reliability, drift, audit record.
Sources: questions+examples, recipe+examples, case_file (run_cases format) or from_batch (a finished batch output plus a
labels file, no request). Reads go through batch.runner (cursor, concurrency as in `batch`); a chunked run keeps its rows
in a work file under OPENJEV_MCP_AUDIT_DIR. Also exports the prompts AUDIT_QUESTION and EXPLAIN_ANSWER."""
from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any

import anyio

from openjev_mcp import audit_store, calibration as cal, schemas, wire
from openjev_mcp.batch import cursor as cur, importers
from openjev_mcp.batch.runner import BatchJob, run_batch
from openjev_mcp.batch.stats import in_audit
from openjev_mcp.batch.store import now_iso, read_output
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError, invalid_input
from openjev_mcp.paths import read_text, resolve_read
from openjev_mcp.prompts import PromptArg, PromptError, PromptSpec
from openjev_mcp.tools import ToolContext, ToolSpec

SPEC = "1.2"
SOURCES = ("questions", "recipe", "case_file", "from_batch")
TRUE, FALSE = {"true", "yes", "1", "y", "t"}, {"false", "no", "0", "n", "f"}

INPUT: dict = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "questions": {"$ref": "#/$defs/QuestionSet"},
        "recipe": {"type": "string"},
        "examples": {"type": "array", "items": {"type": "object", "required": ["state", "label"], "properties": {
            "id": {"type": "string"}, "state": {"$ref": "#/$defs/State"},
            "label": {"type": "object", "description": "question id -> true/false (noul), option key (choice), level index (score)"}}}},
        "case_file": {"type": "string", "description": "run_cases.py format, inside the allowed roots (2.2); expect.answers become labels (noul_gte -> true, noul_lte -> false, choice -> key)"},
        "options": {"$ref": "#/$defs/ReadOptions"},
        "target": {"type": "object", "properties": {"max_errors": {"type": "integer", "default": 0}, "min_precision": {"type": "number"}, "min_coverage": {"type": "number"}}},
        "holdout": {"type": "number", "default": 0, "minimum": 0, "exclusiveMaximum": 1, "description": "fraction held out as drift canary, never used for fitting"},
        "store": {"type": "string", "description": "path of the audit record (.json, inside the allowed roots, 2.2) to write/compare"},
        "compare_to": {"type": "string", "description": "previous audit record (allowed roots, 2.2): report flips and gap change"},
        "from_batch": {"type": "object", "additionalProperties": False, "required": ["output_path", "labels_path", "label_fields"], "properties": {
            "output_path": {"type": "string"},
            "labels_path": {"type": "string", "description": ".csv or .jsonl with an id column and one label column per question"},
            "id_field": {"type": "string", "default": "id"},
            "label_fields": {"type": "object", "additionalProperties": {"type": "string"}, "description": "question id -> label column"}}},
        "concurrency": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
        "max_items_per_call": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
        "cursor": {"type": "string", "description": "as in batch (2.11)"},
    },
    "description": "Give exactly one of: questions + examples, recipe + examples, case_file, or from_batch (questions come from the batch header; no HTTP request is made). Checked in code (OJ_INVALID_INPUT), not with a root oneOf (2.2). Labels: noul true/false (or yes/no, 1/0), choice option key, score level index.",
}

_BIN = {"type": "object", "properties": {"lo": {"type": "number"}, "hi": {"type": "number"}, "count": {"type": "integer"},
                                         "acc": {"type": ["number", "null"]}, "conf": {"type": ["number", "null"]}}}
_HIST = {"type": "array", "minItems": 20, "maxItems": 20, "items": {"type": "integer"}}
OUTPUT: dict = {
    "type": "object", "required": ["model_resolved", "n", "per_question", "items"],
    "properties": {
        "model_resolved": {"type": "string"}, "question_hash": {"type": "string"}, "n": {"type": "integer"},
        "per_question": {"type": "object", "additionalProperties": {"type": "object", "properties": {
            "type": {}, "accuracy_at_0.5": {}, "separable": {"type": ["boolean", "null"]}, "max_negative": {}, "min_positive": {}, "gap": {},
            "t_fit": {}, "suggested_band": {}, "precision_coverage": {"type": "array"}, "overlap_ids": {"type": "array"},
            "confusion": {"type": "object", "description": "choice"},
            "ladder_monotonic": {"type": ["boolean", "null"], "description": "score"},
            "most_borderline": {}, "zero_error_upper_bound_95": {"description": "3/n (rule of three) when 0 errors"},
            "calibration": {"type": "object", "properties": {"bins": {"type": "array", "minItems": 10, "maxItems": 10, "items": _BIN},
                                                              "brier": {"type": ["number", "null"]}, "ece": {"type": ["number", "null"]}}},
            "distributions": {"type": "object", "properties": {"confidence_hist": _HIST, "entropy_hist": _HIST, "max_entropy": {"type": "number"}}}}}},
        "items": {"type": "array"}, "drift": {"type": "object", "properties": {"model_changed": {}, "flipped_ids": {}, "gap_delta": {}}},
        "warnings": {"type": "array"},
    },
}


# ---------------------------------------------------------------- labels

def _label(kind: str, raw: Any, where: str, question: dict | None) -> Any:
    if kind == "noul":
        text = str(raw).strip().lower() if not isinstance(raw, bool) else str(raw).lower()
        if text in TRUE:
            return True
        if text in FALSE:
            return False
        raise invalid_input(where, f"noul label {raw!r} is not true/false (or yes/no, 1/0)")
    if kind == "score":
        try:
            level = int(str(raw).strip())
        except ValueError:
            raise invalid_input(where, f"score label {raw!r} is not a level index") from None
        crit = (question or {}).get("criteria")
        if isinstance(crit, list) and not 0 <= level < len(crit):
            raise invalid_input(where, f"score label {level} is outside 0..{len(crit) - 1}")
        return level
    key = str(raw)
    crit = (question or {}).get("criteria")
    if isinstance(crit, dict) and key not in crit:
        raise invalid_input(where, f"choice label {key!r} is not an option key", "options: " + ", ".join(crit))
    return key


def _example_labels(examples: list[dict], questions: dict | None) -> tuple[list[importers.BatchItem], dict[str, dict]]:
    """(items, raw labels {id: {qid: label}}); labels are coerced here when the question types are known."""
    items, labels = [], {}
    for i, ex in enumerate(examples, 1):
        rid = str(ex["id"]) if ex.get("id") not in (None, "") else f"ex-{i}"
        if rid in labels:
            raise invalid_input(f"examples[{i - 1}].id", f"duplicate example id {rid!r}")
        for qid in ex.get("label") or {}:
            if questions is not None and qid not in questions:
                raise invalid_input(f"examples[{i - 1}].label.{qid}", f"no question {qid!r} in questions")
        items.append(importers.BatchItem(i, rid, ex["state"]))
        labels[rid] = dict(ex.get("label") or {})
    if questions is not None:
        labels = _typed(labels, {q: v["type"] for q, v in questions.items()}, questions)
    return items, labels


def _typed(labels: dict[str, dict], kinds: dict[str, str], questions: dict | None) -> dict[str, dict]:
    """Coerce labels whose question type is only known from the answers (recipe, from_batch)."""
    return {rid: {q: _label(kinds[q], v, f"labels.{rid}.{q}", (questions or {}).get(q)) for q, v in got.items() if q in kinds}
            for rid, got in labels.items()}


def _case_file(path: str, config: Config) -> tuple[list[importers.BatchItem], dict, dict[str, dict], dict, list[str]]:
    text, _, _ = read_text(resolve_read(path, config))
    try:
        doc = json.loads(text)
        cases = doc["cases"] if isinstance(doc, dict) else doc
        assert isinstance(cases, list)
    except (ValueError, KeyError, AssertionError):
        raise invalid_input("case_file", "not a run_cases.py file: expected {\"cases\": [...]}") from None
    items, labels, questions, options, warnings = [], {}, None, {}, []
    for case in cases:
        req = case.get("request") or {}
        exp = case.get("expect") or {}
        cid = str(case.get("id") or f"case-{len(items) + 1}")
        if case.get("endpoint", "/v1/systemone") != "/v1/systemone" or "state" not in req or exp.get("status", 200) != 200:
            continue
        got = {}
        for qid, rules in (exp.get("answers") or {}).items():
            if "noul_gte" in rules:
                got[qid] = True
            elif "noul_lte" in rules:
                got[qid] = False
            elif "choice" in rules:
                got[qid] = rules["choice"]
        if not got:
            warnings.append(f"case {cid}: no noul_gte/noul_lte/choice expectation, skipped")
            continue
        qs = req.get("questions") or {}
        if questions is None:
            questions, options = qs, {k: req[k] for k in (*wire.OPTION_ORDER, "model") if req.get(k) is not None}
        elif cal.question_hash(qs) != cal.question_hash(questions):
            raise invalid_input("case_file", f"case {cid}: questions differ from the first case; calibrate one question set per file")
        items.append(importers.BatchItem(len(items) + 1, cid, req["state"]))
        labels[cid] = {q: _label((qs.get(q) or {}).get("type"), v, f"case_file.{cid}.{q}", qs.get(q)) for q, v in got.items()}
    if not items:
        raise invalid_input("case_file", "no case with a noul_gte, noul_lte or choice expectation")
    return items, questions, labels, options, warnings


def _labels_file(spec: dict, config: Config) -> tuple[dict[str, dict], list[str]]:
    path, idf, fields = spec["labels_path"], spec.get("id_field", "id"), spec["label_fields"]
    real = resolve_read(path, config)
    text, _, _ = read_text(real)
    ext, rows = real.suffix.lower(), []
    if ext in (".csv", ".tsv", ".tab"):
        grid, _ = importers.parse_csv(text, "\t" if ext != ".csv" else ",")
        head = [h.strip() for h in grid[0]] if grid else []
        rows = [dict(zip(head, r)) for r in grid[1:]]
    elif ext in (".jsonl", ".ndjson", ".jsonlines"):
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    raise invalid_input("from_batch.labels_path", f"line {n} is not valid JSON") from None
    else:
        raise invalid_input("from_batch.labels_path", "labels_path must be .csv, .tsv or .jsonl")
    if rows and idf not in rows[0]:
        raise invalid_input("from_batch.id_field", f"no column {idf!r} in the labels file", "columns: " + ", ".join(map(str, rows[0])))
    for qid, col in fields.items():
        if rows and col not in rows[0]:
            raise invalid_input(f"from_batch.label_fields.{qid}", f"no column {col!r} in the labels file", "columns: " + ", ".join(map(str, rows[0])))
    out, warns = {}, []
    for r in rows:
        got = {q: r[c] for q, c in fields.items() if r.get(c) not in (None, "")}
        if got:
            out[str(r[idf])] = got
    return out, warns


# ---------------------------------------------------------------- reading

class _Work:
    """Rows of a chunked run, kept between calls (JSONL under the audit dir)."""

    def __init__(self, config: Config, run: str):
        if not config.audit_dir:
            raise invalid_input("max_items_per_call", "a chunked calibrate run keeps its rows under OPENJEV_MCP_AUDIT_DIR",
                                "set OPENJEV_MCP_AUDIT_DIR or raise max_items_per_call to the example count")
        self.path = Path(config.audit_dir) / "work" / f"calibrate-{run}.jsonl"

    @contextlib.contextmanager
    def lock(self):
        """Exclusive non-blocking flock on a sidecar file (the work file itself is unlinked by reset)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise invalid_input("cursor", "calibrate run in progress", "wait for the other call with the same arguments") from None
            yield
        finally:
            os.close(fd)

    def size(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def reset(self, size: int = 0) -> None:
        if size:
            os.truncate(self.path, size)
        elif self.path.exists():
            self.path.unlink()

    def append(self, rows: list[dict]) -> tuple[int, int]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "ab") as fh:
            for r in rows:
                fh.write((wire.dumps_text(r) + "\n").encode("utf-8"))
        return self.size(), len(self.rows())

    def rows(self) -> list[dict]:
        return [json.loads(x) for x in self.path.read_text("utf-8").splitlines() if x.strip()] if self.path.exists() else []


def _slim(row: dict) -> dict:
    out = {"id": row["id"], "status": row["status"], "model": row.get("model"), "answers": row.get("answers")}
    if row["status"] != "ok":
        out["error"] = row.get("error")
    return out


async def _read_batch(ctx: ToolContext, args: dict, questions: dict, items: list, offset: int, think: bool) -> tuple[list[dict], int | None, dict]:
    opts = dict(args.get("options") or {})

    def job(its, start, per):
        return BatchJob(items=its, questions=questions, options=opts, sampling="server_default", regrey_samples=0,
                        concurrency=args.get("concurrency", 1), include_state=False, max_items_per_call=per, start_offset=start)

    run = await run_batch(ctx, job(items, offset, args.get("max_items_per_call", 25)))
    rows = [_slim(r) for r in run.rows if r["status"] != "skipped"]
    if think:
        by_id = {it.id: it for it in items}
        again = [by_id[r["id"]] for r in rows if r["status"] == "ok"]
        if again:
            second = await run_batch(ctx, job(again, 0, len(again)))
            got = {r["id"]: r.get("answers") for r in second.rows if r["status"] == "ok"}
            for r in rows:
                if r["id"] in got:
                    r["repeat"] = got[r["id"]]
    return rows, run.next_offset, {**run.status, "lint_warnings": [f.line() for f in run.lint_warnings], "warnings": run.warnings}


async def _read_recipe(ctx: ToolContext, args: dict, rec, items: list, offset: int) -> tuple[list[dict], int | None, dict]:
    from openjev_mcp.recipes import engine
    per = args.get("max_items_per_call", 25)
    chunk = items[offset:offset + per]
    opts = dict(args.get("options") or {})
    config = ctx.config
    if opts.get("model"):
        import dataclasses
        config = dataclasses.replace(config, model=opts["model"])
    read_opts = {k: v for k, v in opts.items() if k in engine.OPTION_KEYS} or None
    rows: list[dict | None] = [None] * len(chunk)
    limiter = anyio.CapacityLimiter(args.get("concurrency", 1))

    async def one(i: int, it) -> None:
        async with limiter:
            try:
                out = await engine.run_recipe(rec, it.state, client=ctx.client, config=config, timeout_ms=opts.get("timeout_ms"),
                                              read_options=read_opts)
            except (engine.RecipeError, ToolError) as err:
                rows[i] = {"id": it.id, "status": "error", "model": None, "answers": None,
                           "error": err.to_dict() if isinstance(err, ToolError) else {"code": "OJ_INVALID_INPUT", "message": str(err)}}
                return
        if out.error is not None or not out.answers:
            rows[i] = {"id": it.id, "status": "error", "model": None, "answers": None,
                       "error": out.error.to_dict() if out.error else {"code": "OJ_PROTOCOL", "message": "recipe returned no answers"}}
        else:
            rows[i] = {"id": it.id, "status": "ok", "model": out.meta.get("model") or config.model, "answers": out.answers}

    async with anyio.create_task_group() as tg:
        for i, it in enumerate(chunk):
            tg.start_soon(one, i, it)
    nxt = offset + len(chunk)
    return [r for r in rows if r], (nxt if nxt < len(items) else None), {"total": len(items)}


# ---------------------------------------------------------------- analysis

def _obs(kind: str, a: dict) -> dict:
    if kind == "noul":
        p = a["p"]
        return {"pred": p >= 0.5, "p": p, "conf": max(p, 1 - p), "ent": cal.entropy([p, 1 - p]), "K": 2}
    probs = a.get("probabilities") or {}
    if kind == "choice":
        p = a["p_top"]
        return {"pred": a["choice"], "p": p, "conf": p, "ent": a.get("entropy", cal.entropy(probs.values())), "K": len(probs)}
    level = a["level"]
    p = probs.get(str(level), probs.get(level, max(probs.values(), default=1.0)))
    return {"pred": level, "p": p, "conf": p, "ent": cal.entropy(probs.values()), "K": len(probs), "score": a.get("score", level)}


def _rp(p: float) -> float:
    return float(f"{p:.2g}") if p < 0.001 else round(p, 4)


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def _agree(kind: str, a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return True
    return _obs(kind, a)["pred"] == _obs(kind, b)["pred"]


def _question(qid: str, kind: str, k: int | None, obs: list[dict], target: dict | None, warnings: list[str], tag: str) -> dict:
    n = len(obs)
    for o in obs:
        o["correct"] = o["pred"] == o["label"]
    errors = sum(not o["correct"] for o in obs)
    k = k or max((o["K"] for o in obs), default=2)
    max_h = math.log(2) if kind == "noul" else math.log(max(k, 2))
    pairs = [(o["conf"], o["correct"]) for o in obs]
    bins = cal.reliability_bins(pairs)
    out: dict[str, Any] = {"type": kind, "n": n}
    pos = [(o["id"], o["p"]) for o in obs if o["label"] is True]
    neg = [(o["id"], o["p"]) for o in obs if o["label"] is False]
    if kind == "noul":
        fit = cal.fit_noul(pos, neg)
        both = bool(pos and neg)
        out.update(n_pos=len(pos), n_neg=len(neg), **{"accuracy_at_0.5": _r(1 - errors / n)}, separable=fit["separable"],
                   max_negative=_r(fit["max_negative"]), min_positive=_r(fit["min_positive"]), gap=_r(fit["gap"]), t_fit=_r(fit["t_fit"]))
        band = cal.suggested_band(pos, neg, n)
        out["suggested_band"] = {k_: _r(v) for k_, v in band.items()}
        if not both:
            warnings.append(f"{tag}needs both true and false labels to fit a threshold")
        elif not fit["separable"]:
            out["precision_coverage"] = [{k_: _r(v) if k_ != "errors" else v for k_, v in row.items()} for row in cal.precision_coverage(pos, neg)]
        out["overlap_ids"] = fit["overlap_ids"]
        if target is not None and both:
            grid = sorted({*cal.GRID, *([fit["t_fit"]] if fit["t_fit"] is not None else [])})
            hit = next((r for r in cal.precision_coverage(pos, neg, grid)
                        if r["errors"] <= target.get("max_errors", 0) and (r["precision"] or 0) >= target.get("min_precision", 0)
                        and (r["coverage"] or 0) >= target.get("min_coverage", 0)), None)
            out["target"] = {"met": hit is not None, "t": _r(hit["t"]) if hit else None}
    else:
        out["accuracy"] = _r(1 - errors / n)
        out["confusion"] = cal.confusion((o["label"], o["pred"]) for o in obs)
        if kind == "score":
            out["ladder_monotonic"] = cal.ladder_monotonic((o["label"], o["score"]) for o in obs)
        if target is not None:
            out["target"] = {"met": errors <= target.get("max_errors", 0), "t": None}
    border = min(obs, key=lambda o: (o["conf"], o["id"]))
    out["most_borderline"] = border["id"]
    out["zero_error_upper_bound_95"] = round(min(1.0, 3 / n), 3) if errors == 0 else None
    out["calibration"] = {"bins": [{**b, "lo": _r(b["lo"]), "hi": _r(b["hi"]), "acc": _r(b["acc"]), "conf": _r(b["conf"])} for b in bins],
                          "brier": _r(cal.brier(pairs)), "ece": _r(cal.ece(bins))}
    out["distributions"] = {"confidence_hist": cal.confidence_hist(o["conf"] for o in obs),
                            "entropy_hist": cal.entropy_hist((o["ent"] for o in obs), max_h), "max_entropy": _r(max_h)}
    if n < 30:
        warnings.append(f"{tag}n={n}: smoke test only" + (f"; 0 errors in {n} bounds the error rate at ~{round(300 / n)}% (rule of three)" if errors == 0 else ""))
    return out


# deviation: 2.15 items: with one question label/p are scalars (as the verified example), with several they are {question: value}; choice/score add pred
# deviation: 2.11 cursor: a chunked calibrate run keeps its rows in OPENJEV_MCP_AUDIT_DIR/work (no output_path); partial calls return per_question {} and next_cursor
# deviation: 2.15 reads use sampling server_default (no fast samples=1 / regrey); recipe source: example.state = recipe inputs, labels name answer ids
def analyze(rows: list[dict], labels: dict[str, dict], questions: dict | None, args: dict, *, header_hash: str | None = None,
            think: bool = False) -> dict:
    """The 2.15 report from read rows ({id, status, model, answers, repeat?}) and labels {id: {qid: label}}."""
    warnings: list[str] = []
    ok = [r for r in rows if r["status"] == "ok" and r.get("answers")]
    errs = [r for r in rows if r["status"] != "ok"]
    if errs:
        warnings.append(f"{len(errs)} example(s) failed to read and were left out: " + ", ".join(str(r["id"]) for r in errs[:10]))
    models = Counter(r["model"] for r in ok if r.get("model"))
    if len(models) > 1:
        warnings.append("the resolved model changed during the run: " + ", ".join(models))
    model = models.most_common(1)[0][0] if models else (args.get("_model") or "")
    kinds: dict[str, str] = {}
    for qid, q in (questions or {}).items():
        kinds[qid] = q["type"]
    for r in ok:
        for qid, a in r["answers"].items():
            kinds.setdefault(qid, a.get("type"))
    ks = {qid: (len(q["criteria"]) if isinstance(q.get("criteria"), (list, dict)) else None) for qid, q in (questions or {}).items()}
    holdout = args.get("holdout") or 0
    held = {r["id"] for r in ok if holdout and in_audit(0, str(r["id"]), holdout)}
    per_q, unstable = {}, {}
    for qid, kind in kinds.items():
        obs = []
        for r in ok:
            lab = labels.get(str(r["id"]), {})
            if qid not in lab or qid not in r["answers"] or r["id"] in held:
                continue
            o = {"id": str(r["id"]), "label": lab[qid], **_obs(kind, r["answers"][qid])}
            if think and not _agree(kind, r["answers"][qid], (r.get("repeat") or {}).get(qid)):
                unstable.setdefault(qid, []).append(o["id"])
            obs.append(o)
        if not obs:
            warnings.append(f"{qid}: no labelled answers")
            continue
        per_q[qid] = _question(qid, kind, ks.get(qid), obs, args.get("target"), warnings, f"{qid}: " if len(kinds) > 1 else "")
        if qid in unstable:
            per_q[qid]["non_reproducible"] = unstable[qid]
    if unstable:
        warnings.append(f"think reads disagreed on {len({i for v in unstable.values() for i in v})} item(s): non-reproducible (2.6)")
    qids = list(per_q)
    items = []
    for r in ok:
        lab = labels.get(str(r["id"]), {})
        got = [q for q in qids if q in lab and q in r["answers"]]
        if not got:
            continue
        o = {q: _obs(kinds[q], r["answers"][q]) for q in got}
        if len(qids) == 1:
            q = got[0]
            item = {"id": str(r["id"]), "label": lab[q], "p": _rp(o[q]["p"])}
            if kinds[q] != "noul":
                item["pred"] = o[q]["pred"]
        else:
            item = {"id": str(r["id"]), "label": {q: lab[q] for q in got}, "p": {q: _rp(o[q]["p"]) for q in got},
                    "pred": {q: o[q]["pred"] for q in got if kinds[q] != "noul"}}
        if r["id"] in held:
            item["holdout"] = True
        if any(r["id"] in v for v in unstable.values()):
            item["non_reproducible"] = True
        item["_model"] = r.get("model")
        items.append(item)
    report: dict[str, Any] = {"model_resolved": model, "n": len(ok)}
    qh = cal.question_hash(questions) if questions else header_hash
    if qh:
        report["question_hash"] = qh
    report["per_question"] = per_q
    report["items"] = items
    if holdout:
        report["holdout"] = {"fraction": holdout, "n": len(held), "ids": sorted(map(str, held))}
    if args.get("target"):
        report["target_met"] = bool(per_q) and all(v.get("target", {}).get("met") for v in per_q.values())
        if not report["target_met"]:
            warnings.append("target not met")
    report["warnings"] = warnings
    return report


def _public(report: dict) -> dict:
    return {**report, "items": [{k: v for k, v in it.items() if k != "_model"} for it in report["items"]]}


def _record(report: dict, questions: dict | None, args: dict) -> dict:
    items = [{**{k: v for k, v in it.items() if k != "_model"}, "model": it["_model"]} for it in report["items"]]
    return {"openjev_mcp": audit_store.MARK, "v": 1, "spec": SPEC, "created_at": now_iso(), **{**_public(report), "items": items},
            **({"questions": questions} if questions else {}), "options": args.get("options") or {}}


# ---------------------------------------------------------------- tool

def _check_sources(args: dict) -> str:
    given = [s for s in SOURCES if args.get(s) is not None]
    if len(given) != 1:
        raise invalid_input("arguments", "give exactly one of: questions + examples, recipe + examples, case_file, from_batch",
                            "got: " + (", ".join(given) or "none"))
    src = given[0]
    if src in ("questions", "recipe"):
        if not args.get("examples"):
            raise invalid_input("examples", f"{src} needs examples [{{id, state, label}}]")
    elif args.get("examples") is not None:
        raise invalid_input("examples", f"examples go with questions or recipe, not {src}")
    return src


async def calibrate(ctx: ToolContext, args: dict) -> dict:
    with contextlib.ExitStack() as stack:   # holds the chunked run's work lock for the whole call
        return await _calibrate(ctx, args, stack)


async def _calibrate(ctx: ToolContext, args: dict, stack: contextlib.ExitStack) -> dict:
    config = ctx.config
    src = _check_sources(args)
    holdout = args.get("holdout") or 0
    if not 0 <= holdout < 1:
        raise invalid_input("holdout", "holdout is a fraction in [0, 1)")
    think = bool((args.get("options") or {}).get("think"))
    questions: dict | None = args.get("questions")
    extra: list[str] = []
    header_hash = None
    rec = None
    labels: dict[str, dict]
    if src == "from_batch":
        spec = args["from_batch"]
        header, out_rows, _, warns = read_output(spec["output_path"], config)
        labels, _ = _labels_file(spec, config)
        questions = (header or {}).get("questions")
        header_hash = (header or {}).get("question_hash")
        rows = [_slim(r) for i, r in out_rows.items() if i in labels and r.get("status") == "ok"]
        kinds = {q: a.get("type") for r in rows for q, a in (r["answers"] or {}).items()}
        labels = _typed(labels, kinds, questions)
        extra += warns
        miss = [i for i in labels if i not in out_rows]
        if miss:
            extra.append(f"{len(miss)} labelled id(s) are not in the batch output: " + ", ".join(miss[:10]))
        report = analyze(rows, labels, questions, args, header_hash=header_hash)
        report["warnings"] = extra + report["warnings"]
    else:
        opts = dict(args.get("options") or {})
        if src == "case_file":
            items, questions, labels, case_opts, extra = _case_file(args["case_file"], config)
            args = {**args, "options": {**case_opts, **opts}}
        elif src == "recipe":
            from openjev_mcp.recipes.engine import RecipeError
            from openjev_mcp.recipes.registry import load_all
            reg = load_all(config)
            try:
                rec = reg.get(args["recipe"])
            except RecipeError as e:
                raise invalid_input("recipe", str(e), f"one of: {', '.join(reg.ids())}") from None
            items, labels = _example_labels(args["examples"], None)
            header_hash = cal.question_hash(reg.document(args["recipe"]))
        else:
            items, labels = _example_labels(args["examples"], questions)
        qh = cal.question_hash(questions) if questions else header_hash
        total, per = len(items), args.get("max_items_per_call", 25)
        args_h = cur.args_hash({k: v for k, v in args.items()})
        run = wire.canonical_hash({"calibrate": args_h})[7:23]
        chunked = args.get("cursor") is not None or total > per
        work = _Work(config, run) if chunked else None
        if work is not None:
            stack.enter_context(work.lock())
        offset = 0
        if args.get("cursor") is not None:
            tok = cur.decode(args["cursor"])
            cur.check(tok, run_id=run, args_hash=args_h, out_size=work.size())
            work.reset(tok["out"]["bytes"])
            offset = tok["offset"]
        elif work is not None:
            work.reset()
        if rec is not None:
            rows, nxt, status = await _read_recipe(ctx, args, rec, items, offset)
        else:
            rows, nxt, status = await _read_batch(ctx, args, questions, items, offset, think)
        extra += status.pop("lint_warnings", []) + status.pop("warnings", [])
        if work is not None:
            size, lines = work.append(rows)
            if nxt is not None:
                done = work.rows()
                model = next((r["model"] for r in done if r.get("model")), config.model)
                return {"model_resolved": model, "n": sum(r["status"] == "ok" for r in done), "per_question": {}, "items": [],
                        **({"question_hash": qh} if qh else {}),
                        "status": {**status, "done": len(done), "total": total},
                        "next_cursor": cur.encode(run, nxt, args_h, size, lines),
                        "warnings": extra + [f"partial: {len(done)}/{total} read; call again with next_cursor"]}
            rows = work.rows()
            work.reset()
        report = analyze(rows, labels if rec is None else _typed(labels, {q: a.get("type") for r in rows if r.get("answers")
                                                                         for q, a in r["answers"].items()}, None),
                         questions, {**args, "_model": config.model}, header_hash=header_hash, think=think)
        report["warnings"] = extra + report["warnings"]
    prev = audit_store.load_record(args["compare_to"], config) if args.get("compare_to") else None
    if prev is not None:
        report["drift"] = cal.drift(prev, report)
        if report["drift"]["model_changed"]:
            report["warnings"].append(f"resolved model changed: {prev.get('model_resolved')} -> {report['model_resolved']}")
        if report["drift"]["question_changed"]:
            report["warnings"].append("question_hash changed: stored thresholds no longer apply")
    if args.get("store"):
        report["stored"] = str(audit_store.write_record(args["store"], _record(report, questions, args), config))
    return _public(report)


def register(config: Config) -> ToolSpec:
    schemas.register_tool_schemas("calibrate", INPUT, OUTPUT)
    annotations = {"title": "Calibrate a question set", "readOnlyHint": False, "destructiveHint": False,
                   "idempotentHint": False, "openWorldHint": False}
    return ToolSpec(
        "calibrate", "Calibrate a question set",
        "Run a question set or recipe against labelled examples (or score a finished batch against a labels file): "
        "accuracy, separation, fitted thresholds, borderline items, confusion, ladder monotonicity, reliability bins, Brier, "
        "ECE and histograms. store writes a reproducible audit record, compare_to reports flips and a changed resolved model.",
        schemas.INPUT_SCHEMAS["calibrate"], schemas.OUTPUT_SCHEMAS["calibrate"], annotations, calibrate)


# ---------------------------------------------------------------- prompts

def _msg(text: str) -> list[dict]:
    return [{"role": "user", "content": {"type": "text", "text": text}}]


def _question_set(doc: Any) -> dict:
    q = doc.get("questions") if isinstance(doc, dict) and isinstance(doc.get("questions"), dict) else doc
    if not (isinstance(q, dict) and q and all(isinstance(v, dict) and v.get("type") for v in q.values())):
        raise PromptError("schema_path: expected a question set or a request with questions")
    return q


async def _audit_question(args: dict, ctx: ToolContext) -> list[dict]:
    try:
        text, _, _ = read_text(resolve_read(args["schema_path"], ctx.config))
        questions = _question_set(json.loads(text))
        real = resolve_read(args["labels_path"], ctx.config)
        head = read_text(real)[0].splitlines()[:1]
    except ToolError as e:
        raise PromptError(f"{e.message}") from None
    except ValueError:
        raise PromptError("schema_path: not valid JSON") from None
    cols: list[str] = []
    if head and real.suffix.lower() in (".csv", ".tsv", ".tab"):
        cols = [c.strip() for c in head[0].replace("\t", ",").split(",")]
    elif head:
        try:
            cols = list(json.loads(head[0]))
        except (ValueError, TypeError):
            cols = []
    fields = {q: (q if q in cols else f"<{q} label column>") for q in questions}
    qh = cal.question_hash(questions)
    call = {"from_batch": {"output_path": "<batch output .jsonl>", "labels_path": args["labels_path"], "label_fields": fields},
            "store": f"<audit dir>/{qh[7:19]}.json"}
    table = "| question | type | n | accuracy | separable | gap | t_fit | no_at | yes_at |\n|---|---|---|---|---|---|---|---|---|\n" + "\n".join(
        f"| {q} | {v['type']} | | | | | | | |" for q, v in questions.items())
    return _msg(
        f"Audit the question set in {args['schema_path']} against the labels in {args['labels_path']}.\n"
        f"question_hash: {qh} (a changed hash invalidates stored thresholds; stored records are readable at openjev://audits/{qh}).\n\n"
        "1. Call lint on the question set and fix errors first.\n"
        f"2. Read the labelled states once: call batch with items_file {{path: {args['labels_path']!r}}}, the questions from "
        f"{args['schema_path']} and a new output_path (.jsonl), repeating with next_cursor until it completes.\n"
        f"3. Call calibrate (no new reads):\n```json\n{json.dumps(call, indent=1)}\n```\n"
        "4. Paste the thresholds into this table and state which questions are safe to automate (separable, enough n, "
        "zero_error_upper_bound_95 acceptable) and which need a second signal:\n\n" + table + "\n\n"
        "Rules: n < 30 is a smoke test only; if not separable use precision_coverage and overlap_ids; if the resolved model "
        "changed, run again with compare_to the previous record.")


EXPLAIN = (
    "How to read an OpenJev answer:\n"
    "- noul: p = P(true). There is no separate confidence; certainty is how far p is from 0.5 (margin |2p-1|). band yes/no/grey "
    "comes from the thresholds in use; grey means do not act automatically.\n"
    "- choice: p_top is the probability of the chosen option; its confidence depends on the number of options K (chance is "
    "1/K, so 0.5 means more with K=10 than with K=2). An escape option or p_top below the minimum is an abstention.\n"
    "- score: levels are 0-indexed (level 0 is the first criterion); score is the expected level, level the argmax, "
    "confidence the probability of the argmax. A high spread or bimodal flag means the ladder splits.\n"
    "- samples average N reads; think answers can differ between reads.\n\n"
    "Explain each number above in plain words and say whether to act: act on yes/no outside the grey band with a model "
    "that matches the calibrated one; otherwise ask for a second signal or a human.")


def _find_logged(config: Config, rid: str) -> dict | None:
    if not config.log_path or not os.path.isfile(config.log_path):
        return None
    found = None
    with open(config.log_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if rid in line:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("request_id") == rid or rid in (rec.get("request_ids") or []):
                    found = rec
    return found


async def _explain_answer(args: dict, ctx: ToolContext) -> list[dict]:
    raw = str(args["answer"]).strip()
    if raw.startswith(("{", "[")):
        try:
            data = json.loads(raw)
        except ValueError:
            raise PromptError("answer: pasted response is not valid JSON") from None
        what = "response"
    else:
        data = _find_logged(ctx.config, raw)
        if data is None:
            raise PromptError(f"answer: request id {raw!r} not found in OPENJEV_MCP_LOG" if ctx.config.log_path
                              else "answer: OPENJEV_MCP_LOG is not set; paste the response JSON instead")
        what = f"logged read {raw}"
    return _msg(f"Explain this OpenJev {what}.\n\n```json\n{json.dumps(data, indent=1, ensure_ascii=False)}\n```\n\n{EXPLAIN}")


AUDIT_QUESTION = PromptSpec(
    "audit_question", "Audit a question set",
    "A calibrate plan for a question set and a labels file, plus the threshold table to paste.",
    (PromptArg("schema_path", "JSON file with the questions (or a request with questions)", True),
     PromptArg("labels_path", "labels file (.csv or .jsonl) with an id column and one label column per question", True)),
    _audit_question)

EXPLAIN_ANSWER = PromptSpec(
    "explain_answer", "Explain an answer",
    "What each number of an answer means (0-indexed score, no noul confidence, K-dependent confidence) and whether to act.",
    (PromptArg("answer", "a request id found in OPENJEV_MCP_LOG, or pasted response JSON", True),),
    _explain_answer)
