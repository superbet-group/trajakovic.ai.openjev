"""ToolError and the error codes of spec 2.4."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CODES = frozenset({"OJ_INVALID_INPUT", "OJ_VALIDATION", "OJ_REJECTED", "OJ_TOO_LONG", "OJ_UNKNOWN_MODEL",
    "OJ_BAD_TYPE", "OJ_AUTH", "OJ_FORBIDDEN", "OJ_NOT_FOUND", "OJ_TOO_LARGE", "OJ_RATE_LIMITED",
    "OJ_UNAVAILABLE", "OJ_OVERLOADED", "OJ_BAD_IMAGE", "OJ_SERVER", "OJ_UNREACHABLE", "OJ_TIMEOUT",
    "OJ_PROTOCOL", "OJ_INTERNAL"})
RETRYABLE = frozenset({"OJ_RATE_LIMITED", "OJ_UNAVAILABLE", "OJ_OVERLOADED", "OJ_UNREACHABLE"})
RETRY_ONCE = frozenset({"OJ_SERVER", "OJ_TIMEOUT"})

_UNSET: Any = object()


@dataclass(eq=False)
class ToolError(Exception):
    code: str
    message: str
    http_status: int | None = None
    hint: str | None = None
    path: str | None = None
    retryable: bool = False
    retry_after_s: float | None = None
    request_id: str | None = None
    server_detail: Any = _UNSET

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def to_dict(self) -> dict:
        """Wire ToolError (spec 2.2): hint only when set, server_detail only when set (None counts)."""
        out = {"code": self.code, "message": self.message, "http_status": self.http_status,
               "path": self.path, "retryable": self.retryable, "retry_after_s": self.retry_after_s,
               "request_id": self.request_id}
        if self.hint is not None:
            out["hint"] = self.hint
        if self.server_detail is not _UNSET:
            out["server_detail"] = self.server_detail
        return out


def invalid_input(path: str | None, message: str, hint: str | None = None) -> ToolError:
    return ToolError("OJ_INVALID_INPUT", message, path=path, hint=hint)
