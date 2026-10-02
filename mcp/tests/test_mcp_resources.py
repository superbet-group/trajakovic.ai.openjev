"""resources.py: schema, limits, recipes, templates, patterns, guide, templates and file:// outputs (spec 2.18)."""
from __future__ import annotations

import json
import re
import time

import httpx
import pytest
import stubs

from openjev_mcp import completion, library, schemas
from openjev_mcp.batch import store
from openjev_mcp.recipes import registry
from openjev_mcp.config import Config
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.resources import (BUILD_TIME, RESOURCES, TEMPLATES, ResourceNotFound, listing, read_resource,
                                   templates_listing)
from openjev_mcp.tools import ToolContext

BASE = "http://oj.test:8080"
MODELS = {"models": [{"name": "openjev-latest"}, {"name": "openjev-0.1"}, {"name": "diffusiongemma-26b"}]}


def make_ctx(**cfg) -> ToolContext:
    app = stubs.openjev_app()
    config = Config(base_url=BASE, **cfg)
    client = OpenJevClient(config, transport=stubs.asgi_transport(app))
    return ToolContext(config, client, LimitsCache(client), ProgressEmitter.noop(), None)


def test_resources_order_and_fields():
    assert [r["uri"] for r in RESOURCES] == ["openjev://schema", "openjev://limits", "openjev://recipes",
                                             "openjev://templates", "openjev://patterns", "openjev://guide/authoring"]
    schema, limits = RESOURCES[:2]
    assert (schema["name"], schema["title"], schema["mimeType"]) == (
        "schema", "OpenJev common types", "application/schema+json")
    assert (limits["name"], limits["title"], limits["mimeType"]) == ("limits", "OpenJev limits", "application/json")
    assert schema["annotations"] == {"audience": ["assistant"], "priority": 0.6, "lastModified": BUILD_TIME}
    assert limits["annotations"] == {"audience": ["assistant"], "priority": 0.8}
    assert all(r["description"] for r in RESOURCES)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", BUILD_TIME)


def test_phase_two_resources_have_names_titles_mime_and_annotations():
    by = {r["uri"]: r for r in RESOURCES}
    want = {"openjev://recipes": (["assistant"], 0.5, "application/json"),
            "openjev://templates": (["user", "assistant"], 0.5, "application/json"),
            "openjev://patterns": (["assistant"], 0.4, "application/json"),
            "openjev://guide/authoring": (["user", "assistant"], 0.4, "text/markdown")}
    for uri, (audience, priority, mime) in want.items():
        r = by[uri]
        assert r["name"] and r["title"] and r["description"] and r["mimeType"] == mime
        assert r["annotations"] == {"audience": audience, "priority": priority, "lastModified": BUILD_TIME}


def test_templates_listing():
    out = templates_listing()
    assert [t["uriTemplate"] for t in out] == [
        "openjev://recipes/{id}", "openjev://templates/{id}", "openjev://audits/{question_hash}"] == [
        t["uriTemplate"] for t in TEMPLATES]
    for t in out:
        assert t["name"] and t["title"] and t["mimeType"] == "application/json"
        assert t["annotations"]["priority"] in (0.5, 0.7)
    assert out[0]["annotations"]["lastModified"] == BUILD_TIME
    assert out[0]["annotations"]["audience"] == ["assistant"] and out[1]["annotations"]["audience"] == ["user", "assistant"]
    out[0]["annotations"]["priority"] = 9
    assert templates_listing()[0]["annotations"]["priority"] == 0.5


def test_listing_is_stable_and_leaves_resources_untouched():
    out = listing()
    assert [r["uri"] for r in out] == [r["uri"] for r in RESOURCES]
    assert out[0]["annotations"]["lastModified"] == BUILD_TIME
    assert "lastModified" not in out[1]["annotations"]
    assert all(r["annotations"]["lastModified"] == BUILD_TIME for r in out[2:])
    time.sleep(1.1)
    assert listing() == out


