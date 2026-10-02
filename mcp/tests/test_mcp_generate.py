"""generate (spec 2.17): the ex-generate replay and the MLX guards."""
from __future__ import annotations

import json

import httpx
import pytest
import stubs

from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.tools import ToolContext, generate as gen
from openjev_mcp.tools.dispatch import call_tool
from openjev_mcp.validate import validate_args, validate_output

pytestmark = pytest.mark.anyio

CASES, CAPTURED = stubs._load_cases()
EX = next(c for c in CASES if c["id"] == "ex-generate")["request"]
MSG = [{"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def registered():
    from openjev_mcp import schemas
    gen.register(Config())
    yield
    schemas.INPUT_SCHEMAS.pop("generate", None)
    schemas.OUTPUT_SCHEMAS.pop("generate", None)


def ctx_for(transport, **kw) -> ToolContext:
    config = Config(base_url="http://oj.test:8080", **kw)
    client = OpenJevClient(config, transport=transport)
    return ToolContext(config, client, LimitsCache(client), ProgressEmitter.noop(), None)


def completion(content: str, tokens: int, finish: str = "stop") -> tuple:
    body = {"id": "c", "object": "chat.completion", "model": "diffusiongemma-26b",
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": tokens, "total_tokens": 5 + tokens}}
    return 200, {"content-type": "application/json", "x-request-id": "req_g"}, json.dumps(body).encode()


def chat_transport(items, backend: str | None = None):
    """Serves /v1/models and /v1/limits (404 when backend is None -> unknown) and `items` for each chat POST."""
    n = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal n
        t.requests.append(request)
        path = request.url.path
        if path == "/v1/models":
            return httpx.Response(200, json={"models": [{"name": "openjev-latest"}]})
        if path == "/v1/limits":
            if backend is None:
                return httpx.Response(404, json={"detail": "nope"})
            return httpx.Response(200, json={"backend": backend, "request": {}, "models": {"openjev-0.1": {}}})
        status, headers, body = items[min(n, len(items) - 1)]
        n += 1
        return httpx.Response(status, headers=headers, content=body)
    t = stubs.RecordingTransport(handler)
    return t


@pytest.fixture(autouse=True)
def pipeline(monkeypatch):
    from openjev_mcp.tools import dispatch
    spec = gen.register(Config())
    monkeypatch.setattr(dispatch, "specs", lambda config: (spec,))


def chat_posts(t):
    return [r for r in t.requests if r.url.path == "/v1/chat/completions"]


async def test_ex_generate_replay():
    t = stubs.replay_transport(["ex-generate"])
    res = await call_tool(ctx_for(t), "generate", EX | {})
    assert res["isError"] is False, res
    out = res["structuredContent"]
    assert out == {"content": "4", "finish_reason": "stop",
                   "usage": {"prompt_tokens": 25, "completion_tokens": 1, "total_tokens": 26}, "retried": False,
                   "warnings": [gen.MLX_WARNING]}
    assert validate_output("generate", out) == []
    assert len(chat_posts(t)) == 1


async def test_default_model_and_max_tokens_and_omitted_keys():
    t = chat_transport([completion("ok", 1)])
    await gen.generate(ctx_for(t, chat_model="my-chat"), {"messages": MSG})
    assert json.loads(chat_posts(t)[0].content) == {"model": "my-chat", "max_tokens": 512, "messages": MSG}


async def test_empty_reply_is_retried_exactly_once():
    t = chat_transport([completion("", 0), completion("hello", 2)])
    out = await gen.generate(ctx_for(t), {"messages": MSG})
    assert out["content"] == "hello" and out["retried"] is True and len(chat_posts(t)) == 2


async def test_still_empty_after_the_retry_is_returned_with_a_warning():
    t = chat_transport([completion("", 0)])
    out = await gen.generate(ctx_for(t), {"messages": MSG})
    assert out["content"] == "" and out["retried"] is True and len(chat_posts(t)) == 2
    assert "the reply was empty after one retry" in out["warnings"]


async def test_empty_with_tokens_is_not_retried():
    t = chat_transport([completion("", 3)])
    out = await gen.generate(ctx_for(t), {"messages": MSG})
    assert out["retried"] is False and len(chat_posts(t)) == 1


async def test_length_finish_reason():
    t = chat_transport([completion("abc", 3, "length")])
    assert (await gen.generate(ctx_for(t), {"messages": MSG}))["finish_reason"] == "length"


@pytest.mark.parametrize("messages", [[{"content": "no role"}], [{"role": "user"}], [{"role": "bot", "content": "x"}]])
async def test_bad_message_is_refused_before_any_request(messages):
    t = stubs.fail_on_request_transport()
    res = await call_tool(ctx_for(t), "generate", {"messages": messages})
    assert res["isError"] is True and json.loads(res["content"][0]["text"])["error"]["code"] == "OJ_INVALID_INPUT"
    assert not t.requests
    with pytest.raises(ToolError):   # and the handler guards it on its own too
        await gen.generate(ctx_for(t), {"messages": [{"content": "x"}]})
    assert not t.requests


async def test_max_tokens_above_8192_is_clamped_with_a_warning():
    t = chat_transport([completion("ok", 1)])
    res = await call_tool(ctx_for(t), "generate", {"messages": MSG, "max_tokens": 100000})
    assert res["isError"] is False, res
    assert json.loads(chat_posts(t)[0].content)["max_tokens"] == 8192
    assert any(w.startswith("W701 max_tokens") for w in res["structuredContent"]["warnings"])
    assert validate_args("generate", {"messages": MSG, "max_tokens": 8192}) is None


async def test_multi_token_stop_is_passed_and_flagged():
    t = chat_transport([completion("ok", 1)])
    out = await gen.generate(ctx_for(t), {"messages": MSG, "stop": ["\n", "###", "END OF TEXT"]})
    assert json.loads(chat_posts(t)[0].content)["stop"] == ["\n", "###", "END OF TEXT"]
    flagged = [w for w in out["warnings"] if w.startswith("stop strings")]
    assert len(flagged) == 1 and "END OF TEXT" in flagged[0] and "'###'" not in flagged[0] and "'\\n'" not in flagged[0]


async def test_response_format_and_model_override_pass_through():
    t = chat_transport([completion("{}", 1)])
    await gen.generate(ctx_for(t), {"messages": MSG, "model": "other", "response_format": {"type": "json_object"}})
    body = json.loads(chat_posts(t)[0].content)
    assert body["model"] == "other" and body["response_format"] == {"type": "json_object"}


async def test_mlx_warning_only_for_mlx_or_unknown_backends():
    for backend, warned in (("vllm", False), ("mlx", True), (None, True)):
        out = await gen.generate(ctx_for(chat_transport([completion("ok", 1)], backend)), {"messages": MSG})
        assert (gen.MLX_WARNING in out["warnings"]) is warned, backend


async def test_chat_errors_map_through_the_openai_shape():
    err = (404, {"content-type": "application/json", "x-request-id": "req_e"},
           b'{"error":{"message":"model not found","type":"invalid_request_error","code":"model_not_found"}}')
    res = await call_tool(ctx_for(chat_transport([err])), "generate",
                          {"messages": MSG, "model": "nope"})
    e = json.loads(res["content"][0]["text"])["error"]
    assert res["isError"] is True and e["code"] == "OJ_UNKNOWN_MODEL" and e["request_id"] == "req_e"


async def test_overloaded_chat_is_retried_by_the_client():
    over = (529, {"retry-after": "0", "content-type": "application/json"},
            b'{"error":{"message":"busy","type":"overloaded_error"}}')
    t = chat_transport([over, completion("ok", 1)])
    out = await gen.generate(ctx_for(t), {"messages": MSG})
    assert out["content"] == "ok" and len(chat_posts(t)) == 2 and out["retried"] is False


def test_register_declares_a_non_decision_tool():
    spec = gen.register(Config())
    assert spec.name == "generate" and "NOT for decisions" in spec.description and spec.prepare is gen.prepare
