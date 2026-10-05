---
name: openjev-calibration
description: "Fits and audits thresholds for OpenJev questions and recipes on labelled examples with calibrate (accuracy, separation, fitted thresholds, reliability, drift between audits) instead of guessed cut-offs. Use when choosing or changing a numeric threshold on an OpenJev answer (auto-close, escalate, block, route, merge), when asked how reliable a question is, when building a labelled eval set or CI regression test for a question set, after rewording a question, or when openjev-latest resolves to a new model version."
---

# Calibrate and audit OpenJev questions

Tools live on the `openjev` MCP server as `mcp__openjev__<tool>` (with the openjev-mcp plugin:
`mcp__plugin_openjev-mcp_openjev__<tool>`). If they are missing, load skill `openjev-data-prep`
(Connect) and do not start servers yourself. Confirm `mcp__openjev__status` returns `healthy: true` before the first call.

## Precedence rule
Measured thresholds over defaults; defaults over intuition. A deterministic check still beats any
threshold. Until a question is calibrated on this project's data, its reads are advisory.

Paths (case files, labels, outputs, audit records) must be absolute and inside the MCP server's
allowed roots (its working directory plus `OPENJEV_MCP_ROOTS`); if refused, see openjev-data-prep Connect.

## Checklist
Copy and tick off:

```
- [ ] 1 Labelled items collected (>= 12 smoke test, >= 100 before claiming a band), hard negatives included
- [ ] 2 Production questions used byte-identical (the threshold belongs to its question_hash)
- [ ] 3 mcp__openjev__calibrate run (examples, case_file or from_batch) with store set
- [ ] 4 Read n, separable, gap, t_fit, suggested_band, overlap_ids, zero_error_upper_bound_95
- [ ] 5 Not separable: precision/coverage at several thresholds reported, overlap sent to a human, question reworded, re-run
- [ ] 6 Audit record stored and kept with the schema; re-run with compare_to after rewording or a model change
```

## Choose the input
Give exactly one source per call:

| Source | Use when | Shape |
|---|---|---|
| `questions` + `examples` | labels are in your head or a list | `examples[{id?, state, label{question_id: value}}]` |
| `recipe` + `examples` (recipe ids come from `mcp__openjev__recipe`) | calibrate a built-in recipe | same `examples`, `recipe` id instead of `questions` |
| `case_file` | cases already exist in the case-file format below | absolute path to a JSON file |
| `from_batch` | rows were already read by `mcp__openjev__batch`; no new reads | `{output_path, labels_path, label_fields{question_id: column}, id_field?}` |

Label values: noul `true`/`false` (or yes/no, 1/0); choice the option key; score the 0-indexed
level. Optional: `holdout` (fraction kept as a drift canary, never used to fit), `store` (audit
record path, `.json`), `compare_to` (earlier record), `target {min_precision, min_coverage,
max_errors}`, `concurrency` 1-4, `max_items_per_call` (default 25) with `cursor`.

### Inline examples (verified call)
<!-- openjev-call: calibrate -->
```json
{"questions": {"escalate": {"type": "noul", "instructions": "Does the ticket report a production outage or data loss that needs an on-call engineer now?", "criteria": {"true": "production is down, data is lost or corrupted, or customers are blocked right now", "false": "a question, a feature request, a cosmetic bug or a slow-but-working page"}}},
 "examples": [
  {"id": "t1", "state": "USER MESSAGE: Checkout returns 500 for every customer since 14:00. Revenue is stopped.", "label": {"escalate": true}},
  {"id": "t2", "state": "USER MESSAGE: The nightly import deleted all rows in the orders table.", "label": {"escalate": true}},
  {"id": "t3", "state": "USER MESSAGE: Login is down for everyone in the EU region.", "label": {"escalate": true}},
  {"id": "t4", "state": "USER MESSAGE: How do I change the colour of the dashboard header?", "label": {"escalate": false}},
  {"id": "t5", "state": "USER MESSAGE: Could you add dark mode to the mobile app?", "label": {"escalate": false}},
  {"id": "t6", "state": "USER MESSAGE: The checkout page feels a bit slow today but orders go through.", "label": {"escalate": false}},
  {"id": "t7", "state": "USER MESSAGE: Typo in the footer: 'Privacy Policcy'.", "label": {"escalate": false}}]}
```

### Case-file format
A JSON document `{"cases": [...]}`. Each case: `id`, `request {state, questions, ...options}`,
`expect {answers {question_id: rule}}`. Rules become labels: `{"noul_gte": 0.8}` is true,
`{"noul_lte": 0.2}` is false, `{"choice": "key"}` is that option. All cases must carry the same
`questions` (one question set per file); cases without such an expectation are skipped with a warning.

