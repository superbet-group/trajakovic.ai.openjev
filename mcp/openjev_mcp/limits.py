"""Effective OpenJev limits and per-model capabilities (spec 2.2), cached from /v1/limits."""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import anyio

from .config import Config
from .errors import ToolError

if TYPE_CHECKING:
    from .http import OpenJevClient

DEFAULT_LIMITS: dict = {"questions": 256, "choice_options": 255, "score_levels": 10, "images": 8,
                        "image_bytes": 5242880, "prompt_tokens": None, "body_bytes": 67108864}
MLX_PROMPT_TOKENS = 32768
VLLM_PROMPT_TOKENS = 65536
CHAT_MODELS = frozenset({"diffusiongemma-26b"})
ALIASES_ACCEPTED = ("jev-latest", "jev-preview")
ALIAS_TARGET = {"openjev-latest": "openjev-0.1", **{a: "openjev-0.1" for a in ALIASES_ACCEPTED}}
OPTIONS = ("images", "steps", "samples", "think", "sequential")


def _row(options: bool, prompt: int | None, choices: int | None) -> dict:
    return {**{o: options for o in OPTIONS}, "max_prompt_tokens": prompt, "max_choices": choices}


CAPABILITY_MATRIX: dict[str, dict] = {
    "openjev-0.1": _row(True, None, 255),
    "laya-1.0": _row(False, 1024, None),
    "verdict-1.4": _row(False, 512, 24),
    "clm-v0.1": _row(False, 2048, None),
    "jevk5-0.2": _row(False, 16384, None),
}
BATCH_CAPS = {"concurrency_max": 4, "max_items_per_call": 100, "max_items": 5000}
LIMITS_404_WARNING = ("GET /v1/limits not available (404): limits are the documented defaults, "
                      "limit-dependent lint findings are warnings, backend unknown")


def _prompt_tokens(backend: str) -> int | None:
    return {"mlx": MLX_PROMPT_TOKENS, "vllm": VLLM_PROMPT_TOKENS}.get(backend)


def _matrix_row(name: str, backend: str) -> dict | None:
    row = CAPABILITY_MATRIX.get(name)
    if row is None:
        return None
    row = dict(row)
    if name == "openjev-0.1":
        row["max_prompt_tokens"] = _prompt_tokens(backend)
    return row


