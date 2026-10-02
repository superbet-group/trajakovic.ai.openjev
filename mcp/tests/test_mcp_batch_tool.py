"""batch tool (spec 2.11): source rules, dry_run, ex-batch replay, cursor and resume, exports, links, caps."""
from __future__ import annotations

import asyncio
import json
import math
import time

import anyio
import httpx
import pytest
import stubs

from openjev_mcp import wire
from openjev_mcp.batch import store
from openjev_mcp.config import Config
from openjev_mcp.envelope import success_result
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache, default_limits
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools.batch_tool import register
from openjev_mcp.schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS
from openjev_mcp.validate import _validator, validate_args, validate_output

pytestmark = pytest.mark.anyio

SPEC = None


@pytest.fixture(scope="module", autouse=True)
def _registered():
    """register() adds the batch schemas to the global registries; remove them again (P14 wires them for real)."""
    global SPEC
    SPEC = register(Config())
    yield
    INPUT_SCHEMAS.pop("batch", None)
    OUTPUT_SCHEMAS.pop("batch", None)
    _validator.cache_clear()
Q = {"q": {"type": "noul", "instructions": "Is this a billing issue?", "criteria": {"true": "about charges", "false": "anything else"}}}


class Srv:
    def __init__(self, delay=0.0, answers=None):
        self.delay, self.bodies, self.raw, self.inflight, self.peak = delay, [], [], 0, 0
        self.answers = answers or (lambda b: {"q": {"type": "noul", "noul": 0.95}})
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, request):
        n = len(self.raw)
        self.raw.append(request.content)
        self.bodies.append(json.loads(request.content))
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return httpx.Response(200, headers={"x-request-id": f"req_{n:04d}", "server-timing": "total;dur=3.0"},
                                  json={"model": "openjev-0.1", "answers": self.answers(self.bodies[n]),
                                        "usage": {"input_tokens": 10, "output_tokens": 1}})
        finally:
            self.inflight -= 1


def make_ctx(transport, tmp_path, **cfg):
    config = Config(base_url="http://oj.test", roots=(str(tmp_path.resolve()),), retries=1, **cfg)
    client = OpenJevClient(config, transport=transport, jitter=lambda: 0.0)
    limits = LimitsCache(client, ttl_s=1e9)
    limits._limits, limits._fetched = default_limits(), time.monotonic()
    return ToolContext(config, client, limits, ProgressEmitter.noop(), None)


async def call(ctx, args, validate=True):
    if validate:
        assert validate_args("batch", args) is None
    ctx.links = []
    out = await SPEC.handler(ctx, args)
    assert validate_output("batch", out) == [], validate_output("batch", out)
    return out


async def fails(ctx, args, text):
    with pytest.raises(ToolError) as e:
        await call(ctx, args, validate=False)
    assert e.value.code == "OJ_INVALID_INPUT" and text in e.value.message
    return e.value


def rows(n):
    return [{"id": f"r{i}", "state": f"text {i}"} for i in range(1, n + 1)]


def file_ids(tmp_path, name="o.jsonl"):
    recs = [json.loads(x) for x in (tmp_path.resolve() / name).read_text().splitlines()]
    return recs[0], [r["id"] for r in recs[1:]]


def r4(v):
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, dict):
        return {k: r4(x) for k, x in v.items()}
    if isinstance(v, list):
        return [r4(x) for x in v]
    return v


# --- registration ---

def test_register_annotations_and_schema():
    assert SPEC.name == "batch"
    assert SPEC.annotations == {"title": "Batch read", "readOnlyHint": False, "destructiveHint": False,
                                "idempotentHint": True, "openWorldHint": False}
    assert "images" in SPEC.input_schema["properties"]   # deviation: P15 added batch.images; stale assertion flipped by P14
    assert '"$ref"' not in json.dumps(SPEC.input_schema) + json.dumps(SPEC.output_schema)


# --- source rules ---

async def test_source_rules(tmp_path):
    srv = Srv()
    ctx = make_ctx(srv.transport, tmp_path)
    one = [{"id": "a", "state": "x"}]
    await fails(ctx, {"items": one, "items_file": {"path": str(tmp_path / "f.csv")}, "questions": Q}, "not both")
    await fails(ctx, {"questions": Q}, "no state source")
    await fails(ctx, {"items": one}, "no questions")
    await fails(ctx, {"items": one, "questions": Q, "export": [{"format": "csv", "path": str(tmp_path / "e.csv")}]}, "export needs output_path")
    await fails(ctx, {"template": "nope_not_there"}, "unknown template")
    assert srv.raw == []


