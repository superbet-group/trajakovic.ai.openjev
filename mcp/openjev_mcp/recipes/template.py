"""Mustache-like state templates with JSON-string escaping (spec 2.18 templates)."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

LINE_BREAKS_TABLE = str.maketrans({"\x85": "\\u0085", " ": "\\u2028", " ": "\\u2029"})


class TemplateError(ValueError): ...


def _name(raw: str, pos: int) -> str:
    name = raw.strip()
    if name != "." and not name.isidentifier():
        raise TemplateError(f"bad tag name {name!r} at offset {pos}")
    return name


def _parse(template: str, raw_allowed: frozenset[str]) -> list[tuple]:
    """Nodes: ("text", s), ("var", name), ("raw", name), ("sec", name, children)."""
    root: list[tuple] = []
    stack: list[tuple[str, list[tuple]]] = [("", root)]
    i, n = 0, len(template)
    while i < n:
        j = template.find("{{", i)
        if j < 0:
            stack[-1][1].append(("text", template[i:]))
            break
        if j > i:
            stack[-1][1].append(("text", template[i:j]))
        if template.startswith("{{{", j):
            k = template.find("}}}", j + 3)
            if k < 0:
                raise TemplateError(f"unterminated tag at offset {j}")
            name = _name(template[j + 3:k], j)
            if name not in raw_allowed:
                raise TemplateError(f"{{{{{{{name}}}}}}} is not allowed: input is not marked x-openjev-raw")
            stack[-1][1].append(("raw", name))
            i = k + 3
            continue
        k = template.find("}}", j + 2)
        if k < 0:
            raise TemplateError(f"unterminated tag at offset {j}")
        body = template[j + 2:k]
        i = k + 2
        if body.startswith("#"):
            name = _name(body[1:], j)
            children: list[tuple] = []
            stack[-1][1].append(("sec", name, children))
            stack.append((name, children))
        elif body.startswith("/"):
            name = _name(body[1:], j)
            if len(stack) == 1 or stack[-1][0] != name:
                raise TemplateError(f"unbalanced section close {{{{/{name}}}}} at offset {j}")
            stack.pop()
        else:
            stack[-1][1].append(("var", _name(body, j)))
    if len(stack) > 1:
        raise TemplateError(f"unclosed section {{{{#{stack[-1][0]}}}}}")
    return root


def check_template(template: str, *, raw_allowed: frozenset[str]) -> None:
    _parse(template, raw_allowed)


def _escape(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = json.dumps(value, ensure_ascii=False)[1:-1]
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    return text.translate(LINE_BREAKS_TABLE)


def _lookup(ctx: list[Mapping[str, Any]], name: str) -> Any:
    for frame in reversed(ctx):
        if name in frame:
            return frame[name]
    return None


def _render(nodes: list[tuple], ctx: list[Mapping[str, Any]], out: list[str]) -> None:
    for node in nodes:
        kind = node[0]
        if kind == "text":
            out.append(node[1])
        elif kind == "var":
            out.append(_escape(_lookup(ctx, node[1])))
        elif kind == "raw":
            v = _lookup(ctx, node[1])
            out.append("" if v is None else str(v))
        else:
            v = _lookup(ctx, node[1])
            if isinstance(v, (list, tuple)):
                for item in v:
                    _render(node[2], ctx + [item if isinstance(item, Mapping) else {".": item}], out)
            elif isinstance(v, Mapping):
                if v:
                    _render(node[2], ctx + [v], out)
            elif v:
                _render(node[2], ctx, out)


def render(template: str, inputs: Mapping[str, Any], *, raw_allowed: frozenset[str]) -> str:
    out: list[str] = []
    _render(_parse(template, raw_allowed), [inputs], out)
    return "".join(out)
