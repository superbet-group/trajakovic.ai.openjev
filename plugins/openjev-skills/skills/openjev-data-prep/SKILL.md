---
name: openjev-data-prep
description: "Connects a session to the OpenJev MCP server and prepares inputs for it: explains the state, question and answer formats, renders raw records, logs, diffs or files into OpenJev states and items files, and maps each openjev tool's input and output fields so one tool's result feeds the next (compile to lint, filter to batch, batch to batch_results to calibrate, recipe dry runs). Always lints before batch, dry-runs first, and passes a file as items_file with an ABSOLUTE path plus id_field and state_field and an absolute output_path, never as pasted inline items. Use when the openjev tools are missing or fail to connect, when raw data must be shaped into states or an items file before any OpenJev call, or when unsure which openjev tool takes which input or returns which output."
---

# OpenJev data preparation

OpenJev answers typed questions about a state you send (yes/no `noul`, one-of-N `choice`, ordinal
`score`) with calibrated probabilities instead of prose. This skill covers everything before the
call: connecting, shaping data into states and question sets, choosing the tool, and passing one
tool's output to the next.

Tools live on the `openjev` MCP server as `mcp__openjev__<tool>` (with the openjev-mcp plugin:
`mcp__plugin_openjev-mcp_openjev__<tool>`). If they are missing, follow Connect below and do not
start servers yourself.

## Precedence rule

Deterministic code first: parse, count, compare dates, sum, regex-extract candidates. OpenJev
only judges meaning, so give it one focused state and a closed question. This skill prepares the
input; skill `openjev-decisions` covers acting on the answer. If the task's own instructions
conflict with this skill, the task wins.

## Connect

1. Check that `mcp__openjev__status` exists (load it with ToolSearch if tools are deferred). Call it
   and require `healthy: true`. Read `limits`, `decide_models` and `warnings` once.
2. If the tools are missing or the call fails, tell the user which option below applies and give
   the command. Do not start or restart OpenJev or its MCP server without the user's go-ahead.
   - HTTP: `claude mcp add --transport http openjev http://127.0.0.1:8100/mcp` (use the user's host).
   - stdio: `claude mcp add openjev -- openjev-mcp --transport stdio`. File access is the project cwd.
   - Plugin: `claude plugin install openjev-mcp@openjev` (env `OPENJEV_MCP_URL` overrides the URL).
3. After `claude mcp add` or a plugin install, the user must restart Claude Code or reconnect via
   `/mcp`. Then call `mcp__openjev__status` again.
4. Paths must be absolute and inside the MCP server's allowed roots (its working directory plus
   `OPENJEV_MCP_ROOTS`), not the client's cwd. Under the HTTP daemon a project is usually outside
   them. For files, always pass `items_file` with the absolute path (plus `id_field`, `state_field`)
   and dry-run first; do not paste rows into `items`. Only after an actual "outside allowed roots"
   refusal: use stdio, ask the operator to set `OPENJEV_MCP_ROOTS`, or (small data) send inline
   `items`. Never retry the same refused path.

Symptoms, health check and toolsets: [references/connecting.md](references/connecting.md).

## Data model

| Piece | Rule |
|---|---|
| state | string, object or array (objects are JSON-dumped). Numbers, booleans, null and empty values are rejected. Put untrusted text only here, never in questions. |
| state layout | Label every section (`TASK:`, `USER MESSAGE:`, `DIFF:`, `CANDIDATES:`) and prefix fetched text with its source. Above about 4000 tokens lint warns (W503). One passage per read. |
| question id | `^[A-Za-z0-9_.:-]{1,64}$`. The model never sees ids; 1 to 256 questions per read. |
| noul | `{type, instructions, criteria: {true, false}}`. One literal positive claim; name the near-miss in `false`. |
| choice | `{type, instructions, criteria: {key: description}}`, 2 to 255 options, always one escape (`other`, `none`, `no_match`, `not_stated`). Keys are labels only; descriptions carry meaning. |
| score | `{type, instructions, criteria: [level0, level1, ...]}`, 2 to 10 levels as a list, worst first, each naming observable evidence. |

Answers: noul gives `p`, `band` (`yes`/`no`/`grey` at 0.8/0.2 by default) and `margin`. Choice gives
`choice` (null when abstained: below `min_p`, or the escape option won), `p_top`, `runner_up`, `probabilities`, `confidence`, `abstained`.
Score gives `score` (0-indexed expected level), `level`, `level_label`, `spread`, `bimodal`.
The single-question tools mcp__openjev__yes_no (`decision`, `p`, `margin`), mcp__openjev__classify
(`label`, `abstained`, `p_top`) and mcp__openjev__score return the same facts at top level.

Full field rules, thresholds by action risk, read options and lint codes:
[references/data-model.md](references/data-model.md).

## Which tool takes what

