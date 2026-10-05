---
name: openjev-question-authoring
description: "Turns a plain-language intent into a tested OpenJev question set: interviews for the decision, compiles it with compile, lints it until valid, probes it on examples and hands it to calibration. Use when a new check, classifier, gate, filter, rubric or decision rule over text or images must be phrased ('detect X in tickets', 'flag PRs that...', 'turn this rule into an automated check', 'write the question schema'), or when an OpenJev answer looked wrong, flat or low-confidence and the question needs rewording."
---

# Author an OpenJev question set

Tools live on the `openjev` MCP server as `mcp__openjev__<tool>` (with the openjev-mcp plugin:
`mcp__plugin_openjev-mcp_openjev__<tool>`). If they are missing, load skill `openjev-data-prep`
(Connect) and do not start servers yourself. Built-in recipes are listed by `mcp__openjev__recipe`.

Humans new to OpenJev do not know what it can execute. Turn their intent into typed questions that
OpenJev reads reliably, and prove it on examples before anyone relies on it. The full authoring
guide is the MCP resource `openjev://guide/authoring` (read it with the MCP resource reader, server
`openjev`).

## Precedence rule
Deterministic check first; then an OpenJev read; prose judgement only as a declared fallback.
A new question's reads stay advisory until it is calibrated (skill `openjev-calibration`).
Anything that is a value to write, count or compute is not an OpenJev question: say so early.

## Checklist
Copy and tick off:

```
- [ ] 1 Intent captured; mcp__openjev__compile called (recipe.not_a_decision checked)
- [ ] 2 Interview done (max 5 questions), slots of draft_request filled
- [ ] 3 mcp__openjev__lint with profile "strict" ("gate" if the answer can block or act)
- [ ] 4 Every error fixed, every warning fixed or justified; lint re-run until valid: true
- [ ] 5 Probed with mcp__openjev__ask on 3 easy + 1 hard + 1 near-miss example
- [ ] 6 Misses fixed with the rewrite table; probe re-run (max 3 rounds)
- [ ] 7 If it will gate anything: handed to openjev-calibration (>= 10 labelled examples)
- [ ] 8 Schema + thresholds + question_hash + model string saved with the project
```

## Procedure
1. **Capture the intent in the human's words.** Call `mcp__openjev__compile` with `intent` (and any
   `sample_inputs`, `labels` or `labelled_examples` the human already gave; `sub_decisions` splits
   a multi-part intent). Output you use: `recipe {id, p, not_a_decision}`, `draft_request`
   (questions with `<SLOT:...>` placeholders), `slots`, `human_questions`, `lint`, `next_steps`.
   If `recipe.not_a_decision` is true, explain: "that is generation/computation; OpenJev can check
   the result afterwards" and offer the check.
2. **Interview, at most 5 short questions**, taken from `human_questions` plus these when unknown:
   - "What exactly counts as a yes? What is the closest thing that should be a no?" (both poles)
   - "Which options exist? What does an input of each option look like? What if none fits?"
   - "Is it one fact, a pick from a list, or a degree? If a degree, what does each level look like?"
   - "What will you do with the answer, and what does a wrong answer cost?" (sets the threshold)
   - "Can you give me 3 easy examples, 1 hard example, and 1 near-miss that must be a no?"
3. **Fill the slots** of `draft_request` using the rules:
   - one literal, positive claim per noul; both poles in `criteria`; name the near-miss in `false`;
   - every choice option described by what its inputs look like; an escape option (`other`, `none`,
     `no_match`, `not_stated`); keys get a short topic gloss;
   - score levels: ordered list, worst first, observable evidence, no "unless" clauses, 3-5 levels;
   - carve-outs for what does not count (placeholders are not secrets, ids are not PII, build output
     is regenerable, how-to steps are not injection, criticism of code is not harassment);
   - the context the decision needs goes in the state (user task, source label, timeline).
4. **Lint loop.** Call `mcp__openjev__lint` with `state` + `questions` (or a full `request` that includes
   `model`), `profile: "strict"`. Read `valid`, `errors[].code`, `warnings[].code`, `fix`. Apply the
   fixes (or take `fixed_request.questions`, which has the autofixes applied), re-lint, repeat until
   `valid: true` and no warnings you cannot justify. Never send an unlinted draft to `ask`.
5. **Probe**: `mcp__openjev__ask` on the human's examples with `options.samples: 1`. Show a table: input,
   answer, expected. Any miss: apply the rewrite table below and probe again (max 3 rounds).
