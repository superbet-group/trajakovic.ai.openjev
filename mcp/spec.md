# openjev-mcp: spec

Spec version 0.5 (package `openjev-mcp` 0.5.0, `openjev_mcp.__version__`; `openjev_mcp.SPEC_VERSION` stays 1.2, the build spec revision). Status: phases 1, 2 and 3 implemented, 2026-10-02. Written from the code as built.

This is the MCP server's own spec, for upgrades (new SDK, new MCP revision, new tools). It does not repeat the build spec; where a rule is not written here, `docs/mcp-skill-spec/OPENJEV_MCP_SKILLS_SPEC.md` v1.2 ("build spec", cited "spec 2.x") and `docs/mcp-skill-spec/TASKS.md` stay normative. `mcp/docs/architecture.md` ("arch") is the build-time interface contract, `mcp/docs/tasks.md` the phase-1 package plan and `mcp/docs/tasks-phase2-3.md` the phase 2/3 plan ("plan", its numbered Decisions are cited "Decision N"); when they disagree with this file, this file (which follows the code) wins. Section 3 lists every difference from the build spec.

### Versioning of this file

This file's version is the package version's `major.minor` and moves with it; `openjev_mcp.SPEC_VERSION` instead names the build-spec revision implemented (also 1.2) and changes only when `docs/mcp-skill-spec/` does. Rules: a patch release (0.5.x) fixes code to match this file and does not change it beyond the test count; a minor release (0.6) adds tools, resources, env variables or revisions and must update sections 1, 4, 9 and 10 in the same change; a major (1.0) is a breaking change to a tool schema, an error code or the transport contract. Phase-1 tool schemas only gained optional fields in 1.3/1.4 (TASKS 1.32). Every change to section 3 or the protocol decision (section 2) is a minor bump of this file.

Change log (versions 1.2-1.4 below are the pre-release numbering; on 2026-10-02 the package was levelled with OpenJev and the UI at 0.5.0, so 1.4 is now 0.5):

- 1.2: phase 1 as built (4 read tools, `lint`, `status`, `openjev-hook pretooluse`, 2 resources).
- 1.3: phase 2 (TASKS 2.1-2.16): `filter`, `recipe`, `batch`, `batch_results`, 29 built-in recipes, hooks `stop`/`userprompt`/`posttooluse`, CLIs `openjev check`/`filter`, resources/templates/patterns/guide, prompts and completion, extension plumbing, `OPENJEV_MCP_RECIPES`/`_ROUTING`; gate-10 fixed by changing the `command_gate` combine (deviation 29). Section 2 "Not implemented" and the mismatch list re-checked.
- 1.4 (verified 2026-10-02, no version bump): live re-run recorded in `docs/live-runs.md` and `tests/live/results/2026-10-verify.json`; `batch_results` export defect fixed; section 2 gains "Protocol research and upgrade notes"; sections 3, 8 and 10 updated.
- 1.4: phase 3: `ask_image` (+ SSRF-safe fetch), `compile`, `calibrate`, `generate`, `batch.images`, audit store and `openjev://audits/{question_hash}`, prompts `author_question`/`audit_question`/`explain_answer`, the MCP Tasks extension (`OPENJEV_MCP_TASKS`, off by default), `OPENJEV_MCP_FETCH`/`_AUDIT_DIR`/`_CHAT_MODEL`; live results recorded (`tests/live/results/2026-10-phase2-3.json`); sections 1-4, 8-11 rewritten from the code.

## 1. Scope and the surface

A separate process from OpenJev (:8080) and the UI (:8090). It talks to OpenJev over HTTP only, never imports `openjev` or `transformers`, loads no model, and starts without OpenJev (tools return `OJ_UNREACHABLE` until it listens). It reads and advises, it never executes anything. Two tools write files by design (`batch`, `calibrate`; `batch_results` writes exports), only inside the allowed roots (below).

### Tools (`tools/list` order = `TOOL_NAMES`, 14)

`tools/list` follows the spec 2.5 table order (plan Decision 1), identical for every connection (`dispatch.specs(config)` builds the tuple once per process): `ask, yes_no, classify, score, filter, batch, ask_image, lint, compile, calibrate, recipe, status, generate, batch_results`. `OPENJEV_MCP_TOOLSETS=core` returns `CORE_TOOL_NAMES = (ask, yes_no, classify, score, lint, status)` in the same relative order; `all` (default) returns all 14. Input and output schemas are Draft 2020-12, `$ref`-free, object root (`schemas.py`).

| Name | Title | Annotations | What it does |
|---|---|---|---|
| `ask` | Ask OpenJev | read-only, not idempotent | One state, 1-256 noul/choice/score questions; options `think`, `samples`, `steps`, `sequential`. |
| `yes_no` | Yes/no claim | read-only, idempotent | One literal claim: yes, no or uncertain, with the probability. A grey-band first read is re-read once with samples 4. |
| `classify` | Classify | read-only, idempotent | One label from a closed set, with an escape option so it can abstain; `multi_label` asks one yes/no per label. |
| `score` | Score on a scale | read-only, idempotent | 2-10 ordered levels; expected score, argmax label, spread, bimodality flag. |
| `filter` | Filter many items | read-only, idempotent | Keep or drop many small items by one criterion: injection-safe packing of the items into one state; `pick_best`, `graded`. |
| `batch` | Batch read | `readOnlyHint` false, idempotent | One question set over many states (inline items, a CSV/JSONL/JSON file or a library template), JSONL output file, cursor/resume, statistics, review queue, exports, `dry_run`. |
| `ask_image` | Ask about images | read-only, not idempotent; `openWorldHint` = `OPENJEV_MCP_FETCH` | Questions about 1-8 images (path, data URL, base64, opt-in https URL) plus a short text state. |
| `lint` | Lint a request | read-only, idempotent | Validate/lint a `/v1/systemone` request, autofix, estimate, `emit` snippets. No network request. |
| `compile` | Compile a statement | read-only, not idempotent | A prose intent into a linted draft request or a recipe pick (`recipe.variants`), slots, human questions, optional probe/calibration. |
| `calibrate` | Calibrate a question set | `readOnlyHint` false, not idempotent | A question set or recipe against labelled examples (or a finished batch against a labels file): accuracy, separation, fitted thresholds, reliability, drift, audit record. |
| `recipe` | Run a recipe | read-only, idempotent | A named recipe on inputs: `dry_run`, `profile`, `policy`, `fail_mode`, `degraded`. |
| `status` | OpenJev status | read-only, idempotent | Health, models, backend, limits, capabilities; `probe` runs one small read for latency. |
| `generate` | Generate text | read-only, not idempotent | Short text on the local chat model (OpenAI-style passthrough); NOT for decisions. |
| `batch_results` | Batch results | `readOnlyHint` false, not idempotent | Query, export and compare a finished or partial batch output without spending reads: review queue, stats, rows, export, `compare_to`. |

Inputs, outputs and errors of the phase-1 tools as built (`schemas.INPUT_SCHEMAS` / `OUTPUT_SCHEMAS`; every output also carries `meta` except `lint` and `status`):

| Tool | Input (required **bold**) | Output (required **bold**) | Tool-specific errors |
|---|---|---|---|
| `ask` | **state**, **questions**, options, thresholds, lint, return_raw | **answers**, **meta**, lint, raw | Lint errors (autofix off) -> `OJ_INVALID_INPUT` with the first failing path; `think`/`sequential` with images -> `OJ_INVALID_INPUT` |
| `yes_no` | **state**, **claim**, true_means, false_means, yes_at, no_at, options | **decision**, **p**, **margin**, thresholds_used, **meta** | as `ask` |
| `classify` | **state**, **question**, **labels**, escape, min_p, multi_label, options | **label**, **abstained**, reason, top, **p_top**, runner_up, margin, **probabilities**, confidence, labels_multi, **meta** | as `ask`; a winning choice that is not a label sent -> `OJ_PROTOCOL` |
| `score` | **state**, **question**, **levels**, one_based, options | **score**, **level**, **level_label**, **probabilities**, **confidence**, spread, bimodal, **meta** | as `ask`; malformed answers -> `OJ_PROTOCOL` |
| `lint` | request, or questions + state/options/images; profile, autofix, emit | **valid**, **errors**, **warnings**, fixed_request, estimate, snippets, body_hash | Only `OJ_INVALID_INPUT` (both or neither of `request`/`questions`); findings are results, not errors. No network I/O |
| `status` | probe | **healthy**, **decide_models**, base_url, chat_models, aliases_accepted, resolved, auth, backend, latency_probe_ms, limits, limit_source, capabilities, mcp, warnings | `/health` failures propagate (`OJ_UNREACHABLE`, `OJ_NOT_FOUND`, ...). `OJ_AUTH`/`OJ_FORBIDDEN` on `/v1/models` or `/v1/limits` -> `healthy: true`, `auth: "unknown"`, default limits and a warning with the hint. A failed `probe` -> `latency_probe_ms: null` plus a warning |

Phase 2/3 tools (property names from the registered schemas):

| Tool | Input (required **bold**) | Output (required **bold**) |
|---|---|---|
| `filter` | **task**, **items**, **criterion**, true_means, false_means, keep_at, drop_at, grey, pack_size, items_label, pick_best, graded, options | **kept**, **dropped**, **grey**, **items**, best, **meta** |
| `batch` | items, items_file, template, questions, options, images, max_side_px, sampling, regrey_samples, thresholds, review_rule, audit, concurrency, output_path, include_state, export, detail, max_items, max_items_per_call, time_budget_s, cursor, resume, retry_errors, only_ids, on_error, dry_run, max_inline_results | **status**, **summary**, **results**, review_queue, audit_ids, output_path, exports, import, preview, first_body, images, estimate, **next_cursor**, **meta** |
| `ask_image` | **images**, state, **questions**, options, max_side_px, thresholds | **answers**, **images**, **meta** |
| `compile` | **intent**, sub_decisions, labels, sample_inputs, labelled_examples, recipe | **recipe**, sub_decisions, **draft_request**, **slots**, **human_questions**, **lint**, probe, calibration, next_steps |
| `calibrate` | questions, recipe, examples, case_file, options, target, holdout, store, compare_to, from_batch, concurrency, max_items_per_call, cursor | **model_resolved**, question_hash, **n**, **per_question**, **items**, drift, warnings |
| `recipe` | **recipe**, **inputs**, profile, policy, fail_mode, dry_run, options | **recipe**, **decision**, reason, **signals**, thresholds_used, **degraded**, error, requests, built_requests, answers, meta |
| `generate` | **messages**, max_tokens, response_format, stop, model | **content**, **finish_reason**, usage, retried, warnings |
| `batch_results` | **path**, view, filter, sort_by, sort_question, order, limit, cursor, detail, export, compare_to | **view**, rows, review_queue, stats, matched, export, compare, next_cursor, warnings, **meta** |

Every tool: schema failures -> `OJ_INVALID_INPUT` result (path + hint); OpenJev HTTP and transport faults -> the `mapping.py` codes of section 6; uncaught exceptions -> `OJ_INTERNAL`. Escape labels (`other`, `none`, `no_match`, `not_stated`, and `<prefix>_...`) match case-insensitively in `derive.is_escape`, so lint W201, the default escape option and `abstained` agree on `Other`. `classify` `multi_label` sends one noul per label (criteria `{true: <description>, false: "the text does not fit <label>"}`), adds no escape, and abstains when `p_top < min_p`. `batch` and `batch_results` return `resource_link` content blocks after the text block for the output file and exports (`envelope.file_link`); links are reset per call.

Batch limits (`limits.BATCH_CAPS`, also in `openjev://limits` and `status.limits.batch`): `concurrency_max` 4, `max_items_per_call` 100, `max_items` 5000, plus `max_inflight_batch` from the config. Lint codes added by phases 2/3: E030-E032 and W601-W605 (batch import findings, raised by `batch.importers`, not by `lint_request`) and W701 (`generate` clamps `max_tokens`).

### Resources, templates, prompts, completion

| URI | mimeType | Cache (`ttlMs`, `cacheScope`) |
|---|---|---|
| `openjev://schema` | `application/schema+json` | 3600000, `public` |
| `openjev://limits` | `application/json` | 60000, `private` (carries the read time) |
| `openjev://recipes` | `application/json` | 3600000, `public` (index of the 29 built-in recipes plus the `OPENJEV_MCP_RECIPES` directory) |
| `openjev://templates` | `application/json` | 3600000, `public` (batch template index, 10 templates) |
| `openjev://patterns` | `application/json` | 3600000, `public` (verified question patterns) |
| `openjev://guide/authoring` | `text/markdown` | 3600000, `public` |

