"""batch_results tool: views, filter, sort, cursor, exports, compare. Files only; any request fails the test."""
from __future__ import annotations

import base64
import json
import os
import time

import pytest
import stubs
from openjev_mcp import schemas, validate
from openjev_mcp.batch import exporters, stats, store
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache, default_limits
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools import batch_results as br
from openjev_mcp.tools.dispatch import call_tool

pytestmark = pytest.mark.anyio


def mk_ctx(tmp_path):
    config = Config(base_url="http://oj.test", roots=(str(tmp_path.resolve()),))
    transport = stubs.fail_on_request_transport()
    client = OpenJevClient(config, transport=transport)
    limits = LimitsCache(client, ttl_s=1e9)
    limits._limits, limits._fetched = default_limits(), time.monotonic()
    return ToolContext(config, client, limits, ProgressEmitter(None), None), transport


def noul(p):
    return {"type": "noul", "p": p, "band": "yes" if p >= 0.7 else "no" if p <= 0.3 else "grey", "margin": abs(2 * p - 1)}


def choice(top, p_top, rest=("other", 1.0)):
    return {"type": "choice", "choice": top, "p_top": p_top, "probabilities": {top: p_top, rest[0]: round(1 - p_top, 6)},
            "confidence": p_top, "margin": 0.1, "abstained": False}


def score(level, probs):
    return {"type": "score", "score": level + 0.1, "level": level, "level_label": f"L{level}", "probabilities": probs,
            "confidence": 0.5, "spread": 0.3}


# 8 ok rows (urgent p, team, sev) in file order = index order, plus one error row
DATA = [("r1", 0.95, "pay", 0.9, 2), ("r2", 0.5, "plat", 0.6, 1), ("r3", 0.1, "pay", 0.95, 0), ("r4", 0.8, "plat", 0.55, 2),
        ("r5", 0.65, "pay", 0.7, 1), ("r6", 0.05, "plat", 0.99, 0), ("r7", 0.85, "pay", 0.65, 1), ("r8", 0.4, "plat", 0.85, 2)]
LV = {0: {"0": 0.8, "1": 0.1, "2": 0.1}, 1: {"0": 0.1, "1": 0.8, "2": 0.1}, 2: {"0": 0.1, "1": 0.1, "2": 0.8}}


def make_row(i, rid, p, team, tp, lvl, reasons=None):
    ans = {"urgent": noul(p), "team": choice(team, tp, ("plat" if team == "pay" else "pay", 0)), "sev": score(lvl, LV[lvl])}
    reasons = stats.review_reasons(ans, {})
    return {"index": i, "id": rid, "status": "ok", "state": f"state {rid}", "state_hash": "sha256:x", "answers": ans,
            "needs_review": bool(reasons), "review_reasons": reasons, "audit": rid == "r2", "model": "openjev-0.1",
            "usage": {"input_tokens": 10, "output_tokens": 1}, "latency_ms": 100.0 + i, "request_id": f"q{i}", "body_hash": "sha256:b", "error": None}


def write_out(tmp_path, name="o.jsonl", data=DATA, extra=()):
    header = store.make_header(run_id="sha256:r", question_hash="sha256:q", created_at="2026-10-02T00:00:00Z")
    rows = [make_row(i + 1, *d) for i, d in enumerate(data)]
    rows.append({"index": len(rows) + 1, "id": "bad", "status": "error", "needs_review": True, "review_reasons": ["error"], "state_hash": "sha256:e", "state": "boom",
                 "error": {"code": "OJ_TIMEOUT", "message": "timed out"}, "latency_ms": 5000.0})
    p = tmp_path / name
    p.write_text("".join(json.dumps(x, separators=(",", ":")) + "\n" for x in [header, *rows, *extra]), encoding="utf-8")
    return str(p)


async def run(ctx, **args):
    res = await call_tool(ctx, "batch_results", args)
    assert not res["isError"], res
    assert validate.validate_output("batch_results", res["structuredContent"]) == []
    return res["structuredContent"]


@pytest.fixture(autouse=True)
def keep_registry():
    """register() adds batch_results to the global schema registry; P14 owns that wiring, so undo it per test."""
    yield
    schemas.INPUT_SCHEMAS.pop("batch_results", None)
    schemas.OUTPUT_SCHEMAS.pop("batch_results", None)
    validate._validator.cache_clear()


@pytest.fixture
def tool(tmp_path):
    ctx, transport = mk_ctx(tmp_path)
    from openjev_mcp.tools import dispatch
    spec = br.register(ctx.config)
    dispatch._BY_NAME["batch_results"] = spec
    yield ctx, transport
    dispatch._BY_NAME.pop("batch_results", None)


