# openjev-mcp: live runs

Real MLX model on :8080 (shared, one GPU, calls sequential), own MCP on 8196/8197 (killed afterwards), :8100 untouched.
Harness: `mcp/tests/live/run_live.py` (port via `OJ_LIVE_PORT`), `mcp/tests/live/test_live_tools.py`, recipe files under `mcp/tests/live/`.
Results files: `mcp/tests/live/results/2026-10-phase2-3.json` (full, incl. run_cases and recipe files), `2026-10-verify.json` (`--full --quick`, verifier rerun).

## CI (no model)
`pytest mcp/tests tests/test_api.py -q`: 2259 passed, 41 skipped (live tests skipped without `--live`).

## Batch throughput (200-row CSV, 2 questions per row, concurrency 2, sampling fast, cursor loop of 4 calls x 50 rows)
| run | wall_s | req_per_s | rows | duplicates | gaps |
|---|---|---|---|---|---|
| full run (18:23) | 64.4 | 3.11 | 200/200 | 0 | 0 |
| verify rerun | 57.6 | 3.47 | 200/200 | 0 | 0 |

Interrupted then resumed (80 rows, client closes POST after 4 s, resume without cursor): 11-13 rows done before cancel, all skipped on resume, 80 unique ids, 0 duplicates, 0 gaps.

## Tool checks (verify rerun, `--full --quick`: 18 of 20 pass)
| check | result |
|---|---|
| batch_results view=review/stats/rows | pass (3-11 ms) |
| batch_results export csv/markdown/ojui-batch vs batch export of the same file | byte-identical (was a bug, fixed below); filtered jsonl readable (67 rows) |
| batch_results compare_to (200-run vs resumed 80-run) | matched 80, only_in_a 120 |
| filter (3 calls) | kept L4 only, p50 71 ms |
| recipe ticket_triage dry_run / live | dry_run 33 ms, 0 requests, built request returned; live 228 ms, decision `route`, dept payments p=0.9999 |
| calibrate (7 labelled, x2) | accuracy 1.0, separable, gap 0.9974, band 0.05/0.95, most borderline ex-cal-7 |
| ask_image ex-image (docs/mcp-skill-spec/tests/data) | hotdog 0.998, 840 ms |
| compile | recipe ticket_triage / none / typed questions, p50 546 ms |
| generate x5 | 4 of 5 ok; one empty completion retried (known flake, direct chat 0/5 empty) |
| hooks pretooluse (14 gate cases), stop, userprompt, posttooluse | pass |
| Tasks (`OPENJEV_MCP_TASKS=on`) | task -> completed, result equals sync ids, cancel works |
| phase-1 ex-* calls | 11 calls, 0 mismatches, p50 312 ms |
| `test_live_tools.py` | 11 passed (42 s) |

## Failures and disposition
- batch_results export differed from the batch export (questions rebuilt from the answers, instructions/criteria lost). Fixed: batch header now stores `questions` (additive key), `batch_results` and `batch_prompts` prefer it; old files fall back to inference. Test: `test_make_header_keeps_questions_for_batch_results`. Verified byte-identical live.
- legacy-era client cancel does not stop an in-flight batch (stateless HTTP ignores `notifications/cancelled`): documented deviation 20, SDK design. A closed POST does stop it (the interrupted-resume run).
- earlier full run, model-side: run_cases 06 `secrets-real-token-flagged`; recipe files done_gate gate-03, skill_selection sel-19, semantic_lint secrets-pos-and-neg-pair, entity_match cat-01 (raw case passes, the recipe's own questions fail: engine/threshold tuning, not blocking); known limitations 6.4 unchanged.

## Protocol eras (raw JSON-RPC over HTTP plus SDK 2.2.0 clients, no model)
| era | result |
|---|---|
| 2026-07-28 (envelope, `Mcp-Method`/`Mcp-Name` headers) | `server/discover` supportedVersions 2026-07-28, 2025-11-25, 2025-06-18; tools/resources/templates/prompts list with `resultType: complete`, `ttlMs`, `cacheScope`; unknown tool -32602; `tasks/get` -32601 with Tasks off; unsupported envelope version -32022 with `data.supported` |
| 2025-11-25, 2025-06-18 | initialize negotiates the same version; 14 tools, 6 resources, 3 templates, 5 prompts; `lint` call ok |
| 2025-03-26, 2024-11-05 | served best-effort, same lists |
| unknown version 1999-01-01 | counter-offer 2025-11-25 |
| SDK `ClientSession` (legacy) / `Client` (modern) | both list 14 tools; call ok |