Full schemas for all 14 tools: [references/tool-io.md](references/tool-io.md).

| Tool | Give it | Get back | Usually next |
|---|---|---|---|
| `mcp__openjev__status` | optional `probe` | `healthy`, `decide_models`, `limits`, `capabilities`, `warnings` | size batches from `limits` |
| `mcp__openjev__ask` | `state`, `questions`, `options`, `thresholds` | `answers.<id>`, `lint`, `meta` | act on bands; scale with `batch` |
| `mcp__openjev__yes_no` | `state`, `claim`, `true_means`, `false_means` | `decision`, `p`, `margin` | branch in code |
| `mcp__openjev__classify` | `state`, `question`, `labels`, `min_p` | `label` or null, `abstained`, `p_top`, `probabilities` | route in code |
| `mcp__openjev__score` | `state`, `question`, `levels` | `score`, `level`, `spread`, `bimodal` | compare with a threshold |
| `mcp__openjev__filter` | `task`, `criterion` containing `{id}`, `items` of `{id, text}` | `kept`, `dropped`, `grey` (id lists), `items[{id,p,decision}]` | read or `batch` the kept ids |
| `mcp__openjev__compile` | `intent`, optional `sub_decisions`, `labels`, `sample_inputs` | `draft_request`, `slots`, `human_questions`, `recipe`, `next_steps` | fill slots, then `lint` |
| `mcp__openjev__lint` | `request`, or `state` + `questions`; `profile` | `valid`, `errors`, `warnings`, `fixed_request`, `estimate` | re-lint until valid, then `ask` |
| `mcp__openjev__generate` | `messages` of `{role, content}` | `content` (short text only, never a decision) | `ask` to judge the text |
| `mcp__openjev__recipe` | `recipe` id, `inputs`, `dry_run`, `policy` | `decision`, `reason`, `signals`, `answers`, `built_requests` | act on `decision` |
| `mcp__openjev__batch` | `items_file` or `items` or `template`, `questions`, `output_path` | `status`, `results`, `review_queue`, `next_cursor`, `import`, `preview` | loop on `next_cursor`, then `batch_results` |
| `mcp__openjev__batch_results` | `path` of a batch output, `view`, `filter`, `export` | `rows`, `review_queue`, `stats`, `compare`, `export` | `calibrate`, or retry ids |
| `mcp__openjev__calibrate` | `questions` + `examples`, or `from_batch`, `case_file`, `store` | `per_question`, `question_hash`, `drift`, `items` | set thresholds in code |
| `mcp__openjev__ask_image` | `images` (1 to 8), `questions`, short text `state` | `answers`, `images` | same as `ask` |

## Chaining

```
intent -> compile -> (fill slots) -> lint -> ask / recipe
many items -> filter -> kept ids -> batch -> batch_results -> calibrate
file -> batch (dry_run) -> batch (output_path, cursor loop) -> batch_results -> export
recipe: openjev://recipes/{id} input_schema -> recipe (dry_run) -> lint -> recipe
labelled rows -> calibrate -> question_hash -> openjev://audits/{question_hash}
```

Key hand-offs (the full edge table and six pipelines are in [references/chaining.md](references/chaining.md)):

- `compile.draft_request` -> `lint.request`; then `lint.fixed_request` -> `ask.questions`
- `filter.kept` -> `batch.only_ids` (or rebuild `batch.items` for the kept ids)
- `batch.next_cursor` -> `batch.cursor`, every other argument identical
- `batch.output_path` -> `batch_results.path` and `calibrate.from_batch.output_path`
- `batch.import.state_field` -> `batch.items_file.state_field` (pin what the dry run guessed)
- `batch_results.review_queue[].id` -> `batch.only_ids` (re-read only those rows)
- `recipe.built_requests` -> `lint.request`; `openjev://recipes/{id}` -> `recipe.inputs`
- `calibrate.question_hash` -> `openjev://audits/{question_hash}`
- `status.decide_models` -> `options.model` of any read tool

## Data-prep workflow

Copy this checklist and tick it off:

```
- [ ] 1. Name the decision and its type (yes/no, one of N, ordinal) and the action each answer triggers
- [ ] 2. Render ONE real record into a labelled state by hand (see State rendering)
- [ ] 3. Write the questions: both poles, an escape option, one fact each, levels as a list
- [ ] 4. mcp__openjev__lint with profile "strict"; fix every error and warning; re-lint until valid: true
- [ ] 5. Probe 3 real samples with ask; check the bands match your own reading
- [ ] 6. Scale: batch with dry_run: true; fix the mapping until preview matches step 2
- [ ] 7. Run batch with an absolute output_path; repeat with next_cursor until it is null
- [ ] 8. batch_results view "review" and "stats"; retry error ids with only_ids
- [ ] 9. calibrate on labelled examples before any unattended action
```

