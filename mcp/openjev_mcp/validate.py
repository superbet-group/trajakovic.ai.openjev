"""Draft 2020-12 argument validation, mapped to OJ_INVALID_INPUT."""
from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from .errors import ToolError, invalid_input
from .schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS, register_tool_schemas  # noqa: F401 (re-export)

HINT_MAX = 1024
MESSAGE_MAX = 500


@cache
def _validator(kind: str, tool: str) -> Draft202012Validator:
    return Draft202012Validator((INPUT_SCHEMAS if kind == "in" else OUTPUT_SCHEMAS)[tool])


def _dotted(path) -> str:
    return ".".join(str(p) for p in path)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _branch_mismatch(err) -> bool:
    return err.validator == "const" or (err.validator == "type" and not err.relative_path)


def _deepest(err):
    """Descend into oneOf/anyOf to the branch the instance was aiming at (not a const/root-type miss)."""
    while err.validator in ("oneOf", "anyOf") and err.context:
        branches: dict[Any, list] = {}
        for sub in err.context:
            branches.setdefault(sub.relative_schema_path[0], []).append(sub)
        live = [e for errs in branches.values() if not any(map(_branch_mismatch, errs)) for e in errs]
        if not live:
            break
        err = best_match(live)
    return err


def validate_args(tool: str, args: Mapping[str, Any] | None) -> ToolError | None:
    """First failing error (best_match) as OJ_INVALID_INPUT, else None."""
    err = best_match(_validator("in", tool).iter_errors({} if args is None else args))
    if err is None:
        return None
    err = _deepest(err)
    hint = "expected: " + _clip(json.dumps(err.schema, ensure_ascii=False), HINT_MAX)
    return invalid_input(_dotted(err.absolute_path) or "arguments", _clip(err.message, MESSAGE_MAX), hint)


def validate_output(tool: str, structured: Any) -> list[str]:
    """Error messages against the output schema; [] when valid. Tests only."""
    errs = sorted(_validator("out", tool).iter_errors(structured), key=lambda e: list(map(str, e.absolute_path)))
    return [f"{_dotted(e.absolute_path) or '$'}: {e.message}" for e in errs]
