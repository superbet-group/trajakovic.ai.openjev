# OpenJev tool input and output reference

Generated from the server's JSON Schemas and captured live calls. Do not edit by hand. Every tool also returns an MCP `structuredContent` object equal to the output shown. Paths shown as `/abs/path/to/...` stand for absolute paths inside the server's allowed roots.

## Contents

- [`mcp__openjev__ask`](#ask)
- [`mcp__openjev__yes_no`](#yes_no)
- [`mcp__openjev__classify`](#classify)
- [`mcp__openjev__score`](#score)
- [`mcp__openjev__filter`](#filter)
- [`mcp__openjev__batch`](#batch)
- [`mcp__openjev__ask_image`](#ask_image)
- [`mcp__openjev__lint`](#lint)
- [`mcp__openjev__compile`](#compile)
- [`mcp__openjev__calibrate`](#calibrate)
- [`mcp__openjev__recipe`](#recipe)
- [`mcp__openjev__status`](#status)
- [`mcp__openjev__generate`](#generate)
- [`mcp__openjev__batch_results`](#batch_results)

## ask

Tool name: `mcp__openjev__ask`. Send one state and a full question set (1-256 noul, choice and score questions) and get validated answers with derived fields. Use it for several questions about one state, or for think, samples, steps and sequential.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `state` | string or object or array | yes | What the questions are about. Label sections (USER MESSAGE:, DIFF:, CANDIDATES:). Objects/arrays are JSON-dumped into the prompt. |
| `questions` | object | yes |  |
| `questions.<key>` | noul or choice or score object |  |  |
| `options` | object |  |  |
| `options.model` | string |  | Default OPENJEV_MCP_MODEL (openjev-latest). Valid names are those GET /v1/models lists (served and routed models, e.g. openjev-0.1, laya-1.0, verdict-1.4) plus the unlisted alia... |
| `options.samples` | integer |  | N billed reads averaged. 1 = fastest. Omit = 1 read + 3 free re-reads when uncertain.; minimum 1; maximum 32 |
| `options.steps` | integer |  | minimum 1; maximum 8 |
| `options.think` | integer |  | Thought budget in tokens; text-only states. 256-512 for lookahead, rules, arithmetic.; minimum 0; maximum 4096 |
| `options.sequential` | boolean |  | Chunks see earlier chunks' answers. Only matters when the questions span 2+ canvas chunks (above 10 questions, or fewer when score-heavy); text-only. |
| `options.timeout_ms` | integer |  | minimum 100; maximum 600000 |
| `thresholds` | object |  |  |
| `thresholds.yes_at` | number |  | default 0.8; minimum 0.5; maximum 1 |
| `thresholds.no_at` | number |  | default 0.2; minimum 0; maximum 0.5 |
| `thresholds.choice_min_p` | number |  | default 0.6; minimum 0; maximum 1 |
| `lint` | enum(warn\|off) |  | Lint errors always block; warnings are returned, or suppressed with off.; default "warn" |
| `return_raw` | boolean |  | default false |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `answers` | object | yes |  |
| `answers.<key>` | noul or choice or score object |  |  |
| `meta` | object | yes |  |
| `lint` | object |  |  |
| `lint.warnings` | array |  |  |
| `raw` | object |  | server body, when return_raw |

### Example 1: One state, three typed questions (noul, choice, score)

Call:

<!-- openjev-call: ask -->
```json
{
  "state": "USER MESSAGE: My invoice total is wrong, I was charged twice.",
  "questions": {
    "angry": {
      "type": "noul",
      "instructions": "The user is angry."
    },
    "topic": {
      "type": "choice",
      "instructions": "What is the topic?",
      "criteria": {
        "billing": "charges, invoices, refunds",
        "tech": "bugs, errors",
        "other": "anything else"
      }
    },
    "urgency": {
      "type": "score",
      "instructions": "How urgent is it?",
      "criteria": [
        "not urgent",
        "somewhat urgent",
        "urgent"
      ]
    }
  },
  "options": {
    "samples": 1
  }
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: ask -->
```json
{
  "answers": {
    "angry": {
      "type": "noul",
      "p": 1.26e-05,
      "band": "no",
      "margin": 0.99997
    },
    "topic": {
      "type": "choice",
      "choice": "billing",
      "p_top": 0.99999,
      "runner_up": "tech",
      "margin": 0.99999,
      "probabilities": {
        "billing": 0.99999,
        "tech": 4.6e-06,
        "other": 5.2e-07
      },
      "confidence": 0.99994,
      "entropy": 7e-05,
      "abstained": false
    },
    "urgency": {
      "type": "score",
      "score": 1.9987,
      "level": 2,
      "level_label": "urgent",
      "probabilities": {
        "0": 3e-05,
        "1": 0.00122,
        "2": 0.99875
      },
      "confidence": 0.99112,
      "spread": 0.03663,
      "bimodal": false
    }
  },
  "meta": {
    "model": "openjev-0.1",
    "requests": 1,
    "latency_ms": 393
  },
  "lint": {
    "warnings": [
      {
        "code": "W101",
        "path": "questions.angry",
        "message": "noul without criteria",
        "fix": "add criteria {true, false} that define both poles",
        "rule": "R4"
      },
      {
        "code": "W202",
        "path": "questions.topic.criteria",
        "message": "option descriptions are missing, equal to their keys or shorter than 3 words",
        "fix": "describe what inputs of each option look like",
        "rule": "R6"
      }
    ]
  }
}
```

### Common errors

- `OJ_INVALID_INPUT` with `path` such as `questions.q1.criteria`: the hint carries the schema branch that was expected.
- Lint errors always block (`E0xx`); warnings (`W1xx`-`W3xx`) are returned in `lint.warnings`.

### Usually next

`lint` to pre-check a question set, `calibrate` to fit thresholds, or `batch` to run the same questions over many states.

## yes_no

Tool name: `mcp__openjev__yes_no`. Decide one literal claim about a state: yes, no or uncertain, with the probability. A first read in the grey band is re-read once with samples 4.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `state` | string or object or array | yes | What the questions are about. Label sections (USER MESSAGE:, DIFF:, CANDIDATES:). Objects/arrays are JSON-dumped into the prompt. |
| `claim` | string | yes | Question or claim, literal and positive (no 'fails to', no 'not').; minLength 3 |
| `true_means` | string |  | criteria.true: what makes it yes. |
| `false_means` | string |  | criteria.false: what makes it no, naming the near-miss. |
| `yes_at` | number |  | default 0.8 |
| `no_at` | number |  | default 0.2 |
| `options` | object |  |  |
| `options.model` | string |  | Default OPENJEV_MCP_MODEL (openjev-latest). Valid names are those GET /v1/models lists (served and routed models, e.g. openjev-0.1, laya-1.0, verdict-1.4) plus the unlisted alia... |
| `options.samples` | integer |  | N billed reads averaged. 1 = fastest. Omit = 1 read + 3 free re-reads when uncertain.; minimum 1; maximum 32 |
| `options.steps` | integer |  | minimum 1; maximum 8 |
| `options.think` | integer |  | Thought budget in tokens; text-only states. 256-512 for lookahead, rules, arithmetic.; minimum 0; maximum 4096 |
| `options.sequential` | boolean |  | Chunks see earlier chunks' answers. Only matters when the questions span 2+ canvas chunks (above 10 questions, or fewer when score-heavy); text-only. |
| `options.timeout_ms` | integer |  | minimum 100; maximum 600000 |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `decision` | enum(yes\|no\|uncertain) | yes |  |
| `p` | number | yes |  |
| `margin` | number | yes |  |
| `thresholds_used` | object |  |  |
| `meta` | object | yes |  |

### Example 1: One literal claim

Call:

<!-- openjev-call: yes_no -->
```json
{
  "state": "USER MESSAGE: My invoice total is wrong, I was charged twice.",
  "claim": "The user reports a billing problem.",
  "false_means": "The message is about something else, such as a bug or a feature request.",
  "options": {
    "samples": 1
  }
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: yes_no -->
```json
{
  "decision": "yes",
  "p": 0.99961,
  "margin": 0.99922,
  "thresholds_used": {
    "yes_at": 0.8,
    "no_at": 0.2
  },
  "meta": {
    "model": "openjev-0.1",
    "requests": 1,
    "latency_ms": 161
  }
}
```

### Common errors

- `OJ_INVALID_INPUT` `claim`: shorter than 3 characters.
- W102: only `false_means` given; pass `true_means` too.

### Usually next

`calibrate` (via a one-question `questions` map) to check the thresholds, or `filter` for many items.

## classify

Tool name: `mcp__openjev__classify`. Pick one label from a closed set; an escape option is added so the answer can abstain. multi_label asks one yes/no per label instead.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `state` | string or object or array | yes | What the questions are about. Label sections (USER MESSAGE:, DIFF:, CANDIDATES:). Objects/arrays are JSON-dumped into the prompt. |
| `question` | string | yes | minLength 3 |
| `labels` | object | yes | label -> description of what an input with this label looks like. Also accepted: an array of strings, converted with the label as its own description plus warning W202. |
| `labels.<key>` | string |  | minLength 1 |
| `escape` | const false or object |  | Default {label:'other', description:'anything else, or too vague to tell'}; skipped when a label already starts with other/none/no_match/not_stated. false disables (lint W201). |
| `min_p` | number |  | abstain when p_top < min_p; default 0.6 |
| `multi_label` | boolean |  | default false |
| `options` | object |  |  |
| `options.model` | string |  | Default OPENJEV_MCP_MODEL (openjev-latest). Valid names are those GET /v1/models lists (served and routed models, e.g. openjev-0.1, laya-1.0, verdict-1.4) plus the unlisted alia... |
| `options.samples` | integer |  | N billed reads averaged. 1 = fastest. Omit = 1 read + 3 free re-reads when uncertain.; minimum 1; maximum 32 |
| `options.steps` | integer |  | minimum 1; maximum 8 |
| `options.think` | integer |  | Thought budget in tokens; text-only states. 256-512 for lookahead, rules, arithmetic.; minimum 0; maximum 4096 |
| `options.sequential` | boolean |  | Chunks see earlier chunks' answers. Only matters when the questions span 2+ canvas chunks (above 10 questions, or fewer when score-heavy); text-only. |
| `options.timeout_ms` | integer |  | minimum 100; maximum 600000 |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `label` | string\|null | yes | null when abstained |
| `abstained` | boolean | yes |  |
| `reason` | string |  |  |
| `top` | string |  |  |
| `p_top` | number | yes |  |
| `runner_up` | string\|null |  |  |
| `margin` | number |  |  |
| `probabilities` | object | yes |  |
| `confidence` | number |  |  |
| `labels_multi` | object |  | multi_label: label -> {p, band} |
| `meta` | object | yes |  |

### Example 1: One label from a closed set (escape option added automatically)

Call:

<!-- openjev-call: classify -->
```json
{
  "state": "USER MESSAGE: My invoice total is wrong, I was charged twice.",
  "question": "What is the message about?",
  "labels": {
    "billing": "charges, invoices, refunds",
    "tech": "bugs, errors, crashes"
  },
  "options": {
    "samples": 1
  }
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: classify -->
```json
{
  "label": "billing",
  "abstained": false,
  "top": "billing",
  "p_top": 0.9974,
  "runner_up": "tech",
  "margin": 0.99483,
  "probabilities": {
    "billing": 0.9974,
    "tech": 0.00257,
    "other": 2e-05
  },
  "confidence": 0.98347,
  "meta": {
    "model": "openjev-0.1",
    "requests": 1,
    "latency_ms": 161
  }
}
```

### Common errors

- `OJ_INVALID_INPUT` `labels`: at least 1 label; descriptions must be non-empty.
- W201/W202: no escape option or label equal to its description.

### Usually next

`batch` with the same question as a `choice` question for many states; `calibrate` to measure accuracy.

## score

Tool name: `mcp__openjev__score`. Place one input on an ordered scale of 2-10 described levels, lowest first. Returns the expected score, the argmax level label, the spread and a bimodality flag.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `state` | string or object or array | yes | What the questions are about. Label sections (USER MESSAGE:, DIFF:, CANDIDATES:). Objects/arrays are JSON-dumped into the prompt. |
| `question` | string | yes | minLength 3 |
| `levels` | array<string> | yes | lowest/worst first; each level names observable evidence, mutually exclusive, no escape clauses ('unless', 'no ... mentioned'); minItems 2; maxItems 10 |
| `one_based` | boolean |  | add 1 to score/level in the output (for 1-10 ratings); default false |
| `options` | object |  |  |
| `options.model` | string |  | Default OPENJEV_MCP_MODEL (openjev-latest). Valid names are those GET /v1/models lists (served and routed models, e.g. openjev-0.1, laya-1.0, verdict-1.4) plus the unlisted alia... |
| `options.samples` | integer |  | N billed reads averaged. 1 = fastest. Omit = 1 read + 3 free re-reads when uncertain.; minimum 1; maximum 32 |
| `options.steps` | integer |  | minimum 1; maximum 8 |
| `options.think` | integer |  | Thought budget in tokens; text-only states. 256-512 for lookahead, rules, arithmetic.; minimum 0; maximum 4096 |
| `options.sequential` | boolean |  | Chunks see earlier chunks' answers. Only matters when the questions span 2+ canvas chunks (above 10 questions, or fewer when score-heavy); text-only. |
| `options.timeout_ms` | integer |  | minimum 100; maximum 600000 |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `score` | number | yes | 0-indexed expected level |
| `level` | integer | yes | argmax level |
| `level_label` | string | yes |  |
| `probabilities` | object | yes |  |
| `probabilities.<key>` | number |  |  |
| `confidence` | number | yes |  |
| `spread` | number |  | sqrt(sum p_k (k-score)^2) |
| `bimodal` | boolean |  | two non-adjacent levels each >= 0.2 |
| `meta` | object | yes |  |

### Example 1: Ordinal scale, lowest level first

Call:

<!-- openjev-call: score -->
```json
{
  "state": "USER MESSAGE: My invoice total is wrong, I was charged twice.",
  "question": "How urgent is the request?",
  "levels": [
    "no deadline or impact stated",
    "money lost but no deadline",
    "money lost and a deadline or escalation is stated"
  ],
  "options": {
    "samples": 1
  }
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: score -->
```json
{
  "score": 0.65886,
  "level": 1,
  "level_label": "money lost but no deadline",
  "probabilities": {
    "0": 0.34605,
    "1": 0.64903,
    "2": 0.00492
  },
  "confidence": 0.38657,
  "spread": 0.48435,
  "bimodal": false,
  "meta": {
    "model": "openjev-0.1",
    "requests": 1,
    "latency_ms": 163
  }
}
```

### Common errors

- `OJ_INVALID_INPUT` `levels`: 2-10 non-empty strings, lowest first.
- W302: a level holds an escape clause ('no ... stated').

### Usually next

`calibrate` (score labels are level indexes) to check the ladder is monotonic.

## filter

Tool name: `mcp__openjev__filter`. Keep or drop many small items (log lines, files, hunks, findings) by one criterion: items are packed into one state per request with one yes/no question per item id. pick_best also returns the single best item; graded scores each item instead.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `task` | string | yes | What the agent is doing; goes first in the state as 'TASK: ...' |
| `items` | array<object> | yes | minItems 0; maxItems 5000 |
| `items.id` | string | yes | pattern "^[A-Za-z0-9_.:-]{1,32}$" |
| `items.text` | string | yes |  |
| `criterion` | string | yes | Per-item question with {id}, e.g. 'Is log line {id} a real failure ...?' |
| `true_means` | string |  |  |
| `false_means` | string |  |  |
| `keep_at` | number |  | default 0.6 |
| `drop_at` | number |  | default 0.2 |
| `grey` | enum(keep\|drop\|review) |  | default "keep" |
| `pack_size` | integer |  | default 10; minimum 1; maximum 30 |
| `items_label` | string |  | default "ITEMS" |
| `pick_best` | object |  | adds exists (noul) + best (choice over ids + none) per pack |
| `pick_best.question` | string |  |  |
| `pick_best.none_means` | string |  |  |
| `graded` | object |  | score per item instead of noul, for ranking |
| `graded.levels` | array<string> |  | minItems 2; maxItems 10 |
| `graded.relevant_at` | number |  | default 2.5 |
| `options` | object |  |  |
| `options.model` | string |  | Default OPENJEV_MCP_MODEL (openjev-latest). Valid names are those GET /v1/models lists (served and routed models, e.g. openjev-0.1, laya-1.0, verdict-1.4) plus the unlisted alia... |
| `options.samples` | integer |  | N billed reads averaged. 1 = fastest. Omit = 1 read + 3 free re-reads when uncertain.; minimum 1; maximum 32 |
| `options.steps` | integer |  | minimum 1; maximum 8 |
| `options.think` | integer |  | Thought budget in tokens; text-only states. 256-512 for lookahead, rules, arithmetic.; minimum 0; maximum 4096 |
| `options.sequential` | boolean |  | Chunks see earlier chunks' answers. Only matters when the questions span 2+ canvas chunks (above 10 questions, or fewer when score-heavy); text-only. |
| `options.timeout_ms` | integer |  | minimum 100; maximum 600000 |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `kept` | array<string> | yes |  |
| `dropped` | array<string> | yes |  |
| `grey` | array<string> | yes |  |
| `items` | array<object> | yes |  |
| `items.id` | string | yes |  |
| `items.p` | number |  |  |
| `items.score` | number |  |  |
| `items.decision` | enum(keep\|drop\|grey) | yes |  |
| `best` | object |  |  |
| `best.id` | string\|null |  |  |
| `best.p` | number |  |  |
| `best.exists_p` | number |  |  |
| `meta` | object | yes |  |

### Example 1: Keep or drop items by one criterion containing {id}

Call:

<!-- openjev-call: filter -->
```json
{
  "task": "Find real failures in a deploy log.",
  "items": [
    {
      "id": "l1",
      "text": "ERROR db connection refused"
    },
    {
      "id": "l2",
      "text": "INFO request ok 200"
    },
    {
      "id": "l3",
      "text": "WARN retrying in 5s"
    }
  ],
  "criterion": "Is log line {id} a real failure that needs a human?",
  "items_label": "LOG LINES",
  "options": {
    "samples": 1
  }
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: filter -->
```json
{
  "kept": [
    "l1"
  ],
  "dropped": [
    "l2",
    "l3"
  ],
  "grey": [],
  "items": [
    {
      "id": "l1",
      "p": 0.99942,
      "decision": "keep"
    },
    {
      "id": "l2",
      "p": 8e-05,
      "decision": "drop"
    }
  ],
  "meta": {
    "model": "openjev-0.1",
    "requests": 1,
    "latency_ms": 210
  }
}
```

### Common errors

- `OJ_INVALID_INPUT` `items.N.id`: ids match `^[A-Za-z0-9_.:-]{1,32}$`.
- The criterion must contain `{id}`.

### Usually next

Feed `kept` ids (and `grey` ids if you want to review them) into `batch` with `only_ids`, or into your own next step.

## batch

Tool name: `mcp__openjev__batch`. Run one question set over many states (inline items, a CSV/JSONL/JSON file or a library template) with bounded concurrency, a resumable JSONL output, a review queue, a seeded audit sample, per-question statistics and csv, markdown or ojui-batch exports. One call reads at most max_items_per_call rows; pass next_cursor back to continue. dry_run sends nothing and returns the import report, the first body and an estimate.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `items` | array<object> |  | minItems 1; maxItems 500 |
| `items.id` | string |  | default: 1-based position as a string; pattern "^[A-Za-z0-9_.:-]{1,64}$" |
| `items.state` | string or object or array | yes | What the questions are about. Label sections (USER MESSAGE:, DIFF:, CANDIDATES:). Objects/arrays are JSON-dumped into the prompt. |
| `items_file` | object |  |  |
| `items_file.path` | string | yes | file inside the allowed roots |
| `items_file.also` | array<string> |  | more files merged after path with the UI merge rules; maxItems 7 |
| `items_file.format` | enum(auto\|jsonl\|json\|csv\|tsv\|lines\|blocks\|ojui-batch) |  | default "auto" |
| `items_file.delimiter` | enum(auto\|,\|	\|;) |  | default "auto" |
| `items_file.state_field` | string |  | CSV column or JSON key; '*' = the whole object; omitted = guessed (W602) |
| `items_file.id_field` | string |  | omitted = 1-based row index |
| `items_file.state_template` | string |  | e.g. 'Subject: {subject}\n\n{body}'; placeholders are column/key names and {id}; wins over state_field |
| `items_file.array_key` | string |  | key of the array in a wrapped JSON file |
| `items_file.encoding` | enum(auto\|utf-8\|utf-16\|cp1252) |  | default "auto" |
| `template` | string |  | id of openjev://templates/{id}; pattern "^[a-z0-9_]{1,64}$" |
| `questions` | object |  |  |
| `questions.<key>` | noul or choice or score object |  |  |
| `options` | object |  |  |
| `options.model` | string |  | Default OPENJEV_MCP_MODEL (openjev-latest). Valid names are those GET /v1/models lists (served and routed models, e.g. openjev-0.1, laya-1.0, verdict-1.4) plus the unlisted alia... |
| `options.samples` | integer |  | N billed reads averaged. 1 = fastest. Omit = 1 read + 3 free re-reads when uncertain.; minimum 1; maximum 32 |
| `options.steps` | integer |  | minimum 1; maximum 8 |
| `options.think` | integer |  | Thought budget in tokens; text-only states. 256-512 for lookahead, rules, arithmetic.; minimum 0; maximum 4096 |
| `options.sequential` | boolean |  | Chunks see earlier chunks' answers. Only matters when the questions span 2+ canvas chunks (above 10 questions, or fewer when score-heavy); text-only. |
| `options.timeout_ms` | integer |  | minimum 100; maximum 600000 |
| `images` | array<object or object or object or object> |  | 1-8 images (path, url, data_url or base64+content_type, as ask_image), loaded and re-encoded once per call and sent with every row; options.think and sequential are refused (E02... |
| `max_side_px` | integer |  | longest side after downscaling; images are re-encoded as image/png or image/jpeg; default 1568; minimum 64; maximum 8192 |
| `sampling` | enum(fast\|server_default) |  | fast: samples 1 plus a regrey re-read; server_default: no samples field. An explicit options.samples wins.; default "fast" |
| `regrey_samples` | integer |  | fast mode only: grey/below-review rows are re-read once with this many samples; 0 disables; default 4; minimum 0; maximum 32 |
| `thresholds` | object |  |  |
| `thresholds.yes_at` | number |  | default 0.8; minimum 0.5; maximum 1 |
| `thresholds.no_at` | number |  | default 0.2; minimum 0; maximum 0.5 |
| `thresholds.choice_min_p` | number |  | default 0.6; minimum 0; maximum 1 |
| `review_rule` | object |  |  |
| `review_rule.choice_p_below` | number |  | default 0.8; minimum 0; maximum 1 |
| `review_rule.noul_grey` | array<number> |  | default [0.15, 0.85]; minItems 2; maxItems 2 |
| `review_rule.score_spread_above` | number |  | default 0.6; minimum 0 |
| `audit` | object |  |  |
| `audit.rate` | number |  | default 0.03; minimum 0; maximum 1 |
| `audit.seed` | integer |  | default 0 |
| `concurrency` | integer |  | parallel reads for this call, capped by OPENJEV_MCP_MAX_INFLIGHT_BATCH; no speedup on MLX (W405); default 1; minimum 1; maximum 4 |
| `output_path` | string |  | .jsonl inside the allowed roots: header record + one full row per item; the resumable source of truth |
| `include_state` | boolean |  | write the state text into each JSONL row (exports need it); false keeps only state_hash; default true |
| `export` | array<object> |  | written once, by the call that finishes the job (next_cursor null); needs output_path; maxItems 3 |
| `export.format` | enum(csv\|markdown\|ojui-batch) | yes |  |
| `export.path` | string | yes | .csv, .md or .json, created new inside the allowed roots |
| `detail` | enum(compact\|full) |  | inline results only; JSONL rows are always full; default "compact" |
| `max_items` | integer |  | rows taken from the source; the rest is dropped with W601; default 5000; minimum 1; maximum 100000 |
| `max_items_per_call` | integer |  | default 25; minimum 1; maximum 100 |
| `time_budget_s` | integer |  | no new row is started after this; in-flight rows finish; default 120; minimum 5; maximum 600 |
| `cursor` | string |  | next_cursor of the previous call (opaque) |
| `resume` | boolean |  | skip ids whose last row in output_path is ok (and error unless retry_errors); false requires a new output_path; default true |
| `retry_errors` | boolean |  | re-run ids whose last row in output_path has status error; default false |
| `only_ids` | array<string> |  | run only these ids (single-row retry); minItems 1; maxItems 500 |
| `on_error` | enum(record\|abort) |  | default "record" |
| `dry_run` | boolean |  | no network: import report, 3 preview states, the first HTTP body, an estimate; default false |
| `max_inline_results` | integer |  | default 50; minimum 0; maximum 100 |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `status` | object | yes |  |
| `status.done` | integer | yes | rows finished in this call |
| `status.total` | integer | yes | rows in the source after max_items |
| `status.remaining` | integer |  |  |
| `status.ok` | integer |  |  |
| `status.errors` | integer |  |  |
| `status.skipped` | integer |  | resumed rows not re-read |
| `status.stopped_reason` | enum(complete\|max_items_per_call\|time_budget\|backpressure\|error_abort\|dry_run) | yes |  |
| `status.elapsed_ms` | number |  |  |
| `status.eta_ms` | number\|null |  |  |
| `status.req_per_s` | number |  |  |
| `status.effective_concurrency` | integer |  |  |
| `status.backoffs` | integer |  |  |
| `status.input_tokens` | integer |  |  |
| `status.output_tokens` | integer |  |  |
| `summary` | object | yes |  |
| `summary.scope` | enum(output_path\|call) |  | output_path: all rows in the file; call: this call only (no output_path) |
| `summary.n` | integer |  |  |
| `summary.ok` | integer |  |  |
| `summary.errors` | integer |  |  |
| `summary.needs_review` | integer |  |  |
| `summary.audit` | integer |  |  |
| `summary.per_question` | object |  |  |
| `results` | array<object> | yes |  |
| `results.index` | integer |  |  |
| `results.id` | string | yes |  |
| `results.status` | enum(ok\|error\|skipped) | yes |  |
| `results.answers` | object |  | compact {choice, p_top} / {p, band} / {score, level}; full Answer objects with detail full |
| `results.needs_review` | boolean |  |  |
| `results.audit` | boolean |  |  |
| `results.error` | object or null |  |  |
| `review_queue` | array<object> |  | this call's rows, ascending confidence/margin; the whole file through batch_results |
| `review_queue.id` | string |  |  |
| `review_queue.question` | string |  |  |
| `review_queue.reason` | enum(choice_p_below\|noul_grey\|score_spread_above\|abstained\|error) |  |  |
| `review_queue.confidence` | number |  |  |
| `audit_ids` | array<string> |  |  |
| `output_path` | string\|null |  |  |
| `exports` | array<object> |  |  |
| `exports.format` | string |  |  |
| `exports.path` | string |  |  |
| `exports.bytes` | integer |  |  |
| `import` | object |  | first call, dry_run, and any call without cursor |
| `import.format` | string |  |  |
| `import.delimiter` | string\|null |  |  |
| `import.encoding` | string |  |  |
| `import.state_field` | string\|null |  |  |
| `import.id_field` | string\|null |  |  |
| `import.columns` | array<string> |  |  |
| `import.row_count` | integer |  |  |
| `import.truncated` | boolean |  |  |
| `import.warnings` | array<object> |  |  |
| `preview` | array<object> |  | maxItems 3 |
| `preview.id` | string |  |  |
| `preview.state` | string or object or array |  | What the questions are about. Label sections (USER MESSAGE:, DIFF:, CANDIDATES:). Objects/arrays are JSON-dumped into the prompt. |
| `first_body` | object |  | dry_run: the first POST /v1/systemone body (image data elided) |
| `images` | array<object> |  |  |
| `images.source` | string | yes |  |
| `images.sent_as` | enum(image/png\|image/jpeg) | yes |  |
| `images.bytes` | integer | yes |  |
| `images.reencoded` | boolean | yes |  |
| `estimate` | object |  |  |
| `estimate.requests` | integer |  |  |
| `estimate.billed_reads` | integer |  |  |
| `estimate.input_tokens_approx` | integer |  |  |
| `estimate.time_s_idle_approx` | number |  |  |
| `estimate.time_s_shared_approx` | number |  |  |
| `next_cursor` | string\|null | yes | null when every row is done |
| `meta` | object | yes |  |

### Example 1: Dry run on 3 inline items (no reads, no file written)

Call:

<!-- openjev-call: batch -->
```json
{
  "items": [
    {
      "id": "t1",
      "state": "USER MESSAGE: I was charged twice."
    },
    {
      "id": "t2",
      "state": "USER MESSAGE: The app crashes on launch."
    },
    {
      "id": "t3",
      "state": "USER MESSAGE: How do I change my avatar?"
    }
  ],
  "questions": {
    "billing": {
      "type": "noul",
      "instructions": "The message is about a billing problem.",
      "criteria": {
        "true": "charges, invoices or refunds",
        "false": "anything else, such as a crash or a how-to"
      }
    }
  },
  "dry_run": true
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: batch -->
```json
{
  "status": {
    "done": 0,
    "total": 3,
    "remaining": 3,
    "ok": 0,
    "errors": 0,
    "skipped": 0,
    "stopped_reason": "dry_run"
  },
  "summary": {
    "scope": "call",
    "n": 0,
    "ok": 0,
    "errors": 0,
    "needs_review": 0,
    "audit": 0,
    "per_question": {
      "billing": {
        "type": "noul",
        "n": 0,
        "mean_p": 0,
        "yes": 0,
        "no": 0,
        "grey": 0,
        "mean_margin": 0
      }
    }
  },
  "results": [],
  "review_queue": [],
  "audit_ids": [],
  "output_path": null,
  "import": {
    "format": "items",
    "delimiter": null,
    "encoding": "utf-8",
    "state_field": null,
    "id_field": null,
    "columns": [],
    "row_count": 3,
    "truncated": false,
    "warnings": []
  },
  "preview": [
    {
      "id": "t1",
      "state": "USER MESSAGE: I was charged twice."
    },
    {
      "id": "t2",
      "state": "USER MESSAGE: The app crashes on launch."
    }
  ],
  "first_body": {
    "model": "openjev-latest",
    "samples": 1,
    "state": "USER MESSAGE: I was charged twice.",
    "questions": {
      "billing": {
        "type": "noul",
        "instructions": "The message is about a billing problem.",
        "criteria": {
          "true": "charges, invoices or refunds",
          "false": "anything else, such as a crash or a how-to"
        }
      }
    }
  },
  "estimate": {
    "requests": 3,
    "billed_reads": 3,
    "input_tokens_approx": 191,
    "time_s_idle_approx": 0.9,
    "time_s_shared_approx": 4.5
  },
  "next_cursor": null,
  "meta": {
    "model": "openjev-latest",
    "requests": 0,
    "latency_ms": 0
  }
}
```

### Example 2: Real run on the same 3 items, writing a resumable JSONL

Call:

<!-- openjev-call: batch -->
```json
{
  "items": [
    {
      "id": "t1",
      "state": "USER MESSAGE: I was charged twice."
    },
    {
      "id": "t2",
      "state": "USER MESSAGE: The app crashes on launch."
    },
    {
      "id": "t3",
      "state": "USER MESSAGE: How do I change my avatar?"
    }
  ],
  "questions": {
    "billing": {
      "type": "noul",
      "instructions": "The message is about a billing problem.",
      "criteria": {
        "true": "charges, invoices or refunds",
        "false": "anything else, such as a crash or a how-to"
      }
    }
  },
  "output_path": "/abs/path/to/out/batch1.jsonl"
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: batch -->
```json
{
  "status": {
    "done": 3,
    "total": 3,
    "remaining": 0,
    "ok": 3,
    "errors": 0,
    "skipped": 0,
    "stopped_reason": "complete",
    "elapsed_ms": 652.4,
    "eta_ms": null,
    "req_per_s": 4.6,
    "effective_concurrency": 1,
    "backoffs": 0,
    "input_tokens": 752,
    "output_tokens": 0
  },
  "summary": {
    "scope": "output_path",
    "n": 3,
    "ok": 3,
    "errors": 0,
    "needs_review": 1,
    "audit": 0,
    "per_question": {
      "billing": {
        "type": "noul",
        "n": 3,
        "mean_p": 0.1798,
        "yes": 0,
        "no": 2,
        "grey": 1,
        "mean_margin": 0.6915
      }
    }
  },
  "results": [
    {
      "index": 1,
      "id": "t1",
      "status": "ok",
      "answers": {
        "billing": {
          "p": 0.53827,
          "band": "grey"
        }
      },
      "needs_review": true,
      "error": null
    },
    {
      "index": 2,
      "id": "t2",
      "status": "ok",
      "answers": {
        "billing": {
          "p": 0.00093,
          "band": "no"
        }
      },
      "needs_review": false,
      "error": null
    }
  ],
  "review_queue": [
    {
      "id": "t1",
      "question": "billing",
      "reason": "noul_grey",
      "confidence": 0.07654
    }
  ],
  "audit_ids": [],
  "output_path": "/abs/path/to/out/batch1.jsonl",
  "next_cursor": null,
  "meta": {
    "model": "openjev-0.1",
    "requests": 4,
    "latency_ms": 652
  }
}
```

### Common errors

- `OJ_INVALID_INPUT` 'path outside the allowed roots': `items_file.path` and `output_path` must be absolute and inside the server's roots.
- Give exactly one source: `items`, `items_file` or `template`; `resume: false` needs a new `output_path`.
- E022: `options.think` / `sequential` with `images`.

### Usually next

`batch_results` on `output_path` (review queue, stats, exports); call `batch` again with `cursor` = `next_cursor` until it is null; `calibrate` with `from_batch` once you have labels.

## ask_image

Tool name: `mcp__openjev__ask_image`. Ask questions about 1-8 images (screenshots, photos) plus a short text state. Loads files, data URLs and (only with OPENJEV_MCP_FETCH=on) public https URLs, downscales them and re-encodes them as PNG or JPEG locally. think and sequential are not available for images; prefer ask with a text tree when you have one.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `images` | array<object or object or object or object> | yes | minItems 1; maxItems 8 |
| `state` | string |  | What the images are, the task, and their order for multi-image requests; default "Screenshot." |
| `questions` | object | yes |  |
| `questions.<key>` | noul or choice or score object |  |  |
| `options` | object |  |  |
| `options.model` | string |  |  |
| `options.samples` | integer |  | minimum 1; maximum 32 |
| `options.steps` | integer |  | minimum 1; maximum 8 |
| `options.timeout_ms` | integer |  |  |
| `max_side_px` | integer |  | longest side after downscaling; images are re-encoded as image/png or image/jpeg; default 1568; minimum 64; maximum 8192 |
| `thresholds` | object |  |  |
| `thresholds.yes_at` | number |  | default 0.8; minimum 0.5; maximum 1 |
| `thresholds.no_at` | number |  | default 0.2; minimum 0; maximum 0.5 |
| `thresholds.choice_min_p` | number |  | default 0.6; minimum 0; maximum 1 |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `answers` | object | yes |  |
| `answers.<key>` | noul or choice or score object |  |  |
| `images` | array<object> | yes |  |
| `images.source` | string | yes |  |
| `images.sent_as` | enum(image/png\|image/jpeg) | yes |  |
| `images.bytes` | integer | yes |  |
| `images.reencoded` | boolean | yes |  |
| `meta` | object | yes |  |

### Example 1: One screenshot, one noul question

Call:

<!-- openjev-call: ask_image -->
```json
{
  "images": [
    {
      "path": "/abs/path/to/screenshots/error_500.png"
    }
  ],
  "questions": {
    "is_error": {
      "type": "noul",
      "instructions": "The screenshot shows an error page.",
      "criteria": {
        "true": "an error message such as 500 is visible",
        "false": "a normal page"
      }
    }
  },
  "state": "Screenshot of a web page.",
  "options": {
    "samples": 1
  }
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: ask_image -->
```json
{
  "answers": {
    "is_error": {
      "type": "noul",
      "p": 0.99996,
      "band": "yes",
      "margin": 0.99993
    }
  },
  "images": [
    {
      "source": "/abs/path/to/screenshots/error_500.png",
      "sent_as": "image/png",
      "bytes": 17855,
      "reencoded": false
    }
  ],
  "meta": {
    "model": "openjev-0.1",
    "requests": 1,
    "latency_ms": 640
  }
}
```

### Common errors

- `OJ_BAD_IMAGE`: unreadable or unsupported image.
- `OJ_TOO_LARGE`: image over 5 MiB.
- `https` URLs are refused unless the server runs with `OPENJEV_MCP_FETCH=on`.

### Usually next

`batch` with `images` to reuse the same screenshots across many rows; otherwise stop, this is a leaf call.

## lint

Tool name: `mcp__openjev__lint`. Validate and lint a /v1/systemone request, autofix known mistakes and estimate its cost. Makes no network request.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `request` | object |  | a full /v1/systemone body; or give state + questions |
| `state` | string or object or array |  | What the questions are about. Label sections (USER MESSAGE:, DIFF:, CANDIDATES:). Objects/arrays are JSON-dumped into the prompt. |
| `questions` | object |  |  |
| `options` | object |  |  |
| `images` | array |  |  |
| `profile` | enum(default\|strict\|gate) |  | gate: warnings about blocking questions become errors; default "default" |
| `autofix` | boolean |  | default true |
| `emit` | array<enum(body\|curl\|python)> |  | return the exact HTTP body and request snippets (the key is never inlined: curl uses $OPENJEV_API_KEY, Python os.environ) |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `valid` | boolean | yes | true when the server would accept the (fixed) request |
| `errors` | array<object> | yes |  |
| `errors.code` | string | yes |  |
| `errors.path` | string | yes |  |
| `errors.message` | string | yes |  |
| `errors.fix` | string |  |  |
| `errors.rule` | string |  | guide rule id, section 3 |
| `errors.autofixed` | boolean |  |  |
| `errors.limit_source` | enum(server\|default) |  | limit-dependent codes only (2.2) |
| `warnings` | array<object> | yes |  |
| `warnings.code` | string | yes |  |
| `warnings.path` | string | yes |  |
| `warnings.message` | string | yes |  |
| `warnings.fix` | string |  |  |
| `warnings.rule` | string |  | guide rule id, section 3 |
| `warnings.autofixed` | boolean |  |  |
| `warnings.limit_source` | enum(server\|default) |  | limit-dependent codes only (2.2) |
| `fixed_request` | object |  |  |
| `estimate` | object |  | chunks is the +-25% estimate of 2.3 |
| `estimate.questions` | any |  |  |
| `estimate.chunks` | any |  |  |
| `estimate.input_tokens_approx` | any |  |  |
| `estimate.latency_ms_idle_approx` | any |  |  |
| `estimate.billed_reads` | any |  |  |
| `snippets` | object |  |  |
| `snippets.body` | object |  |  |
| `snippets.curl` | string |  |  |
| `snippets.python` | string |  |  |
| `body_hash` | string |  |  |

### Example 1: Strict lint of a question set (no network)

Call:

<!-- openjev-call: lint -->
```json
{
  "state": "USER MESSAGE: I was charged twice.",
  "questions": {
    "billing": {
      "type": "noul",
      "instructions": "The message is about a billing problem."
    }
  },
  "profile": "strict"
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: lint -->
```json
{
  "valid": true,
  "errors": [],
  "warnings": [
    {
      "code": "W101",
      "path": "questions.billing",
      "message": "noul without criteria",
      "fix": "add criteria {true, false} that define both poles",
      "rule": "R4"
    }
  ],
  "estimate": {
    "questions": 1,
    "chunks": 1,
    "input_tokens_approx": 111,
    "latency_ms_idle_approx": 300,
    "billed_reads": 1
  }
}
```

### Common errors

- Give either `request` or `questions`, not neither (`OJ_INVALID_INPUT`).

### Usually next

`ask`, `batch` or `recipe` once `valid` is true; use `fixed_request` when autofix changed something.

## compile

Tool name: `mcp__openjev__compile`. Turn a prose intent ('tell me if support emails are angry and who should take them') into a linted draft /v1/systemone request built from the recipe library. Routes with one read, types each sub-decision with one read, instantiates a template, lists the slots only a human can fill and the questions to ask, and can probe the draft on sample_inputs or calibrate it on labelled_examples. Never writes text with the chat model.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `intent` | string | yes | minLength 5 |
| `sub_decisions` | array<string> |  | optional split of the intent, one decision each ('whether it is urgent', 'which team') |
| `labels` | object |  | known options per sub-decision: {sub_decision: {label: description} \| [label]} |
| `sample_inputs` | array<string or object or array> |  | maxItems 10 |
| `labelled_examples` | array<object> |  |  |
| `labelled_examples.state` | any | yes |  |
| `labelled_examples.label` | object | yes |  |
| `recipe` | string |  | skip routing and use this recipe id |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `recipe` | object | yes |  |
| `recipe.id` | string |  |  |
| `recipe.p` | number |  |  |
| `recipe.runner_up` | any |  |  |
| `recipe.not_a_decision` | boolean |  |  |
| `recipe.variants` | array<string> |  |  |
| `sub_decisions` | array<object> |  |  |
| `sub_decisions.text` | any |  |  |
| `sub_decisions.qtype` | enum(noul\|choice\|score\|not_typed) |  |  |
| `sub_decisions.p` | any |  |  |
| `sub_decisions.question_id` | any |  |  |
| `sub_decisions.note` | any |  |  |
| `sub_decisions.redirect` | string |  | for not_typed: 'extract candidates in code, then select_extraction' / 'compute in code' |
| `draft_request` | object | yes | a /v1/systemone body, placeholders as <SLOT:name> |
| `slots` | array<object> | yes |  |
| `slots.name` | any |  |  |
| `slots.path` | any |  |  |
| `slots.why` | any |  |  |
| `slots.example` | any |  |  |
| `human_questions` | array<string> | yes |  |
| `lint` | object | yes |  |
| `probe` | array<object> |  |  |
| `probe.input` | any |  |  |
| `probe.answers` | any |  |  |
| `calibration` | object |  | calibrate output when labelled_examples were given |
| `next_steps` | array<string> |  |  |

### Example 1: Prose intent to a linted draft request with slots for a human

Call:

<!-- openjev-call: compile -->
```json
{
  "intent": "tell me if a support email is about billing or about a technical bug",
  "sub_decisions": [
    "which topic the email is about"
  ],
  "labels": {
    "which topic the email is about": {
      "billing": "charges, invoices, refunds",
      "bug": "crashes, errors"
    }
  }
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: compile -->
```json
{
  "recipe": {
    "id": "ticket_triage",
    "p": 0.86319,
    "runner_up": "taxonomy_classify",
    "not_a_decision": false
  },
  "draft_request": {
    "model": "openjev-latest",
    "state": "{<SLOT:text>}",
    "questions": {
      "dept": {
        "type": "choice",
        "instructions": "Which team should own this ticket?",
        "criteria": {
          "<SLOT:teams: label>": "<SLOT:teams: description>",
          "other": "anything else, or too vague to tell"
        }
      },
      "topic_email_about": {
        "type": "choice",
        "instructions": "Which topic the email is about?",
        "criteria": {
          "billing": "charges, invoices, refunds",
          "bug": "crashes, errors"
        }
      }
    }
  },
  "slots": [
    {
      "name": "text",
      "path": "state",
      "why": "the text to judge",
      "example": "<the text>"
    },
    {
      "name": "teams: label",
      "path": "questions.dept.criteria.<SLOT:teams: label>",
      "why": "needs a description only the human knows",
      "example": "label: what an input with this label looks like"
    }
  ],
  "human_questions": [
    "What does 'teams: label' mean for you? (questions.dept.criteria.<SLOT:teams: label>)",
    "Recipe default yes_at = 0.8: does that threshold fit your risk?"
  ],
  "lint": {
    "valid": true,
    "errors": [],
    "warnings": [
      {
        "code": "W201",
        "path": "questions.topic_email_about.criteria",
        "message": "no escape option; a choice cannot abstain",
        "fix": "add \"other\": \"anything else, or too vague to tell\"",
        "rule": "R5"
      }
    ]
  },
  "sub_decisions": [
    {
      "text": "which topic the email is about",
      "qtype": "choice",
      "p": null,
      "question_id": "topic_email_about",
      "note": "pre-rule; no recipe question fits, new question"
    }
  ],
  "next_steps": [
    "fill the <SLOT:...> placeholders in draft_request (probe and calibrate wait for them)",
    "answer human_questions"
  ]
}
```

### Common errors

- `OJ_INVALID_INPUT` `intent`: at least 5 characters.
- `recipe.not_a_decision: true`: the intent is not a typed decision; rephrase it as a question about a state.

### Usually next

Fill the `<SLOT:...>` placeholders, `lint` the draft, then `calibrate` on 10-20 labelled examples, then `ask` or `batch`.

## calibrate

Tool name: `mcp__openjev__calibrate`. Run a question set or recipe against labelled examples (or score a finished batch against a labels file): accuracy, separation, fitted thresholds, borderline items, confusion, ladder monotonicity, reliability bins, Brier, ECE and histograms. store writes a reproducible audit record, compare_to reports flips and a changed resolved model.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `questions` | object |  |  |
| `questions.<key>` | noul or choice or score object |  |  |
| `recipe` | string |  |  |
| `examples` | array<object> |  |  |
| `examples.id` | string |  |  |
| `examples.state` | string or object or array | yes | What the questions are about. Label sections (USER MESSAGE:, DIFF:, CANDIDATES:). Objects/arrays are JSON-dumped into the prompt. |
| `examples.label` | object | yes | question id -> true/false (noul), option key (choice), level index (score) |
| `case_file` | string |  | case-file format (JSON cases), inside the allowed roots; expect.answers become labels (noul_gte -> true, noul_lte -> false, choice -> key) |
| `options` | object |  |  |
| `options.model` | string |  | Default OPENJEV_MCP_MODEL (openjev-latest). Valid names are those GET /v1/models lists (served and routed models, e.g. openjev-0.1, laya-1.0, verdict-1.4) plus the unlisted alia... |
| `options.samples` | integer |  | N billed reads averaged. 1 = fastest. Omit = 1 read + 3 free re-reads when uncertain.; minimum 1; maximum 32 |
| `options.steps` | integer |  | minimum 1; maximum 8 |
| `options.think` | integer |  | Thought budget in tokens; text-only states. 256-512 for lookahead, rules, arithmetic.; minimum 0; maximum 4096 |
| `options.sequential` | boolean |  | Chunks see earlier chunks' answers. Only matters when the questions span 2+ canvas chunks (above 10 questions, or fewer when score-heavy); text-only. |
| `options.timeout_ms` | integer |  | minimum 100; maximum 600000 |
| `target` | object |  |  |
| `target.max_errors` | integer |  | default 0 |
| `target.min_precision` | number |  |  |
| `target.min_coverage` | number |  |  |
| `holdout` | number |  | fraction held out as drift canary, never used for fitting; default 0; minimum 0 |
| `store` | string |  | path of the audit record (.json, inside the allowed roots, 2.2) to write/compare |
| `compare_to` | string |  | previous audit record (allowed roots, 2.2): report flips and gap change |
| `from_batch` | object |  |  |
| `from_batch.output_path` | string | yes |  |
| `from_batch.labels_path` | string | yes | .csv or .jsonl with an id column and one label column per question |
| `from_batch.id_field` | string |  | default "id" |
| `from_batch.label_fields` | object | yes | question id -> label column |
| `concurrency` | integer |  | default 1; minimum 1; maximum 4 |
| `max_items_per_call` | integer |  | default 25; minimum 1; maximum 100 |
| `cursor` | string |  | as in batch (2.11) |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `model_resolved` | string | yes |  |
| `question_hash` | string |  |  |
| `n` | integer | yes |  |
| `per_question` | object | yes |  |
| `per_question.<key>` | object |  |  |
| `items` | array | yes |  |
| `drift` | object |  |  |
| `drift.model_changed` | any |  |  |
| `drift.flipped_ids` | any |  |  |
| `drift.gap_delta` | any |  |  |
| `warnings` | array |  |  |

### Example 1: Eight labelled examples for one noul question (smoke test, nothing stored)

Call:

<!-- openjev-call: calibrate -->
```json
{
  "questions": {
    "billing": {
      "type": "noul",
      "instructions": "The message is about a billing problem.",
      "criteria": {
        "true": "charges, invoices or refunds",
        "false": "anything else, such as a crash or a how-to"
      }
    }
  },
  "examples": [
    {
      "id": "e1",
      "state": "USER MESSAGE: I was charged twice.",
      "label": {
        "billing": true
      }
    },
    {
      "id": "e2",
      "state": "USER MESSAGE: Please refund my last invoice.",
      "label": {
        "billing": true
      }
    },
    {
      "id": "e3",
      "state": "USER MESSAGE: My card was billed after I cancelled.",
      "label": {
        "billing": true
      }
    },
    {
      "id": "e4",
      "state": "USER MESSAGE: Where is the invoice PDF for March?",
      "label": {
        "billing": true
      }
    },
    {
      "id": "e5",
      "state": "USER MESSAGE: The app crashes on launch.",
      "label": {
        "billing": false
      }
    },
    {
      "id": "e6",
      "state": "USER MESSAGE: How do I change my avatar?",
      "label": {
        "billing": false
      }
    },
    {
      "id": "e7",
      "state": "USER MESSAGE: Dark mode would be great.",
      "label": {
        "billing": false
      }
    },
    {
      "id": "e8",
      "state": "USER MESSAGE: Login page shows a 500 error.",
      "label": {
        "billing": false
      }
    }
  ],
  "options": {
    "samples": 1
  }
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: calibrate -->
```json
{
  "model_resolved": "openjev-0.1",
  "n": 8,
  "question_hash": "sha256:34896b89cc4e0b3944c05427be44b206ac979239eb7a8d25c5c6b14e35fa5fe3",
  "per_question": {
    "billing": {
      "type": "noul",
      "n": 8,
      "n_pos": 4,
      "n_neg": 4,
      "accuracy_at_0.5": 1,
      "separable": true,
      "max_negative": 0.0009,
      "min_positive": 0.5,
      "gap": 0.4991,
      "t_fit": 0.2505,
      "suggested_band": {
        "no_at": 0.05,
        "yes_at": 0.95
      },
      "overlap_ids": [],
      "most_borderline": "e1",
      "zero_error_upper_bound_95": 0.375,
      "calibration": {
        "bins": [
          {
            "lo": 0.5,
            "hi": 0.55,
            "count": 1,
            "acc": 1,
            "conf": 0.5
          },
          {
            "lo": 0.55,
            "hi": 0.6,
            "count": 1,
            "acc": 1,
            "conf": 0.5977
          },
          {
            "lo": 0.6,
            "hi": 0.65,
            "count": 0,
            "acc": null,
            "conf": null
          },
          {
            "lo": 0.65,
            "hi": 0.7,
            "count": 0,
            "acc": null,
            "conf": null
          },
          {
            "lo": 0.7,
            "hi": 0.75,
            "count": 1,
            "acc": 1,
            "conf": 0.7177
          },
          {
            "lo": 0.75,
            "hi": 0.8,
            "count": 0,
            "acc": null,
            "conf": null
          },
          {
            "lo": 0.8,
            "hi": 0.85,
            "count": 1,
            "acc": 1,
            "conf": 0.8398
          },
          {
            "lo": 0.85,
            "hi": 0.9,
            "count": 0,
            "acc": null,
            "conf": null
          },
          {
            "lo": 0.9,
            "hi": 0.95,
            "count": 0,
            "acc": null,
            "conf": null
          },
          {
            "lo": 0.95,
            "hi": 1,
            "count": 4,
            "acc": 1,
            "conf": 0.9996
          }
        ],
        "brier": 0.0647,
        "ece": 0.1683
      }
    }
  },
  "items": [
    {
      "id": "e1",
      "label": true,
      "p": 0.5
    },
    {
      "id": "e2",
      "label": true,
      "p": 0.8398
    }
  ],
  "warnings": [
    "n=8: smoke test only; 0 errors in 8 bounds the error rate at ~38% (rule of three)"
  ]
}
```

### Common errors

- Give exactly one of: `questions` + `examples`, `recipe` + `examples`, `case_file`, `from_batch`.
- Labels are true/false for noul, the option key for choice, the level index for score.

### Usually next

Apply `suggested_band` as `thresholds`; re-run with `compare_to` after any wording change; `batch_results` is not needed.

## recipe

Tool name: `mcp__openjev__recipe`. Run a named recipe (gate a command, screen text, done-gate, route, moderate, ...): builds the request(s) from typed inputs, reads, applies the recipe's policy table in code and returns a decision. dry_run returns the built requests without calling. Inputs follow openjev://recipes/{id}.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `recipe` | enum(act_or_ask\|alert_triage\|bulk_label\|claim_check\|command_gate\|done_gate\|duplicate_check\|entity_match\|injection_screen\|issue_triage\|judge_assert\|judge_pairwise\|memory_decide\|model_routing\|moderation\|multistep_tick\|rag_gate\|review_finding_filter\|rubric_score\|select_extraction\|semantic_filter\|semantic_lint\|skill_selection\|taxonomy_classify\|threshold_audit\|ticket_triage\|typed_call\|ui_decision\|verify_fields) | yes | a recipe id; the enum lists the ones this server loaded |
| `inputs` | object | yes | validated against the recipe's input_schema (resource openjev://recipes/{id}) |
| `profile` | string |  | recipe-defined: strict \| default \| lenient |
| `policy` | object |  | threshold overrides, keys from the recipe's policy table |
| `fail_mode` | enum(open\|closed) |  | default from the recipe |
| `dry_run` | boolean |  | return the built request(s) without calling; default false |
| `options` | object |  |  |
| `options.model` | string |  | Default OPENJEV_MCP_MODEL (openjev-latest). Valid names are those GET /v1/models lists (served and routed models, e.g. openjev-0.1, laya-1.0, verdict-1.4) plus the unlisted alia... |
| `options.samples` | integer |  | N billed reads averaged. 1 = fastest. Omit = 1 read + 3 free re-reads when uncertain.; minimum 1; maximum 32 |
| `options.steps` | integer |  | minimum 1; maximum 8 |
| `options.think` | integer |  | Thought budget in tokens; text-only states. 256-512 for lookahead, rules, arithmetic.; minimum 0; maximum 4096 |
| `options.sequential` | boolean |  | Chunks see earlier chunks' answers. Only matters when the questions span 2+ canvas chunks (above 10 questions, or fewer when score-heavy); text-only. |
| `options.timeout_ms` | integer |  | minimum 100; maximum 600000 |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `recipe` | string | yes |  |
| `decision` | string | yes | recipe-specific enum, e.g. allow\|ask\|deny |
| `reason` | string |  |  |
| `signals` | object | yes |  |
| `thresholds_used` | object |  |  |
| `degraded` | boolean | yes | true when the decision is the fail_mode fallback after an error |
| `error` | object |  |  |
| `error.code` | string | yes |  |
| `error.http_status` | integer\|null |  |  |
| `error.message` | string | yes |  |
| `error.hint` | string |  |  |
| `error.path` | string\|null |  |  |
| `error.retryable` | boolean |  |  |
| `error.retry_after_s` | number\|null |  |  |
| `error.request_id` | string\|null |  |  |
| `error.server_detail` | any |  |  |
| `requests` | integer |  |  |
| `built_requests` | array<object> |  |  |
| `answers` | object |  |  |
| `meta` | object |  |  |

### Example 1: command_gate on a destructive command

Call:

<!-- openjev-call: recipe -->
```json
{
  "recipe": "command_gate",
  "inputs": {
    "task": "Clean build artifacts in the project",
    "command": "rm -rf build/"
  },
  "options": {
    "samples": 1
  }
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: recipe -->
```json
{
  "recipe": "command_gate",
  "decision": "deny",
  "reason": "destructive=0.9065 >= 0.85",
  "signals": {
    "destructive": 0.9065,
    "exfiltrates": 4e-05,
    "remote_code": 0.00014,
    "out_of_scope": 0.00807,
    "weakens_security": 0.00063,
    "risk": 0.96999,
    "verdict": "allow",
    "verdict_p": 0.99833
  },
  "thresholds_used": {
    "deny_hazard": 0.85,
    "ask_hazard_low": 0.4,
    "deny_verdict_p": 0.6,
    "deny_risk": 2.3,
    "ask_risk_low": 1.3,
    "allow_hazard_max": 0.2,
    "allow_scope_max": 0.3,
    "allow_risk_max": 1
  },
  "degraded": false,
  "requests": 1,
  "meta": {
    "model": "openjev-0.1",
    "requests": 1,
    "latency_ms": 388.1
  }
}
```

### Common errors

- `OJ_INVALID_INPUT` `inputs`: validated against the recipe's `input_schema`; read `openjev://recipes/{id}` first.

Real error from a live call (inputs are validated against the recipe's input_schema; read openjev://recipes/command_gate first):

<!-- openjev-example -->
```json
{
  "call": {
    "recipe": "command_gate",
    "inputs": {
      "command": "ls -la"
    },
    "dry_run": true
  },
  "error": {
    "code": "OJ_INVALID_INPUT",
    "message": "inputs: 'task' is a required property",
    "path": "inputs",
    "retryable": false,
    "hint": "{\"type\":\"object\",\"required\":[\"task\",\"command\"],...}"
  }
}
```

### Usually next

Act on `decision`; for many inputs run the recipe once per input; `recipe` with `dry_run: true` first to see the built request.

## status

Tool name: `mcp__openjev__status`. Report OpenJev health, models, backend, limits and capabilities. probe also runs one small read to measure latency.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `probe` | boolean |  | also run one samples:1 noul read to measure latency; default false |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `healthy` | boolean | yes |  |
| `base_url` | string |  |  |
| `decide_models` | array | yes |  |
| `chat_models` | array |  |  |
| `aliases_accepted` | array |  |  |
| `resolved` | object |  |  |
| `auth` | enum(none\|bearer\|unknown) |  |  |
| `backend` | string |  | from GET /v1/limits (vllm, mlx, laya, verdict, clm, jevk5), else unknown |
| `latency_probe_ms` | number\|null |  |  |
| `limits` | object |  | prompt_tokens may be null (backend unknown); includes the batch caps |
| `limit_source` | enum(server\|default) |  |  |
| `capabilities` | object |  | per decide model, from /v1/limits or the 2.2 matrix |
| `capabilities.<key>` | object |  |  |
| `mcp` | object |  |  |
| `mcp.server_version` | string |  |  |
| `mcp.protocol_versions` | array<string> |  |  |
| `mcp.batch_max_inflight` | integer |  |  |
| `warnings` | array |  |  |

### Example 1: Health check

Call:

<!-- openjev-call: status -->
```json
{
  "probe": false
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: status -->
```json
{
  "healthy": true,
  "base_url": "http://127.0.0.1:8080",
  "decide_models": [
    "openjev-latest",
    "openjev-0.1"
  ],
  "chat_models": [
    "diffusiongemma-26b"
  ],
  "aliases_accepted": [
    "jev-latest",
    "jev-preview"
  ],
  "auth": "none",
  "backend": "unknown",
  "limit_source": "default",
  "limits": {
    "questions": 256,
    "choice_options": 255,
    "score_levels": 10,
    "images": 8,
    "image_bytes": 5242880,
    "body_bytes": 67108864,
    "batch": {
      "concurrency_max": 4,
      "max_items_per_call": 100,
      "max_items": 5000,
      "max_inflight_batch": 4
    }
  },
  "mcp": {
    "server_version": "0.5.0",
    "transport": "http"
  },
  "warnings": []
}
```

### Common errors

- `healthy: false` or `OJ_UNREACHABLE`: OpenJev on its base_url is down. Report it; the user restarts it.

### Usually next

Any tool. If `healthy` is false, stop and tell the user; do not retry in a loop.

## generate

Tool name: `mcp__openjev__generate`. Draft short text on the local chat model (OpenAI-style passthrough). NOT for decisions: use ask, yes_no, classify or score for those. Guards the MLX bugs: role is required, an empty reply is retried once, max_tokens is clamped to 8192; on MLX newlines are dropped from replies.

**TEXT ONLY: drafts prose on the chat model. It cannot decide anything, so never use it for a yes/no, a label or a score.**

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `messages` | array<object> | yes | minItems 1 |
| `messages.role` | enum(system\|user\|assistant) | yes |  |
| `messages.content` | string | yes |  |
| `max_tokens` | integer |  | default 512; minimum 1; maximum 8192 |
| `response_format` | object |  |  |
| `stop` | array<string> |  |  |
| `model` | string |  |  |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `content` | string | yes |  |
| `finish_reason` | enum(stop\|length) | yes |  |
| `usage` | object |  |  |
| `retried` | boolean |  |  |
| `warnings` | array |  |  |

### Example 1: Short text draft (text only, never a decision)

Call:

<!-- openjev-call: generate -->
```json
{
  "messages": [
    {
      "role": "user",
      "content": "Say hello in five words."
    }
  ],
  "max_tokens": 16
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: generate -->
```json
{
  "content": "Hello to you, friend.",
  "finish_reason": "stop",
  "usage": {
    "prompt_tokens": 19,
    "completion_tokens": 6,
    "total_tokens": 25
  },
  "retried": false,
  "warnings": [
    "MLX backend: newlines are dropped from replies; tools, logprobs and image parts are ignored"
  ]
}
```

### Common errors

- `OJ_INVALID_INPUT` `messages.N.role`: role is required; `max_tokens` is clamped to 8192.

### Usually next

Nothing in the pipeline reads it; use it only to draft prose after the decisions are made.

## batch_results

Tool name: `mcp__openjev__batch_results`. Query, export and compare a finished or partial batch output without spending reads: review queue, sorted and filtered rows, statistics, csv/markdown/ojui-batch/jsonl exports and Jensen-Shannon comparison with a second output. Makes no network request.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `path` | string | yes | a batch output .jsonl inside the allowed roots (first line is the batch header) |
| `view` | enum(rows\|review\|stats) |  | default "rows" |
| `filter` | object |  |  |
| `filter.status` | enum(ok\|error\|any) |  | default "any" |
| `filter.min_confidence_below` | number |  | Playground low-confidence filter: keep rows where any question (or question, when given) is below; minimum 0; maximum 1 |
| `filter.question` | string |  |  |
| `filter.choice` | string |  | with question: rows whose choice is this key |
| `filter.band` | enum(yes\|no\|grey) |  | with question: noul band |
| `filter.needs_review` | boolean |  |  |
| `filter.audit` | boolean |  |  |
| `filter.ids` | array<string> |  | maxItems 500 |
| `sort_by` | enum(index\|value\|confidence) |  | value: choice key, score expected level, noul p of sort_question; confidence: min confidence over questions, or of sort_question; default "index" |
| `sort_question` | string |  |  |
| `order` | enum(asc\|desc) |  | default "asc" |
| `limit` | integer |  | default 50; minimum 1; maximum 500 |
| `cursor` | string |  | next_cursor of the previous batch_results call |
| `detail` | enum(compact\|full) |  | default "compact" |
| `export` | object |  |  |
| `export.format` | enum(csv\|markdown\|ojui-batch\|jsonl) | yes |  |
| `export.path` | string |  | created new (.csv, .md, .json, .jsonl); omitted = inline, up to 64 KiB, truncated with a warning |
| `export.filtered` | boolean |  | export only the rows that pass filter; default false |
| `compare_to` | object |  |  |
| `compare_to.path` | string | yes | a second batch output .jsonl over the same ids |
| `compare_to.question_map` | object |  | question id in path -> question id in compare_to.path; default same ids |
| `compare_to.key_map` | object |  | per question: option key in compare_to.path -> option key in path; keys not listed map to themselves |

### Output

| Field | Type | Required | Notes |
|---|---|---|---|
| `view` | string | yes |  |
| `rows` | array<object> |  | BatchRow (full) or compact rows; last row per id |
| `review_queue` | array<object> |  |  |
| `stats` | object |  |  |
| `stats.n` | integer |  |  |
| `stats.ok` | integer |  |  |
| `stats.errors` | integer |  |  |
| `stats.needs_review` | integer |  |  |
| `stats.per_question` | object |  |  |
| `stats.input_tokens` | integer |  |  |
| `stats.output_tokens` | integer |  |  |
| `stats.latency_ms_p50` | number |  |  |
| `stats.latency_ms_p95` | number |  |  |
| `matched` | integer |  |  |
| `export` | object |  |  |
| `export.format` | string |  |  |
| `export.path` | string\|null |  |  |
| `export.inline` | string\|null |  |  |
| `export.bytes` | integer |  |  |
| `export.truncated` | boolean |  |  |
| `compare` | object |  |  |
| `compare.matched` | integer |  |  |
| `compare.only_in_a` | array<string> |  |  |
| `compare.only_in_b` | array<string> |  |  |
| `compare.max_jsd` | number\|null |  |  |
| `compare.per_question` | object |  |  |
| `next_cursor` | string\|null |  |  |
| `warnings` | array<string> |  |  |
| `meta` | object | yes |  |

### Example 1: Review queue of the batch written above (no reads spent)

Call:

<!-- openjev-call: batch_results -->
```json
{
  "path": "/abs/path/to/out/batch1.jsonl",
  "view": "review"
}
```

Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):

<!-- openjev-result: batch_results -->
```json
{
  "view": "review",
  "matched": 3,
  "next_cursor": null,
  "review_queue": [
    {
      "id": "t1",
      "question": "billing",
      "reason": "noul_grey",
      "confidence": 0.07654
    }
  ],
  "warnings": [],
  "meta": {
    "model": "openjev-0.1",
    "requests": 0
  }
}
```

### Common errors

- `OJ_NOT_FOUND`: `path` does not exist yet (run `batch` first).
- Paths must be inside the allowed roots; exports are created new.

### Usually next

Re-run `batch` with `only_ids` for rows to retry, `calibrate` with `from_batch` for accuracy, or export csv/markdown.
