"""start_batch / review_batch prompt specs: documented messages, PromptError on bad arguments. No network."""
from __future__ import annotations

import json
import time

import pytest
import stubs
from openjev_mcp import batch_prompts as bp
from openjev_mcp import library, prompts
from openjev_mcp.batch import stats
from openjev_mcp.config import Config
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache, default_limits
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools.batch_results import infer_questions, load
from test_mcp_batch_results import write_out

pytestmark = pytest.mark.anyio


@pytest.fixture
def ctx(tmp_path):
    config = Config(base_url="http://oj.test", roots=(str(tmp_path.resolve()),))
    client = OpenJevClient(config, transport=stubs.fail_on_request_transport())
    limits = LimitsCache(client, ttl_s=1e9)
    limits._limits, limits._fetched = default_limits(), time.monotonic()
    return ToolContext(config, client, limits, ProgressEmitter(None), None)


@pytest.fixture
def registered(monkeypatch):
    monkeypatch.setattr(prompts, "PROMPTS", [bp.START_BATCH, bp.REVIEW_BATCH])


def test_specs_shape():
    s, r = bp.START_BATCH, bp.REVIEW_BATCH
    assert [(a.name, a.required, a.complete) for a in s.arguments] == [("template", True, True), ("items_path", False, False),
                                                                        ("output_path", False, False)]
    assert [(a.name, a.required) for a in r.arguments] == [("output_path", True), ("limit", False)]
    assert (s.name, r.name) == ("start_batch", "review_batch")
    assert bp.COMPLETERS[("ref/prompt", "start_batch", "template")]("", None) == library.template_ids()


async def test_start_batch_plan_and_template_resource(ctx, registered):
    tid = library.template_ids()[0]
    out = await prompts.get("start_batch", {"template": tid, "items_path": "/x/items.csv", "output_path": "/x/out.jsonl"}, ctx)
    plan, res = out["messages"]
    assert plan["role"] == res["role"] == "user" and plan["content"]["type"] == "text"
    t = plan["content"]["text"]
    for needle in ("dry_run", "import mapping", "estimate", "output_path: /x/out.jsonl", "next_cursor", "resume: true",
                   'view: "review"', "/x/items.csv"):
        assert needle in t
    assert t.index("dry_run") < t.index("resume: true") < t.index('view: "review"')
    r = res["content"]
    assert r["type"] == "resource" and r["resource"]["uri"] == f"openjev://templates/{tid}"
    assert r["resource"]["mimeType"] == "application/json" and json.loads(r["resource"]["text"])["id"] == tid
    bare = await prompts.get("start_batch", {"template": tid}, ctx)
    assert "example states" in bare["messages"][0]["content"]["text"]


async def test_start_batch_errors(ctx, registered):
    for args, text in (({}, "Missing required argument: template"), ({"template": ""}, "Missing required"),
                       ({"template": "nope"}, "Unknown template"),
                       ({"template": library.template_ids()[0], "bogus": 1}, "Unknown argument")):
        with pytest.raises(prompts.PromptError, match=text):
            await prompts.get("start_batch", args, ctx)


async def test_review_batch_queue_and_per_question(ctx, registered, tmp_path):
    path = write_out(tmp_path)
    out = await prompts.get("review_batch", {"output_path": path}, ctx)
    howto, res = out["messages"]
    assert howto["content"]["type"] == "text" and "batch_results" in howto["content"]["text"]
    r = res["content"]["resource"]
    assert r["mimeType"] == "application/json"
    body = json.loads(r["text"])
    header, rows, _ = load(path, ctx.config)
    qs = infer_questions(rows)
    want = stats.review_queue(rows.values(), qs, header.get("review_rule"))
    assert [e["id"] for e in body["review_queue"]] == [e["id"] for e in want][:20]
    assert body["per_question"] == json.loads(json.dumps(stats.per_question(rows.values(), qs)))
    assert body["n"] == 9 and body["needs_review"] == len(want) and body["review_queue"][0]["state"].startswith("state ")


async def test_review_batch_limit_and_errors(ctx, registered, tmp_path):
    path = write_out(tmp_path)
    body = json.loads((await prompts.get("review_batch", {"output_path": path, "limit": "2"}, ctx))["messages"][1]["content"]["resource"]["text"])
    assert len(body["review_queue"]) == 2
    for args, text in (({}, "Missing required argument: output_path"), ({"output_path": path, "limit": "x"}, "integer"),
                       ({"output_path": path, "limit": "0"}, "between"), ({"output_path": str(tmp_path / "none.jsonl")}, "not found"),
                       ({"output_path": "/etc/hosts.jsonl"}, "outside")):
        with pytest.raises(prompts.PromptError, match=text):
            await prompts.get("review_batch", args, ctx)
    plain = tmp_path / "plain.jsonl"
    plain.write_text('{"id":"a"}\n')
    with pytest.raises(prompts.PromptError, match="batch header"):
        await prompts.get("review_batch", {"output_path": str(plain)}, ctx)