@pytest.mark.anyio
async def test_read_schema():
    res = await read_resource("openjev://schema", make_ctx())
    assert res["ttlMs"] == 3600000 and res["cacheScope"] == "public"
    [item] = res["contents"]
    assert item["uri"] == "openjev://schema" and item["mimeType"] == "application/schema+json"
    assert json.loads(item["text"]) == schemas.SCHEMA_RESOURCE
    assert {"State", "ToolError", "LintFinding", "QuestionStats", "BatchHeader", "BatchRow"} <= set(
        json.loads(item["text"])["$defs"])


@pytest.mark.anyio
async def test_read_limits_matches_limits_resource():
    ctx = make_ctx(max_inflight_batch=3)
    res = await read_resource("openjev://limits", ctx)
    assert res["ttlMs"] == 60000 and res["cacheScope"] == "private"
    [item] = res["contents"]
    assert item["uri"] == "openjev://limits" and item["mimeType"] == "application/json"
    payload = json.loads(item["text"])
    assert payload == (await ctx.limits.get()).resource(ctx.config)
    assert payload["limit_source"] == "default" and payload["batch"]["max_inflight_batch"] == 3
    assert payload["limits"]["prompt_tokens"] is None


@pytest.mark.anyio
async def test_read_limits_falls_back_to_defaults_when_openjev_is_down():
    config = Config(base_url=BASE)
    client = OpenJevClient(config, transport=stubs.fault_transport(exc=httpx.ConnectError("refused")))
    ctx = ToolContext(config, client, LimitsCache(client), ProgressEmitter.noop(), None)
    res = await read_resource("openjev://limits", ctx)
    assert json.loads(res["contents"][0]["text"])["limit_source"] == "default"


@pytest.mark.anyio
@pytest.mark.parametrize("uri", ["openjev://nope", "openjev://schema/", "", "https://x/y"])
async def test_unknown_uri(uri):
    with pytest.raises(ResourceNotFound) as e:
        await read_resource(uri, make_ctx())
    assert isinstance(e.value, LookupError)


@pytest.mark.anyio
async def test_schema_read_is_stateless():
    ctx = make_ctx()
    first = await read_resource("openjev://schema", ctx)
    await read_resource("openjev://limits", ctx)
    assert await read_resource("openjev://schema", ctx) == first
    assert ctx.client is not None and not ctx.warnings


@pytest.mark.anyio
@pytest.mark.parametrize("uri,mime", [("openjev://recipes", "application/json"), ("openjev://templates", "application/json"),
                                      ("openjev://patterns", "application/json"),
                                      ("openjev://guide/authoring", "text/markdown")])
async def test_read_static_phase_two(uri, mime):
    res = await read_resource(uri, make_ctx())
    assert res["ttlMs"] == 3600000 and res["cacheScope"] == "public"
    [item] = res["contents"]
    assert item["uri"] == uri and item["mimeType"] == mime and item["text"].strip()
    if mime == "application/json":
        assert json.loads(item["text"])


@pytest.mark.anyio
async def test_recipe_and_template_indexes_match_their_sources():
    ctx = make_ctx()
    recipes = json.loads((await read_resource("openjev://recipes", ctx))["contents"][0]["text"])
    assert [r["id"] for r in recipes] == registry.load_all(ctx.config).ids()
    templates = json.loads((await read_resource("openjev://templates", ctx))["contents"][0]["text"])
    assert templates == library.templates_index()
    guide = (await read_resource("openjev://guide/authoring", ctx))["contents"][0]["text"]
    assert guide == library.guide()


@pytest.mark.anyio
async def test_read_recipe_and_template_by_id():
    ctx = make_ctx()
    for rid in registry.load_all(ctx.config).ids():
        res = await read_resource(f"openjev://recipes/{rid}", ctx)
        assert res["ttlMs"] == 3600000 and res["cacheScope"] == "public"
        assert json.loads(res["contents"][0]["text"])["id"] == rid
    for tid in library.template_ids():
        res = await read_resource(f"openjev://templates/{tid}", ctx)
        body = json.loads(res["contents"][0]["text"])
        assert body == library.template(tid) and res["contents"][0]["uri"] == f"openjev://templates/{tid}"


@pytest.mark.anyio
@pytest.mark.parametrize("uri", ["openjev://recipes/nope", "openjev://templates/nope", "openjev://recipes/",
                                 "openjev://templates/a/b", "openjev://audits/x"])
async def test_unknown_ids_are_not_found(uri):
    with pytest.raises(ResourceNotFound):
        await read_resource(uri, make_ctx())


