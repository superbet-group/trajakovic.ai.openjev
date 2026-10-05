# Items files for batch

## Contents
- When to use a file
- Formats
- Choosing the id and the state
- state_template
- Encoding and delimiter
- Merging files
- Inline items
- Limits
- The output JSONL
- Exports
- Dry-run checklist

## When to use a file

`mcp__openjev__batch` runs one question set over many states, one read per row, and keeps the job
in a resumable JSONL file. Use it for 5 or more records that share the same questions. For items
that belong to one task context use `filter` (several items per read); for one state use `ask`.

All paths must be absolute and inside the MCP server's allowed roots (its working directory plus
`OPENJEV_MCP_ROOTS`); if refused, see the Connect section of skill `openjev-data-prep`.
Spreadsheets (`.xlsx`) and binaries are refused (E030): export the sheet as CSV first.

## Formats

The format comes from the extension, then from the content. Pass `format` to force it.

| Format | Extension | Each state is | Notes |
|---|---|---|---|
| `csv` / `tsv` | `.csv`, `.tsv` | one row | header row required; delimiter sniffed (`,` tab `;`) or `delimiter`; quoted cells may span lines |
| `jsonl` | `.jsonl`, `.ndjson` | one JSON value per line | objects use `state_field` or `state_template`; strings are states as they are |
| `json` | `.json` | one array element | array, or an object holding the array under `states`, `batchStates`, `items`, `data`, `rows`, `records` or `examples`; else pass `array_key` |
| `lines` | `.txt`, `.log`, `.md` | one non-empty line | for log lines, URLs, sentences |
| `blocks` | `.txt`, `.md` | one blank-line-separated block | for multi-line records such as stack traces |
| `ojui-batch` | `.json` | a Playground batch export | restores its questions and options, so `questions` may be omitted |

<!-- openjev-items: csv -->
```csv
ticket_id,subject,body,priority
T-101,Charged twice for March,"I was billed 49 EUR twice on 3 March. Please refund one charge.",high
T-102,Login loop on mobile,"After the update the app returns me to the login screen every time I sign in.",normal
```

<!-- openjev-items: jsonl -->
```jsonl
{"id": "r1", "text": "Order arrived broken"}
{"id": "r2", "text": "Love it, will buy again"}
```

<!-- openjev-items: json -->
```json
{"items": [{"id": "r1", "text": "Order arrived broken"}, {"id": "r2", "text": "Love it, will buy again"}]}
```

<!-- openjev-items: lines -->
```text
2026-03-01T10:02:11Z WARN cache: evicted 1204 entries
2026-03-01T10:02:12Z ERROR worker: job sync_inventory aborted
```

<!-- openjev-items: blocks -->
```text
Traceback (most recent call last):
  File "orders/service.py", line 88, in create_order
UniqueViolation: duplicate key value

Traceback (most recent call last):
  File "billing/charge.py", line 31, in charge
TimeoutError: processor did not answer
```

## Choosing the id and the state

| Setting | What happens when omitted |
|---|---|
| `id_field` | **no detection**: ids are the 1-based row numbers `"1"`, `"2"`, ... A column named `ticket_id` or `id` is NOT used. Pass `id_field` whenever you will join results back to your data. |
| `state_field` | CSV and TSV: the first column named `text`, `state`, `content`, `input`, `prompt`, `message`, `body`, `review`, `comment`, `question`, `sentence` or `description` (any case), else the column with the longest average cell. JSON objects: the first such key, else the whole object. Warning W602 when the file has more than one column. |

`state_field: "*"` means the whole object (JSONL, JSON) or the whole row as an object (CSV) is
the state. Real dry-run output for the CSV above without `state_field` shows both effects: the
guess `body`, warning W602, and ids `"1"`, `"2"`:

<!-- openjev-call: batch -->
```json
{"items_file": {"path": "/abs/path/to/tickets.csv"}, "questions": {"urgent": {"type": "noul", "instructions": "The customer needs a reply today.", "criteria": {"true": "The ticket reports money lost, an outage or a deadline", "false": "The ticket is a routine question with no deadline"}}}, "dry_run": true}
```

