"""batch.images (spec 2.11 phase 3): loaded once per call, identical in every row body, think/sequential refused."""
from __future__ import annotations

import base64
import io
import json
import time

import httpx
import pytest
from PIL import Image

from openjev_mcp import wire
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache, default_limits
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools.batch_tool import register
from openjev_mcp.validate import _validator, validate_args, validate_output

pytestmark = pytest.mark.anyio

Q = {"q": {"type": "noul", "instructions": "Does the screenshot show an error?", "criteria": {"true": "an error", "false": "no error"}}}
SPEC = None


@pytest.fixture(scope="module", autouse=True)
def _registered():
    had = "batch" in INPUT_SCHEMAS
    global SPEC
    SPEC = register(Config())
    yield
    if not had:
        INPUT_SCHEMAS.pop("batch", None)
        OUTPUT_SCHEMAS.pop("batch", None)
        _validator.cache_clear()


class Srv:
    def __init__(self):
        self.bodies, self.raw = [], []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        n = len(self.raw)
        self.raw.append(request.content)
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, headers={"x-request-id": f"req_{n:04d}"}, json={
            "model": "openjev-0.1", "answers": {"q": {"type": "noul", "noul": 0.95}}, "usage": {"input_tokens": 10, "output_tokens": 1}})


def make_ctx(transport, tmp_path, **cfg):
    config = Config(base_url="http://oj.test", roots=(str(tmp_path.resolve()),), retries=0, transport="stdio", **cfg)
    client = OpenJevClient(config, transport=transport, jitter=lambda: 0.0)
    limits = LimitsCache(client, ttl_s=1e9)
    limits._limits, limits._fetched = default_limits(), time.monotonic()
    return ToolContext(config, client, limits, ProgressEmitter.noop(), None)


async def call(ctx, args):
    assert validate_args("batch", args) is None
    ctx.links = []
    out = await SPEC.handler(ctx, args)
    assert validate_output("batch", out) == [], validate_output("batch", out)
    return out


