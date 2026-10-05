# Chaining OpenJev tools

## Contents
- Reading the Edges table
- Edges
- Pipeline A: intent to a linted question set
- Pipeline B: many small items, filter then batch
- Pipeline C: a file through batch, review and calibrate
- Pipeline D: recipe inputs, dry run, lint, run
- Pipeline E: a library template over your own states
- Pipeline F: audits and comparing runs
- Retrying only some rows

## Reading the Edges table

Each edge says: this field of one tool's output becomes this argument of the next call. Cells are
`tool.path`: the path through the output schema (From) or the input schema (To); `[]` marks an
array. An `openjev://` cell is an MCP resource read with the resource reader on server `openjev`.
Read an edge as "take X, pass it as Y, when Z".

## Edges

| From | To | When |
|---|---|---|
| `status.decide_models` | `ask.options.model` | pick a served model name instead of guessing |
| `status.decide_models` | `batch.options.model` | same model for every row of a run |
| `status.limits.batch.max_items_per_call` | `batch.max_items_per_call` | never ask for more rows per call than the server allows |
| `compile.draft_request` | `lint.request` | always before the first read; fill every `<SLOT:name>` first |
| `compile.recipe.id` | `recipe.recipe` | compile routed the intent to a recipe: run that recipe directly |
| `lint.fixed_request` | `lint.request` | autofix changed something: re-lint the fixed body until valid |
| `lint.fixed_request` | `ask.questions` | take `questions` (and `state`) from the fixed request |
| `filter.kept` | `batch.only_ids` | run the full items file but only for the surviving ids |
| `filter.kept` | `batch.items` | rebuild `{id, state}` items for the survivors when the source is inline |
| `batch.import.state_field` | `batch.items_file.state_field` | pin the column the dry run guessed (W602) |
| `batch.next_cursor` | `batch.cursor` | same job, every other argument identical |
| `batch.output_path` | `batch_results.path` | triage, stats, export, compare |
| `batch.output_path` | `calibrate.from_batch.output_path` | the rows carry the questions; labels come from a file |
| `batch.output_path` | `batch_results.compare_to.path` | compare a second run over the same ids |
| `batch.results[].id` | `batch.only_ids` | re-run named rows, for example the ones with status error |
| `batch_results.review_queue[].id` | `batch.only_ids` | re-read only the rows a human flagged, with more samples |
| `batch_results.next_cursor` | `batch_results.cursor` | next page of rows or review queue |
| `recipe.built_requests[]` | `lint.request` | inspect a dry run before spending reads |
| `openjev://recipes/{id}` | `recipe.inputs` | the recipe's `input_schema` tells you the required fields |
| `openjev://templates/{id}` | `batch.template` | library question set, with or without your own states |
| `calibrate.question_hash` | `openjev://audits/{question_hash}` | read the stored audit record for this exact wording |
| `calibrate.per_question.suggested_band` | `ask.thresholds` | fitted `no_at` and `yes_at` for the next read |
| `calibrate.per_question.suggested_band` | `batch.thresholds` | same fitted band for a whole run |
| `generate.content` | `ask.state` | judge a drafted text before it is used |

## Pipeline A: intent to a linted question set

Use when you know the goal in prose but not the questions.

1. `compile` with `intent` (and `labels` if you know the options, `sample_inputs` to probe).

<!-- openjev-call: compile -->
```json
{"intent": "tell me if support emails are angry and which team should take them", "labels": {"team": {"billing": "charges, refunds, invoices", "technical": "bugs, outages, login problems", "other": "anything else"}}}
```

2. Read the result. Real output (`human_questions` and `warnings` are cut to two entries each):

<!-- openjev-result: compile -->
```json
{"recipe":{"id":"ticket_triage","p":0.9885973803175656,"runner_up":"none","not_a_decision":false},"draft_request":{"model":"openjev-latest","state":"{<SLOT:text>}","questions":{"dept":{"type":"choice","instructions":"Which team should own this ticket?","criteria":{"<SLOT:teams: label>":"<SLOT:teams: description>","other":"anything else, or too vague to tell"}},"refund":{"type":"noul","instructions":"Is the customer asking for money back (a refund or a credit)?"},"churn":{"type":"noul","instructions":"Is the customer threatening to cancel or leave?"}}},"slots":[{"name":"text","path":"state","why":"the text to judge","example":"<the text>"},{"name":"teams: label","path":"questions.dept.criteria.<SLOT:teams: label>","why":"needs a description only the human knows","example":"label: what an input with this label looks like"},{"name":"teams: description","path":"questions.dept.criteria.<SLOT:teams: label>","why":"needs a description only the human knows","example":"label: what an input with this label looks like"}],"human_questions":["What does 'teams: label' mean for you? (questions.dept.criteria.<SLOT:teams: label>)","Recipe default yes_at = 0.8: does that threshold fit your risk?"],"lint":{"valid":true,"errors":[],"warnings":[{"code":"W202","path":"questions.dept.criteria","message":"option descriptions are missing, equal to their keys or shorter than 3 words","fix":"describe what inputs of each option look like","rule":"R6"},{"code":"W101","path":"questions.refund","message":"noul without criteria","fix":"add criteria {true, false} that define both poles","rule":"R4"}]},"next_steps":["fill the <SLOT:...> placeholders in draft_request (probe and calibrate wait for them)","answer human_questions","add 10-20 labelled examples and run calibrate","save as a recipe file"]}
```

