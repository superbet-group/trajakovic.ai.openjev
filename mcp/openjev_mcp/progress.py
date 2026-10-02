"""Rate-limited progress notifications."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

Send = Callable[[float, "float | None", "str | None"], Awaitable[None]]


class ProgressEmitter:
    def __init__(self, send: Send | None, *, min_interval_s: float = 1.0,
                 clock: Callable[[], float] = time.monotonic):
        self._send = send
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._last_progress: float | None = None
        self._last_at: float | None = None

    @classmethod
    def noop(cls) -> ProgressEmitter:
        return cls(None)

    async def emit(self, progress: float, total: float | None = None, message: str | None = None) -> bool:
        if self._send is None:
            return False
        if self._last_progress is not None and progress <= self._last_progress:
            return False
        now = self._clock()
        if self._last_at is not None and now - self._last_at < self._min_interval_s:
            return False
        self._last_progress = progress
        self._last_at = now
        await self._send(progress, total, message)
        return True
