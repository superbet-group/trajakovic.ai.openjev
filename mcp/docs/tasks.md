# openjev-mcp: phase 1 task breakdown

Status: plan, 2026-10-02. Implements `docs/mcp-skill-spec/TASKS.md` phase 1 (1.1-1.32) against the
interface contract in `mcp/docs/architecture.md` (cited "arch X.n"). The build spec
`docs/mcp-skill-spec/OPENJEV_MCP_SKILLS_SPEC.md` v1.2 is cited "spec 2.x" with line ranges, so a
package reads only what it needs.

## Rules for every package

- Own only the files listed for the package. A defect in a file you do not own is reported (file,
  failing call, observed vs expected, the change needed), never fixed in place.
- Never create `mcp/__init__.py` or `mcp/tests/__init__.py`. `from __future__ import annotations`,
  Python 3.10 syntax, anyio primitives in async code. Comments and names terse, in the repo's style.
- Tests run from the repo root: `.venv/bin/python -m pytest -q mcp/tests/<file>`. pytest's rootdir
  is `mcp/` (its `pyproject.toml`), `--import-mode=importlib`, `pythonpath = ["tests"]`, so tests
  `import stubs` / `import rpc` directly. Async tests use `@pytest.mark.anyio`.
- No model, no real OpenJev: tests use `mcp/tests/stubs.py` (stub engine in `openjev.api.create_app`,
  `httpx.MockTransport` faults, captured replays). Never run `mise run start`, `default`, `restart`
  or `install`. A real process started for a smoke test binds a spare port (never 8080/8090/8100)
  and is killed in a `finally`.
- No commits, no `git checkout`/`stash`/`reset`. `docs/mcp-skill-spec/` is read-only.

## Deviations from arch section I (the package split)

1. `tools/__init__.py` holds only the types (`ToolContext`, `ToolSpec`, `Handler`, `UnknownTool`,
   heavy imports under `TYPE_CHECKING`). `TOOLS`, `tools_for` and `call_tool` (arch D.13) live in
   `tools/dispatch.py`. Avoids the import cycle between the registry and the handlers.
2. `recipes/__init__.py` is empty; callers import `openjev_mcp.recipes.engine` directly (keeps
   `import openjev_mcp.hook` light).
3. `tests/stubs.py` holds arch G.1 only (skeleton). The protocol fixtures of arch G.2
   (`rpc_session`, `mcp_client`, `http_app_client`) live in `tests/rpc.py` (server package).
4. `openjev_mcp/__init__.py` also exports `TOOL_NAMES = ("ask", "yes_no", "classify", "score",
   "lint", "status")`, the spec 2.5 order; the skills test and the dispatcher assert against it.
5. `test_mcp_packaging.py` moves to the e2e package (it needs every entry point). The skeleton keeps a
   `test_mcp_dist.py` (no `transformers` in the requirements, `openjev --version`).
6. `mcp/pyproject.toml` adds `pythonpath = ["tests"]` to the pytest options.
7. Root `README.md` belongs to the docs package; `.gitignore` to the mise package.
8. `mcp/spec.md` is written last, from the code as built, by its own package.
9. `hook.py` imports `openjev_mcp.http` (and so `httpx`) lazily, only when the rules did not decide,
   to keep the rule path inside the 300 ms budget.

## Packages and waves

| Wave | Package | Depends on |
|---|---|---|
| 1 | P01 skeleton, config, errors, wire, stub OpenJev | - |
| 2 | P02 HTTP client, error mapping, limits | P01 |
| 2 | P03 schemas and argument validation | P01 |
| 2 | P04 derived fields, envelope, progress, audit | P01 |
| 2 | P05 recipe primitives (expr, rules, shell, template) | P01 |
| 2 | P06 skills and docs (SKILL.md x2, mcp/README.md, root README.md) | P01 |
| 3 | P07 lint (+ lint tool handler) | P02, P03, P04 |
| 3 | P08 status tool and resources | P02, P03, P04 |
| 3 | P09 recipe engine and built-in `command_gate` | P02, P04, P05 |
| 4 | P10 read tools and dispatcher | P02, P03, P04, P07, P08 |
| 4 | P11 `openjev-hook` CLI | P09 |
| 5 | P12 MCP server and transports | P10 |
| 6 | P13 protocol conformance tests | P12 |
| 6 | P14 mise integration | P12 |
| 7 | P15 integration and e2e | P06, P11, P12, P13, P14 |
| 8 | P16 `mcp/spec.md` | P15 |

Critical path: P01 -> P02/P03/P04 -> P07 -> P10 -> P12 -> P13 -> P15 -> P16.

## Package contents

### P01 Skeleton (TASKS 1.1, 1.2)

