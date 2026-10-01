# Usage 21: Dataset labeling, uncertainty sampling, features for ML

Test file: `tests/cases/21-bulk-labeling-active-learning.json` (19 cases). Final result against the live server: **19/19 passed**. No case was a KNOWN LIMITATION; the one weak spot (spread is not returned) is a design point, see Limitations.

## Purpose
Label thousands of rows with typed decisions (topic via `choice`, contains_PII via `noul`), then use the model's own distribution to decide where humans should look: the least-sure rows plus a random audit sample. The same reads double as numeric feature columns for a classical model (score expectation, noul probability, class probabilities). Every value comes from the token distribution, so the confidence you route on is real, not a parsed "I am 80% sure".

## When a coding agent should reach for OpenJev instead of reasoning in prose
Triggers: "Label this CSV", "tag these rows", "why is our classifier unsure", "pick the 25 rows to hand-label", "build features for the regressor".
- More than a handful of rows need a label: do not eyeball them in chat; loop rows through OpenJev and write the label plus its probabilities to a column.
- You need a *ranking of uncertainty*: prose cannot say which of 10k rows is least certain. `confidence` and `probabilities` can.
- You need stable numeric columns (sentiment expectation, P(PII)) for sklearn/xgboost.
- Do not use it to extract or rewrite text; it only returns probabilities over options you supply.

## Recommended question schema (copy-pasteable)
One request per row (or a few rows per request, see batching), same three questions:
```json
{"model":"openjev-latest",
 "state":"Row 1: Hi, I was charged $49 twice for the annual plan. Please refund one charge. My email is jane.doe@gmail.com.",
 "questions":{
  "topic":{"type":"choice",
           "instructions":"What is the main topic of row 1?",
           "criteria":{"billing":"payments, invoices, refunds, pricing, charges",
                       "bug":"a software defect, crash or error",
                       "feature":"a request for new functionality",
                       "account":"login, password, profile or access to the account",
                       "other":"none of the above"}},
  "pii":{"type":"noul",
         "instructions":"Does row 1 contain personal data that identifies a real person, such as a name, email address, phone number or home address? Internal ids, order numbers and SKUs do not count.",
         "criteria":{"true":"a person's name, email, phone number or address appears in the text",
                     "false":"no such personal data; only ids, codes, or generic text"}}}}
```
Numeric feature column (score with 0-indexed described levels; take `score` as the expected value, which is a continuous feature):
```json
"stars":{"type":"score",
         "instructions":"How satisfied is the author of this product review, from 0 (furious) to 4 (delighted)?",
         "criteria":["furious, wants a refund","disappointed","mixed or lukewarm","satisfied","delighted, would recommend"]}
```
Batching several rows: put `R1: ...`, `R2: ...` in `state` and ask `R1_topic`, `R2_topic`, ... with the row id in each instruction.

Client-side derived columns (the server returns `probabilities` and `confidence`, not entropy or spread):
- `entropy = -sum(p*log(p))` over `probabilities` (choice or score).
- `spread = sqrt(sum(p_k*(k-score)^2))` for a score question.
- `margin = p_top1 - p_top2`.
- noul uncertainty = `1 - abs(2*noul - 1)`, or `abs(noul - 0.5)` ascending.

## Thresholds for acting on answers (observed values in parentheses)
| Decision | Rule |
|---|---|
| Auto-accept label | choice `confidence >= 0.9` (clear rows measured 0.994 to 0.998) |
| Send to human queue | `confidence < 0.8` or margin < 0.4. Measured: "callback requested by Marko Horvat ... delayed shipment" topic confidence 0.58 (billing 0.31, other 0.68); "app froze during checkout and now two pending charges" 0.70 (billing 0.82, bug 0.18) |
| Genuinely vague row | e.g. "any news?" gave other 1.0 (confidence 0.994): a confident `other` is not uncertainty, so give `other` its own bucket and audit it by rule |
| noul auto-accept | `>= 0.85` true or `<= 0.15` false (measured true 0.999 to 1.0, false 0.0) |
| noul uncertain | 0.15 to 0.85: queue for a human |
| Audit sample | random 2 to 5 percent of the auto-accepted rows regardless of confidence; stop and rewrite the question if audit agreement drops below 95 percent |
| Score feature | use raw `score` (measured glowing 4.0, lukewarm 2.0, hostile 0.006) |
Outputs are near-binary on clear rows, so a queue of "lowest confidence 25" is short-tailed: rank by ascending `confidence`, not by a fixed cutoff.

