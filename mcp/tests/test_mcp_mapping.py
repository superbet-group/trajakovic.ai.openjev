"""Spec 2.4 error matrix at the mapping level: real bodies from the OpenJev app over a stub engine,
MockTransport only for faults the app cannot produce. Each row asserts code, retryable, status,
request id and retry_after_s."""
from __future__ import annotations

import json

import httpx
import pytest
import stubs

from openjev.engine import Overloaded, SchemaError, Upstream
from openjev_mcp.errors import CODES
from openjev_mcp.mapping import (map_http_error, map_transport_error, parse_retry_after,
                                 parse_server_timing)

BASE = "http://oj.test:8080"
GOOD = {"model": "openjev-latest", "state": "s", "questions": {"q": {"type": "noul", "instructions": "i"}}}


async def real(app, body=GOOD, *, headers=None, method="POST", path="/v1/systemone"):
    async with httpx.AsyncClient(transport=stubs.asgi_transport(app), base_url="http://t") as c:
        if method == "GET":
            r = await c.get(path, headers=headers)
        else:
            r = await c.post(path, content=json.dumps(body).encode(), headers={"content-type": "application/json", **(headers or {})})
    return r.status_code, dict(r.headers), r.content


def mapped(res, *, model="openjev-latest", has_images=False, known=None, base=BASE):
    status, headers, content = res
    return map_http_error(status, headers, content, base_url=base, model=model, has_images=has_images,
                          timeout_ms=30000, known_models=known)


def fault_app(exc):
    return stubs.openjev_app(engine=stubs.StubEngine(fault=lambda q, s, o: exc))


async def rejected(msg, loc=("body",)):
    err = mapped(await real(fault_app(SchemaError(msg, loc))))
    assert err.http_status == 400 and err.request_id and err.request_id.startswith("req_")
    assert err.retryable is False and err.retry_after_s is None
    assert err.server_detail == msg
    return err


def check(err, code, status, *, retryable=False, retry_after=None):
    assert err.code == code and err.code in CODES
    assert err.http_status == status
    assert err.retryable is retryable
    assert err.retry_after_s == retry_after
    return err


@pytest.mark.anyio
async def test_401_wrong_key():
    app = stubs.openjev_app(api_key="right")
    err = mapped(await real(app, headers={"authorization": "Bearer wrong"}))
    check(err, "OJ_AUTH", 401)
    assert err.request_id and "OPENJEV_API_KEY" in err.hint and BASE in err.hint


@pytest.mark.anyio
async def test_403_authentication_error_no_key():
    err = mapped(await real(stubs.openjev_app(api_key="right")))
    check(err, "OJ_AUTH", 403)
    assert err.request_id and BASE in err.message and "OPENJEV_API_KEY" in err.hint


@pytest.mark.anyio
async def test_403_permission_error_origin_secret():
    err = mapped(await real(stubs.openjev_app(origin_secret="front")))
    check(err, "OJ_FORBIDDEN", 403)
    assert err.request_id and "front proxy" in err.message and "OPENJEV_BASE_URL" in err.hint


@pytest.mark.anyio
async def test_unknown_model_lists_known_models():
    err = mapped(await real(stubs.openjev_app(), {**GOOD, "model": "jev-1.13.0"}), model="jev-1.13.0",
                 known=["openjev-latest", "openjev-0.1"])
    check(err, "OJ_UNKNOWN_MODEL", 400)
    assert err.request_id and "jev-1.13.0" in err.message
    assert "openjev-latest, openjev-0.1" in err.hint and "Pinned Jev versions" in err.hint


@pytest.mark.anyio
async def test_unknown_model_without_list():
    err = mapped(await real(stubs.openjev_app(), {**GOOD, "model": "nope"}))
    check(err, "OJ_UNKNOWN_MODEL", 400)
    assert "Available" not in err.hint and "nope" in err.message


@pytest.mark.anyio
async def test_invalid_request_bad_type():
    err = mapped(await real(stubs.openjev_app(), {**GOOD, "questions": {"q": {"type": "foo"}}}))
    check(err, "OJ_BAD_TYPE", 400)
    assert err.request_id and "noul, choice or score" in err.hint


