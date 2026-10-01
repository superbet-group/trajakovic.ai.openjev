# Usage type 02: Confidence-gated act / ask / escalate

Test file: `tests/cases/02-confidence-gated-action.json` (19 cases, 18 pass, 1 KNOWN LIMITATION fails by design).
Run: `.venv/bin/python docs/mcp-skill-spec/tests/run_cases.py docs/mcp-skill-spec/tests/cases/02-confidence-gated-action.json`

## Purpose

The decision is not the label but whether to act on it. Three outcomes: ACT (probability clears the bar for this action), ASK (the request is ambiguous, or the answer sits in the grey zone), ESCALATE (below the handoff floor, or the action is too risky for automation). OpenJev returns calibrated probabilities read from the model's distribution, so the agent can compare a number against a per-action threshold table in code instead of writing "I am fairly sure" in prose.

## When a coding agent should reach for OpenJev here

Use OpenJev instead of reasoning in natural language when:

- The agent is about to write one global `threshold = 0.5` (or `if confident:`). Replace it with a per-action table (see below).
- The agent is about to guess on an ambiguous request ("clean up old branches", "reset the database", "fix that thing from yesterday"). Ask OpenJev a proceed/ask question first.
- The agent wants to apply a suggested fix, migration, refund, delete or merge and its own justification is "looks right to me". Ask a noul "is it correct" and require >= 0.95 for irreversible work.
- The agent wants to say "I am 90% sure". A number from OpenJev is reproducible; a number the agent invents is not.

Do not use it when a deterministic check exists (run the tests, check the schema, diff the file). Gate on facts first, on OpenJev second.

## Recommended question schema (copy-paste)

One request, three independent reads: what action, how risky, is it specified enough to act on.

```json
{
  "model": "openjev-latest",
  "state": "<verbatim user message + the concrete command or diff the agent is about to run>",
  "questions": {
    "action": {
      "type": "choice",
      "instructions": "Which single action does the latest message ask the assistant to perform?",
      "criteria": {
        "check_balance": "Read the current balance of an account; no money moves",
        "approve_transfer": "Approve or execute a transfer of money to another account or person",
        "other": "Anything else, or the request is unclear"
      }
    },
    "proceed_or_ask": {
      "type": "choice",
      "instructions": "Should the agent proceed with the request as written, or ask a clarifying question first?",
      "criteria": {
        "proceed": "Only one reasonable interpretation, or all interpretations have the same safe effect; act now",
        "ask": "Several plausible interpretations with different (possibly destructive) consequences; ask first"
      }
    },
    "blast": {
      "type": "score",
      "instructions": "How large is the blast radius if this command runs?",
      "criteria": [
        "none: read-only, no side effects",
        "small: changes one local file, trivially restorable",
        "large: changes shared state, restore is slow or partial",
        "catastrophic: irreversibly destroys production data"
      ]
    },
    "correct": {
      "type": "noul",
      "instructions": "Is the proposed fix correct for the bug described, without introducing a new bug?",
      "criteria": {"true": "The fix is correct", "false": "The fix is wrong or incomplete"}
    }
  }
}
```

Ask only the questions that matter for the current decision; each extra question costs a little latency (about 0.4 s for a typical read, 3-6 s observed when the shared server was busy).

## Threshold table (defaults; tune per project)

| Action class | Signal | Act when | Ask when | Escalate to human when |
|---|---|---|---|---|
| Read-only (list, get, check_balance) | choice `confidence` or top probability | >= 0.5 | 0.3 to 0.5 | < 0.3 |
| Reversible write (edit local file, create branch) | choice top probability / noul | >= 0.7 | 0.5 to 0.7 | < 0.5 |
| Irreversible or money-moving (transfer, delete, force push, refund) | choice top probability AND proceed_or_ask = proceed at >= 0.8 | >= 0.85 (0.95 for auto-apply of code fixes) | 0.6 to 0.85 | < 0.6, or blast score >= 2.5 |
| Any action | `proceed_or_ask` choice: P(ask) | P(ask) < 0.2 | P(ask) >= 0.2 | never auto-escalate on this alone |

For a noul there is no `confidence` field. Use distance from 0.5: `d = abs(p - 0.5) * 2` (0 = coin flip, 1 = certain). For the yes side, act when `p >= bar`; for the no side, treat `p <= 1 - bar` as a confident "no". Anything in between is the grey zone: ask or escalate. The risk tier is chosen by code (or a `blast`/`risk` question), never by the same read that produces the label.

For a choice, `confidence = 1 - H(p)/ln K` (normalised entropy), so it depends on the number of options; with K = 2 a 0.9/0.1 split is confidence 0.53. Prefer the top probability (`probabilities[choice]`) when comparing against a fixed bar, and use `confidence` only for a rough "is the distribution peaked" test.

## Phrasing rules learned (with before / after)

