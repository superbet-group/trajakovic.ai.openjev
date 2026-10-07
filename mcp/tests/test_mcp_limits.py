"""limits.py: defaults, capability matrix, /v1/limits parsing and the cache."""
from __future__ import annotations

import json
import re

import httpx
import pytest
import stubs

from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import (ALIASES_ACCEPTED, BATCH_CAPS, CAPABILITY_MATRIX, CHAT_MODELS, DEFAULT_LIMITS,
                                LIMITS_404_WARNING, MLX_PROMPT_TOKENS, VLLM_PROMPT_TOKENS, LimitsCache,
                                default_limits, parse_limits)

BASE = "http://oj.test:8080"
MODELS = {"models": [{"name": "openjev-latest"}, {"name": "openjev-0.1"}, {"name": "diffusiongemma-26b"}]}
PAYLOAD = {  # 00-api-surface 15.1
    "backend": "mlx", "server_version": "0.6.0", "logs_bodies": False,
    "request": {"max_questions": 256, "max_images": 8, "max_image_bytes": 5242880, "max_body_bytes": 67108864,
                "image_types": ["image/jpeg", "image/png", "image/webp", "image/gif"],
                "steps": [1, 8], "samples": [1, 32], "think": [0, 4096]},
    "models": {"openjev-0.1": {"max_choices": 255, "max_score_levels": 10, "max_prompt_tokens": 32768,
                               "images": True, "think": True, "sequential": True, "routed": False},
               "verdict-1.4": {"routed": True, "url_host": "verdict"}},
    "capacity": {"max_inflight": 64, "max_queue": 512},
}


def js(obj, status=200):
    return (status, {"content-type": "application/json"}, json.dumps(obj).encode())


def route(**routes):
    """A transport answering by path; a value is a (status, headers, body) tuple or an exception."""
    seen: list[str] = []

    async def handler(request):
        seen.append(request.url.path)
        item = routes[request.url.path]
        if isinstance(item, BaseException):
            raise item
        status, headers, body = item
        return httpx.Response(status, headers=headers, content=body, request=request)

    t = httpx.MockTransport(handler)
    t.seen = seen
    return t


class Wall:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def cache(transport, *, clock=None, ttl_s=60.0, **kw):
    clock = clock or Wall()
    return LimitsCache(OpenJevClient(Config(base_url=BASE, api_key="k", retries=0), transport=transport),
                       ttl_s=ttl_s, clock=clock, wall=lambda: 1_790_000_000.0, **kw), clock


def test_default_limits_values():
    lim = default_limits()
    assert DEFAULT_LIMITS == {"questions": 256, "choice_options": 255, "score_levels": 10, "images": 8,
                              "image_bytes": 5242880, "prompt_tokens": None, "body_bytes": 67108864}
    assert lim.source == "default" and lim.backend == "unknown" and lim.values == DEFAULT_LIMITS
    assert lim.values is not DEFAULT_LIMITS and lim.read_at == 0.0 and lim.warnings == ()
    assert lim.known_models is None and lim.logs_bodies is None
    assert (MLX_PROMPT_TOKENS, VLLM_PROMPT_TOKENS) == (32768, 65536)
    assert CHAT_MODELS == {"diffusiongemma-26b"} and ALIASES_ACCEPTED == ("jev-latest", "jev-preview")
    assert BATCH_CAPS == {"concurrency_max": 4, "max_items_per_call": 100, "max_items": 5000}


def test_default_limits_models_follow_the_listing():
    lim = default_limits(["openjev-latest", "openjev-0.1", "laya-1.0", "mystery-9", "diffusiongemma-26b"])
    assert lim.known_models == ("openjev-latest", "openjev-0.1", "laya-1.0", "mystery-9", "diffusiongemma-26b")
    assert list(lim.models) == ["openjev-0.1", "laya-1.0", "mystery-9"]
    assert lim.models["mystery-9"]["images"] is True and lim.models["laya-1.0"]["images"] is False


@pytest.mark.parametrize("alias", ["openjev-latest", "jev-latest", "jev-preview", "openjev-0.1"])
def test_capabilities_aliases_resolve_to_openjev_0_1(alias):
    caps = default_limits().capabilities(alias)
    assert caps == {"images": True, "steps": True, "samples": True, "think": True, "sequential": True,
                    "max_prompt_tokens": None, "max_choices": 255}


@pytest.mark.parametrize("model,prompt,choices", [("laya-1.0", 1024, None), ("verdict-1.4", 512, 24),
                                                  ("clm-v0.1", 2048, None), ("jevk5-0.2", 16384, None)])
def test_capabilities_encoder_rows(model, prompt, choices):
    caps = default_limits().capabilities(model)
    assert [caps[k] for k in ("images", "steps", "samples", "think", "sequential")] == [False] * 5
    assert caps["max_prompt_tokens"] == prompt and caps["max_choices"] == choices
    assert CAPABILITY_MATRIX[model] == caps