<!-- openjev-result: batch -->
```json
{"status": {"done": 0, "total": 2, "remaining": 2, "ok": 0, "errors": 0, "skipped": 0, "stopped_reason": "dry_run"}, "summary": {"scope": "call", "n": 0, "ok": 0, "errors": 0, "needs_review": 0, "audit": 0, "per_question": {"urgent": {"type": "noul", "n": 0, "mean_p": 0, "yes": 0, "no": 0, "grey": 0, "mean_margin": 0}}}, "results": [], "output_path": null, "import": {"format": "csv", "delimiter": ",", "encoding": "utf-8", "state_field": "body", "id_field": null, "columns": ["ticket_id", "subject", "body", "priority"], "row_count": 2, "truncated": false, "warnings": [{"code": "W602", "path": "items_file.state_field", "message": "state_field omitted: guessed 'body'", "fix": "pass state_field to choose"}]}, "preview": [{"id": "1", "state": "I was billed 49 EUR twice on 3 March. Please refund one charge."}, {"id": "2", "state": "After the update the app returns me to the login screen every time I sign in."}], "estimate": {"requests": 2, "billed_reads": 2, "input_tokens_approx": 120, "time_s_idle_approx": 0.6, "time_s_shared_approx": 3.0}, "next_cursor": null, "meta": {"model": "openjev-latest", "requests": 0, "latency_ms": 0}}
```

Fix: pass `id_field: "ticket_id"` and either `state_field: "body"` or a `state_template`.

## state_template

`state_template` builds the state from several columns. It wins over `state_field`.

<!-- openjev-call: batch -->
```json
{"items_file": {"path": "/abs/path/to/tickets.csv", "id_field": "ticket_id", "state_template": "SUPPORT TICKET (priority {priority})\nSubject: {subject}\n\n{body}"}, "questions": {"urgent": {"type": "noul", "instructions": "The customer needs a reply today.", "criteria": {"true": "The ticket reports money lost, an outage or a deadline", "false": "The ticket is a routine question with no deadline"}}}, "dry_run": true}
```

- Placeholders are `{name}` where `name` is exactly a column or key name; `{id}` is the row id.
  Letters, digits, `_ . : - ` and spaces are allowed in a name. There is no nested lookup: a key
  `a.b` is matched literally.
- An unknown placeholder is refused and the message lists the columns.
- A row whose referenced columns are all empty is skipped (counted as an empty row, W605).
- Use real newlines (`\n` in JSON) and a section label, so the state reads like a hand-written one.

## Encoding and delimiter

`encoding: "auto"` (default) detects UTF-8 (with or without BOM), UTF-16 and cp1252; a
non-UTF-8 file produces W603. Force with `utf-8`, `utf-16` or `cp1252`. `delimiter` is `auto`,
`,`, a tab or `;`.

## Merging files

`items_file.also` lists up to 7 more paths merged after `path`. Files with the same kind and the
same header (CSV) merge into one table; otherwise each is resolved on its own, so give every
file the same columns or use `state_template` with columns common to all.

<!-- openjev-call: batch -->
```json
{"items_file": {"path": "/abs/path/to/march.csv", "also": ["/abs/path/to/april.csv"], "id_field": "ticket_id", "state_field": "body"}, "template": "moderation_guardrails_block_category", "dry_run": true}
```

Here `template` supplies only the questions (`openjev://templates/{id}`); the states come from the
files. A template alone, without `items` or `items_file`, runs the template's sample states.

## Inline items

Up to 500 states with no file: `items` is `[{"id": "r1", "state": "..."}]`. `state` may be a string,
object or array; `id` defaults to the 1-based position and must match `^[A-Za-z0-9_.:-]{1,64}$`.
Prefer `items_file` with an absolute path whenever the data lives in a file. Use inline items only for
small ad-hoc data, or after the server refused the path as outside its allowed roots.

<!-- openjev-call: batch -->
```json
{"items": [{"id": "r1", "state": "Order arrived broken"}, {"id": "r2", "state": "Love it, will buy again"}], "questions": {"negative": {"type": "noul", "instructions": "The review is negative.", "criteria": {"true": "The reviewer complains or regrets the purchase", "false": "The reviewer is satisfied or neutral"}}}, "dry_run": true}
```

