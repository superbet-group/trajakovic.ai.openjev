"""Live: phase-2/3 tools through an own MCP (port 8190-8199) against a real OpenJev (not CI). Sequential, one model.

    OPENJEV_LIVE=1 .venv/bin/python -m pytest -q mcp/tests/live/test_live_tools.py
Small versions of what run_live.py --full records: filter, ask_image, calibrate, compile, generate, a cursor-followed and an
interrupted-then-resumed batch, batch_results, the hook events and Tasks. Skipped unless --live / OPENJEV_LIVE=1."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import run_live as rl  # noqa: E402

pytestmark = [pytest.mark.live, pytest.mark.anyio]
PORT = int(os.environ.get("OJ_LIVE_PORT", 8194))
URL = f"http://127.0.0.1:{PORT}/mcp"


@pytest.fixture(scope="module")
def server():
    try:
        urllib.request.urlopen(rl.BASE + "/health", timeout=5)
    except Exception as e:
        pytest.skip(f"OpenJev not reachable at {rl.BASE}: {e}")
    work = Path(tempfile.mkdtemp(prefix="oj_live_t_"))
    csv_path = work / "rows.csv"
    rl.make_csv(csv_path, n=40)
    proc = rl.spawn_mcp(PORT, [work, rl.ROOT / "tests/data"])
    try:
        yield work, csv_path
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
        shutil.rmtree(work, ignore_errors=True)


async def with_session(fn):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    async with streamable_http_client(URL) as (r, w, *_):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


def test_make_csv_is_deterministic(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    rl.make_csv(a, 200); rl.make_csv(b, 200)
    assert a.read_bytes() == b.read_bytes() and a.read_text().count("\n") > 200   # multi-line states quoted


async def test_filter_ex_filter(server):
    ex = rl.load("00-spec-examples.json")["ex-filter"]["request"]
    import re
    lines = re.findall(r"^(L\d) (.*)$", ex["state"], re.M)
    crit = ex["questions"]["L1"]
    args = {"task": "find log lines that show a real failure an on-call engineer must act on.", "items_label": "LOG LINES", "items": [{"id": i, "text": t} for i, t in lines],
            "criterion": crit["instructions"].replace("L1", "{id}"), "true_means": crit["criteria"]["true"], "false_means": crit["criteria"]["false"]}

    async def go(s):
        return rl.sc_of((await rl.call(s, "filter", args))[0])
    out = await with_session(go)
    assert out["kept"] == ["L4"] and out["meta"]["requests"] == 1


async def test_ask_image_ex_image(server):
    ex = rl.load("00-spec-examples.json")["ex-image"]["request"]

    async def go(s):
        return rl.sc_of((await rl.call(s, "ask_image", {"images": [{"path": str(rl.ROOT / "tests/data/hotdog.jpg")}], "state": ex["state"], "questions": ex["questions"]}))[0])
    a = (await with_session(go))["answers"]
    assert a["hotdog"]["p"] >= 0.9 and a["cat"]["p"] <= 0.1


async def test_calibrate_ex_cal(server):
    ex = rl.load("00-spec-examples.json")
    exs = [{"id": f"ex-cal-{i}", "state": ex[f"ex-cal-{i}"]["request"]["state"], "label": {"escalate": ex[f"ex-cal-{i}"]["label"]}} for i in range(1, 8)]

    async def go(s):
        return rl.sc_of((await rl.call(s, "calibrate", {"questions": ex["ex-cal-1"]["request"]["questions"], "examples": exs, "options": {"samples": 1}}))[0])
    pq = (await with_session(go))["per_question"]["escalate"]
    assert pq["accuracy_at_0.5"] == 1.0 and pq["separable"] and pq["most_borderline"] == "ex-cal-7"


async def test_compile_routes_and_types(server):
    ex = rl.load("00-spec-examples.json")
    subs = [ex[f"ex-compile-qtype-{i}"]["request"]["state"].removeprefix("A human wants this decided about an input: ") for i in range(1, 5)]

    async def go(s):
        a = rl.sc_of((await rl.call(s, "compile", {"intent": "tell me if a support email is angry and whether billing, tech or sales should take it", "sub_decisions": subs}))[0])
        b = rl.sc_of((await rl.call(s, "compile", {"intent": "write me a release announcement for version 2.0"}))[0])
        return a, b
    a, b = await with_session(go)
    assert a["recipe"]["id"] == "ticket_triage" and [d.get("qtype") or d.get("type") for d in a["sub_decisions"]] == ["noul", "choice", "score", "not_typed"]
    assert b["recipe"]["id"] == "none"


async def test_generate_retries_empty_reply_once(server):
    async def go(s):
        return rl.sc_of((await rl.call(s, "generate", {"messages": [{"role": "user", "content": "What is 2+2? Answer with one number."}], "max_tokens": 16}))[0])
    out = await with_session(go)
    assert "4" in out["content"]   # the empty-reply flake (spec 6.4) is retried once by the tool


async def test_batch_cursor_loop_and_resume_without_gaps(server):
    work, csv_path = server
    outp = work / "cur.jsonl"
    args = {"items_file": {"path": str(csv_path), "state_field": "state", "id_field": "id"}, "questions": rl.QS, "concurrency": 2, "output_path": str(outp),
            "max_items": 12, "max_items_per_call": 5, "export": [{"format": "csv", "path": str(work / "cur.csv")}]}

    async def go(s):
        calls, _ = await rl.drain(s, args)
        again = rl.sc_of((await rl.call(s, "batch", {**args, "export": []}))[0])    # resume without a cursor: everything is skipped
        view = rl.sc_of((await rl.call(s, "batch_results", {"path": str(outp), "view": "review"}))[0])
        stats = rl.sc_of((await rl.call(s, "batch_results", {"path": str(outp), "view": "stats"}))[0])
        return calls, again, view, stats
    calls, again, view, stats = await with_session(go)
    assert len(calls) == 3 and sum(c["done"] for c in calls) == 12 and not calls[-1]["has_cursor"]
    dg = rl.dup_gap(outp, [f"r{i + 1:03d}" for i in range(12)])
    assert dg["n_gaps"] == 0 and dg["n_duplicate_ok"] == 0
    assert again["status"]["skipped"] == 12 and again["status"]["ok"] == 0
    assert "review_queue" in view and "stats" in stats and (work / "cur.csv").exists()


async def test_batch_interrupted_by_disconnect_then_resumed(server):
    work, csv_path = server
    outp = work / "int.jsonl"
    args = {"items_file": {"path": str(csv_path), "state_field": "state", "id_field": "id"}, "questions": rl.QS, "concurrency": 1, "output_path": str(outp), "max_items_per_call": 40}
    assert await rl.raw_interrupt(URL, args, 1.5)   # still running when the POST is closed
    import asyncio
    await asyncio.sleep(1.5)
    before = len(rl.read_rows(outp)[1])

    async def go(s):
        return rl.sc_of((await rl.call(s, "batch", args))[0])
    out = await with_session(go)
    ids = [f"r{i + 1:03d}" for i in range(40)]
    dg = rl.dup_gap(outp, ids)
    assert 0 < before < 40 and out["status"]["skipped"] == before and dg["n_gaps"] == 0 and dg["n_duplicate_ok"] == 0


def test_hook_events(server):
    stop = {**json.load(open(rl.FIX / "stop_v2.json")), "transcript_path": str(rl.FIX / "stop_v2_transcript.jsonl")}
    assert rl.hook_run(["stop"], stop)[0].get("decision") == "block"
    up = rl.hook_run(["userprompt", "--roster", str(rl.FIX / "skill_roster.json")], json.load(open(rl.FIX / "userprompt_v2.json")))[0]
    assert "'pdf'" in up["hookSpecificOutput"]["additionalContext"]
    assert rl.hook_run(["posttooluse"], json.load(open(rl.FIX / "posttooluse_v2_webfetch.json")))[0].get("decision") == "block"


def test_gate_hook_14_of_14():
    n, rows, _ = rl.gate_hook()
    assert n == 14, [r for r in rows if not r["ok"]]


async def test_tasks_on(server):
    work, _ = server
    proc = rl.spawn_mcp(8195, [work], {"OPENJEV_MCP_TASKS": "on"})
    try:
        rec = rl.Rec()
        await rl.tasks_section(rec, "http://127.0.0.1:8195/mcp")
    finally:
        proc.terminate()
    assert rec.checks[-1]["pass"], rec.checks[-1]["mismatches"]