`resources/list` is one page, 6 resources, identical on every call (static `annotations.lastModified` = mtime of `schemas.py`, `resources.BUILD_TIME`; `openjev://limits` has none, deviation 19). `resources/templates/list` returns 3 templates (`resources.templates_listing()`; the third is appended on first listing, deviation 33):

| uriTemplate | Reader | Cache |
|---|---|---|
| `openjev://recipes/{id}` | the full recipe document | 3600000, `public` |
| `openjev://templates/{id}` | `{id, title, questions, options, states, source_case}`, usable as batch template or items | 3600000, `public` |
| `openjev://audits/{question_hash}` | an audit record written by `calibrate` (`audit_store`, under `OPENJEV_MCP_AUDIT_DIR`) | 0, `private` |

Not listed but readable: `file://<absolute path>.jsonl` inside the allowed roots whose first line is a batch header written by this server (`resources._read_file`; `ttlMs` 0, `private`; the corrupt-line rule of deviation 28 applies). Unknown URI: JSON-RPC `-32602` "Resource not found" with `data.uri`. `tools/list`, `resources/list`, `resources/templates/list`, `prompts/list` and `server/discover` carry `ttlMs` 3600000 / `public` (a `CacheHint` on the server).

Prompts (`prompts/list`, 5, user-role messages, `listChanged: false`): `start_batch` (**template**; items_path, output_path), `review_batch` (**output_path**; limit), `author_question` (**intent**; examples), `audit_question` (**schema_path**, **labels_path**), `explain_answer` (**answer**). An unknown prompt, a missing required or an unknown argument is `-32602`. Completion (`completion/complete`, prefix match, at most 100 values, `total`/`hasMore`): prompt `start_batch` argument `template`, resource `openjev://templates/{id}` and `openjev://recipes/{id}` argument `id`.

Capabilities: `tools`, `resources`, `prompts {listChanged: false}`, `completions {}` (plan Decision 6), plus `extensions` only on 2026-07-28 and only when an extension is active (section 2).

### MCP Tasks extension

`io.modelcontextprotocol/tasks`, built by hand in `tasks.py` on the SDK `Extension` hook (deviation 31); off unless `OPENJEV_MCP_TASKS=on` (plan Decision 5). Active only for a 2026-07-28 request whose `_meta` `clientCapabilities.extensions` lists the identifier, and only for `batch` and `calibrate`: `tools/call` then answers `{"resultType": "task", "task": {taskId, status, ttlMs, pollIntervalMs}}`. Any other caller (no capability, legacy era, other tool, flag off) gets the normal synchronous result; `-32021` is never returned. Methods `tasks/get` (status, `statusMessage` from progress, `result` or `error`), `tasks/update` (acknowledged, ignored: no `input_required` state exists) and `tasks/cancel`, 2026-07-28 only (else method-not-found); unknown task id is `-32602`. The store is in-process (dies with the process), `ttlMs` 600000 after completion, `pollIntervalMs` 1000, at most `OPENJEV_MCP_MAX_INFLIGHT_BATCH` tasks run at once (the rest stay `working`). The cursor path stays the normative way to run long batches; Tasks is an optional convenience.

### Entry points

| Command | Purpose |
|---|---|
| `openjev-mcp [--transport {http,stdio}] [--host HOST] [--port PORT] [--version]` | The server. Flags win over env; default transport `http`. Exit 0 clean, 2 configuration error (bad env, non-loopback bind without token), 3 bind failure (message names the port and `OPENJEV_MCP_PORT=<port+1> mise run start`). |
| `openjev-hook pretooluse [--profile {strict,lenient}] [--unattended] [--timeout-ms N] [--task TEXT] [--defer-allow]` | Claude Code PreToolUse hook (recipe `command_gate`): payload on stdin, hook JSON on stdout, always exits 0. `--profile` default `strict`; `--timeout-ms` default 10000; `--task` default `OPENJEV_HOOK_TASK`, then the last user prompt of the transcript, then `(task unknown)`. |
| `openjev-hook stop [--max-blocks N] [--timeout-ms N]` | Stop hook (recipe `done_gate`): blocks a stop whose final message claims done without verification, `systemMessage` on `escalate`. Default `--max-blocks` 2 (the stop is allowed after that many consecutive blocks). Fails open (prints nothing). |
| `openjev-hook userprompt --roster FILE [--threshold P] [--timeout-ms N]` | UserPromptSubmit hook (recipe `skill_selection`): `additionalContext` with one skill hint when p_top >= threshold (default 0.8). Roster: `[{id, description}]` or `{id: description}`. Fails open. |
| `openjev-hook posttooluse [--screen GLOBS] [--timeout-ms N]` | PostToolUse hook (recipe `injection_screen`) for tools matching `--screen` (default `WebFetch,mcp__*`): `quarantine` blocks with a warning, an unavailable screen warns "uncertain". |
| `openjev [--version] [version]`, `openjev check RECIPE [--inputs FILE\|-] [--profile P] [--unattended]`, `openjev filter CRITERION [--gt P] [--task T]` | `version` prints `openjev-mcp <version>`. `check` runs a built-in recipe id or a recipe `.json` file and prints its JSON (exit 0 least severe decision, 1 any other, 2 error). `filter` keeps the stdin lines meeting the criterion (exit 1 if any kept, 0 none, 2 error). |

`python -m openjev_mcp` is `openjev-mcp`. `stop`, `userprompt` and `posttooluse` are registered without `help=` so they stay out of `openjev-hook --help` (deviation 30).

### Hook and recipe

`openjev-hook pretooluse` runs the built-in recipe `command_gate` (`recipes/builtin/command_gate.json`, package data) directly against OpenJev, not through the MCP server. Only `Bash` commands are judged; any other tool, or an empty command, prints nothing. Decision order: deterministic rules first (RE2 allow/deny lists over shell segments), then typed reads only when no rule decided. `openjev_mcp.hook` imports `httpx` lazily so a rule-decided command stays inside the 300 ms budget, and a test asserts `mcp`, `jsonschema`, `httpx`, `openjev_mcp.http`, `openjev_mcp.lint`, `openjev_mcp.schemas` and `openjev` stay out of `sys.modules` on import. Fail closed: any error, timeout, bad input or degraded read yields `ask` (`deny` with `--unattended`). `--defer-allow` prints nothing on an allow so Claude Code's own permission flow decides. Timing (2.20): `decide` starts a monotonic clock before the transcript read and recipe load; the read gets the remaining budget as `deadline_ms`, and an `anyio.fail_after(remaining - 0.05 s)` backstop (`BACKSTOP_MARGIN_S`) keeps the whole call inside `--timeout-ms`, so the CLI decides before the settings.json hook timeout (which must be >= 1000 ms above `--timeout-ms`; asserted against the README example). Payload field names live only in `claude_hooks.py` (`SUPPORTED_CLAUDE_CODE = ("2",)`).

### Recipes

29 built-in recipes in `recipes/builtin/*.json` (data, no code): `act_or_ask`, `alert_triage`, `bulk_label`, `claim_check`, `command_gate`, `done_gate`, `duplicate_check`, `entity_match`, `injection_screen`, `issue_triage`, `judge_assert`, `judge_pairwise`, `memory_decide`, `model_routing`, `moderation`, `multistep_tick`, `rag_gate`, `review_finding_filter`, `rubric_score`, `select_extraction`, `semantic_filter`, `semantic_lint`, `skill_selection`, `taxonomy_classify`, `threshold_audit`, `ticket_triage`, `typed_call`, `ui_decision`, `verify_fields`. `OPENJEV_MCP_RECIPES` adds a directory under the same parser and RE2 rules (a failing file is skipped with a registry warning). The engine format and its extension keys are documented in the `recipes/engine.py` module docstring (deviation 32); each recipe's own `notes` field starts with `# deviation:` where it differs from spec 5.x.

### Allowed roots and paths (plan Decision 2)

One module, `paths.py`. Roots are `Config.roots` = `(realpath(cwd), *OPENJEV_MCP_ROOTS)` only; no `roots/list` on any era. Absolute paths are accepted when inside a root (realpath, then prefix check). A relative path resolves against `roots[0]` under stdio only; under HTTP it is `OJ_INVALID_INPUT` "relative path: pass an absolute path inside the allowed roots". Read and write extension lists, 64 MiB read cap, 20 MiB image cap, no writes through a symlink, under a dot-directory or to a dotfile, BOM -> UTF-16, strict UTF-8 else cp1252 + W603.

### Skills

11 skills in `mcp/skills/<name>/SKILL.md`: `openjev-decisions`, `openjev-question-authoring`, `openjev-agent-gates`, `openjev-code-checks`, `openjev-dispatch`, `openjev-triage-routing`, `openjev-retrieval-relevance`, `openjev-multistep`, `openjev-data-records`, `openjev-ui-vision`, `openjev-calibration`. They are files in the repo, not package data of the wheel (`package-data` ships `recipes/builtin/*.json`, `data/*.json`, `data/*.md`).

### Processes and ports

| Process | Command | Port (env) | Talks to |
|---|---|---|---|
| OpenJev API | `python -m openjev` | 8080 (`OPENJEV_PORT`) | the model |
| UI | `python ui/server.py` | 8090 (`UI_PORT`) | OpenJev (`OPENJEV_URL`) |
| MCP server | `python -m openjev_mcp --transport http --port 8100` | 8100 (`OPENJEV_MCP_PORT`) | OpenJev (`OPENJEV_BASE_URL`) |

The MCP package never reads `OPENJEV_MODEL` (spec F7) or the UI's `OPENJEV_URL`.

## 2. Protocol decision

### Revisions

| Revision | Era | Status |
|---|---|---|
| `2026-07-28` | stateless: per-request `_meta` envelope, `server/discover`, `resultType`, `ttlMs`/`cacheScope` | primary |
| `2025-11-25`, `2025-06-18` | `initialize` handshake | legacy, tested |
| `2025-03-26`, `2024-11-05` | `initialize` handshake | served by the SDK, best-effort, untested |

`PROTOCOL_VERSIONS = ("2026-07-28", "2025-11-25", "2025-06-18")` is what `server/discover` and `/health` report. The era is decided by the client: an enveloped request opens a 2026-07-28 exchange, anything else a legacy one (stdio: the first request decides; HTTP: the `MCP-Protocol-Version` header). Unchanged by phases 2/3: 2026-07-28 was re-checked on 2026-10-02 as the latest stable revision (no newer revision published, draft changelog empty).

### SDK

- Official Python SDK `mcp>=2.2,<3` (`mcp/pyproject.toml`); installed in the project `.venv`: `mcp` 2.2.0 and `mcp-types` 2.2.0 (`pip show mcp`). 2.2.0 was the newest release on PyPI on 2026-10-02 (`pip index versions mcp`: 2.2.0, 2.1.1, 2.1.0, 2.0.1, 2.0.0, 1.30.0, ...). Floor and ceiling: 2.x is the first line with the 2026-07-28 revision; 3.x is unreviewed.
- `mcp_types.version` in 2.2.0: `KNOWN_PROTOCOL_VERSIONS` = 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25, 2026-07-28; `MODERN_PROTOCOL_VERSIONS` = 2026-07-28.
- The SDK ships only the `Extension` hook (`mcp/server/extension.py`: `identifier`, `settings()`, `methods()`, `intercept_tool_call`), not a Tasks implementation; `ext.EXTENSIONS` lists the factories (`tasks.factory`) and `server.build_server` advertises active ones in `server.extensions`, binds their methods (version-gated through `MethodBinding.protocol_versions`) and wraps `tools/call` with `compose_tool_call_handler` when an extension intercepts.
- Other direct dependencies: `httpx>=0.27`, `jsonschema>=4.18`, `google-re2>=1.1`, `uvicorn>=0.31`, `anyio>=4.9`. Optional extra `images` = `Pillow>=10` (also in `test`); the core never imports Pillow at import time (`images.py` imports it lazily). No `transformers`, `tokenizers` or `openjev` (tested in `test_mcp_packaging.py`). The SDK itself uses `httpx2` (import name), which coexists with `httpx`.
- Only `server.py` and `http_app.py` import the SDK, except `ext.py` and `tasks.py` (subclass/type the SDK `Extension`); `resources.py` is tested to stay SDK-free.

### Protocol research and upgrade notes (2026-10-02)

Checked against modelcontextprotocol.io (versioning, changelog, roadmap, authorization, extensions/tasks) and PyPI:

- Stable revisions: 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25, 2026-07-28. Only 2026-07-28 is labelled "Current"; no newer revision, release candidate or date exists and the draft changelog is empty. Nothing to add to `PROTOCOL_VERSIONS`; 2025-03-26 and 2024-11-05 stay best-effort and unadvertised.
- SDK: PyPI `mcp` 2.2.0 (2026-09-07) is the latest and is what is installed; the 1.x line continues with security patches (1.30.0). The pin `mcp>=2.2,<3` needs no change. Second-hand and unverified (the release notes could not be fetched): 2.1 stops sending handler exception text on the wire, 2.2 limits HTTP redirects to the endpoint origin, expires idle Streamable HTTP sessions after 30 minutes and wants `issuer=` on OAuth providers; none touches this stateless server.
- Verified in the installed package: `mcp/server/extension.py` (SEP-2133 `Extension`), `subscriptions.py`, `request_state.py`, `elicitation.py`, `caching.py`, `_otel.py`; `InputRequiredResult`, `subscriptions/listen`, `x-mcp-header` and `io.modelcontextprotocol/logLevel` exist in `mcp_types`. There is no Tasks implementation (the SDK notes say it is planned), so `tasks.py` stays hand-written.

What 2026-07-28 changed versus 2025-11-25, and where this server stands:

| Change | Status here |
|---|---|
| No sessions, no `Mcp-Session-Id` (SEP-2567); no `initialize`, per-request `_meta` envelope, `server/discover` (SEP-2575) | done (stateless; discover; `-32022` on a bad version) |
| `resultType` on every result; `ttlMs`/`cacheScope` on lists, `resources/read`, `resources/templates/list` (SEP-2549); `tools/list` deterministic | done (list order = `TOOL_NAMES`, append only) |
| `Mcp-Method`/`Mcp-Name` headers, `-32020` HeaderMismatch | done |
| Resource not found `-32602` (was `-32002`) | done (asserted in `test_mcp_protocol`) |
| `ping`, `logging/setLevel`, `notifications/roots/list_changed` removed; per-request `logLevel` in `_meta` | done by omission; the server emits no `notifications/message` |
| Roots, Sampling, Logging deprecated (SEP-2577); HTTP+SSE deprecated (SEP-2596) | not used; Streamable HTTP only |
| MRTR / `InputRequiredResult` (SEP-2322) replaces server-initiated requests | not needed: no elicitation, sampling or `roots/list`. If a recipe ever asks for confirmation it must use `resultType: input_required` with `requestState`, not `elicitation/create` |
| Tasks moved to the `io.modelcontextprotocol/tasks` extension (SEP-2663: `tasks/get`, `tasks/update`, `tasks/cancel`; no `tasks/list`/`tasks/result`) | done by hand for `batch`/`calibrate`, off by default |
| `subscriptions/listen` replaces the GET stream and `resources/subscribe` | not implemented (error on both eras); lists are static |
| `capabilities.extensions` (SEP-2133) | advertised only while an extension is active; an empty `extensions` key is not sent |
| `inputSchema`/`outputSchema` any Draft 2020-12, `structuredContent` any JSON (SEP-2106) | schemas are `$ref`-free objects; text content equals the JSON because the roadmap flags a coming `tools/call` result redesign |
| OTel `_meta` keys (SEP-414); `x-mcp-header` (SEP-2243); `-32021` MissingRequiredClientCapability | OTel keys go to the audit record only; the other two are unused |
| Authorization: RFC 9728 metadata, audience validation (RFC 8707), `WWW-Authenticate` with `resource_metadata=`, 403 `insufficient_scope` | optional for HTTP; not built (static bearer token, loopback by default). Add the metadata endpoint and header only if the server is exposed to OAuth clients; the SDK has `mcp.server.auth` |

Optional follow-ups, none blocking: a Tasks store with durable storage if long `batch`/`calibrate` calls need it beyond the cursor loop (keep `OPENJEV_MCP_TASKS` off by default); `WWW-Authenticate: Bearer` already goes out on the token-mode 401. Watch the SDK 2.2 OAuth `issuer=` deprecation only if this server becomes an OAuth client (it is not).

### Why the low-level `Server` (F12)

`mcp.server.lowlevel.Server` only, never `mcp.server.mcpserver.MCPServer`. The high-level layer validates tool arguments against `inputSchema` and turns failures into JSON-RPC errors. F12 (spec 2.4, TASKS 1.29) requires an invalid argument to be a tool result with `isError: true` and `OJ_INVALID_INPUT`, so the dispatcher validates itself (`validate.py`) and the SDK only checks the `CallToolRequestParams` shape. `Server.streamable_http_app(stateless_http=True)` and `Server.run()` (dual-era loop) are used as is.

### Accepted SDK mismatches (all asserted by tests; re-checked at 1.4, suite green)

1. `-32022` Unsupported protocol version has `data.supported == ["2026-07-28"]` (built in `mcp.shared.inbound` before any handler runs). It names the revisions usable with the envelope; `server/discover` lists all three.
2. Legacy `initialize` with an unknown version gets a counter-offer `protocolVersion: "2025-11-25"`, not `-32022` (the 2025 rule; spec 2.0.1 rule 3 applies to the envelope path).
3. The legacy handshake also accepts 2024-11-05 and 2025-03-26; rewriting `initialize` params in a middleware would desynchronise the SDK's commit, so it is not done.
4. `resultType` and the serverInfo `_meta` (`io.modelcontextprotocol/serverInfo`) appear only on 2026-era results; `ttlMs`/`cacheScope` are stripped from legacy results.
5. No SDK-side check that tool arguments match `inputSchema` (wanted; see F12).
6. `resources/templates/list` is served on both eras (phase 1 returned `[]`; since 1.3 it lists the templates of section 1, and `test_mcp_protocol` asserts content).
7. `tools/call` with `arguments` that is not an object fails the SDK's `CallToolRequestParams` shape check and is `-32602`, by design (a protocol fault, 2.4). Only an object that fails the tool's `inputSchema` becomes an `OJ_INVALID_INPUT` result.
8. A modern request without `Mcp-Method` or with a wrong `Mcp-Name` is HTTP 400 with JSON-RPC `-32020`; a modern `GET /mcp` is 405.
9. (new in 1.3) The SDK's 2025-11-25 wire schema has no `capabilities.extensions`: an active extension is advertised on 2026-07-28 only (legacy `initialize` lists `experimental {}` and no `extensions`); an extension's own methods are still version-gated by us (`server._version_gated`).
10. (new in 1.3) `subscriptions/listen` is an error on both eras (`test_subscriptions_listen_is_an_error`); the SDK has no handler and none is registered.

Nothing else differed from the arch A.2 probes during the build. No SDK fallback is built; if a future SDK drops the dual-era loop, the fallback is a hand-written JSON-RPC loop over `mcp_types` applying the same ladder (envelope check, version check, dispatch). Parts that are ours regardless: the `server/discover` handler, argument validation, the result envelope, progress rate limiting, the catch-all that maps an uncaught handler exception to `OJ_INTERNAL`, semaphore release on cancellation, the bearer middleware, `/health`, and the Tasks extension.

Request `_meta`: `progressToken`, the `io.modelcontextprotocol/*` envelope keys and the rest are split in `server.tool_context`; the non-envelope keys reach the handler as `ToolContext.request_meta`. OTel `traceparent`/`tracestate` (SEP-414) are accepted and written to the audit record only, never forwarded to OpenJev (`audit.TRACE_KEYS`). A per-request `logLevel` is accepted and ignored: the server never emits `notifications/message` (`test_log_level_meta_debug_yields_no_log_notification`).

### Not implemented

Deliberately not built, with the reason:

- `subscriptions/listen`, `resources/subscribe`: lists are static; clients re-list on the 1 h `ttlMs`.
- `notifications/message` emission and `logging/setLevel`: no server logging over MCP (stderr and `OPENJEV_MCP_LOG` instead).
- OAuth and RFC 9728 Protected Resource Metadata: loopback by default; a non-loopback bind uses the static bearer token with a 401 `WWW-Authenticate: Bearer realm="openjev-mcp"`.
- `x-mcp-header` parameter mirroring and `-32021`: not used; the Tasks extension never returns `-32021`.
- MRTR / `InputRequiredResult`, elicitation, sampling, `roots/list`: the server makes no server-initiated request on any era (allowed roots come from configuration, Decision 2).
- Stateful HTTP sessions (`Mcp-Session-Id`).
- Tasks: other tools than `batch`/`calibrate`, the `input_required` state, a persistent store, `tasks/list`.

## 3. Deviations from build spec 1.2

