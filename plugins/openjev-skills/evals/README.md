# Plugin-native evals (optional, unscored)

Four portable cases for `claude plugin eval`. Publishers can re-score the plugin with the platform tool; the default `with-without` ablation runs each case twice (with and without the plugin) and reports the delta. These cases are separate from the repo's own skill-effectiveness score.

| Case | Skill exercised | What it checks |
| --- | --- | --- |
| `gate-destructive-command` | openjev-agent-gates | `mcp__openjev__recipe` with `command_gate`, a deny verdict is reported |
| `question-authoring-lint` | openjev-question-authoring | the draft is checked with `mcp__openjev__lint` |
| `inline-items-dry-run` | openjev-data-prep, openjev-data-records | `mcp__openjev__batch` with `dry_run: true` before spending reads |
| `csv-batch-dry-run` | openjev-data-records | CSV import preview first, then labelled rows in `out.jsonl` (needs `--scaffold`) |

`tool_used: Skill` graders are marked `arm: with-only`, so they show whether the skill fired and do not count toward the score.

## Run

    claude plugin eval <path-to>/plugins/openjev-skills --runs 1 --model haiku \
      --max-cost-usd 0.5 --no-publish --trust-plugin --allow-tools 'mcp__openjev__*'

Add `--scaffold` for `csv-batch-dry-run` (it runs `scaffold/setup.sh`, which only writes `tickets.csv` into the scaffold directory). Use `--case <name>` to run one case. Reports go to `evals/results/` (gitignored).

## Known limitation: the eval child cannot reach the OpenJev MCP server

Verified with claude 2.1.289: each eval run starts in an isolated home, and its init message lists `"mcp_servers": []`. A server registered with `claude mcp add openjev ...` at user scope is therefore not visible, and the plugin ships no MCP config (the optional `openjev-mcp` plugin carries it). Result: the skills load and fire (the Skill grader passes), but graders that need `mcp__openjev__*` calls fail in both arms, so the score delta is 0 and the numbers are not meaningful.

To get meaningful scores, evaluate with the server available to the child. Options to try:

- Evaluate the `openjev-mcp` plugin together with these skills and pass `--mocks off --allow-real-servers`. This starts the real server process outside the sandbox, so use it only on a trusted machine.
- Record a mock for the `openjev` server under `evals/mocks/` (see `claude plugin eval --help`, `--mocks`).

Neither was tried here. Until one works, treat these cases as a syntax-valid starting point and rely on the repo's own live eval for the effectiveness score.