@pytest.mark.anyio
async def test_model_rejected_upstream():
    err = mapped(await real(fault_app(Upstream("context too long"))))
    check(err, "OJ_REJECTED", 400)
    assert err.request_id and "rejected upstream: context too long" in err.hint
    assert err.server_detail == "the model rejected this request: context too long"


@pytest.mark.anyio
async def test_model_rejected_api_usage_error_dict_shape():
    body = json.dumps({"detail": {"error_type": "api_usage_error", "message": "the model rejected this request: boom"}}).encode()
    err = mapped((400, {"x-request-id": "req_1"}, body))
    check(err, "OJ_REJECTED", 400)
    assert err.request_id == "req_1" and "rejected upstream: boom" in err.hint


@pytest.mark.anyio
async def test_413_parses_limit():
    err = mapped(await real(stubs.openjev_app(max_body_bytes=100), {**GOOD, "state": "x" * 500}))
    check(err, "OJ_TOO_LARGE", 413)
    assert err.request_id and "100-byte limit" in err.hint and "100 bytes" in err.message


@pytest.mark.anyio
async def test_422_score_criteria_object_path():
    body = {**GOOD, "questions": {"sev": {"type": "score", "criteria": {"a": "b"}}}}
    err = mapped(await real(stubs.openjev_app(), body))
    check(err, "OJ_VALIDATION", 422)
    assert err.path == "questions.sev.criteria" and err.request_id
    assert "score criteria must be a list of level strings" in err.hint
    assert isinstance(err.server_detail, list)


@pytest.mark.anyio
async def test_422_choice_criteria_list_path():
    body = {**GOOD, "questions": {"c": {"type": "choice", "criteria": ["a", "b"]}}}
    err = mapped(await real(stubs.openjev_app(), body))
    check(err, "OJ_VALIDATION", 422)
    assert err.path == "questions.c.criteria" and "choice needs criteria" in err.hint


@pytest.mark.anyio
async def test_422_empty_questions_and_samples():
    err = mapped(await real(stubs.openjev_app(), {**GOOD, "questions": {}}))
    check(err, "OJ_VALIDATION", 422)
    assert err.path == "questions" and err.hint == "questions must be a non-empty object"
    err = mapped(await real(stubs.openjev_app(), {**GOOD, "samples": 99}))
    check(err, "OJ_VALIDATION", 422)
    assert err.path == "samples" and err.hint == "samples must be 1-32"


@pytest.mark.anyio
async def test_400_no_choices():
    err = await rejected("Choice question must have at least one choice: pick", ("body", "questions", "pick", "criteria"))
    assert err.code == "OJ_REJECTED" and "question pick has no options" in err.message
    assert "treat the answer as 'none'" in err.hint


@pytest.mark.anyio
async def test_400_too_many_choices():
    err = await rejected("Too many choices. Must have at most 255 choices.")
    assert err.code == "OJ_REJECTED" and "<= 100 candidates" in err.hint


@pytest.mark.anyio
async def test_400_too_many_score_levels():
    err = await rejected("Too many score levels. Must have at most 10 levels.")
    assert err.code == "OJ_REJECTED" and "<= 10 levels" in err.hint


@pytest.mark.anyio
async def test_400_too_many_questions():
    err = await rejected("at most 256 questions per request")
    assert err.code == "OJ_REJECTED" and "split across requests" in err.hint


@pytest.mark.anyio
@pytest.mark.parametrize("msg", [
    "at most 8 images per request",
    "image type 'image/bmp' is not supported; use JPEG, PNG, WebP or GIF",
    "image data is not valid base64",
    "image data is larger than the 5242880 byte limit",
    "image is 6000000 bytes; the limit is 5242880",
])
async def test_400_image_rows(msg):
    err = await rejected(msg)
    assert err.code == "OJ_REJECTED" and "<= 8 JPEG/PNG/WebP/GIF images" in err.hint


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["think", "sequential"])
async def test_400_text_state_only(field):
    err = await rejected(f"{field} needs a text state; send images without it")
    assert err.code == "OJ_REJECTED" and "drop think/sequential" in err.hint


