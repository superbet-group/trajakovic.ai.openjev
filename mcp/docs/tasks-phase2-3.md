# openjev-mcp: phase 2 and phase 3 work packages

Status: plan, 2026-10-02. Covers (A) the live-baseline failures, (B) the protocol gaps against MCP 2026-07-28,
(C) all of `docs/mcp-skill-spec/TASKS.md` phase 2 (2.1-2.16) and (D) all of phase 3. Repo root:
`/Users/trajakovic/Projects/Models/openjev` (paths below are relative to it). Citations: "spec X.y Lm-n" =
`docs/mcp-skill-spec/OPENJEV_MCP_SKILLS_SPEC.md` lines m-n (410 KB: read only the cited ranges); "mcp spec" =
`mcp/spec.md` (the built server's own spec, phase 1 as built); "arch" = `mcp/docs/architecture.md`; phase-1 plan
= `mcp/docs/tasks.md`.

## Inputs this plan is built on

- Live baseline (2026-10-02, real MLX model on :8080). `run_cases.py`: 76/77 (ex-generate flaky once: empty
  completion, passed 3 re-runs). MCP on a spare port: 11/11 calls returned, 10 matched; `ex-classify-abstain`
  mismatch is the script's check (tool returns `label: null, abstained: true, top: "other"`, as documented).
  Hook over 03 gate cases: 11/12; **gate-10 is a real finding**: rustup `curl ... | sh` with task "install Rust"
  -> `deny` ("remote_code=0.9999 >= 0.85"), case expects verdict in [ask, allow]. Not blocking.
  Latency: MCP calls mean 148-219 ms; yes_no 7.1 req/s sequential, 9.3 req/s at concurrency 4; hook 0.5-0.8 s
  model-decided, 70-80 ms rule-decided; think/sequential reads 3.5-5.9 s.
- Protocol research: 2026-07-28 is the latest stable revision (no newer revision, draft changelog empty); SDK
  `mcp` 2.2.0 is the newest release and is installed; the pin `mcp>=2.2,<3` stays. The built server already
  covers every MUST of 2026-07-28. Open, optional items: Tasks extension (`io.modelcontextprotocol/tasks`,
  defined by the official extensions docs, SDK ships only the `Extension` hook in `mcp/server/extension.py`),
  `capabilities.extensions`, OTel `_meta` keys, per-request `logLevel` (we never emit, compliant),
  `subscriptions/listen` (not needed: static lists), RFC 9728 PRM (not needed on loopback). Details in the
  coverage table at the end.
- `docs/mcp-skill-spec/tests/spec_build/captured.json` holds captured responses for every `ex-*` case of
  `00-spec-examples.json` (incl. `ex-filter`, `ex-batch-1..3`, `ex-cal-1..7`, `ex-compile-*`, `ex-image`,
  `ex-generate`, `ex-gate-*`) and for the section 5 usage examples `u01`-`u23`, so every "replay" acceptance
  below is CI-runnable with `tests/stubs.py::replay_transport`.

## Rules for every package

- Read first: this file's "Rules" and "Decisions", then `mcp/spec.md` (sections named in the brief), then the
  cited spec line ranges. Do not re-read big files; do not over-explore.
- Own only the files listed for the package. A defect in a file you do not own is reported (file, failing call,
  observed vs expected, the change needed), never fixed in place. **Ownership is per wave**: a shared wiring file
  may be owned by a later package after the earlier owner is done (hand-off table below); inside one wave no two
  packages own the same file. Feature packages put new code and tests in **new** files and expose a
  `register()`/content function; only the integration packages (P14, P19) wire them into the shared registries.
- Never create `mcp/__init__.py` or `mcp/tests/__init__.py`. `from __future__ import annotations`, Python 3.10
  syntax, anyio in async code, terse names and comments in the repo's style. Only `server.py` and `http_app.py`
  import the `mcp` SDK, except `openjev_mcp/tasks.py` (P12, it subclasses `mcp.server.extension.Extension`).
  `openjev_mcp` never imports `openjev`. `openjev_mcp.hook` stays light (import-isolation test).
- Every feature lands with tests in the same package. Tests run from the repo root:
  `.venv/bin/python -m pytest -q mcp/tests/<file>`; before finishing run the whole suite
  `.venv/bin/python -m pytest -q mcp/tests` (must stay green, except tests a later integration package is
  documented to flip). CI tests never need the model: `tests/stubs.py` (stub engine in `openjev.api.create_app`,
  `fault_transport`, `fail_on_request_transport`, `replay_transport` over captured.json).
- Live checks (real model) live in `mcp/tests/live/`, are marked `@pytest.mark.live` and are skipped unless
  `--live` is passed or `OPENJEV_LIVE=1` (P01 adds the marker and the option). Services already run: OpenJev
  :8080 (real MLX model, one GPU: **model calls sequential, concurrency <= 2**), UI :8090, MCP :8100 (old code,
  editable install; only the user restarts it). Never run `mise run start/stop/restart/default/install`. A
  process you start binds a spare port 8190-8199 and is killed in a `finally`.
- No commits, no `git checkout/stash/reset`. `docs/mcp-skill-spec/` is read-only.
- Final report of every package: files changed, tests added and the suite result, live results if any, and a
  **Deviations** list (build-spec rule, what was built, why). Also mark each deviation in code with a grep-able
  `# deviation: <spec ref>: <what and why>` comment (module docstring for recipe-engine keys), so P22 can rebuild
  `mcp/spec.md` section 3 from `grep -rn 'deviation:' mcp/openjev_mcp` if a report is missing.
- Tool registration contract (P02 builds it): every tool module exposes `register(config: Config) -> ToolSpec`
  and calls `schemas.register_tool_schemas` inside it; `dispatch.specs(config)` builds the tuple once per process
  (cached, so lists are identical for every connection); `tools_for(config)` and `call_tool(ctx, ...)` use
  `specs(ctx.config)`. Completers are `Callable[[str, ToolContext], list[str]]`.

## Decisions (binding for every package)

1. **Tool order** (spec 2.5 L829-858, 2.0.1 rule 5): `tools/list` follows the 2.5 table order, which is
   deterministic. Phase 2 (`TOOL_NAMES` after P14): `ask, yes_no, classify, score, filter, batch, lint, recipe,
   status, batch_results`. Phase 3 (after P19): `ask, yes_no, classify, score, filter, batch, ask_image, lint,
   compile, calibrate, recipe, status, generate, batch_results`. `CORE_TOOL_NAMES = ("ask", "yes_no", "classify",
   "score", "lint", "status")`; `tools_for("core")` returns them in the same relative order (mcp spec deviation 16
   goes away). `generate` is in `all` only.
2. **Allowed roots and paths** (spec 2.2 L700-739; mcp spec 10 "Allowed roots"): roots = `Config.roots`
   (`realpath(cwd)` + `OPENJEV_MCP_ROOTS`) only; no `roots/list` on any era. Absolute paths are always accepted.
   Relative paths resolve against `roots[0]` only under stdio; under the HTTP transport a relative path is
   `OJ_INVALID_INPUT` "relative path: pass an absolute path inside the allowed roots (the HTTP daemon's cwd is
   not your project)". realpath, then prefix check per root. Read and write extension lists, E030 refusals, no
   writes through a symlink, under a dot-directory or to a dotfile, 64 MiB read cap, 20 MiB image cap, BOM ->
   UTF-16, strict UTF-8, else cp1252 + W603. One module: `openjev_mcp/paths.py` (P01).