`mcp/pyproject.toml`, `openjev_mcp/__init__.py`, `__main__.py`, `config.py`, `errors.py`, `wire.py`,
`cli.py`, `tools/__init__.py` (types), `recipes/__init__.py` (empty), `tests/conftest.py`,
`tests/stubs.py`, `tests/test_mcp_config.py`, `tests/test_mcp_wire.py`, `tests/test_mcp_errors.py`,
`tests/test_mcp_stubs.py`, `tests/test_mcp_dist.py`. Installs the package editable into `.venv`
(`pip install -e "./mcp[test]"`). Arch A.1, B.3, C, D.1-D.4, D.13 (types), D.28, G.1.

### P02 HTTP client, mapping, limits (1.4, 1.5, 1.9, part of 1.32)

`mapping.py`, `http.py`, `limits.py`, `tests/test_mcp_mapping.py`, `tests/test_mcp_http_client.py`,
`tests/test_mcp_limits.py`. The full 2.4 error matrix (incl. "Errors added in 1.2") at client
level, against `openjev.api.create_app` with the stub engine plus `httpx.MockTransport`. Retry
policy, `retry-after` + jitter, deadline, in-flight cap, cancellation releasing the semaphore,
`server-timing` parsing, `/v1/limits` 200/404. Arch D.5-D.7.

### P03 Schemas and validation (1.7, 1.29 schema half, 1.32 optional fields)

`schemas.py`, `validate.py`, `tests/test_mcp_schemas.py`, `tests/test_mcp_validate.py`. The 2.2
`$defs` verbatim, the inliner, input/output schemas for the six tools, the `openjev://schema`
payload, Draft 2020-12 validation to `OJ_INVALID_INPUT`. Arch D.8, D.9.

### P04 Derived fields, envelope, progress, audit (1.6, 1.8, 1.30 emitter)

`derive.py`, `envelope.py`, `progress.py`, `audit.py`, `tests/test_mcp_derive.py`,
`tests/test_mcp_envelope.py`, `tests/test_mcp_progress.py`, `tests/test_mcp_audit.py`. Arch
D.10, D.12, D.16, D.17.

### P05 Recipe primitives (1.16-1.19)

`recipes/expr.py`, `recipes/rules.py`, `recipes/shell.py`, `recipes/template.py`, and
`tests/test_mcp_expr.py`, `test_mcp_rules.py`, `test_mcp_shell.py`, `test_mcp_template.py`. The
2.18 grammar (no `eval`), RE2 rules with load-time rejection, the shell split, JSON-string
templating. Pure. Arch D.21-D.24.

### P06 Skills and docs (1.22, 1.23, 1.24)

`mcp/skills/openjev-decisions/SKILL.md`, `mcp/skills/openjev-question-authoring/SKILL.md`,
`mcp/README.md`, root `README.md` (tasks rows, settings rows, a short MCP paragraph),
`tests/test_mcp_skills.py`. Spec 4.1 phase-1 rows only, 4.2 without `compile`. Arch B.2, B.3, B.5,
F.2 (README part), H.

### P07 Lint (1.11, 1.32 lint)

`lint.py`, `tools/lint_tool.py`, `tests/test_mcp_lint.py`. Every phase-1 E and W code with a
positive and a negative fixture, autofix, estimate, `emit` snippets, limit-dependent codes following
`limit_source`, no network I/O. Arch D.11, D.15 (lint).

### P08 Status and resources (1.10, 1.9 resource, 1.7 resource, 1.32 status)

`tools/status.py`, `resources.py`, `tests/test_mcp_status.py`, `tests/test_mcp_resources.py`.
Arch D.15 (status), D.18.

### P09 Recipe engine and `command_gate` (1.20)

`recipes/engine.py`, `recipes/builtin/command_gate.json`, `tests/test_mcp_command_gate.py`. Spec
2.18 recipe document with the questions verbatim (both profiles), the 6.6 rule lists, replay of
`ex-gate-deny`/`ex-gate-allow`, fail-closed fallback. Arch D.25.

### P10 Read tools and dispatcher (1.12-1.15, 1.6, 1.8, 1.32 ask/meta/timeouts)

`tools/read.py`, `tools/dispatch.py`, `tests/test_mcp_tools_read.py`,
`tests/test_mcp_tools_replay.py`, `tests/test_mcp_dispatch.py`. `ask`, `yes_no`, `classify`,
`score`, the `tools/call` pipeline of arch E. Arch D.13 (registry, now `dispatch.py`), D.14, E.

### P11 `openjev-hook` (1.21)

