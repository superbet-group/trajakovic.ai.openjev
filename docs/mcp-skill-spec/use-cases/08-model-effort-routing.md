# Use case 08: Per-turn model and effort routing

Test file: `docs/mcp-skill-spec/tests/cases/08-model-effort-routing.json`
Final result on the live server: **16/16 passed** (14 behavioral cases, 2 error cases). No case is marked KNOWN LIMITATION.

## Purpose

Before an agent harness (or an agent itself) spends a turn, OpenJev reads a compact task summary and returns calibrated probabilities for three routing decisions: which model tier (haiku / sonnet / opus), whether the task needs deep reasoning, and how complex it is. The harness maps those numbers to a model and a thinking effort. The read costs about 0.4 s and no output tokens, so it is far cheaper than asking a frontier model "how hard is this?".

Typical tasks:
- Choose haiku / sonnet / opus per prompt or subtask.
- Decide whether a turn needs extended thinking.
- Log every decision and fail open with a kill switch.

## When an agent should reach for OpenJev instead of reasoning in natural language

Reach for `openjev_read` (routing schema below) when:
- A harness, hook or orchestrator must pick a model or effort level programmatically, before spawning a subagent or issuing the next call.
- The agent is about to spend frontier or high-effort tokens on something that looks mechanical (rename, typo, format, list, summarize, commit).
- The agent is about to delegate many subtasks and wants a cheap per-subtask tier (fan-out).
- A routing decision must be logged and thresholded (a probability is auditable; "I think this is easy" is not).

Do not use it to decide whether the task is safe or allowed (see use cases 03/04), and do not treat it as a substitute for actually reading the task: it routes on the summary you give it.

## Recommended question schema (copy-pasteable)

State: a 1 to 4 sentence task summary in the form `Task summary: <verb> <object>; <scope>; <known cause or spec status>; <tests/constraints>`. Include facts that drive difficulty (root cause known or unknown, number of components, spec clarity), not the whole prompt.

```json
{
  "model": "openjev-latest",
  "state": "Task summary: Add optional limit and offset query params to GET /orders following the pattern in GET /customers. Add two unit tests. The spec is clear.",
  "questions": {
    "tier": {
      "type": "choice",
      "instructions": "Which model tier should handle this next agent turn? Pick the cheapest tier that will still do it correctly.",
      "criteria": {
        "haiku": "Trivial or mechanical: rename, typo, formatting, listing, simple lookup, one-line edit",
        "sonnet": "Routine engineering: implement a well-specified feature, write tests, a bug fix with a known cause, small refactor",
        "opus": "Hard: subtle concurrency or security reasoning, architecture or migration design, unknown root cause across many components"
      }
    },
    "deep": {
      "type": "noul",
      "instructions": "Does this task require deep multi-step reasoning, where a fast shallow answer would likely be wrong?",
      "criteria": {
        "true": "Requires careful multi-step reasoning, tradeoff analysis, or hunting a subtle bug",
        "false": "Mechanical, well-specified, or a lookup; no deep reasoning needed"
      }
    },
    "cx": {
      "type": "score",
      "instructions": "How complex is this task for a coding agent?",
      "criteria": [
        "trivial: one mechanical edit",
        "easy: small well-specified change",
        "moderate: several files, clear approach",
        "hard: subtle bugs or design decisions",
        "expert: deep reasoning over many interacting components"
      ]
    }
  }
}
```

Optional finer-grained effort question (5 options, tested):

```json
"effort": {
  "type": "choice",
  "instructions": "What thinking effort should the next turn use?",
  "criteria": {
    "none": "No extended thinking; answer directly",
    "low": "Brief reasoning, a couple of steps",
    "medium": "Moderate reasoning, weigh a few options",
    "high": "Extended reasoning, careful verification",
    "max": "Maximum reasoning budget for a very hard open-ended problem"
  }
}
```

Note: `score` returns the expected level, 0-indexed (`cx` of 0 is trivial, 4 is expert), as a float.

## Observed results (live server, openjev-latest)

| Case | tier | deep P(yes) | cx |
|---|---|---|---|
| rename variable | haiku (1.00) | 0.00 | 0.11 |
| README typo | haiku (1.00) | n/a | 0.00 |
| list/count log files | haiku (1.00) | 0.00 | n/a |
| pagination + tests, clear spec | sonnet (0.43) | 0.00 | 1.01 |
| known-cause None guard | sonnet (0.99) | 0.01 | n/a |
| monolith to event-sourcing migration | opus (1.00) | 1.00 | 4.00 |
| 1-in-20000 double charge heisenbug | opus (1.00) | 1.00 | 3.96 |
| JWT alg-confusion diff | opus (0.79) | 1.00 | n/a |
| prettier + commit | haiku (1.00) | 0.00 | 0.59 |
| flaky timing test (ambiguous) | opus (0.43) | n/a | 3.02 |
| summarize changelog | haiku (1.00, conf 0.98) | 0.00 | n/a |
| hard scheduling algorithm, think=256 | opus (1.00) | 1.00 | n/a |
| unit tests for slugify, samples=3 | sonnet (0.99, conf 0.93) | 0.00 | n/a |
| effort, routine pagination feature | low (0.92, conf 0.79) | | |

Trivial vs hard separate cleanly: `deep` is at or below 0.01 for every mechanical task and at or above 0.999 for every hard one. The middle band (sonnet) is the fuzzy zone.

## Phrasing rules learned

