# OpenJev MCP server

Model Context Protocol server for OpenJev. A separate process from OpenJev (:8080) and the UI (:8090), with its own port (8100). It talks to OpenJev over HTTP (`OPENJEV_BASE_URL`), never imports `openjev` or `transformers`, and never loads a model. If OpenJev is down it still starts; tools return `OJ_UNREACHABLE` until it is up.

Distribution `openjev-mcp`, package `openjev_mcp`, entry points `openjev-mcp` (server) and `openjev-hook` (Claude Code hooks).

## Run

```sh
mise run start                   # OpenJev :8080 + UI :8090 + MCP :8100
mise run mcp                     # only the MCP server
mise run status                  # is it running and ready?
mise run logs mcp                # follow the MCP log
mise run stop
```

Endpoint: `http://127.0.0.1:8100/mcp` (Streamable HTTP, stateless). `GET /health` answers without calling OpenJev. Secondary transport: `openjev-mcp --transport stdio`.

## Protocol

Revisions advertised by `server/discover` and `/health`: `2026-07-28` (primary: stateless, per-request `_meta` envelope, `resultType`, `ttlMs`/`cacheScope`), plus legacy `2025-11-25` and `2025-06-18` (`initialize`). `2025-03-26` and `2024-11-05` are served best-effort by the SDK. 2026-07-28 is the latest stable revision as of 2026-10-02 (no newer one exists) and the SDK is `mcp` 2.2.0 (pin `>=2.2,<3`), so there is nothing to upgrade. Not built, by choice: `subscriptions/listen`, server logging, OAuth metadata, `x-mcp-header`, elicitation and sampling. The Tasks extension is built but off by default. Details, the revision-by-revision table and the upgrade procedure: [spec.md](spec.md) sections 2 and 11.

## Surface

| Tool | What it does |
| --- | --- |
| `ask` | Several typed questions (noul, choice, score) about one state |
| `yes_no` | One yes/no claim, P(yes) and band |
| `classify` | Pick one option, with an `other` escape; abstains with `label: null`, `abstained: true`, `top` |
| `score` | Expected level on an ordered scale |
| `filter` | Keep or drop many items against one criterion, packed per request |
| `batch` | The same questions over many rows or a file, resumable, with a review queue |
| `ask_image` | Typed questions about images (needs the `images` extra) |
| `lint` | Check a question schema before use |
| `compile` | Turn an intent into a draft question set and a matching recipe |
| `calibrate` | Fit thresholds on labelled examples or a finished batch; audit store |
| `recipe` | Run a built-in decision recipe (`command_gate`, `done_gate`, `ticket_triage`, ...) |
| `status` | Server health, models and limits |
| `generate` | Plain chat text from `OPENJEV_MCP_CHAT_MODEL` (toolset `all` only) |
| `batch_results` | Read, review, export and compare a finished batch output file |

`OPENJEV_MCP_TOOLSETS=core` serves only `ask`, `yes_no`, `classify`, `score`, `lint`, `status`.

| Kind | Name | What it does |
| --- | --- | --- |
| resource | `openjev://schema`, `openjev://limits` | JSON Schema of the question and answer types; effective limits |
| resource | `openjev://recipes`, `openjev://recipes/{id}` | Recipe index and one recipe |
| resource | `openjev://templates`, `openjev://templates/{id}` | Batch question-set templates |
| resource | `openjev://patterns`, `openjev://guide/authoring` | Verified question patterns; the authoring guide |
| prompt | `start_batch`, `review_batch` | Plan and run a batch job; work its review queue |
| prompt | `author_question`, `audit_question`, `explain_answer` | Write a question; plan a calibration; read the numbers |
| skill | 11 skills in `mcp/skills/` | `openjev-decisions` (start here), `-question-authoring`, `-triage-routing`, `-agent-gates`, `-code-checks`, `-dispatch`, `-retrieval-relevance`, `-multistep`, `-data-records`, `-ui-vision`, `-calibration` |
| hook | `openjev-hook pretooluse`, `stop`, `userprompt`, `posttooluse` | Gate Bash commands, claims of "done", skill choice, fetched content |
| cli | `openjev check <recipe>`, `openjev filter` | Run a recipe or a filter from a shell; JSON out, exit code |

## Register in Claude Code

HTTP (default):

