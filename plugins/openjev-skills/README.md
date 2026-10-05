# openjev-skills

Claude Code skills that teach a session how to prepare data for the OpenJev MCP server: the state format, the question formats (yes/no `noul`, one-of-N `choice`, ordinal `score`), what each of the 14 tools takes and returns, how one tool's output feeds the next, and how to build batch items files.

OpenJev answers typed questions about a state you send with calibrated probabilities. The skills never call anything themselves; they guide the session that does.

## Prerequisites

- Claude Code 2.1 or newer (`claude --version`).
- A running OpenJev MCP server. Default endpoint: `http://127.0.0.1:8100/mcp`. Starting it is the operator's job (the repo's `mise run mcp`); the skills never start or restart services.
- The server must be registered in your Claude Code (see "Connect the MCP server" below). The skills work without it only as reading material.

## Install

From a clone of the repository (replace the path), or from a Git host once published:

```sh
claude plugin marketplace add /path/to/openjev          # or: owner/repo
claude plugin install openjev-skills@openjev
```

Or inside Claude Code: `/plugin marketplace add /path/to/openjev`, then `/plugin install openjev-skills@openjev`.

The marketplace is named `openjev` and lists two plugins:

| plugin | contents | install |
|---|---|---|
| `openjev-skills` | the 12 skills below, no server config | `claude plugin install openjev-skills@openjev` |
| `openjev-mcp` | opt-in connector: registers the HTTP server `openjev` | `claude plugin install openjev-mcp@openjev` |

They are separate on purpose: if you already ran `claude mcp add openjev ...`, installing only the skills avoids a duplicate server.

## Connect the MCP server

Pick one. After any of them, restart Claude Code or reconnect via `/mcp`.

1. Plain registration (recommended, tools are named `mcp__openjev__<tool>`):

   ```sh
   claude mcp add --transport http openjev http://127.0.0.1:8100/mcp
   # server started with OPENJEV_MCP_TOKEN:
   claude mcp add --transport http openjev http://127.0.0.1:8100/mcp --header "Authorization: Bearer $OPENJEV_MCP_TOKEN"
   ```

2. The `openjev-mcp` plugin: `claude plugin install openjev-mcp@openjev`. It reads the URL from `OPENJEV_MCP_URL` (default `http://127.0.0.1:8100/mcp`), so export that before starting Claude Code for a non-default host. Plugin-bundled tools are named `mcp__plugin_openjev-mcp_openjev__<tool>` (checked on Claude Code 2.1.289); the skills say `mcp__openjev__<tool>` and the hub skill covers the other prefix. Do not use both options at once.

3. stdio, where Claude Code starts the server itself and your project directory becomes the allowed root:

   ```sh
   claude mcp add openjev -- openjev-mcp --transport stdio
   ```

## Allowed roots

Every tool that reads or writes a file (`batch`, `batch_results`, `calibrate`, `ask_image`) needs an absolute path inside the server's allowed roots: its own working directory plus `OPENJEV_MCP_ROOTS` (`:`-separated). Under the HTTP daemon your project is usually not one of them, so a path error is expected until you do one of:

- use stdio (option 3), which makes the project directory the root;
- ask the operator to start the daemon with `OPENJEV_MCP_ROOTS=/path/to/your/project`;
- send inline `items`, inline `examples`, or `data:` URL images instead of paths.

The server refuses writes under dot-directories (such as `.tmp/`), dotfiles, symlinks and existing export files.

## Skills

Invoke explicitly as `/openjev-skills:<name>`, or let Claude pick one from its description.

| skill | use it for |
|---|---|
| `openjev-data-prep` | Hub. Connecting, the data model, every tool's input and output, chaining, state rendering, items files, pitfalls. Start here when unsure. |
| `openjev-decisions` | A single yes/no, classification, scoring, gating or judgement call through typed reads; routes to the other skills. |
| `openjev-question-authoring` | Writing, linting and compiling questions that OpenJev reads reliably. |
| `openjev-triage-routing` | Routing and labelling tickets, emails, messages and issues. |
| `openjev-agent-gates` | Gating risky agent actions and claims: command checks, done claims, injection screening. |
| `openjev-code-checks` | Semantic checks of code, diffs, docs, replies and citations. |
| `openjev-dispatch` | Selecting which skill, tool, function or model tier handles a request (`skill_selection`, `typed_call`, `model_routing`). |
| `openjev-retrieval-relevance` | Shortlisting what matters among many items (files, log lines, passages) with `filter`. |
| `openjev-multistep` | One tick of a sequential process: which file, link or node to open next toward a goal. |
| `openjev-data-records` | Labelling, extracting and scoring records, including batch jobs over CSV or JSONL with resume, review and export. |
| `openjev-ui-vision` | Screenshots and images with `ask_image`. |
| `openjev-calibration` | Fitting and auditing thresholds on labelled examples with `calibrate`. |

## Verify

1. `/plugin` lists `openjev-skills` as enabled; typing `/openjev-skills:` shows the 12 skills.
2. `/mcp` shows the server `openjev` as connected.
3. Ask Claude to call `mcp__openjev__status`. It must return `healthy: true`. If the tools are missing or the call fails, run the Connect steps in `openjev-data-prep`.

## Local development in the repository

```sh
mise run skills-link          # relative symlinks .claude/skills/openjev-* (gitignored)
claude --plugin-dir plugins/openjev-skills    # alternative, loads as /openjev-skills:<name>
claude plugin validate --strict .             # marketplace; repeat for plugins/openjev-skills and plugins/openjev-mcp
```

`--strict-mcp-config` also disables MCP servers that plugins bundle, so combine it with `--mcp-config` or a registered server, not with `openjev-mcp`.

## Versioning

The plugin version in `.claude-plugin/plugin.json` is the same as the `openjev-mcp` plugin and the `openjev_mcp` package (currently 0.5.0). Bump all three together; Claude Code updates the install when the version changes. Measured effectiveness of each release comes from the evaluation suite described in `mcp/tests/skills_eval/README.md` of the repository.
