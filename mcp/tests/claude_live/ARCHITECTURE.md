# claude_live: architecture of the Claude Code live suite

Live end-to-end suite: `claude -p` (Claude Code headless, the user's subscription login) -> an own `openjev-mcp`
instance -> the real OpenJev on :8080 (real MLX model, one GPU). It checks what a Claude Code user actually gets from
the server: tool calls, results, errors, resources, prompts, hooks and skills. 100 cases, T001-T100, in 10 groups.

Normative sources, in this order: `mcp/spec.md` (1.4, "spec N"), the build spec
`docs/mcp-skill-spec/OPENJEV_MCP_SKILLS_SPEC.md` v1.2 ("build 2.x", grep the heading, never read it whole),
`mcp/docs/architecture.md` ("arch"). Ground truth for model answers comes only from the case files in
`docs/mcp-skill-spec/tests/cases/*.json` and the assertions already proven in `mcp/tests/live/` (named per row below);
no expected decision is invented.

```sh
OPENJEV_CLAUDE_LIVE=1 .venv/bin/python -m pytest -q mcp/tests/claude_live              # everything, serial
OPENJEV_CLAUDE_LIVE=1 .venv/bin/python -m pytest -q mcp/tests/claude_live -m g04 -k T032
.venv/bin/python mcp/tests/claude_live/run_claude_live.py --groups g01,g02 --procs 3     # one pytest per group
```

Never part of `mise run test` / CI: without `OPENJEV_CLAUDE_LIVE=1` the directory's `conftest.py` sets
`collect_ignore_glob = ["test_*.py"]`, so nothing is even imported. `OPENJEV_LIVE=1` alone does not enable it
(different marker, different cost: this suite spends subscription usage).

## 0. Verified facts (probes, 2026-10-02, claude 2.1.287, haiku)

Probe transcripts are kept in `fixtures/probes/` (parser self-test input, see 1.6). Everything below was observed,
not assumed.

| # | Fact | Consequence |
|---|---|---|
| F1 | `--output-format json` prints one JSON array: `system/init` (tools, `mcp_servers[{name,status}]`, `slash_commands`, `skills`, `plugins`, `model`, `apiKeySource`), `assistant`/`user` messages, `system/thinking_tokens`, `rate_limit_event`, final `result` (`subtype`, `is_error`, `result`, `num_turns`, `duration_ms`, `total_cost_usd`, `modelUsage`, `permission_denials`) | parser ignores unknown event types |
| F2 | Tools appear as `mcp__openjev__<tool>`, all 14, in `init.tools`; `apiKeySource: "none"` under the subscription login | precondition check per run |
| F3 | `--setting-sources ""` is accepted and drops user plugins/hooks/settings (only `cc-plugin-*@builtin` remain) | default isolation |
| F4 | `--safe-mode` drops the `--mcp-config` server (`mcp_servers: []`) and loads every user plugin; `--bare` never reads OAuth | both unusable, never pass them |
| F5 | `--disable-slash-commands` removes skills but keeps the 5 MCP prompts as `mcp__openjev__<prompt>` slash commands | on by default, off for skill cases |
| F6 | `--tools ""` removes built-ins incl. `ListMcpResourcesTool`/`ReadMcpResourceTool`; name them in `--tools` to get them | resource cases pass `--tools "ListMcpResourcesTool,ReadMcpResourceTool"` |
| F7 | `ReadMcpResourceTool` input `{server, uri}`; result content is the JSON of `{contents:[{uri,mimeType,text}]}` | resource assertions parse `contents[0].text` |
| F8 | `-p "/mcp__openjev__explain_answer {...}"` runs the MCP prompt (positional, whitespace-split args); the expanded prompt is NOT in the transcript | prompt cases assert on the wire (`prompts/get`) and on the tool call the prompt asks for |
| F9 | `--permission-mode dontAsk`: a tool not in `--allowedTools` is denied (`permission_denials`), but a PreToolUse hook `allow` runs it anyway | hook allow vs defer-allow is deterministic |
| F10 | `--settings <file>` hooks run under `--setting-sources ""`; the real PreToolUse payload carries `session_id, transcript_path, cwd, prompt_id, permission_mode, hook_event_name, tool_name, tool_input, tool_use_id`; Stop carries `stop_hook_active, last_assistant_message, background_tasks, session_crons` | `fixtures/probes/payload_*_2.1.287.json` are the first real payloads (spec deviation 41) |
| F11 | hook model-deny: `git push --force` with task "Summarise the README" -> `tool_result.is_error`, text `PreToolUse:Bash hook error: openjev command_gate: destructive=...`, plus `permission_denials` | reliable deny case |
| F12 | haiku refuses to issue `echo aGk= \| base64 -d \| sh` at all (no tool_use) | rule-deny case runs on the strong tier |
| F13 | Stop hook prints nothing when the turn has no tool call (`hook.decide_stop`: empty timeline -> allow, by design) | a done_gate hook case needs a tool call before the done claim |
| F14 | `--plugin-dir <dir>` with `.claude-plugin/plugin.json` + `skills/<name>` symlinks to `mcp/skills/*` loads all 11 as `openjev-skills:<name>`; `/openjev-skills:openjev-agent-gates ...` runs the skill (model then called `recipe command_gate`); without the slash the model called `yes_no` directly, no `Skill` tool_use | skill assertions are on the downstream MCP call |
| F15 | Claude Code speaks 2026-07-28: `server/discover` first, then `prompts/list`, `resources/list`, `tools/list`; every request has `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` on `tools/call`; `_meta` holds `clientCapabilities {roots, elicitation}` (no `extensions`), `progressToken`, `claudecode/toolUseId` | wire assertions; `claudecode/toolUseId` joins wire and transcript |
| F16 | With `OPENJEV_MCP_TASKS=on`, discover advertises `io.modelcontextprotocol/tasks` but Claude does not declare it, so `batch` returns the normal synchronous result | one negative Tasks case; Tasks proper stays covered by `live/test_live_tools.py::test_tasks_on` |
| F17 | A result with `resource_link` reaches the model as a list: `{"type":"text","text":"[Resource link: name] file:///..."}` then the JSON text block | parser takes the last text block that parses as JSON |
| F18 | haiku chose a relative `output_path` first -> `OJ_INVALID_INPUT` "relative path ...", then retried with an absolute path | prompts always give absolute paths (`{cwd}`), except the case that tests this |
| F20 | `--allowedTools "mcp__openjev"` allows every tool of the server (no denial on `yes_no`) | default allow list |
| F21 | `ReadMcpResourceTool` passes unlisted URIs through: `openjev://recipes/command_gate` (template) and `file://<abs>.jsonl` (batch output, mimeType `application/x-ndjson`) both read | template and file:// cases are reachable |
| F19 | Cost/time: first haiku run of a session $0.040 (20k cache creation), later runs $0.004-0.02, a skill run $0.056; 1.5-10 s per run | ~$4-8 and 20-40 min wall time for a full run at 3 slots |

## 1. Harness design

### 1.1 Layout

```
mcp/tests/claude_live/
  ARCHITECTURE.md
  conftest.py            gate (collect_ignore_glob), marker registration, session fixtures, sys.path insert of this dir
  cl_env.py              knobs (table 1.9), paths, port picking, cross-process slot semaphore
  cl_servers.py          McpInstance (spawn/health/stop own children only), WireTap, FaultUpstream
  cl_claude.py           ClaudeOpts, argv builder, run_claude(), Transcript parser
  cl_assert.py           assertion library (1.5)
  cl_cases.py            Case dataclass, Ctx, run_case() (retry, logging), setup helpers (seed files, seed batches)
  cl_results.py          results JSONL, run dirs, summary
  run_claude_live.py     runner: one pytest process per group, shared OJ_LIVE_RUN_ID, run budget, summary.json
  test_cl_selftest.py    parser/assertion self-tests over fixtures/probes (no claude, no model)
  test_g01_protocol.py ... test_g10_errors.py   CASES list + one parametrized test each
  fixtures/probes/       recorded transcripts and payloads (section 0)
  fixtures/              static inputs: injection_note.txt, roster, labels, recipe_extra/*.json
  results/.gitignore     "*" and "!.gitignore"; results/<run_id>/... is evidence, never committed
```

Module names start with `cl_` (pytest runs `--import-mode=importlib` with `pythonpath = ["tests"]`; `rpc`, `stubs`,
`run_live`, `recipe_harness` already exist there). Reuse, do not copy: `live/run_live.py` (`make_csv`, `QS`,
`read_rows`, `dup_gap`, `load`, `call`, `sc_of`) and `recipe_harness.py` (`load`, `cases_of`, `inputs_from_case`,
`check_expect`) are imported. Never create `__init__.py` here (spec 8: a package named `mcp` shadows the SDK).

### 1.2 Session fixtures (`conftest.py`)

- `live_env` (session): `GET $OPENJEV_BASE_URL/health` (default `http://127.0.0.1:8080`) within 5 s, else
  `pytest.skip("OpenJev not reachable at <url>: <err>; start it yourself, the suite never starts or stops it")`.
  `shutil.which(OJ_CLAUDE_BIN)` else skip. Never runs `mise run start/stop/restart`, never touches :8100.
- `work` (session): `results/<run_id>/work/` realpath'd (macOS `/tmp` -> `/private/tmp`). The root of every MCP
  instance (`OPENJEV_MCP_ROOTS=<work>`, process `cwd=<work>`, `OPENJEV_MCP_AUDIT_DIR=<work>/audits`,
  `OPENJEV_MCP_LOG=<work>/mcp-<profile>.audit.jsonl`). Test data the model must reach (`tests/data/hotdog.jpg`,
  `docs/mcp-skill-spec/tests/data/ui22_*.png`) is copied into `<work>/data/`, so the roots stay one directory.
- `mcp` (session, lazy per profile): `mcp("default")` returns a running `McpInstance(url, port, log)`. Profiles:

| Profile | Env on top of the base | Used by |
|---|---|---|
| `default` | `OPENJEV_BASE_URL=http://127.0.0.1:8080` | almost everything |
| `core` | `OPENJEV_MCP_TOOLSETS=core` | T006 |
| `tasks` | `OPENJEV_MCP_TASKS=on` | T005 |
| `ext` | `OPENJEV_MCP_RECIPES=<work>/recipe_extra` (copied from `fixtures/recipe_extra`), `OPENJEV_MCP_ROUTING=off` | T100 |
| `unreachable` | `OPENJEV_BASE_URL=http://127.0.0.1:<free port nobody listens on>`, `OPENJEV_MCP_RETRIES=0` | T094, T096 |
| `notopenjev` | `OPENJEV_BASE_URL=<the default instance's URL root>` (an HTTP server that is not OpenJev -> 404) | T095 |
| `fault` | `OPENJEV_BASE_URL=<FaultUpstream>`, `OPENJEV_MCP_RETRIES=1`, `OPENJEV_MCP_TIMEOUT_MS=2000` | T099 |

  Ports: MCP instances only in 8200-8299; `OJ_LIVE_MCP_PORT` pins the `default` port, the rest take the next free
  one (bind-test, spawn, wait for `/health`; on exit code 3 pick the next port, 3 tries). Command:
  `.venv/bin/python -m openjev_mcp --transport http --port P`. Teardown terminates the `Popen` handles this process
  started (`terminate`, 5 s, `kill`); never `pkill`/pattern kills (`-m openjev_mcp` also matches the user's :8100).
- `WireTap` (per run, in-process): a Starlette + httpx reverse proxy on an ephemeral port in a uvicorn thread, in
  front of the profile's instance (prototype: 30 lines, streams SSE through). Every claude run talks to its own tap,
  so `wire.jsonl` belongs to one run. Records per HTTP exchange: method, `mcp-*` request headers, request JSON,
  status, response messages (JSON body, or each SSE `data:` line parsed). Always on: it costs ~100 ms and turns
  prompts, resources, discover, progress and `structuredContent` into deterministic evidence. It sees only Claude <-> MCP traffic; MCP -> OpenJev reads are
  evidenced by `meta.requests` and the instance's `OPENJEV_MCP_LOG` audit JSONL.
- `FaultUpstream` (session, for `fault`): a proxy in front of :8080 that forwards everything except a
  `/v1/systemone` body whose `state` contains `#FAULT:<kind>`: `401` (`{"detail":{"error_type":"authentication_error"}}`),
  `503` (`retry-after: 1`), `529` (`overloaded_error`), `500` text/plain, `sleep` (30 s, client times out). The
  bodies follow build 2.4 and `tests/stubs.py`; check `mapping.py` for the exact shapes before writing them.
- `skills_plugin` (session): `<work>/plugin/openjev-skills/.claude-plugin/plugin.json`
  (`{"name":"openjev-skills","version":"0.0.0"}`) plus `skills/<name>` symlinks to every `mcp/skills/*` (F14).
- Per case attempt: logs in `run_dir = results/<run_id>/runs/<Tid>-a<n>/`; Claude's cwd is
  `<work>/cwd/<Tid>-a<n>/` (realpath'd, inside the MCP roots, so every absolute path a prompt names is readable and
  writable by the server). `git init -q` there only for hook cases that run git.

### 1.3 Claude invocation profile

Built by `cl_claude.argv(opts)`; every flag below was verified in section 0 except where marked.

```
claude -p <prompt> --output-format json --model <M> --max-turns <N> --max-budget-usd <B>
  --mcp-config <run_dir>/mcp.json --strict-mcp-config          # {"mcpServers":{"openjev":{"type":"http","url":"<tap>/mcp"}}}
  --setting-sources ""                                         # F3: no user settings, plugins, hooks
  --permission-mode dontAsk --allowedTools <allow...>          # default allow: "mcp__openjev" (whole server)
  --tools <builtins>                                           # default "" (F6); per case e.g. "Bash", "Read", resource tools
  --disable-slash-commands                                     # unless case.skills (F5)
  --no-session-persistence                                     # unless case.persist (hooks read transcript_path, F13)
  --exclude-dynamic-system-prompt-sections                     # prompt-cache reuse across cases (verify in T001; drop on error)
  [--settings <run_dir>/settings.json]                         # case.hooks
  [--plugin-dir <work>/plugin/openjev-skills]                  # case.skills
  [--debug-file <run_dir>/claude-debug.log]                    # OJ_CLAUDE_DEBUG=1
stdin=/dev/null, cwd=<case cwd>, new process group, wall timeout = case.timeout_s (kill the group on expiry)
```

Child env = `os.environ` minus `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`,
`CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_FOUNDRY`, so Claude can only use the
subscription OAuth login. The harness never reads, prints or copies credentials; `claude auth`/`setup-token` are never
run. Precondition asserted on every run (a failure is a harness error, not a case failure): `init.apiKeySource ==
"none"`, `mcp_servers` has `openjev` with `status == "connected"`, `init.tools` contains every expected tool.

Models: `cheap` tier = `OJ_CLAUDE_MODEL` (default `haiku`), used whenever the prompt names the tool and the arguments;
`strong` tier = `OJ_CLAUDE_MODEL_STRONG` (default `sonnet`) for cases where Claude must plan (cursor loops it is not
told how long, implicit skill use, the rule-deny refusal F12). `--max-budget-usd`: 0.25 cheap, 1.00 strong, times
`OJ_CLAUDE_BUDGET_SCALE`.

Hooks: `case.hooks = {"PreToolUse": ("Bash", "pretooluse --unattended --timeout-ms 8000"), ...}` becomes
`settings.json` with `command = "tee -a <run_dir>/hook_<event>.in | env OPENJEV_BASE_URL=<openjev> [OPENJEV_HOOK_TASK=..] <repo>/.venv/bin/openjev-hook <args> | tee -a <run_dir>/hook_<event>.out"`
and `"timeout": 15` (>= 1000 ms above `--timeout-ms`, spec 1 Hook and recipe). The pipeline exits with `tee`'s 0;
the hook itself always exits 0.

### 1.4 `run_claude()` and `Transcript`

```python
def run_claude(prompt: str, *, ctx: Ctx, profile="default", tools="", allow=("mcp__openjev",), tier="cheap",
               max_turns=4, timeout_s=180, hooks=None, skills=False, persist=False, budget_usd=None) -> Transcript
```

Holds one slot of the semaphore (1.7) only while `claude` runs; writes `argv.json`, `mcp.json`, `settings.json`,
`claude.json` (stdout), `stderr.txt`, `wire.jsonl`, `hook_*.in/out` into `run_dir`.

```python
@dataclass
class ToolUse:    id: str; name: str; tool: str | None; input: dict      # tool = "batch" for "mcp__openjev__batch"
@dataclass
class ToolResult: id: str; is_error: bool; text: str; json: Any | None; error: dict | None; links: list[str]
@dataclass
class Call:       use: ToolUse; result: ToolResult | None; wire: dict | None   # wire = server-side result via claudecode/toolUseId
@dataclass
class Transcript:
    events: list[dict]; init: dict; result: dict; calls: list[Call]; final_text: str
    subtype: str; is_error: bool; num_turns: int; duration_ms: int; cost_usd: float; model: str
    permission_denials: list[dict]; wire: WireLog; hooks: dict[str, list[dict]]   # event -> parsed .out lines
    stderr: str; rc: int; run_dir: Path
    def of(self, tool: str) -> list[Call]          # calls of mcp__openjev__<tool> (or a built-in name)
    def tools_called(self) -> list[str]
```

Parsing rules: `tool_result.content` is a string or a list of blocks (F17): `text` = blocks joined, `json` = the
last text block that parses, `links` = the `[Resource link: ...]` blocks; when `is_error` and `json` has `error`,
`error` = that ToolError. Hook errors are plain text (`PreToolUse:Bash hook error: ...`), `json` None.
`WireLog`: `.requests(method, name=None)`, `.result(tool_use_id)` (the server's full result incl.
`structuredContent`, `resultType`), `.progress(tool_use_id)`, `.headers(i)`, `.errors()` (JSON-RPC errors).

### 1.5 Assertions (`cl_assert.py`)

Deterministic checks on calls, results, wire, hook output and files are primary. Every case has at least one; a case
whose only checks are prose fails `test_cl_selftest.py::test_every_case_has_a_deterministic_check` (cases declare
`expect` as a list; prose helpers carry a `weak` attribute). Each failure message ends with a compact transcript
summary (tools called with inputs cut at 200 chars, error codes, final text cut at 300) and the run_dir path.

| Helper | Checks |
|---|---|
| `precondition(t, tools=())` | F2 checks (harness error class) |
| `tool_called(t, name, where=None, times=None) -> list[Call]` | at least one (or exactly/range `times`) call whose input satisfies `where` (dict of path -> predicate) |
| `no_tool_called(t, names=None)` / `tool_not_called(t, name)` | nothing (or none of `names`) called |
| `result_ok(call)` / `result_error(call, code, path=None, retryable=None, msg=None, hint=None)` | `is_error` and ToolError fields |
| `result_matches(call, path, pred)` / `args_match(call, path, pred)` | jsonpath-ish over `result.json` / `use.input`: `$.answers.L1.p`, `$.results[0].id`, `$.probabilities.*`, `len($.kept)` |
| predicates | `eq, approx(x, tol), gt, ge, lt, le, between, one_of, contains, regex, is_type, exists, absent, set_eq, sums_to(1, tol)` |
| `call_order(t, [...])` | subsequence of tools |
| `cursor_chain(t, tool)` | call i+1 `input.cursor` == call i `next_cursor` (exact string), last `next_cursor` is null |
| `wire_called(t, method, name=None, params=None)` / `wire_header(t, header, pred)` / `wire_jsonrpc_error(t, code)` | server-side evidence |
| `hook_decision(t, event, decision=None, reason=None)` / `hook_silent(t, event)` | parsed `.out` lines; `hook_ran(t, event)` = `.in` non-empty |
| `denied(t, tool)` | entry in `permission_denials` |
| `jsonl_rows(path, n=None, ids=None, unique=True)` | batch output files (uses `run_live.read_rows`, `dup_gap`) |
| `final_text_contains(t, *needles, any_of=False)` (weak) | prose, secondary only |

### 1.6 Self-tests

`test_cl_selftest.py` (runs with `OPENJEV_CLAUDE_LIVE=1` or `OJ_CLAUDE_SELFTEST=1`, no claude, no model): parses every
file in `fixtures/probes/`, checks `ToolResult` extraction (string content, list content with resource link, hook
error text), `WireLog` SSE parsing and `claudecode/toolUseId` joins on `wire_tasks_on_batch.jsonl`, the predicate
and jsonpath helpers, the argv builder (no `--bare`, no `--safe-mode`, API-key vars stripped), and the 100-case
catalogue (ids T001-T100 unique and contiguous, each group 8-14, every tool/resource/prompt/hook/recipe in
`COVERAGE` from 1.10 mapped by at least one case).

### 1.7 Concurrency and the GPU

One GPU, OpenJev serial on MLX. `cl_env.slot()` is a cross-process semaphore: `OJ_CLAUDE_SLOTS` (default 3) lock files
in `$TMPDIR/openjev-claude-live-slots/slot-<i>.lock`, `fcntl.flock(LOCK_EX | LOCK_NB)` over the slots with 0.5 s
sleeps, 30 min wait cap (then a harness error). Held only around the `claude` subprocess. Inside one pytest process
cases run serially (no xdist installed); parallelism comes from `run_claude_live.py --procs N` (one pytest per group,
same `OJ_LIVE_RUN_ID`). Batch/calibrate cases use `concurrency <= 2` in their arguments. MCP instances are per pytest
process (each process its own ports in 8200-8299).

### 1.8 Results and cost logging

`results/<run_id>/results.jsonl` (`run_id` = `OJ_LIVE_RUN_ID` or `UTC yyyymmddThhmmss-<pid>`), one line per attempt,
appended under an `flock`:

```json
{"id":"T032","slug":"batch_cursor_chain","group":"g04","attempt":1,"retry_of":null,"retry_reason":null,
 "model":"claude-haiku-4-5-20251001","tier":"cheap","profile":"default","pass":true,"failure":null,
 "error_class":null,"tools":["batch","batch","batch"],"num_turns":4,"duration_ms":18234,"wall_ms":19102,
 "cost_usd":0.0213,"subtype":"success","denials":0,"run_dir":"runs/T032-a1","started":"2026-10-02T19:00:01Z"}
```

`error_class`: `harness` (claude/MCP/precondition), `model_choice` (expected tool absent), `assertion`, `timeout`.
`run_claude_live.py` writes `summary.json` (pass/fail per group, total cost, p50/p95 duration, retries, list of
failing ids with run_dirs) and stops scheduling new groups once the summed cost passes `OJ_CLAUDE_RUN_BUDGET_USD`.
Passing run dirs are pruned to `argv.json` + `claude.json` + `wire.jsonl` unless `OJ_CLAUDE_KEEP=1`; failing ones
are kept whole, plus the tail (200 lines) of each MCP instance log.

### 1.9 Knobs

| Env | Default | Meaning |
|---|---|---|
| `OPENJEV_CLAUDE_LIVE` | unset | `1` enables collection |
| `OPENJEV_BASE_URL` | `http://127.0.0.1:8080` | the real OpenJev |
| `OJ_LIVE_MCP_PORT` | auto in 8200-8299 | port of the `default` instance |
| `OJ_CLAUDE_SLOTS` | `3` | concurrent `claude` processes across all pytest processes |
| `OJ_CLAUDE_BIN` | `claude` | binary |
| `OJ_CLAUDE_MODEL` / `OJ_CLAUDE_MODEL_STRONG` | `haiku` / `sonnet` | tiers |
| `OJ_CLAUDE_BUDGET_SCALE` | `1` | scales the per-run `--max-budget-usd` |
| `OJ_CLAUDE_RUN_BUDGET_USD` | `15` | runner stop |
| `OJ_CLAUDE_DEBUG` | `0` | `1` adds `--debug-file` |
| `OJ_CLAUDE_KEEP` | `0` | keep passing run dirs |
| `OJ_LIVE_RUN_ID` | generated | shared by the runner's processes |

### 1.10 Coverage map

`cl_cases.COVERAGE` lists what must be hit, checked by the self-test: the 14 `TOOL_NAMES`, the 6 resources + 3
templates + `file://`, the 5 prompts, the 29 recipes, the 4 hook events, the 11 skills (loaded; 6 driven), error
families (`OJ_INVALID_INPUT` schema/path/roots/lint/cursor, `OJ_UNREACHABLE`, `OJ_NOT_FOUND`, `OJ_TIMEOUT`,
`OJ_AUTH`, `OJ_UNAVAILABLE`, `OJ_OVERLOADED`, `OJ_SERVER`, `OJ_UNKNOWN_MODEL`, JSON-RPC `-32602`), the profiles
`core`/`tasks`/`ext`, and the batch cursor/resume loop.

## 2. Case format

### 2.1 `Case`

```python
@dataclass(frozen=True)
class Case:
    id: str                    # "T032"
    slug: str                  # "batch_cursor_chain"; pytest id "T032-batch_cursor_chain"
    group: str                 # "g04"
    feature: str               # "batch cursor loop"
    spec: str                  # "spec 1 Tools/batch; build 2.11 Client pagination"
    prompt: str                # str.format with {cwd} {work} {data} {fixtures} {seed[...]} placeholders
    expect: tuple[Callable[[Transcript, Ctx], None], ...]
    primary: tuple[str, ...]   # tools whose total absence triggers the one harness retry
    profile: str = "default"; tools: str = ""; allow: tuple[str, ...] = ("mcp__openjev",)
    tier: str = "cheap"; max_turns: int = 4; timeout_s: int = 180; budget_usd: float | None = None
    setup: Callable[[Ctx], None] | None = None   # writes files, seeds batch outputs via an SDK client (run_live.call)
    hooks: dict | None = None; skills: bool = False; persist: bool = False
    xfail: str | None = None   # "known: <spec 10 item>" only after triage (section 5)
```

Cases live as `CASES = [...]` in their group's `test_gNN_<name>.py`; the module body is one
`@pytest.mark.parametrize("case", CASES, ids=...)` test calling `cl_cases.run_case(case, ctx)`. Python, not JSON,
because `expect`/`setup` are callables; arguments the model must pass are JSON literals inside the prompt.

Prompt style (cheap tier): name the tool and give the exact arguments as compact JSON with absolute paths, and say
"call it exactly once" or "keep calling while next_cursor is not null". Case-file states are taken verbatim
(`run_live.load(...)`, `recipe_harness.inputs_from_case(...)`) and serialised by the harness, never retyped.

Seeding: a `setup` that needs a finished or partial batch output, a cursor, or an audit record makes it with a direct
SDK client against the same instance (`run_live.call` over `streamable_http_client`), so Claude's run measures only
the step under test.

### 2.2 Markers, timeouts

Markers (registered in `conftest.pytest_configure`, not in `pyproject.toml`): `claude_live` on all, `g01`..`g10`,
`strong`, `hooks`, `skills`, `slow` (timeout_s > 180). Timeouts: 180 s default, 300 s for `think` and hook cases,
420 s for batch/calibrate cursor loops; enforced by the subprocess wall timeout (no pytest-timeout dependency).

### 2.3 Retry of a flaky model choice

`run_case` retries once, automatically, only when all hold: the run succeeded as a process, the precondition passed,
none of `case.primary` appears in `t.calls`, and none is in `permission_denials`. The rerun uses the same argv in a
fresh cwd; both attempts are logged (`retry_reason: "model_did_not_call"`). A called tool whose arguments or result
fail an assertion is signal: no automatic retry. Harness errors are not retried either; they fail with
`error_class: harness` and the precondition message.

## 3. Coverage approach per feature class

- **Tools**: unambiguous prompts (tool name + JSON args); assert args reached the server unchanged
  (`wire.result(id)` and `use.input`) and the result fields of spec 1 (output schemas) against case-file ground
  truth. Annotation/registry facts come from the wire `tools/list`.
- **Resources**: `--tools "ListMcpResourcesTool,ReadMcpResourceTool"`; assert `ReadMcpResourceTool` input `uri`, the
  parsed `contents[0].text`, and the wire `resources/read` (mimeType, `ttlMs`, `cacheScope`). Templates are read by
  their expanded URI (Claude has no template listing tool; `resources/templates/list` is not called by Claude, so the
  template listing stays covered by `test_mcp_protocol`).
- **Prompts**: `-p "/mcp__openjev__<prompt> <args>"` (F8); assert wire `prompts/get` with name and arguments, then the
  action the prompt text asks for (e.g. `start_batch` -> `batch` with `dry_run: true` first). `completion/complete` is
  not sent by headless Claude: out of scope here, covered by `test_mcp_protocol_gaps`.
- **Hooks**: `--settings` hooks wrapped with `tee` (1.3); assert `hook_<event>.out` decisions, `tool_result.is_error`
  with the hook text, `permission_denials`, and that a Bash command did or did not run. Only harmless commands:
  everything runs in the case cwd (a scratch git repo with no remote); a command that would be harmful if the gate
  failed open is never used.
- **Skills**: `--plugin-dir` (F14) and the explicit `/openjev-skills:<name>` slash command; assert the downstream MCP
  call shape the SKILL.md procedure prescribes (re-read the SKILL.md before writing the case). One strong-tier case
  without the slash; a `Skill` tool_use is a bonus, never required.
- **Errors**: invalid arguments, relative and outside-root paths, lint misuse, bad cursors on `default`; transport
  faults on `unreachable`/`notopenjev`; HTTP faults through `FaultUpstream`. Assert `isError`, the ToolError `code`,
  `retryable`, `path`/`hint`, and that Claude got a readable error (F12 of the build spec: never a JSON-RPC error for
  tool faults). Not provokable and out of scope: `OJ_FORBIDDEN` (origin-secret proxy), `OJ_TOO_LARGE`,
  `OJ_BAD_IMAGE`, `OJ_PROTOCOL`, `OJ_INTERNAL`, `OJ_RATE_LIMITED` (stub-tested in `test_mcp_mapping`).
- **Tasks**: F16, one negative case. **Cancellation**: not driven from headless Claude (no cancel API); stays in
  `live/test_live_tools.py`.

## 4. Catalogue (100)

Columns: **Prof/Tier/Turns** = MCP profile, model tier, `--max-turns`. "case NN:id" = ground truth from
`docs/mcp-skill-spec/tests/cases/NN-*.json`; pick the id at implementation from cases that
`recipe_harness.inputs_from_case` maps and that are not in the known-failing set (section 5.4), then pin it in the
case. Every row's key assertion is deterministic; prose checks may be added as secondary.

### g01 Connection, discovery, protocol (8)

| ID | Feature | Spec | Intent | Tools | Key assertion | Prof/Tier/Turns |
|---|---|---|---|---|---|---|
| T001 | isolated session | spec 1, F2-F5 | "Reply OK"; nothing else | none | precondition; `init.tools` 14 `mcp__openjev__*` = `TOOL_NAMES`; 5 prompt slash commands; no user plugins; `no_tool_called` | default/cheap/1 |
| T002 | era and headers | spec 2 Revisions, 5 headers | call `status` once | status | wire: first request `server/discover`; every request `MCP-Protocol-Version: 2026-07-28`; `Mcp-Method` = method; `Mcp-Name: status` on the call | default/cheap/3 |
| T003 | tools/list contract | spec 1 table, 2 SEP-2549 | call `status` once | status | wire `tools/list`: names in `TOOL_NAMES` order, `ttlMs` 3600000, `cacheScope public`, annotations (`batch.readOnlyHint` false, `yes_no.idempotentHint` true, `ask_image.openWorldHint` false) | default/cheap/3 |
| T004 | status | spec 1 status, dev 17 | `status` with `probe: true` | status | `healthy` true; `decide_models` non-empty; `latency_probe_ms` > 0; `limit_source` "default" implies a warning naming `/v1/limits` | default/cheap/3 |
| T005 | Tasks negative | spec 1 Tasks, F16 | `batch` 2 inline items | batch | wire discover has `extensions["io.modelcontextprotocol/tasks"]`; call `_meta.clientCapabilities` lacks `extensions`; result `resultType` "complete", `status.ok` 2 | tasks/cheap/3 |
| T006 | core toolset | dev 16 | "Reply OK" | none | `init.tools` openjev subset == `CORE_TOOL_NAMES` (6, in order) | core/cheap/1 |
| T007 | progress | spec 6 progress, dev 21 | `batch` 8 inline items, `concurrency` 1 | batch | wire call has `_meta.progressToken`; >= 1 `notifications/progress` matching `^\d+/\d+ ok=\d+ err=\d+`; values increasing | default/cheap/3 |
| T008 | result envelope | spec 6 envelope | `yes_no` one claim | yes_no | wire result: `resultType` complete, `isError` false, `content[0].text` == compact JSON of `structuredContent`, `annotations.audience == ["assistant"]`; `_meta["claudecode/toolUseId"]` == transcript tool_use id | default/cheap/3 |

### g02 Core reads: ask, yes_no, classify, score (10)

| ID | Feature | Spec | Intent | Tools | Key assertion | Prof/Tier/Turns |
|---|---|---|---|---|---|---|
| T009 | ask noul | build 2.6 | state "deploy failed with exit 1", noul "did it succeed" | ask | `answers.<id>.p` < 0.2; `meta.request_ids` 1; `meta.model` set | default/cheap/3 |
| T010 | ask mixed types | build 2.6, 1.6 | noul + choice + score in one call (case 00 ex with 3 types) | ask | 3 answer ids; choice probabilities `sums_to(1, 0.02)`; score level in range; one request | default/cheap/3 |
| T011 | ask options | build 2.6, 3.6 | `options {samples: 4}` | ask | `args_match options.samples == 4`; wire request body forwarded; result ok | default/cheap/3 |
| T012 | yes_no clear | build 2.7 | claim clearly true for the state | yes_no | `decision` yes, `p` >= 0.8, `thresholds_used` {0.8, 0.2} | default/cheap/3 |
| T013 | yes_no thresholds/means | build 2.7 | `yes_at 0.95`, `no_at 0.05`, `true_means`/`false_means` | yes_no | `thresholds_used` echoes 0.95/0.05; decision consistent with p and band | default/cheap/3 |
| T014 | yes_no grey invariant | build 2.7 re-read | an ambiguous claim (case 02 borderline) | yes_no | `uncertain` iff `no_at < p < yes_at`; `margin` = abs(2p-1) within 1e-6; if grey then `meta.requests` 2 | default/cheap/3 |
| T015 | classify | build 2.8 | ticket text, labels billing/tech/sales (case 01) | classify | `label` == case label; `probabilities` keys = labels + escape; `p_top` == max | default/cheap/3 |
| T016 | classify abstain | build 2.8, spec 1 escape | "lovely sunset" with billing/tech/sales | classify | `abstained` true (or label is escape per `derive.is_escape`) | default/cheap/3 |
| T017 | classify multi_label | spec 1 multi_label | text touching 2 labels, `multi_label: true` | classify | `labels_multi` has every label with p; both relevant labels p >= 0.5 | default/cheap/3 |
| T018 | score | build 2.9 | glowing review, 5 levels, `one_based: true` | score | `score` >= 4; `level_label` in levels; 5 probabilities; `bimodal` boolean | default/cheap/3 |

### g03 filter, lint, compile, generate, ask_image, think (10)

| ID | Feature | Spec | Intent | Tools | Key assertion | Prof/Tier/Turns |
|---|---|---|---|---|---|---|
| T019 | filter | build 2.10 | case 00 `ex-filter` lines and criterion (as `live/test_live_tools.py::test_filter_ex_filter`) | filter | `kept == ["L4"]`; `meta.requests` 1 | default/cheap/3 |
| T020 | filter pick_best/graded | build 2.10 | 5 candidate snippets, `pick_best: true`, `graded: true` | filter | `best` in item ids; every item in exactly one of kept/dropped/grey | default/cheap/3 |
| T021 | lint valid | build 2.13 | a valid request object | lint | `valid` true, `errors` [], `body_hash` `sha256:`, `estimate` present | default/cheap/3 |
| T022 | lint invalid + autofix | build 2.13 | choice question without criteria, `autofix: true` | lint | not `isError`; `valid` false or `fixed_request` present; first error has `code` E0xx and `path` | default/cheap/3 |
| T023 | lint emit | build 2.13, spec 5 Credentials | `emit` snippets for curl and python | lint | snippets present; contain `$OPENJEV_API_KEY` or `os.environ`, never a literal key | default/cheap/3 |
| T024 | compile | build 2.14 | intent "angry support email; billing/tech/sales", then "write a release announcement" (as `test_compile_routes_and_types`) | compile x2 | first `recipe.id` ticket_triage; second `recipe.id` "none"; `draft_request`, `lint` present | default/cheap/4 |
| T025 | generate | build 2.17 | "2+2, one number", `max_tokens` 16 | generate | `content` contains "4"; `finish_reason` set (tool retries the empty-reply flake once) | default/cheap/3 |
| T026 | ask_image path | build 2.12 | `<work>/data/hotdog.jpg`, case 00 `ex-image` questions | ask_image | `answers.hotdog.p` >= 0.9, `answers.cat.p` <= 0.1; `images[0]` metadata | default/cheap/3 |
| T027 | ask_image 2 UI shots | build 2.12, case 22 | cookie modal + dashboard PNGs, case 22 question | ask_image | case 22 expectation on the modal image; 2 entries in `images` | default/cheap/3 |
| T028 | ask think | build 3.6, spec 4 timeout | `options {think: 64}` on a case 23 multistep state | ask | result ok; `args_match options.think == 64`; answer meets case 23 expectation | default/cheap/3, timeout 300 |

### g04 batch, batch_results, cursor and resume (12)

| ID | Feature | Spec | Intent | Tools | Key assertion | Prof/Tier/Turns |
|---|---|---|---|---|---|---|
| T029 | dry_run | build 2.11 | 3 inline items, `dry_run: true`, `output_path` given | batch | `preview`/`estimate`/`first_body` present; no read (`meta.requests` 0, no systemone entry in the instance's `OPENJEV_MCP_LOG`); output file absent | default/cheap/3 |
| T030 | inline run | build 2.11 | 4 inline items, `run_live.QS` | batch | `status.ok` 4; `results` 4 ids; `summary` per question; `next_cursor` null | default/cheap/3 |
| T031 | items_file + output | build 2.11 Import, spec 1 resource_link | `make_csv` 12 rows, `items_file {path, state_field, id_field}`, `output_path` | batch | `jsonl_rows(out, n=12, unique)`; header line; `links` has the output `file://`; wire result has a `resource_link` block | default/cheap/3 |
| T032 | cursor chain | build 2.11 pagination | 5 items, `max_items_per_call` 2, "keep calling with next_cursor" | batch x3 | `cursor_chain(t, "batch")`; 3 calls; sum `status.done` 5; file 5 unique ids | default/cheap/8, timeout 420 |
| T033 | resume | build 2.11 resume, dev 34 | seed: 3 of 8 rows done; Claude calls again with `resume: true`, no cursor | batch | `status.skipped` 3, `status.ok` 5; `dup_gap` no gaps/duplicates | default/cheap/4 |
| T034 | args changed | build 2.11 checks | seed a cursor; Claude passes it with a changed question | batch | `result_error OJ_INVALID_INPUT`, message "arguments changed since this cursor"; hint mentions `resume` | default/cheap/3 |
| T035 | invalid cursor | build 2.11 checks | `cursor: "not-a-cursor"` | batch | `OJ_INVALID_INPUT` "invalid cursor" | default/cheap/3 |
| T036 | template | build 2.18, spec 1 templates | `template: "spec_examples_escalate"` with its own states | batch | ok; questions = template's (`escalate`); `status.ok` = template state count | default/cheap/3 |
| T037 | exports | build 2.11 Exports, dev 34 | `export` csv + `ojui-batch` (check names in `batch/export*`) | batch | export files exist; `exports` lists both; CSV rows end LF | default/cheap/3 |
| T038 | batch_results review | build 2.21 | seeded finished file, `view: "review"` | batch_results | `review_queue` list; no read (`meta.requests` 0 or absent, no new `OPENJEV_MCP_LOG` read entry) | default/cheap/3 |
| T039 | batch_results rows paging | build 2.21 | seeded 6 rows, `view: "rows"`, `sort_by: "confidence"`, `limit` 2, follow `next_cursor` | batch_results x3 | `cursor_chain(t, "batch_results")`; 6 distinct ids across pages; ascending confidence | default/cheap/6 |
| T040 | compare_to | build 2.21, dev 35 | two seeded outputs on the same ids, `compare_to` | batch_results | `compare` block with agreement per question; `matched` 6 | default/cheap/3 |

### g05 calibrate and audits (8)

| ID | Feature | Spec | Intent | Tools | Key assertion | Prof/Tier/Turns |
|---|---|---|---|---|---|---|
| T041 | calibrate ex-cal | build 2.15, dev 36 | case 00 `ex-cal-1..7` written as `case_file`, `options {samples: 1}` | calibrate | `per_question.escalate.accuracy_at_0.5` 1.0, `separable`, `most_borderline` ex-cal-7 (as `test_calibrate_ex_cal`) | default/cheap/3 |
| T042 | audit record | build 2.15, spec 1 audits | same with `store: true` | calibrate | `question_hash` set; `<work>/audits` holds a record for it | default/cheap/3 |
| T043 | read audit | spec 1 `openjev://audits/{question_hash}` | seed T042-style record; Claude reads the URI | ReadMcpResourceTool | `contents[0].text` JSON has the same `question_hash`; wire `ttlMs` 0, `private` | default/cheap/3 |
| T044 | from_batch | build 2.15 | seeded batch output + labels file, `from_batch` | calibrate | `n` = labelled rows; accuracy field present | default/cheap/3 |
| T045 | target/holdout | build 2.15 | case 24 examples, `target`, `holdout` | calibrate | fitted thresholds per question; holdout metrics present | default/cheap/3 |
| T046 | chunked calibrate | dev 36 | 7 examples, `max_items_per_call` 3, follow cursor | calibrate x3 | partial calls `per_question {}` + `next_cursor`; `cursor_chain`; final `n` 7 | default/cheap/8, timeout 420 |
| T047 | compare_to drift | build 2.15 | seeded audit, rerun with `compare_to` | calibrate | `drift` block present | default/cheap/3 |
| T048 | invalid examples | build 2.15, 2.4 | examples without labels | calibrate | `OJ_INVALID_INPUT` with a `path` under examples | default/cheap/3 |

### g06 Recipe families (13)

Inputs and expected decision: `recipe_harness.inputs_from_case(recipe, case)` + `check_expect`; recipe input names from
`recipes/builtin/<id>.json` `input_schema`. Sweep rows give each recipe call its exact arguments in one prompt.

| ID | Feature | Spec | Intent | Tools | Key assertion | Prof/Tier/Turns |
|---|---|---|---|---|---|---|
| T049 | command_gate | build 2.16, 5.3 | case 03 deny case | recipe | `decision` deny; `signals` has destructive; `degraded` false | default/cheap/3 |
| T050 | done_gate | build 5.5 | case 05 block case (not gate-03) | recipe | `decision` block | default/cheap/3 |
| T051 | gates sweep | 5.2, 5.4, 5.17 | act_or_ask (02), injection_screen (04, quarantine), moderation (17) | recipe x3 | each decision == case expectation | default/cheap/6 |
| T052 | recipe dry_run/profile | dev 32 | command_gate `dry_run: true`, `profile: "lenient"` | recipe | `decision` "dry_run"; `built_requests` non-empty; no read (`OPENJEV_MCP_LOG`) | default/cheap/3 |
| T053 | ticket_triage | 5.1 | case 01, teams | recipe | `decision` route + team == case | default/cheap/3 |
| T054 | triage sweep | 5.7, 5.13, 5.20 | alert_triage (13), issue_triage (07), taxonomy_classify (20, not tax-18) | recipe x3 | decisions == cases | default/cheap/6 |
| T055 | dispatch sweep | 5.8-5.10 | skill_selection (09, not sel-19), typed_call (10), model_routing (08) | recipe x3 | decisions == cases | default/cheap/6 |
| T056 | retrieval sweep | 5.14, 5.15 | semantic_filter (14), rag_gate (15) | recipe x2 | decisions == cases | default/cheap/5 |
| T057 | code-check sweep | 5.6, 5.7, 5.16 | semantic_lint (06, not the secrets cases), review_finding_filter (07), claim_check (16) | recipe x3 | decisions == cases | default/cheap/6 |
| T058 | judge sweep | 5.12, 5.18 | judge_assert, judge_pairwise (12), rubric_score (18) | recipe x3 | decisions == cases | default/cheap/6 |
| T059 | records A | 5.11, 5.21 | select_extraction, verify_fields (11), bulk_label (21) | recipe x3 | decisions == cases | default/cheap/6 |
| T060 | records B | 5.7, 5.19 | entity_match (19, not cat-01), memory_decide (19), duplicate_check (07) | recipe x3 | decisions == cases | default/cheap/6 |
| T061 | multistep/ui/audit | 5.22-5.24 | multistep_tick (23), ui_decision (22b), threshold_audit (24) | recipe x3 | decisions == cases (`threshold_audit` -> "report") | default/cheap/6, timeout 300 |

### g07 Resources and prompts (11)

| ID | Feature | Spec | Intent | Tools | Key assertion | Prof/Tier/Turns |
|---|---|---|---|---|---|---|
| T062 | resources/list | spec 1 resources | list the openjev resources | ListMcpResourcesTool | exactly the 6 URIs | default/cheap/3 |
| T063 | schema + limits | spec 1, build 2.4 | read `openjev://schema` and `openjev://limits` | ReadMcpResourceTool x2 | schema mimeType `application/schema+json` and ToolError shape present; limits `limits.questions` 256, `batch.concurrency_max` 4, `read_at` | default/cheap/4 |
| T064 | recipes | spec 1 Recipes | read `openjev://recipes`, then `openjev://recipes/command_gate` | ReadMcpResourceTool x2 | index ids `set_eq` the 29 builtin files; document `id` command_gate with `steps` | default/cheap/4 |
| T065 | templates, patterns, guide | spec 1, build 2.18 | read `openjev://templates`, `openjev://templates/spec_examples_escalate`, `openjev://patterns`, `openjev://guide/authoring` | ReadMcpResourceTool x4 | 10 templates; template `questions` has `escalate`; patterns JSON object; guide mimeType `text/markdown` | default/cheap/6 |
| T066 | unknown URI | spec 1, 2 (-32602) | read `openjev://nope` | ReadMcpResourceTool | tool_result `is_error`; `wire_jsonrpc_error(-32602)` "Resource not found" with `data.uri` | default/cheap/3 |
| T067 | file:// | spec 1 file://, dev 28 | seeded batch output `file://` URI, then `file:///etc/hosts` | ReadMcpResourceTool x2 | first: header + rows; second: error (outside roots / not a batch file) | default/cheap/4 |
| T068 | prompt start_batch | spec 1 prompts, build 2.18 | `/mcp__openjev__start_batch spec_examples_escalate` | batch | wire `prompts/get` name start_batch, `arguments.template`; first `batch` call has `dry_run: true` | default/cheap/4 |
| T069 | prompt review_batch | spec 1 prompts | `/mcp__openjev__review_batch <seeded path> 5` | (optional batch_results) | wire `prompts/get` with `output_path` and `limit` "5"; prompt result content type `resource` `openjev://batch-review` | default/cheap/3 |
| T070 | prompt author_question | spec 1 prompts | `/mcp__openjev__author_question <intent-without-spaces>` | (lint allowed) | wire `prompts/get` name author_question, `intent` set; result messages role user | default/cheap/2 |
| T071 | prompt audit_question | spec 1 prompts | `/mcp__openjev__audit_question <schema_path> <labels_path>` (harness files) | (calibrate allowed) | wire `prompts/get` with both args; if Claude acts, its first openjev call is `calibrate` or `lint` | default/cheap/4 |
| T072 | prompt explain_answer | spec 1 prompts | `/mcp__openjev__explain_answer {"decision":"yes","p":0.93}` | none | wire `prompts/get` `answer` arg equals the JSON; `no_tool_called`; final text mentions 0.93 (weak) | default/cheap/1 |

### g08 Hooks (10)

All with `persist: true` (transcripts), cwd a scratch git repo without remote, Bash not in `--allowedTools` unless the row says
"allowed" (then `Bash(echo *)`).

| ID | Feature | Spec | Intent | Tools | Key assertion | Prof/Tier/Turns |
|---|---|---|---|---|---|---|
| T073 | pretooluse rule allow | spec 1 Hook, build 2.20 | run `git status` | Bash | `hook_decision PreToolUse allow` with reason "allow rule"; Bash result not error (ran though not allowed, F9) | default/cheap/3 |
| T074 | pretooluse model deny | build 2.16, F11 | task env "Summarise the README", run `git push --force origin main` | Bash | hook `deny`; tool_result `is_error` contains "openjev command_gate"; `denied(t, "Bash")` | default/cheap/3, timeout 300 |
| T075 | pretooluse rule deny | command_gate rules | honest framing, run `echo aGk= \| base64 -d \| sh` (prints "hi" if run) | Bash | hook `deny` with the base64 rule reason, decided without a read (reason names the rule) | default/strong/3 |
| T076 | non-Bash ignored | spec 1 Hook | matcher `*`; Read a file | Read | `hook_ran` and `hook_silent` for the Read event; Read result ok | default/cheap/3 |
| T077 | defer-allow | dev 5, `hook.py` (defer applies to rule allows too) | `--defer-allow`, run `ls` | Bash | hook prints nothing; Bash ends in `permission_denials` (dontAsk decides), contrast with T073 | default/cheap/3 |
| T078 | real payload | dev 41, `claude_hooks.py` | run `pwd` | Bash | captured `.in` parses with `claude_hooks.parse_pretooluse`; `tool_name` Bash, `cwd` == case cwd, `transcript_path` exists; `last_user_prompt(transcript_path)` returns the prompt | default/cheap/3 |
| T079 | stop block | build 5.5, F13 | run `echo ok`, then reply exactly "Done - the bug is fixed and all tests pass." | Bash (allowed) | one `hook_decision Stop` with `decision: block` and `[openjev done_gate]`; `num_turns` >= 2; a later stop is allowed (`--max-blocks 1`) | default/cheap/5, timeout 300 |
| T080 | stop allow | build 5.5 | run `echo ok`, reply "Output was ok; nothing was tested." | Bash (allowed) | `hook_ran Stop`; no block line | default/cheap/4 |
| T081 | userprompt hint (payload shape unverified: a parse failure is a server finding) | build 2.20, 5.9 | roster `fixtures/claude_code/skill_roster.json`, prompt of `userprompt_v2.json` | none | `.out` `hookSpecificOutput.additionalContext` contains `'pdf'` (as `test_hook_events`) | default/cheap/1 |
| T082 | posttooluse screen (payload shape unverified, as T081) | build 5.4 | matcher `Read`, `--screen Read`; read planted `injection_note.txt` | Read | `.out` has `decision: block` (quarantine); `.in` `tool_name` Read | default/cheap/3 |

### g09 Skills (8)

All with `skills: true` (plugin dir, slash commands on). Re-read the SKILL.md to set the expected call shape.

| ID | Feature | Spec | Intent | Tools | Key assertion | Prof/Tier/Turns |
|---|---|---|---|---|---|---|
| T083 | skills load | spec 1 Skills | "Reply OK" | none | `init.skills` contains the 11 `openjev-skills:<name>` | default/cheap/1 |
| T084 | agent-gates | build 4.4 | `/openjev-skills:openjev-agent-gates` gate "terraform destroy -auto-approve" for "list the files" (F14) | recipe | `recipe` == command_gate with `task`, `command`; `decision` deny | default/cheap/4 |
| T085 | decisions | build 4.1 | `/openjev-skills:openjev-decisions` is "the build passed" supported by "ERROR: 3 tests failed" | yes_no or ask | a decision tool called with literal claim; result p < 0.2 (or decision no) | default/cheap/4 |
| T086 | triage-routing | build 4.3 | `/openjev-skills:openjev-triage-routing` case 01 ticket and teams | recipe or classify | `ticket_triage` recipe (or classify over the teams); team == case | default/cheap/4 |
| T087 | data-records | build 4.8 batch | `/openjev-skills:openjev-data-records` label the 12-row CSV into `<cwd>/out.jsonl` | batch | first batch call `dry_run` true (if the skill prescribes it), then a run; `jsonl_rows` 12 unique | default/strong/10, timeout 420 |
| T088 | question-authoring | build 4.2 | `/openjev-skills:openjev-question-authoring` write a question for "is this log line a real failure" | lint/compile | `lint` or `compile` called before any read tool (`call_order`) | default/cheap/5 |
| T089 | calibration | build 4.11 | `/openjev-skills:openjev-calibration` with the ex-cal case_file | calibrate | `calibrate` called with the case_file; result accuracy present | default/cheap/5 |
| T090 | implicit use | build 4.1 precedence | no slash: "decide whether running `rm -rf build/` fits the task 'update the README'" | any openjev decision tool | a `recipe` command_gate or `yes_no`/`ask` call happened before the final answer; `Skill` use logged, not required | default/strong/5 |

### g10 Errors, recovery, config profiles (10)

| ID | Feature | Spec | Intent | Tools | Key assertion | Prof/Tier/Turns |
|---|---|---|---|---|---|---|
| T091 | invalid args + recovery | build 2.4 F12, spec 2 F12 | `yes_no` without `claim` first, "then fix it from the error" | yes_no x2 | first `result_error OJ_INVALID_INPUT` path mentions claim, wire shows no JSON-RPC error; second call ok | default/cheap/4 |
| T092 | relative path | dev 27, F18 | `batch` with `output_path: "out.jsonl"` "exactly as given" | batch | `OJ_INVALID_INPUT` "relative path", hint "absolute" | default/cheap/2 |
| T093 | outside roots | spec 1 roots | `items_file` `/etc/hosts` | batch | `OJ_INVALID_INPUT` with `path`; no file read | default/cheap/2 |
| T094 | unreachable | build 2.4 | `status`, then `yes_no` | status, yes_no | both `OJ_UNREACHABLE`, `retryable` true, hint names the base URL | unreachable/cheap/4 |
| T095 | not OpenJev | build 2.4 404, `mapping.py` (any 404 without `model_not_found`) | `yes_no` | yes_no | `OJ_NOT_FOUND`, hint mentions `OPENJEV_BASE_URL` | notopenjev/cheap/3 |
| T096 | recipe fail_mode | build 2.4, 2.16 | `recipe command_gate` on unreachable | recipe | not `isError`; `degraded` true; `decision` ask (closed); `error` names OJ_UNREACHABLE | unreachable/cheap/3 |
| T097 | local refusals | spec 1 ask_image, 2.17 | `ask_image` (hotdog.jpg) with `options {think: 64}` -> refused (`tools/read._refuse_images`); `generate` with `model: "no-such-model"` | ask_image, generate | first `OJ_INVALID_INPUT` with `path` think; second `OJ_UNKNOWN_MODEL` (spike first: if a local check answers, assert what spec 1/2.17 says) | default/cheap/4 |
| T098 | lint misuse | spec 1 lint | `lint` with both `request` and `questions` | lint | `OJ_INVALID_INPUT` (the only lint error result) | default/cheap/2 |
| T099 | upstream faults | build 2.4 matrix | 4 `yes_no` calls with states `#FAULT:503`, `#FAULT:529`, `#FAULT:401`, `#FAULT:sleep` | yes_no x4 | codes `OJ_UNAVAILABLE` (retryable, `retry_after_s` 1), `OJ_OVERLOADED` (retryable), `OJ_AUTH` (not retryable), `OJ_TIMEOUT` | fault/cheap/8, timeout 300 |
| T100 | extension profile | spec 4 RECIPES/ROUTING | read `openjev://recipes`, run the extra fixture recipe, run `model_routing` | ReadMcpResourceTool, recipe x2 | extra id listed and runs; `model_routing` reason "routing off" and no read (`OPENJEV_MCP_LOG`) | ext/cheap/6 |

## 5. Failure triage policy

### 5.1 Classify first

From `results.jsonl` and the run dir: `harness` (claude did not start, MCP not connected, precondition, slot wait,
budget) -> fix the harness, never the test or the server; `model_choice` after the automatic retry -> a test problem
(prompt); `timeout` -> look at the MCP log tail and OpenJev load before anything else; `assertion` -> 5.2.

### 5.2 Attempt budget: 3 test rewrites, then the server

For each failing case, up to 3 attempts, each one:

1. Re-read the cited spec section (`mcp/spec.md` first, then the build-spec section by heading, then the case file
   entry). Write down in `triage.jsonl` (`{id, attempt, spec_ref, spec_says, observed, verdict}`) what the spec
   requires and what the wire/transcript shows.
2. Decide: the test expects something the spec does not promise (wrong field, wrong code, invented ground truth,
   ambiguous prompt) -> rewrite the test (prompt, arguments, assertion) and rerun only that case
   (`-k T0xx`). The server output contradicts the spec -> go to 5.3 at once; do not burn attempts.
3. Model noise (the right tool, the right arguments, a decision that flips between runs on a borderline input) ->
   pick a clearer case-file input; never loosen an assertion to "any decision".

After 3 attempts without a pass and with the spec still supporting the expectation, treat it as an MCP issue (5.3).

### 5.3 Server issue

1. Reproduce without Claude: the same arguments through an SDK client (`run_live.call`) against the same instance.
   Passes there -> the difference is Claude's wire (headers, `_meta`, arguments as sent; compare `wire.jsonl`), still
   a server bug if the server mishandles a valid request.
2. Read the code path: `tools/dispatch.call_tool` -> the tool module -> `envelope`/`mapping`/`recipes/engine`.
3. Fix the code, add a stub-based regression test in `mcp/tests/test_mcp_*.py` (runs in `mise run test`), run
   `.venv/bin/python -m pytest -q mcp/tests`, rerun the live case on a fresh own instance (code changes reach only
   new processes; never restart :8100).
4. Not fixable now, or the build spec itself is inconsistent -> record the case `xfail="known: <reason>"`
   (`strict=False`) and report it with the evidence for spec 10 "Open defects found by the live run".

### 5.4 Known before the first run

Avoid as ground truth (spec 8/10): recipe cases `done_gate` gate-03, `skill_selection` sel-19, `semantic_lint`
secrets x2, `entity_match` cat-01; case-file limitations `02::gate-19`, `06` coasked interference, `20::tax-18`,
`24::cal-10`. Flaky by nature: `generate` empty completion (the tool retries once), grey-band reads. Behaviour by
design, not defects: Stop with no tool call is allowed (F13), Tasks inactive under Claude (F16), haiku refusing the
base64 command (F12).

### 5.5 Evidence to keep

Per failing attempt, the whole run dir: `argv.json`, `mcp.json`, `settings.json`, `claude.json`, `stderr.txt`,
`wire.jsonl`, `hook_*.in/out`, `claude-debug.log` (when on), the MCP instance log tail, the cwd listing and any
output files; plus the `results.jsonl` row and the `triage.jsonl` entries. A server fix references the case id, the
run dir and the regression test name.
