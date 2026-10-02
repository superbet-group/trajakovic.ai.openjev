# claude_live: tasks

Work packages for the suite designed in `ARCHITECTURE.md` (read it first; "A n" below = its section n). This file adds
what the architecture leaves open: the frozen harness API every package codes against (section 2), the per-group case
data files, ownership, and the triage policy. Where the two disagree, this file wins (decisions in section 1).

```
H1 (harness core) ──┬── g01 g02 g03 g04 g05 g06 g07        (H1 only)
                    └── H2 (hooks, skills plugin, faults) ── g08 g09 g10
all groups ── triage phase (server fixes, outside these packages) ── F1 (mise task, docs, full run, RESULTS.md)
```

Run: `OPENJEV_CLAUDE_LIVE=1 .venv/bin/python -m pytest mcp/tests/claude_live/<file> -q` (from the repo root; `-k T032`
for one case). Rules for every package: no git commit/checkout/stash/reset; never start, stop or restart OpenJev :8080
or the user's MCP :8100 (no `mise run start/stop/restart`, no `pkill`); never set or ask for an API key, never read
credentials; stay inside the files you own; terse repo-style code; read the 410 KB build spec only by the line ranges
given; iterate with `-k`, run the whole file once at the end.

## 1. Decisions on top of the architecture

| # | Decision |
|---|---|
| D1 | Case data lives in `cases/gNN_<name>.json` (one per group, owned by the group); `expect`/`setup` callables live in `test_gNN_<name>.py`. Loader `cl_cases.case_from_data(path, tid, expect=..., setup=..., **overrides)` (2.3). |
| D2 | Prompts are rendered by regex over a fixed placeholder set, never `str.format` (prompts embed JSON): `{cwd} {work} {data} {fixtures} {repo} {args} {arg:KEY} {seed:KEY}`. Unknown `{...}` text is left as is. |
| D3 | `Case` gains `covers: tuple[str, ...]` (coverage keys, 2.5) and `pins: dict` (case-file ids used as ground truth, e.g. `{"01": "tri-03"}`). |
| D4 | `cl_assert.weak(fn)` marks a prose-only expectation (`fn.weak = True`); every case needs one non-weak `expect` entry. |
| D5 | `Transcript.audit`: the `OPENJEV_MCP_LOG` lines the instance appended during this run (offset taken before `claude` starts). "No read" = no entry for a read in `t.audit` (inspect `audit.py` for the record shape) plus `meta.requests == 0` when the result has `meta`. |
| D6 | Hooks, the skills plugin and `FaultUpstream` are H2 (`cl_hooks.py`, `cl_fault.py`). H1 only exposes the seam: `run_claude(settings=Path, plugin_dir=Path)`, and `run_case` imports `cl_hooks` lazily when `case.hooks` or `case.skills` is set; profile `fault` imports `cl_fault` lazily. |
| D7 | COVERAGE needs `OJ_SERVER`: T099 gets a fifth call with state `#FAULT:500` -> `OJ_SERVER`, `max_turns` 10. |
| D8 | The catalogue self-test is split: per-group checks always run for the group files present; the global check (T001-T100 contiguous, 10 groups, COVERAGE complete) skips with the list of missing groups until all 10 exist. F1 makes it pass. |
| D9 | claude_live tests carry the `claude_live` marker, never `live` (`mcp/tests/conftest.py` skips `live` unless `OPENJEV_LIVE=1`). |
| D10 | Parallel safety: `OJ_LIVE_MCP_PORT` pins only the default instance of one process; the runner never exports it to its children; every process bind-tests ports in 8200-8299 and retries on exit code 3. The slot lock dir is shared by all processes. |
| D11 | Triage logs: `results/triage-gNN.jsonl` (gitignored, one per group). New server suspects are left failing (no `xfail`); `xfail="known: ..."` only for items in A 5.4 or `mcp/spec.md` 10 "Open defects". |
| D12 | The harness smoke tests (`test_cl_smoke.py`, `test_cl_smoke_hooks.py`) are not catalogue cases and are not counted in the 100. |

## 2. Frozen harness API (H1 implements exactly these names, groups import exactly these names)

### 2.1 `cl_env.py`