```sh
claude mcp add --transport http openjev http://127.0.0.1:8100/mcp
# with OPENJEV_MCP_TOKEN set:
claude mcp add --transport http openjev http://127.0.0.1:8100/mcp --header "Authorization: Bearer $OPENJEV_MCP_TOKEN"
```

`.mcp.json`, HTTP:

```json
{"mcpServers": {"openjev": {"type": "http", "url": "http://127.0.0.1:8100/mcp"}}}
```

`.mcp.json`, stdio (Claude Code starts the process itself; no `mise run mcp` needed):

```json
{"mcpServers": {"openjev": {"command": "<repo>/.venv/bin/openjev-mcp", "args": ["--transport", "stdio"], "env": {"OPENJEV_BASE_URL": "http://127.0.0.1:8080"}}}}
```

Tools appear as `mcp__openjev__<name>`.

## Settings

Environment of the MCP process; set in front of a task, e.g. `OPENJEV_MCP_PORT=8101 mise run mcp`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPENJEV_BASE_URL` | `http://127.0.0.1:8080` | OpenJev server |
| `OPENJEV_API_KEY` | unset | sent to OpenJev as `Authorization: Bearer <key>` |
| `OPENJEV_ORIGIN_SECRET` | unset | sent as `X-Origin-Secret` when set |
| `OPENJEV_MCP_MODEL` | `openjev-latest` | default decide model |
| `OPENJEV_MCP_TIMEOUT_MS` | `30000` | base per-request timeout (scaled) |
| `OPENJEV_MCP_MAX_INFLIGHT` | `1` with stdio, `4` with http | concurrent OpenJev requests from this process |
| `OPENJEV_MCP_MAX_INFLIGHT_BATCH` | `4` | cap of `batch` `concurrency` (reported in `status`) |
| `OPENJEV_MCP_RETRIES` | `2` | retries for retryable errors |
| `OPENJEV_MCP_LOG` | unset | JSONL audit log path |
| `OPENJEV_MCP_LOG_STATES` | `0` | `1` includes raw states in the audit log |
| `OPENJEV_MCP_TOOLSETS` | `all` | `all` or `core` (six tools) |
| `OPENJEV_MCP_BAND` | `0.2,0.8` | default noul band `no_at,yes_at` |
| `OPENJEV_MCP_ROOTS` | unset | `:`-separated extra roots for files that tools read and write (the working directory is always one) |
| `OPENJEV_MCP_TRANSPORT` | `http` | `http` or `stdio` (`--transport` wins) |
| `OPENJEV_MCP_HOST` | `127.0.0.1` | HTTP bind address (`--host` wins) |
| `OPENJEV_MCP_PORT` | `8100` | HTTP port (`--port` wins) |
| `OPENJEV_MCP_TOKEN` | unset | bearer token clients must send to `/mcp` |
| `OPENJEV_MCP_ALLOWED_HOSTS` | unset | extra `Host` values, comma-separated, `host:port` or `host:*` |
| `OPENJEV_MCP_ALLOWED_ORIGINS` | unset | extra `Origin` values, comma-separated |
| `OPENJEV_MCP_MAX_BODY_BYTES` | `4194304` | max request body to `/mcp` |
| `OPENJEV_MCP_DEBUG` | `0` | `1` sets the stderr log level to DEBUG |
| `OPENJEV_MCP_RECIPES` | unset | extra directory of recipe JSON files (must exist) |
| `OPENJEV_MCP_ROUTING` | `on` | `on` or `off`; recipe routing |
| `OPENJEV_MCP_FETCH` | `off` | `on` allows `ask_image` https URL fetch |
| `OPENJEV_MCP_TASKS` | `off` | `on` enables the Tasks extension (2026-07-28 sessions) |
| `OPENJEV_MCP_AUDIT_DIR` | `./openjev-audits` | calibrate audit records; relative to the first root |
| `OPENJEV_MCP_CHAT_MODEL` | `diffusiongemma-26b` | model for `generate` |

Exit codes: `0` clean stop, `2` configuration error, `3` bind failure (the port is named in the message).

## Security

- Binds `127.0.0.1` by default. Any other `OPENJEV_MCP_HOST` needs `OPENJEV_MCP_TOKEN`, otherwise the server exits 2.
- `Host` and `Origin` are validated (DNS rebinding, browser CSRF): bad `Origin` gets 403, bad `Host` 421. Clients that send no `Origin` pass. Extend with `OPENJEV_MCP_ALLOWED_HOSTS` / `OPENJEV_MCP_ALLOWED_ORIGINS`.
- With `OPENJEV_MCP_TOKEN` set, `/mcp` needs `Authorization: Bearer <token>` (401 otherwise). On a loopback bind the token is optional.
- The inbound token is never forwarded to OpenJev. `OPENJEV_API_KEY` and `OPENJEV_ORIGIN_SECRET` come from the environment only and are never logged or put in results.
- No OAuth, no sessions, no server-initiated requests.

