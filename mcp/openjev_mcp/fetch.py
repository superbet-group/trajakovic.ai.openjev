"""SSRF-safe image download (spec 2.2 "Image URL fetch"). Only with OPENJEV_MCP_FETCH=on.

https only; the name is resolved once, every address must be public, and the connection goes to that address
(URL host = the IP, Host header and TLS name = the original host) so a second lookup cannot rebind it. Redirects
are followed by hand (max 3), each hop re-checked. 10 s in total, body aborted past 20 MiB, no cookies,
no credentials (OPENJEV_API_KEY is never read here)."""
from __future__ import annotations

import inspect
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin

import anyio
import httpx

from .config import Config
from .errors import ToolError, invalid_input

MAX_REDIRECTS = 3
TIMEOUT_S = 10.0
MAX_BYTES = 20 * 1024 * 1024
CGNAT = ipaddress.ip_network("100.64.0.0/10")
HINT = "use a public https image URL, or pass path / data_url / base64"
Resolver = Callable[[str], "Awaitable[list[str]] | list[str]"]


async def system_resolver(host: str) -> list[str]:
    infos = await anyio.to_thread.run_sync(lambda: socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))
    return list(dict.fromkeys(i[4][0].split("%")[0] for i in infos))


def refusal(addr: str) -> str | None:
    """Why an address is not allowed, or None for a public unicast address."""
    ip = ipaddress.ip_address(addr)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    for name, bad in (("loopback", ip.is_loopback), ("unspecified", ip.is_unspecified), ("multicast", ip.is_multicast),
                      ("link-local", ip.is_link_local), ("CGNAT", ip.version == 4 and ip in CGNAT),
                      ("private", ip.is_private), ("reserved", ip.is_reserved or not ip.is_global)):
        if bad:
            return f"{name} address {ip}"
    return None


async def _pick(host: str, resolver: Resolver) -> str:
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        res = resolver(host)
        try:
            addrs = list(await res if inspect.isawaitable(res) else res)
        except OSError:
            raise invalid_input("url", f"cannot resolve {host}", HINT) from None
    if not addrs:
        raise invalid_input("url", f"cannot resolve {host}", HINT)
    for a in addrs:   # every answer must be public: a mixed answer is refused, not filtered
        why = refusal(a)
        if why:
            raise invalid_input("url", f"URL refused: {host} resolves to a {why}", HINT)
    return addrs[0]


async def _get(client: httpx.AsyncClient, url: str, resolver: Resolver, hops: int) -> bytes:
    u = httpx.URL(url)
    if u.scheme != "https":
        raise invalid_input("url", "only https image URLs are fetched", HINT)
    host = u.host
    if not host or u.userinfo:
        raise invalid_input("url", "URL must name a host and carry no credentials", HINT)
    ip = await _pick(host, resolver)
    port = u.port or 443
    pinned = u.copy_with(host=ip, port=port, userinfo=b"")
    client.cookies.clear()   # a Set-Cookie from the previous hop must not ride along
    req = client.build_request("GET", pinned, headers={"Host": host if port == 443 else f"{host}:{port}",
                                                       "Accept": "image/*", "User-Agent": "openjev-mcp"},
                               extensions={"sni_hostname": host})
    try:
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as err:
        raise ToolError("OJ_UNREACHABLE", f"could not fetch the image: {type(err).__name__}", path="url", hint=HINT) from None
    try:
        if resp.is_redirect and resp.headers.get("location"):
            if hops >= MAX_REDIRECTS:
                raise invalid_input("url", f"more than {MAX_REDIRECTS} redirects", HINT)
            nxt = urljoin(url, resp.headers["location"])
            await resp.aclose()
            return await _get(client, nxt, resolver, hops + 1)
        if resp.status_code != 200:
            raise invalid_input("url", f"image URL answered HTTP {resp.status_code}", HINT)
        size = resp.headers.get("content-length")
        if size and size.isdigit() and int(size) > MAX_BYTES:
            raise ToolError("OJ_TOO_LARGE", f"image is {size} bytes; the cap is {MAX_BYTES // 1048576} MiB", path="url")
        chunks, n = [], 0
        async for chunk in resp.aiter_bytes():
            n += len(chunk)
            if n > MAX_BYTES:
                raise ToolError("OJ_TOO_LARGE", f"image is larger than {MAX_BYTES // 1048576} MiB; download aborted", path="url")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        await resp.aclose()


async def fetch_image(url: str, config: Config, *, resolver: Resolver | None = None,
                      transport: httpx.AsyncBaseTransport | None = None, timeout_s: float = TIMEOUT_S) -> bytes:
    """Bytes of the image at an https URL. `resolver` (name -> addresses) and `transport` (the inner httpx transport)
    exist for tests; the defaults are the system resolver and a plain AsyncHTTPTransport."""
    if not config.fetch:
        raise invalid_input("url", "image URL fetch is off", "set OPENJEV_MCP_FETCH=on, or pass path, data_url or base64")
    if not isinstance(url, str) or not url.strip():
        raise invalid_input("url", "url must be a non-empty string")
    try:
        async with httpx.AsyncClient(transport=transport or httpx.AsyncHTTPTransport(), follow_redirects=False,
                                     trust_env=False, timeout=timeout_s) as client:
            with anyio.fail_after(timeout_s):
                return await _get(client, url.strip(), resolver or system_resolver, 0)
    except TimeoutError:
        raise ToolError("OJ_TIMEOUT", f"image fetch took longer than {timeout_s:g} s", path="url", hint=HINT) from None
    except httpx.InvalidURL:
        raise invalid_input("url", "not a valid URL", HINT) from None