```python
REPO: Path; HERE: Path                       # repo root; mcp/tests/claude_live
FIXTURES = HERE / "fixtures"; CASES_DIR = HERE / "cases"; RESULTS = HERE / "results"
BASE_URL: str                                # OPENJEV_BASE_URL or http://127.0.0.1:8080
CLAUDE_BIN, MODEL_CHEAP, MODEL_STRONG, SLOTS, BUDGET_SCALE, RUN_BUDGET_USD, DEBUG, KEEP  # A 1.9 knobs
def run_id() -> str                          # OJ_LIVE_RUN_ID or UTC yyyymmddThhmmss-<pid>, cached
def free_port(lo=8200, hi=8299, skip=()) -> int
@contextmanager
def slot() -> Iterator[int]                  # A 1.7 flock semaphore, $TMPDIR/openjev-claude-live-slots
def strip_env(env: dict) -> dict             # drops ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL, CLAUDE_CODE_USE_{BEDROCK,VERTEX,FOUNDRY}
```

### 2.2 `cl_servers.py`

```python
@dataclass
class McpInstance:
    profile: str; port: int; url: str        # url = http://127.0.0.1:<port>/mcp
    root_url: str                            # http://127.0.0.1:<port>
    log: Path                                # OPENJEV_MCP_LOG of this instance
    audit_dir: Path; stdout_log: Path; proc: subprocess.Popen
    def audit_mark(self) -> int              # byte offset of log
    def audit_since(self, mark: int) -> list[dict]
    def tail(self, n=200) -> str             # stdout/stderr log tail for failing run dirs
    def stop(self) -> None                   # terminate, 5 s, kill: only this Popen
class Instances:                             # session object behind the `mcp` fixture
    def get(self, profile: str = "default") -> McpInstance   # lazy, profiles of A 1.2
    def stop_all(self) -> None
PROFILES: dict[str, Callable[[Instances], dict]]   # profile -> extra env; "fault" lazily uses cl_fault (H2)
class WireTap:                               # A 1.2; per claude run
    def __init__(self, upstream: str, out: Path); url: str   # url ends with /mcp
    def __enter__ / __exit__
```

### 2.3 `cl_cases.py`

```python
@dataclass(frozen=True)
class Case:                                  # A 2.1 fields, plus:
    covers: tuple[str, ...] = (); pins: dict = field(default_factory=dict); args: Any = None; cli: tuple[str, ...] = ()
    # args: JSON value from the data file, rendered into {args}/{arg:KEY}; cli: extra claude argv (rare)
    # hooks: dict event -> {"matcher": str, "args": str, "env": {..}}  (consumed by cl_hooks, H2)
@dataclass
class Ctx:
    case: Case; attempt: int; run_id: str
    work: Path; cwd: Path; run_dir: Path; data: Path; fixtures: Path; repo: Path; base_url: str
    seed: dict                               # setup() writes here; {seed:KEY} placeholders
    instances: Instances
    def mcp(self, profile: str = "default") -> McpInstance
    def call_tool(self, tool: str, args: dict, profile="default", timeout=300) -> dict
        # sync SDK call (run_live.call over streamable_http_client, no WireTap): {"is_error", "structured", "text", "content"}
    def read_resource(self, uri: str, profile="default") -> dict   # {"contents": [...]}, raises on JSON-RPC error
    def render(self, s: str) -> str          # D2
def case_from_data(path: Path, tid: str, *, expect, setup=None, **overrides) -> Case
def load_group(path: Path) -> dict           # the JSON file
def run_case(case: Case, ctx_factory) -> Transcript   # retry rule A 2.3, results row A 1.8, raises AssertionError with summary
def load_catalogue() -> dict[str, list[Case]]   # imports every test_g*.py by path, reads CASES
COVERAGE: frozenset[str]                     # 2.5, built from code where possible
GROUPS: dict[str, tuple[str, int, int]]      # "g01": ("protocol", 1, 8), ... (section 3 ranges)
```

Data file schema (`cases/gNN_<name>.json`): `{"group": "gNN", "cases": {"T0xx": {"slug", "feature", "spec", "prompt",
"args"?, "primary": [..], "covers": [..], "pins"?: {}, "profile"?, "tools"?, "allow"?: [..], "tier"?, "max_turns"?,
"timeout_s"?, "budget_usd"?, "hooks"?, "skills"?, "persist"?, "cli"?, "xfail"?}}}`. Omitted keys take the `Case`
defaults. Test file pattern:

```python
import pytest, cl_assert as A
from cl_cases import case_from_data, run_case
from cl_env import CASES_DIR
DATA = CASES_DIR / "g04_batch.json"
def _t030(t, ctx): ...
CASES = [case_from_data(DATA, "T030", expect=(_t030,)), ...]
@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.id}-{c.slug}")
def test_case(case, case_ctx): run_case(case, case_ctx)
```

`case_ctx` (conftest, function scope) is the `ctx_factory`: `case_ctx(case, attempt) -> Ctx` (fresh cwd/run_dir per
attempt). conftest applies markers `claude_live`, the group marker, `strong`/`hooks`/`skills`/`slow` from the case.

### 2.4 `cl_claude.py` and `cl_assert.py`

```python
@dataclass
class ClaudeOpts: prompt, model, max_turns, budget_usd, mcp_url, tools, allow, skills, persist, settings, plugin_dir, cli, debug_file
def argv(o: ClaudeOpts, run_dir: Path) -> list[str]           # A 1.3, writes mcp.json
def run_claude(prompt, *, ctx, profile="default", tools="", allow=("mcp__openjev",), tier="cheap", max_turns=4,
               timeout_s=180, settings=None, plugin_dir=None, skills=False, persist=False, budget_usd=None, cli=()) -> Transcript
def parse(events: list, wire: "WireLog | None" = None, run_dir: Path | None = None) -> Transcript
class WireLog:   # loads tap jsonl AND the probe format (fixtures/probes/wire_*.jsonl: raw "req"/"resp" strings, SSE or JSON)
    entries: list[dict]
    def requests(self, method, name=None) -> list[dict]; def result(self, tool_use_id) -> dict | None
    def progress(self, tool_use_id) -> list[dict]; def headers(self, i) -> dict; def errors(self) -> list[dict]
    def response(self, method, name=None) -> list[dict]          # JSON-RPC responses for a method (e.g. tools/list result)
# ToolUse, ToolResult, Call, Transcript exactly as A 1.4, plus Transcript.audit (D5) and Transcript.cwd
```

`cl_assert` names (A 1.5): `precondition, tool_called, no_tool_called, tool_not_called, result_ok, result_error,
result_matches, args_match, call_order, cursor_chain, wire_called, wire_header, wire_jsonrpc_error, denied, jsonl_rows,
final_text_contains (weak), weak, get (jsonpath-ish getter), no_read(t, call=None)` and predicates `eq, approx, gt, ge,
lt, le, between, one_of, contains, regex, is_type, exists, absent, set_eq, sums_to`. Each failure message ends with
`summary(t)` (A 1.5). Hook helpers `hook_decision, hook_silent, hook_ran, hook_lines` live in `cl_hooks` (H2).

### 2.5 Coverage keys

`tool:<name>` (14, `openjev_mcp.TOOL_NAMES`), `resource:<uri>` (6), `template:<uriTemplate>` (3), `file://`,
`prompt:<name>` (5, `openjev_mcp.prompts`), `recipe:<id>` (29, `recipes/builtin/*.json`), `hook:<Event>` (PreToolUse,
PostToolUse, Stop, UserPromptSubmit), `skills:loaded`, `skill:<dir name>` (the 6 driven: agent-gates, decisions,
triage-routing, data-records, question-authoring, calibration, as `openjev-<x>`), `error:OJ_INVALID_INPUT:{schema,path,
roots,lint,cursor}`, `error:<CODE>` for OJ_UNREACHABLE, OJ_NOT_FOUND, OJ_TIMEOUT, OJ_AUTH, OJ_UNAVAILABLE, OJ_OVERLOADED,
OJ_SERVER, OJ_UNKNOWN_MODEL, `jsonrpc:-32602`, `profile:{core,tasks,ext,unreachable,notopenjev,fault}`,
`loop:batch_cursor`, `loop:batch_resume`. The per-group tables in section 3 say which case carries which key.

## 3. Packages

