"""status tool (spec 2.17, 6.7): health, limits, auth, probe, warnings."""
from __future__ import annotations

import json

import httpx
import pytest
import stubs

from openjev_mcp import PROTOCOL_VERSIONS, __version__
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LIMITS_404_WARNING, LimitsCache
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools.status import LOGS_BODIES_WARNING, status
from openjev_mcp.validate import validate_output

BASE = "http://oj.test:8080"
KEY = "sk-test"
MODELS = {"models": [{"name": "openjev-latest"}, {"name": "openjev-0.1"}, {"name": "diffusiongemma-26b"}]}
LIMITS = {
    "backend": "mlx", "server_version": "0.6.0", "logs_bodies": False,
    "request": {"max_questions": 256, "max_images": 8, "max_image_bytes": 5242880, "max_body_bytes": 67108864,
                "steps": [1, 8], "samples": [1, 32], "think": [0, 4096]},
    "models": {"openjev-0.1": {"max_choices": 255, "max_score_levels": 10, "max_prompt_tokens": 32768,
                               "images": True, "think": True, "sequential": True, "routed": False}},
}
PROBE_ANSWER = {"model": "openjev-0.1", "answers": {"q": {"type": "noul", "noul": 0.9}},
                "usage": {"input_tokens": 95, "output_tokens": 0}}


def ctx_for(wire_transport, **cfg) -> ToolContext:
    config = Config(base_url=BASE, **cfg)
    client = OpenJevClient(config, transport=wire_transport)
    return ToolContext(config, client, LimitsCache(client), ProgressEmitter.noop(), None)


def routed(limits=None, *, health=200, probe=PROBE_ANSWER, models=MODELS):
    """MockTransport routing /health, /v1/models, /v1/limits and /v1/systemone to fixed JSON."""
    def answer(path):
        if path == "/health":
            return health, {"status": "ok"}
        if path == "/v1/models":
            return 200, models
        if path == "/v1/limits":
            return (200, limits) if limits is not None else (404, {"detail": "Not Found"})
        return 200, probe

    async def handler(request):
        t.requests.append(request)
        code, body = answer(request.url.path)
        return httpx.Response(code, json=body, request=request)

    t = stubs.RecordingTransport(handler)
    return t


def over_app(app):
    inner = stubs.asgi_transport(app)

    async def handler(request):
        t.requests.append(request)
        return await inner.handle_async_request(request)

    t = stubs.RecordingTransport(handler)
    return t


def all_keyed(t, key=KEY):
    assert t.requests
    assert all(r.headers.get("authorization") == f"Bearer {key}" for r in t.requests)


def valid(out):
    assert validate_output("status", out) == []


@pytest.mark.anyio
async def test_health_200_via_stub_app_is_healthy_and_validates():
    t = over_app(stubs.openjev_app())
    out = await status(ctx_for(t), {})
    assert out["healthy"] is True and out["base_url"] == BASE
    assert out["decide_models"] == ["openjev-latest", "openjev-0.1"]
    assert out["chat_models"] == ["diffusiongemma-26b"]
    assert out["aliases_accepted"] == ["jev-latest", "jev-preview"]
    assert [r.url.path for r in t.requests] == ["/health", "/v1/models", "/v1/limits"]
    valid(out)


@pytest.mark.anyio
async def test_limits_404_gives_defaults_warning_and_matrix_capabilities():
    out = await status(ctx_for(routed()), {})
    assert out["limit_source"] == "default" and out["backend"] == "unknown"
    assert out["limits"]["prompt_tokens"] is None and out["limits"]["questions"] == 256
    assert out["limits"]["batch"] == {"concurrency_max": 4, "max_items_per_call": 100, "max_items": 5000,
                                      "max_inflight_batch": 4}
    assert out["warnings"] == [LIMITS_404_WARNING]
    for name in ("openjev-latest", "openjev-0.1"):
        caps = out["capabilities"][name]
        assert caps == {"images": True, "steps": True, "samples": True, "think": True, "sequential": True,
                        "max_prompt_tokens": None, "max_choices": 255}
    valid(out)


@pytest.mark.anyio
async def test_limits_present_gives_server_limits_and_backend():
    out = await status(ctx_for(routed(LIMITS)), {})
    assert out["limit_source"] == "server" and out["backend"] == "mlx"
    assert out["limits"]["prompt_tokens"] == 32768 and out["limits"]["images"] == 8
    assert out["capabilities"]["openjev-0.1"]["max_prompt_tokens"] == 32768
    assert out["capabilities"]["openjev-latest"] == out["capabilities"]["openjev-0.1"]
    assert out["warnings"] == []
    valid(out)


@pytest.mark.anyio
async def test_logs_bodies_true_warns():
    out = await status(ctx_for(routed({**LIMITS, "logs_bodies": True})), {})
    assert out["warnings"] == [LOGS_BODIES_WARNING]
    out = await status(ctx_for(routed({**LIMITS, "logs_bodies": False})), {})
    assert out["warnings"] == []


@pytest.mark.anyio
async def test_connect_refused_is_oj_unreachable():
    t = stubs.fault_transport(exc=httpx.ConnectError("refused"))
    with pytest.raises(ToolError) as e:
        await status(ctx_for(t), {})
    assert e.value.code == "OJ_UNREACHABLE" and len(t.requests) == 1


