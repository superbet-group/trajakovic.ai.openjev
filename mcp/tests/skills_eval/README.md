# openjev-skills evaluation

Scores how well the `openjev-skills` plugin (`plugins/openjev-skills/`) guides a fresh Claude Code session in preparing data for the OpenJev MCP. One composite number in 0..1, four weighted components, and an uplift gate against a no-plugin baseline.

```
composite = 0.15 * static + 0.25 * offline + 0.20 * trigger + 0.40 * live
uplift    = live(plugin arm) - live(baseline arm)        # gate, not weighted
```

## Components

| component | weight | floor | what it measures | needs |
|---|---|---|---|---|
| static (S) | 0.15 | 0.95 | Best-practice conformance per skill (frontmatter, third-person description, body under 500 lines, one-level references, no absolute or repo paths, qualified `mcp__openjev__<tool>` names, labelled JSON blocks, checklists, valid `openjev://` URIs, no n-gram overlap with trigger or scenario prompts) plus manifests (`claude plugin validate --strict`, version parity, description disjointness) | nothing (`claude` CLI for the validate check) |
| offline (F) | 0.25 | 0.98 | Every labelled call, result, questions, recipe-inputs and items block checked in-process with the server's own validators, `lint` strict, recipe and batch dry runs on the stub OpenJev, chaining edges against the tool schemas, resource URIs, `tool-io.md` regenerates identically | nothing, no network |
| trigger (T) | 0.20 | 0.80 | Held-out prompts (positive, negative, confusable) scored with OpenJev's own `skill_selection` recipe over the real skill descriptions plus 10 distractor skills. Top-1 of `signals.skill` counts; an abstain with the right skill counts 0.5 | OpenJev on `OPENJEV_BASE_URL` (default `http://127.0.0.1:8080`), local reads only |
| live (L) | 0.40 | 0.70 | `claude -p --model haiku` on 24 scenarios (from the existing cases) with the plugin loaded and the MCP behind WireTap; deterministic partial-credit checks on the recorded MCP calls. Implicit (headline) and explicit (`/openjev-skills:<name>` prefix) subsets are reported separately | `OPENJEV_CLAUDE_LIVE=1`, the MCP at `--mcp-url`, subscription usage |
| uplift (U) | gate | 0.20 | Mean live score of the plugin arm minus the baseline arm (no plugin), paired per scenario, implicit subset; a bootstrap 90% interval is reported | part of the live run |

Targets: composite >= 0.85, uplift >= 0.20, and every floor above. The exit code is 0 only when all hold. If a component did not run, the report shows `composite_partial` with the weights renormalised over the components that ran and `partial: true`; a partial run is never reported as a pass. The report also shows the HEAD-skills "before" scores, and a mutated copy of the skills that must fall below the floors (proof that the checks can fail).

## Commands

Run from the repository root.

```sh
.venv/bin/python mcp/tests/skills_eval/run_eval.py static
.venv/bin/python mcp/tests/skills_eval/run_eval.py offline
.venv/bin/python mcp/tests/skills_eval/run_eval.py trigger
OPENJEV_CLAUDE_LIVE=1 .venv/bin/python mcp/tests/skills_eval/run_eval.py live --model haiku --max-cost-usd 6
OPENJEV_CLAUDE_LIVE=1 .venv/bin/python mcp/tests/skills_eval/run_eval.py all  --model haiku --max-cost-usd 6
```

Useful options: `--only L01,L02` (scenario subset), `--arm skill|baseline|both`, `--runs N`, `--mcp-url URL` (default `http://127.0.0.1:8100/mcp`), `--private-instances` (spawn private MCP instances instead; the orchestrator opts in), `--no-claude` (skip `claude plugin validate`), `--contrast` (HEAD and mutated-copy contrast; default for `all`), `--out-dir`.

Without `OPENJEV_CLAUDE_LIVE=1` a live request exits 2 with a message. CI-safe tests (no live spend): `.venv/bin/python -m pytest -q mcp/tests/test_mcp_skills.py mcp/tests/test_skills_eval.py`.

## Reports

`.tmp/skills-eval/<run_id>/report.json` and `report.md`, copied to `.tmp/skills-eval/latest/`. The JSON has the per-component scores and checks, per-scenario per-arm results (checks passed, cost, turns, tools called, whether the Skill tool was used), weights, targets, composite, uplift and `pass`. The markdown has the headline table (component, score, weight, floor, status), the top failures per component and the cost.

Paths the MCP server writes (batch outputs, exports) go under `logs/skills-eval/<run_id>/` in the repository, because the daemon refuses writes under dot-directories such as `.tmp/`; the report itself stays in `.tmp/skills-eval/`.

## Cost

Static, offline and trigger are free (the trigger run makes about 90 local OpenJev reads, roughly 150-220 ms each). The live component runs only haiku: each run has a per-scenario budget (0.25 USD, 0.5 for the batch scenarios), and `--max-cost-usd` (default 6) stops scheduling new runs once reached. Expect about 2-4 USD for one full paired run (both arms); the report states the actual cost. A re-run repeats both arms and the earlier results stay in the report.

## Layout

| path | role |
|---|---|
| `run_eval.py`, `report.py` | entry point, composite and report |
| `static_check.py`, `offline_check.py`, `trigger_eval.py`, `live_eval.py` | the components |
| `skill_lib.py` | block extractor shared by static and offline checks |
| `gen_tool_io.py` | regenerates the hub's `references/tool-io.md` from the live tool schemas (`--check` in the offline run) |
| `live_scenarios.py` | scenario setup and the check vocabulary |
| `variants.py` | HEAD "before" and mutated-copy contrast |
| `data/` | `triggers.jsonl`, `distractors.json`, `scenarios.json`, `tool_examples.json`, `fixtures/` |

Skill authors do not read `data/`: the trigger and scenario prompts are held out, and `static_check` fails any 8-word sequence shared between a skill and those prompts.
