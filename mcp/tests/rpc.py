"""Protocol fixtures (arch G.2): raw JSON-RPC over memory streams, the SDK client, the HTTP app."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
from mcp import Client
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from mcp_types import (CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY, JSONRPCError, JSONRPCNotification,
                       JSONRPCRequest, JSONRPCResponse)

from openjev_mcp.http_app import build_http_app

MODERN = "2026-07-28"
BASE_URL = "http://127.0.0.1:8100"
ACCEPT = "application/json, text/event-stream"


class RawRPC:
    def __init__(self, send):
        self._send = send
        self._next = 1
        self._done: dict[Any, anyio.Event] = {}
        self._responses: dict[Any, dict] = {}
        self.messages: list[dict] = []

    def feed(self, message) -> None:
        data = message.model_dump(by_alias=True, exclude_none=True, mode="json")
        self.messages.append(data)
        if isinstance(message, (JSONRPCResponse, JSONRPCError)):
            self._responses[data["id"]] = data
            self._done.setdefault(data["id"], anyio.Event()).set()

    async def start(self, method: str, params: dict | None = None, *, envelope: bool = True, id: Any = None) -> Any:
        """Send a request without waiting; returns its id (for cancellation tests)."""
        if id is None:
            id, self._next = self._next, self._next + 1
        params = dict(params or {})
        if envelope:
            meta = dict(params.get("_meta") or {})
            meta.setdefault(PROTOCOL_VERSION_META_KEY, MODERN)
            meta.setdefault(CLIENT_CAPABILITIES_META_KEY, {})
            params["_meta"] = meta
        self._done.setdefault(id, anyio.Event())
        await self._send(SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=id, method=method, params=params or None)))
        return id

    async def response(self, id: Any, timeout: float = 10) -> dict:
        with anyio.fail_after(timeout):
            await self._done.setdefault(id, anyio.Event()).wait()
        return self._responses[id]

    async def request(self, method: str, params: dict | None = None, *, envelope: bool = True,
                      id: Any = None) -> dict:
        return await self.response(await self.start(method, params, envelope=envelope, id=id))

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self._send(SessionMessage(JSONRPCNotification(jsonrpc="2.0", method=method, params=params)))


@asynccontextmanager
async def rpc_session(server):
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        rpc = RawRPC(client_write.send)

        async def pump() -> None:
            async for item in client_read:
                if not isinstance(item, Exception):
                    rpc.feed(item.message)

        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: server.run(*server_streams, server.create_initialization_options()))
            tg.start_soon(pump)
            try:
                yield rpc
            finally:
                tg.cancel_scope.cancel()


def mcp_client(server, mode: str) -> Client:
    return Client(server, mode=mode, cache=None)


def modern_headers(method: str, name: str | None = None, **extra: str) -> dict[str, str]:
    headers = {"accept": ACCEPT, "content-type": "application/json", "mcp-protocol-version": MODERN,
               "mcp-method": method}
    if name is not None:
        headers["mcp-name"] = name
    return {**headers, **extra}


def modern_body(method: str, params: dict | None = None, id: Any = 1) -> dict:
    params = dict(params or {})
    params["_meta"] = {PROTOCOL_VERSION_META_KEY: MODERN, CLIENT_CAPABILITIES_META_KEY: {}, **params.get("_meta", {})}
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params}


@asynccontextmanager
async def http_app_client(server, config):
    app = build_http_app(server, config)
    async with server.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client:
            yield client
