"""Paths, knobs (ARCHITECTURE 1.9), port picking and the cross-process `claude` slot semaphore (1.7)."""
from __future__ import annotations

import fcntl
import os
import random
import socket
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FIXTURES = HERE / "fixtures"
CASES_DIR = HERE / "cases"
RESULTS = HERE / "results"
PY = str(REPO / ".venv/bin/python")
HOOK_BIN = str(REPO / ".venv/bin/openjev-hook")

BASE_URL = os.environ.get("OPENJEV_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
CLAUDE_BIN = os.environ.get("OJ_CLAUDE_BIN", "claude")
MODEL_CHEAP = os.environ.get("OJ_CLAUDE_MODEL", "haiku")
MODEL_STRONG = os.environ.get("OJ_CLAUDE_MODEL_STRONG", "sonnet")
SLOTS = max(1, int(os.environ.get("OJ_CLAUDE_SLOTS", "3")))
BUDGET_SCALE = float(os.environ.get("OJ_CLAUDE_BUDGET_SCALE", "1"))
RUN_BUDGET_USD = float(os.environ.get("OJ_CLAUDE_RUN_BUDGET_USD", "15"))
DEBUG = os.environ.get("OJ_CLAUDE_DEBUG", "0") == "1"
KEEP = os.environ.get("OJ_CLAUDE_KEEP", "0") == "1"
SLOT_WAIT_S = 30 * 60
STRIP = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
         "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")

_RUN_ID: str | None = None


def run_id() -> str:
    global _RUN_ID
    if _RUN_ID is None:
        _RUN_ID = os.environ.get("OJ_LIVE_RUN_ID") or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{os.getpid()}"
    return _RUN_ID


def free_port(lo: int = 8200, hi: int = 8299, skip=()) -> int:
    """First port in lo..hi (pid-staggered start, so parallel pytest processes rarely collide) that binds on 127.0.0.1."""
    n = hi - lo + 1
    start = os.getpid() % n
    for i in range(n):
        port = lo + (start + i) % n
        if port in skip:
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"no free port in {lo}-{hi}")


def slot_dir() -> Path:
    d = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / "openjev-claude-live-slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def slot(slots: int | None = None, wait_s: float = SLOT_WAIT_S, poll_s: float = 0.5) -> Iterator[int]:
    """Cross-process semaphore: one flock'd lock file per slot; held only around the `claude` subprocess."""
    n = slots if slots is not None else max(1, int(os.environ.get("OJ_CLAUDE_SLOTS", SLOTS)))
    d, deadline = slot_dir(), time.monotonic() + wait_s
    while True:
        for i in range(n):
            fh = open(d / f"slot-{i}.lock", "a+")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.close()
                continue
            try:
                yield i
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"harness: no claude slot free within {wait_s:.0f}s ({n} slots in {d})")
        time.sleep(poll_s + random.random() * 0.05)


def strip_env(env: dict) -> dict:
    """Child env without API-key / provider variables: claude can only use the subscription login."""
    return {k: v for k, v in env.items() if k not in STRIP}
