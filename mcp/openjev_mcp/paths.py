"""Allowed roots, read/write path rules and text decoding (spec 2.2 F2; Decision 2)."""
from __future__ import annotations

import codecs
import os
from pathlib import Path

from .config import Config
from .errors import ToolError

MiB = 1024 * 1024
READ_CAP = 64 * MiB
IMAGE_CAP = 20 * MiB
DATA_EXTS = frozenset({".jsonl", ".ndjson", ".jsonlines", ".json", ".csv", ".tsv", ".tab", ".txt", ".text", ".log", ".md"})
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
SHEET_EXTS = frozenset({".xlsx", ".xls", ".ods", ".numbers"})
BINARY_EXTS = frozenset({".pdf", ".docx", ".zip", ".gz", ".parquet", ".sqlite", ".db"})
WRITE_EXTS = frozenset({".jsonl", ".json", ".csv", ".md"})
OUTSIDE = "path outside the allowed roots"


def _bad(message: str, path, hint: str | None = None, code: str = "OJ_INVALID_INPUT") -> ToolError:
    return ToolError(code, message, path=str(path), hint=hint)


def _inside(real: str, root: str) -> bool:
    return real == root or real.startswith(root.rstrip(os.sep) + os.sep)


def within_roots(path: str | os.PathLike, config: Config) -> bool:
    real = os.path.realpath(path)
    return any(_inside(real, os.path.realpath(r)) for r in config.roots)


def _absolute(path: str, config: Config) -> str:
    if not isinstance(path, str) or not path.strip():
        raise _bad("path must be a non-empty string", path)
    if "\0" in path:
        raise _bad("path contains a NUL byte", path.replace("\0", ""))
    p = os.path.expanduser(path)
    if os.path.isabs(p):
        return p
    if config.transport == "stdio" and config.roots:
        return os.path.join(config.roots[0], p)
    raise _bad("relative path: pass an absolute path inside the allowed roots (the HTTP daemon's cwd is not your project)",
               path, "use an absolute path")


def _checked(path: str, config: Config) -> str:
    """realpath of an absolute-or-relative path, which must lie inside a root."""
    real = os.path.realpath(_absolute(path, config))
    if not within_roots(real, config):
        raise _bad(OUTSIDE, path, "allowed roots: " + ", ".join(config.roots))
    return real


def resolve_read(path: str, config: Config, kind: str = "data") -> Path:
    """Existing regular file inside a root with an extension allowed for `kind` ('data' | 'image')."""
    if kind not in ("data", "image"):
        raise ValueError(f"kind={kind!r}")
    real = _checked(path, config)
    ext = os.path.splitext(real)[1].lower()
    if ext in SHEET_EXTS:
        raise _bad(f"{ext} spreadsheets are not readable", path, "export the sheet as CSV first", "E030")
    if ext in BINARY_EXTS:
        raise _bad(f"{ext} binary files are not readable", path, "export it as CSV or JSONL", "E030")
    allowed = DATA_EXTS if kind == "data" else IMAGE_EXTS
    if ext not in allowed:
        raise _bad(f"extension {ext or '(none)'} is not readable here", path, "allowed: " + " ".join(sorted(allowed)))
    if not os.path.isfile(real):
        raise ToolError("OJ_NOT_FOUND", "file not found", path=str(path))
    size, cap = os.path.getsize(real), READ_CAP if kind == "data" else IMAGE_CAP
    if size > cap:
        raise _bad(f"file is {size} bytes; the cap is {cap // MiB} MiB", path, code="OJ_TOO_LARGE")
    return Path(real)


def resolve_write(path: str, config: Config, *, exts=WRITE_EXTS, new_only: bool = True) -> Path:
    """Target for a write: inside a root, allowed extension, no symlink, no dot-dir, no dotfile, existing parent;
    new_only refuses an existing file."""
    full = os.path.normpath(_absolute(path, config))
    parent, name = os.path.split(full)
    if name.startswith("."):
        raise _bad("writing to a dotfile is refused", path)
    ext = os.path.splitext(name)[1].lower()
    if ext not in exts:
        raise _bad(f"extension {ext or '(none)'} is not writable here", path, "allowed: " + " ".join(sorted(exts)))
    if os.path.islink(full):
        raise _bad("writing through a symlink is refused", path)
    real_parent = os.path.realpath(parent)
    if not within_roots(real_parent, config):
        raise _bad(OUTSIDE, path, "allowed roots: " + ", ".join(config.roots))
    if not os.path.isdir(real_parent):
        raise _bad("parent directory does not exist", path)
    root = next(os.path.realpath(r) for r in config.roots if _inside(real_parent, os.path.realpath(r)))
    if any(part.startswith(".") for part in Path(os.path.relpath(real_parent, root)).parts if part != "."):
        raise _bad("writing under a dot-directory is refused", path)
    target = os.path.join(real_parent, name)
    if new_only and os.path.lexists(target):
        raise _bad("file already exists; export files are created new only", path, "choose a new path")
    return Path(target)


def read_text(path: str | os.PathLike) -> tuple[str, str, list[str]]:
    """(text, encoding, warnings): BOM -> UTF-16/UTF-8-sig, strict UTF-8, else cp1252 with W603."""
    raw = Path(path).read_bytes()
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16"), "utf-16", []
    if raw.startswith(codecs.BOM_UTF8):
        return raw[3:].decode("utf-8"), "utf-8", []
    try:
        return raw.decode("utf-8"), "utf-8", []
    except UnicodeDecodeError:
        pass
    text = raw.decode("cp1252", errors="replace")  # cp1252 leaves 5 bytes undefined
    return text, "cp1252", ["W603: file is not valid UTF-8; decoded as Windows-1252"]
