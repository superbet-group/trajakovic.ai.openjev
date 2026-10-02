"""Stored `calibrate` records (spec 2.15, 2.18): the .json audit record, the openjev://audits/{question_hash} resource
under OPENJEV_MCP_AUDIT_DIR (ttlMs 0, private). A record is a dict {"openjev_mcp": "calibrate", "question_hash", ...}."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from openjev_mcp import wire
from openjev_mcp.config import Config
from openjev_mcp.errors import invalid_input
from openjev_mcp.paths import resolve_read, resolve_write
from openjev_mcp.resources import ResourceNotFound
from openjev_mcp.tools import ToolContext

MARK = "calibrate"
AUDIT_PREFIX = "openjev://audits/"
AUDIT_URI = re.compile(r"^openjev://audits/.+$")
_HASH = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")


def is_record(obj) -> bool:
    return isinstance(obj, dict) and obj.get("openjev_mcp") == MARK and isinstance(obj.get("question_hash"), str)


def _parse(text: str, path: str, what: str) -> dict:
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    if not is_record(obj):
        raise invalid_input(path, f"{what} is not a calibrate audit record", "use a new .json path or a record written by calibrate")
    return obj


def load_record(path: str, config: Config) -> dict:
    """An existing record inside the allowed roots (compare_to)."""
    return _parse(resolve_read(path, config).read_text(encoding="utf-8", errors="replace"), path, "compare_to")


def _write(target: Path, record: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-calibrate-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, indent=1, ensure_ascii=False) + "\n")
        os.replace(tmp, target)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def write_record(path: str, record: dict, config: Config) -> Path:
    """Write `record` to `path` (.json, inside the roots). An existing file is replaced only when it parses as a calibrate
    record. A copy goes to the audit dir under its question hash for the openjev://audits resource."""
    target = resolve_write(path, config, exts={".json"}, new_only=False)
    if os.path.lexists(target):
        _parse(target.read_text(encoding="utf-8", errors="replace"), path, "store path holds a file that")
    _write(target, record)
    publish(record, config)
    return target


def publish(record: dict, config: Config) -> Path | None:
    """Copy into OPENJEV_MCP_AUDIT_DIR as <hex>.json (the hash is the key; the latest record wins)."""
    m = _HASH.match(record["question_hash"])
    if not config.audit_dir or not m:
        return None
    os.makedirs(config.audit_dir, exist_ok=True)
    out = Path(config.audit_dir) / f"{m.group(1)}.json"
    _write(out, record)
    return out


def audits_template() -> dict:
    return {"uriTemplate": AUDIT_PREFIX + "{question_hash}", "name": "audits", "title": "Stored calibrate records",
            "mimeType": "application/json",
            "description": "The latest calibrate audit record of a question set, by question_hash (sha256:<hex> or <hex>).",
            "annotations": {"audience": ["user", "assistant"], "priority": 0.7}}


async def read_audit(uri: str, ctx: ToolContext) -> dict:
    from urllib.parse import unquote
    raw = unquote(uri[len(AUDIT_PREFIX):]).strip()
    m = _HASH.match(raw)
    if not m or not ctx.config.audit_dir:
        raise ResourceNotFound(uri)
    file = Path(ctx.config.audit_dir) / f"{m.group(1)}.json"
    try:
        text = file.read_text(encoding="utf-8")
        if not is_record(json.loads(text)):
            raise ValueError
    except (OSError, ValueError):
        raise ResourceNotFound(uri) from None
    return {"contents": [{"uri": uri, "mimeType": "application/json", "text": wire.dumps_text(json.loads(text))}],
            "ttlMs": 0, "cacheScope": "private"}