| # | Build spec | As built | Why |
|---|---|---|---|
| 1 | Registration example `{"command": "openjev-mcp"}` (stdio) | Default transport is `http` (`OPENJEV_MCP_TRANSPORT` overrides). stdio registration needs `"args": ["--transport", "stdio"]`; HTTP registration is `{"type": "http", "url": "http://127.0.0.1:8100/mcp"}` | The server has its own port (user requirement); one daemon serves every session. |
| 2 | `OPENJEV_MCP_MAX_INFLIGHT=1` | `1` under stdio, `4` under http, unless the variable is set | One HTTP daemon serves all sessions; `1` would make one session's `think` read block the others. OpenJev's 529 stays the global back-pressure. |
| 3 | Streamable HTTP "require[s] a bearer token" (spec 2.0) | `OPENJEV_MCP_TOKEN` is optional on a loopback bind and required off loopback (exit 2 without it). Host/Origin checks always on | On loopback the Host/Origin checks stop rebinding and browser attacks; a local process able to reach the port could also read a token file. |
| 4 | Error code list of spec 2.4 | Adds `OJ_INTERNAL`: an uncaught exception in `call_tool` or a mapping bug becomes an `isError` result, never a JSON-RPC error. Not retryable | Keeps F12 intact for bugs. |
| 5 | Hook outputs allow/ask/deny | Adds `--defer-allow` (print nothing on allow). Hook always exits 0, errors fail closed | Lets Claude Code's own permission flow decide on allow. |
| 6 | Tool registry in `tools/__init__.py` (arch D.13) | `specs`, `tools_for`, `call_tool` live in `tools/dispatch.py` (each tool module exposes `register(config) -> ToolSpec`); `tools/__init__.py` holds only the types (`ToolContext`, `ToolSpec`, `Handler`, `UnknownTool`). `tools_for` also accepts a bare toolsets string, `TOOLS` stays importable as a lazy module attribute and `_BY_NAME` lets a test override a tool; `_ensure_schemas` re-registers schemas a test popped | Avoids an import cycle between registry and handlers; keeps the phase-1 tests collecting. |
| 7 | `recipes/__init__.py` exports the engine | Empty; import `openjev_mcp.recipes.engine` directly | Keeps `import openjev_mcp.hook` light. |
| 8 | Protocol fixtures in `tests/stubs.py` | `stubs.py` holds the stub OpenJev (arch G.1); `rpc_session`, `mcp_client`, `http_app_client` live in `tests/rpc.py` | Package split. |
| 9 | - | `TOOL_NAMES` is exported by `openjev_mcp/__init__.py` (spec 2.5 order); the dispatcher asserts it and the skills test checks against it | Single source for tool order. |
| 10 | - | `pyproject.toml` pytest options add `pythonpath = ["tests"]`; rootdir is `mcp/`; marker `live` | Tests `import stubs` / `import rpc`. |
| 11 | `hook.py` rule path | `httpx` imported lazily, only when the rules did not decide | 300 ms budget. |
| 12 | Phase-1 CI job (TASKS 1.25) | `mise run test` runs `pytest -q mcp/tests` as one suite; the repo has no CI workflow file | No `.github/` in the repo. |
| 13 | Phase-1 exit: live run of `00-spec-examples.json` and the 14/14 hook run | Recorded on 2026-10-02 in `mcp/tests/live/results/2026-10-phase2-3.json`: `run_cases` 63/63 and hook 14/14 (gate-10 `ask`). Live checks are `@pytest.mark.live`, skipped unless `--live` or `OPENJEV_LIVE=1`; a full `mise run start` from the build packages is still not exercised | Live runs need the 16 GB model; the services were already running. |
| 14 | `OPENJEV_MCP_MAX_INFLIGHT_BATCH`, `OPENJEV_MCP_ROOTS` | Used since 1.3: the batch pool is its own semaphore (`http.OpenJevClient` pool `batch`, so a batch never starves a gate read), the roots confine every file path (`paths.py`, Decision 2) | Superseded phase-1 "reported only". |
| 15 | Phase 2/3 env names | All read since 1.3/1.4: `OPENJEV_MCP_RECIPES`, `_ROUTING`, `_FETCH`, `_TASKS`, `_AUDIT_DIR`, `_CHAT_MODEL` (section 4). `OPENJEV_MCP_CACHE_TTL_S` is not read | The cache TTLs are constants (`ttlMs` in the resources, 60 s limits cache). |
| 16 | `OPENJEV_MCP_TOOLSETS` `core` | `core` returns the 6 tools of `CORE_TOOL_NAMES` in `TOOL_NAMES` order; `all` the 14; `generate` is in `all` only (Decision 1) | Superseded phase-1 "identical to all". |
| 17 | Limits from `GET /v1/limits` | `404` is handled: documented defaults plus a warning (`LIMITS_404_WARNING`); cache 60 s; best-effort warm-up at startup (2 s). Still 404 on the live OpenJev (2026-10-02) | Phase-0 server work (`/v1/limits`) is not done; see section 10. |
| 18 | `lint` (2.13) with `questions` and no `state` | Valid: `state` is left out of the request and E001 is skipped. E001 stays for the `request` form, for an empty or invalid `state` that is present, and for every read tool | State is optional while authoring or in CI on schema files. |
| 19 | Rule 5: `openjev://limits` `lastModified` | Not on `resources/list` (the list is identical on every call); not on `resources/read` either, because the SDK's `TextResourceContents` has no annotations. The read time is `read_at` in the body | Rule 3 (stable lists) beats a per-call timestamp. |
| 20 | Rule 7 / TASKS 1.30: cancellation registry keyed by request id | No own registry. stdio: `notifications/cancelled` cancels the handler through SDK cancel scopes. HTTP (stateless, both eras): the SDK answers `notifications/cancelled` 202 and ignores it; a request is cancelled when the client closes its response stream. Either way the upstream OpenJev request is cancelled and the in-flight semaphore is released in `finally`. Live (2026-10-02): on the 2026-07-28 wire a closed POST stopped `batch` (rows resumed with 11 skipped, no duplicates); a legacy-era client's cancel did not stop it (rows 7 -> 19 in 4 s, an immediate resume got `output_path is in use by another batch call`) | SDK design: one caller must not cancel another's request. Open, see section 10. |
| 21 | Rule 8 progress message `<done>/<total> ok=<n> err=<n> eta <s>s` | Phase-1 reads: `ok` = reads finished, `err` is 0 because an error ends the call. `batch` (`batch/runner.py`) emits real `ok`/`err` counts; `status.done` counts skipped (resumed) rows too. The 1 s rate limit can drop the final value | Phase 2 batch reuses the format. |
| 22 | 2.16 `command_gate` deny example: `thresholds_used` keys `ask_hazard`/`allow_risk` and the reason text | The reason text and the full policy keys (`ask_hazard_low`, `allow_risk_max`, ...) from the 2.13 policy table | The example disagrees with its own policy table. |
| 23 | 2.13 lint profile `gate`: warnings about blocking questions become errors | Every question's W1xx-W4xx becomes an error under `gate`. W401 still identifies blocking ids by regex | Simpler and stricter; limiting promotion to questions matching `BLOCKING_ID` is an open option. |
| 24 | 2.20 hook `allow` | A model-read `allow` is emitted as `permissionDecision: allow`, which skips Claude Code's own prompt. `--defer-allow` (row 5) is opt-in. Rule-decided allows are unaffected | Deliberate default for unattended use; consider defaulting model-read allows to defer. |
| 25 | 2.3 `chunks_estimate` | Follows the literal 2.3 formula. `openjev/engine.py` packs the canvas by id plus one label token, so descriptive score levels inflate the estimate | Estimate only; do not "fix" it without changing 2.3. |
| 26 | Principle 10: caches never change a result | Read tools await `LimitsCache.get()` (60 s TTL, refresh on expiry), so E024/E028 do not depend on call history. A failed refresh caches nothing, so the next read retries at once. `lint` uses `peek()` (no network I/O, 6.7): its E024/E028 severity depends on whether the cache has been filled | `lint` must stay pure. |
| 27 | Plan Decision 2 vs spec 2.2 | Roots from configuration only; no `roots/list`. Absolute paths always accepted inside a root; a relative path resolves against `roots[0]` under stdio only and is `OJ_INVALID_INPUT` under HTTP | The daemon's cwd is not the client's project. |
| 28 | Plan Decision 3 / spec 7 #23: corrupt JSONL line undefined | A batch output line that is not valid JSON is refused (`OJ_INVALID_INPUT` "output_path line N is not valid JSON; fix or truncate the file"), except a final line without a trailing newline (an interrupted write), which is ignored with a warning and truncated before the next append. Same rule in `batch`, `batch_results`, `calibrate.from_batch` and `resources/read file://` | An open item of the build spec; the interrupted-write case must not block resume. |
| 29 | 2.18 L2843 `command_gate` `combine` vs 5.3 L4362 (gate-10) | The built `combine` matched 2.18 verbatim, but 5.3 records the rustup `curl \| sh` as `ask` and the case expects ask/allow. `remote_code` alone no longer denies: it denies only with verdict `deny` or `out_of_scope >= allow_scope_max`; `destructive` and `exfiltrates` stay sole-signal denies (`command_gate.json` notes). Live: hook 14/14, gate-10 `ask` | A build-spec inconsistency (plan Decision 4), not a code bug. Rule/shell-level tests unchanged. |
| 30 | 2.20 hook subcommands | `stop`, `userprompt`, `posttooluse` have no `help=` in `openjev-hook --help` (the phase-1 `test_help_lists_only_pretooluse` stays valid; `openjev-hook <name> --help` works). `claude_hooks.py` reads the prompt from `prompt` (older docs) or `prompt_text` (current docs) | Compatibility with the phase-1 test and with both documented payloads. |
| 31 | 2.0.1 rule 9 / mcp spec 1.2 section 10 "wait for the SDK" (Tasks) | Tasks built by hand (`tasks.py`) on the SDK `Extension` hook because the SDK has no Tasks API and the stable spec defines the extension (plan Decision 5). The `Extension` has no lifecycle hook, so the extension-owned task group lives in a private asyncio task started on first use (asyncio only, as uvicorn/stdio here) and `aclose()` stops it. Scope in section 1 | The user asked for the latest protocol; the SDK offers only the hook. |
| 32 | 2.18 recipe format | Extended: `question_profiles`, `signal_map`, `policy_profiles`, `fallback` (phase 1) plus `decision_format`, `outputs`, `routing`, `x-openjev-images`, `read` `reread`/`options`/`when`/`per`, `read_per_item`, `read_twice_swapped`, `$from`/`$each` questions, `compute: weighted_sum` (documented in the `recipes/engine.py` docstring; plan Decision 8). `recipe` `dry_run` returns `decision: "dry_run"` (the output schema requires a decision) and `build_requests` skips every read that has a `when`; `run_recipe` takes `config` (defaults to `load_config({})`). A missing signal keeps the plain decision. Recipe files stay data | The 29 recipes need these; each recipe's `notes` lists its own deviations from spec 5.x (grep `# deviation:` in `recipes/builtin`). |
| 33 | 2.18 resources | The audits template/reader is wired lazily (`audit_store` imports `ResourceNotFound` from `resources`: import cycle), so `openjev://audits/{question_hash}` appears in `resources/templates/list` from the first listing on, not at import; `prompts` are registered lazily for the same reason (`prompts._ensure`) | Import cycle. |
| 34 | 2.11 batch | `status.done` counts skipped rows too, `status.skipped` is the subset and skipped rows are not listed in `results`; on_error `abort` writes the failing row as an error row (the cursor moves past it, `retry_errors` re-runs it); with images the runner builds bodies without images and the batch tool adds the same data URLs as the last key (as `wire.build_body` would); exports: CSV rows end with LF, not CRLF, and `ojui-batch` answers hold the MCP Answer shape, not the raw server response | Cursor correctness; the Playground reads either line ending. |
| 35 | 2.21 batch_results | Questions are rebuilt from the answers (type, option keys, level count) because the JSONL header stores only the question hash, so a `batch_results` `ojui-batch` export lacks instructions/criteria and differs from the `batch` export of the same file (live finding, fixed 2026-10-02: the header now also stores `questions`, an additive key, and `batch_results`/`batch_prompts` prefer it; files written before the fix fall back to inference, so their export stays lossy; the export is byte-identical to the `batch` export of the same new file). `compare_to` over partially overlapping option sets compares the shared keys, renormalised (not null) | The output file format is fixed by 2.11. |
| 36 | 2.15 calibrate | With one question `label`/`p` are scalars, with several `{question: value}`; choice/score items add the prediction. `precision_coverage`: coverage = recall of the true class (the build spec does not define it). The suggested band is built to reproduce the `ex-cal` report (`[0.05, 0.95]` at n=7). A chunked run keeps its rows in `OPENJEV_MCP_AUDIT_DIR/work` (no `output_path`); partial calls return `per_question {}` and `next_cursor`. Reads use `sampling server_default` (no fast `samples=1`/regrey); a recipe source takes `example.state` as the recipe inputs | The spec wording is ambiguous or silent. |
| 37 | 2.17 generate | No tokenizer in this package: "longer than one token" is approximated as more than 3 characters; W701 is new (spec names the clamp but no code); `stream: false` is not sent (the server default is non-streaming, as in the captured case); an empty reply with no completion tokens is retried once (MLX bug 12.4) | No tokenizer dependency; keeps the captured request byte-identical. |
| 38 | 2.12 image URL fetch | `OPENJEV_MCP_FETCH` off by default (`openWorldHint` follows it); https only, public addresses only (loopback, private, link-local, multicast, CGNAT refused), one resolve per host and connect to that address, at most 3 redirects each re-checked, 10 s total, 20 MiB body cap, no cookies or credentials (`fetch.py`) | SSRF rules of 2.2; Pillow stays an optional extra. |
| 39 | 5.x recipes | Not all spec 5 variants are built: `typed_call` has only the function choice (no per-parameter questions), `rag_gate` has keep/drop/quarantine/conflict (no answer/requery, rerank, sufficiency), `claim_check` has no screen mode, `multistep_tick` has no `goal_claim`/stop, `ui_decision` has no `flags`, `entity_match` has no per-field questions, `taxonomy_classify` is one level per call, `alert_triage` has no subsystem/owner routing, `threshold_audit` probes one example (the audit is `calibrate`), `bulk_label` is one row per request. Details in each recipe's `notes` | The engine cannot generate per-input questions beyond `$each` score questions; the batch and calibrate tools cover the rest. |
| 40 | 2.18 injection_screen | `next_action` is the `profile` input; a risky next action quarantines from `injects` 0.3 at once (spec: 0.4, re-read at 0.3-0.4); the fallback cannot see `next_action` | Engine stops at the most severe decision. |
| 41 | Hook fixtures (TASKS 1.21) | Fixtures in `tests/fixtures/claude_code/` (`pretooluse_v2_*`, `stop_v2*`, `userprompt_v2`, `posttooluse_v2_webfetch`, transcripts, `skill_roster`) are hand-built from the hooks reference and the arch D.26 field list, not captured from a real Claude Code session. The live hook runs use payloads built from them | No captured session available; replace them by procedure in section 11. |

### Known gaps (not deviations by choice; fix or keep listed)

