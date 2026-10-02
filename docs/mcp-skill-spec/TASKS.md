# OpenJev MCP server: tasks

Task breakdown for `OPENJEV_MCP_SKILLS_SPEC.md` version 1.2. Phases and scope are defined in
spec 2.0; "Done when" points at the acceptance criteria (spec 6.7) and the contract tests (spec
6.6). Only phase 1 is scheduled. Phase 2 is planned and now includes `batch` and `batch_results`
(spec 2.0, G12). Phase 3 is deferred and gets re-planned after phase 2 ships.

Conventions: every task lands with its tests in the same change. "CI" means the contract tests
of 6.6 (no model). "Live" means a run against a real OpenJev server with
`docs/mcp-skill-spec/tests/run_cases.py` or the hook, done by a human and not in CI.

## Phase 0: server prerequisites (in `openjev/`, optional for phase 1)

- [ ] **0.1 `GET /v1/limits`** as proposed in `00-api-surface.md` section 15: `backend`,
  `logs_bodies`, request limits, per-model caps, routed models without URLs or secrets.
  Done when: one test per backend in `tests/test_api.py` (stub engine); auth applies as on the
  other `/v1/` routes. Phase 1 and phase 2 work without it (limits and model capabilities come
  from defaults, `limit_source: "default"`, `prompt_tokens` null while the backend is unknown).
  Recommended before phase 2 ships: `batch` `concurrency` and W405 need the backend to be known.
- [ ] **0.2 Fix or re-word the log comment** in `openjev/api.py`: "a rejected body is never
  logged" is false at debug level (spec 2.1 principle 7, API surface 12.6). Either skip the
  debug request log for bodies that end in 4xx, or change the comment and the README.
  Done when: the behaviour and the comment agree, and a test covers the chosen behaviour.
- [ ] **0.3 (optional) MLX model timing**: record model time in `MlxEngine`, so
  `Server-Timing: model;dur` stops reading 0.0. Only safe after 0.1, because nothing may infer
  the backend from `dur` any more.

## Phase 1 (v1)

### 1A. Package skeleton

- [ ] **1.1 Distribution `openjev-mcp`** in `mcp/` with its own `pyproject.toml`: package
  `openjev_mcp`, Python >= 3.10, dependencies `mcp` (the first release implementing MCP 2026-07-28 and legacy
  `initialize`; the floor is pinned here), `httpx>=0.27`, `jsonschema>=4.18`,
  `google-re2>=1.1`, and the scripts `openjev-mcp`, `openjev-hook`, `openjev` (spec 2.0).
  Done when: `pip install -e mcp/` in a clean venv doesn't pull in `transformers`;
  `openjev-mcp --help` and `openjev-hook --help` run.
- [ ] **1.2 Config** from the environment, with the `OPENJEV_MCP_*` names of spec 2.1. Only
  `OPENJEV_BASE_URL` and `OPENJEV_API_KEY` are shared with the server.
  Done when: a unit test shows `OPENJEV_MODEL` is ignored and `OPENJEV_MCP_MODEL` honoured.
- [ ] **1.3 stdio server** on the MCP Python SDK (protocol 2026-07-28 primary, 2025-11-25 and 2025-06-18
  legacy; spec 2.0.1). `tools/list` returns only the phase-1 tools, in a fixed order with
  `ttlMs`/`cacheScope`, annotations and titles (spec 2.5).
  Done when: the protocol tests of 6.6 pass through the SDK's in-memory client.

### 1B. Core library

- [ ] **1.4 HTTP client**: auth header, per-process in-flight limit, timeouts, the retry policy
  of 2.4 (only the listed codes, `retry-after` plus jitter).
  Done when: the retry rows of the 2.4 error matrix pass in CI.
- [ ] **1.5 Error mapping** on (status, `error_type`), including 401, 403
  `authentication_error` and 403 `permission_error`; limits in messages are parsed, not assumed.
  Error results are `isError: true`, with a text JSON block and no `structuredContent`.
  Done when: the full 2.4 error matrix passes in CI against `openjev.api.create_app` with a stub
  engine, plus `httpx.MockTransport` for transport faults.
- [ ] **1.6 Result envelope**: `structuredContent`, a text block with the same JSON, and output
  validation against `outputSchema` in tests.
  Done when: every success replay validates against its `outputSchema`.
