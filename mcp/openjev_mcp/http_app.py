"""Streamable HTTP app: Host/Origin protection, bearer token, /health (arch B.2, B.5, D.20).

One of the two modules that import the mcp SDK (the other is server.py)."""
from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from openjev_mcp import PROTOCOL_VERSIONS, SERVER_NAME, __version__
from openjev_mcp.config import Config

MCP_PATH = "/mcp"


def _host_pattern(host: str) -> str:
    return f"[{host}]:*" if ":" in host else f"{host}:*"


def security_settings(config: Config) -> TransportSecuritySettings:
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    if not config.is_loopback:
        hosts.append(_host_pattern(config.host))
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                     allowed_hosts=[*hosts, *config.allowed_hosts],
                                     allowed_origins=[*origins, *config.allowed_origins])


class BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str, path: str = MCP_PATH):
        self.app = app
        self.token = token.encode()
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return
        value = dict(scope["headers"]).get(b"authorization", b"")
        scheme, _, given = value.partition(b" ")
        if scheme.lower() == b"bearer" and hmac.compare_digest(given.strip(), self.token):
            await self.app(scope, receive, send)
            return
        response = JSONResponse({"error": "unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": 'Bearer realm="openjev-mcp"'})
        await response(scope, receive, send)


def _public_url(url: str) -> str:
    parts = urlsplit(url)
    host = f"[{parts.hostname}]" if parts.hostname and ":" in parts.hostname else parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return parts._replace(netloc=netloc, query="", fragment="").geturl()


async def health(request: Request) -> JSONResponse:
    config: Config = request.app.state.config
    return JSONResponse({"status": "ok", "name": SERVER_NAME, "version": __version__,
                         "transport": "streamable-http", "protocol_versions": list(PROTOCOL_VERSIONS),
                         "openjev_base_url": _public_url(config.base_url)})


def build_http_app(server: Server, config: Config) -> ASGIApp:
    app = server.streamable_http_app(
        streamable_http_path=MCP_PATH, stateless_http=True, json_response=False, host=config.host,
        max_request_body_size=config.max_body_bytes, transport_security=security_settings(config),
        custom_starlette_routes=[Route("/health", health, methods=["GET"])])
    app.state.config = config
    return BearerTokenMiddleware(app, config.token) if config.token else app