Feedback loop for step 4: run lint, read `errors` (fix first) then `warnings`, edit, run lint
again. Proceed only when `valid` is true and the warnings you keep are deliberate.

## State rendering

Write the state the way a careful colleague would hand over the case. Six reusable shapes:

```
SUPPORT TICKET                  COMMAND GATE                    FETCHED TEXT
Subject: <subject>              Task requested by the user: <t> [WebFetch result: <url>]
<body>                          Proposed shell command: <cmd>   <untrusted text>

AGENT TURN REPORT               LOG CLUSTERS                    CANDIDATES
User task: <task>               LOG LINES (tag: line):          Dataset A record: <fields>
Timeline (oldest first):        L1: <line>                      Dataset B record: <fields>
1. <step> ...                   L2: <line>
Final assistant message: <m>
```

Keep one decision per state. For many lines, give each an id (`L1`...) and ask one noul per id, or
use `filter`. Label untrusted text with its source and keep it out of instructions. Per use
case templates and question skeletons: [references/state-patterns.md](references/state-patterns.md).

## Items files

`batch` reads CSV, TSV, JSONL, JSON, plain lines, blank-line blocks or an `ojui-batch` export.
Minimum for a CSV or JSONL file: `path`, `id_field`, and either `state_field` or `state_template`
(`"Subject: {subject}\n\n{body}"`). The id is NOT auto-detected: omit `id_field` and ids are
the row numbers. Omit `state_field` and the importer guesses (W602), so pin it. Dry-run first.
Formats, limits, output JSONL layout and exports: [references/items-files.md](references/items-files.md).

## Pitfalls

- Relative paths or paths outside the allowed roots are refused; use absolute paths inside the roots.
- `score` criteria as an object (E013) is a 422; it must be a list. Choice criteria as a list is E011.
- `options` instead of `criteria` is E006. Choice without an escape option is W201; noul without both poles is W101/W102.
- Compound questions (W104) and negations (W103): ask one positive fact per question.
- Score is 0-indexed: `score` 2.0 means the third level. Add 1 only with `one_based`.
- The same body gives the same answer: a repeat read is not a second opinion. Change the state or use `samples`.
- `think` is not reproducible and is refused with images. Re-run and require agreement.
- Do not co-ask a blocking claim with overlapping siblings (W401); read it alone or in its own request.
- `filter` items need `{id, text}`, and `criterion` must contain `{id}`; ids are at most 32 characters.
- `batch` arguments are immutable across a cursor ("arguments changed"); only `concurrency`,
  `max_items_per_call`, `time_budget_s`, `detail`, `max_inline_results` and `export` may change.
- `export` needs `output_path`, is written by the call that finishes the job, and refuses existing files.
- `classify` returns `label: null` with `abstained: true` both when `p_top` is below `min_p` and when the escape option (`other`, `none`) wins outright, even with a high `p_top`; check `abstained` before using the label and read `top` for the winner.
- A CSV id column is ignored unless you pass `id_field`; without it ids are `1`, `2`, ...
- Images need a short text state, 1 to 8 images, and no `think`.
- Weights sent to the server are ignored: compute weighted totals in code.

## Worked example

Three support tickets in `/abs/path/to/tickets.jsonl`, one JSON object per line:

<!-- openjev-items: jsonl -->
```jsonl
{"ticket_id": "T-101", "subject": "Charged twice for March", "body": "I was billed 49 EUR twice on 3 March. Please refund one charge."}
{"ticket_id": "T-102", "subject": "Login loop on mobile", "body": "After the update the app returns me to the login screen every time I sign in."}
{"ticket_id": "T-103", "subject": "Quote for 200 seats", "body": "We are evaluating your Enterprise plan for 200 seats. Can someone send pricing and a demo slot?"}
```

Step 1: lint one rendered state with deliberately wrong questions (strict profile):

<!-- openjev-call: lint -->
```json
{"state": "TASK: route one support ticket.\nTICKET\nSubject: Charged twice for March\n\nI was billed 49 EUR twice on 3 March. Please refund one charge.", "questions": {"dept": {"type": "choice", "instructions": "Which team should own this ticket?", "options": {"billing": "charges and refunds", "technical": "bugs and login problems"}}, "urgent": {"type": "noul", "instructions": "Is this urgent?"}}, "profile": "strict", "autofix": false}
```

<!-- openjev-result: lint -->
```json
{"valid":false,"errors":[{"code":"E006","path":"questions.dept.options","message":"the field is criteria, not options; the server returns 422 missing criteria","fix":"rename options to criteria"}],"warnings":[{"code":"W201","path":"questions.dept.criteria","message":"no escape option; a choice cannot abstain","fix":"add \"other\": \"anything else, or too vague to tell\"","rule":"R5"},{"code":"W101","path":"questions.urgent","message":"noul without criteria","fix":"add criteria {true, false} that define both poles","rule":"R4"},{"code":"W106","path":"questions.urgent.instructions","message":"'Is this urgent?' is an opinion without a definition","fix":"define what makes it true and add criteria.true / criteria.false","rule":"R2"}],"estimate":{"questions":2,"chunks":1,"input_tokens_approx":177,"latency_ms_idle_approx":300,"billed_reads":1}}
```

