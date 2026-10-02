# Claude Code live suite

100 end-to-end cases (T001-T100, 10 groups) that run Claude Code headless (`claude -p`) against an OpenJev MCP server and
the real OpenJev on :8080 (real MLX model, one GPU): `claude -> openjev-mcp -> OpenJev`. They check what a Claude Code user
actually gets: tool calls, results, errors, resources, prompts, hooks and skills. Design: [ARCHITECTURE.md](ARCHITECTURE.md);
task breakdown: [TASKS.md](TASKS.md); last full run: [RESULTS.md](RESULTS.md).

**Never part of `mise run test` or CI.** Without `OPENJEV_CLAUDE_LIVE=1` the directory's `conftest.py` ignores every test
file, so nothing is imported. The suite spends subscription usage and needs OpenJev running.

## Isolation

- **Subscription login only.** `claude` uses your existing OAuth login. The suite never reads credentials and never asks for
  an API key; `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` and `CLAUDE_CODE_USE_{BEDROCK,VERTEX,FOUNDRY}`
  are stripped from the child environment. Precondition per run: `system/init` reports `apiKeySource: "none"`.
- `--setting-sources ""` drops user plugins, hooks and settings; `--strict-mcp-config` loads only the suite's `--mcp-config`;
  `--no-session-persistence`, `--disable-slash-commands` (off for skill cases). `--bare` and `--safe-mode` are never used
  (the first skips OAuth, the second drops the MCP server).
- **Own MCP instance** on a free port in 8200-8299 (`OJ_LIVE_MCP_PORT` pins the default one) against
  `OPENJEV_BASE_URL`. Your MCP on :8100 and OpenJev on :8080 are never started, stopped or restarted by the suite.
- Single GPU: concurrent `claude` processes across all pytest processes are capped by a file-lock semaphore
  (`OJ_CLAUDE_SLOTS`, default 3). Batch and calibrate cases use `concurrency <= 2`.

## Run

```sh
mise run test-claude-live --list                  # groups and case ids, starts no claude
mise run test-claude-live --procs 3               # everything (flags pass through; no `--` needed)
mise run test-claude-live --groups g01,g02 -k T012
```

Runner flags: `--groups g01,g02` (default all), `--procs N` (pytest processes, default 3), `-k EXPR` (pytest `-k`), `--list`;
unknown flags go to pytest. The task checks `claude`, `curl` and `$OPENJEV_BASE_URL/health` and stops with a hint
(`mise run start`) if OpenJev is down.

Without mise:

```sh
export OPENJEV_CLAUDE_LIVE=1
.venv/bin/python -m pytest -q mcp/tests/claude_live                       # all, serial
.venv/bin/python -m pytest -q mcp/tests/claude_live/test_g04_batch.py     # one group file
.venv/bin/python -m pytest -q mcp/tests/claude_live -k T032               # one case
OJ_CLAUDE_SELFTEST=1 .venv/bin/python -m pytest -q mcp/tests/claude_live/test_cl_selftest.py mcp/tests/claude_live/test_cl_selftest_h2.py
```

The self-tests need no claude and no OpenJev (parser, assertions, fault proxy, hooks helper, global catalogue check:
T001-T100 contiguous, every COVERAGE key hit).

## Knobs

| Env | Default | Meaning |
|---|---|---|
| `OPENJEV_CLAUDE_LIVE` | unset | `1` enables collection (the mise task sets it) |
| `OPENJEV_BASE_URL` | `http://127.0.0.1:8080` | the real OpenJev |
| `OJ_LIVE_MCP_PORT` | auto in 8200-8299 | port of the `default` MCP instance |
| `OJ_CLAUDE_SLOTS` | `3` | concurrent `claude` processes across all pytest processes |
| `OJ_CLAUDE_BIN` | `claude` | binary |
| `OJ_CLAUDE_MODEL` / `OJ_CLAUDE_MODEL_STRONG` | `haiku` / `sonnet` | cheap and strong tiers |
| `OJ_CLAUDE_BUDGET_SCALE` | `1` | scales the per-run `--max-budget-usd` |
| `OJ_CLAUDE_RUN_BUDGET_USD` | `15` | the runner stops scheduling groups past this summed cost |
| `OJ_CLAUDE_DEBUG` | `0` | `1` adds `--debug-file` |
| `OJ_CLAUDE_KEEP` | `0` | keep passing run dirs whole |
| `OJ_LIVE_RUN_ID` | generated | run id shared by the runner's processes |
| `OJ_CLAUDE_SELFTEST` | unset | `1` collects the self-test files |

