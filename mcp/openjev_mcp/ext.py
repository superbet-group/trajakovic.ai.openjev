"""Extension factories (SEP-2133): each returns an Extension or None when inactive. P19 registers Tasks."""
from __future__ import annotations

from collections.abc import Callable

from mcp.server.extension import Extension

from openjev_mcp.config import Config

from openjev_mcp import tasks

EXTENSIONS: list[Callable[[Config], Extension | None]] = [tasks.factory]
