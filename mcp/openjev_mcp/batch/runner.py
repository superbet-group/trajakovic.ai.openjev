"""Batch runner: one POST /v1/systemone per state, bounded workers, ordered writes, shared back-pressure gate
(spec 2.11 'Concurrency and back-pressure', 2.0.1 rules 7-8)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import anyio

from openjev_mcp import wire
from openjev_mcp.derive import Band, derive_answers
from openjev_mcp.errors import RETRY_ONCE, ToolError
from openjev_mcp.lint import Finding, estimate, lint_request
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools.read import _lint_error, compute_timeout

from . import stats
from .store import OutputHandle, now_iso

OVERLOADED = frozenset({"OJ_RATE_LIMITED", "OJ_UNAVAILABLE", "OJ_OVERLOADED"})   # 429, 503, 529
RECOVER_AFTER = 10
JITTER_MAX = 0.25


@dataclass
class BatchJob:
    items: list                                   # objects (or dicts) with index, id, state
    questions: dict
    options: dict = field(default_factory=dict)
    sampling: str = "fast"                        # fast | server_default
    regrey_samples: int = 4
    thresholds: dict = field(default_factory=dict)
    review_rule: dict = field(default_factory=dict)
    audit: dict = field(default_factory=dict)
    concurrency: int = 1
    output: OutputHandle | None = None
    include_state: bool = True
    max_items_per_call: int = 25
    time_budget_s: float = 120
    on_error: str = "record"                      # record | abort
    start_offset: int = 0                         # position in items, from the cursor
    skip_ids: frozenset | set = frozenset()       # last row ok (or error without retry_errors): counted, not re-read
    only_ids: list | None = None                  # run only these (overrides skip_ids)


@dataclass
class BatchRun:
    rows: list                                    # this call, index order; skipped rows are {index, id, status: skipped}
    status: dict                                  # the 2.11 status block
    next_offset: int | None
    stopped_reason: str
    lint_warnings: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    out_bytes: int = 0
    out_lines: int = 0


class _Backpressure(Exception):
    pass


class _Gate:
    """Shared cooldown: closes for every worker on 429/503/529, effective concurrency 1, +1 per 10 successes."""

    def __init__(self, cap: int, clock, sleep):
        self.cap, self.eff, self.active, self.streak, self.backoffs = cap, cap, 0, 0, 0
        self.until = 0.0
        self._clock, self._sleep = clock, sleep
        self._cond = anyio.Condition()

    async def enter(self) -> None:
        while True:
            wait = self.until - self._clock()
            if wait > 0:
                await self._sleep(wait)
                continue
            async with self._cond:
                if self.active < self.eff:
                    self.active += 1
                    return
                await self._cond.wait()

    async def leave(self, ok: bool) -> None:
        self.streak = self.streak + 1 if ok else 0
        if ok and self.streak >= RECOVER_AFTER and self.eff < self.cap:
            self.eff, self.streak = self.eff + 1, 0
        async with self._cond:
            self.active -= 1
            self._cond.notify_all()

    def overload(self, delay: float) -> None:
        self.until = max(self.until, self._clock() + delay)
        self.eff, self.streak = 1, 0
        self.backoffs += 1


def _item(it: Any) -> tuple[int, str, Any]:
    if isinstance(it, dict):
        return it["index"], str(it["id"]), it["state"]
    return it.index, str(it.id), it.state


def _state_text(state: Any) -> str:
    return state if isinstance(state, str) else wire.dumps_text(state)


def _usage(data: Any, key: str) -> int:
    usage = data.get("usage") if isinstance(data, dict) else None
    value = usage.get(key) if isinstance(usage, dict) else None
    return value if isinstance(value, int) else 0


class _Runner:
    def __init__(self, ctx: ToolContext, job: BatchJob):
        self.ctx, self.job = ctx, job
        self.cfg = ctx.config
        self.clock, self.sleep = ctx.client._clock, ctx.client._sleep
        t = job.thresholds or {}
        self.band = Band(t.get("yes_at", self.cfg.band_yes_at), t.get("no_at", self.cfg.band_no_at),
                         t.get("choice_min_p", Band.choice_min_p))
        self.options = dict(job.options)
        self.explicit_samples = self.options.get("samples") is not None
        if job.sampling == "fast" and not self.explicit_samples:
            self.options["samples"] = 1
        self.model = self.options.get("model") or self.cfg.model
        self.audit = {**stats.AUDIT_DEFAULT, **(job.audit or {})}
        workers = max(1, min(job.concurrency, self.cfg.max_inflight_batch))
        self.workers = workers
        self.gate = _Gate(workers, self.clock, self.sleep)
        self.plan: list[dict] = []
        self.results: dict[int, dict] = {}
        self.k = 0
        self.out: list[dict] = []
        self.counts = {"ok": 0, "error": 0, "skipped": 0, "in": 0, "out": 0}
        self.stop: str | None = None
        self.t0 = self.clock()
        self.known = None

    # --- plan ---
    def build_plan(self) -> tuple[int | None, list[str]]:
        job, warnings = self.job, []
        only = None if job.only_ids is None else set(job.only_ids)
        if only is not None:
            known = {_item(it)[1] for it in job.items}
            unknown = [i for i in job.only_ids if i not in known]
            if unknown:
                warnings.append(f"only_ids: unknown id(s) skipped: {', '.join(unknown[:20])}")
        picked, cut = 0, None
        for pos in range(job.start_offset, len(job.items)):
            index, rid, state = _item(job.items[pos])
            if only is not None and rid not in only:
                continue
            skip = only is None and rid in job.skip_ids
            if not skip:
                if picked >= job.max_items_per_call:
                    cut = pos
                    break
                picked += 1
            self.plan.append({"pos": pos, "index": index, "id": rid, "state": state, "skip": skip})
        for i, e in enumerate(self.plan):
            if e["skip"]:
                self.results[i] = {"index": e["index"], "id": e["id"], "status": "skipped"}
        return cut, warnings

    def body(self, state: Any, samples: int | None = None) -> dict:
        opts = self.options if samples is None else {**self.options, "samples": samples}
        return wire.build_body(self.model, state, self.job.questions, opts)

    # --- one read with the shared gate ---
    async def read(self, body: dict, notes: dict) -> Any:
        retries = self.cfg.retries
        think = body.get("think") or 0
        attempt = 0
        while True:
            await self.gate.enter()
            notes["started"] = True
            try:
                res = await self.ctx.client.systemone(
                    body, timeout_ms=compute_timeout(self.cfg, self.options, estimate(body)), think=think,
                    known_models=self.known, pool="batch", retries=0)
            except ToolError as err:
                await self.gate.leave(False)
                if err.code in OVERLOADED:
                    delay = (err.retry_after_s if err.retry_after_s is not None else 1.0) + min(self.ctx.client._jitter(), JITTER_MAX)
                    self.gate.overload(delay)
                    if attempt >= retries:
                        raise _Backpressure() from None
                elif err.code == "OJ_UNREACHABLE" and attempt < retries or (
                        err.code in RETRY_ONCE and retries > 0 and attempt == 0 and not (err.code == "OJ_TIMEOUT" and think > 0)):
                    await self.sleep((err.retry_after_s if err.retry_after_s is not None else 1.0) + min(self.ctx.client._jitter(), JITTER_MAX))
                else:
                    raise
                attempt += 1
                notes["retried"] = {"status": err.http_status, "kind": err.code, "attempts": attempt + 1}
                continue
            await self.gate.leave(True)
            return res

    async def do_row(self, e: dict) -> dict:
        state = e["state"]
        text = _state_text(state)
        row: dict[str, Any] = {"index": e["index"], "id": e["id"], "status": "ok"}
        if self.job.include_state:
            row["state"] = text
        row["state_hash"] = wire.body_hash(text.encode("utf-8"))
        notes: dict = {}
        body = self.body(state)
        reads: list = []
        try:
            res = await self.read(body, notes)
            reads.append(res)
            answers = derive_answers(res.data.get("answers") if isinstance(res.data, dict) else None, self.job.questions, self.band)
            reasons = stats.review_reasons(answers, self.job.questions, self.job.review_rule)
            if reasons and self.job.sampling == "fast" and not self.explicit_samples and self.job.regrey_samples > 0:
                body = self.body(state, self.job.regrey_samples)
                res = await self.read(body, notes)
                reads.append(res)
                answers = derive_answers(res.data.get("answers") if isinstance(res.data, dict) else None, self.job.questions, self.band)
                reasons = stats.review_reasons(answers, self.job.questions, self.job.review_rule)
        except _Backpressure:
            raise
        except ToolError as err:
            row.update(status="error", needs_review=True, review_reasons=["error"], audit=False, model=self.model,
                       usage={"input_tokens": sum(_usage(r.data, "input_tokens") for r in reads),
                              "output_tokens": sum(_usage(r.data, "output_tokens") for r in reads)},
                       latency_ms=round(sum(r.latency_ms for r in reads), 1), server_timing=None,
                       request_id=err.request_id, body_hash=wire.body_hash(wire.body_bytes(body)),
                       retried=notes.get("retried"), error=err.to_dict(), ts=now_iso())
            return row
        data = res.data if isinstance(res.data, dict) else {}
        review = bool(reasons)
        row.update(answers=answers, needs_review=review, review_reasons=reasons,
                   audit=(not review) and stats.in_audit(self.audit["seed"], e["id"], self.audit["rate"]),
                   model=data.get("model") if isinstance(data.get("model"), str) else self.model,
                   usage={"input_tokens": sum(_usage(r.data, "input_tokens") for r in reads),
                          "output_tokens": sum(_usage(r.data, "output_tokens") for r in reads)},
                   latency_ms=round(sum(r.latency_ms for r in reads), 1), server_timing=res.server_timing,
                   request_id=res.request_id, body_hash=res.body_hash, retried=notes.get("retried"), error=None, ts=now_iso())
        return row

    # --- ordered writes ---
    def flush(self) -> bool:
        moved = False
        while self.k < len(self.plan) and self.k in self.results:
            r = self.results[self.k]
            if r["status"] != "skipped" and self.job.output is not None:
                self.job.output.append(r)
            self.out.append(r)
            c = self.counts
            c[{"ok": "ok", "error": "error", "skipped": "skipped"}[r["status"]]] += 1
            c["in"] += (r.get("usage") or {}).get("input_tokens", 0)
            c["out"] += (r.get("usage") or {}).get("output_tokens", 0)
            self.k += 1
            moved = True
        return moved

    def eta_s(self) -> int | None:
        done = self.counts["ok"] + self.counts["error"]
        left = sum(1 for e in self.plan[self.k:] if not e["skip"])
        return round((self.clock() - self.t0) * left / done) if done else None

    async def progress(self) -> None:
        c = self.counts
        done = c["ok"] + c["error"] + c["skipped"]
        eta = self.eta_s()
        await self.ctx.progress.emit(done, len(self.plan), f"{done}/{len(self.plan)} ok={c['ok']} err={c['error']} eta {eta or 0}s")

    async def worker(self, cursor: list[int]) -> None:
        while self.stop is None:
            while cursor[0] < len(self.plan) and cursor[0] in self.results:
                cursor[0] += 1     # skipped rows are pre-filled
            if cursor[0] >= len(self.plan):
                return
            if self.clock() - self.t0 >= self.job.time_budget_s:
                self.stop = "time_budget"
                return
            i = cursor[0]
            cursor[0] += 1
            try:
                row = await self.do_row(self.plan[i])
            except _Backpressure:
                self.stop = self.stop or "backpressure"
                return
            self.results[i] = row
            if row["status"] == "error" and self.job.on_error == "abort":
                # deviation: 2.11 on_error abort: the failing row is written as an error row (the cursor moves past it; retry_errors re-runs it)
                self.stop = self.stop or "error_abort"
            if self.flush():
                await self.progress()


async def run_batch(ctx: ToolContext, job: BatchJob) -> BatchRun:
    r = _Runner(ctx, job)
    try:
        cut, warnings = r.build_plan()
        lint_warnings: list[Finding] = []
        first = next((e for e in r.plan if not e["skip"]), None)
        if first is not None:
            limits = await ctx.limits.get()
            r.known = limits.known_models
            report = lint_request(r.body(first["state"]), limits=limits, autofix=False)
            if report.errors:
                raise _lint_error(report.errors)
            lint_warnings = report.warnings
        r.t0 = r.clock()
        if r.flush():
            await r.progress()
        cursor = [0]
        async with anyio.create_task_group() as tg:
            for _ in range(r.workers):
                tg.start_soon(r.worker, cursor)
    except BaseException:
        if job.output is not None:
            job.output.close()   # cancel or failure: whole lines only are on disk; the lock goes
        raise
    c, k, total = r.counts, r.k, len(r.plan)
    if r.stop in ("backpressure", "error_abort") or (r.stop == "time_budget" and k < total):
        reason = r.stop
        nxt = r.plan[k]["pos"] if k < total else cut
    elif k < total:   # a worker left rows undone only after a stop
        reason, nxt = r.stop or "time_budget", r.plan[k]["pos"]
    elif cut is not None:
        reason, nxt = "max_items_per_call", cut
    else:
        reason, nxt = "complete", None
    elapsed = (r.clock() - r.t0) * 1000
    done = c["ok"] + c["error"]
    # deviation: 2.11 status.done counts skipped rows too (same number as the progress notifications); status.skipped is the subset
    status = {"done": done + c["skipped"], "total": len(job.items), "remaining": 0 if nxt is None else len(job.items) - nxt,
              "ok": c["ok"], "errors": c["error"], "skipped": c["skipped"], "stopped_reason": reason,
              "elapsed_ms": round(elapsed, 1), "eta_ms": None if nxt is None or not done else round(elapsed / done * (len(job.items) - nxt), 1),
              "req_per_s": round(done / (elapsed / 1000), 2) if elapsed > 0 else 0.0,
              "effective_concurrency": r.gate.eff, "backoffs": r.gate.backoffs,
              "input_tokens": c["in"], "output_tokens": c["out"]}
    out = job.output
    return BatchRun(r.out, status, nxt, reason, lint_warnings, warnings,
                    out.size() if out else 0, out.lines if out else 0)
