"""MCP resources (spec 2.18): schema, limits, recipes, templates, patterns, authoring guide, file:// batch outputs."""
from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlparse
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from openjev_mcp import library, schemas, wire
from openjev_mcp.batch import store
from openjev_mcp.errors import ToolError
from openjev_mcp.paths import resolve_read
from openjev_mcp.recipes import registry
from openjev_mcp.tools import ToolContext

BUILD_TIME = datetime.fromtimestamp(os.path.getmtime(schemas.__file__), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

SCHEMA_URI = "openjev://schema"
LIMITS_URI = "openjev://limits"
RECIPES_URI = "openjev://recipes"
TEMPLATES_URI = "openjev://templates"
PATTERNS_URI = "openjev://patterns"
GUIDE_URI = "openjev://guide/authoring"

RESOURCES: tuple[dict, ...] = (
    {"uri": SCHEMA_URI, "name": "schema", "title": "OpenJev common types", "mimeType": "application/schema+json",
     "description": "JSON Schema $defs shared by the tool results: State, Question, Answer, Meta, ToolError, "
                    "LintFinding, QuestionStats, BatchHeader, BatchRow.",
     "annotations": {"audience": ["assistant"], "priority": 0.6, "lastModified": BUILD_TIME}},
    {"uri": LIMITS_URI, "name": "limits", "title": "OpenJev limits", "mimeType": "application/json",
     "description": "Effective request limits, per-model capabilities, batch caps and limit_source.",
     "annotations": {"audience": ["assistant"], "priority": 0.8}},
    {"uri": RECIPES_URI, "name": "recipes", "title": "OpenJev recipes", "mimeType": "application/json",
     "description": "Recipe index: id, title, description, decisions and input summary. Read openjev://recipes/{id} for one.",
     "annotations": {"audience": ["assistant"], "priority": 0.5, "lastModified": BUILD_TIME}},
    {"uri": TEMPLATES_URI, "name": "templates", "title": "OpenJev batch templates", "mimeType": "application/json",
     "description": "Batch template index: id, title, description, question and state counts.",
     "annotations": {"audience": ["user", "assistant"], "priority": 0.5, "lastModified": BUILD_TIME}},
    {"uri": PATTERNS_URI, "name": "patterns", "title": "OpenJev question patterns", "mimeType": "application/json",
     "description": "The verified question-pattern library keyed <usage>.<question_id>, with measured values.",
     "annotations": {"audience": ["assistant"], "priority": 0.4, "lastModified": BUILD_TIME}},
    {"uri": GUIDE_URI, "name": "guide_authoring", "title": "Authoring guide", "mimeType": "text/markdown",
     "description": "How to write statements, questions and options that OpenJev answers well.",
     "annotations": {"audience": ["user", "assistant"], "priority": 0.4, "lastModified": BUILD_TIME}},
)
_STATIC = {RECIPES_URI: 3600000, TEMPLATES_URI: 3600000, PATTERNS_URI: 3600000, GUIDE_URI: 3600000}


def _tpl(uri: str, name: str, title: str, description: str, audience: list[str]) -> dict:
    return {"uriTemplate": uri, "name": name, "title": title, "mimeType": "application/json", "description": description,
            "annotations": {"audience": audience, "priority": 0.5, "lastModified": BUILD_TIME}}


TEMPLATES: list[dict] = [   # uriTemplate, name, title, mimeType, annotations
    _tpl("openjev://recipes/{id}", "recipe", "OpenJev recipe", "The full recipe document: inputs, questions, policy, limitations.",
         ["assistant"]),
    _tpl("openjev://templates/{id}", "template", "OpenJev batch template",
         "{id, title, questions, options, states, source_case}; usable directly as batch template or items.",
         ["user", "assistant"]),
]
READERS: list[tuple[re.Pattern, Callable[[str, ToolContext], Awaitable[dict]]]] = []   # consulted after static URIs


class ResourceNotFound(LookupError):
    pass


def listing() -> list[dict]:
    """RESOURCES for resources/list; identical on every call (the limits read time is in its body)."""
    return [{**r, "annotations": dict(r["annotations"])} for r in RESOURCES]


def templates_listing() -> list[dict]:
    from openjev_mcp import audit_store
    if not any(t["name"] == "audits" for t in TEMPLATES):
        TEMPLATES.append(audit_store.audits_template())
    return [{**t, **({"annotations": dict(t["annotations"])} if "annotations" in t else {})} for t in TEMPLATES]


async def read_resource(uri: str, ctx: ToolContext) -> dict:
    if uri == SCHEMA_URI:
        text, mime, ttl_ms, scope = wire.dumps_text(schemas.SCHEMA_RESOURCE), RESOURCES[0]["mimeType"], 3600000, "public"
    elif uri == LIMITS_URI:
        limits = await ctx.limits.get()
        text, mime, ttl_ms, scope = (wire.dumps_text(limits.resource(ctx.config)), RESOURCES[1]["mimeType"],
                                     60000, "private")
    elif uri in _STATIC:
        text, mime, ttl_ms, scope = _static_body(uri, ctx), next(r["mimeType"] for r in RESOURCES if r["uri"] == uri), 3600000, "public"
    else:
        for pattern, reader in READERS:
            if pattern.match(uri):
                return await reader(uri, ctx)
        raise ResourceNotFound(uri)
    return {"contents": [{"uri": uri, "mimeType": mime, "text": text}], "ttlMs": ttl_ms, "cacheScope": scope}


def _static_body(uri: str, ctx: ToolContext | None = None) -> str:
    if uri == RECIPES_URI:
        return wire.dumps_text(registry.load_all(ctx.config).index())
    if uri == TEMPLATES_URI:
        return wire.dumps_text(library.templates_index())
    if uri == PATTERNS_URI:
        return wire.dumps_text(library.patterns())
    return library.guide()


def _json(uri: str, body: object, ttl_ms: int = 3600000, scope: str = "public") -> dict:
    return {"contents": [{"uri": uri, "mimeType": "application/json", "text": wire.dumps_text(body)}],
            "ttlMs": ttl_ms, "cacheScope": scope}


async def _read_recipe(uri: str, ctx: ToolContext) -> dict:
    rid = uri[len(RECIPES_URI) + 1:]
    reg = registry.load_all(ctx.config)
    if rid not in reg.recipes:
        raise ResourceNotFound(uri)
    return _json(uri, reg.document(rid))


async def _read_template(uri: str, ctx: ToolContext) -> dict:
    tpl = library.template(uri[len(TEMPLATES_URI) + 1:])
    if tpl is None:
        raise ResourceNotFound(uri)
    return _json(uri, tpl)


async def _read_file(uri: str, ctx: ToolContext) -> dict:
    """A batch output written by this server: .jsonl/.ndjson inside the roots whose first line is the batch header."""
    path = unquote(urlparse(uri).path)
    if os.path.splitext(path)[1].lower() not in store.EXTS:
        raise ResourceNotFound(uri)
    try:
        real = resolve_read(path, ctx.config)
        data = real.read_bytes()
        header, _, _, _, good = store._scan(data)   # same corrupt-line rule as batch (Decision 3)
    except (ToolError, OSError):
        raise ResourceNotFound(uri) from None
    if header is None or header.get("openjev_mcp") != "batch":
        raise ResourceNotFound(uri)
    return {"contents": [{"uri": uri, "mimeType": "application/x-ndjson", "text": data[:good].decode("utf-8", "replace")}],
            "ttlMs": 0, "cacheScope": "private"}


READERS.extend([(re.compile(r"openjev://recipes/[^/]+$"), _read_recipe),
                (re.compile(r"openjev://templates/[^/]+$"), _read_template),
                (re.compile(r"file://"), _read_file)])


# deviation: audits template/reader wired lazily (audit_store imports ResourceNotFound from resources: import cycle)
async def _read_audit(uri: str, ctx: ToolContext) -> dict:
    from openjev_mcp import audit_store   # lazy: audit_store imports ResourceNotFound from this module
    return await audit_store.read_audit(uri, ctx)


READERS.append((re.compile(r"openjev://audits/.+$"), _read_audit))