Give exactly one source: `items`, `items_file`, or a `template` alone.

## Limits

| Limit | Value |
|---|---|
| rows taken from the source | `max_items`, default 5000, up to 100000; rows above it are dropped with W601 |
| rows read per call | `max_items_per_call`, default 25, up to 100; follow `next_cursor` |
| inline `items` | 1 to 500 |
| `also` | 7 files |
| ids | unique within the run; duplicates are refused |
| `concurrency` | 1 to 4, and capped by the server; no speedup on a serial backend |
| `time_budget_s` | default 120; no new row starts after it |
| inline results | `max_inline_results` default 50, up to 100 |

## The output JSONL

`output_path` is a new `.jsonl` file the server writes. Line 1 is the header, every later line is
one row. Without `output_path` nothing persists. The file is the source of truth: resume, retry,
`batch_results` and `calibrate` all read it. If a path is reused with different questions,
source or options, the call is refused; pick a new path.

Header (shown with placeholder paths):

<!-- openjev-example -->
```json
{"openjev_mcp": "batch", "v": 2, "spec": "1.2", "run_id": "sha256:1fcc0d37...", "question_hash": "sha256:a648e133...", "options": {}, "sampling": "fast", "thresholds": {}, "review_rule": {}, "audit": {}, "source": {"kind": "items_file", "path": "/abs/path/to/tickets.jsonl", "format": "jsonl", "delimiter": null, "state_field": null, "id_field": "ticket_id", "row_count": 3}, "created_at": "2026-10-05T08:23:48Z", "questions": {"wants_refund": {"type": "noul", "instructions": "The customer asks for money back.", "criteria": {"true": "The customer explicitly asks for a refund or a reversal of a charge", "false": "The customer only reports a problem or asks a question without requesting money back"}}}}
```

Row:

<!-- openjev-example -->
```json
{"index": 1, "id": "T-101", "status": "ok", "state": "TASK: route one support ticket.\nTICKET\nSubject: Charged twice for March\n\nI was billed 49 EUR twice on 3 March. Please refund one charge.", "state_hash": "sha256:cab93524...", "answers": {"wants_refund": {"type": "noul", "p": 0.9984244654057237, "band": "yes", "margin": 0.9968489308114474}}, "needs_review": false, "review_reasons": [], "audit": false, "model": "openjev-0.1", "usage": {"input_tokens": 228, "output_tokens": 0}, "latency_ms": 230.3, "request_id": "req_...", "body_hash": "sha256:4309ef32...", "retried": null, "error": null, "ts": "2026-10-05T08:23:48Z"}
```

- `status` is `ok` or `error`; an error row has `error` and no `answers`.
- The last row per id wins in every later read, so retries append instead of rewriting.
- `include_state: false` keeps only `state_hash` in rows (use for sensitive data); exports then
  have no state column.
- `needs_review` and `review_reasons` come from `review_rule`; `audit: true` marks the seeded
  random sample (`audit: {rate, seed}`, default rate 0.03).
- To label rows for `calibrate`, join on `id`: a labels file is `.csv` or `.jsonl` with an id
  column and one label column per question.

## Exports

Two ways, both create new files only (an existing path is refused):

- `batch` `export: [{"format": "csv", "path": "/abs/path/to/out.csv"}]`: needs `output_path`, up to
  3 entries, written once by the call that finishes the job (`next_cursor` null).
- `mcp__openjev__batch_results` `export: {"format": "...", "path": "..."}` any time. Formats:
  `csv`, `markdown`, `ojui-batch` (reopen in the Playground), `jsonl` (with `filtered: true` only
  the rows matching `filter`). Without `path` the export comes back inline, up to 64 KiB, with
  `truncated: true` beyond that.

## Dry-run checklist

```
- [ ] Path is absolute and inside the allowed roots
- [ ] dry_run: true returns no error and row_count matches the source
- [ ] id_field set (ids are not auto-detected); state_field or state_template set (no W602)
- [ ] preview[0].state equals the state you rendered by hand
- [ ] estimate.requests and input_tokens_approx are acceptable
- [ ] output_path is a new .jsonl inside the roots, in a directory that exists
```
