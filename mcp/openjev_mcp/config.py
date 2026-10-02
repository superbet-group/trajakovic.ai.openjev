"""Configuration of the MCP process, read once from the environment (arch B.3)."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


class ConfigError(ValueError):
    """A bad setting; the message names the variable."""


@dataclass(frozen=True)
class Config:
    base_url: str = "http://127.0.0.1:8080"
    api_key: str = field(default="", repr=False)
    origin_secret: str = field(default="", repr=False)
    model: str = "openjev-latest"
    timeout_ms: int = 30000                           # 100..600000
    max_inflight: int = 1                             # >= 1
    max_inflight_batch: int = 4                       # 1..4
    retries: int = 2                                  # >= 0
    log_path: str | None = None
    log_states: bool = False
    toolsets: str = "all"                             # "all" | "core"
    band_no_at: float = 0.2
    band_yes_at: float = 0.8
    roots: tuple[str, ...] = ()                       # realpath(cwd) first, then OPENJEV_MCP_ROOTS
    transport: str = "http"                           # "http" | "stdio"
    host: str = "127.0.0.1"
    port: int = 8100
    token: str = field(default="", repr=False)
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    max_body_bytes: int = 4 * 1024 * 1024
    debug: bool = False
    recipes_dir: str | None = None                    # OPENJEV_MCP_RECIPES, realpath, optional
    routing: bool = True                              # OPENJEV_MCP_ROUTING on/off
    fetch: bool = False                               # OPENJEV_MCP_FETCH on/off (image URL fetch)
    tasks: bool = False                               # OPENJEV_MCP_TASKS on/off (Tasks extension)
    audit_dir: str = ""                               # OPENJEV_MCP_AUDIT_DIR, absolute
    chat_model: str = "diffusiongemma-26b"

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK


def _int(env, name, default, lo=None, hi=None):
    raw = env.get(name, "").strip()
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name}={raw!r} is not an integer") from None
    if lo is not None and value < lo or hi is not None and value > hi:
        bounds = f"{lo}..{hi}" if lo is not None and hi is not None else f">= {lo}" if lo is not None else f"<= {hi}"
        raise ConfigError(f"{name}={value} is out of range ({bounds})")
    return value


def _flag(env, name, default=False):
    raw = env.get(name, "").strip().lower()
    if raw == "":
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    raise ConfigError(f"{name}={env[name]!r} is not 0/1 or on/off")


def _choice(env, name, default, choices):
    raw = env.get(name, "").strip()
    if raw == "":
        return default
    if raw not in choices:
        raise ConfigError(f"{name}={raw!r} is not one of {', '.join(choices)}")
    return raw


def _csv(raw):
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _band(env):
    raw = env.get("OPENJEV_MCP_BAND", "").strip()
    if raw == "":
        return Config.band_no_at, Config.band_yes_at
    try:
        no_at, yes_at = (float(p) for p in raw.split(","))
    except ValueError:
        raise ConfigError(f"OPENJEV_MCP_BAND={raw!r} is not 'no_at,yes_at' (two numbers)") from None
    if not 0 <= no_at < yes_at <= 1:
        raise ConfigError(f"OPENJEV_MCP_BAND={raw!r} must satisfy 0 <= no_at < yes_at <= 1")
    return no_at, yes_at


def load_config(env: Mapping[str, str] | None = None, *, transport: str | None = None,
                host: str | None = None, port: int | None = None, cwd: str | None = None) -> Config:
    """Pure over its arguments (env defaults to os.environ, cwd to os.getcwd()). Keyword
    arguments win over env. max_inflight defaults to 1 on stdio and 4 on http unless
    OPENJEV_MCP_MAX_INFLIGHT is set. Never reads OPENJEV_MODEL or OPENJEV_URL."""
    env = os.environ if env is None else env
    transport = transport or _choice(env, "OPENJEV_MCP_TRANSPORT", "http", ("http", "stdio"))
    if transport not in ("http", "stdio"):
        raise ConfigError(f"transport={transport!r} is not one of http, stdio")
    host = host or env.get("OPENJEV_MCP_HOST", "").strip() or Config.host
    port = port if port is not None else _int(env, "OPENJEV_MCP_PORT", Config.port, 1, 65535)
    if not 1 <= port <= 65535:
        raise ConfigError(f"port={port} is out of range (1..65535)")
    no_at, yes_at = _band(env)
    cwd = os.path.realpath(cwd or os.getcwd())
    extra = tuple(os.path.realpath(p) for p in env.get("OPENJEV_MCP_ROOTS", "").split(":") if p.strip())
    recipes = env.get("OPENJEV_MCP_RECIPES", "").strip()
    if recipes and not os.path.isdir(os.path.realpath(recipes)):
        raise ConfigError(f"OPENJEV_MCP_RECIPES={recipes!r} is not a directory")
    audit = env.get("OPENJEV_MCP_AUDIT_DIR", "").strip() or "openjev-audits"
    return Config(
        base_url=(env.get("OPENJEV_BASE_URL", "").strip() or Config.base_url).rstrip("/"),
        api_key=env.get("OPENJEV_API_KEY", ""),
        origin_secret=env.get("OPENJEV_ORIGIN_SECRET", ""),
        model=env.get("OPENJEV_MCP_MODEL", "").strip() or Config.model,
        timeout_ms=_int(env, "OPENJEV_MCP_TIMEOUT_MS", Config.timeout_ms, 100, 600000),
        max_inflight=_int(env, "OPENJEV_MCP_MAX_INFLIGHT", 1 if transport == "stdio" else 4, 1),
        max_inflight_batch=_int(env, "OPENJEV_MCP_MAX_INFLIGHT_BATCH", Config.max_inflight_batch, 1, 4),
        retries=_int(env, "OPENJEV_MCP_RETRIES", Config.retries, 0),
        log_path=env.get("OPENJEV_MCP_LOG", "").strip() or None,
        log_states=_flag(env, "OPENJEV_MCP_LOG_STATES"),
        toolsets=_choice(env, "OPENJEV_MCP_TOOLSETS", "all", ("all", "core")),
        band_no_at=no_at,
        band_yes_at=yes_at,
        roots=(cwd, *extra),
        transport=transport,
        host=host,
        port=port,
        token=env.get("OPENJEV_MCP_TOKEN", ""),
        allowed_hosts=_csv(env.get("OPENJEV_MCP_ALLOWED_HOSTS", "")),
        allowed_origins=_csv(env.get("OPENJEV_MCP_ALLOWED_ORIGINS", "")),
        max_body_bytes=_int(env, "OPENJEV_MCP_MAX_BODY_BYTES", Config.max_body_bytes, 1),
        debug=_flag(env, "OPENJEV_MCP_DEBUG"),
        recipes_dir=os.path.realpath(recipes) if recipes else None,
        routing=_flag(env, "OPENJEV_MCP_ROUTING", True),
        fetch=_flag(env, "OPENJEV_MCP_FETCH"),
        tasks=_flag(env, "OPENJEV_MCP_TASKS"),
        audit_dir=os.path.normpath(os.path.join(cwd, audit)),
        chat_model=env.get("OPENJEV_MCP_CHAT_MODEL", "").strip() or Config.chat_model,
    )