async def test_export_path_checked_before_any_read(tmp_path):
    srv = Srv()
    ctx = make_ctx(srv.transport, tmp_path)
    (tmp_path / "e.csv").write_text("x")
    e = await fails(ctx, {"items": rows(2), "questions": Q, "output_path": str(tmp_path / "o.jsonl"),
                          "export": [{"format": "csv", "path": str(tmp_path / "e.csv")}]}, "already exists")
    assert e.path == "export.0.path" and srv.raw == [] and not (tmp_path / "o.jsonl").exists()


async def test_resume_false_with_existing_output_refused(tmp_path):
    ctx = make_ctx(Srv().transport, tmp_path)
    args = {"items": rows(2), "questions": Q, "output_path": str(tmp_path / "o.jsonl")}
    await call(ctx, args)
    await fails(ctx, {**args, "resume": False}, "output exists")


async def test_lint_error_leaves_no_file(tmp_path):
    srv = Srv()
    ctx = make_ctx(srv.transport, tmp_path)
    bad = {"q": {"type": "boolean", "instructions": "Is it?"}}   # unknown type
    with pytest.raises(ToolError) as e:
        await call(ctx, {"items": rows(1), "questions": bad, "output_path": str(tmp_path / "o.jsonl")}, validate=False)
    assert e.value.code == "OJ_INVALID_INPUT" and srv.raw == [] and not (tmp_path / "o.jsonl").exists()


# --- dry_run ---

async def test_dry_run_sends_nothing_and_first_body_matches_real_run(tmp_path):
    ctx = make_ctx(stubs.fail_on_request_transport(), tmp_path)
    args = {"items": rows(4), "questions": Q, "options": {"model": "openjev-latest"}, "dry_run": True, "concurrency": 2}
    out = await call(ctx, args)
    assert out["status"]["stopped_reason"] == "dry_run" and out["next_cursor"] is None and out["results"] == []
    assert [p["id"] for p in out["preview"]] == ["r1", "r2", "r3"] and out["import"]["row_count"] == 4
    assert any(w.startswith("W405") for w in out["meta"]["warnings"])
    srv = Srv()
    real = make_ctx(srv.transport, tmp_path)
    await call(real, {k: v for k, v in args.items() if k != "dry_run"})
    assert wire.body_bytes(out["first_body"]) == srv.raw[0]


async def test_dry_run_estimate_formula_and_w406(tmp_path):
    ctx = make_ctx(stubs.fail_on_request_transport(), tmp_path)
    items = rows(3)
    out = await call(ctx, {"items": items, "questions": Q, "options": {"samples": 4, "think": 8}, "dry_run": True})
    qc = len(wire.dumps_text(Q))
    tokens = sum(math.ceil((len(wire.dumps_text(i["state"])) + qc) / 3.6) * 4 * 2 for i in items)
    est = out["estimate"]
    assert est["requests"] == 3 and est["billed_reads"] == 12 and est["input_tokens_approx"] == tokens
    assert est["time_s_shared_approx"] == pytest.approx(est["time_s_idle_approx"] * 5, abs=0.2)
    assert any(w.startswith("W406") for w in out["meta"]["warnings"])


# --- ex-batch replay ---

ITEMS = [{"id": "t1", "state": "Subject: Charged twice\n\nI was billed $49 twice on March 3 for the same Pro plan. Please refund one."},
         {"id": "t2", "state": "Subject: API down\n\nPOST /v2/orders returns 500 since 08:00 UTC and our checkout is down for all shoppers."},
         {"id": "t3", "state": "Subject: Pricing\n\nWe are a 300-person company. Could you send enterprise pricing and book a demo next week? No rush."}]
EXQ = {"dept": {"type": "choice", "instructions": "Which team should own this ticket?", "criteria": {"billing": "charges, invoices, refunds, payment methods, plan pricing", "technical": "bugs, errors, outages, integrations, performance, login problems", "sales": "pre-purchase questions, quotes, upgrades, demos, enterprise pricing", "other": "anything else, or too vague to tell"}},
       "urgent": {"type": "noul", "instructions": "Does this need attention today (outage, money lost, deadline, or a blocked customer)?"}}


