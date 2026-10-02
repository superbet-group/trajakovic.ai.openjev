"""Stub OpenJev for the MCP tests: a stub engine behind the real FastAPI app, httpx fault
transports and a replay of the captured spec examples. No model, no network."""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from openjev.api import create_app
from openjev.config import Settings

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SPEC_TESTS = os.path.join(REPO, "docs", "mcp-skill-spec", "tests")


class StubEngine:
    """Replaces app.state.engine. decide() returns canned answers or raises the server's own errors."""

    def __init__(self, answers: Callable[[dict, Any, dict], dict] | None = None,
                 fault: Callable[[dict, Any, dict], BaseException | None] | None = None,
                 input_tokens: int = 100, thought_tokens: int = 0, delay_s: float = 0.0):
        self.answers = answers or default_answers
        self.fault = fault
        self.input_tokens = input_tokens
        self.thought_tokens = thought_tokens
        self.delay_s = delay_s
        self.calls: list[dict] = []

    async def decide(self, questions, state, seed, images=None, options=None):
        self.calls.append({"questions": questions, "state": state, "seed": seed, "images": images,
                           "options": options})
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fault is not None:
            exc = self.fault(questions, state, options or {})
            if exc is not None:
                raise exc
        return self.answers(questions, state, options or {}), self.input_tokens, self.thought_tokens

    async def close(self) -> None:
        pass


def default_answers(questions: dict, state: Any, options: dict) -> dict:
    """Deterministic server-shaped answers, one per question."""
    out: dict[str, dict] = {}
    for qid, q in questions.items():
        kind = q.get("type")
        if kind == "noul":
            out[qid] = {"type": "noul", "noul": 0.9}
        elif kind == "choice":
            labels = list(q.get("criteria") or [])
            first = labels[0] if labels else ""
            probs = {label: (0.7 if i == 0 else 0.3 / max(1, len(labels) - 1)) for i, label in enumerate(labels)}
            out[qid] = {"type": "choice", "choice": first, "probabilities": probs, "confidence": 0.7}
        elif kind == "score":
            levels = q.get("criteria") or []
            n = len(levels)
            probs = [0.7 if i == 0 else 0.3 / max(1, n - 1) for i in range(n)]
            out[qid] = {"type": "score", "score": sum(i * p for i, p in enumerate(probs)),
                        "legend": {str(i): lv for i, lv in enumerate(levels)},
                        "probabilities": {str(i): p for i, p in enumerate(probs)}, "confidence": 0.7}
        else:
            out[qid] = {"type": kind}
    return out


def openjev_app(*, engine: StubEngine | None = None, api_key: str = "", origin_secret: str = "",
                max_body_bytes: int | None = None):
    """The real OpenJev app over a stub engine. The lifespan is never entered (it loads a
    tokenizer); the state it would set up is set by hand."""
    kwargs = {} if max_body_bytes is None else {"max_body_bytes": max_body_bytes}
    app = create_app(Settings(backend="vllm", api_key=api_key, origin_secret=origin_secret, **kwargs),
                     tokenizer=None)
    app.state.engine = engine or StubEngine()
    app.state.routes = httpx.AsyncClient()
    return app


def asgi_transport(app) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=app)


class RecordingTransport(httpx.MockTransport):
    def __init__(self, handler):
        super().__init__(handler)
        self.requests: list[httpx.Request] = []


def _as_response(item, request: httpx.Request) -> httpx.Response:
    if isinstance(item, BaseException):
        raise item
    if isinstance(item, httpx.Response):
        return item
    status, headers, body = item
    return httpx.Response(status, headers=headers, content=body, request=request)


def fault_transport(status: int | None = None, *, headers=None, body: bytes | str = b"",
                    exc: BaseException | None = None, delay_s: float = 0.0,
                    sequence: Sequence[Any] | None = None) -> RecordingTransport:
    """Faults create_app cannot produce. `sequence` holds one item per attempt: an exception, an
    httpx.Response or a (status, headers, body) tuple; the last item repeats. Requests are
    recorded on .requests."""
    content = body.encode() if isinstance(body, str) else body
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        transport.requests.append(request)
        if delay_s:
            await asyncio.sleep(delay_s)
        if sequence:
            item = sequence[min(attempts, len(sequence) - 1)]
            attempts += 1
            return _as_response(item, request)
        if exc is not None:
            raise exc
        return httpx.Response(status if status is not None else 200, headers=headers, content=content,
                              request=request)

    transport = RecordingTransport(handler)
    return transport