- [ ] **1.7 Schemas**: the `$defs` of 2.2 inlined into each tool; no root
  `oneOf`/`anyOf`/`allOf`; the `openjev://schema` resource.
  Done when: a test walks every `inputSchema` root.
- [ ] **1.8 Derived fields** (spec 2.3): band, margin, `p_top`, `runner_up`, entropy,
  abstained, level, spread, bimodal, `chunks_estimate`.
  Done when: the replayed MCP outputs of section 2 match to 4 decimals.
- [ ] **1.9 Limits**: loaded from `GET /v1/limits` when present, else defaults with
  `limit_source: "default"`; resource `openjev://limits`.
  Done when: the limits tests of 6.6 pass, with and without a stub `/v1/limits`.

### 1C. Tools

- [ ] **1.10 `status`** (spec 2.17). Done when: the 6.7 row passes; no request is ever sent
  without the configured key.
- [ ] **1.11 `lint`** (spec 2.13): every E and W code, autofix, estimate; limit-dependent codes
  follow `limit_source`. Done when: the 6.7 row passes (a positive and a negative fixture per
  code; no network I/O).
- [ ] **1.12 `ask`** (spec 2.6). Done when: the 6.7 row passes.
- [ ] **1.13 `yes_no`** (spec 2.7). Done when: the 6.7 row passes.
- [ ] **1.14 `classify`** (spec 2.8). Done when: the 6.7 row passes.
- [ ] **1.15 `score`** (spec 2.9). Done when: the 6.7 row passes.

### 1D. Recipe engine (the subset phase 1 needs) and the command gate

- [ ] **1.16 Expression parser** for `combine` and `when` (spec 2.18 grammar): a hand-written
  recursive-descent parser, an AST evaluator, and load-time errors for unknown identifiers,
  built-ins or a missing `otherwise`. No `eval`.
  Done when: the grammar tests of 6.6 pass, including the rejection cases.
- [ ] **1.17 Deterministic rules on RE2**: `scope`, `unless`, the 512-character pattern cap and
  the 64 KiB input cap; backreferences and lookaround are rejected at load.
  Done when: the pattern-rejection tests pass.
- [ ] **1.18 Shell split** (the `compute` step of `command_gate`): segments on every operator
  for the rules, parts on `;`/`&&`/`||`/newline for the reads; an unparsable command is never
  allowed by a rule.
  Done when: every command-gate rule test of 6.6 passes.
- [ ] **1.19 Template substitution** with JSON-string escaping; `{{{raw}}}` only for inputs
  marked `x-openjev-raw`.
  Done when: an injected newline in `command` stays on the command's line.
- [ ] **1.20 Built-in `command_gate` recipe file** (spec 2.18 and 5.3, questions verbatim).
  Done when: `03-agent-tool-call-gate.json` passes 14/14 live through the engine.
- [ ] **1.21 `openjev-hook pretooluse`**: reads the hook JSON on stdin and writes the decision
  JSON; fails closed (`ask`, or `deny` with `--unattended`); one module holds the Claude Code
  field mapping.
  Done when: the 6.7 hook row passes, including CLI overhead p95 < 300 ms and a contract test
  per supported Claude Code version.

### 1E. Skills and docs

- [ ] **1.22 Skill `openjev-decisions`** limited to the phase-1 rows of its tool table, with the
  advisory-until-calibrated precedence rule (spec 4.1).
  Done when: every tool it names exists in phase 1. The `batch` and `batch_results` rows of the
  tool table and the three `batch` failure bullets are left out until task 2.15.
- [ ] **1.23 Skill `openjev-question-authoring`** without `compile` (spec 4.2).
- [ ] **1.24 Docs**: README section for the MCP package covering registration (`.mcp.json`),
  the env vars, the privacy note about debug-level servers and hosted base URLs (spec 2.1
  principle 7), and the hook setup.
- [ ] **1.25 CI job** running `mcp/tests` on every change to `mcp/` or `docs/mcp-skill-spec/`.

### 1F. Protocol conformance (spec 2.0.1)

- [ ] **1.26 `server/discover` and legacy `initialize`**: `supportedVersions`, capabilities,
  `serverInfo`, `instructions`; -32022 for an unsupported version.
  Done when: the discover and version tests of 6.6 pass (protocol conformance row of 6.7).