@pytest.mark.anyio
async def test_400_request_too_long_parses_n_and_l():
    err = await rejected("the request is 41234 tokens; the limit is 32768")
    assert err.code == "OJ_TOO_LONG" and "41234" in err.message and "32768" in err.message
    assert "one question per chunk" in err.hint


@pytest.mark.anyio
async def test_400_too_long_limit_is_parsed_not_assumed():
    err = await rejected("the request is 700 tokens; the limit is 512")
    assert err.code == "OJ_TOO_LONG" and "limit is 512" in err.message and "32768" not in err.message


@pytest.mark.anyio
async def test_400_does_not_support():
    err = await rejected("laya-1.0 does not support images", ("body", "images"))
    assert err.code == "OJ_REJECTED" and err.hint == "laya-1.0 does not support images; drop it or use openjev-latest"


@pytest.mark.anyio
async def test_400_too_many_choices_for_model():
    err = await rejected("Too many choices for verdict-1.4: a question's options must fit in 512 tokens.")
    assert err.code == "OJ_REJECTED" and "512 label tokens on verdict-1.4" in err.hint


@pytest.mark.anyio
async def test_400_label_tokens():
    err = await rejected("the questions of one read need 700 label tokens; a read allows 512")
    assert err.code == "OJ_REJECTED" and "shorten option keys" in err.hint


@pytest.mark.anyio
async def test_400_answer_template():
    err = await rejected("answer template is 80 tokens; the canvas holds 63")
    assert err.code == "OJ_REJECTED" and "shorten question ids and option keys" in err.hint


@pytest.mark.anyio
async def test_400_other_plain_string_is_verbatim():
    err = await rejected("something nobody planned for")
    assert err.code == "OJ_REJECTED" and err.message == "something nobody planned for" and err.hint is None


@pytest.mark.anyio
async def test_404_not_openjev():
    err = mapped(await real(stubs.openjev_app(), method="GET", path="/v1/limits"))
    check(err, "OJ_NOT_FOUND", 404)
    assert err.request_id is None or err.request_id.startswith("req_")
    assert f"not OpenJev ({BASE})" in err.hint


@pytest.mark.anyio
async def test_405_wrong_verb():
    err = mapped(await real(stubs.openjev_app(), method="GET", path="/v1/systemone"))
    check(err, "OJ_NOT_FOUND", 405)
    assert "wrong verb or URL" in err.hint


@pytest.mark.anyio
async def test_529_overloaded_keeps_retry_after():
    err = mapped(await real(fault_app(Overloaded("OpenJev is at capacity. Retry shortly."))))
    check(err, "OJ_OVERLOADED", 529, retryable=True, retry_after=1.0)
    assert err.request_id and err.server_detail["error_type"] == "overloaded_error"


@pytest.mark.anyio
async def test_503_backend_unavailable():
    err = mapped(await real(fault_app(httpx.ConnectError("refused"))))
    check(err, "OJ_UNAVAILABLE", 503, retryable=True, retry_after=2.0)
    assert err.request_id and "ConnectError" in err.message


def test_503_without_header_defaults_to_2s():
    err = mapped((503, {}, b'{"detail":{"error_type":"api_error","message":"inference backend unavailable"}}'))
    check(err, "OJ_UNAVAILABLE", 503, retryable=True, retry_after=2.0)


def test_529_chat_default_is_2s():
    body = json.dumps({"error": {"message": "busy", "type": "overloaded_error", "code": None}}).encode()
    check(mapped((529, {}, body)), "OJ_OVERLOADED", 529, retryable=True, retry_after=2.0)


def test_429_gateway():
    err = mapped((429, {"Retry-After": "7", "X-Request-Id": "req_x"}, b"slow down"))
    check(err, "OJ_RATE_LIMITED", 429, retryable=True, retry_after=7.0)
    assert err.request_id == "req_x" and "retry after 7.0 s" in err.hint


def test_429_without_retry_after():
    check(mapped((429, {}, b"")), "OJ_RATE_LIMITED", 429, retryable=True, retry_after=None)