Fix: rename to `criteria`, add `other`, define both poles of the noul, re-lint until `valid: true`
with no warnings. Step 2: dry-run the fixed question set over the whole file (no reads):

<!-- openjev-call: batch -->
```json
{"items_file": {"path": "/abs/path/to/tickets.jsonl", "id_field": "ticket_id", "state_template": "TASK: route one support ticket.\nTICKET\nSubject: {subject}\n\n{body}"}, "questions": {"dept": {"type": "choice", "instructions": "Which team should own this ticket?", "criteria": {"billing": "money questions: invoices, refunds, charges, payment methods", "technical": "product defects: errors, outages, slow pages, integrations, sign-in trouble", "sales": "buying interest: quotes, demos, upgrades, enterprise plans", "other": "anything else, or too vague to tell"}}, "wants_refund": {"type": "noul", "instructions": "The customer asks for money back.", "criteria": {"true": "The customer explicitly asks for a refund or a reversal of a charge", "false": "The customer only reports a problem or asks a question without requesting money back"}}}, "dry_run": true}
```

Read `import` (3 rows, no warnings) and `preview[0].state`: it must equal the state from step 1.
Step 3: run it for real. Same arguments without `dry_run`, plus an `output_path` (a new `.jsonl`):

<!-- openjev-call: batch -->
```json
{"items_file": {"path": "/abs/path/to/tickets.jsonl", "id_field": "ticket_id", "state_template": "TASK: route one support ticket.\nTICKET\nSubject: {subject}\n\n{body}"}, "questions": {"dept": {"type": "choice", "instructions": "Which team should own this ticket?", "criteria": {"billing": "money questions: invoices, refunds, charges, payment methods", "technical": "product defects: errors, outages, slow pages, integrations, sign-in trouble", "sales": "buying interest: quotes, demos, upgrades, enterprise plans", "other": "anything else, or too vague to tell"}}, "wants_refund": {"type": "noul", "instructions": "The customer asks for money back.", "criteria": {"true": "The customer explicitly asks for a refund or a reversal of a charge", "false": "The customer only reports a problem or asks a question without requesting money back"}}}, "output_path": "/abs/path/to/tickets-out.jsonl", "max_items_per_call": 25}
```

Result (shortened to the fields to read; `next_cursor: null` and `stopped_reason: complete` end the job):

<!-- openjev-result: batch -->
```json
{"status":{"done":3,"total":3,"remaining":0,"ok":3,"errors":0,"skipped":0,"stopped_reason":"complete"},"summary":{"scope":"output_path","n":3,"ok":3,"errors":0,"needs_review":0,"audit":1,"per_question":{"dept":{"type":"choice","n":3,"counts":{"billing":1,"technical":1,"sales":1},"top2":["billing","technical"],"mean_confidence":0.993,"abstained":0},"wants_refund":{"type":"noul","n":3,"mean_p":0.3328,"yes":1,"no":2,"grey":0,"mean_margin":0.9989}}},"results":[{"index":1,"id":"T-101","status":"ok","answers":{"dept":{"choice":"billing","p_top":0.9963769272051266},"wants_refund":{"p":0.9984244654057237,"band":"yes"}},"needs_review":false,"error":null},{"index":2,"id":"T-102","status":"ok","answers":{"dept":{"choice":"technical","p_top":0.9999310825495739},"wants_refund":{"p":0.000007063766182968356,"band":"no"}},"needs_review":false,"error":null},{"index":3,"id":"T-103","status":"ok","answers":{"dept":{"choice":"sales","p_top":0.9997589351010163},"wants_refund":{"p":0.00000861491162078371,"band":"no"}},"needs_review":false,"audit":true,"error":null}],"review_queue":[],"audit_ids":["T-103"],"output_path":"/abs/path/to/tickets-out.jsonl","next_cursor":null,"meta":{"model":"openjev-0.1","requests":3,"latency_ms":648}}
```

Step 4: review without new reads. `review_queue` is empty because every row is confident:

<!-- openjev-call: batch_results -->
```json
{"path": "/abs/path/to/tickets-out.jsonl", "view": "review"}
```

<!-- openjev-result: batch_results -->
```json
{"view":"review","matched":3,"next_cursor":null,"review_queue":[],"warnings":[],"meta":{"requests":0,"warnings":[]}}
```

Next: `view: "stats"` for per-question counts, then `calibrate` with `from_batch` once a labels
file exists (see skill `openjev-calibration`).