async def test_ex_batch_replay_gives_the_2_11_output(tmp_path):
    transport = stubs.replay_transport(["ex-batch-1", "ex-batch-2", "ex-batch-3"])
    ctx = make_ctx(transport, tmp_path)
    out = await call(ctx, {"items": ITEMS, "questions": EXQ, "options": {"samples": 1}})
    assert len([r for r in transport.requests if r.method == "POST"]) == 3
    st = out["status"]
    assert {k: st[k] for k in ("done", "total", "remaining", "ok", "errors", "skipped", "stopped_reason", "effective_concurrency", "backoffs", "input_tokens", "output_tokens")} == {
        "done": 3, "total": 3, "remaining": 0, "ok": 3, "errors": 0, "skipped": 0, "stopped_reason": "complete",
        "effective_concurrency": 1, "backoffs": 0, "input_tokens": 614, "output_tokens": 0}
    assert r4(out["summary"]) == {"scope": "call", "n": 3, "ok": 3, "errors": 0, "needs_review": 0, "audit": 0, "per_question": {
        "dept": {"type": "choice", "n": 3, "counts": {"billing": 1, "technical": 1, "sales": 1}, "top2": ["billing", "technical"], "mean_confidence": 0.9991, "abstained": 0},
        "urgent": {"type": "noul", "n": 3, "mean_p": 0.6658, "yes": 2, "no": 1, "grey": 0, "mean_margin": 0.9982}}}
    assert r4(out["results"]) == [
        {"index": 1, "id": "t1", "status": "ok", "answers": {"dept": {"choice": "billing", "p_top": 1.0}, "urgent": {"p": 0.9974, "band": "yes"}}, "needs_review": False, "error": None},
        {"index": 2, "id": "t2", "status": "ok", "answers": {"dept": {"choice": "technical", "p_top": 1.0}, "urgent": {"p": 1.0, "band": "yes"}}, "needs_review": False, "error": None},
        {"index": 3, "id": "t3", "status": "ok", "answers": {"dept": {"choice": "sales", "p_top": 0.9997}, "urgent": {"p": 0.0001, "band": "no"}}, "needs_review": False, "error": None}]
    assert out["review_queue"] == [] and out["audit_ids"] == [] and out["output_path"] is None and out["next_cursor"] is None
    assert out["meta"]["model"] == "openjev-0.1" and out["meta"]["requests"] == 3 and "import" not in out
    assert ctx.links == []


# --- cursor, interrupted call, resume ---

async def test_cursor_loop_interrupt_resume_no_duplicates_no_gaps(tmp_path):
    srv = Srv(delay=0.01)
    ctx = make_ctx(srv.transport, tmp_path)
    path = str(tmp_path.resolve() / "o.jsonl")
    base = {"items": rows(60), "questions": Q, "output_path": path, "max_items_per_call": 25}
    a = await call(ctx, base)
    assert a["status"]["stopped_reason"] == "max_items_per_call" and a["status"]["done"] == 25 and a["next_cursor"]
    assert a["summary"]["scope"] == "output_path" and a["summary"]["n"] == 25
    b = await call(ctx, {**base, "cursor": a["next_cursor"], "max_items_per_call": 25})
    assert b["status"]["done"] == 25 and [r["id"] for r in b["results"]][0] == "r26" and b["summary"]["n"] == 50
    with anyio.move_on_after(0.1):   # interrupted call: cancelled mid-flight
        await call(ctx, {**base, "cursor": b["next_cursor"], "max_items_per_call": 100})
    _, got = file_ids(tmp_path)
    assert 50 <= len(got) < 60 and got == [f"r{i}" for i in range(1, len(got) + 1)]
    c = await call(ctx, {**base, "max_items_per_call": 100})   # resume without cursor
    assert c["status"]["stopped_reason"] == "complete" and c["next_cursor"] is None
    assert c["status"]["skipped"] == len(got) and c["summary"]["n"] == 60 and c["summary"]["ok"] == 60
    _, got = file_ids(tmp_path)
    assert got == [f"r{i}" for i in range(1, 61)]
    sent = [b["state"] for b in srv.bodies]
    assert len(sent) >= 60 and set(sent) == {f"text {i}" for i in range(1, 61)}   # the cancelled in-flight read may repeat once
    again = await call(ctx, base)   # a finished job re-run reads nothing
    n = len(srv.raw)
    assert again["status"]["stopped_reason"] == "complete" and again["status"]["skipped"] == 60 and len(srv.raw) == n