def test_500_text_plain_without_images_is_server_error():
    err = mapped((500, {"content-type": "text/plain; charset=utf-8"}, b"Internal Server Error"))
    check(err, "OJ_SERVER", 500, retryable=True)
    assert err.request_id is None and "retry once" in err.hint and err.server_detail == "Internal Server Error"


def test_500_text_plain_with_images_is_bad_image():
    err = mapped((500, {"content-type": "text/plain"}, b"Internal Server Error"), has_images=True)
    check(err, "OJ_BAD_IMAGE", 500)
    assert "Re-encode" in err.hint


def test_chat_model_not_found():
    body = json.dumps({"error": {"message": "Model 'x' not found. Available: diffusiongemma-26b.",
                                 "type": "invalid_request_error", "code": "model_not_found"}}).encode()
    err = mapped((404, {"x-request-id": "req_c"}, body))
    check(err, "OJ_UNKNOWN_MODEL", 404)
    assert err.request_id == "req_c" and err.hint == "chat model must be diffusiongemma-26b"


def test_gateway_502_is_unavailable():
    check(mapped((502, {}, b"bad gateway")), "OJ_UNAVAILABLE", 502, retryable=True, retry_after=2.0)


def test_unexpected_success_status_is_protocol():
    assert mapped((204, {}, b"")).code == "OJ_PROTOCOL"


def test_server_detail_trimmed_to_2kib():
    body = json.dumps({"detail": "x " * 5000}).encode()
    err = mapped((400, {}, body))
    assert err.code == "OJ_REJECTED" and len(json.dumps(err.server_detail).encode()) <= 2100
    assert isinstance(err.server_detail, str) and err.server_detail.endswith("...")


def test_never_raises_on_garbage():
    for status in (0, 200, 400, 422, 500, 999):
        for content in (b"", b"\xff\xfe", b"[]", b"null", b'{"detail": 5}', b'{"detail": [1]}', b'{"error": 3}'):
            err = map_http_error(status, {"retry-after": "zz"}, content, base_url=BASE, model=None,
                                 has_images=False, timeout_ms=1)
            assert err.code in CODES and err.http_status == status


def test_unreachable_connect_error():
    err = map_transport_error(httpx.ConnectError("refused"), base_url=BASE, timeout_ms=1000)
    check(err, "OJ_UNREACHABLE", None, retryable=True)
    assert BASE in err.message and "mise run start" in err.hint


def test_unreachable_connect_timeout_and_other_http_error():
    assert map_transport_error(httpx.ConnectTimeout("t"), base_url=BASE, timeout_ms=1).code == "OJ_UNREACHABLE"
    assert map_transport_error(httpx.RemoteProtocolError("x"), base_url=BASE, timeout_ms=1).code == "OJ_UNREACHABLE"
    assert map_transport_error(OSError("dns"), base_url=BASE, timeout_ms=1).code == "OJ_UNREACHABLE"


def test_timeout():
    for exc in (httpx.ReadTimeout("t"), TimeoutError()):
        err = map_transport_error(exc, base_url=BASE, timeout_ms=1234)
        check(err, "OJ_TIMEOUT", None, retryable=True)
        assert "1234 ms" in err.message and BASE in err.message and "samples:1" in err.hint


def test_internal_on_unknown_exception():
    err = map_transport_error(ValueError("x"), base_url=BASE, timeout_ms=1)
    assert err.code == "OJ_INTERNAL" and err.message == "internal error in openjev-mcp: ValueError"


def test_parse_retry_after():
    assert parse_retry_after({"retry-after": "2"}) == 2.0
    assert parse_retry_after({"Retry-After": "0.5"}) == 0.5
    assert parse_retry_after({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
    assert parse_retry_after({"retry-after": "-1"}) is None
    assert parse_retry_after({"retry-after": "nan"}) is None
    assert parse_retry_after({}) is None


def test_parse_server_timing():
    assert parse_server_timing("model;dur=0.0, server;dur=1886.3, total;dur=1886.3") == {
        "model_ms": 0.0, "server_ms": 1886.3, "total_ms": 1886.3}
    assert parse_server_timing(None) is None and parse_server_timing("") is None
    assert parse_server_timing("garbage") is None and parse_server_timing("total;dur=x") is None