def fail_on_request_transport() -> RecordingTransport:
    """Any request is a test failure (lint and dry paths: no network I/O)."""
    def handler(request: httpx.Request) -> httpx.Response:
        transport.requests.append(request)
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    transport = RecordingTransport(handler)
    return transport


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _resolve_images(req: dict) -> dict:
    """Same as run_cases.py: {"$file": path} becomes a data URL."""
    images = req.get("images")
    if not isinstance(images, list):
        return req
    out = []
    for im in images:
        if isinstance(im, dict) and "$file" in im:
            path = os.path.join(REPO, im["$file"])
            ctype = mimetypes.guess_type(path)[0] or "image/jpeg"
            with open(path, "rb") as f:
                out.append(f"data:{ctype};base64,{base64.b64encode(f.read()).decode()}")
        else:
            out.append(im)
    return {**req, "images": out}


def _load_cases() -> tuple[list[dict], dict]:
    with open(os.path.join(SPEC_TESTS, "cases", "00-spec-examples.json")) as f:
        cases = json.load(f)["cases"]
    with open(os.path.join(SPEC_TESTS, "spec_build", "captured.json")) as f:
        captured = json.load(f)
    return cases, captured


def replay_transport(case_ids: Sequence[str] | None = None) -> RecordingTransport:
    """Answers a request with the captured response of the spec example whose request body equals
    it (canonical JSON; GET cases match on method and path). Unmatched raises AssertionError
    naming the closest case id."""
    cases, captured = _load_cases()
    cases = [c for c in cases if c["id"] in captured and (case_ids is None or c["id"] in case_ids)]

    def key(case: dict) -> tuple[str, str, str]:
        method = case.get("method", "POST")
        endpoint = case.get("endpoint", "/v1/systemone")
        body = "" if method == "GET" else canonical(_resolve_images(case.get("request", {})))
        return method, endpoint, body

    index = {key(c): c for c in cases}

    def closest(method: str, path: str, body: str) -> str:
        def score(c: dict) -> int:
            m, e, b = key(c)
            same = sum(x == y for x, y in zip(b, body))
            return (m == method) * 1000000 + (e == path) * 100000 + same
        return max(cases, key=score)["id"] if cases else "<no cases>"

    def handler(request: httpx.Request) -> httpx.Response:
        transport.requests.append(request)
        path = request.url.path
        raw = request.content.decode("utf-8") if request.content else ""
        try:
            body = canonical(json.loads(raw)) if raw and request.method != "GET" else ""
        except ValueError:
            body = raw
        case = index.get((request.method, path, body))
        if case is None:
            raise AssertionError(f"no captured case matches {request.method} {path}; closest case id: "
                                 f"{closest(request.method, path, body)}")
        cap = captured[case["id"]]
        headers = {"server-timing": cap["server_timing"], "x-request-id": cap["request_id"],
                   "content-type": "application/json"}
        return httpx.Response(cap["status"], headers=headers, content=json.dumps(cap["body"]).encode(),
                              request=request)

    transport = RecordingTransport(handler)
    return transport


def slow_transport(delay_s: float, inner: httpx.AsyncBaseTransport) -> httpx.AsyncBaseTransport:
    """Sleeps delay_s before delegating each request (batch cancellation tests)."""
    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(delay_s)
            return await inner.handle_async_request(request)

    return Slow()


def overload_transport(pattern: Callable[[int, httpx.Request], bool] | Sequence[int] | int, inner: httpx.AsyncBaseTransport,
                       retry_after: float = 0.01) -> RecordingTransport:
    """529 + retry-after for the chosen calls (0-based call index: a set/list of indices, an int N
    for the first N calls, or a predicate(index, request)), then delegates to inner."""
    if callable(pattern):
        hit = pattern
    elif isinstance(pattern, int):
        hit = lambda i, r: i < pattern  # noqa: E731
    else:
        chosen = set(pattern)
        hit = lambda i, r: i in chosen  # noqa: E731
    n = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal n
        i, n = n, n + 1
        transport.requests.append(request)
        if hit(i, request):
            return httpx.Response(529, headers={"retry-after": str(retry_after)}, request=request,
                                  json={"error": {"type": "overloaded", "message": "overloaded"}})
        return await inner.handle_async_request(request)

    transport = RecordingTransport(handler)
    return transport


def captured(case_id: str) -> dict:
    """The captured response body of an ex-*/u* case of the spec tests (KeyError when unknown)."""
    return _load_cases()[1][case_id]["body"]