Cheap tier runs where the prompt names the tool call; the strong tier is used where the cheap model refuses or paraphrases
(for example the rule-deny hook case).

## Cost and wall time

Last full run (2026-10-02, 3 slots, mostly haiku): $1.40 and 316 s wall for 100 cases, p50 5.8 s, p95 13.6 s per case; details in
[RESULTS.md](RESULTS.md). The first claude run of a session costs more (cache creation). The runner stops scheduling groups past
`OJ_CLAUDE_RUN_BUDGET_USD`.

Known issue: concurrent groups share `results/<run_id>/work/` (one MCP audit log), which can add foreign audit lines to a case
(seen once, T029); a single-case rerun with `-k` passes.

## Layout

```
run_claude_live.py   runner: one pytest per group, run budget, results/<run_id>/summary.json
conftest.py          gate, markers, session fixtures
cl_env.py cl_claude.py cl_servers.py cl_assert.py cl_results.py cl_cases.py   harness: env/knobs, claude -p driver and
                     parser, own MCP instances, assertions, results log, catalogue
cl_fault.py cl_hooks.py                                                       fault proxy (upstream errors), hook helper
test_g01_protocol.py ... test_g10_errors.py   the 100 cases by group; cases/ and fixtures/ hold data and probe transcripts
test_cl_selftest*.py test_cl_smoke*.py        harness self-tests and smoke runs
```

Ownership: the harness (`cl_*.py`, runner, conftest) and each group file are separate units; the mise task, this README,
RESULTS.md and the Tests section of `mcp/README.md` belong to the final package.

## Results, run dirs, evidence

`results/<run_id>/results.jsonl` has one line per attempt (id, group, model, tier, pass, error_class, tools, turns,
duration, cost, run_dir, auto-retry link); `summary.json` has pass/fail per group, total cost, p50/p95 duration, retries and
the failing ids with run dirs; `pytest-<group>.log` per group. `error_class`: `harness`, `model_choice`, `assertion`,
`timeout`. Passing run dirs are pruned to `argv.json`, `claude.json` and `wire.jsonl` (the MCP wire log) unless
`OJ_CLAUDE_KEEP=1`; failing ones are kept whole with the tail of each MCP instance log. `results/` is not tracked.

## Failure triage policy

When a case fails: (1) reread `mcp/spec.md`, then the build spec section, and decide whether the test asserts what the spec
promises; if not, rewrite the test (up to 3 attempts, each rereading the spec). (2) If the test is right, read the server
code and fix it, adding a regression test to `mcp/tests`. (3) Record each attempt in `results/triage-<group>.jsonl`
(spec_ref, spec_says, observed, verdict). Spec gaps go to `mcp/spec.md` section 10 'Open defects'. Typical test-side causes
seen so far: the Claude Code client rendering (an unknown resource URI arrives as a normal result, large results are replaced
by a persisted-output notice), and model paraphrasing or refusing a prompt.

## Harness notes

Every flag used was verified against `claude --help` (2.1.287); none dropped. Prompts give absolute paths (`{cwd}`) except
the case that tests relative-path refusal. MCP prompts are asserted on the wire (`prompts/get`), because the expanded
prompt does not appear in the transcript. Claude Code speaks MCP 2026-07-28; the tasks capability is not declared by it, so
`batch` stays synchronous.
