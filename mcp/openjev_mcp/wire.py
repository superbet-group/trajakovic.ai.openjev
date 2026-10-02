"""The one place body bytes, hashes and text-block JSON are produced."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

OPTION_ORDER = ("steps", "samples", "think", "sequential")


def build_body(model: str, state: Any, questions: dict, options: Mapping[str, Any] | None = None,
               images: list | None = None) -> dict:
    """Key order: model, present options in OPTION_ORDER, state, questions, images. timeout_ms and
    None values never reach the body."""
    body: dict[str, Any] = {"model": model}
    for key in OPTION_ORDER:
        if options and options.get(key) is not None:
            body[key] = options[key]
    body["state"] = state
    body["questions"] = questions
    if images:
        body["images"] = images
    return body


def dumps_text(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def body_bytes(body: Mapping[str, Any]) -> bytes:
    return dumps_text(body).encode("utf-8")


def body_hash(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical_hash(obj: Any) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
