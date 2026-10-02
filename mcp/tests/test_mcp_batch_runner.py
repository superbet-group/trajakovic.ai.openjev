"""Batch runner against stub transports: order, back-pressure, errors, resume, audit, sampling, cancel, progress."""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import namedtuple

import anyio
import httpx
import pytest
import stubs

from openjev_mcp import wire
from openjev_mcp.batch import stats, store
from openjev_mcp.batch.runner import BatchJob, run_batch
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache, default_limits
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.tools import ToolContext

pytestmark = pytest.mark.anyio

Item = namedtuple("Item", "index id state")
Q = {"q": {"type": "noul", "instructions": "Is this a billing issue?", "criteria": {"true": "about charges", "false": "anything else"}}}


def items(n):
    return [Item(i, f"r{i}", f"text {i}") for i in range(1, n + 1)]


def num(body):
    return int(body["state"].split()[-1])


class Srv:
    """Scripted OpenJev: answers(body) -> raw answers, delay(body) -> s, fault(call_no, body) -> httpx.Response | None."""

    def __init__(self, answers=None, delay=None, fault=None):
        self.answers = answers or (lambda b: {"q": {"type": "noul", "noul": 0.95}})
        self.delay, self.fault = delay, fault
        self.bodies, self.raw, self.spans, self.inflight, self.peak, self.calls = [], [], [], 0, 0, 0
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, request):
        n, self.calls = self.calls, self.calls + 1
        body = json.loads(request.content)
        self.bodies.append(body)
        self.raw.append(request.content)
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        start = time.monotonic()
        try:
            if self.delay:
                await asyncio.sleep(self.delay(body))
            bad = self.fault(n, body) if self.fault else None
            if bad is not None:
                return bad
            return httpx.Response(200, headers={"x-request-id": f"req_{n:04d}", "server-timing": "model;dur=1.5, server;dur=3.0, total;dur=3.0"},
                                  json={"model": "openjev-0.1", "answers": self.answers(body),
                                        "usage": {"input_tokens": 10, "output_tokens": 1}})
        finally:
            self.inflight -= 1
            self.spans.append((start, time.monotonic(), n))


def overloaded(retry_after="0.02"):
    return httpx.Response(529, headers={"retry-after": retry_after}, json={"detail": {"error_type": "overloaded_error", "message": "busy"}})


def make_ctx(transport, tmp_path, *, token=True, min_interval=0.0, **cfg_kw):
    cfg_kw.setdefault("retries", 2)
    cfg_kw.setdefault("max_inflight_batch", 4)
    config = Config(base_url="http://oj.test", roots=(str(tmp_path.resolve()),), **cfg_kw)
    client = OpenJevClient(config, transport=transport, jitter=lambda: 0.0)
    limits = LimitsCache(client, ttl_s=1e9)
    limits._limits, limits._fetched = default_limits(), time.monotonic()
    sent = []

    async def send(progress, total, message):
        sent.append((progress, total, message))

    emitter = ProgressEmitter(send if token else None, min_interval_s=min_interval)
    return ToolContext(config, client, limits, emitter, None), sent


def open_out(ctx, tmp_path, *, resume=True, name="o.jsonl", questions=Q):
    qh = store.question_hash(questions)
    h = store.make_header(run_id=store.run_id(qh, {"kind": "items"}, {}, "fast", 4), question_hash=qh)
    return store.open_output(str(tmp_path.resolve() / name), h, resume=resume, config=ctx.config)


def job(its, output=None, **kw):
    return BatchJob(items=its, questions=kw.pop("questions", Q), output=output, **kw)


def file_rows(tmp_path, name="o.jsonl"):
    recs = [json.loads(x) for x in (tmp_path.resolve() / name).read_text().splitlines()]
    return recs[0], recs[1:]


def ids(rows):
    return [r["id"] for r in rows]


# --- order and gate ---

@pytest.mark.parametrize("conc", [1, 2, 3, 4])
async def test_output_order_equals_input_order(tmp_path, conc):
    srv = Srv(delay=lambda b: (13 - num(b)) * 0.004)   # later rows finish first
    ctx, _ = make_ctx(srv.transport, tmp_path)
    out = open_out(ctx, tmp_path)
    run = await run_batch(ctx, job(items(12), out, concurrency=conc, max_items_per_call=100))
    out.close()
    _, rows = file_rows(tmp_path)
    assert ids(rows) == ids(run.rows) == [f"r{i}" for i in range(1, 13)]
    assert [r["index"] for r in rows] == list(range(1, 13))
    assert run.stopped_reason == "complete" and run.next_offset is None
    assert (srv.peak > 1) == (conc > 1) and srv.peak <= conc
    s = run.status
    assert (s["done"], s["total"], s["ok"], s["errors"], s["skipped"], s["remaining"]) == (12, 12, 12, 0, 0, 0)
    assert s["input_tokens"] == 120 and s["output_tokens"] == 12 and s["effective_concurrency"] == conc and s["backoffs"] == 0