3. **Corrupt JSONL lines** (open item, spec 7 #23): a batch output line that is not valid JSON is refused
   (`OJ_INVALID_INPUT` "output_path line N is not valid JSON; fix or truncate the file"), except a final line
   without a trailing newline (an interrupted write), which is ignored with a warning and truncated away before
   the next append. Same rule in `batch`, `batch_results`, `calibrate.from_batch`, `resources/read file://`.
4. **gate-10** is a build-spec inconsistency, not a code bug: the built `combine` matches spec 2.18 L2843
   verbatim, but spec 5.3 L4362 records "user-requested rustup `curl | sh` remote_code 0.99 with verdict ask" and
   the case expects ask/allow. P07 changes the `combine` within the constraints in its brief; P22 records it as a
   deviation.
5. **Tasks extension** is built (the stable spec defines it as an official extension; the user asked for the
   latest protocol). `OPENJEV_MCP_TASKS=off` by default; 2026-07-28 era only; only `batch` and `calibrate`; only
   when the request's `clientCapabilities.extensions` lists `io.modelcontextprotocol/tasks`. mcp spec section 10
   said "wait for the SDK"; P22 records the change. The cursor path stays the normative contract.
6. **Capabilities**: `tools`, `resources` as now; `prompts {listChanged: false}` and `completions {}` from P02 on
   (prompts list is empty until P14); `extensions` only when a registered extension is active (2.0.1 rule 4).
7. **Versions**: P14 sets package 1.3.0 (phase 2), P19 sets 1.4.0 (phase 3); `SPEC_VERSION` stays "1.2". Phase-1
   tool schemas only gain optional fields.
8. **Recipe format**: the built format already extends spec 2.18 (`question_profiles`, `signal_map`,
   `policy_profiles`, `fallback`). New engine keys added by P03 are documented in `recipes/engine.py`'s module
   docstring and reported as deviations; recipe files stay data (no code).

## Shared files and hand-offs

| File | W1 | W2 | W3 | W4 | W5 | W6 |
|---|---|---|---|---|---|---|
| `openjev_mcp/__init__.py` (`TOOL_NAMES`, `__version__`) | | | | P14 | P19 | |
| `openjev_mcp/tools/dispatch.py` | P02 | | | P14 | P19 | |
| `openjev_mcp/resources.py` | P02 | | | P14 | P19 | |
| `openjev_mcp/prompts.py`, `completion.py` | P02 (create) | | | P14 | P19 | |
| `openjev_mcp/ext.py` (extension registry) | P02 (create) | | | | P19 | |
| `openjev_mcp/server.py`, `http_app.py`, `audit.py`, `envelope.py`, `tools/__init__.py` | P02 | | | | | |
| `openjev_mcp/config.py`, `paths.py`, `schemas.py`, `validate.py` | P01 | | | | | |
| `mcp/pyproject.toml` | P01 | | | P14 | P19 | |
| `openjev_mcp/http.py` | | P05 | | P16 | | |
| `openjev_mcp/lint.py` | | P04 | | | | |
| `openjev_mcp/recipes/engine.py`, `expr.py`, `template.py` | | P03 | | | | |
| `openjev_mcp/recipes/builtin/command_gate.json` | | | P07 | | | |
| `openjev_mcp/tools/status.py` | | | P06 | | | |
| `openjev_mcp/tools/batch_tool.py` | | | P08 | P15 | | |
| `mcp/README.md` | P01 (env rows) | | | | | P21 |
| `mcp/tests/stubs.py`, `conftest.py` | P01 | | | | | |
| `mcp/tests/test_mcp_protocol.py`, `test_mcp_server.py` | P02 | | | P14 | P19 | |
| `test_mcp_dispatch.py`, `test_mcp_resources.py`, `test_mcp_e2e.py`, `test_mcp_docs_consistency.py`, `test_mcp_skills.py` | | | | P14 | P19 | P21 |
| `mcp/tests/live/run_live.py` | | | | | | P20 |
| `mcp/skills/**`, root `README.md` | | | | | | P21 |
| `mcp/spec.md` | | | | | | P22 (W7) |

## Waves

| Wave | Packages | Depends on |
|---|---|---|
| 1 | P01 foundation; P02 protocol surface | - |
| 2 | P03 recipe engine; P04 batch formats; P05 batch runner; P10 library data | P01 (+P02 for P05) |
| 3 | P06 filter + recipe tool; P07 gate fix + phase-2 recipes; P08 batch tool; P09 batch_results + batch prompts; P11 calibrate; P12 Tasks extension | see briefs |
| 4 | P13 hooks + CLIs; P14 phase-2 integration; P15 images; P16 compile + generate; P17 recipes 3A; P18 recipes 3B | see briefs |
| 5 | P19 phase-3 integration | P14-P18, P11, P12 |
| 6 | P20 live verification; P21 skills and READMEs | P19, P13 |
| 7 | P22 `mcp/spec.md` 1.4 | P20, P21 |

Critical path: P01 -> P05 -> P11 -> P16 -> P19 -> P20 -> P22.

## Package briefs

### P01 Foundation: config, paths, schema registry, stubs, live marker (wave 1)

TASKS: prerequisites of 2.6, 2.8-2.13, phase 3 (env), 7 #23 policy (in P05). Spec: 2.1 env table L432-462;
2.2 file and URL access L700-739; 6.6 "Paths and URLs" L5501-5504; mcp spec section 4 (config), section 10
"Allowed roots".

Files: `openjev_mcp/config.py`, new `openjev_mcp/paths.py`, new `openjev_mcp/batch/__init__.py` (empty),
`openjev_mcp/schemas.py`, `openjev_mcp/validate.py`,
`mcp/pyproject.toml`, `mcp/README.md` (env table rows only), `mcp/tests/conftest.py`, `mcp/tests/stubs.py`,
`tests/test_mcp_config.py`, new `tests/test_mcp_paths.py`, `tests/test_mcp_schemas.py`, `tests/test_mcp_stubs.py`.

Work:
- `config.py`: parse `OPENJEV_MCP_RECIPES` (dir, optional, realpath), `OPENJEV_MCP_ROUTING` (on/off, default
  on), `OPENJEV_MCP_FETCH` (default off), `OPENJEV_MCP_TASKS` (default off), `OPENJEV_MCP_AUDIT_DIR` (default
  `./openjev-audits`, resolved against roots[0]), `OPENJEV_MCP_CHAT_MODEL` (default `diffusiongemma-26b`). New
  `Config` fields `recipes_dir`, `routing`, `fetch`, `tasks`, `audit_dir`, `chat_model`. `_CACHE_TTL_S` stays
  unread (principle 10; listed as not implemented). Add every new name to the README env table in the same change
  (`test_mcp_docs_consistency::test_env_table_matches_config` must pass).
- `paths.py` (Decision 2): `class PathError(ToolError)` or raise `ToolError("OJ_INVALID_INPUT", ..., path=...)`;
  `resolve_read(path, config, *, kinds=("data",)|("image",)) -> Path` (E030 for spreadsheet/binary with the two
  spec hints, extension list per kind, 64 MiB / 20 MiB caps), `resolve_write(path, config, *, exts, new_only:
  bool) -> Path` (no symlink anywhere in the final component, no dot-dir component under the root, no dotfile,
  parent must exist inside a root, `new_only` refuses an existing file), `read_text(path) -> tuple[str, str,
  list[str]]` (text, encoding, warnings incl. W603), `within_roots(path, config) -> bool`. Relative paths per
  Decision 2 (`config.transport`).
- `schemas.py`/`validate.py`: `register_tool_schemas(name, input_schema, output_schema, extra_defs=None)` that
  inlines (`inline(schema, {**DEFS, **extra_defs})`), sets `$schema` 2020-12, asserts an object root without root
  `oneOf/anyOf/allOf` and no `$ref` left, and stores into `INPUT_SCHEMAS`/`OUTPUT_SCHEMAS`; `validate_args` and
  `validate_output` keep their signatures. Feature packages call it from their `register()`.
- `pyproject.toml`: pytest `markers = ["live: needs a real OpenJev with the model (skipped unless --live)"]`;
  package-data adds `data/*.json`, `data/*.md` (P10) next to `recipes/builtin/*.json`.
- `conftest.py`: `--live` option and `OPENJEV_LIVE=1`; auto-skip `live`-marked tests otherwise.
- `stubs.py`: fix the known gap (mcp spec 3 "Known gaps": score answers as dicts `{score, legend: {"0": label},
  probabilities: {"0": p}, confidence}`); add `slow_transport(delay_s, inner)` (for batch cancellation) and
  `overload_transport(pattern)` (yields 529 with `retry-after` for chosen calls, then delegates) and a helper
  `captured(case_id) -> dict` returning the captured response of an `ex-*`/`u*` case.

Acceptance: `test_mcp_paths.py` covers `..` escape, symlink out of a root, write into `.git`/`.ssh`, dotfile,
wrong extension, existing export path, relative path under http vs stdio, E030 hints, 64 MiB cap (sparse file),
BOM/UTF-8/cp1252 decoding; config tests for every new variable (defaults, bad values -> `ConfigError`); schema
registry rejects a root `oneOf` and a leftover `$ref`; stub score-shape test; full suite green.
Run: `.venv/bin/python -m pytest -q mcp/tests`.

### P02 Protocol surface: links, request meta, prompts/completion/extension plumbing, 2026-07-28 gaps (wave 1)

TASKS: 2.14 (protocol half), 2.11 `resource_link` blocks, research gaps (extensions, OTel, logLevel,
subscriptions). Spec: 2.0.1 rules 4-10 L305-335 and the compliance table L337-372; 2.18 resources/prompts tables
L2867-2905; 6.6 "Protocol conformance" L5514-5525 and "Resources, prompts, completion" L5564-5569; mcp spec
section 2 (mismatches 1-8, "Not implemented"), section 5, section 6; arch D.19 L956-998. SDK:
`.venv/lib/python3.12/site-packages/mcp/server/{lowlevel/server.py,extension.py,context.py,caching.py}`.

Files: `openjev_mcp/server.py`, `openjev_mcp/http_app.py`, `openjev_mcp/audit.py`, `openjev_mcp/envelope.py`,
`openjev_mcp/tools/__init__.py`, `openjev_mcp/tools/dispatch.py`, `openjev_mcp/resources.py`, new
`openjev_mcp/prompts.py`, new `openjev_mcp/completion.py`, new `openjev_mcp/ext.py`,
`tests/test_mcp_protocol.py`, `tests/test_mcp_server.py`, `tests/test_mcp_audit.py`, `tests/test_mcp_envelope.py`,
new `tests/test_mcp_protocol_gaps.py`.

Work (public contracts other packages code against; keep these names):
- `tools/__init__.py`: `ToolContext` gains `links: list[dict] = field(default_factory=list)` and
  `request_meta: dict = field(default_factory=dict)` (the request `_meta` minus protocol keys; carries
  `traceparent`, `tracestate`, `baggage` when sent). Handlers append resource links with
  `envelope.file_link(path, mime) -> dict`.
- `envelope.py`: `success_result(structured, links=())` -> content = text block (audience assistant), then one
  `{"type": "resource_link", "uri": "file://<realpath>", "name", "mimeType", "annotations": {"audience":
  ["user", "assistant"]}}` per link (2.0.1 rule 6). `dispatch.call_tool` passes `ctx.links` (reset per call).
- `audit.py`: record `traceparent` (and `tracestate`) from `ctx.request_meta` when present (SEP-414, accepted and
  logged only, never forwarded to OpenJev).
- `prompts.py`: `@dataclass(frozen=True) class PromptArg(name, description, required: bool, complete: bool)`,
  `class PromptSpec(name, title, description, arguments: tuple[PromptArg, ...], build: Callable[[dict,
  ToolContext], Awaitable[list[dict]]])` (returns MCP `PromptMessage` dicts, user role only),
  `PROMPTS: list[PromptSpec] = []`, `listing()`, `async get(name, args, ctx)` raising `PromptError` (unknown
  prompt, missing required or unknown argument) -> JSON-RPC -32602 in `server.py`.
- `completion.py`: `COMPLETERS: dict[tuple[str, str, str], Callable[[str, ToolContext], list[str]]] = {}` keyed
  `(ref_type, ref_name_or_uri_template, argument)`; `complete(ref_type, ref, argument, value, ctx) -> dict`
  (`values` = prefix matches, at most 100, `total`, `hasMore`).
- `dispatch.py`: the registration contract of the Rules section: phase-1 tools become `register(config)`-style
  entries, `PHASE_TOOLS: list[Callable[[Config], ToolSpec]]` (P14/P19 append), `specs(config)` cached per
  process, `tools_for(config)` (honours `config.toolsets`), `call_tool` looks up `specs(ctx.config)`; pass
  `ctx.links` to `success_result`. `tools/list` output unchanged.
- `ext.py`: `EXTENSIONS: list[Callable[[Config], Extension | None]] = []` (factories; `None` = inactive).
- `resources.py`: generic registry so P14/P19 only add entries: `RESOURCES` (static list, 2.18 order),
  `TEMPLATES: list[dict] = []` (`uriTemplate`, name, title, mimeType, annotations), `READERS: list[tuple[pattern,
  async reader(uri, ctx) -> dict]]` consulted after the static URIs; `templates_listing()`; unknown URI stays
  `ResourceNotFound` -> -32602 with `data.uri`.
- `server.py`: `build_server(config, *, client=None, transport=None, warm_limits=True, extensions=None)`;
  `extensions=None` means `[f(config) for f in ext.EXTENSIONS]` minus `None`. Apply each active extension:
  advertise `server.extensions[identifier] = ext.settings()`, register `ext.methods()` request handlers, fold
  `intercept_tool_call` around `tools/call` (`mcp.server.extension.compose_tool_call_handler`). Fill
  `ToolContext.request_meta` from `ctx.meta`. Handlers for `prompts/list`, `prompts/get`, `completion/complete`,
  `resources/templates/list` (delegating to the registries); capabilities `prompts {listChanged: false}`,
  `completions {}`; cache hint 3600000/public on `prompts/list`. Keep instructions (three sentences, rule 1).
- Tests (`test_mcp_protocol_gaps.py`, both eras where meaningful): capabilities now `{tools, resources, prompts,
  completions}` and no `extensions` key by default; an injected dummy `Extension` (test-local) is advertised under
  `capabilities.extensions`, its method answers, its interceptor wraps `tools/call`; `prompts/list` empty with
  cache fields; a test prompt/completer registered in-test round-trips; `prompts/get` unknown/missing argument ->
  -32602; `_meta` `io.modelcontextprotocol/logLevel: "debug"` on a call -> still no `notifications/message`;
  `subscriptions/listen` -> JSON-RPC error (method not found; record the code), documented as not implemented;
  `traceparent` lands in the audit record; `resource_link` block shape and order; unknown resource -32602.
  Update `test_mcp_server.py`/`test_mcp_protocol.py` assertions that pinned capabilities to `{tools, resources}`.
- Re-check the 401 path in `http_app.py` keeps `WWW-Authenticate: Bearer realm="openjev-mcp"` (RFC 6750); do not
  add RFC 9728 PRM (loopback daemon; documented by P22).

Acceptance: whole suite green; the new tests above; `tools/list` output unchanged. Report the exact SDK calls
used for extensions so P12 and P19 can rely on them.

### P03 Recipe engine generalisation (wave 2; depends P01)

TASKS: 2.2 (engine part), 2.3 and phase-3 recipes (engine features they need). Spec: 2.18 recipe schema and
grammar L2709-2789, templates L2785-2789; 2.16 L2459-2504; policies of section 5: 5.2 L4272-4345, 5.4
L4369-4416, 5.5 L4416-4462, 5.8 L4576-4623, 5.9 L4623-4666, 5.10 L4666-4748, 5.12 L4790-4831, 5.14 L4878-4920,
5.15 L4920-4962, 5.17 L5003-5050, 5.18 L5050-5098, 5.19 L5098-5147, 5.22 L5240-5305, 5.23 L5305-5347 (read the
"Policy:" paragraph and the verified request of each, not the whole section). Code: `recipes/engine.py`,
`expr.py`, `template.py`, `rules.py`; arch D.21-D.25 L1011-1170.

Files: `openjev_mcp/recipes/engine.py`, `openjev_mcp/recipes/expr.py`, `openjev_mcp/recipes/template.py`, new
`tests/test_mcp_engine_p2.py`. Do not touch `command_gate.json` or the hook.

Work: keep `load_recipe`, `load_builtin`, `run_recipe(...) -> RecipeOutcome` and every phase-1 behaviour
(`test_mcp_command_gate.py`, `test_mcp_hook.py` unchanged and green). Add, each with load-time validation and a
docstring entry (Decision 8):
- per-step `questions` (override of `question_profiles`) and signals that accumulate across read steps, so a
  later `when` (e.g. `grey(block)`) sees earlier signals; per-step `options` (re-read with `samples: 3/4`), and a
  `reread` step form: same questions with other options, replacing the grey signal.
- `read_per_item`: one read per element of an input array (`items: "<input name>"`), state rendered per item,
  per-item decision by `combine`; outcome adds `items: [{id, decision, signals}]` and an aggregate decision
  (`aggregate: worst|any:<decision>|none`).
- `read_twice_swapped`: read with candidates A/B, then swapped; `combine` sees `<sig>` and `<sig>_swapped`
  (judge_pairwise: agree -> A/B, disagree -> tie).
- choice criteria from inputs: `"criteria": {"$from": "<input array>", "key": "<field>", "text": "<field>",
  "extra": {"none": "..."}}` expanded before the read (skill_selection roster, typed_call functions, ui elements,
  multistep candidates, taxonomy children); keys validated against the choice key rules.
- decision formatting: `decision_format: {"inject": "inject:{skill}"}` applied after `combine` (signal value
  substitution, no grammar change), so `inject:<id>`, `click:<id>`, `move:<id>` work; secondary outputs
  `outputs: {"effort": "<combine>"}` (model_routing tier + effort) returned in `signals`/`outputs`.
- `compute` kinds beyond `split`: `weighted_sum` (rubric_score: weights from inputs, applied in code, floors) and
  `threshold` helpers if a section 5 policy needs them; nothing that evaluates text as code.
- images input: an input marked `"x-openjev-images": true` (array of data URLs) is sent as `images`; `think`
  and `sequential` refused with them (E022).
- routing recipes: `"routing": true` recipes return `fallback.interactive` without a read when
  `config.routing` is false (`OPENJEV_MCP_ROUTING=off`), `degraded: false`, reason "routing off".
- `build_requests(recipe, inputs, *, profile=None, policy=None) -> list[dict]` (the exact bodies a run would send
  before any conditional step; no I/O) for `recipe` `dry_run`.
- `run_recipe` gains `policy_overrides`, `fail_mode` override and returns `degraded: true` with the fail-mode
  decision on any error (existing behaviour, now also for the new kinds).

Acceptance: `test_mcp_engine_p2.py` with synthetic recipe documents (no builtin recipes needed) covering every
feature, its load-time errors, totality of `when` (missing signal -> false), "a rule's deny is final",
`build_requests` makes no request (`fail_on_request_transport`), images refusal, routing off; existing recipe and
hook tests green; `import openjev_mcp.hook` isolation test still green.

### P04 Batch formats: importers, exporters, lint codes (wave 2; depends P01)

TASKS: 2.8, 2.12. Spec: 2.11 input schema L1480-1531, import rules L1532-1570, exports L1717-1734, limits
L1735-1746; 2.13 codes table (E030-E032, W601-W605) around L1995-2010; 2.2 L700-739; 6.6 "Batch import"
L5532-5540 and the export sentences in "Batch runner" L5555-5557. Playground sources (behaviour reference):
`ui/static/js/jev/batchImport.js`, `ui/static/js/jev/batch.js`, `ui/tests/batch_import.test.mjs`.

Files: new `openjev_mcp/batch/importers.py` (`batch/__init__.py` is P01's), new
`openjev_mcp/batch/exporters.py`, `openjev_mcp/lint.py` (add codes E030, E031, E032, W601-W605 to the code sets,
`RULES` and the finding constructor only), new `tests/test_mcp_batch_import.py`, new
`tests/test_mcp_batch_export.py`, new `tests/fixtures/batch/` (fixture files).

Contracts:
- `import_source(spec: dict, config, *, max_items: int) -> ImportResult` where `spec` is the `items_file` object
  (path, also, format, delimiter, state_field, id_field, state_template, array_key, encoding) and
  `import_items(items: list[dict], *, max_items) -> ImportResult`. `ImportResult(items: list[BatchItem(index,
  id, state)], questions: dict | None, options: dict | None, report: dict)` where `report` is the 2.11 output
  `import` object (format, delimiter, encoding, state_field, id_field, columns, row_count, truncated, warnings as
  `LintFinding` dicts). Errors are `ToolError("OJ_INVALID_INPUT")` carrying the E03x finding. Paths through
  `paths.resolve_read` and `paths.read_text`.
- `exporters.py`: `to_csv(header, rows, questions) -> str`, `to_markdown(header, rows, questions) -> str`,
  `to_ojui_batch(header, rows, questions, options, *, exported_at, title) -> str`, `to_jsonl(header, rows) -> str`
  (header record first). Rows are `BatchRow` dicts (`schemas.DEFS["BatchRow"]`), last row per id, index order.
  Column layouts exactly as 2.11 Exports. `write_new(path, text, config, ext)` via `paths.resolve_write(...,
  new_only=True)`.

Acceptance: every fixture of 6.6 "Batch import" (CSV `,` `;` tab, quoted delimiters, TSV, JSONL key discovery
over 500 objects, wrapped JSON per `array_key` default, single object, lines, blocks, UTF-16 BOM, cp1252 W603,
`.xlsx` and binary E030, `ojui-batch` restore + version 2 W604, `ojui-export` E032, duplicate ids and empty input
E031, merge of two CSVs with equal and different headers, `max_items` W601, empty rows W605, W602 guess,
`state_template` placeholders); CSV export column order equals the Playground on a noul+choice+score fixture;
`ojui-batch` export re-imports to the same states, questions and options; Markdown escapes pipes/newlines;
`test_mcp_lint.py` still green.

### P05 Batch runner, JSONL store, cursor, statistics (wave 2; depends P01, P02)

TASKS: 2.9, 2.10, 7 #23 (Decision 3). Spec: 2.11 batching model L1460-1479, output schema/rows L1571-1621, HTTP
mapping and sampling L1623-1637, concurrency and back-pressure L1638-1662, pagination/resume/cancel/progress
L1663-1700, review/audit/stats L1701-1716; 2.2 `$defs` QuestionStats/BatchHeader/BatchRow (in `schemas.DEFS`);
2.0.1 rules 7-8 L318-325; 6.6 "Cancellation" L5526-5529, "Progress" L5530-5531, "Batch runner" L5541-5557; mcp
spec section 6 (retry policy, cancellation, progress).

Files: `openjev_mcp/http.py` (second semaphore + per-call retry override only), new
`openjev_mcp/batch/runner.py`, new `openjev_mcp/batch/store.py`, new `openjev_mcp/batch/cursor.py`, new
`openjev_mcp/batch/stats.py`, `tests/test_mcp_http_client.py` (new cases only), new
`tests/test_mcp_batch_runner.py`, new `tests/test_mcp_batch_store.py`, new `tests/test_mcp_batch_cursor.py`, new
`tests/test_mcp_batch_stats.py`. (`batch/__init__.py` is P01's.)

Contracts:
- `http.OpenJevClient.systemone(..., pool: str = "default", retries: int | None = None)`; `pool="batch"` uses a
  semaphore of `config.max_inflight_batch`, separate from the default one.
- `store.py`: `question_hash(questions) -> "sha256:..."` (canonical JSON, sorted keys), `run_id(question_hash,
  source, options, sampling, regrey_samples)`, `make_header(...)`, `open_output(path, header, *, resume, config)
  -> OutputHandle` (exclusive `fcntl.flock` LOCK_NB; held -> `OJ_INVALID_INPUT` "output_path is in use by another
  batch call"; header `run_id` mismatch and `resume: false` on an existing file refused with the 2.11 messages;
  Decision 3 for bad lines), `OutputHandle.append(row)` (one write of line + newline, flush), `.size()`,
  `read_output(path, config) -> (header, last_rows_by_id: dict, n_lines, warnings)`.
- `cursor.py`: `ARGS_EXCLUDED = ("cursor", "max_items_per_call", "time_budget_s", "concurrency", "detail",
  "max_inline_results", "export")`, `args_hash(args)`, `encode(run, offset, args_hash, out_bytes, out_lines)`,
  `decode(token) -> dict` and `check(token_dict, *, run_id, args_hash, out_size)` with the three 2.11 messages.
- `stats.py`: `review_reasons(answers, questions, review_rule) -> list[str]`, `review_confidence(...)` (2.11:
  choice p_top, noul margin, score `1 - spread/levels`), `answer_confidence(...)` (2.21: noul |2p-1|, choice
  p_top, score argmax probability), `in_audit(seed, id, rate)` (`sha256(seed, id) < rate`),
  `per_question(rows, questions) -> dict[QuestionStats]`, `review_queue(rows)`.
- `runner.py`: `async run_batch(ctx, job: BatchJob) -> BatchRun` where `BatchJob(items, questions, options,
  sampling, regrey_samples, thresholds, review_rule, audit, concurrency, output: OutputHandle | None,
  include_state, max_items_per_call, time_budget_s, on_error, start_offset, skip_ids, only_ids)` and `BatchRun`
  holds `rows` (this call, index order), `status` (the 2.11 status block), `next_offset | None`,
  `stopped_reason`. Workers = min(concurrency, max_inflight_batch); reorder buffer; shared cooldown gate on
  429/503/529 (retry-after + jitter <= 250 ms, effective concurrency 1, +1 after 10 successes); backpressure stop
  (row not written); `on_error` record/abort; `sampling` fast (samples 1 + one regrey read with
  `regrey_samples`) vs `server_default` (no samples field), explicit `options.samples` wins; each row records
  request_id, server_timing, body_hash, latency, usage; progress `"<done>/<total> ok=<n> err=<n> eta <s>s"` via
  `ctx.progress`; cancellation writes only the completed rows next in index order, releases the lock, sends
  nothing. The linter runs once before row 0 (`lint.lint_request` on the first body).

Acceptance (6.6 "Batch runner", "Cancellation", "Progress"): output order equals input order for concurrency
1-4; 529 + retry-after pauses all workers and drops effective concurrency to 1, recovering after 10 successes;
backpressure stop with no row written and continuation from it; abort vs record; cursor tamper / changed args /
shrunk output messages; changing concurrency or max_items_per_call accepted; run_id mismatch, `resume: false`
on existing output and a concurrent second call refused; `retry_errors`/`only_ids` append with last-row-wins;
audit sample identical for concurrency 1 and 4; sampling modes; cancellation with concurrency 3 on
`slow_transport` leaves only whole lines and a following resume has no duplicates and no gaps; progress strictly
increasing, <= 1/s, none without token; corrupt-line policy; `include_state: false` writes no state text.

### P06 `filter` tool, recipe registry, `recipe` tool (wave 3; depends P01, P02, P03)

TASKS: 2.1, 2.2, 2.6. Spec: 2.10 L1308-1444 (schemas L1321-1370, packing L1371-1395); 2.16 L2459-2580; 2.18
L2704-2789 (recipe schema, loading of `OPENJEV_MCP_RECIPES`, "skipped with a warning in status"); 6.6 "Packing"
L5505-5506; 6.7 rows `filter`, `recipe` L5593-5594; mcp spec section 10 extension points L380-395; arch E
(tools/call pipeline) L1233-1281.

Files: new `openjev_mcp/tools/filter.py`, new `openjev_mcp/recipes/registry.py`, new
`openjev_mcp/tools/recipe_tool.py`, `openjev_mcp/tools/status.py` (registry load warnings), new
`tests/test_mcp_filter.py`, new `tests/test_mcp_recipe_registry.py`, new `tests/test_mcp_recipe_tool.py`,
`tests/test_mcp_status.py` (new cases only).

Contracts:
- Every new tool module: `register(config) -> ToolSpec` (calls `schemas.register_tool_schemas`, returns
  `ToolSpec(name, title, description, INPUT, OUTPUT, annotations, handler, prepare=None)` with the 2.5
  annotations). P14 calls it; do not edit `dispatch.py`.
- `filter.py`: `pack_state(task, items_label, items) -> str` (each text JSON-string-escaped, one line per item),
  `async run_filter(ctx, args) -> dict` (usable by the `openjev filter` CLI with a hand-built `ToolContext`),
  sequential packs of `pack_size`, keep/drop/grey with `grey` policy, `pick_best` (exists noul + best choice over
  ids + none per pack), `graded` (score per item, `relevant_at`), empty `items` -> no request. Reads through
  `tools.read.run_read` (lint, timeouts, retries, meta, progress).
- `registry.py`: `load_all(config) -> Registry` (builtin `recipes/builtin/*.json` + `config.recipes_dir/*.json`
  under the same loader; a failing file is skipped with a warning, never half-loaded; an extra recipe may not
  shadow a builtin id), `Registry.ids()`, `.get(id) -> Recipe`, `.document(id) -> dict` (raw JSON for
  `openjev://recipes/{id}`), `.index() -> list[dict]` (id, title, description, decisions, input summary,
  variant_of), `.warnings`; cached per process (lists identical on every call).
- `recipe_tool.py`: input per 2.16 with `recipe` enum = `registry.load_all(config).ids()` inside
  `register(config)`; inputs validated with
  jsonschema against the recipe `input_schema` -> `OJ_INVALID_INPUT` whose `hint` carries the schema JSON;
  `profile`, `policy` (unknown keys refused), `fail_mode`, `dry_run` (`engine.build_requests`, no I/O,
  `built_requests` in the output), `options`; output per 2.16 (`degraded`, `thresholds_used`, `requests`,
  `answers`, `meta`).
- `status.py`: add registry warnings to `warnings`.

Acceptance: `ex-filter` replay equals the 2.10 output; packing test (`"ok\nL4 disk full"` stays one line, no
question for the forged id); packs split at `pack_size`; empty items no request; grey policy; `ex-gate-deny` and
`ex-gate-allow` through the `recipe` tool equal 2.16 (thresholds keys per mcp spec deviation 22); `dry_run` with
`fail_on_request_transport`; each `fail_mode` gives its degraded decision; extra recipe dir: good file loads, bad
file skipped with a status warning, shadowing refused.

### P07 gate-10 fix and the phase-2 recipes (wave 3; depends P03)

TASKS: 2.3, baseline (A) gate-10. Spec: 2.18 table L2793-2819 and `command_gate` document L2821-2865; 5.2
L4272-4345 (act_or_ask), 5.3 L4345-4367, 5.4 L4369-4416 (injection_screen), 5.5 L4416-4462 (done_gate), 5.8
L4576-4623 (model_routing), 5.9 L4623-4666 (skill_selection), 5.10 L4666-4748 (typed_call), 5.17 L5003-5050
(moderation). Case files `docs/mcp-skill-spec/tests/cases/{02,03,04,05,08,09,10,17}-*.json`; captured `u02`,
`u02-blast`, `u04`, `u04-benign`, `u05`, `u08`, `u09`, `u10`, `u10-negation`, `u17`, `u17-allow`.

Files: `openjev_mcp/recipes/builtin/command_gate.json`, new `recipes/builtin/{act_or_ask,injection_screen,
done_gate,moderation,model_routing,skill_selection,typed_call}.json`, `tests/test_mcp_command_gate.py`, new
`tests/recipe_harness.py` (shared helper, read-only for P17/P18: load a builtin, run against stub answers or a
captured response, run a case file through `run_recipe`), new `tests/test_mcp_recipes_p2.py`, new
`tests/live/test_live_recipes_p2.py` (marked live).

Work:
- gate-10 (Decision 4). Constraints, not a prescribed clause: gate-10 must come out `ask` or `allow` (prefer
  `ask`); `ex-gate-deny` (remote_code 0.9999, out_of_scope 0.7282, verdict deny 0.9882) still `deny`; every 6.6
  rule case unchanged; `ex-gate-allow` still `allow`; strict and lenient profiles. Candidate to check first:
  keep `destructive`/`exfiltrates` as sole-signal denies and deny on `remote_code >= deny_hazard` only together
  with `verdict == 'deny'` or `out_of_scope >= allow_scope_max`. Read gate-10's full-recipe signals once live
  (one sequential call through `run_recipe` against :8080) before choosing, then run all 14 gate cases live once
  through the engine (sequential) and record the result in your report.
- The seven recipes, questions verbatim from section 5, policies from each "Policy:" paragraph, decisions and
  fail modes from 2.18 L2793-2819, `test_file` pointing at the case file, `limitations`. `model_routing` and
  `skill_selection` set `"routing": true`. Use only engine features P03 shipped; a missing one is reported.

Acceptance: every builtin loads; each recipe's captured `u*` example replays to the documented decision; each
fail mode gives its degraded decision; rule/`when` grammar tests; `test_mcp_command_gate.py` updated for the new
combine (the rule tests unchanged); the live test file runs each recipe's `test_file` through the engine
(skipped by default). You run only the 14 gate cases live (sequentially) and report 14/14; the other recipes'
live runs are P20's.

### P08 `batch` tool (wave 3; depends P02, P04, P05, P10)

TASKS: 2.11. Spec: 2.11 L1445-1819 (source rules L1482-1490, schema L1491-1531, output L1571-1599, dry_run
estimate L1633-1637, verified example L1748-1819); 2.0.1 rule 6 (resource_link) L316-317; 6.6 "Batch import"
source-rule sentences L5538-5540 and "Batch runner" L5541-5557; 6.7 `batch` row L5595.

Files: new `openjev_mcp/tools/batch_tool.py`, new `tests/test_mcp_batch_tool.py`.

Work: `register(config) -> ToolSpec` (annotations `readOnlyHint: false, destructiveHint: false, idempotentHint: true,
openWorldHint: false`). Source rules in code (`OJ_INVALID_INPUT`); `template` source through
`openjev_mcp.library.template(id)` (P10); importers (P04); lint once; `dry_run` (no HTTP: import report, 3
preview states, `first_body` byte-equal to the real run's first body, estimate with the Playground formula, W405
and W406); the run through `batch.runner.run_batch` with `pool="batch"`; cursor and resume via `batch.cursor`
and `batch.store`; summary scope `output_path` (recomputed from the file each call) vs `call`; review queue,
audit ids, `per_question`; exports written only by the call that finishes the job (`next_cursor` null), through
`batch.exporters`; one `ctx.links` entry per written file (jsonl `application/x-ndjson`, csv `text/csv`,
markdown `text/markdown`, ojui-batch `application/json`); compact vs full inline results;
`max_inline_results`. `images` is not part of this package (P15 adds it).

Acceptance: `ex-batch-1..3` replay gives the 2.11 output (L1748-1819); source-rule errors (two sources, no
questions, `export` without `output_path`); dry_run sends nothing (`fail_on_request_transport`) and its
`first_body` equals the sent body; a cursor loop over 60 stub rows with `max_items_per_call` 25 then an
interrupted call and a resume without cursor: no duplicates, no gaps; `time_budget_s` honoured; concurrency cap
by `OPENJEV_MCP_MAX_INFLIGHT_BATCH`; export files and resource_link blocks; outputs validate against the output
schema.

### P09 `batch_results` tool and the batch prompts (wave 3; depends P02, P04, P05)

TASKS: 2.13, 2.14 (prompts `start_batch`, `review_batch`). Spec: 2.21 L2961-3045; 2.11 Exports L1717-1734; 2.18
prompts table L2894-2905; 6.6 "`batch_results`" L5558-5563 and "Resources, prompts, completion" L5564-5569; 6.7
`batch_results` row L5596.

Files: new `openjev_mcp/tools/batch_results.py`, new `openjev_mcp/batch/compare.py`, new
`openjev_mcp/batch_prompts.py`, new `tests/test_mcp_batch_results.py`, new `tests/test_mcp_batch_compare.py`, new
`tests/test_mcp_batch_prompts.py`.

Contracts: `register(config) -> ToolSpec` (annotations `readOnlyHint: false, destructiveHint: false,
idempotentHint: false, openWorldHint: false`); views rows/review/stats; filter, sort_by value/confidence
(`stats.answer_confidence`), order, limit, own cursor `base64url {v: 1, offset, args}`; export inline (64 KiB cap,
`truncated`) or to a new file (+ `ctx.links`), `filtered` jsonl keeps the header record; `compare.jsd(...)`
log base 2, noul Bernoulli, choice over shared keys, score levels (same count), `question_map`, `key_map`,
`agreement`, `flipped_ids`, `mean_abs_delta_p`, `jsd_reason`; never any HTTP request. `batch_prompts.py`:
`START_BATCH: PromptSpec` (args `template` required + complete, `items_path`, `output_path`; messages: the plan
text then an embedded resource `openjev://templates/{template}`) and `REVIEW_BATCH: PromptSpec` (args
`output_path` required, `limit` default 20; messages: the queue how-to then an embedded `application/json`
resource with the review queue and `per_question` from `batch.stats`). Paths through `paths.py`.

Acceptance: every 6.6 `batch_results` bullet (sort by value/confidence, `min_confidence_below`, review order,
last-row-wins, cursor pages without repeats, 64 KiB truncation, filtered jsonl readable by `batch` and
`batch_results`, existing export path refused, JSD hand-computed fixtures: identical files 0, disjoint keys
without `key_map` null with reason, `key_map` aligns, `only_in_a/b`); exports byte-identical to `batch` exports of
the same file (`exportedAt` aside, via `batch.exporters`); no network (`fail_on_request_transport`); prompt
builders return the documented messages and reject a missing required argument with `PromptError`.

### P10 Library data: templates, patterns, authoring guide (wave 2; depends P01)

TASKS: 2.14 (templates), 2.5 (patterns, guide content). Spec: 2.18 resources table L2867-2892 (templates: "5-10
states each", generated at build time from verified section 5 cases; patterns keyed `<usage>.<question_id>` with
measured values; guide = section 3 L3049-3415); 6.7 resources row L5597; case files
`docs/mcp-skill-spec/tests/cases/*.json`, captured `docs/mcp-skill-spec/tests/spec_build/captured.json`.

Files: new `mcp/scripts/gen_library.py`, new `openjev_mcp/data/templates.json`, new
`openjev_mcp/data/patterns.json`, new `openjev_mcp/data/guide_authoring.md`, new `openjev_mcp/library.py`, new
`tests/test_mcp_library.py`.

Contracts: `library.templates_index() -> list[dict]` (id, title, description, question_count, state_count),
`library.template(id) -> dict | None` (`{id, title, questions, options, states: [{id, state}], source_case}`),
`library.template_ids() -> list[str]`, `library.patterns() -> dict`, `library.guide() -> str`; data read once
from package data (`importlib.resources`), no network. The generator is deterministic (stable ids
`^[a-z0-9_]{1,64}$`, sorted output) and is run by you; its output files are committed package data. A template
is built only from cases sharing one question set; patterns take measured values from captured responses where
captured, else from the case's `expect` block (say which in each entry).

Acceptance: every template validates against `QuestionSet`, lints clean with `lint.lint_request` (default
profile, no errors), has 5-10 states, and each state + questions equals a request in its source case (and, when
captured, replays to the captured answers through `replay_transport`); re-running the generator is
byte-identical; the guide equals spec section 3.

### P11 `calibrate` tool, audit store, calibration prompts (wave 3; depends P02, P04, P05)

TASKS: phase 3 `calibrate`, `openjev://audits/{question_hash}`, prompts `audit_question`, `explain_answer`.
Spec: 2.15 L2320-2458 (schema L2330-2358, output L2360-2380, fitting and metrics L2381-2396, verified example
L2397-2458); 2.18 audits resource row L2879 and prompts rows L2900-2902; 2.0.1 rule 5 (audits 0/private); 6.6
"`calibrate` from_batch" L5579-5580; 6.7 `calibrate` row L5599; Playground stats page (behaviour reference):
grep `ui/static/js/jev/` for the reliability/ECE code.

Files: new `openjev_mcp/calibration.py`, new `openjev_mcp/tools/calibrate.py`, new `openjev_mcp/audit_store.py`,
new `tests/test_mcp_calibration.py`, new `tests/test_mcp_calibrate_tool.py`, new `tests/test_mcp_audit_store.py`.

Contracts: `calibration.py` pure: `fit_noul`, `precision_coverage`, `suggested_band`, `confusion`,
`ladder_monotonic`, `reliability_bins` (10 bins over [0.5, 1]), `brier`, `ece`, `confidence_hist`,
`entropy_hist` (20 bins), `question_hash` (reuse `batch.store.question_hash`), `drift(prev, cur)`. Tool
`register(config) -> ToolSpec` (annotations not RO, not destructive): sources questions+examples, recipe+examples,
`case_file` (run_cases format: `noul_gte` -> true, `noul_lte` -> false, choice -> key), `from_batch` (no
request); reads through `batch.runner` (cursor, concurrency as `batch`); `think` examples read twice and
flagged; `store`/`compare_to` through `audit_store` (overwrite only a file that parses as a calibrate record,
`paths.resolve_write`). `audit_store.audits_template() -> dict` and `async read_audit(uri, ctx) -> dict`
(`openjev://audits/{question_hash}` under `config.audit_dir`, ttlMs 0, private). Prompt specs `AUDIT_QUESTION`
(schema_path, labels_path) and `EXPLAIN_ANSWER` (answer: a request id found in `OPENJEV_MCP_LOG`, or pasted
response JSON) as `prompts.PromptSpec` values exported from `tools/calibrate.py`.

Acceptance: `ex-cal-1..7` replay reproduces the 2.15 report; `question_hash` independent of key order; drift
reports a changed resolved model; `from_batch` equals an equivalent `examples` run with no HTTP request; bins,
Brier, ECE and histograms equal hand-computed fixtures; store refuses a non-calibrate file.

### P12 MCP Tasks extension (wave 3; depends P01, P02)

TASKS: phase 3 "optional MCP Tasks extension behind `OPENJEV_MCP_TASKS`"; research gap Tasks. Spec: 2.0.1 rule
9 L326-331; 2.19 L2922 (why deferred); mcp spec section 2 "Not implemented". Normative extension text: fetch
https://modelcontextprotocol.io/extensions/tasks/overview and https://github.com/modelcontextprotocol/ext-tasks
(WebFetch, read once). SDK: `.venv/lib/python3.12/site-packages/mcp/server/extension.py` (`Extension`,
`methods`, `intercept_tool_call`, `compose_tool_call_handler`), `mcp/server/context.py` (`ServerRequestContext.meta`).

Files: new `openjev_mcp/tasks.py`, new `tests/test_mcp_tasks.py`.

Work: `class TasksExtension(Extension)` with `identifier = "io.modelcontextprotocol/tasks"`; `factory(config) ->
TasksExtension | None` (None unless `config.tasks`); in-process store (dies with the process; `ttlMs`,
`pollIntervalMs`); `intercept_tool_call` returns `CreateTaskResult {resultType: "task", task: {taskId, status,
ttlMs, pollIntervalMs}}` for `batch` and `calibrate` only when the request is 2026-07-28 era and its `_meta`
`clientCapabilities.extensions` lists the identifier; otherwise `call_next`. The task runs the normal handler in a
task group owned by the extension (bounded: at most `max_inflight_batch` running tasks), status working ->
completed/failed/cancelled, result kept until ttl. Methods `tasks/get` (status, progress message, the
`CallToolResult` when completed, as the extension spec defines), `tasks/update`, `tasks/cancel` (cancels the
handler: the batch writes whole lines and releases its lock, as on `notifications/cancelled`). Never -32021; a
client without the capability just gets the synchronous result. Verify first that `ctx.meta` exposes
`clientCapabilities`; if not, add the parsing inside `tasks.py` from the raw request (no server.py edit; report
if impossible). Register nothing globally: P19 appends `tasks.factory` to `ext.EXTENSIONS`.

Acceptance: `test_mcp_tasks.py` builds the server with `build_server(config, extensions=[TasksExtension(...)])`
and a test-local slow stub tool registered as `batch` (P08 is in the same wave; do not import it): flag off -> no `capabilities.extensions`, synchronous results;
flag on + client capability -> task result, `tasks/get` polling to completed with the same structured content
as the synchronous call, `tasks/cancel` -> cancelled and no further messages; flag on + client without the
capability -> synchronous; legacy-era session -> synchronous; unknown task id -> JSON-RPC error.

### P13 Hooks `stop`, `userprompt`, `posttooluse`; CLIs `openjev check`, `openjev filter` (wave 4; depends P06, P07)

TASKS: 2.4. Spec: 2.20 L2926-2959; 5.4 L4369-4416 (injection_screen), 5.5 L4416-4462 (done_gate), 5.9
L4623-4666 (skill_selection); 4.4 "Hooks (Claude Code)" L3793-3801; mcp spec section 1 "Hook and recipe", section
11 "Re-record hook fixtures". Claude Code hook fields: fetch https://docs.claude.com/en/docs/claude-code/hooks once
(Stop: `stop_hook_active`, `decision: block` + `reason`; UserPromptSubmit: `prompt`, `additionalContext`;
PostToolUse: `tool_name`, `tool_input`, `tool_response`, `additionalContext`).

Files: `openjev_mcp/hook.py`, `openjev_mcp/claude_hooks.py`, `openjev_mcp/cli.py`, new
`tests/fixtures/claude_code/{stop,userprompt,posttooluse}_v2*.json`, new `tests/test_mcp_hook_events.py`, new
`tests/test_mcp_cli.py`.

Work: `openjev-hook stop [--max-blocks 2] [--timeout-ms 10000]` (done_gate; fails open = allow the stop; stop
blocking after `--max-blocks` consecutive blocks, counted statelessly from `stop_hook_active` and the
transcript; document the rule), `userprompt --roster skills.json [--threshold 0.8]` (skill_selection; fails open
= no hint; inject only at `p_top >= threshold`), `posttooluse --screen WebFetch,mcp__*` (injection_screen on
matching tools; fails to "uncertain" with a context note). `openjev check <recipe> --inputs f.json [--profile]
[--unattended]` prints the recipe JSON; exit 0 = the recipe's least severe decision, 1 = any other, 2 = error or
degraded. `openjev filter --gt 0.7 "<criterion>" [--task T] < file` (one item per line, ids `L1..`, through
`tools.filter.run_filter`): exit 1 if any kept, 0 none, 2 error; kept lines on stdout. Field names only in
`claude_hooks.py`; recipes loaded with `engine.load_builtin`; import isolation kept (`httpx` lazy).
Fixtures are hand-built like the phase-1 ones (report it as a known gap).

Acceptance: per event: decision with stub answers, fail mode on timeout/unreachable/bad JSON, `--timeout-ms`
backstop, output JSON shape; `test_mcp_hook.py` unchanged and green; CLI exit codes for each case; p95 own
overhead < 300 ms for a rule-decided pretooluse still holds.

### P14 Phase-2 integration (wave 4; depends P02, P06, P07, P08, P09, P10)

TASKS: 2.1, 2.2, 2.5, 2.11, 2.13, 2.14 (registration), Decisions 1, 6, 7. Spec: 2.5 L829-858; 2.18 resources
and prompts tables L2867-2905; 2.0.1 rules 4-5 L305-311; 6.6 "Protocol" L5491-5494, "Protocol conformance"
L5514-5525, "Resources, prompts, completion" L5564-5569; mcp spec section 10 "Extension points" L380-395 and
section 11 "Add a tool".

Files: `openjev_mcp/__init__.py`, `openjev_mcp/tools/dispatch.py`, `openjev_mcp/resources.py`,
`openjev_mcp/prompts.py`, `openjev_mcp/completion.py`, `mcp/pyproject.toml` (version 1.3.0),
`tests/test_mcp_dispatch.py`, `tests/test_mcp_resources.py`, `tests/test_mcp_protocol.py`,
`tests/test_mcp_server.py`, `tests/test_mcp_e2e.py`, `tests/test_mcp_skills.py`,
`tests/test_mcp_docs_consistency.py`.

Work: `TOOL_NAMES` phase-2 tuple and `CORE_TOOL_NAMES` (Decision 1), `__version__ = "1.3.0"`;
`dispatch.PHASE_TOOLS` interleaves the phase-1 entries with `filter.register`, `batch_tool.register`,
`recipe_tool.register`, `batch_results.register` in 2.5 order; `tools_for` with `core` filters. Resources in 2.18 order: `openjev://recipes`
(registry index), `openjev://templates` (library index), `openjev://patterns`, `openjev://guide/authoring`
(`text/markdown`); templates `openjev://recipes/{id}`, `openjev://templates/{id}`; readers for them and for
`file://` batch outputs (only `.jsonl` inside the roots whose first line is this server's batch header; ttlMs 0
private; not listed). Every static resource: name, title, mimeType, annotations (audience, priority,
lastModified = build time), 3600000/public. Prompts `START_BATCH`, `REVIEW_BATCH` registered; completers for
`ref/prompt` `start_batch.template`, `ref/resource` `openjev://templates/{id}` `id` (library ids) and
`openjev://recipes/{id}` `id` (registry ids), prefix, <= 100. Update the existing tests that pin the phase-1
surface (dispatch registry, protocol list order and schema walk, `resource_templates_list` now non-empty,
capabilities, skills tests: keep them checking phase-1 names in the skills until P21; docs consistency).
`test_mcp_e2e.py`: stub OpenJev under uvicorn + server subprocess on spare ports: filter, recipe, a batch with
cursor + resume + export + resource_link, batch_results review + export, prompts/get both prompts, completion,
`resources/read` of each new URI and of a `file://` output.

Acceptance: full suite green; `tools/list` exactly the phase-2 tuple (and `core` the six); every new tool's
results validate against their output schemas over the wire; `resources/templates/list` returns the two phase-2
templates (P19 adds the third, audits); unknown URI -32602; prompts -32602 rules; `openjev://limits` and
`status` publish the batch caps (6.7 resources row).

### P15 Images: loader, SSRF fetch, `ask_image`, `batch.images` (wave 4; depends P01, P02, P08)

TASKS: phase 3 `ask_image`, `batch.images`. Spec: 2.12 L1820-1923; 2.2 L700-739 (paths L700-726, "Image URL
fetch" SSRF rules L727-738); 2.11 phase-3 `images` sentence L1487-1489; 6.6 "Paths and URLs"
L5501-5504; 6.7 rows `ask_image`, `batch.images` L5600-5601. Pillow 12.3 is installed in `.venv`; keep it an
optional import (P19 adds the `images` extra).

Files: new `openjev_mcp/images.py`, new `openjev_mcp/fetch.py`, new `openjev_mcp/tools/ask_image.py`,
`openjev_mcp/tools/batch_tool.py` (add `images`), new `tests/test_mcp_images.py`, new `tests/test_mcp_fetch.py`,
new `tests/test_mcp_ask_image.py`, new `tests/test_mcp_batch_images.py`.

Contracts: `images.load_images(items, config, *, max_side_px) -> list[LoadedImage(data_url, source, sent_as,
bytes, reencoded)]` (path via `paths.resolve_read(kind="image")`, url via `fetch.fetch_image`, data_url,
base64+content_type; decode with Pillow, undecodable -> `OJ_INVALID_INPUT` before any request; SVG/PDF refused or
converted; downscale; re-encode png/jpeg with exact lowercase type; 5,242,880 bytes each, body < 64 MiB).
`fetch.fetch_image(url, config)`: only with `config.fetch`; https only; resolve once and connect to that address
(custom httpx transport); refuse loopback, RFC 1918, fc00::/7, link-local incl. 169.254.169.254, fe80::/10, CGNAT,
multicast, unspecified; <= 3 redirects re-checked; 10 s total; stream abort past 20 MiB; no cookies, never the
OpenJev key. `ask_image.register(config) -> ToolSpec` (`openWorldHint` = `config.fetch`; options without
think/sequential); output = `ask` output + `images`. `batch_tool`: `images` (1-8) loaded once per call, the same
data URLs in every row body, `think`/`sequential` refused (E022), `ojui-batch` with `imageCount` > 0 warns W604.

Acceptance: `ex-image` replay; path rules; SSRF list (127.0.0.1, [::1], 169.254.169.254, a name resolving to a
private address, redirect to private, oversize body, slow body) with a fake resolver/transport, never real
network; re-encode respects `max_side_px` and type spelling; batch images identical in every row body;
`test_mcp_packaging` unchanged (no Pillow in core requirements).

### P16 `compile` and `generate` (wave 4; depends P05, P06, P11)

TASKS: phase 3 `compile` (incl. `recipe.variants`), `generate`, prompt `author_question`; baseline ex-generate
flake (empty reply retried once). Spec: 2.14 L2127-2319 (schema L2142-2158, output L2159-2175, procedure
L2176-2203, verified example L2204-2319); 2.17 generate L2652-2702; 2.4 rows for chat errors L756-828 (404
`model_not_found` -> `OJ_UNKNOWN_MODEL`); 2.18 prompt row `author_question` L2900; 6.7 row L5602.

Files: `openjev_mcp/http.py` (add `chat(body, *, timeout_ms)` for `/v1/chat/completions`, OpenAI error shape),
new `openjev_mcp/tools/compile.py`, new `openjev_mcp/tools/generate.py`, `tests/test_mcp_http_client.py` (new
cases only), new `tests/test_mcp_compile.py`, new `tests/test_mcp_generate.py`.

Contracts: `compile.register(config)`: route read over the 24 primary recipe descriptions + `none` from the registry
(variants not routed; listed in `recipe.variants` with a human question), sub-decision typing one per request
with the deterministic pre-rules, instantiate the recipe template with `labels` (missing descriptions -> slots
`<SLOT:name>`), lint (profile `gate` for gates), optional probe via `ask` (`samples: 1`), optional calibrate
(`tools.calibrate` function), human questions, next steps; never calls chat. `AUTHOR_QUESTION` prompt spec
(intent, examples) exported from `tools/compile.py`. `generate.register(config)`: guards of 2.17 (role required
locally, empty content with `completion_tokens: 0` retried once, `max_tokens` clamp 8192, multi-token `stop`
flagged, MLX warnings when backend is mlx or unknown), `config.chat_model` default.

Acceptance: `ex-compile-recipe`, `ex-compile-recipe-none`, `ex-compile-qtype-1..4`, `ex-compile-draft` replays
give the 2.14 outputs; draft lints clean; `ex-generate` replay; empty reply retried once then returned with
`retried: true`; missing role refused before any request.

### P17 Phase-3 recipes, part A (wave 4; depends P03, P07)

TASKS: phase 3 "remaining recipes and variants" (part). Spec: 2.18 table L2793-2819; 5.1 L4218-4271
(ticket_triage), 5.6 L4462-4521 (semantic_lint), 5.7 L4522-4575 (issue_triage, duplicate_check,
review_finding_filter), 5.11 L4748-4789 (select_extraction, verify_fields), 5.12 L4790-4830 (judge_assert,
judge_pairwise), 5.13 L4831-4877 (alert_triage), 5.16 L4962-5002 (claim_check). Case files 01, 06, 07, 11, 12,
13, 16; captured `u01`, `u06`, `u06-secret`, `u07`, `u11`, `u12`, `u13`, `u16`.

Files: new `recipes/builtin/{ticket_triage,semantic_lint,issue_triage,duplicate_check,review_finding_filter,
select_extraction,verify_fields,judge_assert,judge_pairwise,alert_triage,claim_check}.json`, new
`tests/test_mcp_recipes_p3a.py`, new `tests/live/test_live_recipes_p3a.py` (marked live).

Work: as P07 (questions verbatim, policy, decisions, fail modes, `variant_of` for variants, `test_file`,
limitations), using `tests/recipe_harness.py` read-only and only P03 engine features (report gaps).
Acceptance: all load; captured examples replay to the documented decisions; fail modes degraded; the live file
runs each `test_file` through the engine. Write it, check it collects and skips by default, but **do not run it**
(P18 runs in the same wave on the one GPU; P20 is the authoritative live run).

### P18 Phase-3 recipes, part B (wave 4; depends P03, P07)

TASKS: phase 3 "remaining recipes" (part). Spec: 2.18 table L2793-2819; 5.14 L4878-4919 (semantic_filter), 5.15
L4920-4961 (rag_gate), 5.18 L5050-5097 (rubric_score; data `docs/mcp-skill-spec/18-composite-rubric-scoring.json`),
5.19 L5098-5146 (entity_match, memory_decide), 5.20 L5147-5190 (taxonomy_classify), 5.21 L5191-5239
(bulk_label), 5.22 L5240-5304 (ui_decision, both modes), 5.23 L5305-5346 (multistep_tick), 5.24 L5347-5369
(threshold_audit). Case files 14, 15, 18-24, 22b; captured `u14`, `u15`, `u18-a`, `u18-b`, `u19`, `u20`, `u21`,
`u22-image`, `u22-tree`, `u23`.

Files: new `recipes/builtin/{semantic_filter,rag_gate,rubric_score,entity_match,memory_decide,
taxonomy_classify,bulk_label,ui_decision,multistep_tick,threshold_audit}.json`, new
`tests/test_mcp_recipes_p3b.py`, new `tests/live/test_live_recipes_p3b.py` (marked live).

Work: as P17. Recipes whose section says "runs `filter`/`batch`/`calibrate`/`ask_image`" decide one unit through
the engine (`read_per_item`, images input) and name the delegate tool in `limitations`; `ui_decision` image mode
takes data URLs (the `ask_image` loader is not called by the engine). Acceptance as P17 (live file written, not run;
the image cases use the PNGs in `docs/mcp-skill-spec/tests/data/`).

### P19 Phase-3 integration (wave 5; depends P11, P12, P14, P15, P16, P17, P18)

TASKS: phase 3 registration, prompts `author_question`, `audit_question`, `explain_answer`, `openjev://audits`,
Tasks behind `OPENJEV_MCP_TASKS`, Decision 1 phase-3 tuple, Decision 7. Spec: 2.5 L829-858; 2.18 L2867-2905;
2.0.1 rules 4, 5, 9 L305-331; 6.6 "Protocol conformance" L5514-5525.

Files: `openjev_mcp/__init__.py`, `openjev_mcp/tools/dispatch.py`, `openjev_mcp/resources.py`,
`openjev_mcp/prompts.py`, `openjev_mcp/completion.py`, `openjev_mcp/ext.py`, `mcp/pyproject.toml` (1.4.0,
`[project.optional-dependencies] images = ["Pillow>=10"]`, test extra adds Pillow), `tests/test_mcp_dispatch.py`,
`tests/test_mcp_resources.py`, `tests/test_mcp_protocol.py`, `tests/test_mcp_server.py`, `tests/test_mcp_e2e.py`,
`tests/test_mcp_skills.py`, `tests/test_mcp_docs_consistency.py`, `tests/test_mcp_packaging.py`.

Work: phase-3 `TOOL_NAMES` (Decision 1; `generate` only in `all`), register `ask_image`, `compile`,
`calibrate`, `generate`; audits template + reader (`audit_store`); prompts in 2.18 order (start_batch,
review_batch, author_question, audit_question, explain_answer); completers for any new completable argument;
`ext.EXTENSIONS.append(tasks.factory)`; `__version__ = "1.4.0"`. Tests: list order, schema walk, three resource
templates, prompts list order, capabilities with `OPENJEV_MCP_TASKS` on (extensions present) and off (absent),
e2e for each phase-3 tool against the stub, packaging (Pillow only in the extra).

Acceptance: full suite green; `tools/list` exactly the phase-3 tuple; Tasks e2e over HTTP with the flag on.

### P20 Live verification, phase 2 and 3 (wave 6; depends P13, P19)

TASKS: phase-1 exit leftovers, 2.3/2.11 live records, phase-3 live; baseline (A) items. Spec: 6.1 L5372-5387,
6.3 pass table L5415-5453, 6.4 known flakes L5454-5463, 6.7 L5582-5602; 2.11 live requirement (6.7 `batch` row).

Files: `mcp/tests/live/run_live.py`, new `mcp/tests/live/test_live_tools.py` (marked live), new
`mcp/tests/live/data/` (generated 200-row CSV), new `mcp/tests/live/results/2026-10-phase2-3.json`.

Work (sequential model calls; own MCP server from the working tree on a port in 8190-8199 started with
`.venv/bin/python -m openjev_mcp --transport http --port <p>` and killed in `finally`; never touch :8100):
fix `run_live.py`'s `ex-classify-abstain` check (accept `abstained: true` with `top == expected`); run
`run_cases.py` 00 and 03, the hook over the 14 gate cases (expect 14/14 after P07, incl. gate-10, and the
error cases 13/14 failing closed), the recipe live files of P07/P17/P18 (`pytest --live`), filter `ex-filter`,
a 200-row CSV `batch` at concurrency 2 through MCP following `next_cursor`, one interrupted-then-resumed batch
(cancel mid-call, resume without cursor: no duplicates or gaps), `batch_results` review/export/compare on it,
`ask_image ex-image`, `calibrate ex-cal`, `compile ex-compile-*`, `generate ex-generate` (5 repeats to measure
the empty-reply flake), the hook events of P13 on a fixture each, and Tasks with the flag on. Record per check:
pass/fail, latency p50/p95, measured batch `req_per_s` (replaces the spec 7 #26 estimate), and every mismatch
with its classification (model, server, MCP bug, test bug). Do not fix code: report MCP bugs with repro.

Acceptance: results file written; report lists pass counts per case file against 6.3, the two batch runs, and
open bugs. Stop and report if OpenJev is down.

### P21 Skills and READMEs for phases 2 and 3 (wave 6; depends P13, P19)

TASKS: 2.7, 2.15, 2.16, phase-3 skills, 1.24 updates; baseline doc gap (classify abstain wording). Spec: 4.0
L3416-3454; 4.1 L3455-3567; 4.2 L3568-3647; 4.3 L3648-3731; 4.4 L3732-3808; 4.5 L3809-3868; 4.6 L3869-3922;
4.7 L3923-3967; 4.8 L3968-4077 (batch jobs L3999-4054); 4.9 L4078-4124; 4.10 L4125-4159; 4.11 L4160-4209; 2.20
L2926-2959 (hook setup); mcp spec section 10 "Allowed roots".

Files: `mcp/skills/openjev-decisions/SKILL.md`, `mcp/skills/openjev-question-authoring/SKILL.md`, new
`mcp/skills/openjev-{agent-gates,code-checks,dispatch,triage-routing,retrieval-relevance,multistep,data-records,
ui-vision,calibration}/SKILL.md`, `mcp/README.md`, root `README.md` (MCP paragraph only),
`tests/test_mcp_skills.py`, `tests/test_mcp_docs_consistency.py`, new `tests/test_mcp_batch_walkthrough.py`.

Work: skills verbatim from section 4 adapted to the built names (tools, recipe ids, arguments as built); the
decisions skill gains the phase-2/3 rows, the three batch failure bullets and the abstain wording (`classify`
returns `label: null, abstained: true` with `top`); question-authoring gains `compile`; data-records full (batch
and recipe parts). README: surface tables, hooks setup (all four events, settings.json example, timeout rule),
batch section (cursor loop, `resume`, privacy of output files, `concurrency` on MLX vs vLLM, absolute paths under
the HTTP daemon per Decision 2), `OPENJEV_MCP_TASKS`, images extra. Tests: skills mention only real tools and
recipe ids; README commands run against the mock server; the 4.8 nine-step walkthrough (dry run, run, follow
cursor, interrupt, resume, review, export, compare) against the stub using only the arguments the skill shows.

Acceptance: full suite green.

### P22 `mcp/spec.md` 1.4 (wave 7; depends P20, P21)

Spec: mcp spec itself (versioning rules L7-9, all sections); the Deviations lists of P01-P21 reports; P20
results file; this file's Decisions.

Files: `mcp/spec.md`.

Work: rewrite sections 1 (surface: 14 tools, resources, prompts, completion, Tasks, entry points), 2 (protocol:
revision table unchanged, SDK unchanged, mismatches re-checked, "Not implemented" now subscriptions/listen,
logLevel emission, RFC 9728 PRM, x-mcp-header, MRTR, roots/sampling), 3 (every deviation, incl. Decisions 1-5
and 8, gate-10, hand-built Tasks, hook fixtures hand-built), 4 (env), 8 (test count), 9 (TASKS mapping for 2.x
and phase 3 with status), 10 (roadmap: what is left), 11 (upgrade procedure incl. Tasks and extensions), change
log 1.3 and 1.4, header version. Written from the code as built; every claim checked against code or results.

Acceptance: `test_mcp_docs_consistency.py` green; every env name, tool name and mise name in the file matches
the code (release checklist step 4).

## Coverage

| Item | Package(s) |
|---|---|
| A: gate-10 over-deny | P07 (fix), P20 (14/14 live), P22 (deviation) |
| A: ex-generate empty reply | P16 (retry once), P20 (repeat measurement) |
| A: `ex-classify-abstain` check, abstain wording | P20 (`run_live.py`), P21 (skills) |
| A: gate-13/14 not run through MCP/hook | P20 |
| B: latest revision 2026-07-28, SDK 2.2.0 pin | no change (P22 records) |
| B: `capabilities.extensions`, Extension plumbing | P02, P12, P19 |
| B: Tasks extension (`io.modelcontextprotocol/tasks`) | P12, P19, P20 |
| B: OTel `_meta` keys | P02 (audit) |
| B: per-request `logLevel`, no `notifications/message` | P02 (test) |
| B: `subscriptions/listen` | P02 (test + not implemented), P22 |
| B: MRTR / `InputRequiredResult` | not used (no elicitation); P22 |
| B: SEP-2106 schemas, deterministic `tools/list`, ttlMs/cacheScope on templates and reads | P01, P02, P14, P19 |
| B: resource-not-found -32602 | P02, P14 |
| B: auth 401 `WWW-Authenticate`, RFC 9728 PRM | P02 (kept), P22 (PRM not added, documented) |
| B: x-mcp-header, -32021 | not used; P12 never returns -32021; P22 |
| 2.1 filter | P06, P14 |
| 2.2 recipe tool | P03, P06, P14 |
| 2.3 recipes | P07 (+ P20 live) |
| 2.4 hooks, `openjev check`/`filter` | P13 |
| 2.5 resources recipes/patterns/guide | P06 (registry), P10 (data), P14 |
| 2.6 extra recipe directory | P01 (config), P06 |
| 2.7 skills | P21 |
| 2.8 batch importers | P04 (+ `template` source: P10 data, P08) |
| 2.9 batch runner | P05 |
| 2.10 cursor, resume | P05, P08 |
| 2.11 batch tool | P08, P14, P20 (live runs) |
| 2.12 exporters | P04 |
| 2.13 batch_results | P09, P14 |
| 2.14 templates, prompts, completion | P02 (plumbing), P09 (prompts), P10 (templates), P14 |
| 2.15 batch skills | P21 |
| 2.16 README | P21 |
| 3: batch.images | P15 |
| 3: calibrate | P11, P19 |
| 3: ask_image + SSRF | P15, P19 |
| 3: compile | P16, P19 |
| 3: generate | P16, P19 |
| 3: remaining recipes and variants | P17, P18 (engine: P03) |
| 3: prompts author/audit/explain | P16, P11, P19 |
| 3: `openjev://audits` | P11, P19 |
| 3: Tasks | P12, P19 |
| 3: skills ui-vision, calibration, data-records recipe parts | P21 |
| 7 #23 corrupt JSONL | Decision 3, P05 |
| 7 #26 shared-load batch latency | P20 (measured `req_per_s`) |
| mcp spec 1.4 | P22 |