3. Replace every `<SLOT:...>` in `draft_request` with real text: the state becomes your rendered
   state, `<SLOT:teams: label>` keys become your team names with descriptions. Ask the user for the
   slots only they can answer (`human_questions`).
4. `lint` with `request` set to the filled `draft_request` and `profile: "strict"`. Fix the
   warnings the draft lists (noul criteria, option descriptions). Re-lint until `valid: true`.
5. `ask` with the `state` and `questions` of the fixed request, or `recipe` with the routed
   `recipe.id` when the intent matched a recipe.
6. Add 10 to 20 labelled examples and `calibrate` (skill `openjev-calibration`).

## Pipeline B: many small items, filter then batch

Use when the items are lines, files or hunks of one task and you only want the relevant ones.

1. `filter` packs up to `pack_size` items (default 10, maximum 30) per read and asks one
   noul per item id. `criterion` is a string that contains `{id}`; each item is `{id, text}` with an id of
   at most 32 characters from `A-Za-z0-9_.:-`.

<!-- openjev-call: filter -->
```json
{"task": "Find log lines that show a real failure of the payment service.", "criterion": "Is log line {id} a real failure of the payment service?", "items": [{"id": "L1", "text": "ERROR payments: charge ch_91 declined by processor, retries exhausted"}, {"id": "L2", "text": "INFO payments: health check ok in 12ms"}, {"id": "L3", "text": "WARN payments: retry 1/3 for charge ch_92 succeeded"}], "true_means": "The line reports an operation that failed and was not recovered", "false_means": "The line is routine, informational, or a failure that recovered by itself"}
```

<!-- openjev-result: filter -->
```json
{"kept":["L1"],"dropped":["L2","L3"],"grey":[],"items":[{"id":"L1","p":0.998875575755482,"decision":"keep"},{"id":"L2","p":0.0003301017893381897,"decision":"drop"},{"id":"L3","p":0.0001879319519994352,"decision":"drop"}],"meta":{"model":"openjev-0.1","request_ids":["req_e0b95fbe78e56887047d4597a77aa9ff"],"body_hashes":["sha256:7b31842e410064ee1bbcae1478a6c9d2a6ed285599777d77ed6381a6f769584d"],"requests":1,"latency_ms":474,"input_tokens":283,"output_tokens":0,"chunks_estimate":1,"timeout_ms_used":30000,"warnings":[],"server_ms":471.8}}
```

2. `kept`, `dropped` and `grey` are lists of ids; `items` carries `p` and `decision` per id.
   Decide what `grey` means up front with the `grey` argument (`keep` default, `drop`, `review`).
   `keep_at` default 0.6, `drop_at` default 0.2.
3. Hand the survivors on: build `items` for `batch` from the kept ids and the original
   texts, or keep the file and pass `only_ids` set to `kept`.

<!-- openjev-call: batch -->
```json
{"items": [{"id": "L1", "state": "ERROR payments: charge ch_91 declined by processor, retries exhausted"}], "questions": {"sev": {"type": "score", "instructions": "How severe is the problem shown in this log line?", "criteria": ["cosmetic, no user impact", "degraded for some users, retries recover", "a payment failed for a customer", "payments are down for many customers"]}}, "output_path": "/abs/path/to/payment-sev.jsonl"}
```

For ranking instead of dropping, pass `graded: {levels: [...]}` to `filter` (a score per item) or
`pick_best: {question, none_means}` to also get one `best` id per pack.

## Pipeline C: a file through batch, review and calibrate

1. `batch` with `dry_run: true`: read `import` (`format`, `state_field`, `id_field`, `row_count`,
   `warnings`), `preview[0].state`, `estimate`. Fix the mapping until the preview is right.
