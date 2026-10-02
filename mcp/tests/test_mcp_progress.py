from __future__ import annotations

import pytest

from openjev_mcp.progress import ProgressEmitter

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make(min_interval_s: float = 1.0):
    sent: list[tuple] = []
    clock = Clock()

    async def send(progress, total, message):
        sent.append((progress, total, message))

    return ProgressEmitter(send, min_interval_s=min_interval_s, clock=clock), clock, sent


async def test_first_immediate_then_throttled():
    em, clock, sent = make()
    assert await em.emit(1, 4, "a") is True
    clock.now += 0.5
    assert await em.emit(2, 4, "b") is False
    clock.now += 0.5
    assert await em.emit(2, 4, "b") is True
    assert sent == [(1, 4, "a"), (2, 4, "b")]


async def test_non_increasing_suppressed():
    em, clock, sent = make()
    assert await em.emit(3) is True
    clock.now += 5
    assert await em.emit(3) is False
    assert await em.emit(2) is False
    clock.now += 5
    assert await em.emit(4) is True
    assert [s[0] for s in sent] == [3, 4]


async def test_suppressed_emit_does_not_move_window():
    em, clock, sent = make()
    await em.emit(1)
    clock.now += 0.9
    assert await em.emit(2) is False
    clock.now += 0.2
    assert await em.emit(2) is True


async def test_no_send_never_sends():
    em = ProgressEmitter(None)
    assert await em.emit(1, 1, "x") is False


async def test_noop():
    em = ProgressEmitter.noop()
    assert await em.emit(1) is False
    assert await em.emit(2) is False
