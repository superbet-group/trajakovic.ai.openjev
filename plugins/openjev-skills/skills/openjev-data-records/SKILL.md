---
name: openjev-data-records
description: "Labels, extracts and scores records with OpenJev: picks the right value among candidates in a text (phone number, email, amount, order id), verifies fields an extractor produced, labels many CSV or JSONL rows with a resumable batch job, builds the human review queue, exports or compares runs, scores resumes, leads or documents on a weighted rubric, and matches or deduplicates records and memory notes. Batch runs lint the questions, dry_run first, read the file through items_file with an ABSOLUTE path plus id_field and state_field, write to an absolute output_path and follow next_cursor. Use when working with records, fields or whole datasets: bulk labelling and resuming a run, choosing which rows people should review, building features from text for a classical model, entity matching, or deciding whether a new note duplicates, updates or adds to stored ones."
---

# Records, extraction, labelling, rubrics, dedupe

Precedence: regex/parsers extract candidates; code counts, sums, compares dates and computes
weighted totals; OpenJev only selects, verifies, labels and scores.

Tools live on the `openjev` MCP server as `mcp__openjev__<tool>` (with the openjev-mcp plugin:
`mcp__plugin_openjev-mcp_openjev__<tool>`). If they are missing, load skill `openjev-data-prep`
(Connect) and do not start servers yourself. Confirm `mcp__openjev__status` returns `healthy: true` before the first call. Paths must be absolute and inside the MCP server's
allowed roots (its working directory plus `OPENJEV_MCP_ROOTS`); if refused, see openjev-data-prep
Connect. File formats, id/state mapping and templates: openjev-data-prep.

