"""Image loader and re-encoder shared by ask_image and batch.images (spec 2.12 HTTP mapping, steps 1-6).

path / url / data_url / base64 -> decoded with Pillow (imported lazily; the core install stays free of it) ->
downscaled to max_side_px -> png or jpeg with the exact lowercase type -> <= 5 MiB each, body < 64 MiB."""
from __future__ import annotations

import base64
import binascii
import io
import re
from dataclasses import dataclass
from typing import Any

import anyio

from . import fetch, paths
from .config import Config
from .errors import ToolError, invalid_input

MiB = 1024 * 1024
MAX_IMAGES = 8
IMAGE_BYTES = 5 * MiB          # 5,242,880 per image after re-encoding
BODY_BYTES = 64 * MiB
MAX_SIDE_PX = 1568
MAX_PIXELS = 100_000_000
DATA_URL = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,", re.IGNORECASE)
KEYS = ("path", "url", "data_url", "base64")
KEEP = {"PNG": "image/png", "JPEG": "image/jpeg"}   # sent unchanged when small enough
HINT_ONE = "give exactly one of path, url, data_url or base64+content_type"


@dataclass(frozen=True)
class LoadedImage:
    data_url: str
    source: str
    sent_as: str
    bytes: int
    reencoded: bool

    def info(self) -> dict:
        return {"source": self.source, "sent_as": self.sent_as, "bytes": self.bytes, "reencoded": self.reencoded}


def _pil():
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise ToolError("OJ_INVALID_INPUT", "images need Pillow, which is not installed",
                        hint="pip install 'openjev-mcp[images]'") from None
    return Image, ImageOps


def _bad(i: int, message: str, hint: str | None = None, code: str = "OJ_INVALID_INPUT") -> ToolError:
    return ToolError(code, message, path=f"images.{i}", hint=hint)


def _b64(i: int, text: str) -> bytes:
    if len(text) > paths.IMAGE_CAP * 4 // 3 + 8:
        raise _bad(i, f"image data is larger than the {paths.IMAGE_CAP // MiB} MiB cap", code="OJ_TOO_LARGE")
    try:
        return base64.b64decode(re.sub(r"\s+", "", text), validate=True)
    except (binascii.Error, ValueError):
        raise _bad(i, "image data is not valid base64", "send plain base64 without a data: prefix") from None


def _encode(im, fmt: str):
    buf = io.BytesIO()
    if fmt == "JPEG":
        im.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
    else:
        im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _process(i: int, raw: bytes, max_side: int) -> tuple[bytes, str, bool]:
    """(bytes to send, exact type, reencoded). Runs in a worker thread."""
    head = raw[:512].lstrip().lower()
    if head.startswith((b"<svg", b"<?xml")) or b"<svg" in head:
        raise _bad(i, "SVG images are not supported", "render it to PNG first")
    if raw.startswith(b"%PDF"):
        raise _bad(i, "PDF files are not images", "render the page to PNG first")
    Image, ImageOps = _pil()
    try:
        im = Image.open(io.BytesIO(raw))
        fmt = im.format or ""
        if im.size[0] * im.size[1] > MAX_PIXELS:
            raise _bad(i, f"image is {im.size[0]}x{im.size[1]} pixels; the cap is {MAX_PIXELS // 1_000_000} megapixels",
                       code="OJ_TOO_LARGE")
        im.load()
    except ToolError:
        raise
    except Exception:
        raise _bad(i, "image data cannot be decoded as an image", "send a PNG, JPEG, WebP or GIF file") from None
    w, h = im.size
    if fmt in KEEP and max(w, h) <= max_side and len(raw) <= IMAGE_BYTES:
        return raw, KEEP[fmt], False
    im = ImageOps.exif_transpose(im)
    if max(im.size) > max_side:
        scale = max_side / max(im.size)
        im = im.resize((max(1, round(im.size[0] * scale)), max(1, round(im.size[1] * scale))), Image.LANCZOS)
    jpeg = fmt == "JPEG"
    for _ in range(6):   # still over the cap: shrink 25% per round, png falls back to jpeg after the first miss
        out = _encode(im, "JPEG" if jpeg else "PNG")
        if len(out) <= IMAGE_BYTES:
            return out, "image/jpeg" if jpeg else "image/png", True
        jpeg = True
        im = im.resize((max(1, im.size[0] * 3 // 4), max(1, im.size[1] * 3 // 4)), Image.LANCZOS)
    raise _bad(i, "image is larger than 5,242,880 bytes after re-encoding", code="OJ_TOO_LARGE")


async def _read(i: int, item: Any, config: Config) -> tuple[bytes, str]:
    """(raw bytes, source label) of one item."""
    if not isinstance(item, dict):
        raise _bad(i, "an image is an object with path, url, data_url or base64+content_type", HINT_ONE)
    given = [k for k in KEYS if item.get(k) is not None]
    if given == ["base64"] and not isinstance(item.get("content_type"), str):
        raise _bad(i, "base64 needs content_type", "add content_type, e.g. image/png")
    if len(given) != 1:
        raise _bad(i, f"image has {', '.join(given) or 'none'} of path, url, data_url, base64", HINT_ONE)
    key = given[0]
    val = item[key]
    if not isinstance(val, str):
        raise _bad(i, f"{key} must be a string")
    if key == "path":
        try:
            real = paths.resolve_read(val, config, "image")
        except ToolError as err:
            err.path = f"images.{i}.path"
            raise
        return await anyio.to_thread.run_sync(real.read_bytes), val
    if key == "url":
        if not config.fetch:
            raise _bad(i, "image URL fetch is off", "set OPENJEV_MCP_FETCH=on, or pass path, data_url or base64")
        try:
            return await fetch.fetch_image(val, config), val
        except ToolError as err:
            err.path = f"images.{i}.url"
            raise
    if key == "data_url":
        m = DATA_URL.match(val)
        if not m:
            raise _bad(i, "data_url must be data:image/<type>;base64,<data>")
        return _b64(i, val[m.end():]), "data_url"
    return _b64(i, item["base64"]), "base64"


async def load_images(items: Any, config: Config, *, max_side_px: int = MAX_SIDE_PX) -> list[LoadedImage]:
    """1-8 items -> LoadedImage list in order. Any failure is raised before a request is made."""
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_IMAGES:
        raise invalid_input("images", f"images must be a list of 1-{MAX_IMAGES} items", f"send 1-{MAX_IMAGES} images")
    if not isinstance(max_side_px, int) or max_side_px < 1:
        raise invalid_input("max_side_px", "max_side_px must be a positive integer")
    out: list[LoadedImage] = []
    total = 0
    for i, item in enumerate(items):
        raw, source = await _read(i, item, config)
        if len(raw) > paths.IMAGE_CAP:
            raise _bad(i, f"image is {len(raw)} bytes; the cap is {paths.IMAGE_CAP // MiB} MiB before re-encoding",
                       code="OJ_TOO_LARGE")
        data, ctype, re_enc = await anyio.to_thread.run_sync(_process, i, raw, max_side_px)
        url = f"data:{ctype};base64,{base64.b64encode(data).decode()}"
        total += len(url)
        if total >= BODY_BYTES:
            raise invalid_input("images", "the images together are 64 MiB or more", "send fewer or smaller images")
        out.append(LoadedImage(url, source, ctype, len(data), re_enc))
    return out
