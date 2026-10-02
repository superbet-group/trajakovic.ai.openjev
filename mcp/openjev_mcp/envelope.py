"""The only two producers of a tools/call result dict (camelCase wire keys)."""
from __future__ import annotations

import os
from collections.abc import Iterable

from .errors import ToolError
from .wire import dumps_text

TEXT_ANNOTATIONS = {"audience": ["assistant"]}


def file_link(path: str, mime: str) -> dict:
    """A resource_link block for a file the tool wrote or read (2.0.1 rule 6)."""
    real = os.path.realpath(path)
    return {"type": "resource_link", "uri": "file://" + real, "name": os.path.basename(real), "mimeType": mime,
            "annotations": {"audience": ["user", "assistant"]}}


def success_result(structured: dict, links: Iterable[dict] = ()) -> dict:
    text = {"type": "text", "text": dumps_text(structured), "annotations": TEXT_ANNOTATIONS}
    return {"content": [text, *links], "structuredContent": structured, "isError": False}


def error_result(err: ToolError) -> dict:
    return {"content": [{"type": "text", "text": dumps_text({"error": err.to_dict()}),
                         "annotations": TEXT_ANNOTATIONS}],
            "isError": True}