1. **Describe every tier in `criteria`, with examples of task kinds.** Tier names alone ("haiku/sonnet/opus") tell the model nothing about what each tier is for. Describing each tier by task type made all tier reads pass on the first run. Before: `criteria: {"haiku": null, "sonnet": null, "opus": null}` with "Which model?". After: the descriptions in the schema above. Measured on the README typo: bare phrasing routed to **sonnet** (P 0.67, confidence 0.36) and gave haiku only P 0.31; with described tiers it routes to haiku at 1.00. On the pagination task bare phrasing gave sonnet 0.87 (confidence 0.63) with `deep` "Is this hard?" at 0.19, versus 0.43 confidence with described tiers, so described tiers sharpen the trivial end, not necessarily the middle.
2. **Ask "cheapest tier that still does it correctly".** It biases ties down, which is what a cost router wants.
3. **Define `deep` with `criteria.true/false` and say "a fast shallow answer would likely be wrong".** This anchors on failure risk, not on task size. A long but mechanical task (format 40 files) should still be `false`.
4. **Put difficulty drivers in the state.** "Root cause known/unknown", "spec is clear", "N interacting components" moved answers far more than the task verb.
5. **Use `score` for a numeric complexity gate and keep level labels self-describing** ("trivial: one mechanical edit"), so a level index is meaningful when logged.
6. **Test an extension on its own task, not a clone.** `samples:3` was first tested on the same rename text as case 01, which proved nothing new; it now runs on a different routine task. Similarly, a tier assertion should match the case description: the changelog summary is asserted `haiku` only (P 1.00), not `haiku or sonnet`.
7. **One decision per question.** Do not ask "is this hard and which model" in one noul; keep tier, deep and complexity separate and combine in the harness.

## Thresholds for acting on answers

Suggested harness policy (all values were consistent with the tested labels):

| Condition | Action |
|---|---|
| `deep.noul >= 0.7` | Use high effort / extended thinking. |
| `deep.noul <= 0.15` and `tier == haiku` and `tier.confidence >= 0.9` | Route to haiku, no extended thinking. |
| `tier.confidence < 0.5` (seen on the sonnet feature case at 0.43 and the flaky test at 0.43) | Low confidence: round UP one tier, or fall back to the safe default. |
| `cx.score >= 3.0` | Treat as opus-class even if `tier` disagrees. |
| `cx.score` in [1.0, 2.8] and `deep.noul <= 0.3` | Sonnet, medium effort. |

Rounding up on low confidence is deliberate: a wrongly cheap route fails the task, a wrongly expensive route only costs tokens.

## Safe default, fail-open and kill switch (MCP layer must implement)

- **Safe default:** sonnet, medium effort (or whatever the harness would have used without routing).
- **Fail open** on: connection error, timeout, HTTP 5xx, 429 or 529 (retry once with backoff, then default), and any response missing an expected answer key.
- **400 unknown model** (tested: `jev-1.13.0` gives `400` with "Unknown model"): do not retry; fall back to `openjev-latest` once, then to the safe default. Pinned Jev versions are not valid names on OpenJev.
- **422 validation** (tested: a `choice` with no `criteria`): a schema bug on the caller's side; never retry with the same payload. The MCP layer should validate that every choice/score has `criteria` before sending, and treat 422 as a bug to log plus fall back to the default.
- **Kill switch:** an env var such as `OPENJEV_ROUTING=off` makes the router return the safe default without any network call.
- **Log every decision:** state hash, three answers with probabilities, chosen model/effort, latency, and whether the default was used. This is what makes the router auditable and lets thresholds be tuned.
- Set a short client timeout (2 s is plenty for a text read). Under load from other agents on the same server, reads took 1.5 to 18 s during testing; a routing call that exceeds the timeout should fail open, not block the turn.

## Extensions relevant here

- `samples: 3` (tested): averages three reads; use only for borderline cases, since it multiplies cost. On clear cases it changes nothing.
- `think: 256` (tested): still produced the correct hard-task answer. Not needed for routing in general; reserve it for summaries where difficulty is buried in detail. It costs input tokens twice plus output tokens.
- `sequential` and `images`: not relevant to routing.

## Limitations

- Routes on the summary only. A misleadingly short summary ("fix the bug") gets a cheap route. The agent or harness must include the difficulty drivers.
- The sonnet middle band is soft: tier confidence was 0.43 on a clean feature task and on an ambiguous flaky test (the latter routed to opus, cx 3.0). Use confidence and `cx` together, and round up.
- The 5-way effort choice is only tested on a routine task (low, 0.92), and earlier trivial and heisenbug variants gave only moderate confidence (0.51 to 0.65) at the clear-cut ends, so those were dropped as near-duplicates of the tier/deep cases; prefer the `deep` noul plus `cx` score for effort, and use the 5-way `effort` choice only as a secondary signal.
- The model has no knowledge of the target model's actual capability or the repo. Tier descriptions encode a policy; tune them per team.
- Latency measured on a shared server ranged widely; do not assume 0.4 s under contention.

## Test inventory (17 cases, all pass)

01 trivial rename, 02 README typo, 03 read-only listing, 04 routine feature, 05 known-cause bugfix, 06 architecture migration, 07 heisenbug root cause, 08 security diff, 09 format and commit, 10 flaky test (hard/ambiguous, decidable), 11 changelog summary (haiku only), 12 `think` extension, 13 `samples:3` extension on a distinct routine task (slugify unit tests), 14 effort 5-way on a routine feature, 16 error unknown model (400), 17 error missing criteria (422). Ids 15 is retired (its heisenbug task duplicated 07). Dedup rule applied: one task text per case; extensions and alternative question shapes are tested on different tasks, never on a copy of an existing one.