<!-- openjev-example -->
```json
{"cases": [
  {"id": "esc-01", "request": {"state": "USER MESSAGE: Checkout returns 500 for every customer since 14:00.", "questions": {"escalate": {"type": "noul", "instructions": "Does the ticket report a production outage or data loss that needs an on-call engineer now?", "criteria": {"true": "production is down or data is lost", "false": "a question, request or cosmetic issue"}}}}, "expect": {"answers": {"escalate": {"noul_gte": 0.8}}}},
  {"id": "esc-02", "request": {"state": "USER MESSAGE: Typo in the footer: 'Privacy Policcy'.", "questions": {"escalate": {"type": "noul", "instructions": "Does the ticket report a production outage or data loss that needs an on-call engineer now?", "criteria": {"true": "production is down or data is lost", "false": "a question, request or cosmetic issue"}}}}, "expect": {"answers": {"escalate": {"noul_lte": 0.2}}}}
]}
```

Call it with `{"case_file": "/abs/path/to/cases.json"}`.

### From a finished batch
Read the rows once with `batch` (skill `openjev-data-records`), keep the output JSONL, write a
labels file (CSV or JSONL, an `id` column plus one label column per question), then:

<!-- openjev-call: calibrate -->
```json
{"from_batch": {"output_path": "/abs/path/to/run.jsonl", "labels_path": "/abs/path/to/labels.csv", "label_fields": {"escalate": "escalate_label"}}, "store": "/abs/path/to/audits/escalate-1.json"}
```

No new reads are made; the questions come from the batch header.

## Read the result
Output fields, in order of use:

- `n`, `model_resolved`, `question_hash`, `warnings` (for example the rule-of-three smoke-test note).
- `per_question.<q>`: `n`, `n_pos`, `n_neg`, `accuracy_at_0.5`, `separable` (true when every
  positive reads above every negative), `max_negative`, `min_positive`, `gap` (min_positive minus
  max_negative), `t_fit`, `suggested_band {no_at, yes_at}`, `overlap_ids`, `most_borderline`
  (item id), `zero_error_upper_bound_95` (0 errors in n bound the error rate near 3/n),
  `calibration {bins, brier, ece}`, `distributions`. Score questions add `ladder_monotonic`.
- `items[{id, label, p}]`: the per-example reads.
- `stored`: the audit record path, present only when `store` was set. Read it later through the
  MCP resource `openjev://audits/{question_hash}`.
- `next_cursor` and `status` : present when the run was chunked; call again with the same arguments
  plus `cursor` until it is absent.

## Interpret
1. `separable: true` with a wide `gap` and n >= 100: adopt `suggested_band` as `no_at`/`yes_at`
   and stay conservative for irreversible actions (>= 0.95).
2. Not separable: report precision/coverage at several thresholds, send `overlap_ids` to a human,
   reword the question (skill `openjev-question-authoring`) and re-run on the same examples.
3. Scores: check `ladder_monotonic`; use the level distribution to find borderline items that a
   saturated noul hides.
4. `calibration.ece` above 0.1, or a bin whose `acc` is far below its `conf`: the raw probability
   is not a usable confidence for this question; gate on fitted thresholds only.
5. Drift: after rewording or when `openjev-latest` resolves to a new model, re-run with
   `compare_to` set to the stored record; it reports flips and a changed resolved model. Fail CI on
   any labelled item that flips sides or a gap below 0.3.

## Facts to respect
- Identical requests return identical numbers; 15 replays prove nothing. Use paraphrased states or
  `options.samples` > 1 on borderline items. Exception: `think` reads vary between runs; read twice.
- Log the response `model` (for example `openjev-0.1`), not the alias.
- Near-saturated fixtures (0.00/1.00) validate plumbing, not the decision boundary.
- Hold out a canary set that is never used for fitting (`holdout`).

## Worked example
The inline call above (7 tickets, 3 escalate, 4 not) returned, verified live: `n` 7,
`accuracy_at_0.5` 1, `separable` true, `min_positive` 0.999, `max_negative` 0.0001, `gap` 0.9989,
`suggested_band {no_at: 0.05, yes_at: 0.95}`, `most_borderline` t2, `zero_error_upper_bound_95`
0.429, and the warning "n=7: smoke test only". Clean but tiny: the 43% error bound says collect 100+
items, including hard negatives, before trusting the band.
