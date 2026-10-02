---
name: openjev-data-records
description: Use when working with records, fields and datasets: picking the right value among several candidates in a text (which phone number, email, amount, date part, request id), verifying fields an extractor produced, NER span typing, labelling many CSV/JSONL rows (and resuming, exporting or comparing such a run), choosing which rows humans should review, building numeric features from text for a classical model, scoring resumes, leads or documents on a weighted rubric, matching or deduplicating records (companies, people, products), or deciding whether a new memory/note/rule duplicates, updates or adds to stored ones.
---

# Records, extraction, labelling, rubrics, dedupe

Precedence: regex/parsers extract candidates; code counts, sums, compares dates and computes
weighted totals; OpenJev only selects, verifies, labels and scores.

## Extraction by selection (use case 11, 13/13)
Candidate strings as keys, each with how it differs, plus a positive `not_stated`:
```json
{"callback": {"type": "choice", "instructions": "Which candidate is the number the customer asks to be called back on?", "criteria": {"415-555-0142": "the customer's own number where they want the callback", "415-555-0199": "the customer's office line", "1-800-555-0100": "the support company's own line", "not_stated": "the ticket gives no callback number"}}}
```
Accept p >= 0.7 (not `not_stated`); `not_stated` wins -> the field is missing, do not guess. Verify
extracted fields with `verify_fields` ("Does the text contain this exact string as the order ID?"):
accept >= 0.85, reject <= 0.15. Check in code that a chosen span is a substring of the text.

## Bulk labelling (use case 21, 19/19)
`batch` over rows with a topic choice (+ `other` and `empty`) and feature nouls/scores. Policy:
auto-accept `p_top` >= 0.9; human queue < 0.8 or margin < 0.4, ascending (`batch_results`
`view: review`); audit a seeded random 2-5% of auto-accepted rows (`audit.rate`); rewrite the
question if audit agreement < 95%. Validate the config before row 0 (a mistyped type is a 400; a
list taxonomy is a 422; `batch` lints once before the first read). Features: score expectation,
noul p, entropy, margin, spread (client-side, from the full JSONL rows). The mechanics are in the
next section.

## Batch jobs: the cursor loop, the review queue, formats (phase 2)
`batch` sends one request per row (the server takes one state per request) and keeps the job in a
JSONL file you name. Every number you need is in the result; nothing else is remembered.

1. **Dry run first.** No network, no cost:
```json
{"items_file": {"path": "data/tickets.csv", "id_field": "ticket_id", "state_template": "Subject: {subject}\n\n{body}"},
 "questions": {"dept": {"type": "choice", "instructions": "Which team should own this ticket?", "criteria": {"billing": "charges, invoices, refunds, payment methods, plan pricing", "technical": "bugs, errors, outages, integrations, performance, login problems", "sales": "pre-purchase questions, quotes, upgrades, demos, enterprise pricing", "other": "anything else, or too vague to tell"}}},
 "dry_run": true}
```
   Read `import` (format, delimiter, `state_field` guess and warning W602, `row_count`, W601 when
   rows were dropped), `preview` (3 states) and `estimate` (requests, tokens, idle and shared time).
   Fix the mapping (`state_field`, `state_template`, `id_field`, `format`, `encoding`) until the
   preview is what you would send by hand. Show the user the estimate before a large run.
2. **Run with an `output_path`.** Same arguments minus `dry_run`, plus `output_path` (a new
   `.jsonl` inside the allowed roots), `max_items_per_call` and, on vLLM only, `concurrency` 2-4.
   Without `output_path` nothing persists and a lost call is re-read in full.
3. **Follow `next_cursor`.** Call again with identical arguments and `cursor` set to the previous
   `next_cursor` until it is `null`. `concurrency`, `max_items_per_call`, `time_budget_s`,
   `detail`, `max_inline_results` and `export` may change between calls; anything else is refused
   as "arguments changed since this cursor". Read `status.stopped_reason`: `max_items_per_call`
   and `time_budget` mean call again; `backpressure` means wait, then call again (no row is lost);
   `error_abort` appears only with `on_error: "abort"`; `complete` ends the job.