@dataclass(frozen=True)
class Limits:
    source: str                          # "server" | "default"
    backend: str                         # vllm|mlx|laya|verdict|clm|jevk5|"unknown"
    values: dict
    models: dict[str, dict]
    known_models: tuple[str, ...] | None
    logs_bodies: bool | None
    read_at: float                       # time.time() of the read, 0.0 for defaults
    warnings: tuple[str, ...]

    def capabilities(self, model: str) -> dict:
        name = ALIAS_TARGET.get(model, model)
        row = self.models.get(name) or _matrix_row(name, self.backend)
        if row is None:
            return _row(True, None, None)
        return dict(row)

    def resource(self, config: Config) -> dict:
        read_at = None if not self.read_at else (
            datetime.fromtimestamp(self.read_at, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
        return {"limit_source": self.source, "backend": self.backend, "limits": dict(self.values),
                "capabilities": {n: dict(r) for n, r in self.models.items()},
                "batch": {**BATCH_CAPS, "max_inflight_batch": config.max_inflight_batch},
                "logs_bodies": self.logs_bodies, "read_at": read_at, "warnings": list(self.warnings)}


def _decide_models(known_models: Sequence[str] | None) -> list[str]:
    names = ["openjev-0.1"]
    for n in known_models or ():
        if n not in CHAT_MODELS and n not in ALIAS_TARGET and n not in names:
            names.append(n)
    return names


def default_limits(known_models: Sequence[str] | None = None) -> Limits:
    models = {n: _matrix_row(n, "unknown") or _row(True, None, None) for n in _decide_models(known_models)}
    return Limits("default", "unknown", dict(DEFAULT_LIMITS), models,
                  tuple(known_models) if known_models is not None else None, None, 0.0, ())


def _int(obj: dict, key: str, default: Any, where: str, null_ok: bool = False) -> Any:
    if key not in obj:
        return default
    value = obj[key]
    if value is None and null_ok:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{where}.{key} must be a non-negative integer")
    return value


def parse_limits(payload: dict, *, known_models: Sequence[str] | None = None, now: float) -> Limits:
    if not isinstance(payload, dict):
        raise ValueError("limits payload is not an object")
    backend = payload.get("backend")
    request, served = payload.get("request"), payload.get("models")
    if not isinstance(backend, str) or not backend:
        raise ValueError("limits.backend must be a string")
    if not isinstance(request, dict):
        raise ValueError("limits.request must be an object")
    if not isinstance(served, dict):
        raise ValueError("limits.models must be an object")
    logs = payload.get("logs_bodies")
    if logs is not None and not isinstance(logs, bool):
        raise ValueError("limits.logs_bodies must be a boolean")
    steps, samples = request.get("steps"), request.get("samples")
    for name, rng in (("steps", steps), ("samples", samples)):
        if rng is not None and not (isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, int) for x in rng)):
            raise ValueError(f"limits.request.{name} must be [min, max]")
    models: dict[str, dict] = {}
    for name, entry in served.items():
        if not isinstance(entry, dict):
            raise ValueError(f"limits.models.{name} must be an object")
        if name in CHAT_MODELS:
            continue
        base = _matrix_row(name, backend) or _row(True, None, None)
        row = dict(base)
        if steps is not None:
            row["steps"] = base["steps"] and steps[1] > 1
        if samples is not None:
            row["samples"] = base["samples"] and samples[1] > 1
        for opt in ("images", "think", "sequential"):
            if isinstance(entry.get(opt), bool):
                row[opt] = entry[opt]
        row["max_prompt_tokens"] = _int(entry, "max_prompt_tokens", row["max_prompt_tokens"], f"models.{name}", True)
        row["max_choices"] = _int(entry, "max_choices", row["max_choices"], f"models.{name}", True)
        if "max_score_levels" in entry:
            row["max_score_levels"] = _int(entry, "max_score_levels", None, f"models.{name}", True)
        models[name] = row
    main = models.get("openjev-0.1") or next((r for r in models.values()), None) or {}
    values = {
        "questions": _int(request, "max_questions", DEFAULT_LIMITS["questions"], "request"),
        "choice_options": main.get("max_choices") or DEFAULT_LIMITS["choice_options"],
        "score_levels": main.get("max_score_levels") or DEFAULT_LIMITS["score_levels"],
        "images": _int(request, "max_images", DEFAULT_LIMITS["images"], "request"),
        "image_bytes": _int(request, "max_image_bytes", DEFAULT_LIMITS["image_bytes"], "request"),
        "prompt_tokens": main.get("max_prompt_tokens") or _prompt_tokens(backend),
        "body_bytes": _int(request, "max_body_bytes", DEFAULT_LIMITS["body_bytes"], "request"),
    }
    return Limits("server", backend, values, models, tuple(known_models) if known_models is not None else None,
                  logs, now, ())


def _model_names(data: Any) -> tuple[str, ...]:
    entries = data.get("models") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not all(isinstance(m, dict) and isinstance(m.get("name"), str) for m in entries):
        raise ToolError("OJ_PROTOCOL", "GET /v1/models did not answer a model list",
                        hint="OPENJEV_BASE_URL may point at something that is not OpenJev")
    return tuple(m["name"] for m in entries)


class LimitsCache:
    def __init__(self, client: OpenJevClient, *, ttl_s: float = 60.0, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time):
        self._client = client
        self._ttl = ttl_s
        self._clock = clock
        self._wall = wall
        self._limits: Limits | None = None
        self._fetched: float = 0.0
        self._lock = anyio.Lock()

    def peek(self) -> Limits:
        return self._limits or default_limits()

    async def refresh(self) -> Limits:
        models = _model_names((await self._client.get("/v1/models")).data)
        try:
            res = await self._client.get("/v1/limits", ok=(200, 404))
        except ToolError as err:
            limits = replace(default_limits(models), warnings=(
                f"GET /v1/limits failed ({err.code}): limits are the documented defaults",))
        else:
            if res.status == 404:
                limits = replace(default_limits(models), warnings=(LIMITS_404_WARNING,))
            else:
                try:
                    limits = parse_limits(res.data, known_models=models, now=self._wall())
                except ValueError as exc:
                    limits = replace(default_limits(models), warnings=(
                        f"GET /v1/limits answered an unusable payload ({exc}): limits are the documented defaults",))
        self._limits, self._fetched = limits, self._clock()
        return limits

    async def get(self) -> Limits:
        async with self._lock:
            if self._limits is not None and self._clock() - self._fetched < self._ttl:
                return self._limits
            try:
                return await self.refresh()
            except Exception:
                return self.peek()
