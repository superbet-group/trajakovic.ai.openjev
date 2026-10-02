"""SSRF-safe image fetch (spec 2.2): fake resolver and transport only, never the real network."""
from __future__ import annotations

import anyio
import httpx
import pytest

from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.fetch import MAX_BYTES, fetch_image, refusal

pytestmark = pytest.mark.anyio

ON = Config(fetch=True, api_key="sk-secret")
PUBLIC = "93.184.216.34"
PAYLOAD = b"\x89PNG-bytes"


class Net:
    """Fake DNS plus a recording transport. `routes` maps request path -> response factory."""

    def __init__(self, dns=None, routes=None):
        self.dns, self.routes, self.lookups, self.requests = dns or {}, routes or {}, [], []
        self.transport = httpx.MockTransport(self.handle)

    async def resolve(self, host):
        self.lookups.append(host)
        return self.dns.get(host, [PUBLIC])

    def handle(self, request):
        self.requests.append(request)
        route = self.routes.get(request.url.path)
        return route(request) if route else httpx.Response(200, content=PAYLOAD)

    async def get(self, url, **kw):
        return await fetch_image(url, ON, resolver=self.resolve, transport=self.transport, **kw)


def redirect(to, status=302, **headers):
    return lambda request: httpx.Response(status, headers={"location": to, **headers})


async def refused(net, url, text="refused", code="OJ_INVALID_INPUT", **kw):
    with pytest.raises(ToolError) as e:
        await net.get(url, **kw)
    assert e.value.code == code and text in e.value.message, e.value
    return e.value


async def test_off_by_default():
    net = Net()
    with pytest.raises(ToolError) as e:
        await fetch_image("https://img.test/a.png", Config(), resolver=net.resolve, transport=net.transport)
    assert e.value.code == "OJ_INVALID_INPUT" and "fetch is off" in e.value.message
    assert not net.requests and not net.lookups


async def test_success_connects_to_the_resolved_address_only():
    net = Net()
    assert await net.get("https://img.test/a.png?x=1") == PAYLOAD
    assert net.lookups == ["img.test"]
    [req] = net.requests
    assert req.url.host == PUBLIC and req.url.scheme == "https" and req.url.path == "/a.png" and req.url.query == b"x=1"
    assert req.headers["host"] == "img.test" and req.extensions["sni_hostname"] == "img.test"
    assert not {"authorization", "cookie", "x-api-key"} & set(req.headers) and "sk-secret" not in str(req.headers)


async def test_port_kept_in_host_header():
    net = Net()
    await net.get("https://img.test:8443/a.png")
    assert net.requests[0].url.port == 8443 and net.requests[0].headers["host"] == "img.test:8443"


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/a.png", "https://[::1]/a.png", "https://169.254.169.254/latest/meta-data/",
    "https://10.1.2.3/a.png", "https://192.168.0.5/a.png", "https://172.16.9.9/a.png", "https://100.64.0.1/a.png",
    "https://[fc00::1]/a.png", "https://[fd12:3456::1]/a.png", "https://[fe80::1]/a.png", "https://224.0.0.1/a.png",
    "https://[ff02::1]/a.png", "https://0.0.0.0/a.png", "https://[::]/a.png", "https://[::ffff:127.0.0.1]/a.png",
    "https://[::ffff:169.254.169.254]/a.png"])
async def test_private_literals_refused_before_any_request(url):
    net = Net()
    await refused(net, url, "URL refused")
    assert not net.requests


@pytest.mark.parametrize("addrs", [["10.0.0.7"], ["127.0.0.1"], ["::1"], ["169.254.169.254"], ["100.100.100.100"],
                                   [PUBLIC, "192.168.1.1"], ["fd00::5"]])
async def test_name_resolving_to_private_refused(addrs):
    net = Net(dns={"evil.test": addrs})
    e = await refused(net, "https://evil.test/a.png", "resolves to a")
    assert not net.requests and e.path == "url"


async def test_unresolvable_and_empty():
    async def boom(host):
        raise OSError("nxdomain")
    with pytest.raises(ToolError) as e:
        await fetch_image("https://nx.test/a.png", ON, resolver=boom, transport=Net().transport)
    assert "cannot resolve" in e.value.message
    await refused(Net(dns={"e.test": []}), "https://e.test/a.png", "cannot resolve")


@pytest.mark.parametrize("url", ["http://img.test/a.png", "ftp://img.test/a.png", "file:///etc/passwd", "https://u:p@img.test/a.png", "https:///a.png"])
async def test_scheme_and_userinfo(url):
    net = Net()
    with pytest.raises(ToolError) as e:
        await net.get(url)
    assert e.value.code == "OJ_INVALID_INPUT" and not net.requests


