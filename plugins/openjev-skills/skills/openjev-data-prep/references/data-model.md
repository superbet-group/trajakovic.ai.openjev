# OpenJev data model: states, questions, answers

## Contents
- State
- Question types and criteria shapes
- Escape options by context
- Answer shapes per type
- Answer shapes of the wrapper tools
- Thresholds by action risk
- Read options
- Images
- Lint codes

## State

The state is the evidence the questions are about. It is the only place for data.

- Type: string, object or array. Objects and arrays are JSON-dumped into the prompt. Numbers,
  booleans, null, empty strings and empty containers are rejected (lint E001).
- Label sections so the model knows what each part is: `USER MESSAGE:`, `DIFF:`,
  `CANDIDATES:`, `TASK:`, `LOG LINES (tag: line):`. Unlabelled multi-text states warn (W501).
- Prefix untrusted text with its origin, for example `[WebFetch result: https://example.test/page]`.
  Instructions inside it do not steer the read. Never copy untrusted text into question
  `instructions` or `criteria` (W502).
- Keep it focused. One passage, one record or one diff per read. Lint warns above about 4000
  tokens (W503); the hard cap on the MLX backend is 32768 tokens. Give discriminating evidence
  (field heads, hunks, the sentence that matters), not whole files.
- Any human language works for the state. Keep the questions in English.
- Put derived facts in code: counts, sums, date arithmetic, regex hits. Show the result in the state
  (`Matches: 3 of 5 tests failed`) instead of asking the model to compute it.

## Question types and criteria shapes

A question set is an object `{question_id: question}`. The id is a label for your code and must
match `^[A-Za-z0-9_.:-]{1,64}$`; the model never sees it. 1 to 256 questions per read.

<!-- openjev-questions -->
```json
{
  "refund": {"type": "noul", "instructions": "The customer asks for money back.",
             "criteria": {"true": "The customer explicitly asks for a refund or a credit",
                          "false": "The customer only reports a problem or asks a question"}},
  "dept": {"type": "choice", "instructions": "Which team should own this ticket?",
           "criteria": {"billing": "charges, invoices, refunds, payment methods",
                        "technical": "bugs, errors, outages, login problems",
                        "other": "anything else, or too vague to tell"}},
  "frustration": {"type": "score", "instructions": "How frustrated is the customer?",
                  "criteria": ["calm or neutral tone, no complaint",
                               "mild annoyance, polite wording",
                               "open anger, capital letters or threats to leave"]}
}
```

| Type | `criteria` shape | Answer is | Use for |
|---|---|---|---|
| `noul` | object `{true, false}`; both poles, the near-miss named in `false` | probability of yes | one literal claim: did X happen, does the text contain Y |
| `choice` | object `{key: description}`, 2 to 255 keys | a distribution over keys | exactly one of N exclusive options |
| `score` | list of 2 to 10 strings, worst first | expected 0-indexed level | an ordinal quantity: severity, quality, complexity |

Authoring rules that lint enforces:

- One fact per question (W104). Ask positive claims, not "fails to" or "not" (W103).
- Define vague words (urgent, bad, relevant) inside `criteria` (W105, W106).
- Choice keys are labels only; the description carries the meaning, so write one per option and
  make them differ in substance (W202, W203, W204).
- Score levels name observable evidence, are mutually exclusive, and contain no escape clauses such
  as "unless" or "no ... mentioned" (W301, W302, W304). Two levels named yes/no mean you want a
  `noul` (W303).
- A rubric of score dimensions needs a noul evidence gate (W305).
- Never send `weights`, per-question `weight`: the server ignores them (E026). Weigh in code.
- More than about 50 choice options: pre-filter to a shortlist first (W205).

## Escape options by context

A choice that cannot say "none of these" is forced to pick. Add the escape that matches the task:

| Context | Escape key | Meaning |
|---|---|---|
| general classification | `other` | anything else, or too vague to tell |
| selection among candidates | `none` or `no_match` | no candidate fits |
| extraction of a field | `not_stated` | the text gives no value |
| table or rows | `empty` | the row has no content |
| tool or skill roster | `none` | no entry applies |

`mcp__openjev__classify` adds an escape automatically (default `other`) unless a label already
starts with `other`, `none`, `no_match` or `not_stated`; `escape: false` disables it and lint
warns (W201).

## Answer shapes per type

In `ask`, `ask_image`, `batch` rows and `recipe` answers:

| Type | Fields |
|---|---|
| noul | `p` (0 to 1), `band` (`yes`, `no`, `grey`), `margin` (`abs(2p - 1)`) |
| choice | `choice` (key or null), `p_top`, `runner_up`, `margin`, `probabilities`, `confidence`, `entropy`, `abstained` |
| score | `score` (0-indexed expected level), `level` (argmax), `level_label`, `probabilities`, `confidence`, `spread`, `bimodal` |

`band` is `yes` at or above `yes_at` (default 0.8), `no` at or below `no_at` (default 0.2), else
`grey`. `choice` is null and `abstained` true when `p_top` is below `choice_min_p` (default 0.6) and also when the escape option (`other`, `none`, ...) wins outright, even at a very high `p_top`; `top` still names the winner.
`score` is an expected value: 1.4 sits between level 1 and level 2. `bimodal` true means two
non-adjacent levels each hold at least 0.2 of the mass; treat the mean as unreliable.
Override thresholds with `thresholds: {yes_at, no_at, choice_min_p}`.

