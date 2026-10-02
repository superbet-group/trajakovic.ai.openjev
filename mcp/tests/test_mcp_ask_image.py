"""ask_image (spec 2.12): ex-image replay, local refusals before any request, schema, annotations."""
from __future__ import annotations

import base64
import io
import json
import os

import httpx
import pytest
import stubs
from PIL import Image

from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools.ask_image import register
from openjev_mcp.validate import _validator, validate_args, validate_output

pytestmark = pytest.mark.anyio

HOTDOG = "tests/data/hotdog.jpg"
Q = {"hotdog": {"type": "noul", "instructions": "The photo shows a hot dog"},
     "cat": {"type": "noul", "instructions": "The photo shows a cat"}}
CASES, CAPTURED = stubs._load_cases()


@pytest.fixture(scope="module", autouse=True)
def _registered():
    """register() adds the schemas to the global registries; remove them again unless somebody wired them already."""
    had = "ask_image" in INPUT_SCHEMAS
    global SPEC
    SPEC = register(Config())
    yield
    if not had:
        INPUT_SCHEMAS.pop("ask_image", None)
        OUTPUT_SCHEMAS.pop("ask_image", None)
        _validator.cache_clear()


def make_ctx(transport, **cfg) -> ToolContext:
    config = Config(base_url="http://oj.test:8080", roots=(stubs.REPO,), transport="stdio", **cfg)
    client = OpenJevClient(config, transport=transport)
    return ToolContext(config, client, LimitsCache(client), ProgressEmitter.noop(), None)


async def call(ctx, args):
    assert validate_args("ask_image", args) is None
    out = await SPEC.handler(ctx, args)
    assert validate_output("ask_image", out) == []
    return out


def png(size=(40, 30)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, (1, 2, 3)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


async def test_ex_image_replay():
    transport = stubs.replay_transport(["ex-image"])
    out = await call(make_ctx(transport), {"images": [{"path": HOTDOG}], "state": "Look at the photo.", "questions": Q})
    cap = CAPTURED["ex-image"]
    assert out["images"] == [{"source": HOTDOG, "sent_as": "image/jpeg", "bytes": 12860, "reencoded": False}]
    assert out["answers"]["hotdog"]["band"] == "yes" and round(out["answers"]["hotdog"]["p"], 4) == 0.9982
    assert out["answers"]["cat"]["band"] == "no" and round(out["answers"]["cat"]["p"], 4) == 0.0003
    assert out["meta"]["request_ids"] == [cap["request_id"]] and out["meta"]["requests"] == 1
    assert out["meta"]["input_tokens"] == 355 and out["meta"]["output_tokens"] == 0
    [post] = [r for r in transport.requests if r.method == "POST"]
    body = json.loads(post.content)
    assert list(body) == ["model", "state", "questions", "images"] and body["model"] == "openjev-latest"
    raw = open(os.path.join(stubs.REPO, HOTDOG), "rb").read()
    assert body["images"] == ["data:image/jpeg;base64," + base64.b64encode(raw).decode()]


async def test_default_state_and_max_side():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "openjev-0.1", "answers": {"q": {"type": "noul", "noul": 0.5}},
                                         "usage": {"input_tokens": 1, "output_tokens": 0}})
    big = Image.new("RGB", (900, 300), (5, 5, 5))
    buf = io.BytesIO()
    big.save(buf, "PNG")
    out = await call(make_ctx(httpx.MockTransport(handler)), {
        "images": [{"base64": base64.b64encode(buf.getvalue()).decode(), "content_type": "image/png"}, {"data_url": "data:image/png;base64," + png()}],
        "questions": {"q": {"type": "noul", "instructions": "Is it dark?"}}, "max_side_px": 300, "options": {"samples": 1}})
    [body] = seen
    assert body["state"] == "Screenshot." and body["samples"] == 1 and len(body["images"]) == 2
    first = Image.open(io.BytesIO(base64.b64decode(body["images"][0].split(",", 1)[1])))
    assert first.size == (300, 100)
    assert [i["reencoded"] for i in out["images"]] == [True, False] and [i["source"] for i in out["images"]] == ["base64", "data_url"]


@pytest.mark.parametrize("images,text", [([{"base64": "bm90IGFuIGltYWdl", "content_type": "image/png"}], "cannot be decoded"),
                                         ([{"path": "tests/data/missing.png"}], "not found"),
                                         ([{"path": "/etc/passwd"}], "outside the allowed roots"),
                                         ([{"url": "https://img.test/a.png"}], "fetch is off")])
async def test_local_refusals_make_no_request(images, text):
    transport = stubs.fail_on_request_transport()
    with pytest.raises(ToolError) as e:
        await call(make_ctx(transport), {"images": images, "questions": Q})
    assert text in e.value.message and e.value.path.startswith("images.0")
    assert not transport.requests


async def test_url_fetched_when_on(monkeypatch):
    async def fake(url, config):
        return base64.b64decode(png())
    monkeypatch.setattr("openjev_mcp.fetch.fetch_image", fake)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"model": "m", "answers": {"q": {"type": "noul", "noul": 0.1}}, "usage": {}}))
    out = await call(make_ctx(transport, fetch=True), {"images": [{"url": "https://img.test/a.png"}], "questions": {"q": {"type": "noul", "instructions": "ok?"}}})
    assert out["images"][0]["source"] == "https://img.test/a.png"


@pytest.mark.parametrize("opt", [{"think": 128}, {"sequential": True}])
def test_think_and_sequential_not_in_schema(opt):
    err = validate_args("ask_image", {"images": [{"path": HOTDOG}], "questions": Q, "options": opt})
    assert err is not None and err.code == "OJ_INVALID_INPUT"


def test_schema_shape():
    s = INPUT_SCHEMAS["ask_image"]
    assert s["required"] == ["images", "questions"] and s["additionalProperties"] is False
    assert s["properties"]["images"]["minItems"] == 1 and s["properties"]["images"]["maxItems"] == 8
    assert s["properties"]["state"]["default"] == "Screenshot." and s["properties"]["max_side_px"]["default"] == 1568
    assert set(s["properties"]["options"]["properties"]) == {"model", "samples", "steps", "timeout_ms"}
    assert validate_args("ask_image", {"images": [], "questions": Q}) is not None
    assert validate_args("ask_image", {"images": [{"path": "a.png", "url": "https://x.test/a.png"}], "questions": Q}) is not None
    assert validate_args("ask_image", {"images": [{"base64": "AA"}], "questions": Q}) is not None


def test_annotations_follow_fetch_flag():
    assert SPEC.name == "ask_image" and SPEC.annotations["openWorldHint"] is False and SPEC.annotations["readOnlyHint"] is True
    assert register(Config(fetch=True)).annotations["openWorldHint"] is True
