"""The batch walkthrough of skill openjev-data-records (spec 4.8) against the stub engine, with only the arguments the skill shows:
dry run, run with output_path, follow next_cursor, interrupt, resume, review, export, compare."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import anyio
import pytest
import stubs

from openjev_mcp.config import Config
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools.dispatch import call_tool

pytestmark = pytest.mark.anyio

SKILL = (Path(__file__).resolve().parents[2] / "plugins" / "openjev-skills" / "skills" / "openjev-data-records" / "SKILL.md").read_text()
DEPT = {"billing": "money questions: invoices, refunds, charges, payment methods",
        "technical": "product defects: errors, outages, slow pages, integrations, sign-in trouble",
        "sales": "buying interest: quotes, demos, upgrades, enterprise plans",
        "other": "anything else, or too vague to tell"}
QUESTIONS = {"dept": {"type": "choice", "instructions": "Which team should own this ticket?", "criteria": DEPT}}
N = 7


def test_skill_dry_run_block_is_what_the_test_sends():
    block = json.loads(re.search(r"```json\n(\{\"items_file\".*?)\n```", SKILL, re.S).group(1))
    assert block["questions"] == QUESTIONS and block["dry_run"] is True
    assert set(block["items_file"]) == {"path", "id_field", "state_template"}
    for word in ("next_cursor", "resume: true", "retry_errors", "only_ids", "view: \"review\"", "compare_to", "include_state"):
        assert word in SKILL, word


def ctx_for(tmp_path, delay_s=0.0):
    engine = stubs.StubEngine(delay_s=delay_s)
    config = Config(base_url="http://oj.test", roots=(str(tmp_path.resolve()),), retries=1)
    client = OpenJevClient(config, transport=stubs.asgi_transport(stubs.openjev_app(engine=engine)))
    return ToolContext(config, client, LimitsCache(client), ProgressEmitter(None), None), engine


async def run(ctx, name, args):
    res = await call_tool(ctx, name, args)
    assert not res["isError"], res["content"][0]["text"]
    return res["structuredContent"]


def ids(path):
    return [json.loads(x).get("id") for x in Path(path).read_text().splitlines()[1:]]


async def test_nine_steps(tmp_path):
    root = tmp_path.resolve()
    src = root / "tickets.csv"
    with open(src, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticket_id", "subject", "body"])
        for i in range(1, N + 1):
            w.writerow([f"T{i}", f"Subject {i}", f"Body of ticket {i}, with a comma"])
    items_file = {"path": str(src), "id_field": "ticket_id", "state_template": "Subject: {subject}\n\n{body}"}
    ctx, engine = ctx_for(tmp_path, delay_s=0.0)

    # 1. dry run: no reads, import + preview + estimate
    dry = await run(ctx, "batch", {"items_file": items_file, "questions": QUESTIONS, "dry_run": True})
    assert engine.calls == [] and dry["status"]["stopped_reason"] == "dry_run"
    assert dry["import"]["row_count"] == N and len(dry["preview"]) == 3 and dry["estimate"]
    assert "Subject: Subject 1" in json.dumps(dry["preview"][0])

    # 2. run with output_path, 3. follow next_cursor with identical arguments
    out1 = str(root / "run1.jsonl")
    base = {"items_file": items_file, "questions": QUESTIONS, "output_path": out1, "max_items_per_call": 3}
    first = await run(ctx, "batch", base)
    assert first["status"]["stopped_reason"] == "max_items_per_call" and first["next_cursor"]
    cur, calls = first["next_cursor"], 1
    while cur:
        res = await run(ctx, "batch", {**base, "cursor": cur})
        cur, calls = res["next_cursor"], calls + 1
    assert res["status"]["stopped_reason"] == "complete" and calls == 3
    assert ids(out1) == [f"T{i}" for i in range(1, N + 1)]

    # a changed argument with a cursor is refused as the skill says
    again = await call_tool(ctx, "batch", {**base, "cursor": first["next_cursor"], "questions": {"dept": {**QUESTIONS["dept"], "instructions": "Who owns it?"}}})
    assert again["isError"] and "arguments changed" in again["content"][0]["text"]

    # 4. interruption (cancelled call, no result, no cursor), 5. resume with the same arguments and no cursor
    slow_ctx, slow = ctx_for(tmp_path, delay_s=0.05)
    out2 = str(root / "run2.jsonl")
    job2 = {"items_file": items_file, "questions": QUESTIONS, "output_path": out2, "max_items_per_call": 100}
    with anyio.move_on_after(0.12):
        await call_tool(slow_ctx, "batch", job2)
    done = len(ids(out2)) if Path(out2).exists() else 0
    assert done < N
    resumed = await run(ctx, "batch", {**job2, "resume": True})
    assert resumed["status"]["stopped_reason"] == "complete" and resumed["status"]["skipped"] == done
    assert ids(out2) == [f"T{i}" for i in range(1, N + 1)]

    # retry only what failed: nothing failed, nothing is read
    n = len(engine.calls)
    retry = await run(ctx, "batch", {**job2, "retry_errors": True})
    assert retry["status"]["stopped_reason"] == "complete" and len(engine.calls) == n

    # 6. triage without reading again
    n = len(engine.calls)
    review = await run(ctx, "batch_results", {"path": out1, "view": "review"})
    assert review["view"] == "review" and "review_queue" in review
    stats = await run(ctx, "batch_results", {"path": out1, "view": "stats"})
    assert stats["stats"]["n"] == N and "dept" in stats["stats"]["per_question"]
    rows = await run(ctx, "batch_results", {"path": out1, "filter": {"needs_review": True}, "sort_by": "confidence"})
    assert rows["view"] == "rows"

    # 7. export for people
    csv_path, md_path = root / "out.csv", root / "out.md"
    for fmt, p in (("csv", csv_path), ("markdown", md_path)):
        ex = await run(ctx, "batch_results", {"path": out1, "export": {"format": fmt, "path": str(p)}})
        assert ex["export"] and p.is_file() and "T1" in p.read_text()
    refused = await call_tool(ctx, "batch_results", {"path": out1, "export": {"format": "csv", "path": str(csv_path)}})
    assert refused["isError"]   # exports are created new; an existing path is refused
    out3, ex_csv = str(root / "run3.jsonl"), root / "done.csv"
    await run(ctx, "batch", {**job2, "output_path": out3, "export": [{"format": "csv", "path": str(ex_csv)}]})
    assert ex_csv.is_file()

    # 8. compare two runs (a reworded question)
    reworded = {"dept": {**QUESTIONS["dept"], "instructions": "Which team handles this ticket?"}}
    out4 = str(root / "run4.jsonl")
    await run(ctx, "batch", {"items_file": items_file, "questions": reworded, "output_path": out4})
    cmp = await run(ctx, "batch_results", {"path": out1, "compare_to": {"path": out4}})
    body = json.dumps(cmp)
    assert "agreement" in body and "flipped_ids" in body
    assert len(engine.calls) > n and not re.search(r'"isError": true', body)

    # 9. privacy: include_state false keeps only hashes
    out5 = str(root / "run5.jsonl")
    await run(ctx, "batch", {**job2, "output_path": out5, "include_state": False})
    assert "Body of ticket" not in Path(out5).read_text()