def test_capabilities_unknown_model_assumes_everything():
    caps = default_limits().capabilities("routed-x")
    assert all(caps[k] is True for k in ("images", "steps", "samples", "think", "sequential"))
    assert caps["max_prompt_tokens"] is None and caps["max_choices"] is None


def test_capabilities_returns_copies():
    lim = default_limits()
    lim.capabilities("openjev-latest")["images"] = False
    assert lim.capabilities("openjev-latest")["images"] is True and CAPABILITY_MATRIX["openjev-0.1"]["images"] is True


def test_prompt_tokens_follow_the_backend():
    assert default_limits().capabilities("openjev-latest")["max_prompt_tokens"] is None
    assert default_limits().values["prompt_tokens"] is None
    mlx = parse_limits({**PAYLOAD, "models": {"openjev-0.1": {}}}, now=1.0)
    vllm = parse_limits({**PAYLOAD, "backend": "vllm", "models": {"openjev-0.1": {}}}, now=1.0)
    assert mlx.capabilities("openjev-latest")["max_prompt_tokens"] == 32768 and mlx.values["prompt_tokens"] == 32768
    assert vllm.capabilities("openjev-latest")["max_prompt_tokens"] == 65536 and vllm.values["prompt_tokens"] == 65536


def test_parse_limits_on_the_15_1_payload():
    lim = parse_limits(PAYLOAD, known_models=["openjev-latest", "openjev-0.1"], now=1_790_000_000.0)
    assert lim.source == "server" and lim.backend == "mlx" and lim.logs_bodies is False
    assert lim.values == {"questions": 256, "choice_options": 255, "score_levels": 10, "images": 8,
                          "image_bytes": 5242880, "prompt_tokens": 32768, "body_bytes": 67108864}
    assert lim.read_at == 1_790_000_000.0 and lim.warnings == ()
    assert lim.known_models == ("openjev-latest", "openjev-0.1")
    assert lim.models["openjev-0.1"]["max_choices"] == 255 and lim.models["openjev-0.1"]["think"] is True
    assert lim.models["verdict-1.4"]["images"] is False and lim.models["verdict-1.4"]["max_choices"] == 24
    assert lim.capabilities("openjev-latest") == lim.models["openjev-0.1"]


def test_parse_limits_follows_the_server_values():
    payload = json.loads(json.dumps(PAYLOAD))
    payload["request"].update(max_questions=64, steps=[1, 1], samples=[1, 1])
    payload["models"]["openjev-0.1"].update(max_choices=24, max_prompt_tokens=None, images=False)
    lim = parse_limits(payload, now=5.0)
    assert lim.values["questions"] == 64 and lim.values["choice_options"] == 24
    caps = lim.capabilities("openjev-latest")
    assert caps["images"] is False and caps["steps"] is False and caps["samples"] is False
    assert caps["max_choices"] == 24 and lim.known_models is None


def test_parse_limits_skips_chat_models():
    payload = {**PAYLOAD, "models": {**PAYLOAD["models"], "diffusiongemma-26b": {"routed": False}}}
    assert "diffusiongemma-26b" not in parse_limits(payload, now=1.0).models


@pytest.mark.parametrize("bad", [
    None, [], "x", {}, {"backend": "mlx"}, {**PAYLOAD, "backend": ""}, {**PAYLOAD, "backend": 3},
    {**PAYLOAD, "request": []}, {**PAYLOAD, "models": []}, {**PAYLOAD, "logs_bodies": "no"},
    {**PAYLOAD, "request": {**PAYLOAD["request"], "max_questions": "256"}},
    {**PAYLOAD, "request": {**PAYLOAD["request"], "max_images": True}},
    {**PAYLOAD, "request": {**PAYLOAD["request"], "steps": [1]}},
    {**PAYLOAD, "models": {"openjev-0.1": []}},
    {**PAYLOAD, "models": {"openjev-0.1": {"max_choices": -1}}},
])
def test_parse_limits_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_limits(bad, now=1.0)


def test_resource_shape_with_batch_caps():
    cfg = Config(max_inflight_batch=2)
    res = parse_limits(PAYLOAD, known_models=["openjev-latest"], now=1_790_000_000.0).resource(cfg)
    assert set(res) == {"limit_source", "backend", "limits", "capabilities", "batch", "logs_bodies", "read_at", "warnings"}
    assert res["limit_source"] == "server" and res["backend"] == "mlx" and res["logs_bodies"] is False
    assert res["batch"] == {"concurrency_max": 4, "max_items_per_call": 100, "max_items": 5000, "max_inflight_batch": 2}
    assert set(res["capabilities"]) == {"openjev-0.1", "verdict-1.4"} and res["warnings"] == []
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", res["read_at"])
    json.dumps(res)


