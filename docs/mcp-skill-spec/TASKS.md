# OpenJev MCP server: tasks

Task breakdown for `OPENJEV_MCP_SKILLS_SPEC.md` version 1.1. Phases and scope are defined in
spec 2.0; "Done when" points at the acceptance criteria (spec 6.7) and the contract tests (spec
6.6). Only phase 1 is scheduled. Phase 2 is planned. Phase 3 is deferred and gets re-planned
after phase 2 ships.

Conventions: every task lands with its tests in the same change. "CI" means the contract tests
of 6.6 (no model). "Live" means a run against a real OpenJev server with
`docs/mcp-skill-spec/tests/run_cases.py` or the hook, done by a human and not in CI.

## Phase 0: server prerequisites (in `openjev/`, optional for phase 1)

- [ ] **0.1 `GET /v1/limits`** as proposed in `00-api-surface.md` section 15: `backend`,
  `logs_bodies`, request limits, per-model caps, routed models without URLs or secrets.
  Done when: one test per backend in `tests/test_api.py` (stub engine); auth applies as on the
  other `/v1/` routes. Phase 1 works without it (limits come from defaults and are marked
  `limit_source: "default"`).
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
  `openjev_mcp`, Python >= 3.10, dependencies `mcp>=1.10`, `httpx>=0.27`, `jsonschema>=4.18`,
  `google-re2>=1.1`, and the scripts `openjev-mcp`, `openjev-hook`, `openjev` (spec 2.0).
  Done when: `pip install -e mcp/` in a clean venv doesn't pull in `transformers`;
  `openjev-mcp --help` and `openjev-hook --help` run.
- [ ] **1.2 Config** from the environment, with the `OPENJEV_MCP_*` names of spec 2.1. Only
  `OPENJEV_BASE_URL` and `OPENJEV_API_KEY` are shared with the server.
  Done when: a unit test shows `OPENJEV_MODEL` is ignored and `OPENJEV_MCP_MODEL` honoured.
- [ ] **1.3 stdio server** on the MCP Python SDK (protocol 2025-06-18). `tools/list` returns
  only the phase-1 tools, with annotations and titles (spec 2.5).
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
  Done when: every tool it names exists in phase 1.
- [ ] **1.23 Skill `openjev-question-authoring`** without `compile` (spec 4.2).
- [ ] **1.24 Docs**: README section for the MCP package covering registration (`.mcp.json`),
  the env vars, the privacy note about debug-level servers and hosted base URLs (spec 2.1
  principle 7), and the hook setup.
- [ ] **1.25 CI job** running `mcp/tests` on every change to `mcp/` or `docs/mcp-skill-spec/`.

Phase 1 exit: 1.1-1.25 done, CI green, one live run of `00-spec-examples.json` and
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

## Phase 3 (deferred, not scheduled)

- `batch` with cursor chunking and file rules (spec 2.11, 2.2).
- `calibrate` with cursor chunking, audit store and drift (spec 2.15).
- `ask_image` with path rules, opt-in URL fetch under the SSRF rules, and re-encoding with
  Pillow (spec 2.12, 2.2).
- `compile` (spec 2.14), including variant handling (`recipe.variants`).
- `generate` (spec 2.17).
- The remaining recipes and variants; prompts; `openjev://audits`.
- Skills data-records, ui-vision, calibration.

## Open items outside the phases

- The `tests/spec_build/p*.md` sources are a newer revision than the rendered spec. They
  reference cases that `00-spec-examples.json` and `captured.json` don't have yet
  (`ex-filter-pick-best`, `ex-author-fixed`, ...), so `render.py` fails today. The 1.1 changes
  were applied to both, but the sources' own newer content has not been rendered or reviewed.
  Finish that revision (author the missing cases, capture them live, render), then review the
  diff against the rendered 1.1.
