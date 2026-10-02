# openjev-mcp: architecture and interface contract

Status: phase 1 build plan, 2026-10-02. Source of truth for behaviour is
`docs/mcp-skill-spec/OPENJEV_MCP_SKILLS_SPEC.md` v1.2 (cited as "spec 2.x") and `TASKS.md` 1.1-1.32.
This document fixes what the spec leaves to the implementation: the SDK, the process and transport
model, the module layout, every module's public interface, and the mise integration. Where this
document deliberately deviates from the spec it says so in a "Deviation" line; `mcp/spec.md` carries
those deviations forward.

Contents:

- A. Protocol revision and SDK decision (with evidence)
- B. Process model, transports, port, configuration, security
- C. Repository layout of `mcp/`
- D. Interface contract (every module)
- E. Data flow of a tool call, error mapping, result envelope
- F. mise integration
- G. Testing strategy
- H. Non-goals
- I. Work packages for parallel implementation

---

## A. Protocol revision and SDK decision

### A.1 Decision

- Target **MCP 2026-07-28** (stateless requests, `server/discover`, per-request `_meta`,
  `resultType`, `ttlMs`/`cacheScope`) as the primary revision, and serve the legacy `initialize`
  handshake for **2025-11-25** and **2025-06-18** (spec 2.0.1 rule 1).
- SDK: the official Python SDK **`mcp>=2.2,<3`** (2.2.0 is the newest release on PyPI today; it pins
  `mcp-types==2.2.0`). Pin in `mcp/pyproject.toml`: `"mcp>=2.2,<3"`.
- Use the **low-level server** `mcp.server.lowlevel.Server` only. Never `mcp.server.mcpserver.MCPServer`
  (the FastMCP-style layer): it validates tool arguments against `inputSchema` itself and would turn
  argument errors into protocol errors, which breaks F12 (spec 2.4, TASKS 1.29).

### A.2 Evidence

`pip index versions mcp` (2026-10-02): `2.2.0, 2.1.1, 2.1.0, 2.0.1, 2.0.0, 1.30.0, ...`.
`mcp==2.2.0` was installed in a scratch venv and its source read (`mcp_types/version.py`,
`mcp/server/runner.py`, `mcp/server/lowlevel/server.py`, `mcp/server/streamable_http_manager.py`,
`mcp/server/_streamable_http_modern.py`, `mcp/server/transport_security.py`, `mcp/server/caching.py`,
`mcp/shared/jsonrpc_dispatcher.py`, `mcp/client/client.py`). Facts:

- `mcp_types.version`: `KNOWN_PROTOCOL_VERSIONS = (2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25,
  2026-07-28)`, `HANDSHAKE_PROTOCOL_VERSIONS` = the first four, `MODERN_PROTOCOL_VERSIONS =
  ("2026-07-28",)`. Typed surfaces exist for both eras (`mcp_types/_v2025_11_25`, `_v2026_07_28`).
- `Server.run()` drives `serve_dual_era_loop`: the client's first request decides the era of a
  stream connection (stdio). An enveloped request opens a 2026-07-28 connection; anything else opens
  a legacy one.
- `Server.streamable_http_app(stateless_http=True)` routes on the `MCP-Protocol-Version` header: a
  modern value goes to the single-exchange 2026-07-28 entry (`handle_modern_request`), a handshake
  value or none to the legacy stateless transport (no `Mcp-Session-Id`).
- `Server(cache_hints={method: CacheHint(ttl_ms, scope)})` fills `ttlMs`/`cacheScope`; a handler's
  explicit values win.
- The low-level `Server` validates only the `CallToolRequestParams` shape (`name`, `arguments` is an
  object), never the arguments against `inputSchema`.

Probes run against a low-level `Server` with one tool (raw JSON-RPC over
`mcp.shared.memory.create_client_server_memory_streams` + `server.run`, and HTTP through
`httpx.ASGITransport` inside `server.session_manager.run()`). Observed outputs:

| Probe | Observed |
|---|---|
| modern `server/discover` with our override handler | `supportedVersions: ["2026-07-28","2025-11-25","2025-06-18"]`, `capabilities.tools.listChanged: false`, `instructions`, `ttlMs: 3600000`, `cacheScope: "public"`, `resultType: "complete"`, `_meta["io.modelcontextprotocol/serverInfo"]` |
| modern `tools/list` | `inputSchema`/`outputSchema` keep `$schema` and `additionalProperties`; `ttlMs`/`cacheScope` from cache hints; `resultType`; serverInfo `_meta` |
| modern request without `clientCapabilities` | `-32602 "params._meta is missing the required envelope key(s): io.modelcontextprotocol/clientCapabilities"` |
| modern request with `protocolVersion: "2099-01-01"` | `-32022 "Unsupported protocol version"`, `data: {"supported": ["2026-07-28"], "requested": "2099-01-01"}` |
| first request without any envelope (stdio) | legacy era, init gate: `-32602 "Invalid request parameters"` |
| `notifications/cancelled` during a slow `tools/call` | two progress notifications, then nothing: no result, no error |
| `progressToken` in `_meta`, handler calls `ctx.session.report_progress` | `notifications/progress {progressToken, progress, total, message}` |
| same handler, request without `progressToken` | only the result; `report_progress` is a no-op |
| `types.CallToolResult.model_validate` on a camelCase dict (`structuredContent`, `isError`) | validates (`structured_content`, `is_error` set) |
| `mcp_types.methods.CACHEABLE_METHODS` | `prompts/list, resources/list, resources/read, resources/templates/list, server/discover, tools/list` |
| legacy `initialize` 2025-06-18 | `protocolVersion: "2025-06-18"`; later `tools/list` has no `ttlMs`/`resultType`/serverInfo `_meta` (the 2025 sieve strips them) |
| legacy `initialize` 2024-11-05 | accepted, `protocolVersion: "2024-11-05"` |
| legacy `initialize` 1999-01-01 | counter-offer `protocolVersion: "2025-11-25"` |
| HTTP modern, no `Mcp-Method` header | `400`, `-32020 "mcp-method header does not match the request body's method"` |
| HTTP modern `tools/call`, wrong `Mcp-Name` | `400`, `-32020 "mcp-name header does not match ..."` |
| HTTP modern, `Origin: https://evil.example` | `403 Invalid Origin header` |
| HTTP modern, `Host: evil.example:8100` | `421 Invalid Host header` |
| HTTP legacy `initialize`, then `tools/list` with `MCP-Protocol-Version: 2025-06-18` | both `200`, `text/event-stream`, no `Mcp-Session-Id` |
| HTTP `GET /mcp` with a modern header | `405` |
| tool handler returns `isError` / does not validate args | arguments the schema would reject reach our handler (F12 holds by construction) |

The modern SSE path already sends `x-accel-buffering: no`, `cache-control: no-cache, no-transform`
and `: ping` keep-alive comments every 15 s (`_SSE_HEADERS`, `_SSE_PING_INTERVAL`), as the spec's
Streamable HTTP clause asks.

Dependency check against the project `.venv` (`pip install --dry-run mcp==2.2.0 google-re2 jsonschema`):
installs only `mcp`, `mcp-types`, `sse-starlette`, `PyJWT`, `cryptography`, `jsonschema*`,
`referencing`, `rpds-py`, `attrs`, `google-re2`; no upgrade of `fastapi`/`starlette` (1.7.0)/
`pydantic` (2.13.5)/`uvicorn` (0.54.0). The SDK uses `httpx2` (import name `httpx2`), which coexists
with the `httpx` 0.28 that OpenJev and `openjev_mcp` use.

### A.3 SDK versus spec: observed mismatches and decisions

1. **-32022 `data.supported` lists only `["2026-07-28"]`.** It is built in `mcp.shared.inbound`
   before any handler or middleware runs, so it cannot be changed without forking the SDK.
   Decision: accept. That list names the revisions usable *with the per-request envelope*;
   `server/discover` (our handler) lists all three. Tests assert exactly this.
2. **Legacy `initialize` with an unknown version counter-offers `2025-11-25`** instead of -32022.
   This is the 2025 negotiation rule; the -32022 rule of spec 2.0.1 applies to the envelope path.
   Decision: accept.
3. **The legacy handshake also serves 2024-11-05 and 2025-03-26.** A middleware that rewrites
   `initialize` params desynchronises the SDK's commit (`runner.py` TODO). Decision: accept; only
   2025-06-18 and 2025-11-25 are tested and supported, older revisions are served best-effort.
4. **`resultType` and the serverInfo `_meta` stamp appear only on 2026-era results**, and `ttlMs`/
   `cacheScope` are stripped on legacy results. Correct per era. Spec 2.0.1 rules 2 and 5 are
   tested on modern requests only.

### A.4 What we implement ourselves anyway (would also be the fallback)

The SDK provides everything 2026-07-28 needs, so there is no protocol fallback to build. These parts
are ours regardless, and would stay ours if a later SDK changed:

- `server/discover` handler returning the three-version list (registered with
  `server.add_request_handler("server/discover", types.RequestParams, handler)`).
- Argument validation (`jsonschema` Draft 2020-12) mapped to `isError` `OJ_INVALID_INPUT`.
- The result envelope (structured content + identical text block + annotations).
- Progress monotonicity and the 1-per-second limit (the SDK sends whatever we report).
- A catch-all in the tool dispatcher: an uncaught handler exception would otherwise become a
  JSON-RPC error; we turn it into `isError` `OJ_INTERNAL`.