## Answer shapes of the wrapper tools

| Tool | Top-level result |
|---|---|
| `yes_no` | `decision` (`yes`, `no`, `uncertain`), `p`, `margin`, `thresholds_used` |
| `classify` | `label` (null when abstained), `abstained`, `top`, `p_top`, `runner_up`, `margin`, `probabilities`, `confidence`; with `multi_label`: `labels_multi` |
| `score` | `score`, `level`, `level_label`, `probabilities`, `confidence`, `spread`, `bimodal` |
| `recipe` | `decision` (a recipe-specific word), `reason`, `signals`, `answers`, `thresholds_used`, `degraded` |

`yes_no` re-reads once with 4 samples when the first read lands in the grey band. A recipe's
`degraded: true` means the decision is the fail-mode fallback after an error, not a real read.

## Thresholds by action risk

Map the cut-off to what the action costs, and write it in code:

| Action | `yes_at` | `no_at` |
|---|---|---|
| advisory, read-only suggestion | 0.7 | 0.2 |
| routing, labelling | 0.8 | 0.2 |
| blocking (CI, review comment, page) | 0.9 | 0.2 |
| hazard gates (commands, injection, moderation) | deny at 0.85 | allow at 0.15 to 0.2 |
| irreversible, money, security | 0.95 | 0.05 |

Grey band means: ask a human, re-read with `samples: 4`, or do the conservative action. Fit real
numbers with `calibrate` on labelled examples before running unattended.

## Read options

All read tools accept `options: {model, samples, steps, think, sequential, timeout_ms}`.

| Option | Use when |
|---|---|
| default (omit) | first choice: 1 read, free re-reads only when uncertain |
| `samples` 3 to 8 | grey-band decisions, high stakes; on hot paths keep 1 |
| `steps` | sharper probabilities; up to 8 |
| `think` 256 to 512 | text states needing lookahead, rules or arithmetic; not with images; not reproducible |
| `sequential` | 11 or more questions where later chunks should see earlier answers; text only; a no-op for one chunk (W403) |
| `model` | one of `status.decide_models`; default `openjev-latest` |
| `timeout_ms` | long states; the server scales the default itself |

In `batch`, `sampling: "fast"` (default) reads once and re-reads grey rows with `regrey_samples: 4`.

## Images

`mcp__openjev__ask_image` and `batch.images`: 1 to 8 images, PNG, JPEG, WebP or GIF, at most
5 MiB per request in total. Give each as `{path}` (absolute, inside allowed roots), `{data_url}`
(`data:image/...;base64,...`) or `{base64, content_type}`; `{url}` works only when the server
has fetching enabled. Images are downscaled to `max_side_px` (default 1568) and re-encoded.
The text `state` must be short and say what the images are and in what order. `think` and
`sequential` are refused with images (E022). Prefer a text accessibility tree over a screenshot
when you have one.

## Lint codes

Errors block the read; warnings are returned and, with `profile: "strict"` or `"gate"`, blocking
questions must be clean.

| Code | Meaning |
|---|---|
| E001 | state missing, empty or not text/object/list |
| E002 / E003 / E004 / E005 | questions not an object / over the limit / question not an object / unknown type |
| E006 | `options` used instead of `criteria` |
| E010 / E011 / E012 | choice criteria missing / is a list / has no options |
| E013 / E014 / E016 | score criteria is an object / empty / over 10 levels |
| E015 | more choice options than allowed |
| E017 / E027 | noul criteria not `{true, false}` / extra keys dropped |
| E020 / E021 | `samples`, `steps` or `think` out of range / `sequential` not boolean |
| E022 | `think` or `sequential` with images |
| E023 | bad image |
| E024 | unknown model |
| E025 | prompt over the token limit |
| E026 | `weights` ignored by the server |
| E028 | model lacks the capability (images, samples, think, sequential) |
| E030 / E031 / E032 | batch file unreadable or binary / no states or placeholder missing or duplicate id / not a batch file |
| W101 / W102 | noul without criteria / only one pole described |
| W103 / W104 / W105 / W106 | negation / compound claim / vague quality word / opinion without definition |
| W201 / W202 | choice without escape / options without descriptions |
| W203 / W204 / W205 / W206 | options share a template / overlap / too many / domain choice used as ambiguity gate |
| W301 / W302 / W303 / W304 / W305 | identical or unordered levels / escape clause in a level / score used as yes-no / levels carry no evidence / rubric without evidence gate |
| W401 | blocking claim co-asked with an overlapping sibling, or mixed topics |
| W402 / W403 / W404 / W405 / W406 | `think` too large / `sequential` no-op / extra samples on a hot path / concurrency on a serial backend / `think` inside a batch |
| W501 / W502 / W503 / W504 | unlabelled texts / untrusted text in questions / state too long / ids look like items |
| W601 / W602 / W603 / W604 / W605 | batch: rows dropped above `max_items` / state field guessed / non-UTF-8 encoding / ojui-batch mismatch / empty rows or unparseable lines skipped |