@pytest.mark.anyio
async def test_404_on_health_is_oj_not_found():
    t = stubs.fault_transport(404, body=b'{"detail":"Not Found"}')
    with pytest.raises(ToolError) as e:
        await status(ctx_for(t), {})
    assert e.value.code == "OJ_NOT_FOUND"


@pytest.mark.anyio
async def test_wrong_key_is_oj_auth():
    app = stubs.openjev_app(api_key="right-key")
    t = over_app(app)
    out = await status(ctx_for(t, api_key="wrong-key"), {})
    assert out["healthy"] is True and out["auth"] == "unknown" and out["decide_models"] == []
    assert out["limit_source"] == "default"
    assert any("OJ_AUTH" in w and "OPENJEV_API_KEY" in w for w in out["warnings"])
    valid(out)
    all_keyed(t, "wrong-key")


@pytest.mark.anyio
async def test_auth_bearer_when_key_set_and_accepted_none_when_unset():
    app = stubs.openjev_app(api_key=KEY)
    t = over_app(app)
    out = await status(ctx_for(t, api_key=KEY), {"probe": False})
    assert out["auth"] == "bearer"
    all_keyed(t)
    valid(out)
    out = await status(ctx_for(routed()), {})
    assert out["auth"] == "none"


@pytest.mark.anyio
async def test_auth_unknown_when_limits_read_is_refused():
    async def handler(request):
        t.requests.append(request)
        if request.url.path == "/v1/limits":
            return httpx.Response(401, json={"error": {"message": "no"}}, request=request)
        return httpx.Response(200, json=MODELS if request.url.path == "/v1/models" else {"status": "ok"},
                              request=request)
    t = stubs.RecordingTransport(handler)
    out = await status(ctx_for(t, api_key=KEY), {})
    assert out["auth"] == "unknown" and "OJ_AUTH" in out["warnings"][0]
    valid(out)


@pytest.mark.anyio
async def test_every_request_carries_the_key_with_probe():
    t = routed(LIMITS)
    await status(ctx_for(t, api_key=KEY), {"probe": True})
    assert [r.url.path for r in t.requests] == ["/health", "/v1/models", "/v1/limits", "/v1/systemone"]
    all_keyed(t)


@pytest.mark.anyio
async def test_probe_off_sends_no_systemone_and_reports_null():
    t = routed()
    out = await status(ctx_for(t), {})
    assert "/v1/systemone" not in {r.url.path for r in t.requests}
    assert out["latency_probe_ms"] is None and "resolved" not in out


@pytest.mark.anyio
async def test_probe_on_measures_latency_and_resolves_alias():
    t = routed()
    out = await status(ctx_for(t), {"probe": True})
    assert isinstance(out["latency_probe_ms"], int) and out["latency_probe_ms"] >= 0
    assert out["resolved"] == {"openjev-latest": "openjev-0.1"}
    sent = json.loads(t.requests[-1].content)
    assert sent["model"] == "openjev-latest" and sent["samples"] == 1
    assert [q["type"] for q in sent["questions"].values()] == ["noul"]
    valid(out)


@pytest.mark.anyio
async def test_failed_probe_degrades_to_a_warning():
    async def handler(request):
        if request.url.path == "/v1/systemone":
            return httpx.Response(500, json={"detail": "boom"}, request=request)
        return await routed().handle_async_request(request)
    out = await status(ctx_for(httpx.MockTransport(handler)), {"probe": True})
    assert out["healthy"] is True and out["latency_probe_ms"] is None
    assert any(w.startswith("probe failed (OJ_SERVER)") for w in out["warnings"])
    valid(out)


@pytest.mark.anyio
async def test_mcp_block_values():
    out = await status(ctx_for(routed(), max_inflight_batch=2, transport="stdio"), {})
    assert out["mcp"] == {"server_version": __version__, "protocol_versions": list(PROTOCOL_VERSIONS),
                          "batch_max_inflight": 2, "transport": "stdio"}
    assert out["mcp"]["protocol_versions"][0] == "2026-07-28"
    assert out["limits"]["batch"]["max_inflight_batch"] == 2


@pytest.mark.anyio
async def test_registry_warnings_reach_status(tmp_path):
    from openjev_mcp.recipes import registry
    (tmp_path / "junk.json").write_text("{nope")
    (tmp_path / "gate.json").write_text(json.dumps(
        {**json.loads(registry.resources.files("openjev_mcp.recipes").joinpath("builtin", "command_gate.json")
                      .read_text(encoding="utf-8")), "title": "Shadow"}))
    registry.clear_cache()
    try:
        out = await status(ctx_for(routed(), recipes_dir=str(tmp_path)), {})
        mine = [w for w in out["warnings"] if w.startswith("recipe file")]
        assert len(mine) == 2 and any("junk.json" in w for w in mine) and any("gate.json" in w for w in mine)
        valid(out)
        registry.clear_cache()
        assert not [w for w in (await status(ctx_for(routed()), {}))["warnings"] if w.startswith("recipe file")]
    finally:
        registry.clear_cache()
