"""The only module that talks to OpenJev: auth headers, retry policy, in-flight cap (arch D.6)."""
from __future__ import annotations

import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import anyio
import httpx

from . import __version__, wire
from .config import Config
from .errors import RETRYABLE, RETRY_ONCE, ToolError
from .mapping import map_http_error, map_transport_error, parse_server_timing


@dataclass(frozen=True)
class HttpResult:
    status: int
    data: Any
    headers: Mapping[str, str]
    request_id: str | None
    body_hash: str | None
    latency_ms: float
    server_timing: dict[str, float] | None
    attempts: int
    retried: dict | None


class OpenJevClient:
    def __init__(self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None,
                 max_inflight: int | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
                 jitter: Callable[[], float] = lambda: random.uniform(0, 0.25)):
        self.config = config
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        headers = {"user-agent": f"openjev-mcp/{__version__}"}
        if config.api_key:
            headers["authorization"] = f"Bearer {config.api_key}"
        if config.origin_secret:
            headers["x-origin-secret"] = config.origin_secret
        self._http = httpx.AsyncClient(base_url=config.base_url, transport=transport, timeout=None, headers=headers)
        self._sem = anyio.Semaphore(max_inflight or config.max_inflight)
        self._pools = {"default": self._sem, "batch": anyio.Semaphore(config.max_inflight_batch)}   # batch never starves a gate

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> OpenJevClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def systemone(self, body: dict, *, timeout_ms: int, think: int = 0, has_images: bool = False,
                        deadline_ms: int | None = None, known_models: Sequence[str] | None = None,
                        pool: str = "default", retries: int | None = None) -> HttpResult:
        """pool 'batch' has its own in-flight cap (config.max_inflight_batch); retries overrides config.retries."""
        if pool not in self._pools:
            raise ValueError(f"pool={pool!r}")
        raw = wire.body_bytes(body)
        return await self._run("POST", "/v1/systemone", content=raw, body_hash=wire.body_hash(raw),
                               model=body.get("model"), timeout_ms=timeout_ms, ok=range(200, 300), think=think,
                               has_images=has_images, deadline_ms=deadline_ms, known_models=known_models,
                               max_retries=self.config.retries if retries is None else retries,
                               sem=self._pools[pool])

    async def chat(self, body: dict, *, timeout_ms: int, deadline_ms: int | None = None,
                   retries: int | None = None) -> HttpResult:
        """POST /v1/chat/completions (generate). Errors arrive in the OpenAI shape and map through mapping.py."""
        raw = wire.body_bytes(body)
        return await self._run("POST", "/v1/chat/completions", content=raw, body_hash=wire.body_hash(raw),
                               model=body.get("model"), timeout_ms=timeout_ms, ok=range(200, 300), think=0,
                               has_images=False, deadline_ms=deadline_ms, known_models=None,
                               max_retries=self.config.retries if retries is None else retries, sem=self._sem)

    async def get(self, path: str, *, timeout_ms: int = 5000, ok: tuple[int, ...] = (200,),
                  retry: bool = False) -> HttpResult:
        return await self._run("GET", path, content=None, body_hash=None, model=None, timeout_ms=timeout_ms, ok=ok,
                               think=0, has_images=False, deadline_ms=None, known_models=None,
                               max_retries=self.config.retries if retry else 0, sem=self._sem)

    async def _run(self, method: str, path: str, *, content: bytes | None, body_hash: str | None, model: str | None,
                   timeout_ms: int, ok: Sequence[int], think: int, has_images: bool, deadline_ms: int | None,
                   known_models: Sequence[str] | None, max_retries: int, sem: anyio.Semaphore) -> HttpResult:
        deadline = None if deadline_ms is None else self._clock() + deadline_ms / 1000
        attempts = 0
        last: ToolError | None = None
        while True:
            attempts += 1
            try:
                result = await self._attempt(method, path, content, body_hash, model, timeout_ms, ok, has_images,
                                             deadline, known_models, sem)
            except ToolError as err:
                if not self._again(err, attempts, think, max_retries):
                    raise
                delay = (err.retry_after_s if err.retry_after_s is not None else 1.0) + self._jitter()
                if deadline is not None and self._clock() + delay >= deadline:
                    raise
                last = err
                await self._sleep(delay)
                continue
            if last is None:
                return result
            return replace(result, attempts=attempts,
                           retried={"status": last.http_status, "code": last.code, "attempts": attempts})

    def _again(self, err: ToolError, attempts: int, think: int, max_retries: int) -> bool:
        if err.code in RETRYABLE:
            return attempts <= max_retries
        if err.code in RETRY_ONCE and max_retries > 0:
            return attempts <= 1 and not (err.code == "OJ_TIMEOUT" and think > 0)
        return False

    async def _attempt(self, method, path, content, body_hash, model, timeout_ms, ok, has_images, deadline,
                       known_models, sem) -> HttpResult:
        base = self.config.base_url
        async with sem:
            limit_ms = timeout_ms
            if deadline is not None:
                limit_ms = min(timeout_ms, int((deadline - self._clock()) * 1000))
                if limit_ms <= 0:
                    raise map_transport_error(TimeoutError(), base_url=base, timeout_ms=timeout_ms)
            headers = {"content-type": "application/json"} if content is not None else None
            started = self._clock()
            try:
                with anyio.fail_after(limit_ms / 1000):
                    resp = await self._http.request(method, path, content=content, headers=headers)
            except (httpx.HTTPError, OSError) as exc:
                raise map_transport_error(exc, base_url=base, timeout_ms=limit_ms) from None
            latency_ms = (self._clock() - started) * 1000
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
        if resp.status_code not in ok:
            raise map_http_error(resp.status_code, hdrs, resp.content, base_url=base, model=model,
                                 has_images=has_images, timeout_ms=limit_ms, known_models=known_models)
        try:
            data = json.loads(resp.content) if resp.content else None
        except ValueError:
            data = None
        return HttpResult(resp.status_code, data, hdrs, hdrs.get("x-request-id"), body_hash, latency_ms,
                          parse_server_timing(hdrs.get("server-timing")), 1, None)