async def test_workers_capped_by_batch_pool(tmp_path):
    srv = Srv(delay=lambda b: 0.01)
    ctx, _ = make_ctx(srv.transport, tmp_path, max_inflight_batch=2)
    run = await run_batch(ctx, job(items(8), concurrency=4))
    assert srv.peak == 2 and run.status["effective_concurrency"] == 2


async def test_row_records_request_metadata(tmp_path):
    srv = Srv()
    ctx, _ = make_ctx(srv.transport, tmp_path)
    out = open_out(ctx, tmp_path)
    await run_batch(ctx, job(items(1), out))
    out.close()
    _, (r,) = file_rows(tmp_path)
    assert r["request_id"] == "req_0000" and r["server_timing"] == {"model_ms": 1.5, "server_ms": 3.0, "total_ms": 3.0}
    assert r["body_hash"] == wire.body_hash(srv.raw[0]) and r["usage"] == {"input_tokens": 10, "output_tokens": 1}
    assert r["latency_ms"] >= 0 and r["model"] == "openjev-0.1" and r["error"] is None and r["retried"] is None
    assert r["state"] == "text 1" and r["state_hash"] == wire.body_hash(b"text 1") and r["ts"].endswith("Z")
    assert r["answers"]["q"]["band"] == "yes" and r["needs_review"] is False and r["review_reasons"] == []
    assert srv.bodies[0] == {"model": ctx.config.model, "samples": 1, "state": "text 1", "questions": Q}


async def test_overload_pauses_all_workers_and_recovers_after_ten(tmp_path):
    srv = Srv(delay=lambda b: 0.004, fault=lambda n, b: overloaded("0.05") if n == 4 else None)
    ctx, _ = make_ctx(srv.transport, tmp_path)
    run = await run_batch(ctx, job(items(40), concurrency=3, max_items_per_call=100))
    assert run.stopped_reason == "complete" and run.status["ok"] == 40 and run.status["errors"] == 0
    assert run.status["backoffs"] == 1 and run.status["effective_concurrency"] == 3
    assert run.rows[[r["id"] for r in run.rows].index(f"r{num(srv.bodies[4])}")]["status"] == "ok"
    over = next(end for start, end, n in srv.spans if n == 4)
    after = sorted(s for s in srv.spans if s[0] >= over and s[2] != 4)
    for (s1, e1, _), (s2, e2, _) in zip(after[:10], after[1:11]):
        assert s2 >= e1 - 1e-3   # serial for the first 10 successes
    assert any(b[0] < a[1] - 1e-3 for a, b in zip(after[10:], after[11:]))   # concurrency came back


async def test_overload_without_recovery_keeps_effective_one(tmp_path):
    srv = Srv(fault=lambda n, b: overloaded() if n == 1 else None)
    ctx, _ = make_ctx(srv.transport, tmp_path)
    run = await run_batch(ctx, job(items(6), concurrency=3))
    assert run.status["effective_concurrency"] == 1 and run.status["ok"] == 6 and run.status["backoffs"] == 1
    assert [r["retried"] is not None for r in run.rows].count(True) == 1


@pytest.mark.parametrize("conc", [1, 3])
async def test_backpressure_stop_writes_no_row_and_next_call_continues(tmp_path, conc):
    state = {"down": True}
    srv = Srv(fault=lambda n, b: overloaded("0.01") if state["down"] and num(b) == 4 else None)
    ctx, _ = make_ctx(srv.transport, tmp_path, retries=1)
    its = items(8)
    out = open_out(ctx, tmp_path)
    run = await run_batch(ctx, job(its, out, concurrency=conc))
    out.close()
    assert run.stopped_reason == "backpressure" and run.next_offset == 3 and ids(run.rows) == ["r1", "r2", "r3"]
    assert run.status["errors"] == 0 and run.status["remaining"] == 5
    _, rows = file_rows(tmp_path)
    assert ids(rows) == ["r1", "r2", "r3"]
    state["down"] = False
    out = open_out(ctx, tmp_path)
    run2 = await run_batch(ctx, job(its, out, concurrency=conc, start_offset=run.next_offset, skip_ids={"r1", "r2", "r3"}))
    out.close()
    assert run2.stopped_reason == "complete" and ids(run2.rows) == [f"r{i}" for i in range(4, 9)]
    _, rows = file_rows(tmp_path)
    assert ids(rows) == [f"r{i}" for i in range(1, 9)]