2. `batch` with `output_path` (absolute, new `.jsonl`) and `max_items_per_call`. Read `status`:
   `done`, `total`, `remaining`, `ok`, `errors`, `skipped`, `stopped_reason`.
3. While `next_cursor` is not null, call `batch` again with identical arguments plus
   `cursor: <next_cursor>`. Only `concurrency`, `max_items_per_call`, `time_budget_s`, `detail`,
   `max_inline_results` and `export` may differ. `stopped_reason` values: `max_items_per_call`
   and `time_budget` mean call again; `backpressure` means wait, then call again; `complete`
   ends the job; `error_abort` appears only with `on_error: "abort"`.
4. After an interruption: same arguments, same `output_path`, `resume: true`, no cursor.
5. `batch_results` with `path` set to `output_path`: `view: "review"` (least confident first, with
   a reason), `view: "stats"` (per question counts and mean confidence), `view: "rows"` with
   `filter`, `sort_by` and `limit` for detail.
6. Labelled rows: write a labels file (`.csv` or `.jsonl`, an id column and one label column per
   question), then `calibrate` with `from_batch` (no new reads).

<!-- openjev-call: calibrate -->
```json
{"from_batch": {"output_path": "/abs/path/to/tickets-out.jsonl", "labels_path": "/abs/path/to/tickets-labels.csv", "id_field": "ticket_id", "label_fields": {"wants_refund": "refund", "dept": "team"}}, "store": "/abs/path/to/tickets-audit.json"}
```

   Labels: noul `true`/`false` (also `yes`/`no`, `1`/`0`), choice the option key, score the level
   index. Read `per_question.<id>`: `accuracy_at_0.5`, `separable`, `gap`, `suggested_band`,
   `overlap_ids`, `confusion` (choice). `calibrate` can also stop early and return a
   `next_cursor`; pass it back as `cursor` with the same arguments.
7. Export for people: `batch_results` with `export: {format: "csv", path: "/abs/path/to/out.csv"}`.

## Pipeline D: recipe inputs, dry run, lint, run

1. Read `openjev://recipes` for the index and `openjev://recipes/{id}` for one recipe. Its
   `input_schema` lists required and optional inputs; its policy table lists tunable thresholds.
2. Build `inputs` from that schema. `recipe` with `dry_run: true` returns the exact requests
   without reading, in `built_requests`.

<!-- openjev-call: recipe -->
```json
{"recipe": "command_gate", "inputs": {"task": "Free disk space in the project", "command": "rm -rf ./build"}, "dry_run": true}
```

3. Pass `built_requests[0]` to `lint` (`request`) when you want the cost and warnings first.
4. Run it without `dry_run`. Act on `decision` (a recipe-specific word such as `allow`, `ask`,
   `deny`); read `reason` and `signals`. `degraded: true` means the fail-mode fallback was used
   because a read failed; `fail_mode` is `open` or `closed`. Override cut-offs only through
   `policy`, with keys from the recipe's policy table.

## Pipeline E: a library template over your own states

1. Read `openjev://templates` for the index (id, title, question and state counts) and
   `openjev://templates/{id}` for the questions and sample states.
2. `batch` with `template` alone runs the template's sample states. With `items` or `items_file`
   the template supplies only the questions, so your states go through a tested question set.

<!-- openjev-call: batch -->
```json
{"template": "moderation_guardrails_block_category", "items": [{"id": "m1", "state": "Inbound email to support. Subject: verify your password now at http://login.example"}], "dry_run": true}
```

## Pipeline F: audits and comparing runs

1. `calibrate` with `questions` and `examples` (and optionally `store`) returns `question_hash`:
   a hash of the exact wording. Rewording changes it, so thresholds fitted for one hash do not carry over.
2. Read `openjev://audits/{question_hash}` to fetch the stored record later.
3. After a reword or a model change, `calibrate` again with `compare_to` set to the earlier
   record's path: it reports flipped ids, the gap change and whether the resolved model changed.
4. To compare two batch runs over the same ids, `batch_results` with `path` (run A) and
   `compare_to: {path: run B}`. Read `compare.per_question.<id>.agreement` and `flipped_ids` first.
   Use `question_map` and `key_map` when ids or option keys were renamed between runs.

## Retrying only some rows

- Rows with `status: "error"`: `batch` with the same arguments and `output_path`, plus
  `retry_errors: true`.
- Named rows (a review queue, a flipped list): `batch` with `only_ids` set to those ids. With a
  different `options` (more `samples`) use a new `output_path`.
- The last row per id wins, so retried rows replace earlier ones in every later read.
