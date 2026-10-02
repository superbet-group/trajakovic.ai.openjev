"""Optional JSONL audit log of read calls (OPENJEV_MCP_LOG). Must not import openjev_mcp.tools."""
from __future__ import annotations

import sys
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from .wire import canonical_hash, dumps_text


# set by dispatch.call_tool for the duration of a call; read_record picks traceparent/tracestate from it
# (SEP-414: accepted and logged only, never forwarded to OpenJev)
REQUEST_META: ContextVar[dict] = ContextVar("openjev_request_meta", default={})
TRACE_KEYS = ("traceparent", "tracestate")


class AuditLog:
    def __init__(self, path: str, *, log_states: bool = False):
        self.path = path
        self.log_states = log_states
        self._lock = threading.Lock()
        self._disabled = False

    def write(self, record: dict) -> None:
        line = dumps_text(record) + "\n"
        with self._lock:
            if self._disabled:
                return
            try:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError as exc:
                self._disabled = True
                print(f"openjev-mcp: audit log {self.path} disabled: {exc}", file=sys.stderr)


def read_record(tool: str, body: dict, outcome: Any, *, log_states: bool,
                decision: str | None = None, degraded: bool = False) -> dict:
    raw = outcome.raw if isinstance(outcome.raw, dict) else {}
    meta = outcome.meta or {}
    request_ids = meta.get("request_ids") or []
    if not request_ids:
        request_ids = [r.request_id for r in getattr(outcome, "results", []) or [] if r.request_id]
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "tool": tool,
        "question_hash": canonical_hash(body.get("questions")),
        "state_hash": canonical_hash(body.get("state")),
        "model_resolved": raw.get("model") or meta.get("model"),
        "request_id": request_ids[-1] if request_ids else None,
        "answers": outcome.answers,
        "decision": decision,
        "latency_ms": meta.get("latency_ms"),
        "degraded": degraded,
    }
    for key in TRACE_KEYS:
        if REQUEST_META.get().get(key):
            record[key] = REQUEST_META.get()[key]
    if log_states:
        record["state"] = body.get("state")
    return record