## Phrasing rules learned (with before/after)
1. **State what does not count in the noul instruction.** First-draft design applied from the start (all cases passed on the first run, so there is no measured failure): naive `"Does this row contain PII?"` risks flagging identifiers. Final: `"...such as a name, email address, phone number or home address? Internal ids, order numbers and SKUs do not count."` Result: user_id/order/sku/coupon row gave 0.0, name+phone row 1.0, email row 0.999. The untested naive wording is a hypothesis, not a measured before.
2. **Give the true/false criteria concrete examples**, as above; a company mailbox (`support@acme-corp.com`) scored 0.059 with `samples:4`.
3. **Always include an `other` option in topic choices.** The vague row went to `other` with 1.0 instead of being forced into billing/bug. Without `other` the model must pick a wrong label and may do so confidently.
4. **Split multi-attribute labels into separate questions** (topic choice, PII noul, refund-intent noul, sentiment score) in one request; the four-column read on one review gave 0.006 / 1.0 / 1.0.
5. **Batching does not change answers.** Four rows in one request: account, feature, billing, bug all at confidence >= 0.994; row R3 alone gave billing at 0.996 (identical label, 0.001 confidence delta). Batch 4 to 5 rows per request, id in each instruction.
6. **Describe options by what the row talks about**, including tricky variants ("GDPR, data deletion or export requests" won 1.0 among 8 options).
7. **Give degenerate rows their own bucket.** An empty cell with only billing/bug options was an unstable coin flip (P(bug) 0.19 to 0.55 depending on wording); adding `"empty": "the row has no readable content"` gave empty 0.96 (confidence 0.82). Before: two content labels only; after: explicit `empty` (or `other`) option, so blank rows never receive a confident content label.
8. **Use `score` with described levels for features; noul for yes/no columns.** Do not ask a noul "is it positive?" when you want a numeric feature.

## Extensions and edges
- `samples:4` on a borderline PII row returned 0.059 (decidable); use it only for the grey zone before sending to a human.
- `sequential:true` + `think:64` on a two-question read still gave bug 0.997 and PII 1.0; not needed for routine labeling (much slower: about 17 to 20 s on the shared server versus 2 to 12 s).
- Latency observed 2 to 15 s per request on a shared, loaded server (about 0.4 s idle). For 10k rows run sequential workers sized to the server and checkpoint after each batch.

## MCP-layer requirements exercised
- A misspelled question type (`category` instead of `choice`) is a 400 (`err-labeler-typo-type`): the tool must validate type names in the labeling config before looping over rows, so a 10k-row job fails at row 0.
- A taxonomy given as a plain list of labels (as from a CSV header) is a 422 (`err-taxonomy-as-list`): the tool must convert `[labels]` to a `{label: description}` map. An empty `criteria` dict or empty score list is also rejected (400/422), so skip the call when a filter leaves no labels.
- Unknown model names are covered generically in the transport-level usage notes, not here.
- The tool should compute entropy, margin, spread and the human-queue flag itself so the agent gets `{label, confidence, entropy, needs_review}` per row, plus a deterministic seeded audit sample, and write results to JSONL/CSV.
- Sampling for the audit must be seeded and logged so the run is reproducible.

## Limitations
- **Spread is not returned.** The server gives score expectation and the full `probabilities`, but not the standard deviation; compute it client-side. A test asserting an upper bound on confidence or spread is not expressible with the current case runner (no `confidence_lte`), so "entropy ordering" is verified through the measured confidences above (0.58 < 0.70 < 0.99) rather than asserted.
- Probabilities are heavily saturated (mostly 0.00 or 1.00); use the low tail (confidence < 0.8) for uncertainty sampling, not fine-grained ranks. Use `score` when you need graded feature values.
- Model confidence is not agreement with your human labelers; measure audit agreement on your own data before trusting the auto-accept band.
- The topic on `pii-phone-in-log` was uncertain (0.58): rows that mix several intents need a "multi-topic" option or a per-intent noul.
- Batch invariance was tested with 4 rows only.

## Test inventory (19)
row-billing-pii; row-bug-nopii; row-feature; pii-phone-in-log; pii-ids-only-negative (hard negative); batch-4-rows; batch-single-r3 (batch invariance); uncertain-row-decidable (billing 0.82 / bug 0.18); uncertain-vague-row (other); empty-row-own-bucket; feature-sentiment-high / -low / -mixed (score 4.0 / 0.006 / 2.0); multi-feature-vector (score + 2 noul); many-options-topic (8 options); ext-samples-borderline; ext-sequential-think; err-labeler-typo-type; err-taxonomy-as-list.

**Final pass count: 19/19.**