`claude_hooks.py`, `hook.py`, `tests/test_mcp_hook.py`, `tests/fixtures/claude_code/` (recorded
PreToolUse payloads). Fail closed, `--unattended`, `--defer-allow`, p95 overhead < 300 ms, import
isolation. Arch D.26, D.27.

### P12 MCP server and transports (1.3, 1.30/1.31 wiring)

`server.py`, `http_app.py`, `tests/rpc.py`, `tests/test_mcp_server.py`,
`tests/test_mcp_http_transport.py`. Low-level SDK `Server`, both eras, `server/discover` override,
cache hints, Streamable HTTP on `/mcp` (stateless), Host/Origin checks, bearer token, `/health`,
stdio, CLI and exit codes. Arch A, B, D.19, D.20, G.2.

### P13 Protocol conformance (1.26-1.31)

`tests/test_mcp_protocol.py`, `tests/test_mcp_cancel_progress.py`, `tests/test_mcp_stdio.py`. Tests
only: spec 2.0.1 rules for 2026-07-28 and legacy `initialize` sessions (2025-11-25, 2025-06-18),
expected values from the arch A.2 probe table and the A.3 accepted mismatches.

### P14 mise integration (1.25, user requirement 4)

`mise-tasks/mcp` (new), `mise-tasks/lib/common.sh`, `mise-tasks/start`, `stop`, `status`, `logs`,
`install`, `test`, `.gitignore`. `mise run start` (alias `default`) starts OpenJev, UI and MCP with
the same install check, port preflight, pid/log files and readiness wait; `stop`/`status`/`logs`/
`restart` cover MCP. Arch F. `mise run test` runs `mcp/tests` (stands in for the CI job of 1.25:
the repo has no CI).

### P15 Integration and e2e (1.1 entry points, phase-1 exit in CI form)

`tests/test_mcp_packaging.py`, `tests/test_mcp_e2e.py`. A stub OpenJev under real uvicorn on a spare
port, the MCP server as a real subprocess on another spare port, the SDK client over Streamable
HTTP and over stdio, every phase-1 tool and resource, the token mode, and the hook against the same
stub. Runs the whole `mcp/tests` suite.

### P16 `mcp/spec.md` (user requirement 5)

The MCP server's own spec for future upgrades, written from the code as built: scope and phase-1
surface, protocol decision and SDK mismatches, deviations from build spec 1.2, configuration,
transport and security contract, mise contract, roadmap (phase 2/3, open items), upgrade procedure.

## TASKS.md coverage

| TASKS id | Package(s) |
|---|---|
| 1.1 distribution | P01 (install, deps), P15 (entry points `--help`) |
| 1.2 config | P01 |
| 1.3 stdio server, tools/list | P12 (server), P13 (protocol tests) |
| 1.4 HTTP client | P02 |
| 1.5 error mapping | P02 (matrix), P04 (error envelope), P10 (envelope through the pipeline) |
| 1.6 result envelope | P04, P10 (every replay validates against `outputSchema`) |
| 1.7 schemas | P03, P08 (`openjev://schema`), P13 (schema walk over the wire) |
| 1.8 derived fields | P04, P10 (replays to 4 decimals) |
| 1.9 limits | P02, P08 (`openjev://limits`) |
| 1.10 status | P08 |
| 1.11 lint | P07 |
| 1.12-1.15 ask, yes_no, classify, score | P10 |
| 1.16 expression parser | P05 |
| 1.17 RE2 rules | P05 |
| 1.18 shell split | P05 |
| 1.19 templates | P05 |
| 1.20 built-in `command_gate` | P09 (CI part; the 14/14 live run is human-run) |
| 1.21 `openjev-hook pretooluse` | P11, P15 (against a real stub server) |
| 1.22, 1.23 skills | P06 |
| 1.24 docs | P06 |
| 1.25 CI job | P14 (`mise run test`; no CI in the repo, arch H) |
| 1.26 discover / initialize | P12, P13 |
| 1.27 per-request `_meta` | P12, P13 |
| 1.28 caching fields | P12, P13 |
| 1.29 argument validation path | P03, P10, P13 |
| 1.30 cancellation, progress | P02 (semaphore), P04 (emitter), P12 (wiring), P13 (tests) |
| 1.31 stdio hygiene | P01 (roots), P12 (stderr logging), P13 (stdout test) |
| 1.32 1.2 corrections | P02 (mapping rows, server-timing), P03 (optional fields), P07 (lint), P08 (status), P10 (timeout scaling, `Meta.body_hashes`) |
| user: own port, mise run mcp, start covers MCP | P12, P14 |
| user: `mcp/spec.md` | P16 |

Phase-1 exit also needs one live run of `00-spec-examples.json` and `03-agent-tool-call-gate.json`
(14/14 through the hook). Those are human-run against the real model and are not part of any
package.
