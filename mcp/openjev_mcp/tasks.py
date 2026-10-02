"""MCP Tasks extension (io.modelcontextprotocol/tasks) for batch and calibrate, behind OPENJEV_MCP_TASKS.

A 2026-07-28 client that lists the identifier in its request _meta clientCapabilities.extensions gets a
CreateTaskResult instead of the synchronous result; anyone else gets the synchronous result (never -32021).
The store is in-process (dies with the process); results are kept until ttl.
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
import uuid
from collections.abc import Sequence
from typing import Any

import anyio
import mcp_types as types
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.extension import Extension, MethodBinding
from mcp.shared.exceptions import MCPError
from mcp_types import CLIENT_CAPABILITIES_META_KEY, CallToolRequestParams, RequestParams

from openjev_mcp.config import Config

IDENTIFIER = "io.modelcontextprotocol/tasks"
ERA = "2026-07-28"
TOOLS = frozenset({"batch", "calibrate"})
TERMINAL = frozenset({"completed", "failed", "cancelled"})


class TaskParams(RequestParams):
    task_id: str
    input_responses: dict[str, Any] | None = None


class _Task:
    def __init__(self, ttl_ms: int, poll_ms: int):
        self.id = uuid.uuid4().hex
        self.status = "working"
        self.message: str | None = None
        self.result: dict | None = None
        self.error: dict | None = None
        self.ttl_ms, self.poll_ms = ttl_ms, poll_ms
        self.created = self.updated = time.time()
        self.scope = anyio.CancelScope()
        self.done_at: float | None = None

    def finish(self, status: str, *, result=None, error=None) -> None:
        if self.status in TERMINAL:
            return
        self.status, self.result, self.error = status, result, error
        self.updated = self.done_at = time.time()

    def view(self, full: bool = True) -> dict:
        out: dict[str, Any] = {"taskId": self.id, "status": self.status, "ttlMs": self.ttl_ms,
                               "pollIntervalMs": self.poll_ms}
        if full:
            if self.message:
                out["statusMessage"] = self.message
            if self.status == "completed":
                out["result"] = self.result
            elif self.status == "failed":
                out["error"] = self.error
        return out


class _Session:
    """Stands in for ctx.session inside a task: progress becomes the task's status message, nothing is sent."""

    def __init__(self, real, task: _Task):
        self._real, self._task = real, task

    async def report_progress(self, progress: float, total: float | None = None, message: str | None = None) -> None:
        if self._task.status == "working":
            self._task.message = message or (f"{progress:g}/{total:g}" if total else f"{progress:g}")

    def __getattr__(self, name: str):
        return getattr(self._real, name)


def _declared(ctx: ServerRequestContext) -> bool:
    caps = (ctx.meta or {}).get(CLIENT_CAPABILITIES_META_KEY)   # ctx.meta exposes it (verified); dict-like
    if hasattr(caps, "model_dump"):
        caps = caps.model_dump(by_alias=True)
    exts = caps.get("extensions") if isinstance(caps, dict) else None
    return isinstance(exts, dict) and IDENTIFIER in exts


class TasksExtension(Extension):
    identifier = IDENTIFIER

    def __init__(self, *, max_running: int = 4, ttl_ms: int = 600_000, poll_ms: int = 1000):
        self.max_running, self.ttl_ms, self.poll_ms = max_running, ttl_ms, poll_ms
        self.tasks: dict[str, _Task] = {}
        self._tg: anyio.abc.TaskGroup | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._slots = anyio.Semaphore(max_running)

    # deviation: P12 brief: the SDK Extension has no lifecycle hook, so the extension-owned task group lives in a
    # private asyncio task started on first use (asyncio only, as uvicorn/stdio here); aclose() stops it
    async def _group(self) -> anyio.abc.TaskGroup:
        loop = asyncio.get_running_loop()
        if self._tg is None or self._loop is not loop:
            ready = asyncio.Event()
            self._loop, self._stop = loop, asyncio.Event()

            async def host() -> None:
                async with anyio.create_task_group() as tg:
                    self._tg = tg
                    ready.set()
                    await self._stop.wait()
                    tg.cancel_scope.cancel()

            self._host = loop.create_task(host())
            await ready.wait()
        return self._tg

    async def aclose(self) -> None:
        if self._stop is not None:
            self._stop.set()
            await self._host
            self._tg = None

    def _purge(self) -> None:
        now = time.time()
        for tid in [t.id for t in self.tasks.values() if t.done_at and now - t.done_at > t.ttl_ms / 1000]:
            del self.tasks[tid]

    def _get(self, params: TaskParams) -> _Task:
        self._purge()
        task = self.tasks.get(params.task_id)
        if task is None:
            raise MCPError(types.INVALID_PARAMS, "Unknown task", {"taskId": params.task_id})
        return task

    async def _run(self, task: _Task, ctx: ServerRequestContext, call_next: CallNext) -> None:
        inner = dataclasses.replace(ctx, session=_Session(ctx.session, task))
        try:
            with task.scope:
                async with self._slots:   # bounded: extra tasks stay `working` until a slot frees
                    res = await call_next(inner)
                data = res.model_dump(by_alias=True, exclude_none=True, mode="json") \
                    if hasattr(res, "model_dump") else res
                task.finish("completed", result=data)
                return
        except MCPError as exc:
            task.finish("failed", error=exc.error.model_dump(by_alias=True, exclude_none=True))
            return
        except Exception as exc:
            task.finish("failed", error={"code": types.INTERNAL_ERROR, "message": f"{type(exc).__name__}: {exc}"})
            return
        task.finish("cancelled")   # scope cancelled (tasks/cancel or shutdown)

    async def intercept_tool_call(self, params: CallToolRequestParams, ctx: ServerRequestContext,
                                  call_next: CallNext) -> HandlerResult:
        if params.name not in TOOLS or ctx.protocol_version != ERA or not _declared(ctx):
            return await call_next(ctx)
        self._purge()
        if len(self.tasks) >= 4 * self.max_running:   # working + retained: past the cap fall back to the synchronous result
            return await call_next(ctx)
        task = _Task(self.ttl_ms, self.poll_ms)
        self.tasks[task.id] = task
        (await self._group()).start_soon(self._run, task, ctx, call_next)
        return {"resultType": "task", "task": task.view(full=False)}

    async def _tasks_get(self, ctx, params: TaskParams) -> HandlerResult:
        return self._get(params).view()

    async def _tasks_update(self, ctx, params: TaskParams) -> HandlerResult:
        self._get(params)   # no input_required state is ever entered: responses are acknowledged and ignored
        return {}

    async def _tasks_cancel(self, ctx, params: TaskParams) -> HandlerResult:
        task = self._get(params)
        if task.status not in TERMINAL:
            task.finish("cancelled")
            task.scope.cancel()
        return {}

    def methods(self) -> Sequence[MethodBinding]:
        v = frozenset({ERA})
        return (MethodBinding("tasks/get", TaskParams, self._tasks_get, v),
                MethodBinding("tasks/update", TaskParams, self._tasks_update, v),
                MethodBinding("tasks/cancel", TaskParams, self._tasks_cancel, v))


def factory(config: Config) -> TasksExtension | None:
    return TasksExtension(max_running=config.max_inflight_batch) if config.tasks else None