def test_resource_of_defaults():
    res = default_limits().resource(Config())
    assert res["limit_source"] == "default" and res["backend"] == "unknown" and res["read_at"] is None
    assert res["limits"] == DEFAULT_LIMITS and res["batch"]["max_inflight_batch"] == 4
    assert res["capabilities"]["openjev-0.1"]["max_prompt_tokens"] is None


@pytest.mark.anyio
async def test_refresh_404_gives_defaults_and_warning():
    t = route(**{"/v1/models": js(MODELS), "/v1/limits": js({"detail": "Not Found"}, 404)})
    c, clock = cache(t)
    lim = await c.refresh()
    assert lim.source == "default" and lim.warnings == (LIMITS_404_WARNING,) and lim.backend == "unknown"
    assert lim.known_models == ("openjev-latest", "openjev-0.1", "diffusiongemma-26b")
    assert "diffusiongemma-26b" not in lim.models and c.peek() is lim
    assert t.seen == ["/v1/models", "/v1/limits"]


@pytest.mark.anyio
async def test_refresh_200_gives_server_limits():
    c, _ = cache(route(**{"/v1/models": js(MODELS), "/v1/limits": js(PAYLOAD)}))
    lim = await c.refresh()
    assert lim.source == "server" and lim.backend == "mlx" and lim.read_at == 1_790_000_000.0
    assert lim.known_models == ("openjev-latest", "openjev-0.1", "diffusiongemma-26b") and lim.warnings == ()


@pytest.mark.anyio
@pytest.mark.parametrize("limits", [js({"detail": "boom"}, 500), js({"detail": "no"}, 401),
                                    httpx.ConnectError("gone"), js({"nope": 1}), js([1, 2])])
async def test_refresh_other_limits_failure_degrades_to_defaults(limits):
    c, _ = cache(route(**{"/v1/models": js(MODELS), "/v1/limits": limits}))
    lim = await c.refresh()
    assert lim.source == "default" and len(lim.warnings) == 1 and "/v1/limits" in lim.warnings[0]
    assert lim.warnings[0] != LIMITS_404_WARNING


@pytest.mark.anyio
@pytest.mark.parametrize("models,code", [
    (httpx.ConnectError("gone"), "OJ_UNREACHABLE"),
    (js({"detail": {"error_type": "authentication_error", "message": "x"}}, 401), "OJ_AUTH"),
    (js({"detail": "Not Found"}, 404), "OJ_NOT_FOUND"),
    (js({"models": "nope"}), "OJ_PROTOCOL"),
])
async def test_refresh_models_failure_raises_tool_error(models, code):
    t = route(**{"/v1/models": models, "/v1/limits": js(PAYLOAD)})
    c, _ = cache(t)
    with pytest.raises(ToolError) as ei:
        await c.refresh()
    assert ei.value.code == code and t.seen == ["/v1/models"]
    assert c.peek().source == "default"


@pytest.mark.anyio
async def test_get_respects_ttl_with_injected_clock():
    t = route(**{"/v1/models": js(MODELS), "/v1/limits": js(PAYLOAD)})
    c, clock = cache(t, ttl_s=60.0)
    first = await c.get()
    clock.t = 59.0
    assert await c.get() is first and t.seen == ["/v1/models", "/v1/limits"]
    clock.t = 61.0
    second = await c.get()
    assert second is not first and second == first and len(t.seen) == 4


@pytest.mark.anyio
async def test_get_never_raises_and_falls_back_to_cache_or_defaults():
    state = {"down": False}

    async def handler(request):
        if state["down"]:
            raise httpx.ConnectError("gone")
        item = {"/v1/models": js(MODELS), "/v1/limits": js(PAYLOAD)}[request.url.path]
        return httpx.Response(item[0], headers=item[1], content=item[2], request=request)

    c, clock = cache(httpx.MockTransport(handler), ttl_s=10.0)
    state["down"] = True
    cold = await c.get()
    assert cold.source == "default"
    state["down"] = False
    warm = await c.get()
    assert warm.source == "server"
    state["down"] = True
    clock.t = 100.0
    stale = await c.get()
    assert stale is warm and c.peek() is warm


def test_peek_before_any_read_is_default_and_does_no_io():
    c = LimitsCache(OpenJevClient(Config(base_url=BASE), transport=stubs.fail_on_request_transport()))
    assert c.peek().source == "default"


@pytest.mark.anyio
async def test_cache_against_the_real_app_404_for_limits():
    app = stubs.openjev_app()
    c = LimitsCache(OpenJevClient(Config(base_url=BASE), transport=stubs.asgi_transport(app)))
    lim = await c.refresh()
    assert lim.source == "default" and lim.warnings == (LIMITS_404_WARNING,)
    assert {"openjev-latest", "openjev-0.1", "diffusiongemma-26b"} <= set(lim.known_models)
    assert list(lim.models) == ["openjev-0.1"]