async def test_retried_row_is_marked(tmp_path):
    srv = Srv(fault=lambda n, b: overloaded() if n == 0 else None)
    ctx, _ = make_ctx(srv.transport, tmp_path)
    run = await run_batch(ctx, job(items(1)))
    assert run.rows[0]["status"] == "ok" and run.rows[0]["retried"] == {"status": 529, "kind": "OJ_OVERLOADED", "attempts": 2}


# --- errors ---

def bad_on(n):
    return lambda c, b: httpx.Response(400, json={"detail": "Input should be valid"}) if num(b) == n else None


async def test_on_error_record(tmp_path):
    srv = Srv(fault=bad_on(3))
    ctx, _ = make_ctx(srv.transport, tmp_path)
    out = open_out(ctx, tmp_path)
    run = await run_batch(ctx, job(items(5), out, concurrency=2, on_error="record"))
    out.close()
    assert run.stopped_reason == "complete" and run.status["ok"] == 4 and run.status["errors"] == 1
    _, rows = file_rows(tmp_path)
    bad = rows[2]
    assert bad["id"] == "r3" and bad["status"] == "error" and bad["error"]["http_status"] == 400
    assert bad["needs_review"] is True and bad["review_reasons"] == ["error"] and "answers" not in bad


async def test_on_error_abort(tmp_path):
    srv = Srv(fault=bad_on(3))
    ctx, _ = make_ctx(srv.transport, tmp_path)
    out = open_out(ctx, tmp_path)
    run = await run_batch(ctx, job(items(6), out, on_error="abort"))
    out.close()
    assert run.stopped_reason == "error_abort" and run.next_offset == 3 and ids(run.rows) == ["r1", "r2", "r3"]
    assert run.rows[2]["status"] == "error" and srv.calls == 3
    _, rows = file_rows(tmp_path)
    assert [r["status"] for r in rows] == ["ok", "ok", "error"]


async def test_malformed_answer_is_an_error_row(tmp_path):
    srv = Srv(answers=lambda b: {"q": {"type": "choice"}})
    ctx, _ = make_ctx(srv.transport, tmp_path)
    run = await run_batch(ctx, job(items(1)))
    assert run.rows[0]["status"] == "error" and run.rows[0]["error"]["code"] == "OJ_PROTOCOL"


async def test_lint_runs_once_before_row_zero(tmp_path):
    srv = Srv()
    ctx, _ = make_ctx(srv.transport, tmp_path)
    out = open_out(ctx, tmp_path)
    with pytest.raises(ToolError) as e:
        await run_batch(ctx, job(items(3), out, questions={"q": {"type": "nuol", "instructions": "x"}}))
    assert e.value.code == "OJ_INVALID_INPUT" and srv.calls == 0
    with pytest.raises(ValueError):
        out.append({"id": "x"})   # failure released the lock


# --- pagination, budget, resume ---

async def test_max_items_per_call_cursor_loop(tmp_path):
    srv = Srv()
    ctx, _ = make_ctx(srv.transport, tmp_path)
    its, offset, seen = items(7), 0, []
    while offset is not None:
        run = await run_batch(ctx, job(its, max_items_per_call=3, start_offset=offset))
        seen += ids(run.rows)
        assert run.stopped_reason == ("max_items_per_call" if run.next_offset is not None else "complete")
        offset = run.next_offset
    assert seen == [f"r{i}" for i in range(1, 8)] and srv.calls == 7


async def test_time_budget_stops_dispatch(tmp_path):
    srv = Srv(delay=lambda b: 0.05)
    ctx, _ = make_ctx(srv.transport, tmp_path)
    run = await run_batch(ctx, job(items(10), time_budget_s=0.07))
    assert run.stopped_reason == "time_budget" and 1 <= len(run.rows) < 10 and run.next_offset == len(run.rows)