async def test_cursor_errors(tmp_path):
    ctx = make_ctx(Srv().transport, tmp_path)
    path = str(tmp_path.resolve() / "o.jsonl")
    base = {"items": rows(6), "questions": Q, "output_path": path, "max_items_per_call": 2}
    a = await call(ctx, base)
    await fails(ctx, {**base, "cursor": "garbage!"}, "invalid cursor")
    await fails(ctx, {**base, "cursor": a["next_cursor"], "options": {"samples": 2}}, "arguments changed")
    ok = await call(ctx, {**base, "cursor": a["next_cursor"], "max_items_per_call": 3, "concurrency": 2})   # excluded keys may change
    assert ok["status"]["done"] == 3
    (tmp_path / "o.jsonl").write_bytes(b"")
    await fails(ctx, {**base, "cursor": ok["next_cursor"]}, "shrank")


# --- caps ---

async def test_time_budget_honoured(tmp_path):
    srv = Srv(delay=0.05)
    ctx = make_ctx(srv.transport, tmp_path)
    out = await call(ctx, {"items": rows(20), "questions": Q, "time_budget_s": 0.12, "max_items_per_call": 100}, validate=False)
    assert out["status"]["stopped_reason"] == "time_budget" and 1 <= out["status"]["done"] < 20 and out["next_cursor"]
    assert validate_args("batch", {"items": rows(1), "questions": Q, "time_budget_s": 1}) is not None   # schema floor is 5


@pytest.mark.parametrize("cap,conc", [(2, 4), (1, 3)])
async def test_inflight_batch_cap(tmp_path, cap, conc):
    srv = Srv(delay=0.02)
    ctx = make_ctx(srv.transport, tmp_path, max_inflight_batch=cap)
    out = await call(ctx, {"items": rows(8), "questions": Q, "concurrency": conc})
    assert srv.peak == cap and out["status"]["effective_concurrency"] == cap


async def test_env_cap_is_read_from_config(tmp_path):
    from openjev_mcp.config import load_config
    assert load_config({"OPENJEV_MCP_MAX_INFLIGHT_BATCH": "2"}).max_inflight_batch == 2


# --- summary, review queue, audit, compact vs full ---

async def test_review_queue_audit_and_detail(tmp_path):
    def answers(body):
        n = int(body["state"].split()[-1])
        return {"q": {"type": "noul", "noul": 0.5 if n % 2 else 0.97}}
    srv = Srv(answers=answers)
    ctx = make_ctx(srv.transport, tmp_path)
    args = {"items": rows(6), "questions": Q, "audit": {"rate": 1.0, "seed": 3}, "options": {"samples": 2}}
    out = await call(ctx, args)
    assert out["summary"]["needs_review"] == 3 and [e["id"] for e in out["review_queue"]] == ["r1", "r3", "r5"]
    assert all(e["reason"] == "noul_grey" and e["question"] == "q" for e in out["review_queue"])
    assert out["audit_ids"] == ["r2", "r4", "r6"] and out["summary"]["audit"] == 3
    assert out["results"][1]["audit"] is True and "audit" not in out["results"][0]
    assert out["results"][0]["answers"]["q"] == {"p": 0.5, "band": "grey"}
    full = await call(ctx, {**args, "detail": "full"})
    assert full["results"][0]["answers"]["q"]["type"] == "noul" and full["results"][0]["review_reasons"] == ["noul_grey"]
    few = await call(ctx, {**args, "max_inline_results": 2})
    assert len(few["results"]) == 2 and any("first 2 of 6" in w for w in few["meta"]["warnings"])
    none = await call(ctx, {**args, "max_inline_results": 0})
    assert none["results"] == [] and none["summary"]["n"] == 6


async def test_fast_sampling_regrey_and_requests_counted(tmp_path):
    def answers(body):
        return {"q": {"type": "noul", "noul": 0.5 if body.get("samples") == 1 and "text 1" in body["state"] else 0.97}}
    srv = Srv(answers=answers)
    ctx = make_ctx(srv.transport, tmp_path)
    out = await call(ctx, {"items": rows(2), "questions": Q})
    assert [b.get("samples") for b in srv.bodies] == [1, 4, 1] and out["meta"]["requests"] == 3