1. **Ambiguity is its own question.** Before: read a domain choice ("what does 'reset the database' mean?" with local/staging/production/restart) and treat low confidence as "ambiguous". Observed: P(local_dev_db) = 0.9986, confidence 0.99, i.e. the model picks one reading with full confidence. After: ask `proceed_or_ask`, a two-option choice whose descriptions say "several plausible interpretations with different consequences" versus "only one reasonable interpretation". Observed P(ask) = 0.99 for "reset the database", P(proceed) >= 0.8 once the request names `./dev.db` and `make seed`. Test: gate-11, gate-15; failing counterpart: gate-19 (KNOWN LIMITATION).
2. **Put the deciding condition in the criteria descriptions.** For "is it specified" noul questions, give `criteria: {true: "All of action, account and amount are stated", false: "At least one of ... is missing or only implied"}`. The model then scores the checklist rather than a vibe. Tests: gate-03, gate-04, gate-05 (0.0, 0.0, 1.0).
3. **Ask "would it be safe to act without asking?", not "is it clear?".** Bare "Is this clear?" reads the tone of the text; the safety framing reads whether a wrong guess would matter.
4. **Score levels carry their own definitions.** Before: `criteria: ["none: read-only", "small: ...", "large: ...", "catastrophic: destroys production data with no backup mentioned"]`. The `rm -rf /var/lib/postgresql/15/main` command scored 2.46 (the "no backup mentioned" clause made the top level hedge). After: `"catastrophic: irreversibly destroys production data"` and mutually exclusive levels: score 2.99, and the read-only `git branch --list` scored <= 0.5. Test: gate-17, gate-18. Never put escape clauses ("unless", "with no ... mentioned") inside a level description.
5. **Unproven is a separate label from wrong.** For the "apply a fix" gate, make the false criterion "Plausible but incomplete, unverified or masks the underlying problem", not just "wrong". A null-check patch with tests not run then lands below 0.5, far under the 0.95 apply bar. Test: gate-16 (with `samples: 8`).
6. **Split compound questions.** `moves_money`, `reversible` and `risk` are three reads. One question like "is this safe and reversible and approved?" mixes three events into one probability. Test: gate-10 (0.95, 0.03, choice irreversible_or_money at confidence 0.98).
7. **`think` is for arithmetic and rule-combination**, e.g. daily limit 5,000, already approved 2,900 + 1,800, new 1,500. Use `think: 512`; expect 10+ seconds on a busy server. Test: gate-12 (P(allowed) = 0.0).

## How the answers behave (observed)

- Noul answers are close to binary in practice: 0.000 to 0.003 or 0.94 to 1.0 for clear cases. Grey-zone values (0.2 to 0.4) appear mainly with thin evidence ("null check, tests not run" gave 0.26 to 0.31). So a noul gate mostly separates confident yes / confident no; do not expect a smooth dial.
- Monotonicity holds coarsely, not strictly. Weak hearsay evidence gave 0.02, any concrete deploy-log evidence gave 0.88 to 1.0, and adding more evidence did not always raise the value (0.98 with the deploy log alone vs 0.88 with timing added). Test only weak-below vs strong-above (gate-08 vs gate-09), never a strict ordering of several close levels.
- `samples` (2 to 8) smooths noise for borderline reads and adds no output tokens; use it on the money-moving check, not on every call.

## Limitations

- **KNOWN LIMITATION (gate-19):** a domain choice's confidence does not flag ambiguity. Ambiguous input still yields a peaked distribution for the "default" reading. Detect ambiguity with a dedicated proceed/ask question.
- The choice confidence depends on K; do not compare it across questions with different option counts.
- A noul has no confidence field; the 0.5-distance rule is a convention, not a calibrated interval.
- Calibration is not guaranteed: a 0.97 is "the model is very sure", not "97% of such cases are correct". Fit thresholds on labelled examples from your own project (usage type 24) before trusting 0.85 or 0.95 with real money.
- Score questions can ignore the state on some inputs (README caveat); the blast scale here was verified on two contrasting commands only.
- The state must contain the concrete command or diff. A request text alone ("delete the branches") without the resolved command hides the real blast radius.
- The MCP layer must reject a choice with empty `criteria` (server returns 400 `Choice question must have at least one choice`, gate-13) and an unknown model (400 `Unknown model: ...`, gate-14) before or after sending; both are covered as error cases.

## Test map

| Case | What it checks | Result |
|---|---|---|
| gate-01 | read-only intent, confidence >= 0.85 | pass (0.985) |
| gate-02 | explicit money transfer clears 0.85 | pass (0.995) |
| gate-03 | vague money request not fully specified | pass |
| gate-04 | "clean up old branches" is ambiguous | pass |
| gate-05 | pinned-down branch cleanup is specified | pass |
| gate-06 | correct fix >= 0.95 | pass (0.997) |
| gate-07 | wrong fix <= 0.05 | pass |
| gate-08 / 09 | weak vs strong evidence (monotonicity) | pass |
| gate-10 | per-action risk table, multi-question | pass |
| gate-11 | proceed/ask on "reset the database", `samples` 4 | pass |
| gate-12 | `think` 512, daily-limit arithmetic | pass |
| gate-15 | proceed/ask on a fully specified reset | pass |
| gate-16 | thin evidence fails the 0.95 bar, `samples` 8 | pass |
| gate-17 / 18 | score blast radius high vs low | pass (2.99 / low) |
| gate-19 | KNOWN LIMITATION: domain-choice confidence as ambiguity | fails by design |
| gate-13 / 14 | error cases: empty criteria, unknown model | pass |

## Final pass count

18 of 19 pass. The single failure, gate-19, is the intentionally kept KNOWN LIMITATION. No threshold was loosened; two phrasing rewrites were made (rule 4 score levels; rule 1 replaced a choice-confidence ambiguity test with a proceed/ask question).