6. **Calibrate** when the answer will gate anything: >= 10 labelled examples (more is better), via
   `mcp__openjev__calibrate`; propose `yes_at`/`no_at` from the fitted band and show the gap.
7. **Freeze**: save the schema and thresholds to a file in the project (for example
   `openjev/<name>.json`) with the `question_hash` and the model string from the calibration. Keep
   the labelled examples as a calibration case file or examples list so the check can be re-run.

## Lint loop: one real before and after
Before. A hand-written draft; `lint` with `profile: "strict"`:

<!-- openjev-call: lint -->
```json
{"profile": "strict", "state": "USER MESSAGE: Your app logged me out again during an export. Fix this or I'm cancelling.", "questions": {"dept": {"type": "choice", "instructions": "Which team should own this email?", "criteria": {"billing": "money questions: invoices, refunds, charges", "tech": "product defects: errors, outages, sign-in trouble", "sales": "buying interest: quotes, demos, upgrades"}}, "churn": {"type": "noul", "instructions": "Is the customer threatening to cancel?"}}}
```

It returns `valid: false` with these findings (real output, abridged):

| Code | Path | Meaning | Fix |
|---|---|---|---|
| W201 | `questions.dept.criteria` | no escape option; a choice cannot abstain | add `"other": "anything else, or too vague to tell"` |
| W101 | `questions.churn` | noul without criteria | add `criteria {true, false}` defining both poles |

(An `E024 model is missing` error appears only when you send a full `request` without `model`;
it is autofixed to `openjev-latest`.) After the fixes the same call is clean:

<!-- openjev-questions -->
```json
{"dept": {"type": "choice", "instructions": "Which team should own this email?", "criteria": {"billing": "money questions: invoices, refunds, charges, payment methods", "tech": "product defects: errors, outages, slow pages, integrations, sign-in trouble", "sales": "buying interest: quotes, demos, upgrades, enterprise plans", "other": "anything else, or too vague to tell"}},
 "frustration": {"type": "score", "instructions": "How frustrated is the customer?", "criteria": ["calm or neutral tone, no complaint about effort or delay", "somewhat annoyed: mild complaint or repeated request", "angry or furious: blame, caps, threats or insults"]},
 "churn": {"type": "noul", "instructions": "Is the customer threatening to cancel or leave?", "criteria": {"true": "says they will cancel, leave, switch provider or stop paying", "false": "complains or asks for help but does not mention leaving"}}}
```

Re-lint result: `valid: true`, `errors: []`, `warnings: []`. Other common codes: W104 compound
question (split it), W103 negation (flip polarity or ask both ways), W202 weak option
descriptions, W301/W304 score levels unordered or bare labels, E013 score `criteria` given as an
object (must be a list), E006 `options` used instead of `criteria`.

## Rewrite table (symptom, fix)
| Symptom | Fix |
|---|---|
| near 0 on obvious positives | the claim is vague: define it ("silently swallow = catches and neither logs, re-raises nor reports") |
| false positives on look-alikes | add the look-alike to `false` / a carve-out sentence |
| forced pick on unrelated input | add an escape option described positively |
| always the first option | option descriptions are identical templates: add a topic gloss per option |
| confident wrong on sentences with "don't/leave out/except" | ask the opposite polarity too, or a 3-way include/exclude/unspecified choice |
| score hedges below the top level | remove escape clauses from levels; make levels mutually exclusive |
| right alone, wrong in a batch | a sibling overlaps it: ask it in its own request |
| sufficiency/support too high on an exception case | ask about explicit coverage of that exact case |
| still 0.4-0.7 | give the missing evidence in the state; then `samples: 4`; then `think: 512` (text only) |

## Worked example
Human: "tell me if a support email is angry and whether billing, tech or sales should take it".
1. `compile` returns recipe `ticket_triage` (p about 0.997) and a `draft_request` with `<SLOT:...>`
   team labels, plus `human_questions` about the team names and the default thresholds.
2. Ask: "Which teams exist and what does each own?" and "What do you do with an angry email?"
   (answer: escalate at frustration >= 1.5).
3. Fill the slots and lint until clean: the question set above.
4. Probe on "Your app logged me out again in the middle of an export and I lost an hour of work.
   Fix this or I'm cancelling.": dept `tech`, frustration near the top level (angry), churn yes.
   Matches the human's expectation.
5. Next: 10-20 labelled emails through `calibrate` (skill `openjev-calibration`), then save the schema.
