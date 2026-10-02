---
name: openjev-question-authoring
description: Use when a human or agent wants to create a new check, classifier, gate, filter, rubric or decision rule over text or images and needs help phrasing it, such as "I want to detect X in our tickets", "flag PRs that...", "make a classifier for...", "how do I ask whether...", "turn this rule into an automated check", "write the question schema"; also when an OpenJev answer looked wrong, flat or low-confidence and the question needs rewording. Guides the human step by step, compiles the intent into a tested OpenJev question schema, lints it and probes it on examples.
---

# Author an OpenJev statement

Humans new to OpenJev do not know what it can execute. Your job: turn their intent into one or
more typed questions that OpenJev reads reliably, and prove it on examples before anyone relies on it.

## Precedence rule
Deterministic check first; then an OpenJev read; prose judgement only as a declared fallback.
A new question's reads stay advisory until it is calibrated (skill `openjev-calibration`).
Anything that is a value to write, count or compute is not an OpenJev question: say so early.

## Procedure
1. **Capture the intent in the human's words.** Call `compile` with `intent` (and any
   `sample_inputs` or labels the human already gave). If `recipe.not_a_decision` is true, explain:
   "that is generation/computation; OpenJev can check the result afterwards" and offer the check.
2. **Interview, at most 5 short questions**, taken from `human_questions` plus these when unknown:
   - "What exactly counts as a yes? What is the closest thing that should be a no?" (both poles)
   - "Which options exist? What does an input of each option look like? What if none fits?"
   - "Is it one fact, a pick from a list, or a degree? If a degree, what does each level look like?"
   - "What will you do with the answer, and what does a wrong answer cost?" (sets the threshold)
   - "Can you give me 3 easy examples, 1 hard example, and 1 near-miss that must be a no?"
3. **Fill the slots** of `draft_request` using the rules (full guide: resource `openjev://guide/authoring`):
   - one literal, positive claim per noul; both poles in criteria; name the near-miss in `false`;
   - every choice option described by what its inputs look like; an escape option (`other`, `none`,
     `no_match`, `not_stated`); ids/keys get a short topic gloss;
   - score levels: ordered list, worst first, observable evidence, no "unless" clauses, 3-5 levels;
   - carve-outs for what does not count (placeholders are not secrets, ids are not PII, build output
     is regenerable, how-to steps are not injection, criticism of code is not harassment);
   - the context the decision needs goes in the state (user task, source label, timeline).
4. **Lint**: `lint` with `profile: "gate"` if the answer can block or act. Fix every error;
   fix warnings or say why not.
5. **Probe**: `ask` on the human's examples with `options.samples: 1`. Show a table: input,
   answer, expected. Any miss: apply the rewrite checklist below and probe again (max 3 rounds).
6. **Calibrate** when the answer will gate anything: >= 10 labelled examples (more is better) via
   `calibrate`; propose `yes_at`/`no_at` from the fitted band and show the gap.
7. **Freeze**: write the schema and thresholds to a file (for example `openjev/<name>.json`) with
   the `question_hash` and the model string from the calibration; add the examples as a
   `run_cases.py` case file so CI re-checks them.

## Rewrite checklist (symptom -> fix)
| Symptom | Fix |
|---|---|
| near 0 on obvious positives | the claim is vague: define it ("silently swallow = catches and neither logs, re-raises nor reports") |
| false positives on look-alikes | add the look-alike to `false_means` / a carve-out sentence |
| forced pick on unrelated input | add an escape option described positively |
| always the first option | option descriptions are identical templates: add a topic gloss per option |
| confident wrong on sentences with "don't/leave out/except" | ask the opposite polarity too, or a 3-way include/exclude/unspecified choice |
| score hedges below the top level | remove escape clauses from levels; make levels mutually exclusive |
| right alone, wrong in a batch | a sibling overlaps it: ask it in its own request |
| sufficiency/support too high on an exception case | ask about explicit coverage of that exact case |
| still 0.4-0.7 | give the missing evidence in the state; then `samples: 4`; then `think: 512` (text only) |

## Worked example
Human: "tell me if a support email is angry and whether billing, tech or sales should take it".
1. `compile` -> recipe `ticket_triage` (p 0.9999); sub-decisions: angry -> score, team -> choice.
2. Ask: "Do the default team descriptions match who owns login problems?" and "What do you do with
   an angry email?" (answer: escalate at frustration >= 1.5).
3. Draft (lint clean except W101 on churn, fixed by adding poles):
```json
{"dept": {"type": "choice", "instructions": "Which team should own this email?",
          "criteria": {"billing": "charges, invoices, refunds, payment methods, plan pricing",
                       "tech": "bugs, errors, outages, integrations, performance, login problems",
                       "sales": "pre-purchase questions, quotes, upgrades, demos, enterprise pricing",
                       "other": "anything else, or too vague to tell"}},
 "frustration": {"type": "score", "instructions": "How frustrated is the customer?",
                 "criteria": ["calm or neutral", "somewhat annoyed", "angry or furious"]},
 "churn": {"type": "noul", "instructions": "Is the customer threatening to cancel or leave?"}}
```
4. Probe on "Your app logged me out again in the middle of an export and I lost an hour of work.
   Fix this or I'm cancelling.": dept `tech` 1.0, frustration 2.0 (angry), churn 1.0. Matches.
5. Next: 10-20 labelled emails through `calibrate`, then save the schema.