- [ ] **1.27 Per-request `_meta`**: `protocolVersion` and `clientCapabilities` required on
  2026-07-28 requests (-32602 when missing); `resultType: "complete"` and `serverInfo` `_meta` on
  every result. Done when: the 6.6 `_meta` tests pass.
- [ ] **1.28 Caching fields**: `ttlMs` and `cacheScope` on list, discover and `resources/read`
  results with the 2.0.1 values; single-page lists. Done when: the 6.6 caching tests pass.
- [ ] **1.29 Argument validation path**: SDK validation never answers -32602 for tool arguments;
  `$schema` 2020-12 on every schema and no `$ref` after inlining. Done when: the schema walk and
  the invalid-argument tests of 6.6 pass.
- [ ] **1.30 Cancellation and progress plumbing**: a cancellation registry keyed by request id
  that cancels tasks and httpx requests and suppresses further messages; a progress emitter
  (strictly increasing, at most one per second, only with a `progressToken`). Done when: the
  cancellation and progress tests of 6.6 pass on `ask`; phase 2 reuses them for `batch`.
- [ ] **1.31 stdio hygiene**: diagnostics on stderr only, no `notifications/message`, no
  server-initiated requests; allowed roots from cwd + `OPENJEV_MCP_ROOTS`.
  Done when: a test asserts stdout carries JSON-RPC only.
- [ ] **1.32 Lint and mapping corrections of 1.2**: E017 split, E027, E028, W403 narrowed,
  `chunks_estimate` as an estimate, the new rows of 2.4, timeout scaling, `Meta.body_hashes` and
  `server_timing`, `lint` `emit`, `status` `capabilities` and `mcp`. Phase-1 tool schemas gain
  optional fields only; the section 2 replays are unchanged. Done when: the "Errors added in 1.2",
  "Lint 1.2" and "Mapping 1.2" tests of 6.6 pass (6.7 `lint`, `status`, `ask` rows).

Phase 1 exit: 1.1-1.32 done, CI green (protocol tests green for both eras), one live run of `00-spec-examples.json` and
`03-agent-tool-call-gate.json` recorded.

## Phase 2 (planned)

- [ ] **2.1 `filter`** (spec 2.10), with injection-safe packing. Done when: the 6.7 row passes.
- [ ] **2.2 `recipe` tool** (spec 2.16), with `dry_run`, fail modes and `degraded`.
  Done when: the 6.7 row passes.
- [ ] **2.3 Recipes** `act_or_ask`, `injection_screen`, `done_gate`, `moderation`,
  `model_routing`, `skill_selection`, `typed_call`, each with its section 5 questions verbatim.
  Done when: each recipe's `test_file` passes live.
- [ ] **2.4 Hooks** `stop`, `userprompt`, `posttooluse`, and the CLIs `openjev check` and
  `openjev filter` (spec 2.20). Done when: fail modes are tested; exit codes as specified.
- [ ] **2.5 Resources** `openjev://recipes`, `openjev://recipes/{id}`, `openjev://patterns`,
  `openjev://guide/authoring`.
- [ ] **2.6 Extra recipe directory** (`OPENJEV_MCP_RECIPES`), loaded under the same parser and
  RE2 rules; a recipe that fails to load is skipped with a warning.
- [ ] **2.7 Skills**: agent-gates, code-checks, dispatch, triage-routing, retrieval-relevance,
  multistep.
- [ ] **2.8 Batch importers** (spec 2.11, "Import rules"): sniffing, formats, delimiter, text-column
  guess, `array_key`, encoding, rejections, merge, `ojui-batch`, `template` source.
  Done when: the "Batch import" tests of 6.6 pass.
- [ ] **2.9 Batch runner**: worker pool under `OPENJEV_MCP_MAX_INFLIGHT_BATCH`, reorder buffer,
  shared cooldown, `stopped_reason: backpressure`, `sampling` modes and the regrey read, `flock`,
  whole-line writes, cancellation. Done when: the "Batch runner" and "Cancellation" tests of 6.6
  pass.
- [ ] **2.10 Cursor codec and resume scan**: base64url token, `args_hash` with its exclusion list,
  header `run_id` check, last-row-wins, `retry_errors`, `only_ids`. Done when: the cursor and
  resume tests inside "Batch runner" of 6.6 pass.
