"""Tool types. The registry and call_tool live in tools/dispatch.py."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openjev_mcp.config import Config

if TYPE_CHECKING:
    from openjev_mcp.audit import AuditLog
    from openjev_mcp.http import OpenJevClient
    from openjev_mcp.limits import LimitsCache
    from openjev_mcp.lint import Finding
    from openjev_mcp.progress import ProgressEmitter


@dataclass
class ToolContext:
    config: Config
    client: OpenJevClient
    limits: LimitsCache
    progress: ProgressEmitter
    audit: AuditLog | None
    warnings: list[Finding] = field(default_factory=list)   # filled by call_tool from ToolSpec.prepare
    links: list[dict] = field(default_factory=list)         # resource_link blocks (envelope.file_link); reset per call
    request_meta: dict = field(default_factory=dict)        # request _meta minus protocol keys (traceparent, ...)


Handler = Callable[[ToolContext, dict], Awaitable[dict]]   # returns structuredContent; raises ToolError


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_schema: dict
    output_schema: dict
    annotations: dict                      # wire names: readOnlyHint, idempotentHint, openWorldHint (+ title)
    handler: Handler
    prepare: Callable[[dict], tuple[dict, list[Finding]]] | None = None
    # runs BEFORE validate_args; returns (coerced args, findings) and may raise ToolError


class UnknownTool(LookupError):
    pass