## Privacy

States sent to a tool reach OpenJev and may contain secrets or personal data. A hosted `OPENJEV_BASE_URL` sends them off the machine; assume it logs them. A local OpenJev started with `OPENJEV_LOG_LEVEL=debug` logs full request and response bodies, rejected ones included; `status` warns when OpenJev reports it. The MCP audit log (`OPENJEV_MCP_LOG`) stores hashes only unless `OPENJEV_MCP_LOG_STATES=1`. Diagnostics go to stderr, never stdout.

## Hooks

Hooks cannot call MCP tools, so `openjev-hook` runs the recipes directly against OpenJev (no MCP server needed). Four events:

| Event | Command | Recipe | On failure |
| --- | --- | --- | --- |
| PreToolUse (Bash) | `openjev-hook pretooluse [--profile strict\|lenient] [--unattended] [--timeout-ms N] [--task TEXT] [--defer-allow]` | `command_gate` | fails closed: `ask` (`deny` with `--unattended`) |
| Stop | `openjev-hook stop [--max-blocks 2] [--timeout-ms N]` | `done_gate` | fails open: the stop is allowed |
| UserPromptSubmit | `openjev-hook userprompt --roster skills.json [--threshold 0.8] [--timeout-ms N]` | `skill_selection` | fails open: no hint |
| PostToolUse | `openjev-hook posttooluse [--screen "WebFetch,mcp__*"] [--timeout-ms N]` | `injection_screen` | fails to uncertain, with a note in the context |

Claude Code `settings.json` (or `.claude/settings.json`):

```json
{"hooks": {
 "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "<repo>/.venv/bin/openjev-hook pretooluse --profile strict", "timeout": 15}]}],
 "Stop": [{"hooks": [{"type": "command", "command": "<repo>/.venv/bin/openjev-hook stop --max-blocks 2", "timeout": 15}]}],
 "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "<repo>/.venv/bin/openjev-hook userprompt --roster .claude/skill-roster.json", "timeout": 15}]}],
 "PostToolUse": [{"matcher": "WebFetch|mcp__.*", "hooks": [{"type": "command", "command": "<repo>/.venv/bin/openjev-hook posttooluse --screen 'WebFetch,mcp__*'", "timeout": 15}]}]}}
```

Timeout rule: the hook `timeout` in `settings.json` is the end-to-end budget. `--timeout-ms` (default 10000) must stay at least 1000 ms below it so the CLI, not the harness, decides; on expiry the failure mode above applies. Deterministic rules decide without a read whenever they match. The roster for `userprompt` is `[{"id", "description"}]` or `{id: description}`.

`pretooluse` flags: `--profile` picks the rule and threshold set of `command_gate`; `--unattended` makes failures deny; `--task` (else `OPENJEV_HOOK_TASK`, else the last user prompt) is the task context; `--defer-allow` prints nothing on allow so Claude Code's own permission flow decides. Non-Bash tools get no opinion. `stop` blocks at most `--max-blocks` times in a row, then lets the stop through.

## Batch jobs

`batch` sends one request per row and keeps the job in a JSONL file you name, so any call can be lost and resumed. Read from a file (`items_file`: CSV, TSV, JSON, JSONL, plain lines, a Playground export) or inline `items`; start with `dry_run: true` (no reads: import summary, 3-state preview, estimate).

1. Run with `output_path` (a new `.jsonl`) and `max_items_per_call`.
2. Cursor loop: call again with identical arguments and `cursor` set to the previous `next_cursor` until it is `null`. `concurrency`, `max_items_per_call`, `time_budget_s`, `detail`, `max_inline_results` and `export` may change between calls; anything else is refused as "arguments changed since this cursor". `status.stopped_reason` says why a call ended (`complete`, `max_items_per_call`, `time_budget`, `backpressure`, `error_abort`).
3. After an interruption (cancelled call, closed session, no result): same arguments, same `output_path`, `resume: true`, no cursor. Finished rows are skipped. `retry_errors: true` and `only_ids` re-run chosen rows.
4. `batch_results` reads a finished file without new reads: `view: "review"` (least confident first), `"stats"`, filtered `rows`, `export` (`csv`, `markdown`, `jsonl`, `ojui-batch`) and `compare_to`.

