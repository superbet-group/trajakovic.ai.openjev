"""The MCP server process: low-level SDK Server, stdio and Streamable HTTP, CLI (arch D.19).

One of the two modules that import the mcp SDK (the other is http_app.py)."""
from __future__ import annotations

import argparse
import logging
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import anyio
import httpx
import mcp_types as types
from mcp.server.caching import CacheHint
from mcp.server.context import ServerRequestContext
from mcp.server.extension import Extension, compose_tool_call_handler
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from openjev_mcp import PROTOCOL_VERSIONS, SERVER_NAME, __version__
from openjev_mcp import completion, ext, prompts, resources
from openjev_mcp.audit import AuditLog
from openjev_mcp.config import Config, ConfigError, load_config
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.tools import ToolContext, UnknownTool
from openjev_mcp.tools.dispatch import call_tool, tools_for

INSTRUCTIONS = ("OpenJev answers typed questions about a state you send (yes/no, one of N, a scale) "
                "with calibrated probabilities instead of generated text. Call status if you are not "
                "sure the server is up. These tools only read and advise; they never execute anything.")

WARM_TIMEOUT_S = 2.0
log = logging.getLogger("openjev_mcp")


@dataclass
class AppState:
    config: Config
    client: OpenJevClient
    limits: LimitsCache
    audit: AuditLog | None


class BindError(OSError):
    pass


async def _warm(limits: LimitsCache) -> None:
    with anyio.move_on_after(WARM_TIMEOUT_S):
        try:
            await limits.refresh()
        except Exception as exc:
            log.debug("limits warm-up failed: %s", exc)


def _version_gated(binding):
    """Reject an extension method at a protocol version outside binding.protocol_versions (METHOD_NOT_FOUND)."""
    async def gated(ctx, params):
        if ctx.protocol_version not in binding.protocol_versions:
            raise MCPError(types.METHOD_NOT_FOUND, "Method not found", binding.method)
        return await binding.handler(ctx, params)
    return gated