- Release of semaphores in `finally` on cancellation (the SDK cancels the handler's scope).
- The HTTP bearer-token middleware and the `/health` route.

If a future SDK drops the dual-era loop, the fallback is a hand-written JSON-RPC loop over
`mcp_types` models that applies the same ladder (envelope check, version check, dispatch); it is
not built in phase 1.

---

## B. Process model, transports, configuration, security

### B.1 Processes

Three independent processes, each with its own port, pid file and log:

| Process | Command (spawned by mise) | Port (env) | Talks to |
|---|---|---|---|
| OpenJev API | `.venv/bin/python -m openjev` | 8080 (`OPENJEV_PORT`) | the model |
| UI | `.venv/bin/python ui/server.py` | 8090 (`UI_PORT`) | OpenJev over HTTP (`OPENJEV_URL`) |
| **MCP server** | `.venv/bin/python -m openjev_mcp --transport http` | **8100 (`OPENJEV_MCP_PORT`)** | OpenJev over HTTP (`OPENJEV_BASE_URL`) |

`openjev_mcp` never imports `openjev` (spec 2.0) and never imports `transformers`. It does not
depend on OpenJev being up: it starts in under a second, `/health` answers immediately, and tools
return `OJ_UNREACHABLE` until OpenJev listens. `openjev-hook` is a fourth, short-lived process per
hook call; it does not go through the MCP server.

### B.2 Transports

- **Streamable HTTP (primary)**, single endpoint `POST /mcp`, bound to `127.0.0.1:8100`. Built with
  `server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=False,
  host=config.host, max_request_body_size=config.max_body_bytes, transport_security=<B.5>,
  custom_starlette_routes=[Route("/health", ...)])` and served by `uvicorn`. Stateless: no
  `Mcp-Session-Id` is ever issued, in either era. `json_response=False` because progress needs SSE;
  the SDK still answers with `application/json` when the handler finishes before emitting anything.
  Clients must send `Accept: application/json, text/event-stream` (406 otherwise) and, on 2026-07-28,
  `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` for `tools/call`/`resources/read`.
- **stdio (secondary)**, `openjev-mcp --transport stdio`: `mcp.server.stdio.stdio_server()` +
  `server.run(...)`. stdout carries JSON-RPC only; all diagnostics go to stderr (spec 2.0.1 rule 10).
- Default `--transport` is `http` (`OPENJEV_MCP_TRANSPORT` overrides). Deviation: the spec's
  registration example `{"command": "openjev-mcp"}` must now say `"args": ["--transport", "stdio"]`.
- `GET /health` (not MCP): `200 {"status": "ok", "name": "openjev-mcp", "version": "1.2.0",
  "transport": "streamable-http", "protocol_versions": [...], "openjev_base_url": "<url>"}`. No
  OpenJev call, no auth, no secrets. mise readiness waits on it.

Claude Code registration (documented in `mcp/README.md`):

```json
{"mcpServers": {"openjev": {"type": "http", "url": "http://127.0.0.1:8100/mcp"}}}
```

or `claude mcp add --transport http openjev http://127.0.0.1:8100/mcp` (add
`--header "Authorization: Bearer $OPENJEV_MCP_TOKEN"` when a token is set). stdio:
`{"mcpServers": {"openjev": {"command": "<repo>/.venv/bin/openjev-mcp", "args": ["--transport", "stdio"], "env": {"OPENJEV_BASE_URL": "http://127.0.0.1:8080"}}}}`.

### B.3 Configuration (environment of the MCP process)

Read once by `config.load_config()`. Phase-1 names only; phase-2/3 names of spec 2.1
(`OPENJEV_MCP_RECIPES`, `_ROUTING`, `_FETCH`, `_TASKS`, `_AUDIT_DIR`, `_CHAT_MODEL`,
`_MAX_INFLIGHT_BATCH` beyond reporting it, `_CACHE_TTL_S`) are not read in phase 1. The MCP package
never reads `OPENJEV_MODEL` (spec F7) and never reads the UI's `OPENJEV_URL`.

| Variable | Default | Meaning |
|---|---|---|
| `OPENJEV_BASE_URL` | `http://127.0.0.1:8080` | OpenJev server (shared name with `run_cases.py`) |
| `OPENJEV_API_KEY` | unset | sent to OpenJev as `Authorization: Bearer <key>` (shared with the server) |
| `OPENJEV_ORIGIN_SECRET` | unset | sent as `X-Origin-Secret` when set (same name the UI uses) |
| `OPENJEV_MCP_MODEL` | `openjev-latest` | default decide model |
| `OPENJEV_MCP_TIMEOUT_MS` | `30000` | base per-request timeout (scaled, spec 2.4) |
| `OPENJEV_MCP_MAX_INFLIGHT` | `1` with stdio, `4` with http | concurrent OpenJev requests from this process |
| `OPENJEV_MCP_MAX_INFLIGHT_BATCH` | `4` | reported in `status.mcp` and `openjev://limits` only (phase 2 uses it) |
| `OPENJEV_MCP_RETRIES` | `2` | retries for retryable codes (spec 2.4) |
| `OPENJEV_MCP_LOG` | unset | JSONL audit log path (spec 2.1 principle 7) |
| `OPENJEV_MCP_LOG_STATES` | `0` | `1` includes raw states in the audit log |
| `OPENJEV_MCP_TOOLSETS` | `all` | `all` or `core`; identical in phase 1 (spec 2.0) |
| `OPENJEV_MCP_BAND` | `0.2,0.8` | default noul `no_at,yes_at` |
| `OPENJEV_MCP_ROOTS` | unset | `:`-separated extra roots (parsed and reported; no phase-1 tool reads files) |
| `OPENJEV_MCP_TRANSPORT` | `http` | `http` or `stdio` (the `--transport` flag wins) |
| `OPENJEV_MCP_HOST` | `127.0.0.1` | HTTP bind address (`--host` wins) |
| `OPENJEV_MCP_PORT` | `8100` | HTTP port (`--port` wins) |
| `OPENJEV_MCP_TOKEN` | unset | bearer token clients must send to `/mcp` (B.5) |
| `OPENJEV_MCP_ALLOWED_HOSTS` | unset | extra `Host` values, comma-separated, `host:port` or `host:*` |
| `OPENJEV_MCP_ALLOWED_ORIGINS` | unset | extra `Origin` values, comma-separated |
| `OPENJEV_MCP_MAX_BODY_BYTES` | `4194304` | max HTTP request body to `/mcp` (SDK 413 past it) |
| `OPENJEV_MCP_DEBUG` | `0` | `1` sets the stderr log level to DEBUG |

`OPENJEV_MCP_MAX_INFLIGHT` default: the spec's `1` assumes one stdio process per Claude session. One
HTTP daemon serves every session on the machine, so `1` would make one session's `think` read block
every other session. Deviation: `4` under http. OpenJev's own admission control (529) stays the
global back-pressure; MLX serialises reads anyway.

### B.4 Lifecycle

- `main()` parses args, loads config, configures `logging` to **stderr** (format
  `openjev-mcp: %(levelname)s %(message)s`, level `WARNING`, `OPENJEV_MCP_DEBUG=1` -> `DEBUG`), then
  runs the transport. Exit codes: `0` clean stop, `2` configuration error (bad env, non-loopback
  without token), `3` bind failure (message names the port and the hint
  `OPENJEV_MCP_PORT=<port+1> mise run start`).
- The server lifespan creates one `OpenJevClient` and one `LimitsCache` per process and closes them
  on shutdown. It starts a best-effort limits warm-up (`GET /v1/models`, `GET /v1/limits`, 2 s
  timeout, errors ignored) in the background; `build_server(..., warm_limits=False)` disables it
  (tests).
- SIGTERM/SIGINT: uvicorn's default handling; in-flight requests are cancelled at shutdown.

### B.5 Security

- **Loopback bind by default.** `--host`/`OPENJEV_MCP_HOST` other than `127.0.0.1`, `localhost`,
  `::1` requires `OPENJEV_MCP_TOKEN`; without one `main()` exits 2 ("binding <host> needs
  OPENJEV_MCP_TOKEN").
- **Host and Origin validation** (DNS rebinding, browser CSRF): always pass an explicit
  `TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=[...],
  allowed_origins=[...])`: hosts `127.0.0.1:*`, `localhost:*`, `[::1]:*`, plus `<host>:*` for a
  non-loopback bind, plus `OPENJEV_MCP_ALLOWED_HOSTS`; origins `http://127.0.0.1:*`,
  `http://localhost:*`, `http://[::1]:*` plus `OPENJEV_MCP_ALLOWED_ORIGINS`. Bad Origin -> 403, bad
  Host -> 421 (SDK). A request without `Origin` (every non-browser client) passes.
- **Bearer token** (`OPENJEV_MCP_TOKEN`): a pure-ASGI middleware (`http_app.BearerTokenMiddleware`)
  in front of `/mcp` only. Missing or wrong `Authorization: Bearer <token>` -> `401`,
  `WWW-Authenticate: Bearer realm="openjev-mcp"`, body `{"error": "unauthorized"}`; compared with
  `hmac.compare_digest` on bytes (as `openjev/api.py` does). Deviation: spec 2.0 says Streamable HTTP
  "require[s] a bearer token"; on a loopback bind the token is optional, because the Host/Origin
  checks stop browser and rebinding attacks and any local process that could reach the port could
  also read a token file of the same user. Recorded in `mcp/spec.md` as a deliberate choice.
- **Credential separation.** The inbound MCP token is never forwarded to OpenJev; OpenJev
  credentials (`OPENJEV_API_KEY`, `OPENJEV_ORIGIN_SECRET`) come only from the environment and are
  never logged, never put in results, and never inlined in `lint` `emit` snippets (curl uses
  `$OPENJEV_API_KEY`, Python `os.environ["OPENJEV_API_KEY"]`). `Config.__repr__` hides them.
- No OAuth, no RFC 9728 metadata (H). No server-initiated requests (the SDK denies them on modern
  connections; we never call sampling, elicitation or `roots/list` on any era).

---

## C. Repository layout

Rules that are easy to break:

- **Never create `mcp/__init__.py`.** `mcp/` is a plain directory. A regular package there would
  shadow the SDK (`import mcp`) for anything that has the repo root on `sys.path`; as a namespace
  portion it loses to the installed regular package `mcp`.
- **No `__init__.py` in `mcp/tests/`.** The root `tests/` has none either; pytest runs with
  `--import-mode=importlib` (set in `mcp/pyproject.toml`) so same-named modules do not clash.
  Test file names are prefixed `test_mcp_` anyway.
- `openjev_mcp/__init__.py` is import-light: constants only, no SDK, no httpx.
- Only `server.py` and `http_app.py` import the `mcp` SDK. `hook.py` must import neither the SDK
  nor `jsonschema` (300 ms budget, spec 2.20); a test asserts `"mcp" not in sys.modules` after
  `import openjev_mcp.hook`.
- `google-re2` imports as `re2`. Python's `re` is never used for recipe patterns.

```
mcp/
  pyproject.toml                  distribution openjev-mcp 1.2.0; package openjev_mcp; scripts; extras; pytest config
  README.md                       registration (.mcp.json, claude mcp add), env vars, privacy note, hook setup (TASKS 1.24)
  spec.md                         MCP server spec for future upgrades: normative summary, deviations, roadmap (phase 2/3)
  docs/
    architecture.md               this document
  openjev_mcp/
    __init__.py                   __version__, SPEC_VERSION, PROTOCOL_VERSIONS, SERVER_NAME
    __main__.py                   `python -m openjev_mcp` -> server.main()
    config.py                     Config, load_config, ConfigError
    errors.py                     ToolError, error codes, invalid_input()
    wire.py                       body serialisation, hashing, text-block JSON, request body builder
    mapping.py                    (status, error_type) -> ToolError (spec 2.4), transport faults
    http.py                       OpenJevClient: auth headers, in-flight cap, timeouts, retries
    limits.py                     defaults, capability matrix, /v1/limits parsing, LimitsCache
    schemas.py                    $defs of 2.2, inliner, per-tool input/output schemas, openjev://schema
    validate.py                   argument validation -> OJ_INVALID_INPUT; output validation (tests)
    derive.py                     derived fields (spec 2.3), chunks estimate
    lint.py                       linter (spec 2.13): E/W codes, autofix, estimate, emit snippets
    envelope.py                   success/error CallToolResult dicts
    progress.py                   ProgressEmitter (monotonic, <= 1/s)
    audit.py                      optional JSONL audit log
    resources.py                  openjev://schema, openjev://limits
    tools/
      __init__.py                 ToolSpec, ToolContext, TOOLS (fixed order), call_tool()
      read.py                     ask, yes_no, classify, score, shared run_read()
      lint_tool.py                lint tool handler
      status.py                   status tool handler
    server.py                     build_server(), main() CLI, stdio and http runners  [imports mcp SDK]
    http_app.py                   build_http_app(), BearerTokenMiddleware, /health      [imports mcp SDK]
    recipes/
      __init__.py                 re-exports load_recipe, load_builtin, run_recipe
      expr.py                     combine/when grammar: tokenizer, recursive-descent parser, evaluator
      rules.py                    RE2 deterministic rules, pattern checks, segment decision
      shell.py                    command split (segments for rules, parts for reads)
      template.py                 {{name}} / {{#list}} / {{{raw}}} renderer with JSON-string escaping
      engine.py                   Recipe model, loader, runner (deterministic, compute, read steps)
      builtin/
        command_gate.json         spec 2.18 + 5.3, questions verbatim (both profiles)
    claude_hooks.py               Claude Code PreToolUse input/output mapping (the only such module)
    hook.py                       `openjev-hook` CLI (phase 1: pretooluse)
    cli.py                        `openjev` CLI (phase 1: version only)
  skills/
    openjev-decisions/SKILL.md            spec 4.1, phase-1 rows only (TASKS 1.22)
    openjev-question-authoring/SKILL.md   spec 4.2, without compile (TASKS 1.23)
  tests/
    conftest.py                   fixtures of section G
    stubs.py                      StubEngine, openjev_app(), replay and fault transports, raw RPC session
    test_mcp_config.py
    test_mcp_wire.py
    test_mcp_schemas.py
    test_mcp_mapping.py           2.4 error matrix (incl. "Errors added in 1.2")
    test_mcp_http_client.py       retries, retry-after, timeouts, in-flight cap, cancellation
    test_mcp_limits.py
    test_mcp_derive.py
    test_mcp_lint.py              positive + negative fixture per code; 2.13 example; emit
    test_mcp_tools_replay.py      section-2 replays from captured.json
    test_mcp_tools_read.py        ask/yes_no/classify/score behaviour (6.7 rows)
    test_mcp_status.py
    test_mcp_resources.py
    test_mcp_protocol.py          both eras: discover, _meta, -32602/-32022, caching, schema walk, statelessness
    test_mcp_http_transport.py    /mcp over ASGI: headers, -32020, Origin/Host, token, /health; one uvicorn smoke
    test_mcp_cancel_progress.py
    test_mcp_stdio.py             subprocess: stdout carries JSON-RPC only
    test_mcp_expr.py
    test_mcp_rules.py
    test_mcp_shell.py
    test_mcp_template.py
    test_mcp_command_gate.py      rule tests of 6.6, replay of ex-gate-deny / ex-gate-allow
    test_mcp_hook.py              hook I/O, fail-closed, overhead p95, import isolation
    test_mcp_skills.py            every tool a skill names exists in phase 1
    test_mcp_packaging.py         no transformers in requirements; entry points resolve
```

`mcp/pyproject.toml`:

```toml
[project]
name = "openjev-mcp"
version = "1.2.0"
description = "MCP server, hook CLI and skills for OpenJev typed reads"
requires-python = ">=3.10"
license = "Apache-2.0"
dependencies = ["mcp>=2.2,<3", "httpx>=0.27", "jsonschema>=4.18", "google-re2>=1.1", "uvicorn>=0.31", "anyio>=4.9"]

[project.optional-dependencies]
test = ["pytest"]          # the contract tests also need the server package: pip install -e . (repo root)

[project.scripts]
openjev-mcp = "openjev_mcp.server:main"
openjev-hook = "openjev_mcp.hook:main"
openjev = "openjev_mcp.cli:main"

[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["openjev_mcp*"]

[tool.setuptools.package-data]
openjev_mcp = ["recipes/builtin/*.json"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--import-mode=importlib"
```

Root `.gitignore` gains `.openjev-mcp.log` and `.openjev-mcp.pid`.

---

## D. Interface contract

Conventions for every module:

- Python 3.10 syntax, `from __future__ import annotations`. Async code uses `anyio` primitives
  (`anyio.Semaphore`, `anyio.sleep`, `anyio.fail_after`), because the SDK cancels handlers through
  anyio cancel scopes.
- "Raises" lists the only exceptions a caller must handle. `ToolError` is the single failure type
  that crosses module boundaries inside a tool call.
- "Pure" means no I/O and no clock reads (except where a `clock` is injected).
- JSON objects are plain `dict`/`list`; key order matters where stated (insertion order is kept).

### D.1 `openjev_mcp/__init__.py`

```python
__version__ = "1.2.0"
SPEC_VERSION = "1.2"
SERVER_NAME = "openjev-mcp"
PROTOCOL_VERSIONS = ("2026-07-28", "2025-11-25", "2025-06-18")   # server/discover order
```

### D.2 `config.py`

```python
class ConfigError(ValueError): ...   # message names the variable, e.g. "OPENJEV_MCP_PORT='x' is not an integer"

@dataclass(frozen=True)
class Config:
    base_url: str = "http://127.0.0.1:8080"          # rstrip("/")
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

    @property
    def is_loopback(self) -> bool: ...                # host in {"127.0.0.1", "localhost", "::1"}

def load_config(env: Mapping[str, str] | None = None, *, transport: str | None = None,
                host: str | None = None, port: int | None = None, cwd: str | None = None) -> Config:
    """Pure over its arguments (env defaults to os.environ, cwd to os.getcwd()).
    Explicit keyword arguments win over env. max_inflight default depends on the final transport
    (1 stdio, 4 http) unless OPENJEV_MCP_MAX_INFLIGHT is set. Raises ConfigError."""
```

Never reads `OPENJEV_MODEL` or `OPENJEV_URL`.

### D.3 `errors.py`

```python
CODES = frozenset({"OJ_INVALID_INPUT", "OJ_VALIDATION", "OJ_REJECTED", "OJ_TOO_LONG", "OJ_UNKNOWN_MODEL",
    "OJ_BAD_TYPE", "OJ_AUTH", "OJ_FORBIDDEN", "OJ_NOT_FOUND", "OJ_TOO_LARGE", "OJ_RATE_LIMITED",
    "OJ_UNAVAILABLE", "OJ_OVERLOADED", "OJ_BAD_IMAGE", "OJ_SERVER", "OJ_UNREACHABLE", "OJ_TIMEOUT",
    "OJ_PROTOCOL", "OJ_INTERNAL"})
RETRYABLE = frozenset({"OJ_RATE_LIMITED", "OJ_UNAVAILABLE", "OJ_OVERLOADED", "OJ_UNREACHABLE"})
RETRY_ONCE = frozenset({"OJ_SERVER", "OJ_TIMEOUT"})

_UNSET = object()

@dataclass(eq=False)
class ToolError(Exception):
    code: str
    message: str
    http_status: int | None = None
    hint: str | None = None
    path: str | None = None
    retryable: bool = False
    retry_after_s: float | None = None
    request_id: str | None = None
    server_detail: Any = _UNSET

    def to_dict(self) -> dict:
        """Wire ToolError (spec 2.2). Always: code, message, http_status, path, retryable,
        retry_after_s, request_id (nullable ones as null). hint only when not None.
        server_detail only when set (may be any JSON value, including None)."""

def invalid_input(path: str | None, message: str, hint: str | None = None) -> ToolError:
    """OJ_INVALID_INPUT, retryable False."""
```

`OJ_INTERNAL` is new (not in spec 2.4): an unexpected exception inside `openjev_mcp`; message
`"internal error in openjev-mcp: <ExceptionType>"`, retryable False, details only on stderr.

### D.4 `wire.py`

The one place body bytes, hashes and the text-block JSON are produced. Every module that sends,
hashes or prints a body uses these functions.

```python
OPTION_ORDER = ("steps", "samples", "think", "sequential")

def build_body(model: str, state: Any, questions: dict, options: Mapping[str, Any] | None = None,
               images: list | None = None) -> dict:
    """Key order: model, then the present options in OPTION_ORDER (timeout_ms is never a body field,
    None values are dropped), then state, questions, then images when given. Pure. This order
    reproduces the spec's verified bodies (ex-yes-no: model, samples, state, questions)."""

def body_bytes(body: Mapping[str, Any]) -> bytes:
    """json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"). Pure."""

def body_hash(raw: bytes) -> str:
    """'sha256:' + hexdigest. Pure."""

def dumps_text(obj: Any) -> str:
    """Text-block JSON: json.dumps(obj, separators=(",", ":"), ensure_ascii=False). The text block of
    every result is dumps_text(structuredContent); tests compare json.loads(text) == structured."""

def canonical_hash(obj: Any) -> str:
    """'sha256:' + sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)).
    question_hash and state_hash in audit records. Pure."""
```

The HTTP client sends `content=body_bytes(body)` with `content-type: application/json`, never
`json=` (httpx's default separators differ, which would break `body_hashes`).

### D.5 `mapping.py`

```python
def map_http_error(status: int, headers: Mapping[str, str], content: bytes, *, base_url: str,
                   model: str | None, has_images: bool, timeout_ms: int,
                   known_models: Sequence[str] | None = None) -> ToolError:
    """A non-2xx OpenJev response -> ToolError (spec 2.4). Pure. Never raises.
    Classification order:
      1. parse JSON; detail = body["detail"] (dict with error_type/message, list (422), or str);
         chat errors use body["error"] {code,type,message}.
      2. by (status, error_type) first: 401 authentication_error -> OJ_AUTH; 403 authentication_error
         -> OJ_AUTH (no key); 403 permission_error -> OJ_FORBIDDEN; 400 api_usage_error "Unknown model: X"
         -> OJ_UNKNOWN_MODEL (hint lists known_models when given); 400 api_usage_error "Invalid request."
         -> OJ_BAD_TYPE; 400 api_usage_error "the model rejected this request: ..." -> OJ_REJECTED;
         413 -> OJ_TOO_LARGE (limit parsed from "larger than <N> bytes"); 529 overloaded_error ->
         OJ_OVERLOADED; 503 -> OJ_UNAVAILABLE (retry-after default 2); 429 -> OJ_RATE_LIMITED.
      3. then by shape: 422 list -> OJ_VALIDATION, path from loc ("body","questions","sev","score",
         "criteria") -> "questions.sev.criteria" (drop "body" and the discriminator tag); 400 str
         detail matched against the 2.4 message templates in table order ("the request is N tokens;
         the limit is L" -> OJ_TOO_LONG with N and L parsed; "Choice question must have at least
         one choice: <id>", "Too many choices", "Too many score levels", "at most N questions",
         images, "think needs a text state", "<model> does not support <field>", "Too many choices
         for <model>", "label tokens", "answer template is N tokens") -> OJ_REJECTED with that row's
         hint; any other str detail -> OJ_REJECTED, server_detail verbatim; 404 -> OJ_NOT_FOUND
         ("OPENJEV_BASE_URL points at something that is not OpenJev (<url>)"), chat model_not_found ->
         OJ_UNKNOWN_MODEL; 405 -> OJ_NOT_FOUND; 500 text/plain -> OJ_BAD_IMAGE if has_images else
         OJ_SERVER.
    Always fills http_status, request_id (x-request-id), retryable (errors.RETRYABLE/RETRY_ONCE),
    retry_after_s (parse_retry_after), server_detail (the parsed detail, trimmed to 2 KiB of JSON)."""

def map_transport_error(exc: BaseException, *, base_url: str, timeout_ms: int) -> ToolError:
    """httpx.TimeoutException -> OJ_TIMEOUT ("no answer in <timeout_ms> ms; ..."); httpx.ConnectError,
    httpx.ConnectTimeout, DNS failures -> OJ_UNREACHABLE ("no OpenJev at <base_url>. Start it (not from
    an agent): mise run start, or fix OPENJEV_BASE_URL"); other httpx.HTTPError -> OJ_UNREACHABLE.
    Pure. Never raises."""

def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """retry-after seconds (int or float); HTTP-date is ignored (None). Pure."""

def parse_server_timing(value: str | None) -> dict[str, float] | None:
    """'model;dur=0.0, server;dur=1886.3, total;dur=1886.3' -> {"model_ms": 0.0, "server_ms": 1886.3,
    "total_ms": 1886.3}; None when absent or unparsable. Pure."""
```

Hints and messages follow the 2.4 table text; `<base_url>`, `<model>`, `<N>`, `<L>` are substituted.

### D.6 `http.py`

```python
@dataclass(frozen=True)
class HttpResult:
    status: int
    data: Any                         # parsed JSON body (None if not JSON)
    headers: Mapping[str, str]        # lower-case keys
    request_id: str | None            # x-request-id
    body_hash: str | None             # wire.body_hash of the bytes sent (POST only)
    latency_ms: float                 # wall time of the successful attempt
    server_timing: dict[str, float] | None
    attempts: int                     # 1 + retries performed
    retried: dict | None              # {"status": int|None, "code": str, "attempts": int} when attempts > 1

class OpenJevClient:
    def __init__(self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None,
                 max_inflight: int | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
                 jitter: Callable[[], float] = lambda: random.uniform(0, 0.25)): ...
        # one httpx.AsyncClient(base_url=config.base_url, transport=transport, timeout=None),
        # headers: authorization "Bearer <api_key>" when set, x-origin-secret when set,
        # user-agent "openjev-mcp/<version>". In-flight cap: anyio.Semaphore(max_inflight or config.max_inflight).

    async def aclose(self) -> None: ...
    async def __aenter__(self) -> OpenJevClient: ...
    async def __aexit__(self, *exc) -> None: ...

    async def systemone(self, body: dict, *, timeout_ms: int, think: int = 0, has_images: bool = False,
                        deadline_ms: int | None = None, known_models: Sequence[str] | None = None) -> HttpResult:
        """POST /v1/systemone with content=wire.body_bytes(body). 2xx -> HttpResult; otherwise
        raises ToolError from mapping.map_http_error / map_transport_error after the retry policy:
        RETRYABLE codes up to config.retries extra attempts; RETRY_ONCE codes 1 extra attempt;
        OJ_TIMEOUT is never retried when think > 0; never a 4xx other than 429. Delay = retry_after_s
        (default 1.0) + jitter(). deadline_ms bounds all attempts and sleeps together (hooks):
        no attempt starts once it would exceed the deadline, and each attempt's timeout is
        min(timeout_ms, remaining). The semaphore is held per attempt, not during the sleep.
        Cancellation propagates (anyio cancelled exception) and releases the semaphore."""

    async def get(self, path: str, *, timeout_ms: int = 5000, ok: tuple[int, ...] = (200,),
                  retry: bool = False) -> HttpResult:
        """GET <path> (e.g. "/health", "/v1/models", "/v1/limits"). A status in ok -> HttpResult
        (so status can accept 404 for /v1/limits); otherwise raises ToolError (mapping).
        retry=False: one attempt. Always sends the configured auth headers (status never probes
        without the key)."""
```

### D.7 `limits.py`

```python
DEFAULT_LIMITS: dict = {"questions": 256, "choice_options": 255, "score_levels": 10, "images": 8,
                        "image_bytes": 5242880, "prompt_tokens": None, "body_bytes": 67108864}
MLX_PROMPT_TOKENS = 32768
VLLM_PROMPT_TOKENS = 65536
CHAT_MODELS = frozenset({"diffusiongemma-26b"})
ALIASES_ACCEPTED = ("jev-latest", "jev-preview")
CAPABILITY_MATRIX: dict[str, dict]   # spec 2.2 table; keys images, steps, samples, think, sequential,
                                     # max_prompt_tokens, max_choices. "openjev-0.1" max_prompt_tokens is
                                     # None while the backend is unknown, 32768 mlx, 65536 vllm.
BATCH_CAPS = {"concurrency_max": 4, "max_items_per_call": 100, "max_items": 5000}

@dataclass(frozen=True)
class Limits:
    source: str                          # "server" | "default"
    backend: str                         # vllm|mlx|laya|verdict|clm|jevk5|"unknown"
    values: dict                         # DEFAULT_LIMITS shape
    models: dict[str, dict]              # per-model capabilities (CAPABILITY_MATRIX shape)
    known_models: tuple[str, ...] | None # names from GET /v1/models, when read
    logs_bodies: bool | None
    read_at: float                       # time.time() of the read (0.0 for defaults)
    warnings: tuple[str, ...]

    def capabilities(self, model: str) -> dict:
        """Resolve openjev-latest/jev-latest/jev-preview to openjev-0.1; known model -> its row;
        unknown -> every option True, caps None (the server 400 is mapped instead). Pure."""

    def resource(self, config: Config) -> dict:
        """openjev://limits payload: {"limit_source", "backend", "limits", "capabilities"
        (decide models only), "batch": {**BATCH_CAPS, "max_inflight_batch": config.max_inflight_batch},
        "logs_bodies", "read_at" (ISO 8601 or null), "warnings"}. Pure."""

LIMITS_404_WARNING = ("GET /v1/limits not available (404): limits are the documented defaults, "
                      "limit-dependent lint findings are warnings, backend unknown")

def default_limits(known_models: Sequence[str] | None = None) -> Limits: ...   # pure
def parse_limits(payload: dict, *, known_models: Sequence[str] | None = None, now: float) -> Limits:
    """00-api-surface 15.1 shape -> Limits(source="server"). Raises ValueError on a malformed payload."""

class LimitsCache:
    def __init__(self, client: OpenJevClient, *, ttl_s: float = 60.0, clock=time.monotonic): ...
    def peek(self) -> Limits:
        """Cached Limits, or default_limits(); no I/O. Used by lint and the read tools."""
    async def refresh(self) -> Limits:
        """GET /v1/models then GET /v1/limits (ok=(200, 404)); 404 -> default + LIMITS_404_WARNING;
        200 -> parse_limits. Stores and returns. Raises ToolError only for /v1/models failures that
        status must report (OJ_UNREACHABLE, OJ_AUTH, ...); /v1/limits errors other than 404 degrade to
        defaults with a warning."""
    async def get(self) -> Limits:
        """refresh() when older than ttl_s, else cached; never raises (falls back to peek())."""
```

### D.8 `schemas.py`

```python
SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
DEFS: dict                 # spec 2.2 $defs verbatim (State ... ToolError, incl. QuestionStats, BatchHeader, BatchRow)
SCHEMA_RESOURCE: dict      # {"$schema": SCHEMA_URI, "$id": "openjev://schema", "$defs": DEFS}

def inline(schema: dict, defs: Mapping[str, dict] = DEFS) -> dict:
    """Deep copy with every {"$ref": "#/$defs/X"} replaced by a copy of defs[X] (recursively, sibling
    keys merged), root gets "$schema": SCHEMA_URI, no "$ref" and no "$defs" remain. Raises KeyError
    for an unknown def. Pure."""

INPUT_SCHEMAS: dict[str, dict]    # "ask","yes_no","classify","score","lint","status" -> inlined (spec 2.6-2.9, 2.13, 2.17)
OUTPUT_SCHEMAS: dict[str, dict]   # same keys -> inlined output schemas
```

Rules: every input root is `{"type": "object", ...}` with no root `oneOf`/`anyOf`/`allOf`; nested
`oneOf` (State, escape, Answer) stays. Schemas are the spec's, verbatim, plus the 1.2 optional
fields (lint `emit`, status `capabilities` and `mcp`, Meta `body_hashes`, `server_timing`,
`timeout_ms_used`). `score.levels` stays `type: array` (object levels are rejected in code with
the E013 hint); `classify.labels` stays `type: object` (an array is converted before validation,
D.13).

### D.9 `validate.py`

```python
def validate_args(tool: str, args: Mapping[str, Any] | None) -> ToolError | None:
    """Validate against INPUT_SCHEMAS[tool] (cached Draft202012Validator). None -> {}. First failing
    error by jsonschema.exceptions.best_match -> invalid_input(path, message, hint) where path is the
    dotted instance path ("questions.team.criteria", "" -> "arguments") and hint is
    "expected: <json of the failing subschema, max 1 KiB>". Pure."""

def validate_output(tool: str, structured: Any) -> list[str]:
    """Error messages against OUTPUT_SCHEMAS[tool]; [] when valid. Tests only; never on the hot path."""
```

### D.10 `derive.py`

```python
ESCAPE_PREFIXES = ("other", "none", "no_match", "not_stated")    # a key k is an escape when k == p or k.startswith(p + "_")

@dataclass(frozen=True)
class Band:
    yes_at: float = 0.8
    no_at: float = 0.2
    choice_min_p: float = 0.6

def is_escape(key: str) -> bool: ...
def entropy(probs: Mapping[str, float]) -> float: ...            # -sum p ln p over p > 0
def noul_answer(raw: dict, band: Band) -> dict:
    """{"type": "noul", "p", "band": yes|no|grey, "margin": |2p-1|}"""
def choice_answer(raw: dict, band: Band) -> dict:
    """{"type", "choice", "p_top", "runner_up" (None if 1 key), "margin" (p_top - p_second, p_top if
    1 key), "probabilities" (server order), "confidence" (server), "entropy", "abstained"
    (is_escape(choice) or p_top < band.choice_min_p)}"""
def score_answer(raw: dict, criteria: Sequence[str] | None = None) -> dict:
    """{"type", "score", "level" (argmax over int keys), "level_label" (raw legend[str(level)], else
    criteria[level]), "probabilities", "confidence", "spread" (sqrt(sum p_k (k-score)^2)), "bimodal"
    (two levels i, j with |i-j| >= 2 and p_i, p_j >= 0.2)}"""
def derive_answers(raw_answers: Mapping[str, dict], questions: Mapping[str, dict], band: Band) -> dict:
    """Ordered by `questions`. Raises ToolError OJ_PROTOCOL ("response lacks answers.<id>") for a
    missing key or a type mismatch. Pure."""
def chunks_estimate(questions: Mapping[str, dict]) -> int:
    """Spec 2.3 packing estimate (+-25%): rows ceil(chars(id + labels)/3.6) + 2 tokens into a 64-token
    canvas; lines format up to 10 questions, indexed above. Must give 1 for 10 noul, 2 for 10
    questions with 5-level scores, 22 for 256 noul. Pure."""
```

No rounding: outputs carry the server's floats; tests compare to 4 decimals.

### D.11 `lint.py`

```python
@dataclass
class Finding:
    code: str
    path: str
    message: str
    fix: str | None = None
    rule: str | None = None
    autofixed: bool | None = None
    limit_source: str | None = None        # limit-dependent codes only
    def to_dict(self) -> dict: ...          # omits None fields
    def line(self) -> str: ...              # "W101 questions.urgent: noul without criteria"

@dataclass
class LintReport:
    valid: bool                            # no errors after autofix
    errors: list[Finding]
    warnings: list[Finding]
    fixed_request: dict | None             # only when autofix changed something
    estimate: dict                         # {"questions","chunks","input_tokens_approx","latency_ms_idle_approx","billed_reads"}

def lint_request(request: Mapping[str, Any], *, limits: Limits, profile: str = "default",
                 autofix: bool = True) -> LintReport:
    """A full /v1/systemone body (model, state, questions, options at top level, images). Pure, no
    network, never raises on bad input (findings instead; a non-object request is E002/E001).
    Codes: E001-E028 and W101-W504 of spec 2.13 with the 1.2 narrowings (E017 non-object noul criteria
    only, E027 unknown noul criteria keys with yes->true/no->false autofix, E028 per-model capability,
    W403 only for <= 10 all-noul/choice questions). E003/E015/E023/E025 are errors only when
    limits.source == "server", else warnings with limit_source "default"; E025 with prompt_tokens None
    names both caps (32,768 MLX, 65,536 vLLM). E024 checks limits.known_models + ALIASES_ACCEPTED
    only when known_models is not None. profile "gate": warnings about blocking questions become
    errors; W404 applies. W405/W406 fire only when options carry batch fields (concurrency, sampling).
    E030-E032 and W601-W605 are phase 2 (batch import) and not implemented."""

def estimate(request: Mapping[str, Any]) -> dict:
    """The estimate block alone (used for timeout scaling). Pure."""

def snippets(body: Mapping[str, Any], base_url: str) -> dict:
    """{"body": body, "curl": "...", "python": "..."}; curl sends --data-binary of exactly
    wire.body_bytes(body) and -H "Authorization: Bearer $OPENJEV_API_KEY"; python reads
    os.environ.get("OPENJEV_API_KEY"). The key value never appears. Pure."""
```

### D.12 `envelope.py`

```python
TEXT_ANNOTATIONS = {"audience": ["assistant"]}

def success_result(structured: dict) -> dict:
    """{"content": [{"type": "text", "text": wire.dumps_text(structured), "annotations": TEXT_ANNOTATIONS}],
        "structuredContent": structured, "isError": False}. Pure."""

def error_result(err: ToolError) -> dict:
    """{"content": [{"type": "text", "text": wire.dumps_text({"error": err.to_dict()}),
        "annotations": TEXT_ANNOTATIONS}], "isError": True}; no structuredContent. Pure."""
```

The only two producers of a `tools/call` result. `server.py` converts with
`types.CallToolResult.model_validate(result)`.

### D.13 `tools/__init__.py`

```python
@dataclass
class ToolContext:
    config: Config
    client: OpenJevClient
    limits: LimitsCache
    progress: ProgressEmitter
    audit: AuditLog | None
    warnings: list[Finding] = field(default_factory=list)   # filled by call_tool from ToolSpec.prepare

Handler = Callable[[ToolContext, dict], Awaitable[dict]]   # returns structuredContent; raises ToolError

@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_schema: dict                     # schemas.INPUT_SCHEMAS[name]
    output_schema: dict                    # schemas.OUTPUT_SCHEMAS[name]
    annotations: dict                      # wire names: readOnlyHint, destructiveHint, idempotentHint, openWorldHint (+ title)
    handler: Handler
    prepare: Callable[[dict], tuple[dict, list[Finding]]] | None = None
    # runs BEFORE validate_args; returns (coerced args, findings) and may raise ToolError.
    # classify: labels list -> object + W202. score: object levels -> raise E013-hinted OJ_INVALID_INPUT.

TOOLS: tuple[ToolSpec, ...]                # exactly: ask, yes_no, classify, score, lint, status (spec 2.5 order)

def tools_for(toolsets: str) -> tuple[ToolSpec, ...]:
    """Phase 1: the same six tools for "all" and "core"."""

class UnknownTool(LookupError): ...

async def call_tool(ctx: ToolContext, name: str, arguments: Mapping[str, Any] | None) -> dict:
    """The tools/call pipeline (section E). Returns an envelope dict. Raises UnknownTool (server.py
    turns it into JSON-RPC -32602 "Unknown tool: <name>"); lets anyio cancellation propagate; every
    other exception becomes error_result (ToolError as is; anything else OJ_INTERNAL, traceback to stderr)."""
```

Annotations (spec 2.5): `ask` readOnly, idempotent false (`think` breaks it), `yes_no`/`classify`/
`score`/`lint`/`status` readOnly + idempotent; all `openWorldHint: false`, `destructiveHint` absent.
Titles: "Ask OpenJev", "Yes/no claim", "Classify", "Score on a scale", "Lint a request",
"OpenJev status". Descriptions: one or two sentences each, saying what is decided and (lint) "no
network".

### D.14 `tools/read.py`

```python
@dataclass
class ReadOutcome:
    raw: dict                              # last server body
    answers: dict                          # derived, request order
    meta: dict                             # Meta (spec 2.2) over every request made
    lint_warnings: list[Finding]
    results: list[HttpResult]

def compute_timeout(config: Config, options: Mapping[str, Any], estimate: Mapping[str, Any]) -> int:
    """options.timeout_ms if given; else max(config.timeout_ms, 3 * estimate.latency_ms_idle_approx +
    15 * think), at least 120000 with think > 0, capped at 600000. Pure."""

async def run_read(ctx: ToolContext, *, state: Any, questions: dict, options: Mapping[str, Any] | None,
                   band: Band, lint_mode: str = "warn", profile: str = "default",
                   criteria_for_score: Mapping[str, Sequence[str]] | None = None) -> ReadOutcome:
    """Build the body (wire.build_body, model = options.model or config.model), lint it
    (lint_request with ctx.limits.peek()); any error -> raise ToolError OJ_INVALID_INPUT whose message
    is "; ".join("<path>: <message>. Fix: <fix>") and path/hint from the first error; think/sequential
    with images refused here. Then client.systemone, derive_answers, Meta, progress after each request,
    audit record. Raises ToolError."""

async def ask(ctx: ToolContext, args: dict) -> dict: ...       # spec 2.6 output; "lint" omitted when lint == "off"; "raw" when return_raw
async def yes_no(ctx: ToolContext, args: dict) -> dict: ...    # spec 2.7; question id "q"; samples 1, one samples:4 re-read on grey unless options.samples given
async def classify(ctx: ToolContext, args: dict) -> dict: ...  # spec 2.8; escape handling, abstain reason, multi_label, returned key outside set -> OJ_PROTOCOL
async def score(ctx: ToolContext, args: dict) -> dict: ...     # spec 2.9; object levels -> OJ_INVALID_INPUT with the E013 hint; one_based
def classify_prepare(args: dict) -> tuple[dict, list[Finding]]: ...   # labels list -> {l: l} + W202
def score_prepare(args: dict) -> tuple[dict, list[Finding]]:
    """dict `levels` -> raise invalid_input("levels", "levels is an object; the server returns 422
    'Input should be a valid list'", hint="E013: use a list ordered lowest first"); else (args, [])."""
```

Meta construction (every read tool): `model` = last response `model`; `request_ids`,
`body_hashes` in request order; `requests` = count; `latency_ms` = sum of wall times;
`server_ms` = sum of `total_ms` from server-timing (absent -> omitted); `server_timing` = per-field
sums; `input_tokens`/`output_tokens` = sums of `usage`; `chunks_estimate`; `timeout_ms_used`;
`warnings` = operational strings (re-read done, retries), then `Finding.line()` of each
`ctx.warnings` entry (prepare findings such as W202), then, for `yes_no`/`classify`/`score` (which
have no `lint` field), `Finding.line()` of each lint warning. `ask` puts lint warnings in
`lint.warnings` as `Finding.to_dict()`. Details per tool: spec 2.6-2.9 and 6.7.

Abstain reason strings (classify): `"escape option '<k>' won (p=<p:.4f>)"`,
`"p_top <p:.4f> < min_p <m>"`.

### D.15 `tools/lint_tool.py` and `tools/status.py`

```python
async def lint(ctx: ToolContext, args: dict) -> dict:
    """Spec 2.13. Exactly one of `request` or `questions` (both or neither -> OJ_INVALID_INPUT).
    state/questions/options/images are merged into a body (model = config.model) when `request` is
    absent. Uses ctx.limits.peek(); never does I/O. emit -> snippets + body_hash of the (fixed) body."""

async def status(ctx: ToolContext, args: dict) -> dict:
    """Spec 2.17 phase-1 fields. GET /health (200 -> healthy true; ToolError propagates:
    OJ_UNREACHABLE, OJ_NOT_FOUND, OJ_AUTH ...), ctx.limits.refresh(), optional probe = yes_no-shaped
    samples:1 noul read (latency_probe_ms, resolved). auth: "bearer" when api_key set and reads succeed,
    "none" when unset and reads succeed, else "unknown". mcp: {"server_version", "protocol_versions",
    "batch_max_inflight", "transport"}. warnings: limits warnings, logs_bodies warning."""
```

### D.16 `progress.py`

```python
class ProgressEmitter:
    def __init__(self, send: Callable[[float, float | None, str | None], Awaitable[None]] | None, *,
                 min_interval_s: float = 1.0, clock: Callable[[], float] = time.monotonic): ...
    @classmethod
    def noop(cls) -> ProgressEmitter: ...
    async def emit(self, progress: float, total: float | None = None, message: str | None = None) -> bool:
        """Sends only when send is set, progress > last sent progress, and min_interval_s has passed
        since the last send (the first send is immediate). Returns whether it sent."""
```

`server.py` builds it per request with `send = ctx.session.report_progress`, which the SDK turns
into a no-op when the request carried no `progressToken` (verified, A.2), so no `_meta` parsing is
needed on our side. `ask`, `classify`, `score` emit `(1, 1, "1/1 requests")` after their one
request; `yes_no` emits once, after its final read, with `total` = requests made. The 6.6
"constant total" test runs on `ask`.

### D.17 `audit.py`

```python
class AuditLog:
    def __init__(self, path: str, *, log_states: bool = False): ...
    def write(self, record: dict) -> None:
        """Append one line (wire.dumps_text + "\n") under a threading.Lock, opened in append mode per
        write; an OSError is reported once on stderr and disables the log."""

def read_record(tool: str, body: dict, outcome: ReadOutcome, *, log_states: bool,
                decision: str | None = None, degraded: bool = False) -> dict:
    """{ts, tool, question_hash, state_hash, model_resolved, request_id, answers, decision,
    latency_ms, degraded} (+ "state" when log_states). Pure except ts."""
```

### D.18 `resources.py`

```python
RESOURCES: tuple[dict, ...]   # wire Resource dicts in spec 2.18 order:
  # {"uri": "openjev://schema", "name": "schema", "title": "OpenJev common types", "mimeType": "application/schema+json",
  #  "description": ..., "annotations": {"audience": ["assistant"], "priority": 0.6, "lastModified": BUILD_TIME}}
  # {"uri": "openjev://limits", "name": "limits", "title": "OpenJev limits", "mimeType": "application/json",
  #  "annotations": {"audience": ["assistant"], "priority": 0.8}}   # lastModified = read time, set on read

class ResourceNotFound(LookupError): ...

async def read_resource(uri: str, ctx: ToolContext) -> dict:
    """{"contents": [{"uri", "mimeType", "text"}], "ttlMs", "cacheScope"}: schema -> dumps_text(SCHEMA_RESOURCE),
    3600000 public; limits -> dumps_text((await ctx.limits.get()).resource(config)), 60000 private.
    Raises ResourceNotFound (server.py -> JSON-RPC -32602 "Resource not found", data {"uri": uri};
    -32002 is retired in 2026-07-28)."""
```

`BUILD_TIME`: ISO 8601 of the package (`importlib.metadata` is not reliable for it; use the
modification time of `schemas.py`, UTC).

### D.19 `server.py` (SDK boundary)

```python
INSTRUCTIONS = ("OpenJev answers typed questions about a state you send (yes/no, one of N, a scale) "
                "with calibrated probabilities instead of generated text. Call status if you are not "
                "sure the server is up. These tools only read and advise; they never execute anything.")

@dataclass
class AppState:                       # what the lifespan yields; handlers read ctx.lifespan_context
    config: Config
    client: OpenJevClient
    limits: LimitsCache
    audit: AuditLog | None

def build_server(config: Config, *, client: OpenJevClient | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 warm_limits: bool = True) -> mcp.server.lowlevel.Server[AppState]:
    """Server(SERVER_NAME, version=__version__, instructions=INSTRUCTIONS, lifespan=...,
    cache_hints={"tools/list": CacheHint(3600000, "public"), "resources/list": CacheHint(3600000, "public"),
                 "server/discover": CacheHint(3600000, "public")},
    on_list_tools, on_call_tool, on_list_resources, on_read_resource).
    Adds the server/discover override (supportedVersions = PROTOCOL_VERSIONS, capabilities =
    server.get_capabilities(protocol_version=ctx.protocol_version), instructions).
    No prompts, completions, subscribe or logging handlers. server.middleware keeps the SDK default.
    `client` injected -> the lifespan uses it and does not close it; else it creates
    OpenJevClient(config, transport=transport). Pure construction (no I/O until run)."""

async def run_stdio(config: Config) -> None: ...
async def run_http(config: Config) -> None: ...      # uvicorn.Server(uvicorn.Config(build_http_app(...), host, port, log_level="warning"))

def main(argv: list[str] | None = None) -> int:
    """openjev-mcp [--transport http|stdio] [--host H] [--port P] [--version]. Exit codes in B.4."""
```

Handlers: `on_list_tools` returns `ListToolsResult(tools=[Tool(name, title, description,
input_schema, output_schema, annotations=ToolAnnotations(...)) for t in tools_for(config.toolsets)])`;
`on_call_tool` builds `ToolContext` (progress = `ProgressEmitter(ctx.session.report_progress)`), calls
`tools.call_tool` and returns `types.CallToolResult.model_validate(result)` (verified to accept
the camelCase dict; returning the dict itself also works, the SDK sieve validates `Mapping`
results), `UnknownTool` -> `MCPError(INVALID_PARAMS, "Unknown tool: <name>")`;
`on_read_resource` returns `ReadResourceResult(contents=[TextResourceContents(...)], ttl_ms, cache_scope)`.

### D.20 `http_app.py` (SDK boundary)

```python
def security_settings(config: Config) -> TransportSecuritySettings: ...      # B.5
class BearerTokenMiddleware:                                                  # pure ASGI
    def __init__(self, app: ASGIApp, token: str, path: str = "/mcp"): ...
async def health(request: Request) -> JSONResponse: ...                       # B.2 body
def build_http_app(server: Server, config: Config) -> ASGIApp:
    """server.streamable_http_app(...) per B.2 with Route("/health", health, methods=["GET"]);
    wrapped in BearerTokenMiddleware when config.token. The Starlette lifespan runs
    server.session_manager.run(), which enters the server lifespan once."""
```

### D.21 `recipes/expr.py`

```python
class ExprError(ValueError): ...      # load-time; message names the offending token and its offset

@dataclass(frozen=True)
class Signal:
    value: float | str                 # noul p, score expected value, choice key
    p: float | None = None             # choice p_top (".p")
    grey: bool = False                 # noul band grey (policy yes_at/no_at, default 0.8/0.2)
    abstained: bool = False            # choice abstained (derive.choice_answer)

# AST node classes (frozen dataclasses): Or(items), And(items), Not(item), Compare(left, op, right),
# Quant(kind "any"|"all", signals, op, right), Pred(kind "grey"|"abstained"|"rule", arg),
# Lit(value), SigRef(name, attr None|"p"), PolRef(name). Clause(decision, cond | None). Combine(clauses).

def parse_expr(text: str, *, signals: frozenset[str], policy: frozenset[str],
               decisions: frozenset[str]) -> Node: ...           # raises ExprError
def parse_combine(text: str, *, signals: frozenset[str], policy: frozenset[str],
                  decisions: frozenset[str]) -> Combine: ...     # last clause must be "<d> otherwise"
def evaluate(node: Node, *, signals: Mapping[str, Signal], policy: Mapping[str, float | str],
             rule_decision: str | None) -> bool: ...             # total: missing signal -> comparison false; all() with a missing signal -> false
def decide(combine: Combine, **env) -> tuple[str, int]: ...      # (decision, clause index)
def explain(node: Node, **env) -> str: ...                       # "remote_code=0.9999 >= 0.85" for reasons
```

Grammar exactly as spec 2.18. Tokens: identifiers `[A-Za-z_][A-Za-z0-9_]*`, numbers, single-quoted
strings, `( ) , ; .p`, the six operators, keywords `if otherwise and or not any all grey abstained
rule`. An identifier that is neither a signal nor a policy key is an `ExprError`. No `eval`, no
attribute access other than `.p`.

### D.22 `recipes/rules.py`

```python
MAX_PATTERN_CHARS = 512
MAX_INPUT_BYTES = 65536

class PatternError(ValueError): ...

@dataclass(frozen=True)
class Rule:
    index: int
    decision: str
    scope: str                          # "segment" | "command"
    match: Any                          # re2 compiled
    unless: Any | None
    source: str                         # original match pattern

@dataclass(frozen=True)
class RuleVerdict:
    decision: str | None                # "allow" | "deny" | None (go to reads)
    rule: int | None
    reason: str

def compile_rule(spec: Mapping[str, Any], *, index: int, decisions: frozenset[str]) -> Rule:
    """Rejects (PatternError): pattern > 512 chars, backreferences (\\1-\\9, \\k<, (?P=), lookaround
    ((?=, (?!, (?<=, (?<!), anything re2.compile refuses, a decision not in decisions."""

def apply_rules(rules: Sequence[Rule], command: str, split: SplitResult) -> RuleVerdict:
    """Spec 2.18 segment decision with re2 search(): deny if any segment (scope segment) or the whole
    command (scope command) matches a deny rule; allow only if split.ok, input not truncated, and every
    segment matches some allow rule without matching that rule's unless; else None. The input is
    truncated to MAX_INPUT_BYTES (UTF-8) before matching. Pure."""
```

### D.23 `recipes/shell.py`

```python
@dataclass(frozen=True)
class SplitResult:
    ok: bool
    segments: tuple[str, ...]           # for rules
    parts: tuple[str, ...]              # for reads
    reason: str | None                  # why ok is False

def split_command(command: str) -> SplitResult:
    """Pure. segments: split on unquoted ; && || | & and newlines; each $( ... ), `...`, <( ... ), >( ... )
    is also emitted as a segment of its own while the enclosing segment keeps its text (so it never
    matches the allow class). parts: split on top-level ; && || and newlines only (a pipeline stays one
    part). Segments and parts are stripped; empty ones dropped. Unbalanced quotes or substitutions,
    heredocs (<<), and backslash-newline continuations -> ok False, segments = parts = (command,)."""
```

### D.24 `recipes/template.py`

```python
class TemplateError(ValueError): ...

def check_template(template: str, *, raw_allowed: frozenset[str]) -> None:
    """Load-time: balanced sections, {{{name}}} only for raw_allowed names. Raises TemplateError."""

def render(template: str, inputs: Mapping[str, Any], *, raw_allowed: frozenset[str]) -> str:
    """{{name}} -> json.dumps(str(v), ensure_ascii=False)[1:-1] for str (non-str: json.dumps(v)), so
    a newline in an input becomes the two characters \\n and stays on its line; missing -> "".
    {{#name}}...{{/name}}: list -> once per item (item keys shadow outer names), truthy scalar -> once,
    falsy/missing -> never. {{{name}}} -> raw str(v), only for raw_allowed. Pure."""
```

### D.25 `recipes/engine.py`

```python
class RecipeError(ValueError): ...

@dataclass(frozen=True)
class Recipe:
    id: str; title: str; usage_type: str; description: str
    input_schema: dict
    steps: tuple[dict, ...]                     # validated step dicts; read steps carry compiled templates
    rules: tuple[Rule, ...]
    policy: dict[str, float]
    policy_profiles: dict[str, dict[str, float]]
    question_profiles: dict[str, dict]          # profile -> QuestionSet ("strict" is the default set)
    signal_map: dict[str, dict[str, str]]       # profile -> {question id: signal name}
    decisions: tuple[str, ...]
    combine: Combine
    when: dict[str, Node]                       # step id -> parsed when
    fail_mode: str                              # "open" | "closed"
    fallback: dict[str, str]                    # {"interactive": "ask", "unattended": "deny"}
    test_file: str | None
    limitations: tuple[str, ...]

@dataclass
class RecipeOutcome:
    recipe: str
    decision: str
    reason: str
    signals: dict                               # name -> number | key; choice adds "<name>_p"
    thresholds_used: dict
    degraded: bool
    error: ToolError | None
    requests: int
    answers: dict                               # last read's derived answers
    meta: dict                                  # Meta over all reads
    rule: dict | None                           # {"index", "decision", "pattern"} when a rule decided
    def to_dict(self) -> dict: ...              # spec 2.16 output shape

def load_recipe(doc: Mapping[str, Any]) -> Recipe:
    """Validates the 2.18 recipe schema, compiles rules (PatternError), templates (TemplateError),
    combine and when (ExprError); wraps any of them in RecipeError naming the recipe id. Never
    half-loads."""

def load_builtin(recipe_id: str) -> Recipe: ...   # importlib.resources "openjev_mcp.recipes.builtin/<id>.json"

async def run_recipe(recipe: Recipe, inputs: Mapping[str, Any], *, client: OpenJevClient, config: Config,
                     profile: str | None = None, timeout_ms: int | None = None,
                     deadline_ms: int | None = None, read_options: Mapping[str, Any] | None = None) -> RecipeOutcome:
    """Order: compute split -> deterministic rules -> read steps whose `when` holds -> combine.
    command_gate reads the whole command first, then each part when there is more than one, and keeps
    the worst decision (deny > ask > allow). A rule deny is final. On any ToolError (or deadline) the
    outcome is the fallback for inputs["unattended"] with degraded True and error set; never raises
    ToolError. Raises RecipeError only for inputs failing input_schema (checked without jsonschema:
    required keys and primitive types)."""
```

`builtin/command_gate.json` extends the 2.18 document with `question_profiles` (`strict`: the seven
questions of `ex-gate-deny` verbatim with `destructive`; `lenient`: the same with
`destructive_regenerable` of 5.3 instead), `signal_map.lenient = {"destructive_regenerable":
"destructive"}`, and `fallback`. The verbatim question text comes from
`docs/mcp-skill-spec/tests/cases/00-spec-examples.json::ex-gate-deny` and spec 5.3.

### D.26 `claude_hooks.py`

The only module that knows Claude Code's hook field names. Fields used (Claude Code hooks
reference; contract-tested against recorded payloads, spec 7 #14):

```python
class HookInputError(ValueError): ...

@dataclass(frozen=True)
class PreToolUseEvent:
    tool_name: str
    command: str | None                 # tool_input["command"] for Bash
    cwd: str | None
    session_id: str | None
    transcript_path: str | None
    permission_mode: str | None
    raw: dict

def parse_pretooluse(payload: Any) -> PreToolUseEvent:
    """Requires hook_event_name == "PreToolUse" (when present), tool_name, tool_input (object).
    Raises HookInputError."""

def last_user_prompt(transcript_path: str | None, *, max_bytes: int = 262144) -> str | None:
    """Best effort: reads at most the last max_bytes of the JSONL transcript and returns the text of the
    last user message (entries with type "user" / message.role "user"; string content or the text parts).
    Never raises."""

def pretooluse_output(decision: str, reason: str) -> dict:
    """{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
    "permissionDecisionReason": reason}} for decision in allow|ask|deny."""

SUPPORTED_CLAUDE_CODE = ("2",)          # major versions with a recorded fixture in tests
```

### D.27 `hook.py`

```python
def main(argv: list[str] | None = None) -> int:
    """openjev-hook pretooluse [--profile strict|lenient] [--unattended] [--timeout-ms 10000]
                               [--task TEXT] [--defer-allow]
    Reads stdin JSON, writes one JSON object to stdout, exits 0 (a decision is always JSON, including
    fail-closed ones). Not a Bash tool (or no command) -> prints nothing, exit 0 (no opinion).
    task = --task, else OPENJEV_HOOK_TASK, else claude_hooks.last_user_prompt(transcript_path), else
    "(task unknown)"; context = "cwd: <cwd>". Runs recipes.run_recipe(load_builtin("command_gate"),
    {"task", "command", "context", "unattended", "profile"}, deadline_ms=timeout_ms). Any failure
    (bad stdin, recipe degraded, deadline) -> "ask", or "deny" with --unattended; reason names the cause.
    --defer-allow: an "allow" decision prints nothing (Claude Code's own permission flow decides).
    Other subcommands (stop, userprompt, posttooluse) are phase 2 and absent from --help."""
```

Imports allowed: `argparse, json, os, sys, anyio`, `config`, `errors`, `wire`, `mapping`, `http`,
`derive`, `recipes.*`, `claude_hooks`. Not `mcp`, not `jsonschema`, not `lint`, not `schemas`.

### D.28 `cli.py`

```python
def main(argv: list[str] | None = None) -> int:
    """openjev [--version] | openjev version. `check` and `filter` are phase 2 (spec 2.20) and absent."""
```

---

## E. Data flow of a tool call

```
client --tools/call--> SDK ladder (envelope, version, Mcp-* headers on HTTP)
   -> server.on_call_tool(ctx, params)
   -> tools.call_tool(ToolContext, name, arguments)
        1. spec = TOOLS[name]                      unknown -> UnknownTool -> JSON-RPC -32602
        2. prepare(args) -> ctx.warnings           classify arrays -> object + W202;
                                                   score object levels -> ToolError with the E013 hint
        3. validate.validate_args                  fail -> error_result(OJ_INVALID_INPUT)
        4. handler(ctx, args):
             a. tool-specific checks (yes_at > no_at, think/sequential with images)
             b. wire.build_body
             c. lint.lint_request(body, limits=ctx.limits.peek())   always runs;
                errors -> ToolError OJ_INVALID_INPUT (nothing sent); lint "off" only hides warnings
             d. timeout = compute_timeout(config, options, report.estimate)
             e. client.systemone(body, timeout_ms=timeout, think=...)   retries inside
                non-2xx / transport fault -> mapping -> ToolError
             f. derive.derive_answers                missing key -> ToolError OJ_PROTOCOL
             g. tool-level decision (yes_no re-read on grey, classify abstain, score one_based)
             h. Meta, progress.emit after each request, audit.write
             -> structured dict
        5. envelope.success_result(structured)     ToolError anywhere -> envelope.error_result
                                                   other Exception -> error_result(OJ_INTERNAL)
   -> types.CallToolResult.model_validate(result) -> SDK stamps resultType/_meta (modern) -> client
```

Result envelope:

- Success: `structuredContent` = the tool's output object (validates against `outputSchema`), and
  exactly one text block `dumps_text(structuredContent)` with `annotations {audience: ["assistant"]}`.
  No `resource_link` blocks in phase 1 (no tool writes files).
- Error: `isError: true`, one text block `{"error": ToolError}`, no `structuredContent`.
- Protocol faults stay JSON-RPC errors and are produced by the SDK or `server.py` only: malformed
  request, unknown tool or resource (-32602), missing envelope keys (-32602), unsupported revision
  (-32022), header mismatch on HTTP (-32020).

Cancellation: the SDK cancels the handler's cancel scope (`notifications/cancelled` on stdio, client
disconnect on HTTP 2026-07-28). The cancellation lands as an anyio cancelled exception inside the
awaited httpx request or retry sleep; `async with` on the semaphore releases it; `call_tool` does not
catch it; the SDK writes no result (verified, A.2). `ProgressEmitter` sends nothing after
cancellation because its coroutine is never reached again.

Statelessness: per-process state is the httpx connection pool, the semaphore, the limits cache and
the audit file handle. None changes a result except that lint findings for limit-dependent codes
follow the cached `limit_source`, which the spec accepts (2.2 "Limits").

---

## F. mise integration

Nobody runs `mise run start`, `mise run default` or `mise run restart` while implementing (they
load the 16 GB model). The MCP branch of every task is exercised with `mise run mcp`,
`mise run status`, `mise run logs mcp` and `mise run stop`, plus `bash -n` on every edited script.

### F.1 Names and files

| Item | Value |
|---|---|
| service key in `oj_svc` | `mcp` |
| label | `MCP` |
| port | `OPENJEV_MCP_PORT` (default 8100), exported in `common.sh` |
| URL | `OJ_MCP_URL="http://127.0.0.1:$OPENJEV_MCP_PORT"`; MCP endpoint `$OJ_MCP_URL/mcp` |
| health | `$OJ_MCP_URL/health` |
| pid file | `$OJ_RUN_DIR/.openjev-mcp.pid` (`OJ_MCP_PIDFILE`) |
| log file | `$OJ_RUN_DIR/.openjev-mcp.log` (`OJ_MCP_LOG`) |
| command | `$OJ_PY -m openjev_mcp --transport http --port $OPENJEV_MCP_PORT` (cwd `$OJ_ROOT`) |
| process match | `SVC_KIND=mcp`, `SVC_MATCH="-m openjev_mcp"` |
| env passed | `PYTHONUNBUFFERED=1 OPENJEV_BASE_URL="${OPENJEV_BASE_URL:-$OJ_SERVER_URL}"` (the UI keeps `OPENJEV_URL`) |

### F.2 Edits per file

- `mise-tasks/lib/common.sh`
  - export `OPENJEV_MCP_PORT="${OPENJEV_MCP_PORT:-8100}"`; add `OJ_MCP_URL`, `OJ_MCP_PIDFILE`,
    `OJ_MCP_LOG`.
  - `oj_svc mcp)` arm with the F.1 values.
  - `oj_cmd_is`: kind `mcp` accepts `rest` equal to `" -m openjev_mcp"` or starting with
    `" -m openjev_mcp "`. The `server` arm is unchanged: `" -m openjev_mcp ..."` does not match
    `" -m openjev"|" -m openjev "*`. `pgrep -f -- "-m openjev"` may list the MCP process as a server
    candidate; `oj_pid_is_ours ... server` rejects it.
  - `oj_port_foreign`: also skip `oj_pid_is_ours "$p" mcp`.
  - `oj_spawn`: `mcp` branch via `spawn.py` with the F.1 command and env (same error handling as ui).
  - `oj_mcp_installed() { [ -x "$OJ_PY" ] && "$OJ_PY" -c 'import openjev_mcp, mcp, re2, jsonschema' >/dev/null 2>&1; }`
    and `oj_require_mcp_installed` ("the MCP server is not installed. Run: mise run install").
- `mise-tasks/start`
  - description: "Start OpenJev (:8080), the UI (:8090) and the MCP server (:8100) in the
    background, then open the UI in your browser"; keep `#MISE alias="default"`.
  - `oj_require_mcp_installed` after `oj_require_installed`.
  - `preflight_one mcp` beside server and ui, before anything is spawned; hint
    `OPENJEV_MCP_PORT=$((OPENJEV_MCP_PORT + 1))`.
  - `start_one mcp` after ui; wait `oj_wait_up "$OJ_MCP_URL/health" 20 "$mcp_pid"` before the model
    wait (failure: tail the MCP log, warn that OpenJev keeps loading if this run spawned it, die with
    "the MCP server did not come up on $OJ_MCP_URL (full log: mise run logs mcp)").
  - final lines add `oj_ok "MCP:     $OJ_MCP_URL/mcp"`.
- `mise-tasks/stop`: stop order `mcp`, `ui`, `server`; description mentions the MCP server.
- `mise-tasks/status`: `show mcp` after ui; description mentions MCP.
- `mise-tasks/logs`: `mcp` selector (`mise run logs mcp`); default follows all three logs; usage
  `mise run logs [server|ui|mcp]`.
- `mise-tasks/restart`: unchanged code (stop then start cover MCP); description unchanged.
- `mise-tasks/mcp` (new, executable, same header style):
  - `#MISE description="Start only the MCP server (:8100) in the background and print how to register it; --foreground runs it in this terminal"`
  - `oj_require_cmds curl lsof ps`, `oj_require_mcp_installed` (no macOS/RAM/model checks: the MCP
    server needs no model).
  - default: if running, report PID; else port preflight (same message and hint as start), spawn,
    wait 20 s on `/health`, then print the `claude mcp add --transport http openjev $OJ_MCP_URL/mcp`
    line and the `.mcp.json` snippet, and note when OpenJev is not up yet (`oj_http_up
    $OJ_SERVER_URL/v1/models`), without starting it.
  - `--foreground`: `exec "$OJ_PY" -m openjev_mcp --transport http --port "$OPENJEV_MCP_PORT"` with
    the same env (Ctrl+C stops it).
- `mise-tasks/install`: after the root `pip install -e ".[mlx,test]"`, run
  `pip install -e "./mcp[test]"`; the stamp hashes `pyproject.toml` and `mcp/pyproject.toml`
  together; the import checks add `openjev_mcp, mcp, re2`.
- `mise-tasks/test`: `oj_require_mcp_installed`; add `run_suite "MCP server (pytest mcp/tests)"
  "$OJ_PY" -m pytest -q mcp/tests` (rootdir is `mcp/` through its `pyproject.toml`); description lists it.
- `.gitignore`: `.openjev-mcp.log`, `.openjev-mcp.pid`.
- `README.md`: tasks table rows for `mise run mcp` and the updated `start`/`stop`/`logs`; settings
  rows `OPENJEV_MCP_PORT` (8100) and `OPENJEV_MCP_TOKEN`; a short "MCP server" paragraph with the
  `claude mcp add` line and a link to `mcp/README.md`.

`mise.toml` needs no change: tasks are discovered from `mise-tasks/`.

---

## G. Testing strategy

All tests run under `pytest mcp/tests` with no model, no network, no `transformers` import at
runtime. `pytest`'s anyio plugin (`@pytest.mark.anyio`, `anyio_backend = "asyncio"` fixture in
`conftest.py`) drives async tests.

### G.1 Stub OpenJev (`tests/stubs.py`)

```python
class StubEngine:
    """Replaces app.state.engine. decide() returns canned answers or raises the server's own errors."""
    def __init__(self, answers: Callable[[dict, Any, dict], dict] | None = None,
                 fault: Callable[[dict, Any, dict], BaseException | None] | None = None,
                 input_tokens: int = 100, thought_tokens: int = 0, delay_s: float = 0.0): ...
    async def decide(self, questions, state, seed, images=None, options=None) -> tuple[dict, int, int]: ...
    async def close(self) -> None: ...

def default_answers(questions: dict, state: Any, options: dict) -> dict:
    """Deterministic server-shaped answers: noul {"type","noul"}; choice {"type","choice",
    "probabilities","confidence"}; score {"type","score","legend","probabilities","confidence"}."""

def openjev_app(*, engine: StubEngine | None = None, api_key: str = "", origin_secret: str = "",
                max_body_bytes: int | None = None):
    """openjev.api.create_app(Settings(backend="vllm", api_key=..., origin_secret=..., ...)); sets
    app.state.engine = engine or StubEngine() and app.state.routes = httpx.AsyncClient() directly.
    The lifespan is NEVER entered: it would call AutoTokenizer.from_pretrained. Everything the error
    matrix needs (auth 401/403, 413, 422, the 400 routes, 529/503 from Overloaded/httpx errors raised
    by the stub) runs without it."""

def asgi_transport(app) -> httpx.ASGITransport: ...
def fault_transport(status: int | None = None, *, headers=None, body: bytes | str = b"",
                    exc: BaseException | None = None, delay_s: float = 0.0,
                    sequence: Sequence[...] | None = None) -> httpx.MockTransport:
    """Faults create_app cannot produce: connect refused (httpx.ConnectError), client timeout,
    429, text/plain 500, slow bodies; `sequence` returns a different response per attempt (retry tests).
    Records every request on .requests."""
def fail_on_request_transport() -> httpx.MockTransport:
    """Raises AssertionError on any request (lint and dry paths: no network I/O)."""
def replay_transport(case_ids: Sequence[str] | None = None) -> httpx.MockTransport:
    """Loads docs/mcp-skill-spec/tests/cases/00-spec-examples.json and tests/spec_build/captured.json
    ({id: {status, ms, server_timing, request_id, body}}). Matches a request by canonical JSON equality
    of its body with a case's `request` (model aside: the request model is compared as sent); answers
    with the captured status, body, `server-timing` and `x-request-id`. Unmatched -> AssertionError."""
```

Raising `openjev.engine.SchemaError(msg, loc)` from the stub makes `create_app` answer the exact 400
strings that `mapping.py` parses (`Overloaded` -> 529 with `retry-after: 1`, `httpx.ConnectError` ->
503 with `retry-after: 2`, `Upstream` -> "the model rejected this request").

### G.2 Protocol fixtures

```python
async def rpc_session(server) -> RawRPC:      # async context manager
    """create_client_server_memory_streams + server.run(..., server.create_initialization_options())
    in a task group. RawRPC.request(method, params, *, envelope=True, id=None) -> dict (the JSON-RPC
    response); RawRPC.notify(method, params); RawRPC.messages (everything received, in order);
    envelope=True adds _meta protocolVersion 2026-07-28 + clientCapabilities {}."""

def mcp_client(server, mode: str):            # mcp.Client(server, mode=mode, cache=None)
    """mode "2026-07-28" (modern_on_request, no framing) or "legacy" (initialize over memory streams)."""

async def http_app_client(server, config) -> httpx.AsyncClient:
    """build_http_app + `async with server.session_manager.run()` + httpx.ASGITransport;
    base_url http://127.0.0.1:8100."""
```

Which fixture for which 6.6 row:

- Raw JSON-RPC (`rpc_session`) for the protocol ladder: discover, -32602 on missing envelope keys,
  -32022, `resultType` + serverInfo `_meta`, `ttlMs`/`cacheScope` on list/discover/read, legacy
  `initialize` for 2025-06-18 and 2025-11-25, statelessness (two sessions, and a fresh server vs.
  one that served calls first, give identical `tools/list`, `resources/list`, `server/discover` and
  `resources/read openjev://schema`; `openjev://limits` is excluded because it carries the read time), cancellation (`notifications/cancelled` on a slow stub: no message
  for that id afterwards; semaphore available again), progress (strictly increasing, constant total,
  <= 1/s with a fake clock, none without a token). The high-level `Client` cannot test the ladder:
  in-process modern mode bypasses framing.
- `mcp_client(server, "2026-07-28")` and `mcp_client(server, "legacy")` for tool behaviour in both
  eras: tools/list order, titles, annotations, schema walk (root `type: object`, no root
  oneOf/anyOf/allOf, `$schema` 2020-12, no `$ref`), every success validates against its
  `outputSchema` and its text equals its JSON, invalid arguments -> `isError` `OJ_INVALID_INPUT`.
- `http_app_client` for HTTP: -32020 on missing `Mcp-Method`/wrong `Mcp-Name`, 403 Origin, 421 Host,
  401 without token when `OPENJEV_MCP_TOKEN` is set, `/health`, no `Mcp-Session-Id`, legacy stateless
  initialize + tools/list, SSE commit with progress.
- One real-HTTP smoke test: `python -m openjev_mcp --transport http --port <ephemeral>` as a
  subprocess with `OPENJEV_BASE_URL=http://127.0.0.1:9` (nothing listens there), then
  `mcp.Client("http://127.0.0.1:<port>/mcp")` lists the six tools and gets `isError`
  `OJ_UNREACHABLE` from `status`; the process is killed in teardown.
- stdio hygiene: `python -m openjev_mcp --transport stdio` subprocess; send initialize + tools/list;
  every stdout line parses as JSON-RPC; stderr may have text.

### G.3 Replays and rows

- Replay (`test_mcp_tools_replay.py`): `ex-ask`, `ex-think`, `ex-sequential`, `ex-yes-no`,
  `ex-classify-abstain`, `ex-score`, `ex-gate-deny`, `ex-gate-allow` through the tools / recipe engine
  over `replay_transport`. Compare answers and decisions to the spec's MCP output blocks with every
  float rounded to 4 decimals; `meta.latency_ms` is wall time and only checked to be a number;
  `request_ids` must equal the captured ids; warnings are asserted separately.
- Error matrix (`test_mcp_mapping.py`): one test per 2.4 row and per "Errors added in 1.2" row; each
  asserts `code`, `retryable`, number of attempts and the envelope shape.
- Lint (`test_mcp_lint.py`): a positive and a negative fixture per implemented code; the 2.13 example
  gives exactly the documented output; `fail_on_request_transport`; limits with and without a stub
  `/v1/limits` (E015 on 300 options warning by default, 30 options error with `max_choices: 24`);
  `emit` body bytes equal what `OpenJevClient` sends; snippets never contain the key.
- Env (`test_mcp_config.py`): `OPENJEV_MODEL` ignored, `OPENJEV_MCP_MODEL` honoured.
- Recipe engine: grammar acceptance and rejection (`__import__('os')`, attribute access, unknown
  identifiers, missing `otherwise`), pattern rejection (backreference, lookaround, > 512 chars), the
  6.6 command-gate rule lists, template newline test (an injected `\n` in `command` stays on its line).
- Hook (`test_mcp_hook.py`): subprocess `openjev-hook pretooluse` with recorded Claude Code payloads;
  fail-closed on every retryable error and on timeout (`ask`, `deny` with `--unattended`); overhead
  p95 < 300 ms measured on a rule-decided command (no read); `import openjev_mcp.hook` leaves
  `"mcp"` and `"jsonschema"` out of `sys.modules`.
- Packaging: `importlib.metadata.requires("openjev-mcp")` names no `transformers`/`tokenizers`/
  `openjev`; the three entry points resolve and `--help` exits 0.
- Live runs (`run_cases.py`, the 14/14 hook run) are human-run and recorded, not CI (TASKS conventions).

---

## H. Non-goals (phase 1)

- Phase 2 tools and features: `filter`, `batch`, `batch_results`, `recipe` tool, the other recipes,
  hooks `stop`/`userprompt`/`posttooluse`, `openjev check`/`openjev filter`, resources
  `openjev://recipes*`, `openjev://templates*`, `openjev://patterns`, `openjev://guide/authoring`,
  prompts and `completion/complete`, `resource_link` blocks, `OPENJEV_MCP_RECIPES`, the six phase-2
  skills.
- Phase 3: `calibrate`, `ask_image`, `batch.images`, `compile`, `generate`, the Tasks extension,
  `openjev://audits`, image URL fetching and the SSRF rules.
- Server change TASKS 0.1-0.3 (`/v1/limits` etc.): the MCP side handles the 404.
- No `MCPServer` layer, no OAuth / RFC 9728 metadata, no `subscriptions/listen`, no
  `resources/subscribe`, no `logging/setLevel`, no `notifications/message`, no `roots/list` on any
  era, no sampling, no elicitation, no stateful HTTP sessions.
- No Docker service for the MCP server (`docker-compose.yml` unchanged); no CI workflow file (the
  repo has no `.github/`; `mise run test` runs the suite, TASKS 1.25 is satisfied by it until a CI
  exists).
- Phase-2 note: file-path tools will need allowed roots from configuration, because a shared HTTP
  daemon's cwd is the repo root, not the client's project (`mcp/spec.md` roadmap).

---

## I. Work packages (for parallel implementation)

Each package owns its files exclusively and codes against section D only.

| WP | Files | Depends on (interfaces only) |
|---|---|---|
| 1 Skeleton + config | `mcp/pyproject.toml`, `openjev_mcp/__init__.py`, `__main__.py`, `config.py`, `errors.py`, `wire.py`, `cli.py`, `tests/test_mcp_config.py`, `test_mcp_wire.py`, `test_mcp_packaging.py` | - |
| 2 HTTP + mapping + limits | `http.py`, `mapping.py`, `limits.py`, `tests/stubs.py` (G.1 part), `test_mcp_mapping.py`, `test_mcp_http_client.py`, `test_mcp_limits.py` | WP1 |
| 3 Schemas + validation + derive + envelope + progress | `schemas.py`, `validate.py`, `derive.py`, `envelope.py`, `progress.py`, `audit.py`, `test_mcp_schemas.py`, `test_mcp_derive.py` | WP1 |
| 4 Lint | `lint.py`, `tools/lint_tool.py`, `test_mcp_lint.py` | WP1, WP3, `limits.Limits` |
| 5 Read tools + status + resources | `tools/__init__.py`, `tools/read.py`, `tools/status.py`, `resources.py`, `test_mcp_tools_read.py`, `test_mcp_tools_replay.py`, `test_mcp_status.py`, `test_mcp_resources.py` | WP2-4 |
| 6 Server + transports | `server.py`, `http_app.py`, `tests/conftest.py`, `tests/stubs.py` (G.2 part), `test_mcp_protocol.py`, `test_mcp_http_transport.py`, `test_mcp_cancel_progress.py`, `test_mcp_stdio.py` | WP5 (`tools.TOOLS`, `call_tool`, `resources`) |
| 7 Recipes + hook | `recipes/*`, `claude_hooks.py`, `hook.py`, `test_mcp_expr.py`, `test_mcp_rules.py`, `test_mcp_shell.py`, `test_mcp_template.py`, `test_mcp_command_gate.py`, `test_mcp_hook.py` | WP1, WP2 (`OpenJevClient`), WP3 (`derive`) |
| 8 mise | `mise-tasks/lib/common.sh`, `start`, `stop`, `status`, `logs`, `install`, `test`, new `mcp`, `.gitignore` | `python -m openjev_mcp`, `/health` (B.2) |
| 9 Docs + skills | `mcp/README.md`, `mcp/spec.md`, `mcp/skills/*/SKILL.md`, root `README.md`, `test_mcp_skills.py` | sections A, B, F, H |

`tests/stubs.py` is shared: WP2 writes G.1, WP6 appends G.2; `conftest.py` belongs to WP6 and only
imports from `stubs.py`.

`mcp/spec.md` (WP9) outline: 1. scope and the phase-1 surface (tools, resources, hook, skills);
2. protocol decision (A.1) and the accepted SDK mismatches (A.3); 3. deviations from the 1.2 build
spec (B.2 default transport and registration args, B.3 `OPENJEV_MCP_MAX_INFLIGHT` under http, B.5
optional token on loopback, `OJ_INTERNAL`, hook `--defer-allow`); 4. configuration table (B.3);
5. transport and security contract (B.2, B.5); 6. mise contract (F.1); 7. roadmap: phase 2 and 3
(H), with the allowed-roots note and the open items of `TASKS.md`; 8. upgrade procedure (bump the
SDK floor, re-run the A.2 probes as tests, re-record hook fixtures).