Concurrency: `concurrency` 2-4 helps only on vLLM; on the MLX backend requests are served one at a time, so it brings no speedup (the lint warns, W405).

Paths: absolute paths are always accepted inside the allowed roots (the server's working directory and `OPENJEV_MCP_ROOTS`). Relative paths resolve against the first root under stdio only; under the HTTP daemon (the default) a relative path is refused, because the daemon's working directory is not your project. Pass absolute paths. Symlinks, dot-directories and dotfiles are not written.

Privacy: output files hold the state text of every row (exports need it) and the answers, with the permissions of your account. Pass `include_state: false` to keep only hashes; keep job files out of version control. Exports are created new; an existing path is refused. The skill `openjev-data-records` has the full walkthrough.

## Tasks extension

`OPENJEV_MCP_TASKS=on` enables the MCP Tasks extension (`io.modelcontextprotocol/tasks`) for `batch` and `calibrate`. It applies only to `2026-07-28` sessions whose client lists the extension in its capabilities; every other client gets the normal synchronous result. The task store lives in the process and dies with it. The cursor loop above stays the portable way to run long jobs.

## Images

`ask_image` and `batch` `images` need Pillow, an optional extra: `pip install -e 'mcp[images]'` (or `pip install Pillow`). Without it they fail with `OJ_INVALID_INPUT` and that hint; everything else works. Images come from a local path inside the allowed roots, a `data:` URL or base64; https URLs are fetched only with `OPENJEV_MCP_FETCH=on` (private addresses are refused).

## Skills

```sh
mkdir -p .claude/skills && cp -R mcp/skills/* .claude/skills/        # or ln -s "$PWD"/mcp/skills/* .claude/skills/
```

Start with `openjev-decisions`; it routes to the other ten by task.

## Measured (real model, 2026-10-02)

Against OpenJev on :8080 (MLX, one GPU), full numbers in [docs/live-runs.md](docs/live-runs.md): a read tool call takes about 150-220 ms on average (`yes_no` 7.1 req/s sequential, 9.3 req/s at concurrency 4); `filter` p50 71 ms; a 200-row `batch` (two questions per row, concurrency 2) ran 57-64 s, 3.1-3.5 req/s, 200 unique rows, no gaps, and an interrupted-then-resumed run lost nothing. The `pretooluse` hook takes 0.5-0.8 s when the model decides and 70-80 ms when a rule does. Live checks are marked `live` and skipped by default: `pytest --live mcp/tests/live`, or `.venv/bin/python mcp/tests/live/run_live.py --full --quick` (port from `OJ_LIVE_PORT`).

## Known gaps

- A legacy-era (2025) client cancel does not stop a running `batch`; closing the request does, and `resume: true` continues. 2026-07-28 clients cancel by disconnect.
- Four recipe cases (`done_gate` gate-03, `skill_selection` sel-19, `semantic_lint` secrets pair, `entity_match` cat-01) pass as raw cases but miss through the recipe's own questions; the chat model sometimes returns one empty completion (`generate` retries once).
- `/v1/limits` does not exist on OpenJev yet, so limits come from defaults; no CI workflow; Claude Code hook fixtures are hand-built; auth is a static bearer token only (no OAuth).
- The `think` option is not reproducible; never cache it. `command_gate` is a screen, not a security boundary.

Roadmap and open items: [spec.md](spec.md) section 10.

## Tests

```sh
.venv/bin/python -m pytest -q mcp/tests          # or: mise run test
```

No model and no running OpenJev: tests use a stub engine.

`mise run test` runs the same suite.

A separate live suite drives Claude Code headless (`claude -p`, your subscription login) against this server and the running OpenJev, 100 cases in 10 groups. It spends subscription usage, starts its own MCP on a port in 8200-8299, never starts or stops services, and is not part of `mise run test`:

```sh
mise run test-claude-live        # see tests/claude_live/README.md
```

Details: [tests/claude_live/README.md](tests/claude_live/README.md).

More: [spec.md](spec.md) · [docs/architecture.md](docs/architecture.md) · [build spec](../docs/mcp-skill-spec/OPENJEV_MCP_SKILLS_SPEC.md)