def build_server(config: Config, *, client: OpenJevClient | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 warm_limits: bool = True, extensions: list[Extension] | None = None) -> Server[AppState]:
    if extensions is None:
        extensions = [e for e in (f(config) for f in ext.EXTENSIONS) if e is not None]

    @asynccontextmanager
    async def lifespan(_: Server[AppState]) -> AsyncIterator[AppState]:
        oj = client or OpenJevClient(config, transport=transport)
        limits = LimitsCache(oj)
        audit = AuditLog(config.log_path, log_states=config.log_states) if config.log_path else None
        try:
            async with anyio.create_task_group() as tg:
                if warm_limits:
                    tg.start_soon(_warm, limits)
                yield AppState(config, oj, limits, audit)
                tg.cancel_scope.cancel()
        finally:
            if client is None:
                await oj.aclose()

    def tool_context(ctx: ServerRequestContext[AppState]) -> ToolContext:
        app = ctx.lifespan_context
        meta = {k: v for k, v in (ctx.meta or {}).items()
                if k != "progressToken" and not k.startswith("io.modelcontextprotocol/")}
        return ToolContext(app.config, app.client, app.limits, ProgressEmitter(ctx.session.report_progress),
                           app.audit, request_meta=meta)

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[
            types.Tool(name=t.name, title=t.title, description=t.description, input_schema=t.input_schema,
                       output_schema=t.output_schema, annotations=types.ToolAnnotations.model_validate(t.annotations))
            for t in tools_for(config)])

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            result = await call_tool(tool_context(ctx), params.name, params.arguments)
        except UnknownTool:
            raise MCPError(types.INVALID_PARAMS, f"Unknown tool: {params.name}") from None
        return types.CallToolResult.model_validate(result)

    async def on_list_resources(ctx, params) -> types.ListResourcesResult:
        return types.ListResourcesResult(
            resources=[types.Resource.model_validate(r) for r in resources.listing()])

    async def on_list_resource_templates(ctx, params) -> types.ListResourceTemplatesResult:
        return types.ListResourceTemplatesResult(
            resource_templates=[types.ResourceTemplate.model_validate(t) for t in resources.templates_listing()])

    async def on_read_resource(ctx, params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
        try:
            result = await resources.read_resource(params.uri, tool_context(ctx))
        except resources.ResourceNotFound:
            raise MCPError(types.INVALID_PARAMS, "Resource not found", {"uri": params.uri}) from None
        return types.ReadResourceResult.model_validate(result)

    async def on_list_prompts(ctx, params) -> types.ListPromptsResult:
        return types.ListPromptsResult(prompts=[types.Prompt.model_validate(p) for p in prompts.listing()])

    async def on_get_prompt(ctx, params: types.GetPromptRequestParams) -> types.GetPromptResult:
        try:
            result = await prompts.get(params.name, params.arguments, tool_context(ctx))
        except prompts.PromptError as exc:
            raise MCPError(types.INVALID_PARAMS, str(exc)) from None
        return types.GetPromptResult.model_validate(result)

    async def on_completion(ctx, params: types.CompleteRequestParams) -> types.CompleteResult:
        ref = params.ref
        ref_type, name = (("ref/prompt", ref.name) if isinstance(ref, types.PromptReference)
                          else ("ref/resource", ref.uri))
        done = completion.complete(ref_type, name, params.argument.name, params.argument.value, tool_context(ctx))
        return types.CompleteResult(completion=types.Completion(
            values=done["values"], total=done["total"], has_more=done["hasMore"]))

    async def on_discover(ctx, params) -> types.DiscoverResult:
        return types.DiscoverResult(supported_versions=list(PROTOCOL_VERSIONS),
                                    capabilities=server.get_capabilities(protocol_version=ctx.protocol_version),
                                    instructions=INSTRUCTIONS)

    hour = CacheHint(3600000, "public")
    server: Server[AppState] = Server(
        SERVER_NAME, version=__version__, instructions=INSTRUCTIONS, lifespan=lifespan,
        cache_hints={"tools/list": hour, "resources/list": hour,
                     "resources/templates/list": hour, "prompts/list": hour, "server/discover": hour},
        on_list_tools=on_list_tools, on_call_tool=on_call_tool,
        on_list_resources=on_list_resources, on_list_resource_templates=on_list_resource_templates,
        on_read_resource=on_read_resource, on_list_prompts=on_list_prompts, on_get_prompt=on_get_prompt,
        on_completion=on_completion)
    server.add_request_handler("server/discover", types.RequestParams, on_discover)
    for e in extensions:   # SEP-2133: advertise, serve the extension's methods, wrap tools/call
        server.extensions[e.identifier] = e.settings()
        for b in e.methods():
            server.add_request_handler(b.method, b.params_type,
                                       _version_gated(b) if b.protocol_versions is not None else b.handler)
    if any(type(e).intercept_tool_call is not Extension.intercept_tool_call for e in extensions):
        server.add_request_handler("tools/call", types.CallToolRequestParams,
                                   compose_tool_call_handler(extensions, on_call_tool))
    return server


async def run_stdio(config: Config) -> None:
    server = build_server(config)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def _bind(host: str, port: int) -> socket.socket:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        family, _, _, _, addr = next((i for i in infos if i[0] == socket.AF_INET), infos[0])
        sock = socket.socket(family, socket.SOCK_STREAM)
    except OSError as exc:
        raise BindError(f"cannot resolve {host}:{port}: {exc}") from exc
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(addr)
        sock.set_inheritable(True)
    except OSError as exc:
        sock.close()
        raise BindError(f"cannot bind {host}:{port}: {exc.strerror or exc}. "
                        f"Try: OPENJEV_MCP_PORT={port + 1} mise run start") from exc
    return sock


async def run_http(config: Config) -> None:
    import uvicorn

    from openjev_mcp.http_app import build_http_app

    sock = _bind(config.host, config.port)
    app = build_http_app(build_server(config), config)
    srv = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port, log_level="warning",
                                        access_log=False, timeout_graceful_shutdown=5))
    await srv.serve(sockets=[sock])


def _logging(debug: bool) -> None:
    logging.basicConfig(stream=sys.stderr, format="openjev-mcp: %(levelname)s %(message)s",
                        level=logging.DEBUG if debug else logging.WARNING, force=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openjev-mcp", description="OpenJev MCP server (Streamable HTTP or stdio)")
    parser.add_argument("--transport", choices=("http", "stdio"), help="default: OPENJEV_MCP_TRANSPORT or http")
    parser.add_argument("--host", help="HTTP bind address (default: OPENJEV_MCP_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="HTTP port (default: OPENJEV_MCP_PORT or 8100)")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    args = parser.parse_args(argv)
    if args.version:
        print(f"{SERVER_NAME} {__version__}")
        return 0
    try:
        config = load_config(transport=args.transport, host=args.host, port=args.port)
    except ConfigError as exc:
        print(f"openjev-mcp: {exc}", file=sys.stderr)
        return 2
    _logging(config.debug)
    if config.transport == "http" and not config.is_loopback and not config.token:
        print(f"openjev-mcp: binding {config.host} needs OPENJEV_MCP_TOKEN", file=sys.stderr)
        return 2
    try:
        anyio.run(run_stdio if config.transport == "stdio" else run_http, config)
    except BindError as exc:
        print(f"openjev-mcp: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        pass
    return 0