| Id | Kind | Owns (under `mcp/tests/claude_live/` unless absolute) | Depends |
|---|---|---|---|
| H1 | harness | `conftest.py`, `cl_env.py`, `cl_servers.py`, `cl_claude.py`, `cl_assert.py`, `cl_cases.py`, `cl_results.py`, `run_claude_live.py`, `test_cl_selftest.py`, `test_cl_smoke.py`, `results/.gitignore`, `fixtures/probes/*` | - |
| H2 | harness | `cl_hooks.py`, `cl_fault.py`, `test_cl_selftest_h2.py`, `test_cl_smoke_hooks.py` | H1 |
| G01 | group | `test_g01_protocol.py`, `cases/g01_protocol.json`, `results/triage-g01.jsonl` | H1 |
| G02 | group | `test_g02_reads.py`, `cases/g02_reads.json`, `results/triage-g02.jsonl` | H1 |
| G03 | group | `test_g03_tools.py`, `cases/g03_tools.json`, `results/triage-g03.jsonl` | H1 |
| G04 | group | `test_g04_batch.py`, `cases/g04_batch.json`, `results/triage-g04.jsonl` | H1 |
| G05 | group | `test_g05_calibrate.py`, `cases/g05_calibrate.json`, `results/triage-g05.jsonl` | H1 |
| G06 | group | `test_g06_recipes.py`, `cases/g06_recipes.json`, `results/triage-g06.jsonl` | H1 |
| G07 | group | `test_g07_resources_prompts.py`, `cases/g07_resources_prompts.json`, `results/triage-g07.jsonl` | H1 |
| G08 | group | `test_g08_hooks.py`, `cases/g08_hooks.json`, `fixtures/injection_note.txt`, `results/triage-g08.jsonl` | H1, H2 |
| G09 | group | `test_g09_skills.py`, `cases/g09_skills.json`, `results/triage-g09.jsonl` | H1, H2 |
| G10 | group | `test_g10_errors.py`, `cases/g10_errors.json`, `fixtures/recipe_extra/*.json`, `results/triage-g10.jsonl` | H1, H2 |
| F1 | final | `/mise-tasks/test-claude-live`, `README.md`, `RESULTS.md`, `/mcp/README.md` (Tests section only) | all |

Group id ranges: g01 T001-T008, g02 T009-T018, g03 T019-T028, g04 T029-T040, g05 T041-T048, g06 T049-T061, g07
T062-T072, g08 T073-T082, g09 T083-T090, g10 T091-T100. Rows: A 4 (the brief of each package copies its rows).

Coverage carried per group: g01 `tool:status`, `profile:tasks`, `profile:core`; g02 `tool:ask|yes_no|classify|score`;
g03 `tool:filter|lint|compile|generate|ask_image`; g04 `tool:batch|batch_results`, `loop:batch_cursor`,
`loop:batch_resume`, `error:OJ_INVALID_INPUT:cursor`; g05 `tool:calibrate`, `template:openjev://audits/{question_hash}`;
g06 the 29 `recipe:*` (g06 alone covers all 29); g07 the 6 `resource:*`, `template:openjev://recipes/{id}`,
`template:openjev://templates/{id}`, `file://`, the 5 `prompt:*`, `jsonrpc:-32602`; g08 the 4 `hook:*`; g09
`skills:loaded`, the 6 `skill:*`; g10 `error:OJ_INVALID_INPUT:{schema,path,roots,lint}`, the 8 other `error:*`,
`profile:{ext,unreachable,notopenjev,fault}`.

## 4. Triage policy (every group package)

1. Run the case. Pass -> next. Harness error (`error_class: harness`: claude did not start, MCP not connected,
   precondition, slot wait) -> do not touch the test; report it to the orchestrator with the run dir (a tiny workaround
   inside your own test file is fine; never edit `cl_*.py`).
2. Failure: up to 3 attempts. Each attempt first re-reads the cited spec section (`mcp/spec.md` first, then the build
   spec by line range, then the case-file entry) and appends `{id, attempt, spec_ref, spec_says, observed, verdict,
   change}` to `results/triage-gNN.jsonl`. Then:
   - the test expects something the spec does not promise (wrong field, wrong code, invented ground truth, ambiguous
     prompt, model did not call the tool) -> rewrite the prompt, arguments, data or assertion; loosen only what the
     spec does not guarantee, never weaken a check to nothing (no "any decision", no dropped deterministic check);
   - model noise (right tool and arguments, a borderline decision that flips) -> pick a clearer case-file input;
   - the server output contradicts the spec -> stop rewriting at once: it is a server suspect.