def msg(res):
    return json.loads(res["content"][0]["text"])["error"]["message"]


def ids(out):
    return [r["id"] for r in out["rows"]]


def test_register_annotations_and_schema():
    spec = br.register(Config())
    assert spec.name == "batch_results" and spec.annotations == {"title": "Batch results", "readOnlyHint": False,
                                                                "destructiveHint": False, "idempotentHint": False,
                                                                "openWorldHint": False}
    assert spec.input_schema["required"] == ["path"] and spec.input_schema["additionalProperties"] is False
    assert spec.input_schema is schemas.INPUT_SCHEMAS["batch_results"]
    assert validate.validate_args("batch_results", {"path": "x", "limit": 0}) is not None


async def test_rows_default_last_row_wins_and_compact(tool, tmp_path):
    ctx, transport = tool
    newer = make_row(2, "r2", 0.99, "pay", 0.99, 0)   # re-run of r2 appended later: last row wins
    path = write_out(tmp_path, extra=[newer])
    out = await run(ctx, path=path)
    assert ids(out) == ["r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "bad"] and out["matched"] == 9 and out["next_cursor"] is None
    r2 = out["rows"][1]
    assert r2["answers"]["urgent"] == {"p": 0.99, "band": "yes"} and r2["min_confidence"] == pytest.approx(0.8)
    assert out["rows"][-1]["error"]["code"] == "OJ_TIMEOUT" and out["rows"][-1]["min_confidence"] is None
    full = await run(ctx, path=path, detail="full", limit=1)
    assert full["rows"][0]["body_hash"] == "sha256:b" and full["rows"][0]["answers"]["team"]["probabilities"]
    assert transport.requests == []


async def test_sort_by_value_confidence_and_order(tool, tmp_path):
    ctx, _ = tool
    path = write_out(tmp_path)
    by_p = await run(ctx, path=path, sort_by="value", sort_question="urgent", order="desc")
    assert ids(by_p)[:3] == ["r1", "r7", "r4"] and ids(by_p)[-1] == "bad"
    asc = await run(ctx, path=path, sort_by="value", sort_question="urgent")
    assert ids(asc)[:3] == ["r6", "r3", "r8"] and ids(asc)[-1] == "bad"
    team = await run(ctx, path=path, sort_by="value", sort_question="team")   # choice key, ties in index order
    assert ids(team)[:4] == ["r1", "r3", "r5", "r7"]
    sev = await run(ctx, path=path, sort_by="value", sort_question="sev")
    assert ids(sev)[:2] == ["r3", "r6"]
    conf = await run(ctx, path=path, sort_by="confidence", sort_question="urgent")   # |2p-1|
    assert ids(conf)[:3] == ["r2", "r8", "r5"]
    conf_all = await run(ctx, path=path, sort_by="confidence", order="desc")   # min over the three questions
    mins = {r["id"]: r["min_confidence"] for r in conf_all["rows"]}
    assert ids(conf_all)[-1] == "bad" and mins["r2"] == 0.0 and mins["r1"] == 0.8
    assert ids(conf_all)[:-1] == sorted(ids(conf_all)[:-1], key=lambda i: (-mins[i], i))
    desc_idx = await run(ctx, path=path, order="desc")
    assert ids(desc_idx)[:2] == ["bad", "r8"]
    with pytest.raises(AssertionError):
        await run(ctx, path=path, sort_by="value", sort_question="nope")


async def test_filters(tool, tmp_path):
    ctx, _ = tool
    path = write_out(tmp_path)
    assert ids(await run(ctx, path=path, filter={"status": "error"})) == ["bad"]
    assert len(ids(await run(ctx, path=path, filter={"status": "ok"}))) == 8
    # min_confidence_below: any question below 0.3 -> |2p-1| < 0.35: r2 (0), r5 (0.3), r8 (0.2)
    low = await run(ctx, path=path, filter={"min_confidence_below": 0.35, "question": "urgent"})
    assert ids(low) == ["r2", "r5", "r8"] and low["matched"] == 3
    anyq = await run(ctx, path=path, filter={"min_confidence_below": 0.6})
    assert "r2" in ids(anyq) and "r1" not in ids(anyq) and "bad" not in ids(anyq)
    assert ids(await run(ctx, path=path, filter={"question": "team", "choice": "plat"})) == ["r2", "r4", "r6", "r8"]
    assert ids(await run(ctx, path=path, filter={"question": "urgent", "band": "grey"})) == ["r2", "r5", "r8"]
    assert ids(await run(ctx, path=path, filter={"ids": ["r3", "r1", "nope"]})) == ["r1", "r3"]
    assert ids(await run(ctx, path=path, filter={"audit": True})) == ["r2"]
    nr = await run(ctx, path=path, filter={"needs_review": True})
    assert all(r["needs_review"] for r in nr["rows"]) and "r1" not in ids(nr)
    res = await call_tool(ctx, "batch_results", {"path": path, "filter": {"choice": "x"}})
    assert res["isError"]


async def test_review_order_and_stats(tool, tmp_path):
    ctx, _ = tool
    path = write_out(tmp_path)
    out = await run(ctx, path=path, view="review")
    q = out["review_queue"]
    confs = [e["confidence"] for e in q]
    assert confs == sorted(confs) and confs[0] == 0.0 and {"id": "bad", "question": None, "reason": "error", "confidence": 0.0} in q
    assert "rows" not in out and out["matched"] == 9
    st = (await run(ctx, path=path, view="stats"))["stats"]
    assert (st["n"], st["ok"], st["errors"]) == (9, 8, 1) and st["input_tokens"] == 80 and st["output_tokens"] == 8
    assert st["per_question"]["urgent"]["n"] == 8 and st["per_question"]["team"]["counts"] == {"pay": 4, "plat": 4}
    assert st["latency_ms_p50"] == 105.0 and st["latency_ms_p95"] == 5000.0
    sub = (await run(ctx, path=path, view="stats", filter={"status": "ok"}))["stats"]
    assert sub["n"] == 8 and sub["errors"] == 0


async def test_cursor_pages_without_repeats(tool, tmp_path):
    ctx, _ = tool
    path = write_out(tmp_path)
    seen, cursor, calls = [], None, 0
    while True:
        args = dict(path=path, limit=4, sort_by="confidence", sort_question="urgent")
        out = await run(ctx, **args, **({"cursor": cursor} if cursor else {}))
        seen += ids(out)
        calls += 1
        cursor = out["next_cursor"]
        if cursor is None:
            break
    full = await run(ctx, path=path, sort_by="confidence", sort_question="urgent")
    assert seen == ids(full) and len(set(seen)) == 9 and calls == 3
    token = json.loads(base64.urlsafe_b64decode(out and (await run(ctx, path=path, limit=2))["next_cursor"] + "=="))
    assert token["v"] == 1 and token["offset"] == 2 and isinstance(token["args"], str)
    first = await run(ctx, path=path, limit=2)
    other = await call_tool(ctx, "batch_results", {"path": path, "limit": 2, "order": "desc", "cursor": first["next_cursor"]})
    assert other["isError"] and "different query" in msg(other)
    bad = await call_tool(ctx, "batch_results", {"path": path, "cursor": "!!"})
    assert bad["isError"]
    rq1 = await run(ctx, path=path, view="review", limit=2)
    rq2 = await run(ctx, path=path, view="review", limit=2, cursor=rq1["next_cursor"])
    assert {e["id"] for e in rq1["review_queue"]}.isdisjoint(e["id"] for e in rq2["review_queue"])


async def test_exports_match_batch_exporters(tool, tmp_path):
    ctx, _ = tool
    path = write_out(tmp_path)
    header, rows, _, _ = store.read_output(path, ctx.config)
    ordered = sorted(rows.values(), key=lambda r: r["index"])
    qs = br.infer_questions(rows)
    assert qs["team"]["criteria"] == {"pay": "", "plat": ""} and qs["sev"]["criteria"] == ["0", "1", "2"]
    inline = {f: (await run(ctx, path=path, export={"format": f}))["export"] for f in ("csv", "markdown", "jsonl", "ojui-batch")}
    assert inline["csv"]["inline"] == exporters.to_csv(header, ordered, qs) and inline["csv"]["path"] is None
    assert inline["markdown"]["inline"] == exporters.to_markdown(header, ordered, qs)
    assert inline["jsonl"]["inline"] == exporters.to_jsonl(header, ordered)
    oj = json.loads(inline["ojui-batch"]["inline"])
    want = json.loads(exporters.to_ojui_batch(header, ordered, qs, header["options"], exported_at=oj["exportedAt"], title="batch"))
    assert oj == want and oj["format"] == "ojui-batch" and len(oj["rows"]) == 9
    # to files: new only, link emitted, bytes reported
    target = tmp_path / "out.csv"
    res = await call_tool(ctx, "batch_results", {"path": path, "export": {"format": "csv", "path": str(target)}})
    exp = res["structuredContent"]["export"]
    assert exp["path"] == str(target.resolve()) and exp["inline"] is None and target.read_text(encoding="utf-8") == inline["csv"]["inline"]
    assert exp["bytes"] == target.stat().st_size and res["content"][-1]["type"] == "resource_link"
    assert res["content"][-1]["uri"] == "file://" + str(target.resolve())
    again = await call_tool(ctx, "batch_results", {"path": path, "export": {"format": "csv", "path": str(target)}})
    assert again["isError"] and "already exists" in msg(again)
    wrong = await call_tool(ctx, "batch_results", {"path": path, "export": {"format": "csv", "path": str(tmp_path / "o.md")}})
    assert wrong["isError"]
    md = await call_tool(ctx, "batch_results", {"path": path, "export": {"format": "ojui-batch", "path": str(tmp_path / "o.json")}})
    assert json.loads((tmp_path / "o.json").read_text())["title"] == "o"
    assert not md["isError"]


async def test_filtered_jsonl_export_keeps_header_and_is_readable(tool, tmp_path):
    ctx, _ = tool
    path = write_out(tmp_path)
    target = tmp_path / "plat.jsonl"
    out = await run(ctx, path=path, filter={"question": "team", "choice": "plat"}, export={"format": "jsonl", "path": str(target), "filtered": True})
    assert out["export"]["path"] == str(target.resolve()) and out["matched"] == 4
    first = json.loads(target.read_text().splitlines()[0])
    assert first["openjev_mcp"] == "batch" and first["run_id"] == "sha256:r"
    again = await run(ctx, path=str(target))
    assert ids(again) == ["r2", "r4", "r6", "r8"]
    header, rows, n, _ = store.read_output(str(target), ctx.config)   # what batch resume reads
    assert header["run_id"] == "sha256:r" and set(rows) == {"r2", "r4", "r6", "r8"} and n == 5
    unfiltered = await run(ctx, path=path, filter={"status": "ok"}, export={"format": "jsonl", "path": str(tmp_path / "all.jsonl")})
    assert len((tmp_path / "all.jsonl").read_text().splitlines()) == 10   # filtered:false exports every row


async def test_inline_export_truncates_at_64_kib(tool, tmp_path):
    ctx, _ = tool
    big = [(f"r{i}", 0.9, "pay", 0.9, 1) for i in range(400)]
    path = write_out(tmp_path, data=big)
    out = await run(ctx, path=path, export={"format": "jsonl"})
    e = out["export"]
    assert e["truncated"] is True and len(e["inline"].encode()) <= 64 * 1024 and e["bytes"] > 64 * 1024
    assert any("truncated" in w for w in out["warnings"])
    small = await run(ctx, path=path, filter={"ids": ["r1"]}, export={"format": "csv", "filtered": True})
    assert small["export"]["truncated"] is False and small["export"]["bytes"] == len(small["export"]["inline"].encode())


async def test_compare_and_errors(tool, tmp_path):
    ctx, transport = tool
    a = write_out(tmp_path, "a.jsonl")
    out = await run(ctx, path=a, compare_to={"path": a})
    c = out["compare"]
    assert c["matched"] == 9 and c["max_jsd"] == 0.0 and c["only_in_a"] == []
    assert c["per_question"]["urgent"]["agreement"] == 1.0
    flipped = [("r1", 0.05, "plat", 0.9, 2)] + DATA[1:6]   # r1 flips; r7, r8 only in a
    b = write_out(tmp_path, "b.jsonl", data=flipped)
    c = (await run(ctx, path=a, compare_to={"path": b}))["compare"]
    assert c["only_in_a"] == ["r7", "r8"] and c["only_in_b"] == [] and c["matched"] == 7
    assert c["per_question"]["urgent"]["flipped_ids"] == ["r1"] and c["max_jsd"] > 0.5
    assert c["per_question"]["team"]["jsd_reason"] is None
    nohdr = tmp_path / "plain.jsonl"
    nohdr.write_text('{"id":"a","index":1}\n')
    res = await call_tool(ctx, "batch_results", {"path": str(nohdr)})
    assert res["isError"] and "batch header" in msg(res)
    bad = tmp_path / "bad.jsonl"
    bad.write_text(open(a).read().replace('"id":"r3"', "{{", 1))
    assert (await call_tool(ctx, "batch_results", {"path": str(bad)}))["isError"]
    out_of_roots = await call_tool(ctx, "batch_results", {"path": "/etc/passwd.jsonl"})
    assert out_of_roots["isError"]
    assert transport.requests == []