## Extraction by selection
Candidate strings as keys, each with how it differs, plus a positive `not_stated`:
<!-- openjev-questions -->
```json
{"callback": {"type": "choice", "instructions": "Which candidate is the number the customer asks to be called back on?", "criteria": {"415-555-0142": "the customer's own number where they want the callback", "415-555-0199": "the customer's office line", "1-800-555-0100": "the support company's own line", "not_stated": "the ticket gives no callback number"}}}
```
Accept p >= 0.7 (not `not_stated`); `not_stated` wins -> the field is missing, do not guess. Verify
extracted fields with the `verify_fields` recipe ("Does the text contain this exact string as the
order ID?"): accept >= 0.85, reject <= 0.15. Check in code that a chosen span is a substring of the text.
Read `openjev://recipes/verify_fields` for its inputs.

## Bulk labelling
`batch` over rows with a topic choice (+ `other` and `empty`) and feature nouls/scores. Policy:
auto-accept `p_top` >= 0.9; human queue < 0.8 or margin < 0.4, ascending (`batch_results`
`view: "review"`); audit a seeded random 2-5% of auto-accepted rows (`audit.rate`); rewrite the
question if audit agreement < 95%. A mistyped question type is a 400 and a list taxonomy a 422, so
`batch` lints once before the first read. Features: score expectation, noul p, entropy, margin,
spread (client-side, from the full JSONL rows).

## Batch jobs: the cursor loop, the review queue, formats
`mcp__openjev__batch` sends one request per row (the server takes one state per request) and keeps
the job in a JSONL file you name. Every number you need is in the result; nothing else is remembered.

```
Batch checklist (copy and tick)
- [ ] Dry run first: items_file mapping right, preview looks like a hand-written state
- [ ] Run with a new absolute output_path (.jsonl, inside the allowed roots)
- [ ] Follow next_cursor until null (identical arguments)
- [ ] Read status: errors 0, or retry_errors: true
- [ ] batch_results view "review" / "stats"; export; audit
```

1. **Dry run first.** No network, no cost. Show the user the estimate before a large run.
<!-- openjev-call: batch -->
```json
{"items_file": {"path": "/abs/path/to/tickets.csv", "id_field": "ticket_id", "state_template": "Subject: {subject}\n\n{body}"},
 "questions": {"dept": {"type": "choice", "instructions": "Which team should own this ticket?", "criteria": {"billing": "money questions: invoices, refunds, charges, payment methods", "technical": "product defects: errors, outages, slow pages, integrations, sign-in trouble", "sales": "buying interest: quotes, demos, upgrades, enterprise plans", "other": "anything else, or too vague to tell"}}},
 "dry_run": true}
```
   Read `import` (format, delimiter, `state_field` guess and warning W602, `row_count`, W601 when
   rows were dropped), `preview` (3 states) and `estimate` (requests, tokens, idle and shared time).
   Fix the mapping (`state_field`, `state_template`, `id_field`, `format`, `encoding`) and dry-run
   again until the preview is what you would send by hand.
2. **Run with an `output_path`.** Same arguments minus `dry_run`, plus `output_path` (a new
   `.jsonl`), `max_items_per_call` (default 25) and, on vLLM only, `concurrency` 2-4.
   Without `output_path` nothing persists and a lost call is re-read in full.
3. **Follow `next_cursor`.** Call again with identical arguments and `cursor` set to the previous
   `next_cursor` until it is `null`. `concurrency`, `max_items_per_call`, `time_budget_s`,
   `detail`, `max_inline_results` and `export` may change between calls; anything else is refused
   as "arguments changed since this cursor". Read `status.stopped_reason`: `max_items_per_call`
   and `time_budget` mean call again; `backpressure` means wait, then call again (no row is lost);
   `error_abort` appears only with `on_error: "abort"`; `complete` ends the job.
4. **After an interruption** (cancel, closed session, crash, no result): call again with the same
   arguments, the same `output_path` and `resume: true` (the default), and no cursor. Finished rows
   are skipped (`status.skipped`). A different question set, source or options is refused: use a
   new path.
5. **Retry only what failed.** `retry_errors: true` re-runs rows whose last status is `error`;
   `only_ids: [...]` re-runs named rows (with different `options`, use a new `output_path`).
   The last row per id wins in every later read.
6. **Triage without reading again.** `mcp__openjev__batch_results` with `view: "review"` lists the
   queue, least confident first, with the reason per row; `view: "stats"` gives `per_question`
   statistics; `filter` and `sort_by: "confidence"` narrow and order the rows. Use the
   `review_batch` prompt when a person should walk the queue.
7. **Export for people** with `batch_results` `export` (`csv`, `markdown`, `ojui-batch` to reopen
   in the OpenJev UI, or a filtered `jsonl`), or pass `export: [{"format": "csv", "path": "/abs/path/to/done.csv"}]`
   to the call that finishes the job. Exports are created new; an existing path is refused.
8. **Compare two runs** (a reworded question, another model) with `batch_results`
   `compare_to: {"path": ..., "key_map": {...}}`; read `agreement` and `flipped_ids` first, then
   the divergence (`jsd_*`). Null divergence with a `jsd_reason` is an answer, not an error.
9. **Privacy.** Rows store the state text so exports work; pass `include_state: false` to keep
   only hashes when the data is sensitive.

Output file shape (header line, row lines, last row per id wins): see
[references/batch-output-format.md](references/batch-output-format.md).

Input formats `batch` reads (auto-detected from the extension, then the content): CSV and TSV
(header row, delimiter sniffed), JSONL (objects use `state_field`, `*` = the whole object), JSON
(an array, or an object holding one under `array_key`), plain lines (one state per line), blank-line
blocks (one state per block), and an `ojui-batch` export (restores its questions and
options). Spreadsheets (`.xlsx`) and binaries are refused: export the sheet as CSV first.
Several files merge with `items_file.also`. `template` names a built-in question set
(`openjev://templates/{id}`) and supplies its sample states when no source is given.

Do not use `batch` for: items that belong to one task context (use `mcp__openjev__filter`, one
request per pack); a decision on one state (use `mcp__openjev__ask`); a threshold choice (use
`mcp__openjev__calibrate`, which can score a finished batch output against a labels file with
`from_batch` and no new reads). Packing several rows into one state trades per-row resume, retry
and statistics for fewer requests; the default is one row per request.

## Weighted rubric
One score per dimension, levels naming observable evidence, plus a noul evidence gate; weights in
code only (the server ignores `weights`):
<!-- openjev-call: recipe -->
```json
{"recipe": "rubric_score", "inputs": {"text": "<resume text>", "label": "RESUME", "dimensions": [
 {"name": "python_depth", "instructions": "How deep is the candidate's Python experience?", "weight": 0.7, "levels": ["no evidence of Python", "basic scripting or coursework only", "solid application development in Python with some libraries", "deep expertise: internals, performance profiling, C extensions, or core-library/open-source contributions"]},
 {"name": "leadership", "instructions": "How much has the candidate led other people?", "weight": 0.3, "levels": ["no evidence of leading anyone", "informal mentoring or leading small tasks", "led a team or a project end to end", "managed multiple teams or set org-level technical direction"]}]}}
```
composite = sum(w * score / (levels - 1)); shortlist only if every must-have clears its floor;
re-weighting is arithmetic, no new read. Hiring decisions keep a human in the loop.

## Entity match and memory
Pair: 3-level score different/related/same with a definition per level + one noul per field
stating what to ignore (recipe `entity_match`); auto-merge only if score >= 1.7 AND every required
field >= 0.85; reject <= 0.5; between = link as related / human. Memory: the `memory_decide`
recipe with the top-k neighbours (k <= 10), `action` add/duplicate/supersede + `target` keyed by
neighbour id with a topic gloss and `none`; zero neighbours -> add without a call; act at
confidence >= 0.8, else add and flag.

Worked example: stored M3 "The primary database is MySQL 8."; new fact "Last week we migrated the
primary database from MySQL to PostgreSQL 16." `memory_decide` -> supersede (1.0), target M3 (1.0)
-> overwrite M3.