3. After 3 attempts with the spec still backing the expectation, or on a server suspect: reproduce without Claude
   (`ctx.call_tool` with the same arguments, or `run_live.call`), keep the run dir, leave the case failing (no xfail,
   D11), report it. Do not edit `mcp/openjev_mcp/` or `mcp/tests/test_mcp_*.py`: server fixes are the triage phase.
4. Report back: per id `pass|fail`, attempts used, what changed; server suspects with run_dir, spec ref (file:lines),
   the wire/result excerpt, and the SDK-client repro result; harness issues.

Triage phase (after the groups, before F1, run by the orchestrator, not a package here): for each server suspect A
5.3: SDK repro, read `tools/dispatch.call_tool` -> tool module -> `envelope`/`mapping`/`recipes/engine`, fix, add a
stub regression test in `mcp/tests/test_mcp_*.py`, `.venv/bin/python -m pytest -q mcp/tests`, rerun the live case on
a fresh own instance; unfixable -> `xfail="known: ..."` plus an entry for `mcp/spec.md` 10 "Open defects".

## 5. Package notes (the orchestrator's briefs repeat these in full)

### H1 harness core

Implement A 1.1-1.10, A 2.1-2.3 with the names of section 2 and decisions D1-D10. Specifics:
- Verify every flag of A 1.3 against `claude --help` first (`--max-budget-usd`, `--exclude-dynamic-system-prompt-sections`,
  `--no-session-persistence`, `--setting-sources ""`, `--disable-slash-commands`, `--strict-mcp-config`, `--tools`,
  `--permission-mode dontAsk`, `--plugin-dir`, `--settings`, `--debug-file`); drop unsupported ones in `argv()` and list
  them in the report (F1 records them in README). Never pass `--bare` or `--safe-mode` (F4).
- `conftest.py`: without `OPENJEV_CLAUDE_LIVE=1` and without `OJ_CLAUDE_SELFTEST=1`: `collect_ignore_glob =
  ["test_*.py"]`; with only `OJ_CLAUDE_SELFTEST=1`: collect only `test_cl_selftest*.py`. Insert this dir into `sys.path`.
  Session fixtures `live_env`, `work`, `instances` (`Instances`, teardown `stop_all`), function fixture `case_ctx`.
  Register markers (D9). `live_env` skips with the A 1.2 message, it never starts anything.
- `WireTap`: Starlette + httpx reverse proxy in a uvicorn thread, SSE streamed through, one `wire.jsonl` per run (A 1.2).
- `Ctx.call_tool`/`read_resource`: sync wrappers (`anyio.run`) over the MCP SDK `streamable_http_client` +
  `ClientSession`, straight to the instance (not the tap); reuse `mcp/tests/live/run_live.py` (`call`, `sc_of`).
- `run_case`: render prompt (D2), run `case.setup(ctx)` first, then `run_claude`, `precondition`, every `expect`,
  automatic retry per A 2.3, results row per A 1.8 (`cl_results`), prune passing run dirs unless `OJ_CLAUDE_KEEP=1`, on
  failure copy `instances.get(profile).tail()` into the run dir.
- `run_claude_live.py`: `--groups g01,g02` (default all present `test_g*.py`), `--procs N` (default 3), `-k EXPR`
  passthrough, `--list` (prints groups and case ids, no claude), shared `OJ_LIVE_RUN_ID`, `OPENJEV_CLAUDE_LIVE=1` for the
  children, never `OJ_LIVE_MCP_PORT` (D10), budget stop `OJ_CLAUDE_RUN_BUDGET_USD`, writes `results/<run_id>/summary.json`.
- `test_cl_selftest.py` (no claude, no model, no network): parse every `fixtures/probes/*` (string and list
  `tool_result.content`, resource link F17, hook error text F11, `permission_denials`, init fields), `WireLog` over
  `wire_tasks_on_batch.jsonl` (SSE + `claudecode/toolUseId` join), jsonpath getter and every predicate, `argv()` (no
  `--bare`/`--safe-mode`, API-key vars stripped, `mcp.json` shape), D2 rendering (JSON prompts survive), slot semaphore
  (two locks held -> third waits, `OJ_CLAUDE_SLOTS=2`), port picker skips a bound port; catalogue checks per D8.