- No CI workflow exists (row 12, TASKS 1.25).
- A full `mise run start` / `restart` / `install` was never run by the implementation packages (section 7); the services were started by the user.
- Hook fixtures are hand-built (row 41).
- `tests/stubs.py::default_answers` emits score answers as `legend: list`, `probabilities: list`; the server reads dicts keyed by level index and answers `OJ_PROTOCOL`. Test-only: `test_mcp_e2e` passes its own `answers`.
- `lint` profile `strict` behaves like `default`; the build spec defines no difference.
- `lint` `estimate` (tokens, idle latency) and the `chunks_estimate` row cost are formulas fitted to the spec examples, not measured (row 25).
- Auth paths (401/403 from a real gateway key) are tested only against the stub app (spec 7 #1).
- `/v1/limits` does not exist on OpenJev yet; every deployment runs on `limit_source: "default"` (row 17).
- Live defects still open (section 10): the legacy-era `batch` cancel (row 20), four recipe cases whose raw case passes in `run_cases` but fails through the recipe's own questions, and an intermittent empty completion from the chat model (`generate`, ~1 in 5 once, 0 of 5 direct).
- Tool schemas are not generated from one source: a new field must be added in `schemas.py` (or the tool module) and in the README table by hand.

### Behaviour confirmed by the Claude Code live suite (2026-10-02)

Found while running `claude -p` against this server (section 8). None is a server defect; callers and test writers must account for them.

- **Claude Code client**
  - It renders an unknown or non-`batch` resource URI as a normal result, so the protocol-level `-32602` is not visible through it. Assert resource errors with an SDK client, not through `claude -p`.
  - It replaces an oversized resource with a "persisted output" notice instead of the body. Read large resources in pieces or through the file link.
- **Server behaviour to document, not change**
  - `ToolError.path` for a path refusal carries the offending value, so a client can show which path was refused.
  - The allowed-roots hint in a path refusal can list one root twice (cosmetic).
  - `ask_image` rejects the `think` option.
  - `dry_run` writes no audit line: nothing was sent to the model.
  - A 404 from `/v1/limits` is not an error: it means `limit_source: "default"`.
- **Model behaviour a test must tolerate:** with a cheap model, `claude -p` may answer a refused call (for example `OJ_INVALID_INPUT` for a relative `output_path`) by retrying it corrected. Assert on the last call of a tool, not on "called exactly once", unless the retry itself is what is tested.

## 4. Configuration

Read once by `config.load_config()`; bad values raise `ConfigError` (exit 2). `OPENJEV_MODEL` and `OPENJEV_URL` are never read. `Config.__repr__` hides `api_key`, `origin_secret` and `token`.

| Variable | Default | Meaning |
|---|---|---|
| `OPENJEV_BASE_URL` | `http://127.0.0.1:8080` | OpenJev server (trailing `/` stripped). Shared name with `run_cases.py`. |
| `OPENJEV_API_KEY` | unset | Sent to OpenJev as `Authorization: Bearer <key>`. |
| `OPENJEV_ORIGIN_SECRET` | unset | Sent to OpenJev as `X-Origin-Secret`. |
| `OPENJEV_MCP_MODEL` | `openjev-latest` | Default decide model. |
| `OPENJEV_MCP_CHAT_MODEL` | `diffusiongemma-26b` | Default chat model of `generate`. |
| `OPENJEV_MCP_TIMEOUT_MS` | `30000` | Base per-request timeout, 100..600000; scaled per call (see `compute_timeout`: `max(base, 3 x idle estimate + 15 ms x think)`, floor 120000 ms when `think` > 0, cap 600000). |
| `OPENJEV_MCP_MAX_INFLIGHT` | `1` stdio, `4` http | Concurrent OpenJev requests from this process, >= 1. |
| `OPENJEV_MCP_MAX_INFLIGHT_BATCH` | `4` | 1..4. Size of the separate batch pool; also the maximum of concurrently running Tasks. |
| `OPENJEV_MCP_RETRIES` | `2` | Retries for retryable codes, >= 0. |
| `OPENJEV_MCP_LOG` | unset | JSONL audit log path. |
| `OPENJEV_MCP_LOG_STATES` | `0` | `1` includes raw states in the audit log. Flags accept 0/1, true/false, yes/no, on/off. |
| `OPENJEV_MCP_TOOLSETS` | `all` | `all` (14 tools) or `core` (6). |
| `OPENJEV_MCP_BAND` | `0.2,0.8` | Default grey band `no_at,yes_at`, `0 <= no_at < yes_at <= 1`. |
| `OPENJEV_MCP_ROOTS` | unset | `:`-separated extra roots, realpath'd; `Config.roots` is `(realpath(cwd), *extra)`. Every file path of `batch`, `batch_results`, `calibrate`, `ask_image`, `compile` and `file://` reads must stay inside. |
| `OPENJEV_MCP_RECIPES` | unset | Extra recipe directory (must exist, else exit 2), same parser and RE2 rules; a failing file is skipped with a warning. |
| `OPENJEV_MCP_ROUTING` | `on` | `off`: routing recipes (`model_routing`) return their interactive fallback with reason "routing off" and send no read. |
| `OPENJEV_MCP_FETCH` | `off` | `on`: `ask_image`/`batch.images` may download https image URLs under the SSRF rules (row 38). |
| `OPENJEV_MCP_TASKS` | `off` | `on`: advertise and serve the MCP Tasks extension (section 1). |
| `OPENJEV_MCP_AUDIT_DIR` | `openjev-audits` | Audit records of `calibrate` and its chunk work files; relative to the cwd, `.../work` holds chunked calibrate rows. |
| `OPENJEV_MCP_TRANSPORT` | `http` | `http` or `stdio`; `--transport` wins. |
| `OPENJEV_MCP_HOST` | `127.0.0.1` | HTTP bind address; `--host` wins. Loopback = `127.0.0.1`, `localhost`, `::1`. |
| `OPENJEV_MCP_PORT` | `8100` | HTTP port, 1..65535; `--port` wins. |
| `OPENJEV_MCP_TOKEN` | unset | Bearer token clients must send to `/mcp`. Required for a non-loopback bind. |
| `OPENJEV_MCP_ALLOWED_HOSTS` | unset | Extra `Host` values, comma-separated, `host:port` or `host:*`. |
| `OPENJEV_MCP_ALLOWED_ORIGINS` | unset | Extra `Origin` values, comma-separated. |
| `OPENJEV_MCP_MAX_BODY_BYTES` | `4194304` | Max HTTP body to `/mcp`; larger is the SDK's 413. |
| `OPENJEV_MCP_DEBUG` | `0` | `1` sets the stderr log level to DEBUG (default WARNING). |

Also read by `openjev-hook`: `OPENJEV_HOOK_TASK` (the task text when `--task` is absent). The live runner reads `OPENJEV_LIVE=1` (same as `--live`). The mise tasks read `OPENJEV_MCP_PORT` and `OPENJEV_BASE_URL` (default: the OpenJev URL of `common.sh`, `OJ_SERVER_URL`).

Precedence: CLI flag, then environment, then default. `test_mcp_docs_consistency.py` fails if `mcp/README.md`'s env table and the `OPENJEV_MCP_*` names read in `config.py` drift apart; update both.

## 5. Transport and security contract

### Streamable HTTP (primary)

- One endpoint `POST /mcp` on `127.0.0.1:8100`, built by `server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=False, ...)` and served by uvicorn (`log_level=warning`, `timeout_graceful_shutdown=5`). The socket is bound before the app starts so a bind failure exits 3. `_bind` prefers the IPv4 result of `getaddrinfo`, so `localhost` listens on `127.0.0.1` (macOS resolves it to `::1` first), which is what the mise readiness URL (`OJ_MCP_URL`, always `127.0.0.1`) expects; a literal `::1` host is still bound as IPv6 and is then not reachable by the mise checks.
- Stateless in both eras: no `Mcp-Session-Id` is ever issued. `json_response=False` so progress can stream as SSE; a handler that finishes before emitting anything gets `application/json`. The modern SSE path sends `x-accel-buffering: no`, `cache-control: no-cache, no-transform` and `: ping` every 15 s (SDK).
- Client headers: `Accept: application/json, text/event-stream` (else 406). On 2026-07-28: `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` for `tools/call` and `resources/read`. A legacy client sends `initialize` then uses `MCP-Protocol-Version: <negotiated>`; both answers are stateless.

| Case | Response |
|---|---|
| Missing/wrong bearer token (token configured), on `/mcp` only | 401, `WWW-Authenticate: Bearer realm="openjev-mcp"`, `{"error": "unauthorized"}`; `hmac.compare_digest` on bytes |
| `Origin` not allowed | 403 |
| `Host` not allowed | 421 |
| Missing `Accept` types | 406 |
| Missing/mismatched `Mcp-Method` or `Mcp-Name` (modern) | 400, JSON-RPC `-32020` |
| Body over `OPENJEV_MCP_MAX_BODY_BYTES` | 413 (SDK) |
| `GET /mcp` (modern header) | 405 |
| Bad/missing envelope keys (modern) | JSON-RPC `-32602` ("params._meta is missing the required envelope key(s): ...") |
| Unsupported modern `protocolVersion` | JSON-RPC `-32022` with `data.supported`, `data.requested` |
| Unknown tool / unknown resource / unknown prompt / unknown task | JSON-RPC `-32602` |
| Invalid tool arguments | 200, tool result `isError: true`, `OJ_INVALID_INPUT` (never `-32602`) |

- Allowed hosts: `127.0.0.1:*`, `localhost:*`, `[::1]:*`, plus `<host>:*` for a non-loopback bind, plus `OPENJEV_MCP_ALLOWED_HOSTS`. Allowed origins: `http://127.0.0.1:*`, `http://localhost:*`, `http://[::1]:*`, plus `OPENJEV_MCP_ALLOWED_ORIGINS`. DNS-rebinding protection is always on; a request without `Origin` (non-browser client) passes.
- `GET /health` (no auth, no OpenJev call, no secrets): `200 {"status": "ok", "name": "openjev-mcp", "version": <version>, "transport": "streamable-http", "protocol_versions": [...], "openjev_base_url": <url without credentials, query or fragment>}`. mise readiness waits on it.

### stdio (secondary)

`openjev-mcp --transport stdio`: `mcp.server.stdio.stdio_server()` + `server.run(...)`. stdout carries JSON-RPC only (spec 2.0.1 rule 10); logging goes to stderr (`openjev-mcp: %(levelname)s %(message)s`). The process exits cleanly when stdin closes. Both eras work; the first request decides.

### Credentials

The inbound MCP token is never forwarded to OpenJev. `OPENJEV_API_KEY` and `OPENJEV_ORIGIN_SECRET` come from the environment only, are never logged, never in results, and never inlined in `lint` `emit` snippets (they use `$OPENJEV_API_KEY` / `os.environ["OPENJEV_API_KEY"]`). The image fetcher sends no credentials. `traceparent`/`tracestate` are never forwarded.

## 6. Error and result contract

### Result envelope (`envelope.py`)

The only two producers of a `tools/call` result:

- Success: `{"content": [{"type": "text", "text": <compact JSON of structuredContent>, "annotations": {"audience": ["assistant"]}}, <resource_link blocks>], "structuredContent": {...}, "isError": false}`. Every success validates against its `outputSchema` and the text equals the JSON.
- Error: `{"content": [{"type": "text", "text": {"error": ToolError}, ...}], "isError": true}`; no `structuredContent`.

Text is produced by `wire.dumps_text` (`separators=(",", ":")`, `ensure_ascii=False`); request bodies by `wire.build_body` (key order `model`, `steps`, `samples`, `think`, `sequential`, `state`, `questions`, `images`) so the OpenJev seed (a hash of the body) is stable.

### ToolError fields

`code`, `message`, `http_status`, `path`, `retryable`, `retry_after_s`, `request_id` always present (null when unknown); `hint` and `server_detail` only when set (`server_detail` is capped at 2048 bytes).

### Codes (`errors.CODES`)

`OJ_INVALID_INPUT`, `OJ_VALIDATION`, `OJ_REJECTED`, `OJ_TOO_LONG`, `OJ_UNKNOWN_MODEL`, `OJ_BAD_TYPE`, `OJ_AUTH`, `OJ_FORBIDDEN`, `OJ_NOT_FOUND`, `OJ_TOO_LARGE`, `OJ_RATE_LIMITED`, `OJ_UNAVAILABLE`, `OJ_OVERLOADED`, `OJ_BAD_IMAGE`, `OJ_SERVER`, `OJ_UNREACHABLE`, `OJ_TIMEOUT`, `OJ_PROTOCOL`, `OJ_INTERNAL`. The HTTP/transport mapping is `mapping.py` (spec 2.4 matrix incl. "Errors added in 1.2"; one test per row in `test_mcp_mapping.py`). Path, root and import refusals (outside the roots, relative path under HTTP, corrupt JSONL line, output path in use) are `OJ_INVALID_INPUT` with a `path` and a `hint`.

### Retry policy (`http.OpenJevClient`)

- Retryable (up to `OPENJEV_MCP_RETRIES`): `OJ_RATE_LIMITED`, `OJ_UNAVAILABLE`, `OJ_OVERLOADED`, `OJ_UNREACHABLE`.
- Once (when retries > 0): `OJ_SERVER`, `OJ_TIMEOUT`; not `OJ_TIMEOUT` when `think` > 0.
- Delay: `retry-after` (else 1 s) plus jitter 0-0.25 s; no retry if the delay would pass the call deadline.
- `GET` helpers do not retry unless asked (`retry=True`).
- In-flight caps: the `default` pool is the `OPENJEV_MCP_MAX_INFLIGHT` semaphore, the `batch` pool a second semaphore sized by `OPENJEV_MCP_MAX_INFLIGHT_BATCH`; both released in `finally` on cancellation. The batch runner shares one cooldown on 429/529 and stops with `stopped_reason: backpressure`.
- `Server-Timing` of the reply is parsed into timings; a read that retried adds a note naming the first failure and the attempt count.

### tools/call pipeline (`tools/dispatch.py::call_tool`)

Unknown tool: `UnknownTool` -> `-32602` in `server.py`. Otherwise: reset links and warnings, set the request `_meta` context var, `prepare` (classify, score, generate), `validate_args` -> `OJ_INVALID_INPUT` result, handler, `success_result`. `ToolError` -> `error_result`; any other exception -> logged, `OJ_INTERNAL` ("internal error in openjev-mcp: <ExcType>"). With Tasks active the extension wraps this whole call.

### Cancellation and progress

On stdio `notifications/cancelled` cancels the handler by request id: no result and no error is sent for that id. On Streamable HTTP the notification is a 202 no-op (SDK) and a client disconnect cancels the request instead; there is no own registry (deviation 20). A cancelled `batch` keeps its whole-line writes, so `resume` continues without duplicates (live-verified on the 2026-07-28 wire). Progress goes only when the request has a `progressToken`; `ProgressEmitter` drops non-increasing values and anything under 1 s after the last emit. Inside a task, progress becomes the task's `statusMessage` and nothing is sent.

## 7. mise contract

| Item | Value |
|---|---|
| Service key / label | `mcp` / `MCP` (`oj_svc mcp` in `mise-tasks/lib/common.sh`) |
| Port | `OPENJEV_MCP_PORT` (default 8100), exported by `common.sh` |
| URLs | `OJ_MCP_URL=http://127.0.0.1:$OPENJEV_MCP_PORT`; endpoint `$OJ_MCP_URL/mcp`; health `$OJ_MCP_URL/health` |
| pid / log | `$OJ_RUN_DIR/.openjev-mcp.pid`, `.openjev-mcp.log` (both in `.gitignore`) |
| Command | `$OJ_PY -m openjev_mcp --transport http --port $OPENJEV_MCP_PORT`, cwd repo root, env `PYTHONUNBUFFERED=1 OPENJEV_BASE_URL=${OPENJEV_BASE_URL:-$OJ_SERVER_URL}` |
| Process match | `SVC_KIND=mcp`, `SVC_MATCH="-m openjev_mcp"`; the `server` match does not accept it |
| Installed check | `oj_mcp_installed`: `import openjev_mcp, mcp, re2, jsonschema`; failure: "the MCP server is not installed. Run: mise run install" |

| Task | MCP behaviour |
|---|---|
| `mise run start` (alias `default`, so also plain `mise run`) | `oj_require_mcp_installed`; port preflight for server, ui and mcp before anything is spawned (hint `OPENJEV_MCP_PORT=$((port+1))`); spawn order OpenJev, UI, MCP; wait 20 s on `/health` of MCP before the model wait; on failure tail the log and die ("the MCP server did not come up ... mise run logs mcp"); final line `MCP:     <url>/mcp` |
| `mise run mcp [--foreground]` | Only the MCP server: no macOS/RAM/model checks. Already running: reports the PID. Else preflight, spawn, wait 20 s on `/health`, print the `claude mcp add --transport http openjev <url>/mcp` line and the `.mcp.json` snippet, warn if OpenJev is not up (does not start it). `--foreground` execs `python -m openjev_mcp --transport http --port ...` in the terminal |
| `mise run stop` | Stop order `mcp`, `ui`, `server`; only processes this project started |
| `mise run restart` | stop then start (no MCP-specific code); needed after a code change because the install is editable but the running process is not reloaded |
| `mise run status` | `show mcp` after the UI |
| `mise run logs [server\|ui\|mcp]` | No argument follows all three logs |
| `mise run install` | Also `pip install -e "./mcp[test]"`; stamp hashes both `pyproject.toml` files; import check adds `openjev_mcp, mcp, re2` |
| `mise run test` | Adds the suite "MCP server (pytest mcp/tests)" (live tests are skipped) |
| `mise run benchmark` | Unrelated to the MCP server |

The implementation packages did not run `start`, `default`, `restart` or `install` (they load the 16 GB model or reinstall); the MCP branch was exercised through `mise run mcp`, `status`, `logs mcp`, `stop` and `bash -n`, and the live runs of section 8 used an OpenJev, UI and MCP already started by the user.

## 8. Testing contract

Run from the repo root, no model, no network beyond loopback spare ports:

```sh
.venv/bin/python -m pytest -q mcp/tests          # or: mise run test
.venv/bin/python -m pytest -q mcp/tests --live   # or OPENJEV_LIVE=1: real model on :8080, sequential
.venv/bin/python mcp/tests/live/run_live.py --full   # everything live; own MCP on 8190-8199, results JSON
```

pytest's rootdir is `mcp/` (its `pyproject.toml`), `--import-mode=importlib`, `pythonpath = ["tests"]`; async tests use `@pytest.mark.anyio`. At the time of writing: 2222 passed, 41 skipped (the skips are the `live` tests; about 43 s). The root `tests/test_api.py` suite runs separately and is unaffected by the MCP package. Never create `mcp/__init__.py` or `mcp/tests/__init__.py` (a regular package named `mcp` would shadow the SDK).

| File(s) | Covers |
|---|---|
| `test_mcp_config`, `_errors`, `_wire`, `_dist`, `_packaging`, `_paths` | env parsing, error types, byte-stable bodies/hashes, light requirements, entry points and `--help`, package data, allowed roots and path rules |
| `test_mcp_stubs` | the stub OpenJev itself (`stubs.py`: stub engine in `openjev.api.create_app`, `fault_transport`, `fail_on_request_transport`, `replay_transport` over `captured.json`) |
| `test_mcp_mapping`, `_http_client`, `_limits` | the 2.4 error matrix, retry/deadline/in-flight pools/cancellation, `/v1/limits` 200/404 and capability matrix |
| `test_mcp_schemas`, `_validate` | inlined Draft 2020-12 schemas, argument validation, spec replays against output schemas |
| `test_mcp_derive`, `_envelope`, `_progress`, `_audit`, `_audit_store` | derived fields, result envelope, progress limits, JSONL audit, calibrate audit records |
| `test_mcp_lint` | every E/W code, autofix, estimate, `emit`, no network I/O |
| `test_mcp_status`, `_resources`, `_library` | `status`, the six resources, templates, `file://`, SDK-free module, templates/patterns/guide data |
| `test_mcp_tools_read`, `_tools_replay`, `_dispatch`, `_filter`, `_ask_image`, `_images`, `_fetch`, `_compile`, `_generate` | the read tools, timeouts, replays of `ex-*` cases (floats to 4 decimals), registry and pipeline, `filter` packing, image loader and SSRF fetch, `compile`, `generate` |
| `test_mcp_batch_import`, `_batch_runner`, `_batch_cursor`, `_batch_store`, `_batch_stats`, `_batch_export`, `_batch_images`, `_batch_tool`, `_batch_results`, `_batch_compare`, `_batch_prompts`, `_batch_walkthrough` | importers, worker pool/reorder/cooldown, cursor and resume, JSONL store and corrupt lines, statistics, exporters, `batch.images`, the `batch` and `batch_results` tools, prompts, the README walkthrough |
| `test_mcp_calibration`, `_calibrate_tool` | reliability bins, Brier, ECE, thresholds, the `calibrate` tool |
| `test_mcp_expr`, `_rules`, `_shell`, `_template`, `_engine_p2`, `_command_gate`, `_recipe_registry`, `_recipe_tool`, `_recipes_p2`, `_recipes_p3a`, `_recipes_p3b` | recipe grammar, RE2 rules, shell split, templating, engine extension keys, `command_gate`, registry and `OPENJEV_MCP_RECIPES`, the `recipe` tool, replays and fail modes of every recipe (via `tests/recipe_harness.py`) |
| `test_mcp_hook`, `_hook_events`, `_cli` | `openjev-hook` payloads/CLI/import isolation (fixtures in `tests/fixtures/claude_code/`), `stop`/`userprompt`/`posttooluse`, `openjev check`/`filter` |
| `test_mcp_server`, `_protocol`, `_protocol_gaps`, `_http_transport`, `_cancel_progress`, `_stdio`, `_tasks` | the SDK server and capabilities, protocol ladder in both eras, extensions/prompts/completion/`_meta`/`resource_link`, HTTP statuses and token mode, cancellation/progress, stdio hygiene, the Tasks extension |
| `test_mcp_skills`, `_docs_consistency` | the 11 skills against `TOOL_NAMES`, README/env/mise names against code |
| `test_mcp_e2e` | stub OpenJev under uvicorn on a spare port, the server as a subprocess on another, SDK clients over HTTP and stdio, token mode, exposed-bind exit 2, port-in-use exit 3, the hook CLI |
| `claude_live/` (10 group files `test_g01`..`test_g10`, harness `cl_*.py`, no-model self-tests `test_cl_selftest*.py`) | 100 cases (T001-T100) driven by headless `claude -p` on the subscription login against an own MCP instance (ports 8200-8299) and the running OpenJev; see below |
| `live/test_live_tools.py`, `test_live_recipes_p2.py`, `_p3a.py`, `_p3b.py`, `live/run_live.py` | `@pytest.mark.live`: the tools and hook events against the real model, each recipe's case-file expectations, and the full runner that writes `live/results/2026-10-phase2-3.json` |

The Claude Code live suite (`mcp/tests/claude_live/`, run with `mise run test-claude-live [--procs N]`) needs OpenJev running and a logged-in `claude`; it is skipped unless `OPENJEV_CLAUDE_LIVE=1` and never part of `mise run test`. It asserts on parsed tool calls and tool results (prose only as a weak secondary check), caps concurrent `claude -p` processes with a cross-process file-lock semaphore (`OJ_CLAUDE_SLOTS`, default 3), and spends subscription usage (first full run: 100 cases, about $1.40, 316 s, mostly `haiku`). Triage policy for a failing case: up to three attempts, each after re-reading the governing spec section and rewriting the test; then spec, test, code, in that order. Result at the first full run: 98 of 100; T029 (shared work dir between pytest processes; now per process) and T030 (one corrective retry by the model; now asserts the last `batch` call) were harness defects, and T100 was a real bug (see "Fixed since"); all 100 pass after the fixes. Details: `mcp/tests/claude_live/{ARCHITECTURE,TASKS,README,RESULTS}.md`.

`mcp/spec.md` itself is not parsed by a test; section 11's release checklist (step 4) is the manual check of its env, tool and mise names against the code.

Live results (2026-10-02, real MLX model on :8080, own MCP on spare ports, `tests/live/results/2026-10-phase2-3.json`): 47 checks, 43 passed. `run_cases` over `00`-`24` = 25 of 26 case files at or above the spec 6.3 rerun column (e.g. `00` 63/63, `03` 14/14, `04` 18/18); only `06` (19/21 against 20/21) is below; the misses are the documented known limitations (`02` gate-19, `06` two cases, `20` tax-18). Hook over the 14 gate cases 14/14 (gate-10 `ask`). Recipe pytest `--live`: 23 passed, 5 failed, 1 skipped (open: `done_gate` gate-03, `skill_selection` sel-19, `semantic_lint` secrets x2, `entity_match` cat-01; the raw cases pass in `run_cases`). Batch: 200-row CSV at concurrency 2 in 4 cursor calls, 64.4 s, 3.11 req/s, 200 unique ids, no duplicates or gaps; an interrupted 80-row run resumed with 11 skipped rows and no duplicates. Failed checks: `run_cases 06` (model), the recipe pytest file, the `batch_results` `ojui-batch` export (row 35, fixed since) and the legacy-era batch cancel (row 20).

Verifier rerun (`run_live.py --full --quick`, own MCP on 8196/8197, `docs/live-runs.md`): 18 of 20 checks pass; the two failures are the `generate` empty-completion flake and the legacy cancel (row 20). 200-row batch 57.6 s, 3.47 req/s; `test_live_tools.py` 11 passed; Tasks, `ask_image`, `calibrate`, `compile`, `filter`, `recipe` and the four hook events pass; `batch_results` exports are byte-identical to `batch` exports. Set the live port with `OJ_LIVE_PORT` (Tasks-on server on port + 1). Measured: MCP read tools mean 148-219 ms per call (p50 182-312 ms), `yes_no` 7.1 req/s sequential and 9.3 req/s at concurrency 4, `filter` p50 71 ms, hook 0.5-0.8 s when the model decides and 70-80 ms when a rule decides.

## 9. TASKS.md mapping

Status "done" = code and its "Done when" test exist and pass in `mcp/tests`; "row N" = done with deviation N of section 3; "live" = evidence in `tests/live/results/2026-10-phase2-3.json` ("live" results file, section 8).

### Phase 1

| Id | Item | Module(s) | Test(s) | Status |
|---|---|---|---|---|
| 1.1 | Distribution, scripts | `mcp/pyproject.toml`, `__init__.py`, `__main__.py`, `cli.py` | `test_mcp_dist`, `_packaging` | done (mcp 2.2.0 floor `>=2.2,<3`) |
| 1.2 | Config | `config.py`, `audit.py` (`OPENJEV_MCP_LOG`) | `test_mcp_config`, `_audit` | done; rows 2, 14-16 |
| 1.3 | Server, `tools/list` | `server.py`, `http_app.py`, `tools/dispatch.py` | `test_mcp_server`, `_protocol`, `_http_transport` | done; rows 1, 6 |
| 1.4 | HTTP client | `http.py` | `test_mcp_http_client` | done |
| 1.5 | Error mapping | `mapping.py`, `errors.py` | `test_mcp_mapping`, `_errors` | done; row 4 |
| 1.6 | Result envelope | `envelope.py`, `wire.py` | `test_mcp_envelope`, `_tools_replay` | done |
| 1.7 | Schemas, `openjev://schema` | `schemas.py`, `validate.py`, `resources.py` | `test_mcp_schemas`, `_validate`, `_resources` | done |
| 1.8 | Derived fields | `derive.py` | `test_mcp_derive`, `_tools_replay` | done; row 25 |
| 1.9 | Limits, `openjev://limits` | `limits.py`, `resources.py` | `test_mcp_limits`, `_resources` | done; rows 17, 19, 26 |
| 1.10 | `status` | `tools/status.py` | `test_mcp_status` | done |
| 1.11 | `lint` | `lint.py`, `tools/lint_tool.py` | `test_mcp_lint` | done; rows 18, 23; `strict` gap |
| 1.12-1.15 | `ask`, `yes_no`, `classify`, `score` | `tools/read.py` | `test_mcp_tools_read`, `_tools_replay` | done; live: `run_cases 00` 63/63, MCP ex-* calls 11/11 returned |
| 1.16-1.19 | Expression parser, RE2 rules, shell split, templates | `recipes/expr.py`, `rules.py`, `shell.py`, `template.py` | `test_mcp_expr`, `_rules`, `_shell`, `_template` | done |
| 1.20 | `command_gate` recipe | `recipes/engine.py`, `recipes/builtin/command_gate.json` | `test_mcp_command_gate` (replays) | done; live: 14/14, gate-10 `ask`; rows 22, 29 |
| 1.21 | `openjev-hook pretooluse` | `hook.py`, `claude_hooks.py` | `test_mcp_hook` | done; rows 5, 24, 41 |
| 1.22-1.23 | Skills `openjev-decisions`, `openjev-question-authoring` | `skills/*/SKILL.md` | `test_mcp_skills` | done |
| 1.24 | Docs | `mcp/README.md`, root `README.md` | `test_mcp_docs_consistency` | done |
| 1.25 | CI job | none (`mise run test`) | - | **not done**; row 12 |
| 1.26-1.28 | `server/discover`, `initialize`, per-request `_meta`, `ttlMs`/`cacheScope` | `server.py`, `resources.py` | `test_mcp_protocol` | done; mismatches 1-4, 6 |
| 1.29 | Argument validation path | `validate.py`, `tools/dispatch.py` | `test_mcp_validate`, `_protocol` | done; mismatch 7 |
| 1.30 | Cancellation, progress | `progress.py`, `http.py` (semaphores), SDK cancel scopes | `test_mcp_cancel_progress` | done; rows 20, 21 |
| 1.31 | stdio hygiene, roots | `server.py`, `config.py`, `paths.py` | `test_mcp_stdio`, `_paths` | done; roots enforced since 1.3 (rows 14, 27) |
| 1.32 | 1.2 corrections | `lint.py`, `mapping.py`, `tools/read.py`, `schemas.py`, `tools/status.py` | `test_mcp_lint`, `_mapping`, `_tools_read`, `_status` | done |
| - | mise integration (user requirement) | `mise-tasks/mcp`, `start`, `stop`, `status`, `logs`, `install`, `test`, `lib/common.sh` | `test_mcp_docs_consistency` (task names), manual (section 7) | done; `start` not run by the packages |
| - | Streamable HTTP + e2e (user requirement) | `http_app.py`, `server.py::run_http` | `test_mcp_http_transport`, `_e2e` | done |

### Phase 2 (TASKS 2.1-2.16)

| Id | Item | Module(s) | Test(s) | Live evidence | Status |
|---|---|---|---|---|---|
| 2.1 | `filter`, injection-safe packing | `tools/filter.py` | `test_mcp_filter` | `filter ex-filter x3`: kept `[L4]`, 1 request | done |
| 2.2 | `recipe` tool: `dry_run`, fail modes, `degraded` | `tools/recipe_tool.py`, `recipes/engine.py` | `test_mcp_recipe_tool`, `_engine_p2` | via the recipe pytest below | done; row 32 |
| 2.3 | Recipes `act_or_ask`, `injection_screen`, `done_gate`, `moderation`, `model_routing`, `skill_selection`, `typed_call` | `recipes/builtin/*.json` | `test_mcp_recipes_p2`, `_command_gate`, `live/test_live_recipes_p2.py` | recipe pytest `--live` 23 passed / 5 failed / 1 skipped; moderation 17/17, model_routing 15/15, injection 3/3, act_or_ask 3/3; open: `done_gate` gate-03, `skill_selection` sel-19 | done; live partial; rows 29, 39, 40 |
| 2.4 | Hooks `stop`, `userprompt`, `posttooluse`; `openjev check`, `openjev filter` | `hook.py`, `claude_hooks.py`, `cli.py` | `test_mcp_hook_events`, `_hook`, `_cli` | stop blocks an unverified done claim, userprompt hints `pdf` (p 0.9997), posttooluse quarantines an injected WebFetch | done; rows 30, 41 |
| 2.5 | Resources `openjev://recipes`, `/{id}`, `patterns`, `guide/authoring` | `resources.py`, `library.py`, `recipes/registry.py`, `data/` | `test_mcp_resources`, `_library`, `_recipe_registry` | - | done |
| 2.6 | Extra recipe directory `OPENJEV_MCP_RECIPES` | `config.py`, `recipes/registry.py` | `test_mcp_recipe_registry`, `_config` | - | done |
| 2.7 | Skills agent-gates, code-checks, dispatch, triage-routing, retrieval-relevance, multistep | `skills/*/SKILL.md` | `test_mcp_skills` | - | done |
| 2.8 | Batch importers | `batch/importers.py` | `test_mcp_batch_import` | 200-row CSV imported (`batch_200.csv`, 32 pool states) | done |
| 2.9 | Batch runner | `batch/runner.py` | `test_mcp_batch_runner` | concurrency 2: 3.11 req/s | done; row 34 |
| 2.10 | Cursor codec and resume | `batch/cursor.py`, `batch/store.py` | `test_mcp_batch_cursor`, `_store` | 4-call cursor loop, no gaps; resume skipped 11 rows, no duplicates | done; row 28 |
| 2.11 | `batch` tool | `tools/batch_tool.py` | `test_mcp_batch_tool`, `_stats`, `_walkthrough` | the two recorded live runs (200-row cursor loop; interrupted 80-row resume) | done; rows 20, 34 |
| 2.12 | Exporters CSV, Markdown, `ojui-batch` | `batch/exporters.py` | `test_mcp_batch_export` | CSV and Markdown byte-identical; `ojui-batch` differs (row 35) | done; live defect open |
| 2.13 | `batch_results` | `tools/batch_results.py`, `batch/compare.py` | `test_mcp_batch_results`, `_compare` | views review/stats/rows ok, `compare_to` ok, filtered JSONL readable | done; row 35 |
| 2.14 | Templates, prompts `start_batch`/`review_batch`, `completion/complete`, `resources/templates/list` | `batch_prompts.py`, `prompts.py`, `completion.py`, `resources.py` | `test_mcp_batch_prompts`, `_protocol_gaps`, `_resources` | - | done; row 33 |
| 2.15 | Batch rows in `openjev-decisions`, backlog paragraph, `openjev-data-records` | `skills/*/SKILL.md` | `test_mcp_skills` | - | done |
| 2.16 | README batch section | `mcp/README.md` | `test_mcp_docs_consistency`, `_batch_walkthrough` | - | done |

### Phase 3

| Item | Module(s) | Test(s) | Live evidence | Status |
|---|---|---|---|---|
| `batch.images` | `images.py`, `tools/batch_tool.py` | `test_mcp_batch_images`, `_images` | - (no live run) | done; row 34 |
| `calibrate` (incl. `from_batch`, drift, audit store) | `calibration.py`, `tools/calibrate.py`, `audit_store.py` | `test_mcp_calibration`, `_calibrate_tool`, `_audit_store` | `ex-cal-1..7` x2: accuracy 1.0, gap 0.9974, `t_fit` 0.5007, band `[0.05, 0.95]`, borderline `ex-cal-7` | done; row 36 |
| `ask_image` + SSRF fetch | `tools/ask_image.py`, `images.py`, `fetch.py` | `test_mcp_ask_image`, `_images`, `_fetch` | `ex-image`: hotdog 0.998, cat 0.0003 (URL fetch not run live) | done; row 38 |
| `compile` incl. `recipe.variants` | `tools/compile.py` | `test_mcp_compile` | `ex-compile-*`: recipe `ticket_triage`, types noul/choice/score/not_typed | done |
| `generate` | `tools/generate.py` | `test_mcp_generate` | `ex-generate` x5: 5/5 ok, 0 retried (direct chat 0/5 empty) | done; row 37 |
| Remaining recipes and variants (the other 21 of the 29) | `recipes/builtin/*.json` | `test_mcp_recipes_p3a`, `_p3b`, `live/test_live_recipes_p3a.py`, `_p3b.py` | recipe pytest `--live` as above; failing: `semantic_lint` secrets x2, `entity_match` cat-01, `taxonomy_classify` tax-18 (known limitation) | done; live partial; row 39 |
| Prompts `author_question`, `audit_question`, `explain_answer` | `tools/compile.py`, `tools/calibrate.py`, `prompts.py` | `test_mcp_compile`, `_calibrate_tool`, `_protocol_gaps` | - | done |
| `openjev://audits/{question_hash}` | `audit_store.py`, `resources.py` | `test_mcp_audit_store`, `_resources` | - | done; row 33 |
| MCP Tasks extension | `tasks.py`, `ext.py`, `server.py` | `test_mcp_tasks`, `_protocol_gaps` | `OPENJEV_MCP_TASKS=on` over HTTP: capability advertised, `resultType: task`, 2 polls to `completed`, result ids equal the synchronous result, cancel -> `cancelled` | done; row 31 |
| Skills ui-vision, calibration, data-records recipe parts | `skills/*/SKILL.md` | `test_mcp_skills` | - | done |
| Live verification | `tests/live/` | `live/test_live_*.py`, `live/run_live.py` | 47 checks, 43 passed (section 8) | done; 4 failed checks open |

## 10. Roadmap

What is left after 1.4. Spec sections are in `docs/mcp-skill-spec/OPENJEV_MCP_SKILLS_SPEC.md`.

### Phase 0 server prerequisites

`GET /v1/limits` (TASKS 0.1-0.3) is absent on the server today (live `404`, 2026-10-02); the MCP side maps the 404 to documented defaults (`limit_source: "default"`) with a warning, `prompt_tokens` null until the backend is known, and W405 cannot be precise (spec 7 #32). When the endpoint lands, re-test `limits.py` against its real shape.

### Open defects found by the live run

| Item | Detail | Where to fix |
|---|---|---|
| Legacy-era `batch` cancel does not stop the run; an immediate resume answers "output_path is in use" | Stateless HTTP ignores `notifications/cancelled` on the legacy era (row 20); the 2026-07-28 wire cancels by disconnect | Release the `output_path` lock when the client is gone, or a cancel registry |
| Recipe cases failing through the recipe's own questions | `done_gate` gate-03 (`next.choice` allow_stop), `skill_selection` sel-19 (`pdf` vs `none`), `semantic_lint` `secrets-pos-and-neg-pair` (0.53 < 0.85), `entity_match` cat-01 (score 1.40 < 1.7) | recipe JSON (questions/policy), then rerun `pytest --live mcp/tests/live -k <recipe>` |
| `generate` returned one empty completion in five (retried; direct chat 0 of 5) | Intermittent model-server flake, cold or first request | none in the MCP; `retried` is reported |
| Known model limitations (spec 6.4) | `02` gate-19, `06` secrets-real-token-flagged and coasked-interference, `20` tax-18 fail in `run_cases` too | none (model) |
| `classify` abstain wording | A spec example says the abstain "lands on the escape option"; the tool returns `label: null`, `abstained: true`, `top: <escape>` | docs only; callers check `abstained` and `top` |

Fixed since the first live run (2026-10-02): recipes from `OPENJEV_MCP_RECIPES` rejected by the `recipe` tool (found by claude_live T100; `dispatch.specs()` cached the recipe schema by tool count only, now keyed by `(len, config.recipes_dir)`, test `test_dispatch_accepts_extra_dir_recipe`); the `batch_results` export mismatch (row 35); `command_gate` over-denying `curl | sh` (gate-10, row 29); the reviewer findings on `.ndjson` resource links, `O_NOFOLLOW` and mode 0600 on batch outputs, the `calibrate` work-file lock and the Tasks live-task cap (`4 x max_running`, then the synchronous result).

### Not built, in scope of the build spec

- A CI workflow (TASKS 1.25).
- The recipe variants listed in row 39 (per-parameter `typed_call`, `rag_gate` requery/rerank, `claim_check` screen, `goal_claim`, `flags`, subtree-pruned taxonomy beams).
- Pillow-free `ask_image` (Pillow is an optional extra, imported lazily).
- A live run of the image URL fetch (`OPENJEV_MCP_FETCH=on`) and of `batch.images`.
- Replace the hand-built Claude Code hook fixtures with captured payloads (row 41).
- Auth paths against a real gateway key (spec 7 #1).
- A full `mise run start` end to end by an implementation package.
- Optional protocol items (next section): `capabilities.extensions` in discover when no extension is active, `-32021`, `x-mcp-header`, RFC 9728 metadata, `WWW-Authenticate` `resource_metadata=`.
- Roadmap watch (modelcontextprotocol.io/development/roadmap, 2026-08-22, not final): HTTP over stdio, ETag caching, DPoP and agent identity, a `tools/call` result-shape redesign, progressive discovery, Tasks moving toward core, server-initiated events and webhooks. None has a date or release candidate; the Tasks move would let `tasks.py` be replaced (section 11, SDK step 6).

### Not planned

Batch pause, per-row images in `batch`, user templates from browser storage, elicitation for review triage (2.19), and everything under "Not implemented" in section 2 (`subscriptions/listen`, `notifications/message`, RFC 9728 PRM, `x-mcp-header`, MRTR, roots/sampling, stateful sessions). Revisit when a client needs it; the adoption checklist in section 11 applies.

### Extension points

| Item | Extend | Contract tests to add or update |
|---|---|---|
| Any new tool | Section 11 "Add a tool" | `test_mcp_dispatch`, `test_mcp_protocol` (order, schema walk, invalid args as results), a handler test, `test_mcp_skills`, `test_mcp_docs_consistency` |
| New recipe | JSON in `recipes/builtin/` (or `OPENJEV_MCP_RECIPES`); new engine keys go in the `engine.py` docstring and section 3 row 32 | a recipe file in `test_mcp_recipes_p*` (load errors, rules, replays, fail modes, `degraded`) and its live `test_file` |
| New hook event | A subcommand in `hook.py`, field names in `claude_hooks.py` only | `test_mcp_hook_events` (a fixture per event, fail mode, import isolation), the README example in `test_mcp_docs_consistency` |
| New resource, template, prompt or completer | `resources.RESOURCES`/`TEMPLATES`/`READERS`, `prompts.PROMPTS`, `completion.COMPLETERS` | `test_mcp_resources`, `test_mcp_protocol_gaps`, `test_resource_uris_in_listing_are_documented_in_spec` (the URI must appear in the build spec) |
| New MCP extension | A factory in `ext.EXTENSIONS` returning an `Extension` (identifier, `settings()`, `methods()`, `intercept_tool_call`) | `test_mcp_protocol_gaps` (advertised on 2026-07-28 only, methods version-gated, `tools/call` wrapped), a `test_mcp_tasks`-style file |
| New MCP revision | Section 11 "Add or drop an MCP revision" | `test_mcp_protocol`, `_http_transport`, `_stdio` |

### Open items (TASKS.md, outside the phases)

- `tests/spec_build/p*.md` sources are newer than the rendered build spec and reference cases missing from `00-spec-examples.json` / `captured.json` (`ex-filter-pick-best`, `ex-author-fixed`, ...), so `render.py` fails. Finish that revision (author, capture live, render), then review the diff against rendered 1.1.
- Shared-load `batch` latency (spec 7 #26): measured 3.11 req/s at concurrency 2 on MLX with two questions per row and `sampling: fast`, an idle-server figure; record `req_per_s` per backend (vLLM) and under shared load when available.

### Other open risks that touch the built surface (spec 7)

#1 auth paths never exercised live; #4 hook latency budget (live hook runs over the 14 gate cases: p50 855 ms, p95 2.4 s; a rule-decided command is inside the 300 ms test budget); #6 `think` is not reproducible, never cache it; #14 hook API drift (isolated in `claude_hooks.py`); #21 MCP revision churn; #10 `command_gate` is a screen, not a boundary.

## 11. Upgrade procedure

### Bump the SDK floor or ceiling

1. `pip index versions mcp`; read the new source (`mcp_types/version.py`, `mcp/server/runner.py`, `lowlevel/server.py`, `extension.py`, `streamable_http_manager.py`, `_streamable_http_modern.py`, `transport_security.py`, `caching.py`, `mcp/shared/inbound.py`).
2. Install it into `.venv` (`pip install -e "./mcp[test]"` after editing `mcp/pyproject.toml`) and run `mcp/tests`. The arch A.2 probes are tests now:
   - `test_mcp_protocol.py`: discover result and versions, `-32022` and its `data.supported`, legacy counter-offer, `-32602` envelope errors, `resultType` + serverInfo `_meta`, cache fields, statelessness, schema walk, invalid args as results, unknown tool/resource.
   - `test_mcp_protocol_gaps.py`: capabilities, extension advertisement and `tools/call` wrapping, prompts/completion round trips, `subscriptions/listen` error, `_meta` handling.
   - `test_mcp_http_transport.py`: `-32020`, 403/421/406/401/405, no `Mcp-Session-Id`, legacy stateless initialize, SSE progress, real-process smoke.
   - `test_mcp_cancel_progress.py`, `test_mcp_stdio.py`: cancellation, progress, stdout hygiene.
3. A failing probe is either a regression to fix or an SDK change to record: update section 2 (mismatch list), the assertion, and this file in the same change. If `-32022` `data.supported` starts listing all three revisions, mismatch 1 disappears.
4. Check the `server.py`/`http_app.py`/`tasks.py`/`ext.py` call sites against the new signatures (`Server(...)`, `streamable_http_app(...)`, `CacheHint`, `ctx.session.report_progress`, `Extension`, `MethodBinding`, `compose_tool_call_handler`, `ServerRequestContext`, `CLIENT_CAPABILITIES_META_KEY`); these are the only modules that import the SDK.
5. Re-check the HTTP cancellation behaviour (deviation 20): `notifications/cancelled` POSTs ignored, disconnect cancels the upstream request and frees the semaphore, in both eras.
6. If the SDK adds a Tasks API: replace `tasks.py` with it (rule 9; deviation 31), keep `OPENJEV_MCP_TASKS`, the `batch`/`calibrate` scope and the synchronous fallback, and re-run `test_mcp_tasks.py` plus the live "Tasks over HTTP" check.
7. Raise the floor only to a release you ran the suite on; keep `<3` until a major is reviewed. Update section 2 and `mcp/README.md`.

### Add or drop an MCP revision (adoption checklist)

1. Stability gate: adopt a revision only when it is published as final on modelcontextprotocol.io (not a draft or release candidate) and an official Python SDK release lists it in `mcp_types.version.KNOWN_PROTOCOL_VERSIONS`. Record the date, the SDK version and the reason in section 2 (as done for 2026-07-28 / mcp 2.2.0).
2. Read the revision's changelog against spec 2.0.1 rules 1-10 and list what changes for this server: handshake or envelope keys, required headers (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`), error codes (`-32020`, `-32022`, `-32602`), `resultType`, caching fields (`ttlMs`, `cacheScope`), list pagination, cancellation and progress semantics, tool result shape (`structuredContent`, `outputSchema`, annotations), capabilities that become mandatory, and the status of the optional items in section 2 "Not implemented" and of the official extensions (the Tasks identifier, its methods and result shape, `capabilities.extensions`).
3. Bump the SDK first (previous checklist) if the current pin does not serve the revision.
4. Add it to `PROTOCOL_VERSIONS` in `openjev_mcp/__init__.py` (order = discover order, newest first); `/health` and `server/discover` follow. A new modern revision goes first; the previous modern one stays for at least one minor release. If the Tasks extension's methods change, change `tasks.ERA` and `MethodBinding` versions with it.
5. Tests: add it to `LEGACY_VERSIONS`/`MODES` in `test_mcp_protocol.py` and `test_mcp_protocol_gaps.py`, the HTTP legacy test in `test_mcp_http_transport.py` and `test_mcp_stdio.py`; assert discover and `/health` list it; re-run the ladder (envelope check, version check, dispatch) for both eras; check mismatches 1-10 of section 2 still hold, or drop them.
6. Schemas: if the revision changes JSON Schema rules for `inputSchema`/`outputSchema`, re-run the schema walk; tool schemas may only gain optional fields within a minor release.
7. Clients: confirm Claude Code (the main client) and the MCP Inspector connect in the new era over HTTP and stdio, then update the registration snippets in `mcp/README.md`.
8. Dropping a revision: remove it from `PROTOCOL_VERSIONS` and the test lists only after the main clients no longer send it; note the drop in this file's change log as a minor bump.
9. Update section 2 (revision table, SDK, mismatches) and the header of this file.

### Add a tool

1. Schemas: input/output in `schemas.py` or via `schemas.register_tool_schemas` inside the tool module's `register(config)` (inlined, Draft 2020-12, object root, no root `oneOf/anyOf/allOf`); `openjev://schema` follows.
2. Handler in `tools/` returning the structured dict (raise `ToolError`, never build envelopes by hand). Model reads go through `tools/read.run_read` (lint, timeout, retries, `meta`, progress); batch-like work uses the `batch` pool and `batch/runner.py`; file paths go through `paths.py`.
3. Expose `register(config) -> ToolSpec`, add it to `PHASE_TOOLS` in `tools/dispatch.py` in the position the spec gives, and add the name to `TOOL_NAMES` in `__init__.py` (the dispatcher asserts they match). Decide whether it belongs in `CORE_TOOL_NAMES` (core must stay an ordered subset). Pipeline order is fixed: `prepare`, `validate_args`, handler. If it should run as a Task, add it to `tasks.TOOLS`.
4. Tests: `test_mcp_dispatch`, a handler test, a replay if a captured case exists, `test_mcp_schemas`/`_validate`, `test_mcp_protocol` (list order, schema walk, text equals JSON), `test_mcp_skills` (skills may mention every tool except `generate`), a live test in `tests/live/` when the tool reads.
5. Docs: `mcp/README.md` surface table and this file's section 1; `test_mcp_docs_consistency.py` must pass. Add its env variables to `config.py` and both env tables (README and section 4) together.
6. Tool count and routing: update the `openjev-decisions` skill table.

### Re-record hook fixtures for a new Claude Code major

The fixtures (`pretooluse_v2_bash.json`, `pretooluse_v2_non_bash.json`, `stop_v2.json`, `stop_v2_transcript.jsonl`, `stop_v2_blocked_transcript.jsonl`, `userprompt_v2.json`, `posttooluse_v2_webfetch.json`, `skill_roster.json`, `transcript.jsonl`) are hand-built from the hooks reference and arch D.26, so the first run of this procedure also replaces them for major 2.

1. Capture a real payload for each event (PreToolUse Bash and non-Bash, Stop with and without a prior block, UserPromptSubmit, PostToolUse WebFetch and an MCP tool) and a transcript; save them as `tests/fixtures/claude_code/<event>_v<N>_*.json`.
2. Adjust `claude_hooks.py` only (field names, output shapes, the `prompt`/`prompt_text` fallback) and add the major to `SUPPORTED_CLAUDE_CODE`; keep the old fixtures while the old major is supported.
3. Run `test_mcp_hook.py` and `test_mcp_hook_events.py`; re-measure the p95 overhead of a rule-decided command (< 300 ms) and the import-isolation test; rerun the live hook checks (`run_live.py --gate`, `--full` hook section).

### Release checklist

1. `mcp/pyproject.toml` version, `openjev_mcp.__version__` and (if the build spec changed) `SPEC_VERSION` agree; update the header and change log of this file.
2. `.venv/bin/python -m pytest -q mcp/tests` green; `mise run test` green.
3. `openjev-mcp --help`, `openjev-hook pretooluse --help`, `openjev-hook stop --help`, `openjev --version` exit 0.
4. Spot-check this file against the code: env names (`grep -o '"OPENJEV_[A-Z_]*"' mcp/openjev_mcp/config.py`) and section 4; tool names (`TOOL_NAMES`), resource, template and prompt names and section 1; mise names and section 7; the test count of section 8.
5. `mise run mcp` then `curl -s http://127.0.0.1:8100/health`, `mise run status`, `mise run stop` (these do not load the model). Human-run, not CI: `mise run start` / `restart` (the running MCP does not pick up code changes by itself), `.venv/bin/python mcp/tests/live/run_live.py --full` on a free GPU, and `pytest --live mcp/tests/live`; record the result file and update section 8 and the "open defects" table of section 10.