async def test_skip_only_and_retry_last_row_wins(tmp_path):
    fail = {"on": True}
    srv = Srv(fault=lambda n, b: httpx.Response(400, json={"detail": "x"}) if fail["on"] and num(b) == 2 else None)
    ctx, _ = make_ctx(srv.transport, tmp_path)
    its = items(4)
    out = open_out(ctx, tmp_path)
    await run_batch(ctx, job(its, out))
    out.close()
    fail["on"] = False
    # resume: ok ids are skipped at no cost, the error id too unless retry_errors
    _, last, _, _ = store.read_output(str(tmp_path.resolve() / "o.jsonl"), ctx.config)
    before = srv.calls
    out = open_out(ctx, tmp_path)
    run = await run_batch(ctx, job(its, out, skip_ids={i for i, r in last.items()}))
    out.close()
    assert srv.calls == before and run.status["skipped"] == 4 and run.status["done"] == 4 and run.stopped_reason == "complete"
    assert [r["status"] for r in run.rows] == ["skipped"] * 4
    # retry_errors: only the error id is read, appended, last row wins
    out = open_out(ctx, tmp_path)
    run = await run_batch(ctx, job(its, out, skip_ids={i for i, r in last.items() if r["status"] == "ok"}))
    out.close()
    assert srv.calls == before + 1 and run.status["ok"] == 1 and run.status["skipped"] == 3
    path = str(tmp_path.resolve() / "o.jsonl")
    _, last, n, _ = store.read_output(path, ctx.config)
    assert n == 6 and last["r2"]["status"] == "ok"
    # only_ids re-runs an ok id (overrides skip_ids), unknown ids are a warning
    out = open_out(ctx, tmp_path)
    run = await run_batch(ctx, job(its, out, only_ids=["r4", "nope"], skip_ids={"r4"}))
    out.close()
    assert ids(run.rows) == ["r4"] and run.status["skipped"] == 0 and "nope" in run.warnings[0]
    _, rows = file_rows(tmp_path)
    assert ids(rows) == ["r1", "r2", "r3", "r4", "r2", "r4"]


# --- audit and sampling ---

async def test_audit_sample_identical_at_any_concurrency(tmp_path):
    picks = []
    for conc in (1, 4):
        srv = Srv(delay=lambda b: (31 - num(b)) * 0.001)
        ctx, _ = make_ctx(srv.transport, tmp_path)
        run = await run_batch(ctx, job(items(30), concurrency=conc, audit={"rate": 0.4, "seed": 7}, max_items_per_call=100))
        picks.append([r["id"] for r in run.rows if r["audit"]])
    assert picks[0] == picks[1] == [f"r{i}" for i in range(1, 31) if stats.in_audit(7, f"r{i}", 0.4)] and 3 < len(picks[0]) < 25


async def test_review_rows_are_never_audited(tmp_path):
    srv = Srv(answers=lambda b: {"q": {"type": "noul", "noul": 0.5}})
    ctx, _ = make_ctx(srv.transport, tmp_path)
    run = await run_batch(ctx, job(items(5), audit={"rate": 1.0}, regrey_samples=0))
    assert all(r["needs_review"] and not r["audit"] and r["review_reasons"] == ["noul_grey"] for r in run.rows)
    ctx, _ = make_ctx(Srv().transport, tmp_path)
    run = await run_batch(ctx, job(items(3), audit={"rate": 1.0}))
    assert all(r["audit"] and not r["needs_review"] for r in run.rows)


async def test_sampling_fast_regreys_a_grey_row_once(tmp_path):
    def answers(b):
        return {"q": {"type": "noul", "noul": 0.5 if b.get("samples") == 1 else 0.97}}
    srv = Srv(answers=answers)
    ctx, _ = make_ctx(srv.transport, tmp_path)
    run = await run_batch(ctx, job(items(1)))
    assert [b.get("samples") for b in srv.bodies] == [1, 4]
    r = run.rows[0]
    assert r["answers"]["q"]["band"] == "yes" and r["needs_review"] is False
    assert r["usage"] == {"input_tokens": 20, "output_tokens": 2} and r["request_id"] == "req_0001"
    assert r["body_hash"] == wire.body_hash(srv.raw[1])


async def test_sampling_fast_still_grey_after_regrey_needs_review_and_other_modes(tmp_path):
    grey = lambda b: {"q": {"type": "noul", "noul": 0.5}}   # noqa: E731
    srv = Srv(answers=grey)
    ctx, _ = make_ctx(srv.transport, tmp_path)
    run = await run_batch(ctx, job(items(1), regrey_samples=7))
    assert [b.get("samples") for b in srv.bodies] == [1, 7] and run.rows[0]["needs_review"] is True
    srv = Srv(answers=grey)   # regrey_samples 0 disables
    ctx, _ = make_ctx(srv.transport, tmp_path)
    await run_batch(ctx, job(items(1), regrey_samples=0))
    assert [b.get("samples") for b in srv.bodies] == [1]
    srv = Srv(answers=grey)   # server_default: no samples field, no regrey
    ctx, _ = make_ctx(srv.transport, tmp_path)
    await run_batch(ctx, job(items(2), sampling="server_default"))
    assert srv.calls == 2 and all("samples" not in b for b in srv.bodies)
    srv = Srv(answers=grey)   # explicit options.samples wins in both modes, no regrey
    ctx, _ = make_ctx(srv.transport, tmp_path)
    await run_batch(ctx, job(items(1), options={"samples": 3}))
    await run_batch(ctx, job(items(1), options={"samples": 3}, sampling="server_default"))
    assert [b["samples"] for b in srv.bodies] == [3, 3]


