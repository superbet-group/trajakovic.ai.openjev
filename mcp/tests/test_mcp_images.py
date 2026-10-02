"""Image loader and re-encoder (spec 2.12 steps 1-6): sources, decode, downscale, exact types, caps."""
from __future__ import annotations

import base64
import io
import os
import subprocess
import sys

import pytest
import stubs
from PIL import Image

from openjev_mcp import images
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.images import load_images

pytestmark = pytest.mark.anyio

HOTDOG = os.path.join(stubs.REPO, "tests", "data", "hotdog.jpg")


def cfg(root, **kw) -> Config:
    return Config(roots=(str(root),), transport="stdio", **kw)


def blob(size=(64, 32), fmt="PNG", mode="RGB", color=(200, 30, 30), **save) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, fmt, **save)
    return buf.getvalue()


def decode(data_url: str) -> Image.Image:
    head, b64 = data_url.split(",", 1)
    assert head in ("data:image/png;base64", "data:image/jpeg;base64"), head
    return Image.open(io.BytesIO(base64.b64decode(b64)))


async def fails(items, config, text, code="OJ_INVALID_INPUT", **kw) -> ToolError:
    with pytest.raises(ToolError) as e:
        await load_images(items, config, **kw)
    assert e.value.code == code and text in e.value.message, e.value
    return e.value


async def test_jpeg_passes_through_unchanged(tmp_path):
    raw = open(HOTDOG, "rb").read()
    [im] = await load_images([{"path": HOTDOG}], cfg(os.path.dirname(HOTDOG)))
    assert im.info() == {"source": HOTDOG, "sent_as": "image/jpeg", "bytes": 12860, "reencoded": False}
    assert im.data_url == "data:image/jpeg;base64," + base64.b64encode(raw).decode()


async def test_relative_path_resolves_against_first_root(tmp_path):
    (tmp_path / "a.png").write_bytes(blob())
    [im] = await load_images([{"path": "a.png"}], cfg(tmp_path))
    assert im.source == "a.png" and im.sent_as == "image/png" and not im.reencoded


async def test_downscale_respects_max_side_and_type(tmp_path):
    (tmp_path / "big.png").write_bytes(blob((3000, 1000)))
    [im] = await load_images([{"path": str(tmp_path / "big.png")}], cfg(tmp_path), max_side_px=500)
    out = decode(im.data_url)
    assert im.sent_as == "image/png" and im.reencoded and out.format == "PNG" and out.size == (500, 167)
    assert im.bytes == len(base64.b64decode(im.data_url.split(",", 1)[1]))


async def test_jpeg_downscale_stays_jpeg():
    [im] = await load_images([{"base64": base64.b64encode(blob((2000, 900), "JPEG")).decode(), "content_type": "image/jpeg"}],
                             Config(), max_side_px=1000)
    out = decode(im.data_url)
    assert im.sent_as == "image/jpeg" and out.format == "JPEG" and max(out.size) == 1000 and im.reencoded


@pytest.mark.parametrize("fmt", ["WEBP", "GIF", "BMP", "TIFF"])
async def test_other_formats_become_png(fmt):
    raw = blob((40, 30), fmt, mode="P" if fmt == "GIF" else "RGB", color=1 if fmt == "GIF" else (1, 2, 3))
    [im] = await load_images([{"data_url": f"data:image/{fmt.lower()};base64," + base64.b64encode(raw).decode()}], Config())
    assert im.sent_as == "image/png" and im.reencoded and decode(im.data_url).format == "PNG"


async def test_wrong_declared_type_is_corrected_without_reencoding():
    raw = blob((20, 20), "JPEG")
    [im] = await load_images([{"data_url": "data:image/JPG;base64," + base64.b64encode(raw).decode()}], Config())
    assert im.data_url.startswith("data:image/jpeg;base64,") and not im.reencoded and im.source == "data_url"


async def test_oversize_png_falls_back_to_jpeg_under_cap():
    noise = Image.frombytes("RGB", (1800, 1800), os.urandom(1800 * 1800 * 3))
    buf = io.BytesIO()
    noise.save(buf, "PNG")
    assert len(buf.getvalue()) > images.IMAGE_BYTES
    [im] = await load_images([{"base64": base64.b64encode(buf.getvalue()).decode(), "content_type": "image/png"}], Config(), max_side_px=2000)
    assert im.bytes <= images.IMAGE_BYTES and im.reencoded and im.sent_as == "image/jpeg"


