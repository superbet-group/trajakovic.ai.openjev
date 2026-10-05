# Connecting a session to the OpenJev MCP server

## Contents
- What must be running
- Three ways to connect
- After connecting: verify
- Tool names you will see
- Allowed roots and paths
- Toolsets
- Symptoms and fixes

## What must be running

Two local processes, both started by the user or operator, never by the session:

- OpenJev itself (the model server, default `http://127.0.0.1:8080`).
- The OpenJev MCP server (`openjev-mcp`), which exposes the 14 tools. HTTP default:
  `http://127.0.0.1:8100/mcp`. Its liveness check, which does not call OpenJev, is
  `GET http://127.0.0.1:8100/health`.

Do not start, stop or restart either without the user's go-ahead. Give the user the command and
wait.

## Three ways to connect

Pick the first that fits the user's setup. Each ends with a restart or `/mcp` reconnect.

1. HTTP server already running (default): 

   ```
   claude mcp add --transport http openjev http://127.0.0.1:8100/mcp
   ```

   With a token set on the server (`OPENJEV_MCP_TOKEN`):

   ```
   claude mcp add --transport http openjev http://127.0.0.1:8100/mcp --header "Authorization: Bearer $OPENJEV_MCP_TOKEN"
   ```

   File access is limited to the daemon's working directory plus the roots the operator set in
   `OPENJEV_MCP_ROOTS` (colon-separated). A user project outside them cannot be read by path.

2. stdio, started by Claude Code per session:

   ```
   claude mcp add openjev -- openjev-mcp --transport stdio
   ```

   The process inherits the project directory, so the project is an allowed root and relative
   paths resolve against it. Set `OPENJEV_BASE_URL` in its environment if OpenJev is not on
   `http://127.0.0.1:8080`. Requires `openjev-mcp` on `PATH` (installed from the OpenJev MCP package).

3. Plugin connector (HTTP, from the `openjev` marketplace):

   ```
   claude plugin install openjev-mcp@openjev
   ```

   The URL comes from the environment variable `OPENJEV_MCP_URL` and defaults to
   `http://127.0.0.1:8100/mcp`. Use this or option 1, not both, or the tools appear twice.

If none of these is possible (no server at all, a locked-down machine, a path refused as outside the roots), prepare data inline: pass
`items` in `batch`, `examples` in `calibrate`, and `data_url` images in `ask_image`, which need no
file access.

## After connecting: verify

1. The user restarts Claude Code, or reconnects the server with `/mcp`.
2. Load the tool schemas if they are deferred (ToolSearch with `select:mcp__openjev__status,...`).
3. Call `mcp__openjev__status`. Require `healthy: true`.
4. Read once and remember: `decide_models` (valid values for `options.model`), `limits` (questions
   256, batch items per call, images), `capabilities` per model (images, think, sequential),
   `limit_source` and `warnings`.

If `limit_source` is `default`, OpenJev did not report its own limits, so limit lint findings are
warnings, not errors. This is normal on older OpenJev builds.

Optional: `mcp__openjev__status` with `probe: true` runs one small read and reports latency.

## Tool names you will see

| Connected via | Tool name |
|---|---|
| `claude mcp add ... openjev` (HTTP or stdio) | `mcp__openjev__ask` |
| plugin `openjev-mcp` | `mcp__plugin_openjev-mcp_openjev__ask` |

Skills write `mcp__openjev__<tool>`. Substitute the plugin form when that is what the tool list
shows. Resources are read with the MCP resource reader on server `openjev`:
`openjev://guide/authoring`, `openjev://schema`, `openjev://limits`, `openjev://recipes`,
`openjev://recipes/{id}`, `openjev://templates`, `openjev://templates/{id}`,
`openjev://patterns`, `openjev://audits/{question_hash}`.

## Allowed roots and paths

Every path argument (`items_file.path`, `items_file.also`, `output_path`, `export.path`,
`case_file`, `store`, `compare_to`, image `path`) must be:

- absolute, because the HTTP daemon's working directory is not your project;
- inside an allowed root (the server's working directory plus `OPENJEV_MCP_ROOTS`);
- for files the server writes: new, not a symlink, not under a dot-directory, not a dotfile, with
  the right extension (`.jsonl` for batch output, `.csv` / `.md` / `.json` for exports).

The refusal names the roots, for example `path outside the allowed roots` with a hint
`allowed roots: <dir>`. Read that hint once, then pick one fix:

- write the file inside a listed root, or
- ask the operator to restart the server with `OPENJEV_MCP_ROOTS=/abs/path/to/project`, or
- switch to stdio (option 2), or
- only then, for small data, send it inline (`items`); a file inside the roots always uses `items_file`.

Do not loop on a refused path.

## Toolsets

`OPENJEV_MCP_TOOLSETS=core` serves only six tools: `ask`, `yes_no`, `classify`, `score`, `lint`,
`status`. If `batch`, `filter`, `recipe`, `compile`, `calibrate`, `batch_results` or `ask_image`
are missing but `ask` works, the server runs the core set; tell the user, who can restart it with
the default `all` toolset. The `generate` tool exists only in the `all` set.

## Symptoms and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| No `mcp__openjev__*` tools at all | server not registered, or session not restarted | give the `claude mcp add` command; restart or `/mcp` |
| Tools deferred, call fails with a schema error | schemas not loaded | ToolSearch `select:` the tool names first |
| `OJ_UNREACHABLE` | OpenJev (:8080) is down or `OPENJEV_BASE_URL` is wrong | tell the user; do not start it |
| `healthy: false` in status | OpenJev reachable but unhealthy | show `warnings` to the user |
| MCP server unreachable, connection refused on :8100 | MCP daemon not running | give the user the start command; wait |
| HTTP 401 | server requires `OPENJEV_MCP_TOKEN` | re-add with `--header "Authorization: Bearer ..."` |
| HTTP 403 or 421 | bad `Origin` or `Host` for the endpoint | operator sets `OPENJEV_MCP_ALLOWED_HOSTS` / `OPENJEV_MCP_ALLOWED_ORIGINS` |
| Path error naming allowed roots | file outside the roots, or relative path | see Allowed roots and paths |
| `OJ_INVALID_INPUT` with a hint `expected: ...` | argument shape wrong | fix from the hint; run `lint` for question sets |
| `ask_image` fails mentioning an optional extra | Pillow missing on the server | tell the user to install it; use text states meanwhile |
| Reads fail with `OJ_AUTH` | OpenJev itself needs a key | tell the user; `status` shows `auth` |
| `warnings` says limits are the documented defaults | OpenJev has no limits endpoint (`limit_source: default`) | informational |
| Tools appear twice with different prefixes | both `claude mcp add` and the plugin connector installed | remove one |
