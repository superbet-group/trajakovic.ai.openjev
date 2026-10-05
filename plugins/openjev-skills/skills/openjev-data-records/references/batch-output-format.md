# Batch output file format

## Contents
- Header line
- Row lines
- Reading rules
- Example

The file named by `output_path` of `mcp__openjev__batch` is JSONL. It is the resumable source of truth and the input of `batch_results` and of `calibrate` (`from_batch`). The authoritative schemas are `BatchHeader` and `BatchRow` in the MCP resource `openjev://schema`.

## Header line
Line 1, one object. Required: `openjev_mcp` (always `"batch"`), `v` (2), `spec`, `run_id` (sha256 of questions, source, options, sampling), `question_hash` (sha256 of the canonical questions), `created_at`. Optional: `options`, `sampling`, `thresholds`, `review_rule`, `audit`, `source` (`kind` items / items_file / template, `path`, `format`, `delimiter`, `state_field`, `id_field`, `row_count`).

## Row lines
One object per processed item. Required: `index`, `id`, `status` (`ok` or `error`), `state_hash`. Common: `state` (absent when `include_state` is false), `answers` (question id to full answer with probabilities, confidence, entropy and derived fields), `needs_review`, `review_reasons`, `audit`, `model`, `usage`, `latency_ms`, `request_id`, `body_hash`, `error` (`code`, `message`; null when ok), `ts`.

## Reading rules
- The last row per `id` wins. A retry appends a new row; never edit the file by hand.
- Rows are always full, whatever `detail` the call used.
- A different question set or source needs a new `output_path`; `resume: true` refuses a mismatch.
- To read a finished file without new reads, use `mcp__openjev__batch_results` (`view` rows, review or stats), not a JSONL parser of your own; parse by hand only for custom features.

## Example
<!-- openjev-example -->
```json
{"openjev_mcp": "batch", "v": 2, "spec": "2.11", "run_id": "sha256:aaaa", "question_hash": "sha256:bbbb", "created_at": "2026-01-01T00:00:00Z", "source": {"kind": "items_file", "format": "csv", "id_field": "ticket_id", "row_count": 3}}
```
<!-- openjev-example -->
```json
{"index": 0, "id": "T-1001", "status": "ok", "state_hash": "sha256:cccc", "answers": {"dept": {"top": "billing"}}, "needs_review": false, "error": null}
```