def shot(tmp_path, name="s.png", size=(60, 40), color=(9, 9, 9)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    (tmp_path / name).write_bytes(buf.getvalue())
    return buf.getvalue()


def rows(n):
    return [{"id": f"r{i}", "state": f"text {i}"} for i in range(1, n + 1)]


async def test_same_images_in_every_row_body(tmp_path):
    raw = shot(tmp_path)
    shot(tmp_path, "t.png", (3000, 1000))
    srv = Srv()
    imgs = [{"path": "s.png"}, {"path": "t.png"}, {"data_url": "data:image/png;base64," + base64.b64encode(raw).decode()}]
    out = await call(make_ctx(srv.transport, tmp_path), {"items": rows(4), "questions": Q, "images": imgs, "max_side_px": 500})
    assert len(srv.bodies) == 4 and out["status"]["ok"] == 4 and out["meta"]["requests"] == 4
    first = srv.bodies[0]["images"]
    assert len(first) == 3 and first[0] == "data:image/png;base64," + base64.b64encode(raw).decode()
    assert Image.open(io.BytesIO(base64.b64decode(first[1].split(",", 1)[1]))).size == (500, 167)
    assert all(b["images"] == first for b in srv.bodies)
    assert [b["state"] for b in srv.bodies] == [f"text {i}" for i in range(1, 5)]
    assert all(list(b) == ["model", "samples", "state", "questions", "images"] for b in srv.bodies)
    assert all(raw_body == wire.body_bytes(b) for raw_body, b in zip(srv.raw, srv.bodies))
    assert [i["reencoded"] for i in out["images"]] == [False, True, False] and out["images"][0]["source"] == "s.png"


async def test_images_loaded_once_per_call(tmp_path, monkeypatch):
    shot(tmp_path)
    import openjev_mcp.tools.batch_tool as bt
    loads = []
    real = bt.load_images

    async def counting(*a, **kw):
        loads.append(1)
        return await real(*a, **kw)
    monkeypatch.setattr(bt, "load_images", counting)
    srv = Srv()
    await call(make_ctx(srv.transport, tmp_path), {"items": rows(5), "questions": Q, "images": [{"path": "s.png"}]})
    assert len(loads) == 1 and len(srv.bodies) == 5


async def test_regrey_reread_carries_images(tmp_path):
    shot(tmp_path)
    srv = Srv()

    def grey(request):
        srv.handle(request)
        return httpx.Response(200, json={"model": "m", "answers": {"q": {"type": "noul", "noul": 0.5}},
                                         "usage": {"input_tokens": 1, "output_tokens": 0}})
    srv.transport = httpx.MockTransport(grey)
    await call(make_ctx(srv.transport, tmp_path), {"items": rows(1), "questions": Q, "images": [{"path": "s.png"}]})
    assert len(srv.bodies) == 2 and srv.bodies[0]["images"] == srv.bodies[1]["images"] and srv.bodies[1]["samples"] == 4


@pytest.mark.parametrize("opts", [{"think": 128}, {"sequential": True}])
async def test_think_and_sequential_refused_e022(tmp_path, opts):
    shot(tmp_path)
    srv = Srv()
    with pytest.raises(ToolError) as e:
        await call(make_ctx(srv.transport, tmp_path), {"items": rows(2), "questions": Q, "options": opts, "images": [{"path": "s.png"}]})
    assert e.value.code == "OJ_INVALID_INPUT" and "E022" in e.value.message and not srv.bodies
    assert not list(tmp_path.glob("*.jsonl"))


async def test_think_still_fine_without_images(tmp_path):
    srv = Srv()
    out = await call(make_ctx(srv.transport, tmp_path), {"items": rows(1), "questions": Q, "options": {"think": 64}})
    assert out["status"]["ok"] == 1 and "images" not in srv.bodies[0] and "images" not in out


async def test_bad_image_fails_before_any_request_or_output_file(tmp_path):
    (tmp_path / "bad.png").write_bytes(b"not a png")
    srv = Srv()
    with pytest.raises(ToolError) as e:
        await call(make_ctx(srv.transport, tmp_path), {"items": rows(2), "questions": Q, "images": [{"path": "bad.png"}],
                                                       "output_path": str(tmp_path / "o.jsonl")})
    assert "cannot be decoded" in e.value.message and e.value.path == "images.0" and not srv.bodies
    assert not (tmp_path / "o.jsonl").exists()


async def test_image_url_needs_fetch(tmp_path):
    srv = Srv()
    with pytest.raises(ToolError) as e:
        await call(make_ctx(srv.transport, tmp_path), {"items": rows(1), "questions": Q, "images": [{"url": "https://img.test/a.png"}]})
    assert "fetch is off" in e.value.message and not srv.bodies


async def test_dry_run_shows_elided_images_and_sends_nothing(tmp_path):
    shot(tmp_path)
    srv = Srv()
    out = await call(make_ctx(srv.transport, tmp_path), {"items": rows(2), "questions": Q, "images": [{"path": "s.png"}], "dry_run": True})
    assert not srv.bodies and out["status"]["stopped_reason"] == "dry_run"
    assert out["first_body"]["images"] == [f"image/png;base64,<{out['images'][0]['bytes']} bytes elided>"]
    assert out["images"][0]["sent_as"] == "image/png"


async def test_cursor_continues_with_same_images_and_rejects_a_changed_image(tmp_path):
    shot(tmp_path)
    srv = Srv()
    ctx = make_ctx(srv.transport, tmp_path)
    args = {"items": rows(4), "questions": Q, "images": [{"path": "s.png"}], "max_items_per_call": 2, "output_path": str(tmp_path / "o.jsonl")}
    a = await call(ctx, args)
    assert a["next_cursor"] and len(srv.bodies) == 2
    b = await call(ctx, {**args, "cursor": a["next_cursor"]})
    assert b["next_cursor"] is None and len(srv.bodies) == 4 and all(x["images"] == srv.bodies[0]["images"] for x in srv.bodies)
    shot(tmp_path, color=(200, 0, 0))
    with pytest.raises(ToolError) as e:
        await call(ctx, {**args, "cursor": a["next_cursor"]})
    assert e.value.code == "OJ_INVALID_INPUT" and "cursor" in (e.value.message + (e.value.hint or "")).lower() and len(srv.bodies) == 4


async def test_ojui_batch_with_images_warns_w604(tmp_path):
    shot(tmp_path)
    doc = {"format": "ojui-batch", "version": 1, "imageCount": 2, "questions": Q, "options": {"samples": 1},
           "rows": [{"index": 1, "state": "a", "status": "ok"}, {"index": 2, "state": "b", "status": "ok"}]}
    (tmp_path / "b.json").write_text(json.dumps(doc))
    srv = Srv()
    out = await call(make_ctx(srv.transport, tmp_path), {"items_file": {"path": "b.json"}, "dry_run": True})
    w = [x for x in out["import"]["warnings"] if x["code"] == "W604"]
    assert len(w) == 1 and "images" in w[0]["message"]
    out = await call(make_ctx(srv.transport, tmp_path), {"items_file": {"path": "b.json"}, "images": [{"path": "s.png"}]})
    assert out["status"]["ok"] == 2 and all(len(b["images"]) == 1 for b in srv.bodies)
    assert any(x["code"] == "W604" for x in out["import"]["warnings"])
    doc["imageCount"] = 0
    (tmp_path / "b.json").write_text(json.dumps(doc))
    out = await call(make_ctx(srv.transport, tmp_path), {"items_file": {"path": "b.json"}, "dry_run": True})
    assert not [x for x in out["import"]["warnings"] if x["code"] == "W604"]


def test_schema_accepts_ask_image_items_and_caps_at_eight():
    ok = {"items": rows(1), "questions": Q}
    assert validate_args("batch", {**ok, "images": [{"path": "a.png"}, {"url": "https://x.test/a.png"}, {"data_url": "data:image/png;base64,AA"},
                                                    {"base64": "AA", "content_type": "image/png"}]}) is None
    assert validate_args("batch", {**ok, "images": []}) is not None
    assert validate_args("batch", {**ok, "images": [{"path": "a.png"}] * 9}) is not None
    assert validate_args("batch", {**ok, "images": [{"path": "a.png"}], "max_side_px": 800}) is None
