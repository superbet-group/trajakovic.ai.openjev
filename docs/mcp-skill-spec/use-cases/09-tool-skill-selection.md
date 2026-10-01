# Usage type 09: Tool, skill and MCP selection from a roster

Test file: `tests/cases/09-tool-skill-selection.json` (20 cases, 20/20 pass on `openjev-latest`, MLX backend).

## Purpose

Decide which one skill, tool, MCP server or rule (if any) to inject for a user prompt, out of a roster of N,
with an explicit `none` option so the selector can abstain. Replaces keyword matching over descriptions and
loading a 100+ entry roster into the agent's own context.

## When a coding agent should reach for OpenJev instead of reasoning

- The agent is about to paste a long skill/tool list into context, or match descriptions by keyword.
- A hook (UserPromptSubmit) must choose a skill hint cheaply before the main model runs (~0.4 s read; 1.5-13 s measured on the shared server depending on roster size and load).
- Keyword matching is unsafe: "slide" in "sliding window", "PDF"/"chart" inside a bug report.
- The output must be a validated id from a closed set, never free text.

## Recommended question schema

Single pick with abstention (put roster ids as `criteria` keys, one-line descriptions as values):

```json
{
  "model": "openjev-latest",
  "state": "User prompt: make me a pitch deck for our seed round, 10 slides, investor-friendly",
  "questions": {
    "skill": {
      "type": "choice",
      "instructions": "Which single skill from the roster, if any, should be loaded for this user prompt? Pick a skill only if the activity the user wants done IS what the skill does; a topic word appearing in a code change does not count. Otherwise pick none.",
      "criteria": {
        "pptx": "Create slide decks and pitch decks in PowerPoint format",
        "pdf": "Read, extract, merge, split or fill PDF files",
        "systematic-debugging": "Investigate a bug, failing test or unexpected behavior before proposing fixes",
        "none": "No skill from the roster applies; answer directly without loading any skill"
      }
    }
  }
}
```

Per-tool gating (activate each tool whose `is_needed` clears the bar); one noul per candidate, one request:

```json
{"needs_slack": {"type": "noul",
  "instructions": "Is the tool 'slack-messaging' (Draft and send Slack messages and announcements) needed to complete this user prompt?"}}
```

Cheap pre-gate before any roster read:

```json
{"any_skill_needed": {"type": "noul",
  "instructions": "Does a specialized skill from this roster clearly match the user prompt, so it should be loaded? Roster: pdf: ...; pptx: ...; ..."}}
```

## Phrasing rules learned

1. Always include a `none` option with a self-explanatory description ("No skill applies; answer directly"). Without it the model must pick something.
2. Write roster descriptions as activities ("Create slide decks"), not marketing. One line each.
3. Put the user prompt in `state`, prefixed `User prompt:`. Put roster in `criteria`, not in `state`.
4. Keyword-trap guard. Before: `"Which skill should be loaded?"` on "our PDF export button throws a 500 because render_chart() returns None; add a null check" picked `pdf` (P=0.49, confidence 0.49). After: appended "Pick a skill only if the activity the user wants done IS what the skill does; a topic word appearing in a code change (pdf, chart) does not count. Otherwise pick none." gives `none` (P=0.41 vs pdf 0.36). Still marginal: see thresholds.
5. For noul over a roster, inline the roster in the instruction (noul has no criteria list); or use per-tool noul questions.
6. Do not split a compound prompt across questions unless you want per-tool flags; for "fix the test and post to Slack" per-tool noul flags (sel-09) correctly returned both true.

## Thresholds for acting

| Signal | Rule |
|---|---|
| choice `confidence` >= 0.8 and `choice != none` | inject that skill |
| `choice == none` | inject nothing |
| confidence < 0.6 (e.g. sel-19 at 0.57) | treat as abstain; or re-read with `samples: 3` / `think: 256`, or ask the user |
| per-tool noul | activate if >= 0.7, skip if <= 0.3, between: leave it to the agent |
| `any_skill_needed` | < 0.3 skip the roster read entirely; > 0.7 proceed to the choice read |

Observed values: true positives 0.94-1.0 confidence; clear negatives noul 0.00; noul true positives 0.98-1.0.

## Validation and errors the MCP layer must handle

- Always check `answers.skill.choice` is a key of the roster (or `none`) before injecting. The server returns only criteria keys, but the MCP layer should still validate.
- Option cap is 255 choices (400 `"Too many choices. Must have at most 255 choices."`, verified: 255 works, 256 fails). With `none` the roster limit is 254. Real rosters of 180 work (sel-18). For larger rosters pre-filter (embedding/keyword shortlist to <= 100), then choose.
- Empty `criteria` is a 400. MCP must refuse before calling.
- Input tokens grow ~15 per option: 100 opts ~1.5k, 255 opts ~3.9k tokens.
- Use "top 3 second read": first read the full roster, take the 3 highest probabilities plus `none`, re-ask with only those and `samples: 3` (sel-12).

## Limitations

- Marginal cases remain: sel-19 passes only barely (P(none) 0.41 vs pdf 0.36). Do not rely on abstention for prompts containing a roster topic word unless confidence is high.
- A multi-intent prompt (test fix + Slack post) yields a single choice for the dominant skill; use per-tool noul flags for multi-select.
- Wall time on the shared server was 1.5-18 s for large rosters and `think`; not a sub-second guarantee.
- Skill descriptions that overlap (pr-review vs security-review) split probability; accept `choice_in` both or inspect `probabilities`.

## Test coverage (20 cases)

sel-01 pptx pitch deck; sel-02 chit-chat none; sel-03 debugging from traceback; sel-04 PR review (pr-review or security-review accepted); sel-05/06 any_skill_needed noul +/-; sel-07 "sliding window" keyword trap -> none; sel-08 SQL; sel-09 four per-tool noul; sel-10 release notes; sel-11 near-miss rename -> none, choice and noul agree; sel-12 narrowed roster + `samples: 3`; sel-13 xlsx with `think: 256`; sel-14/15 62 options pick/abstain; sel-16 empty criteria 400; sel-17 256 options 400; sel-18 181 options security-review; sel-19 marginal keyword trap; sel-20 docker compose gate.

## Final result

20/20 passed (`run_cases.py`, live server). No case marked KNOWN LIMITATION; sel-19 is flagged marginal.
