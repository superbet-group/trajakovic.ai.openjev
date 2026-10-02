"""Opaque batch cursor: base64url compact JSON {v, run, offset, args, out:{bytes, lines}} (spec 2.11)."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from openjev_mcp.errors import invalid_input

ARGS_EXCLUDED = ("cursor", "max_items_per_call", "time_budget_s", "concurrency", "detail", "max_inline_results", "export")


def args_hash(args: Mapping[str, Any]) -> str:
    kept = {k: v for k, v in args.items() if k not in ARGS_EXCLUDED}
    return hashlib.sha256(json.dumps(kept, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def encode(run: str, offset: int, args: str, out_bytes: int, out_lines: int) -> str:
    raw = json.dumps({"v": 1, "run": run, "offset": offset, "args": args, "out": {"bytes": out_bytes, "lines": out_lines}},
                     separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _bad() -> Exception:
    return invalid_input("cursor", "invalid cursor", "drop the cursor and call again with resume:true")


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def decode(token: str) -> dict:
    try:
        data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except (ValueError, binascii.Error, TypeError):
        raise _bad() from None
    out = data.get("out") if isinstance(data, dict) else None
    if not (isinstance(data, dict) and data.get("v") == 1 and isinstance(data.get("run"), str) and _int(data.get("offset"))
            and isinstance(data.get("args"), str) and isinstance(out, dict) and _int(out.get("bytes")) and _int(out.get("lines"))):
        raise _bad()
    return data


def check(token: Mapping[str, Any], *, run_id: str, args_hash: str, out_size: int | None) -> None:
    """Raises OJ_INVALID_INPUT for a cursor of other arguments or an output file smaller than at the cursor."""
    if token["run"] != run_id or token["args"] != args_hash:
        raise invalid_input("cursor", "arguments changed since this cursor; drop cursor, keep resume:true",
                            "drop cursor, keep resume:true")
    if (out_size or 0) < token["out"]["bytes"]:
        raise invalid_input("cursor", "output file shrank since the cursor; call again without cursor",
                            "call again without cursor")