- `test_cl_smoke.py` (live, one claude run): `status` called exactly once; asserts precondition (`apiKeySource == "none"`,
  `openjev` connected, 14 tools), `tool_called`, `result_ok`, wire `tools/call` joined by `claudecode/toolUseId`, a
  `results.jsonl` row with cost; afterwards nothing of ours listens on 8200-8299 and :8100 still answers `/health`.

Acceptance: `OJ_CLAUDE_SELFTEST=1 .venv/bin/python -m pytest -q mcp/tests/claude_live/test_cl_selftest.py` green with no
claude process; `.venv/bin/python -m pytest -q mcp/tests` (no env) collects nothing under `claude_live` and its
pass/skip counts are unchanged; `OPENJEV_CLAUDE_LIVE=1 .venv/bin/python -m pytest -q mcp/tests/claude_live/test_cl_smoke.py`
green; `run_claude_live.py --list` works with zero group files.

### H2 hooks, skills plugin, upstream faults

`cl_hooks.py`: `settings_for(case, ctx) -> Path` (A 1.3 hooks line; `case.hooks` = `{Event: {"matcher", "args",
"env"}}`; each `.in`/`.out` record newline-terminated even when Claude or the hook writes none, e.g. a small
`sh -c` wrapper; `timeout` >= `--timeout-ms` + 1000 ms rounded up to seconds; the pipeline exits 0), `plugin_dir(ctx) ->
Path` (A 1.2 `skills_plugin`, session-cached), `hook_lines(t, event)`, `payloads(t, event)`, `hook_ran`, `hook_silent`,
`hook_decision(t, event, decision=None, reason=None)` (PreToolUse: `hookSpecificOutput.permissionDecision`/
`permissionDecisionReason`; Stop/PostToolUse: top-level `decision`/`reason`; UserPromptSubmit:
`hookSpecificOutput.additionalContext`; check `mcp/openjev_mcp/hook.py` for the exact output). Hook CLI flags:
`.venv/bin/openjev-hook <pretooluse|stop|userprompt|posttooluse> --help` (top-level `--help` lists only pretooluse).
`cl_fault.py`: `FaultUpstream(upstream).start()/.stop()/.url`, kinds `401 503 529 500 sleep` per A 1.2 (bodies from
`mcp/openjev_mcp/mapping.py` and `mcp/tests/stubs.py`). `test_cl_selftest_h2.py` (no claude, no model): settings JSON
shape, the wrapper over a fake hook command, decision parsing over `fixtures/probes/hook_pretooluse_{allow,deny}.json`
and `mcp/tests/fixtures/claude_code/*`, plugin dir has 11 skill symlinks, FaultUpstream over a local stub upstream for
every kind. `test_cl_smoke_hooks.py` (live, 2 claude runs): a PreToolUse `pretooluse` hook on `git status` in a scratch
git repo (decision lines recorded), and `--plugin-dir` loads 11 `openjev-skills:*` in `init.skills`.

### Groups G01-G10

Rows: A 4 (copied in each brief). Coverage keys: section 3. Triage: section 4. Each group's `setup` seeds through
`ctx.call_tool`; prompts name the tool and give compact-JSON arguments with absolute paths; ground truth only from case
files and proven `mcp/tests/live/` assertions; pin the chosen case-file ids in `pins`.

### F1 final

`mise-tasks/test-claude-live` (executable; `#MISE description=...` naming subscription use; sources `lib/common.sh`;
`oj_require_mcp_installed`; `oj_require_cmds claude curl`; dies with a hint when `$OPENJEV_BASE_URL/health` is down,
never starts anything; `export OPENJEV_CLAUDE_LIVE=1`; `exec "$OJ_PY" mcp/tests/claude_live/run_claude_live.py "$@"`),
`README.md` here (purpose, isolation and subscription login, how to run, knobs, cost and time, layout, triage policy,
harness deviations reported by H1/H2), the Tests section of `mcp/README.md` (one paragraph + the command; create the
task first, `test_mcp_docs_consistency::test_mise_tasks_exist` checks it), a full run (`mise run test-claude-live
--procs 3`), `RESULTS.md` (per group pass/fail, cost, p50/p95, retries, failing ids with run dirs and their triage
verdicts, server fixes made in the triage phase, open suspects). Do not edit `mcp/spec.md` (flag the missing section
7/8 rows in the report).