async def test_exif_orientation_applied_on_reencode():
    im = Image.new("RGB", (400, 100), (9, 9, 9))
    exif = Image.Exif()
    exif[0x0112] = 6   # rotate 90 clockwise
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    [out] = await load_images([{"base64": base64.b64encode(buf.getvalue()).decode(), "content_type": "image/jpeg"}], Config(), max_side_px=200)
    assert decode(out.data_url).size == (50, 200)


@pytest.mark.parametrize("raw,text", [(b"not an image at all", "cannot be decoded"),
                                      (b"\x89PNG\r\n\x1a\ntruncated", "cannot be decoded"),
                                      (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "SVG"),
                                      (b"<?xml version='1.0'?><svg/>", "SVG"),
                                      (b"%PDF-1.7 ...", "PDF")])
async def test_undecodable_svg_pdf_refused(raw, text):
    e = await fails([{"base64": base64.b64encode(raw).decode(), "content_type": "image/png"}], Config(), text)
    assert e.path == "images.0"


async def test_source_shape_errors(tmp_path):
    c = cfg(tmp_path)
    await fails([], c, "1-8")
    await fails([{"data_url": "data:image/png;base64,AAAA"}] * 9, c, "1-8")
    await fails(["x.png"], c, "object")
    await fails([{}], c, "none of")
    await fails([{"path": "a.png", "url": "https://x.test/a.png"}], c, "path, url")
    await fails([{"base64": "AAAA"}], c, "content_type")
    await fails([{"data_url": "data:text/plain;base64,AAAA"}], c, "data:image")
    await fails([{"data_url": "data:image/png;base64,@@@"}], c, "base64")
    await fails([{"data_url": "data:image/png;base64,AAAA"}], c, "decoded")
    await fails([{"data_url": "data:image/png;base64,AAAA"}], c, "positive", max_side_px=0)


async def test_path_rules(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "out.png").write_bytes(blob())
    (root / "t.txt").write_text("x")
    (root / "t.pdf").write_bytes(b"%PDF")
    e = await fails([{"path": str(tmp_path / "out.png")}], cfg(root), "outside the allowed roots")
    assert e.path == "images.0.path"
    await fails([{"path": str(root / "t.txt")}], cfg(root), "not readable here")
    await fails([{"path": str(root / "t.pdf")}], cfg(root), "binary", code="E030")
    await fails([{"path": str(root / "gone.png")}], cfg(root), "not found", code="OJ_NOT_FOUND")
    (root / "link.png").symlink_to(tmp_path / "out.png")
    await fails([{"path": str(root / "link.png")}], cfg(root), "outside the allowed roots")
    await fails([{"path": "a.png"}], Config(roots=(str(root),), transport="http"), "relative path")


async def test_file_over_cap_refused_before_decode(tmp_path):
    p = tmp_path / "huge.png"
    with open(p, "wb") as f:
        f.truncate(20 * 1024 * 1024 + 1)
    await fails([{"path": str(p)}], cfg(tmp_path), "cap is 20 MiB", code="OJ_TOO_LARGE")


async def test_url_needs_fetch_on(tmp_path):
    e = await fails([{"url": "https://example.test/a.png"}], cfg(tmp_path), "fetch is off")
    assert e.path == "images.0"


async def test_url_goes_through_fetch(monkeypatch, tmp_path):
    calls = []

    async def fake(url, config):
        calls.append(url)
        return blob((10, 10))

    monkeypatch.setattr("openjev_mcp.fetch.fetch_image", fake)
    [im] = await load_images([{"url": "https://example.test/a.png"}], cfg(tmp_path, fetch=True))
    assert calls == ["https://example.test/a.png"] and im.source == "https://example.test/a.png" and im.sent_as == "image/png"


async def test_total_body_cap(monkeypatch):
    monkeypatch.setattr(images, "BODY_BYTES", 100)
    await fails([{"base64": base64.b64encode(blob()).decode(), "content_type": "image/png"}], Config(), "64 MiB")


def test_pillow_stays_lazy():
    code = "import sys, openjev_mcp.images, openjev_mcp.fetch; assert 'PIL' not in sys.modules"
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


async def test_missing_pillow_is_a_clear_error(monkeypatch):
    item = {"base64": base64.b64encode(blob()).decode(), "content_type": "image/png"}
    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.setitem(sys.modules, "PIL.Image", None)
    e = await fails([item], Config(), "Pillow")
    assert "openjev-mcp[images]" in e.hint