def batch_file(tmp_path, name="out.jsonl", header=True, rows=2, tail=""):
    p = tmp_path / name
    lines = [json.dumps(store.make_header(run_id="r", question_hash="h"))] if header else ['{"id": "x"}']
    lines += [json.dumps({"id": str(i), "index": i, "status": "ok"}) for i in range(rows)]
    p.write_text("\n".join(lines) + "\n" + tail)
    return p


@pytest.mark.anyio
async def test_file_uri_serves_a_batch_output(tmp_path):
    ctx = make_ctx(roots=(str(tmp_path.resolve()),))
    p = batch_file(tmp_path, tail='{"id": "9", "zzz')   # interrupted last write is dropped, not an error
    res = await read_resource("file://" + str(p.resolve()), ctx)
    assert (res["ttlMs"], res["cacheScope"]) == (0, "private")
    [item] = res["contents"]
    assert item["mimeType"] == "application/x-ndjson" and len(item["text"].splitlines()) == 3
    assert "zzz" not in item["text"]


@pytest.mark.anyio
async def test_file_uri_refusals(tmp_path):
    ctx = make_ctx(roots=(str(tmp_path.resolve()),))
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    other = batch_file(outside, "o.jsonl")
    bad = {"no header": batch_file(tmp_path, "nh.jsonl", header=False), "not jsonl": tmp_path / "a.csv",
           "missing": tmp_path / "missing.jsonl", "outside roots": other}
    (tmp_path / "a.csv").write_text("a\n1\n")
    for label, path in bad.items():
        with pytest.raises(ResourceNotFound):
            await read_resource("file://" + str(path.resolve() if path.exists() else path), ctx)
    corrupt = tmp_path / "c.jsonl"
    corrupt.write_text(json.dumps(store.make_header(run_id="r", question_hash="h")) + "\nnot json\n{}\n")
    with pytest.raises(ResourceNotFound):
        await read_resource("file://" + str(corrupt.resolve()), ctx)


def test_file_uris_are_not_listed():
    assert not any(r["uri"].startswith("file://") for r in listing())


def test_completers_registered():
    ctx = make_ctx()
    ids = library.template_ids()
    assert completion.complete("ref/resource", "openjev://templates/{id}", "id", "", ctx)["values"] == ids[:100]
    assert completion.complete("ref/prompt", "start_batch", "template", ids[0][:2], ctx)["values"] == [
        i for i in ids if i.startswith(ids[0][:2])]
    got = completion.complete("ref/resource", "openjev://recipes/{id}", "id", "command", ctx)
    assert got == {"values": ["command_gate"], "total": 1, "hasMore": False}
    assert completion.complete("ref/resource", "openjev://recipes/{id}", "id", "zzz", ctx)["values"] == []


def test_no_sdk_imports():
    import openjev_mcp.resources as r
    import openjev_mcp.tools.status as s
    for mod in (r, s):
        src = open(mod.__file__).read()
        assert not re.search(r"^\s*(from|import) mcp\b", src, re.M)


@pytest.mark.anyio
async def test_audits_template_reads_a_stored_record_uncached_and_private(tmp_path):
    h = "ab" * 32
    rec = {"openjev_mcp": "calibrate", "question_hash": f"sha256:{h}", "n": 3}
    (tmp_path / f"{h}.json").write_text(json.dumps(rec))
    ctx = make_ctx(audit_dir=str(tmp_path))
    for uri in (f"openjev://audits/{h}", f"openjev://audits/sha256:{h}"):
        res = await read_resource(uri, ctx)
        assert json.loads(res["contents"][0]["text"]) == rec and (res["ttlMs"], res["cacheScope"]) == (0, "private")
    with pytest.raises(ResourceNotFound):
        await read_resource(f"openjev://audits/{'cd' * 32}", ctx)


@pytest.mark.anyio
async def test_file_uri_serves_ndjson_output(tmp_path):
    ctx = make_ctx(roots=(str(tmp_path.resolve()),))
    p = batch_file(tmp_path, name="out.ndjson")
    res = await read_resource("file://" + str(p.resolve()), ctx)
    assert len(res["contents"][0]["text"].splitlines()) == 3