async def test_redirect_to_private_address_refused():
    for target in ("https://169.254.169.254/latest", "https://[::1]/x", "https://internal.test/x"):
        net = Net(dns={"internal.test": ["10.0.0.9"]}, routes={"/a.png": redirect(target)})
        await refused(net, "https://img.test/a.png", "URL refused")
        assert len(net.requests) == 1   # the second hop never connected


async def test_redirect_to_http_refused():
    net = Net(routes={"/a.png": redirect("http://img.test/b.png")})
    await refused(net, "https://img.test/a.png", "only https")
    assert len(net.requests) == 1


async def test_redirects_are_followed_and_rechecked_up_to_three():
    routes = {"/a": redirect("/b"), "/b": redirect("https://cdn.test/c"), "/c": redirect("/d")}
    net = Net(routes=routes)
    assert await net.get("https://img.test/a") == PAYLOAD
    assert net.lookups == ["img.test", "img.test", "cdn.test", "cdn.test"]   # resolved again on every hop
    assert [r.url.path for r in net.requests] == ["/a", "/b", "/c", "/d"]
    assert net.requests[2].url.host == PUBLIC and net.requests[2].headers["host"] == "cdn.test"


async def test_fourth_redirect_refused():
    net = Net(routes={f"/{c}": redirect(f"/{chr(ord(c) + 1)}") for c in "abcde"})
    await refused(net, "https://img.test/a", "more than 3 redirects")
    assert len(net.requests) == 4


async def test_dns_rebinding_second_lookup_is_never_used():
    answers = iter([[PUBLIC], ["127.0.0.1"]])

    async def flip(host):
        return next(answers)
    net = Net()
    assert await fetch_image("https://img.test/a.png", ON, resolver=flip, transport=net.transport) == PAYLOAD
    assert net.requests[0].url.host == PUBLIC and net.requests[0].url.host != "127.0.0.1"
    assert next(answers) == ["127.0.0.1"]   # the second answer was never consumed


async def test_no_cookies_across_hops():
    net = Net(routes={"/a": redirect("/b", **{"set-cookie": "sid=1; Path=/"})})
    await net.get("https://img.test/a")
    assert all("cookie" not in r.headers for r in net.requests)


async def test_non_200_and_connect_errors():
    net = Net(routes={"/a": lambda r: httpx.Response(404)})
    await refused(net, "https://img.test/a", "HTTP 404")

    def down(request):
        raise httpx.ConnectError("no route")
    net = Net(routes={"/a": down})
    e = await refused(net, "https://img.test/a", "could not fetch", code="OJ_UNREACHABLE")
    assert e.path == "url"


class Chunks(httpx.AsyncByteStream):
    def __init__(self, n, size=1024 * 1024):
        self.n, self.size, self.sent = n, size, 0

    async def __aiter__(self):
        for _ in range(self.n):
            self.sent += 1
            yield b"x" * self.size


async def test_oversize_stream_aborts_past_20_mib():
    body = Chunks(500)   # would be 500 MiB
    net = Net(routes={"/a": lambda r: httpx.Response(200, stream=body)})
    await refused(net, "https://img.test/a", "download aborted", code="OJ_TOO_LARGE")
    assert body.sent == MAX_BYTES // body.size + 1   # stopped at the first chunk past the cap


async def test_oversize_content_length_refused_without_reading():
    body = Chunks(1)
    net = Net(routes={"/a": lambda r: httpx.Response(200, headers={"content-length": str(MAX_BYTES + 1)}, stream=body)})
    await refused(net, "https://img.test/a", "cap is 20 MiB", code="OJ_TOO_LARGE")
    assert body.sent == 0


async def test_exactly_at_the_cap_is_allowed():
    net = Net(routes={"/a": lambda r: httpx.Response(200, stream=Chunks(20))})
    assert len(await net.get("https://img.test/a")) == MAX_BYTES


class Slow(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"x"
        await anyio.sleep(30)
        yield b"y"


async def test_slow_body_times_out():
    net = Net(routes={"/a": lambda r: httpx.Response(200, stream=Slow())})
    with anyio.fail_after(5):
        await refused(net, "https://img.test/a", "longer than 0.3 s", code="OJ_TIMEOUT", timeout_s=0.3)


async def test_slow_resolver_counts_against_the_total():
    async def slow(host):
        await anyio.sleep(30)
    with pytest.raises(ToolError) as e, anyio.fail_after(5):
        await fetch_image("https://img.test/a", ON, resolver=slow, transport=Net().transport, timeout_s=0.2)
    assert e.value.code == "OJ_TIMEOUT"


def test_refusal_table():
    assert refusal(PUBLIC) is None and refusal("2606:4700:4700::1111") is None
    assert "loopback" in refusal("127.0.0.1") and "link-local" in refusal("169.254.169.254")
    assert "CGNAT" in refusal("100.64.0.1") and "multicast" in refusal("239.1.1.1")
    assert "unspecified" in refusal("0.0.0.0") and "private" in refusal("fc00::1")