4. **After an interruption** (cancel, closed session, crash, no result): call again with the same
   arguments, the same `output_path` and `resume: true`, and no cursor. Finished rows are skipped
   (`status.skipped`). A different question set, source or options is refused: use a new path.
5. **Retry only what failed.** `retry_errors: true` re-runs rows whose last status is `error`;
   `only_ids: [...]` re-runs named rows (with different `options`, use a new `output_path`).
   The last row per id wins in every later read.
6. **Triage without reading again.** `batch_results` with `view: "review"` lists the queue,
   least confident first, with the reason per row; `view: "stats"` gives `per_question`
   statistics; `filter` and `sort_by: "confidence"` reproduce the Playground table. Use the
   `review_batch` prompt when the human should walk the queue.
7. **Export for people** with `batch_results` `export` (`csv`, `markdown`, `ojui-batch` to reopen
   in the Playground, or a filtered `jsonl`), or pass `export: [{"format": "csv", "path": ...}]`
   to the call that finishes the job. Exports are created new; an existing path is refused.
8. **Compare two runs** (a reworded question, another model) with `batch_results`
   `compare_to: {"path": ..., "key_map": {...}}`; read `agreement` and `flipped_ids` first, then
   the divergence (`jsd_*`). Null divergence with a `jsd_reason` is an answer, not an error.
9. **Privacy.** Rows store the state text so exports work; pass `include_state: false` to keep
   only hashes when the data is sensitive.

Input formats `batch` reads (auto-detected from the extension, then the content): CSV and TSV
(header row, delimiter sniffed), JSONL (objects use `state_field`, `*` = the whole object), JSON
(an array, or an object holding one under `array_key`), plain lines (one state per line), blank-line
blocks (one state per block), and a Playground `ojui-batch` export (restores its questions and
options). Spreadsheets (`.xlsx`) and binaries are refused: export the sheet as CSV first.
Several files merge with `items_file.also`. `template` names a built-in question set
(`openjev://templates/{id}`) and supplies its sample states when no source is given.

Do not use `batch` for: items that belong to one task context (use `filter`, one request per
pack); a decision on one state (use `ask`); a threshold choice (use `calibrate`, phase 3, which
can score a finished batch output against a labels file with `from_batch` and no new reads).
A row packing several rows into one state (the `rows_per_request` input of use case 21) trades
per-row resume, retry and statistics for fewer requests; the default is one row per request.

## Weighted rubric (use case 18, 18/18)
One score per dimension, levels naming observable evidence, plus a noul evidence gate; weights in
code only (the server ignores `weights`):
```json
{"recipe": "rubric_score", "inputs": {"text": "RESUME\n<text>", "dimensions": [
 {"name": "python_depth", "weight": 0.7, "levels": ["no evidence of Python", "basic scripting or coursework only", "solid application development in Python with some libraries", "deep expertise: internals, performance profiling, C extensions, or core-library/open-source contributions"]},
 {"name": "leadership", "weight": 0.3, "levels": ["no evidence of leading anyone", "informal mentoring or leading small tasks", "led a team or a project end to end", "managed multiple teams or set org-level technical direction"]}]}}
```
composite = sum(w * score / (levels - 1)); shortlist only if every must-have clears its floor;
re-weighting is arithmetic, no new read. Hiring decisions keep a human in the loop.

## Entity match and memory (use case 19, 16/16)
Pair: 3-level score different/related/same with a definition per level + one noul per field
stating what to ignore; auto-merge only if score >= 1.7 AND every required field >= 0.85; reject
<= 0.5; between = link as related / human. Memory: `memory_decide` with the top-k neighbours
(k <= 10), `action` add/duplicate/supersede + `target` keyed by neighbour id with a topic gloss and
`none`; zero neighbours -> add without a call; act at confidence >= 0.8, else add and flag.

## Worked example
Stored M3 "The primary database is MySQL 8."; new fact "Last week we migrated the primary database from
MySQL to PostgreSQL 16." `memory_decide` -> supersede (1.0), target M3 (1.0) -> overwrite M3.