async def test_errors_recorded_and_retry_errors(tmp_path):
    fail = {"on": True}

    def handler(request):
        if fail["on"] and b"text 2" in request.content:
            return httpx.Response(400, json={"detail": "bad"})
        return httpx.Response(200, json={"model": "openjev-0.1", "answers": {"q": {"type": "noul", "noul": 0.97}},
                                         "usage": {"input_tokens": 1, "output_tokens": 0}})
    ctx = make_ctx(httpx.MockTransport(handler), tmp_path)
    args = {"items": rows(3), "questions": Q, "output_path": str(tmp_path.resolve() / "o.jsonl")}
    out = await call(ctx, args)
    assert out["summary"]["errors"] == 1 and out["results"][1]["status"] == "error" and out["results"][1]["error"]["code"]
    assert out["review_queue"] == [{"id": "r2", "reason": "error", "confidence": 0.0}]
    again = await call(ctx, args)
    assert again["status"]["skipped"] == 3
    fail["on"] = False
    fixed = await call(ctx, {**args, "retry_errors": True})
    assert fixed["status"]["ok"] == 1 and fixed["status"]["skipped"] == 2 and fixed["summary"]["errors"] == 0 and fixed["summary"]["ok"] == 3
    one = await call(ctx, {**args, "only_ids": ["r1", "zzz"]})
    assert [r["id"] for r in one["results"]] == ["r1"] and any("zzz" in w for w in one["meta"]["warnings"])


# --- items_file, template, exports, links ---

async def test_items_file_exports_and_resource_links(tmp_path):
    root = tmp_path.resolve()
    (root / "in.csv").write_text("id,body\nA,first text 1\nB,second text 2\nC,third text 3\n")
    ctx = make_ctx(Srv().transport, tmp_path)
    exp = [{"format": "csv", "path": str(root / "e.csv")}, {"format": "markdown", "path": str(root / "e.md")},
           {"format": "ojui-batch", "path": str(root / "e.json")}]
    args = {"items_file": {"path": str(root / "in.csv"), "state_field": "body", "id_field": "id"}, "questions": Q,
            "output_path": str(root / "o.jsonl"), "export": exp, "max_items_per_call": 2}
    a = await call(ctx, args)
    assert a["import"]["format"] == "csv" and a["import"]["row_count"] == 3 and a["next_cursor"]
    assert "exports" not in a and not (root / "e.csv").exists()   # not finished: no exports yet
    assert [c["mimeType"] for c in ctx.links] == ["application/x-ndjson"]
    b = await call(ctx, {**args, "cursor": a["next_cursor"]})
    assert b["next_cursor"] is None and "import" not in b
    assert [e["format"] for e in b["exports"]] == ["csv", "markdown", "ojui-batch"]
    assert [c["mimeType"] for c in ctx.links] == ["application/x-ndjson", "text/csv", "text/markdown", "application/json"]
    assert all(c["type"] == "resource_link" and c["uri"].startswith("file://") for c in ctx.links)
    assert [c["name"] for c in ctx.links] == ["o.jsonl", "e.csv", "e.md", "e.json"]
    res = success_result(b, ctx.links)
    assert len(res["content"]) == 5 and res["content"][0]["type"] == "text"
    csv_text = (root / "e.csv").read_text()
    assert csv_text.splitlines()[0].startswith("index,id,state,status,q.noul") and len(csv_text.splitlines()) == 4
    assert b["exports"][0]["bytes"] == len(csv_text.encode())
    assert json.loads((root / "e.json").read_text())["rows"][2]["state"] == "third text 3"
    assert "| 3 | C |" in (root / "e.md").read_text()
    head, ids = file_ids(tmp_path)
    assert head["source"]["kind"] == "items_file" and head["source"]["id_field"] == "id" and ids == ["A", "B", "C"]
    await fails(ctx, args, "already exists")   # the finished job re-run would overwrite exports: refused


async def test_include_state_false(tmp_path):
    ctx = make_ctx(Srv().transport, tmp_path)
    await call(ctx, {"items": rows(1), "questions": Q, "output_path": str(tmp_path.resolve() / "o.jsonl"), "include_state": False})
    row = json.loads((tmp_path / "o.jsonl").read_text().splitlines()[1])
    assert "state" not in row and row["state_hash"].startswith("sha256:")


async def test_template_source_and_questions_only_with_items(tmp_path):
    from openjev_mcp import library
    tid = library.template_ids()[0]
    tpl = library.template(tid)
    ctx = make_ctx(stubs.fail_on_request_transport(), tmp_path)
    dry = await call(ctx, {"template": tid, "dry_run": True})
    assert dry["import"]["row_count"] == len(tpl["states"]) == dry["estimate"]["requests"]
    assert dry["first_body"]["questions"] == tpl["questions"] and dry["first_body"]["state"] == tpl["states"][0]["state"]
    mixed = await call(ctx, {"template": tid, "items": [{"state": "own state"}], "dry_run": True})
    assert mixed["first_body"]["state"] == "own state" and mixed["first_body"]["questions"] == tpl["questions"]