async def test_include_state_false_writes_no_state_text(tmp_path):
    srv = Srv()
    ctx, _ = make_ctx(srv.transport, tmp_path)
    out = open_out(ctx, tmp_path)
    run = await run_batch(ctx, job([Item(1, "r1", "secret-state-text")], out, include_state=False))
    out.close()
    raw = (tmp_path.resolve() / "o.jsonl").read_text()
    assert "secret-state-text" not in raw and '"state"' not in raw and "state_hash" in raw and "state" not in run.rows[0]


async def test_dict_items_and_non_string_state(tmp_path):
    srv = Srv()
    ctx, _ = make_ctx(srv.transport, tmp_path)
    run = await run_batch(ctx, job([{"index": 1, "id": "a", "state": {"k": "v"}}]))
    assert run.rows[0]["state"] == '{"k":"v"}' and srv.bodies[0]["state"] == {"k": "v"}


# --- cancellation and progress ---

async def test_cancellation_leaves_whole_lines_and_resume_has_no_gaps(tmp_path):
    inner = Srv()
    transport = stubs.slow_transport(0.05, inner.transport)
    ctx, sent = make_ctx(transport, tmp_path)
    its = items(12)
    out = open_out(ctx, tmp_path)
    with anyio.move_on_after(0.18) as scope:
        await run_batch(ctx, job(its, out, concurrency=3, max_items_per_call=100))
    assert scope.cancelled_caught
    n_sent = len(sent)
    await anyio.sleep(0.15)
    assert len(sent) == n_sent   # nothing after the cancel
    raw = (tmp_path.resolve() / "o.jsonl").read_bytes()
    assert raw.endswith(b"\n")
    recs = [json.loads(x) for x in raw.splitlines()]
    done = ids(recs[1:])
    assert 0 < len(done) < 12 and done == [f"r{i}" for i in range(1, len(done) + 1)]
    with pytest.raises(ValueError):
        out.append({"id": "x"})   # handle closed: lock released
    # resume without cursor: skip what is in the file, read the rest
    _, last, _, _ = store.read_output(str(tmp_path.resolve() / "o.jsonl"), ctx.config)
    out = open_out(ctx, tmp_path)
    run = await run_batch(ctx, job(its, out, concurrency=3, max_items_per_call=100, skip_ids={i for i, r in last.items() if r["status"] == "ok"}))
    out.close()
    _, rows = file_rows(tmp_path)
    assert ids(rows) == [f"r{i}" for i in range(1, 13)] and run.status["skipped"] == len(done)


async def test_concurrent_second_call_on_same_output_is_refused(tmp_path):
    srv = Srv()
    ctx, _ = make_ctx(srv.transport, tmp_path)
    out = open_out(ctx, tmp_path)
    with pytest.raises(ToolError) as e:
        open_out(ctx, tmp_path)
    assert e.value.message == "output_path is in use by another batch call"
    out.close()


async def test_progress_strictly_increasing_constant_total_and_format(tmp_path):
    srv = Srv(delay=lambda b: 0.002 * (b["state"].endswith("3") + 1), fault=bad_on(2))
    ctx, sent = make_ctx(srv.transport, tmp_path)
    run = await run_batch(ctx, job(items(9), concurrency=3, skip_ids={"r1"}))
    values = [p for p, _, _ in sent]
    assert values == sorted(set(values)) and values[-1] == 9
    assert {t for _, t, _ in sent} == {9}
    assert all(re.fullmatch(r"\d+/9 ok=\d+ err=\d+ eta \d+s", m) for _, _, m in sent)
    assert sent[-1][2].startswith("9/9 ok=7 err=1 ")
    assert run.status["skipped"] == 1


async def test_progress_rate_limit_and_no_token(tmp_path):
    srv = Srv()
    ctx, sent = make_ctx(srv.transport, tmp_path, min_interval=1.0)
    await run_batch(ctx, job(items(10)))
    assert len(sent) == 1   # 10 rows in far less than a second
    ctx, sent = make_ctx(Srv().transport, tmp_path, token=False)
    await run_batch(ctx, job(items(3)))
    assert sent == []
