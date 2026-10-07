# Results of the last full run

| | |
|---|---|
| Run id | `full-20261002T195546` (`results/full-20261002T195546/`, `summary.json`) |
| Date | 2026-10-02 |
| Claude Code | 2.1.287, subscription login (`apiKeySource: none`) |
| Models | cheap tier `claude-haiku-4-5-20251001` (97 attempts), strong tier `claude-sonnet-5-5` (3 attempts) |
| Command | `mise run test-claude-live --procs 3` (OpenJev :8080 and the own MCP instances; slots 3) |
| Totals | 100 cases, 100 attempts: **98 passed, 2 failed**, 0 xfail, 0 skip, 0 auto-retries |
| Cost | $1.40 (budget $15, not reached) |
| Wall | 316 s for the run (groups 44-131 s each) |
| Case duration | p50 5.8 s, p95 13.6 s |

Coverage: the global catalogue check passes (T001-T100 contiguous, every COVERAGE key hit; `OJ_CLAUDE_SELFTEST=1` run of both
self-test files: 60 passed). CI: `.venv/bin/python -m pytest -q mcp/tests` gives 2223 passed, 41 skipped, no `claude_live`
file collected; `test_mcp_docs_consistency` green.

## Per group

| Group | Cases | Pass | Cost | Mean s |
|---|---|---|---|---|
| g01 protocol | 8 | 8 | $0.070 | 3.5 |
| g02 reads | 10 | 10 | $0.106 | 4.9 |
| g03 tools | 10 | 10 | $0.120 | 6.0 |
| g04 batch | 12 | 10 | $0.193 | 8.8 |
| g05 calibrate | 8 | 8 | $0.109 | 7.2 |
| g06 recipes | 13 | 13 | $0.204 | 8.0 |
| g07 resources/prompts | 11 | 11 | $0.181 | 6.7 |
| g08 hooks | 10 | 10 | $0.102 | 5.0 |
| g09 skills | 8 | 8 | $0.187 | 11.1 |
| g10 errors | 10 | 10 | $0.124 | 5.6 |

## Failures

Both are in g04, both classified `assertion`, both pass when run alone (`rerun-T029-T030`: 2 passed, $0.03, one targeted
rerun). Neither is a server defect; no test was edited.

| Id | run_dir | error_class | Verdict | Cause | Spec ref |
|---|---|---|---|---|---|
| T029 batch_dry_run | `runs/T029-a1` | assertion (`no_read: 1 audit entries ['classify']`) | harness defect, test right | All ten pytest processes of a runner run share `results/<run_id>/work/`, so the `default` instance's `OPENJEV_MCP_LOG` (`mcp-default.audit.jsonl`) and audit dir are one file across concurrent groups. A `classify` line written by another group during T029's window was counted as an audit entry of the dry run. The dry run itself behaved correctly (`stopped_reason: dry_run`, `n: 0`, `audit: 0`). | build 2.11 (dry_run), mcp/spec.md (batch dry_run) |
| T030 batch_inline_run | `runs/T030-a1` | assertion (`tool_called(batch, times=1)`: 2 calls) | model flake | The prompt says "exactly these arguments"; haiku added `output_path: "batch_results.jsonl"`, got `OJ_INVALID_INPUT` ("relative path ... pass an absolute path"; correct server behaviour, spec 7) and retried with a fix. The rerun made a single call. | build 2.11; mcp/spec.md dev 27 |

Suggested fixes for the owners (not applied, outside this package): make `work` per pytest process (for example
`run_root()/"work"/f"p{os.getpid()}"` in `conftest.py::work`) and the `T030` check tolerant of one corrective retry, or
mention in the prompt that no `output_path` is wanted.

## Triage phase (before this run)

Triage records are in `results/triage-g02,g03,g05..g10.jsonl` (verdicts: test wrong, test ambiguous, client rendering).
One server fix came out of it:

| Case | Verdict | Fix | Regression test |
|---|---|---|---|
| T100 extension_profile | code bug (spec: `OPENJEV_MCP_RECIPES` ids appear in the recipe enum and are callable; mcp/spec.md:119,299, build 2.16) | `mcp/openjev_mcp/tools/dispatch.py`: `specs()` cache key is now `(len, config.recipes_dir)`; on a hit with a different active key the recipe schema is re-registered and the validator cache cleared (the import-time `_ORIG` had registered the default-config schema) | `test_dispatch_accepts_extra_dir_recipe` in `mcp/tests/test_mcp_recipe_tool.py` (not run against the unfixed code, reasoned to fail there) |

T100 passes live in this run.

## Open suspects for mcp/spec.md section 10 'Open defects'

Not edited here (not owned). Candidates:

- Client rendering, not server: Claude Code shows an unknown or non-batch resource URI (`openjev://nope`, `file:///etc/hosts`)
  as a normal tool result with text `Resource not found: ...` (`is_error: false`) and replaces a 204 KB resource by a
  persisted-output notice (patterns resource). Worth a line in section 7/8 so the wire contract (-32602) and the Claude Code
  behaviour are both documented.
- `OJ_INVALID_INPUT` `path` carries the offending value (`batch_results.jsonl`, `/etc/hosts`) rather than a JSON path for
  path refusals; the hint "allowed roots: <work>, <work>" lists a root twice (cosmetic).
- `ask_image` options have no `think`; unknown option keys are `OJ_INVALID_INPUT`.
- `/v1/limits` 404 on OpenJev: limits come from defaults (`read_at: null`, `limit_source: default`), already deviation 17.
- Spec rows to add to sections 7/8: `error.path` semantics for path refusals; `dry_run` writes no audit line and
  `output_path` is ignored (`output_path: null`).

## Follow-up

T029 (shared work dir between pytest processes) and T030 (tolerate one corrective retry) were harness defects, fixed in conftest.py and test_g04_batch.py. g04 reruns 12/12; with T100 fixed in triage, all 100 cases pass.

## 2026-10-07 run on 0.6.0 (merge of razorback16/openjev 0.6.0)

First full run: 96/100 (g03 T021, g04 T034, g07 T071, g09 T086). Two further full runs: 99/100 (T046 only) and 100/100. No server
defect found; the merge touched none of the MCP code.

| Id | error_class | Verdict | Cause | Action |
|---|---|---|---|---|
| T021 lint_valid | assertion (`body_hash` missing) | model flake | haiku dropped `emit: ["body"]` from "exactly these arguments"; lint then has no body to hash | none; passes on rerun |
| T034 batch_cursor_args_changed, T046 chunked_calibrate | assertion (cursor differs from `next_cursor`) | model flake | haiku mistyped one character while copying a ~230-char base64 cursor; the server rejected it with `OJ_INVALID_INPUT` as it should | none; pass on rerun. Copying opaque cursors is the weakest point of the cheap tier |
| T071 prompt_audit_question | assertion (first openjev call was `read`) | harness defect | the prompt hands over file paths but the case ran with no built-in tools, so the model could not read the question file (it passed on 2026-10-02 only because the model guessed) | case now gets `Read`; "first openjev call" ignores built-ins; 3/3 reruns pass |
| T086 triage_routing_ticket | assertion (`signals.dept` missing) | harness defect | haiku previewed with `dry_run: true` before the real call; the check read `rc[0]`, the dry run, whose `signals` is `{}` | check skips `decision == "dry_run"` calls |

Also fixed outside the live suite: `mcp/tests/test_mcp_batch_walkthrough.py::test_skill_dry_run_block_is_what_the_test_sends`
had failed since commit 2a091d8 moved and reworded the skills; its `DEPT` criteria now match `openjev-data-records/SKILL.md`.