- [ ] **2.11 `batch` tool** (spec 2.11): schema, `dry_run` with estimate, status block,
  `per_question` statistics, review queue, audit sample, `resource_link` blocks, and the batch
  caps in `openjev://limits`. Done when: the 6.7 `batch` row passes, including the two recorded
  live runs (200-row CSV with `concurrency` 2; interrupted then resumed). The `images` option is
  not part of this task (phase 3).
- [ ] **2.12 Exporters**: CSV, Markdown and `ojui-batch` in the Playground layouts. Done when: the
  export tests pass and an `ojui-batch` export opens in the Playground (checked by a human, not
  in CI).
- [ ] **2.13 `batch_results` tool** (spec 2.21): views, filter, sort, cursor, export, compare with
  Jensen-Shannon divergence. Done when: the 6.7 `batch_results` row passes.
- [ ] **2.14 Templates, prompts, completion**: `openjev://templates` and `openjev://templates/{id}`
  generated at build time from verified section 5 cases; prompts `start_batch` and
  `review_batch`; `completion/complete` for template and recipe ids; `resources/templates/list`.
  Done when: the 6.7 resources, prompts, completion row passes (each template replays its source
  case).
- [ ] **2.15 Skills for batch** (spec 4.1, 4.3, 4.8): add the `batch` and `batch_results` rows and
  the failure bullets to `openjev-decisions`, the backlog paragraph to `openjev-triage-routing`,
  and ship `openjev-data-records` limited to its "Batch jobs" part. Done when: a scripted
  walk-through of the nine steps of 4.8 against the mock server (dry run, run, follow cursor,
  interrupt, resume, review, export, compare) succeeds using only the arguments the skill shows.
- [ ] **2.16 README**: a batch section (the cursor loop, `resume`, privacy of output files,
  `concurrency` on MLX versus vLLM). Done when: the README commands run against the mock server.

## Phase 3 (deferred, not scheduled)

- `batch.images`: shared images, same loader as `ask_image` (spec 2.11, 2.12).
- `calibrate` on the batch runner, with `from_batch`, reliability bins, Brier, ECE and the
  histograms, cursor, audit store and drift (spec 2.15). Done when the 6.7 `calibrate` row passes.
- `ask_image` with path rules, opt-in URL fetch under the SSRF rules, and re-encoding with
  Pillow (spec 2.12, 2.2).
- `compile` (spec 2.14), including variant handling (`recipe.variants`).
- `generate` (spec 2.17).
- The remaining recipes and variants; prompts `author_question`, `audit_question`,
  `explain_answer`; `openjev://audits/{question_hash}` with `OPENJEV_MCP_AUDIT_DIR`.
- The optional MCP Tasks extension behind `OPENJEV_MCP_TASKS` (spec 2.0.1 rule 9). Deferred on
  purpose: experimental, and the cursor path already works on every client.
- Skills ui-vision, calibration, and the recipe parts of data-records.
- Not planned at all: batch pause (stopping calls is the pause), per-row images in `batch`,
  user templates from browser storage, elicitation for review triage (spec 2.19).

## Open items outside the phases

- The `tests/spec_build/p*.md` sources are a newer revision than the rendered spec. They
  reference cases that `00-spec-examples.json` and `captured.json` don't have yet
  (`ex-filter-pick-best`, `ex-author-fixed`, ...), so `render.py` fails today. The 1.1 changes
  were applied to both, but the sources' own newer content has not been rendered or reviewed.
  Finish that revision (author the missing cases, capture them live, render), then review the
  diff against the rendered 1.1.
- Hand-edited or corrupt JSONL output: the spec does not say what `batch` and `batch_results` do
  with a line that is not valid JSON (skip with a warning, or refuse and name the line). Decide
  when task 2.9 is written and add a 6.6 test; until then the behaviour is undefined (spec 7 #23).
- The cursor loop and `resume` are specified, but only the mock server exercises them until the
  two live runs of task 2.11 are recorded; do not claim the live behaviour before that.
- Shared-load latency of `batch` (5-20x idle) is an estimate (spec 7 #26); the first live runs
  should record the measured `req_per_s` per backend to replace it.
