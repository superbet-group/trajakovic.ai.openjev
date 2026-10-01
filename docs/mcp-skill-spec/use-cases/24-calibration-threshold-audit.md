# Usage type 24: Calibration audit, threshold fitting, repeat-and-agree

Final result: 19/19 cases pass against the live server (`tests/cases/24-calibration-threshold-audit.json`), all on calibration, threshold fitting or repeat-and-agree. No `KNOWN LIMITATION` case was needed. After an audit the file was reworked: the sequential multi-question case and the two error-handling cases (unknown model 400, dict score criteria 422) were removed because they belong to other usage types; the near-duplicate fit cases were differentiated by role (auto-act band edges, ladder rungs, ladder plus noul on the same item) rather than by loosened thresholds; the '2 pixels off-center' item was replaced by a representative cosmetic ticket (footer links wrapping on phones). The set now includes one genuinely mid-range item (slow checkout for some mobile users, ladder score about 1.45, confidence about 0.50), so the borderline behaviour is actually exercised. Fixtures other than that one are still saturated, so they validate plumbing and separation, not decision-boundary behaviour.

## Purpose
Replace "I picked 0.7 because it felt right" with measured thresholds. Run the model in shadow mode over past human-labelled items, record P(true) per item, choose a per-question threshold (zero observed errors, or a target precision/coverage), and re-run the same fixtures whenever the model alias moves so CI fails on drift. All values come from the model's distribution, so a threshold table is just arithmetic on floats.

## When a coding agent should reach for OpenJev
Instead of: hard-coding `if score > 0.8`, asserting "accuracy stayed the same" from memory, or eyeballing a handful of outputs after a model bump.
Trigger when the agent is about to:
- choose or change any numeric threshold on an OpenJev answer (auto-close, escalate, block, route);
- see a model swap, new alias, or release note (`openjev-latest` moving);
- be asked "how reliable is this question?" or "which items should go to a human?".
Do not use it to measure the model's absolute accuracy on data you have not labelled; without human labels there is nothing to calibrate against.

## Recommended question schema
Use the production question unchanged in the audit, so the fitted threshold applies to the exact wording. Escalation example (one request per labelled item, since `state` is per request):
```json
{
  "model": "openjev-latest",
  "state": "Ticket #4412: Since the 14:05 deploy every checkout request returns HTTP 500. Payments are failing for all customers. Error rate 100%.",
  "questions": {
    "escalate": {
      "type": "noul",
      "instructions": "Should this support ticket be escalated to the on-call engineer right now?",
      "criteria": {
        "true": "production impact, data loss, security exposure, or all users blocked",
        "false": "cosmetic, how-to, feature request, or a single-user inconvenience with a workaround"
      }
    }
  }
}
```
Severity ladder for monotonicity checks (score is 0-indexed expected level; `criteria` MUST be a list):
```json
{"sev": {"type": "score", "instructions": "Rate the severity of this ticket.",
  "criteria": ["cosmetic or question", "minor, single user, workaround exists",
               "significant, many users degraded", "critical outage, data loss or security breach"]}}
```
Per-class table via choice (read `probabilities`, not just `choice`):
```json
{"cat": {"type": "choice", "instructions": "Which category best fits this ticket?",
  "criteria": {"security": "exposure of data or access control flaw",
               "outage": "service down or errors for many users",
               "billing": "charges, invoices, refunds", "ui": "cosmetic or usability"}}}
```

## The audit loop (what the skill should generate)
1. Collect N labelled items (human decision per item). Keep them in a fixture file with the question JSON version.
2. For each item call `/v1/systemone` sequentially; store `{id, label, p, model}` where `model` is the resolved string from the response (`openjev-latest` returned `"model":"openjev-0.1"`).
3. Sort by `p`. Threshold with zero observed errors exists only if `max(p | label=false) < min(p | label=true)`; set `t` at the midpoint and report the gap. If ranges overlap, report precision/coverage at several `t` and the overlap items for human review.
4. Store the table (`t`, gap, N, model string, question hash) in the repo. Reproducibility test: recompute the table from stored fixtures and compare.
5. Drift CI: rerun fixtures when the resolved model string differs from the stored one, or nightly; fail when any labelled item flips sides of `t`, or the gap shrinks below a set margin (for example 0.3).

## Phrasing rules learned (with before/after)
- No rewrites were needed for the fixtures; every noul used defined true and false poles, which is the form to keep. Weak alternative not measured here: `"Is this urgent?"` with no criteria. Convention, not a measured gain.
- Put both poles of the label in `criteria`, worded the way the human labeller decided ("single-user inconvenience with a workaround" is the false pole). Otherwise the fitted threshold encodes the model's definition, not the labeller's.
- Test monotonicity and stability rather than absolute accuracy: outage scored 2.997 and typo 0.006 on a 0..3 ladder; a swap that reorders the ladder is drift even if accuracy looks the same.
- Keep the question text byte-identical between audit and production; a reworded question needs a new audit.
- Use a second, non-saturated signal to find borderline items: a noul can be near 0 while the score ladder still splits between two rungs (slow-checkout ticket: escalate 0.0024, sev 1.45, confidence 0.50). Rank items for human review by ladder confidence, not by the noul alone.
- Do not use two tests with the same fixture role and threshold; give each fit case a distinct job (band edge t_lo/t_hi, ladder rung, per-class table) so a failure points at one thing.
- Choose cosmetic negatives a human would actually file (layout wrap on phones), not pixel-level trivia.

## Measured behaviour
| Item (label) | P(escalate) |
|---|---|
| checkout outage (yes) | 0.99978 |
| IDOR invoice exposure (yes) | 0.99977 |
| migration data loss, 2,000 users (yes) | 0.99963 |
| back-button shows card digits, single customer (yes, hard) | 0.99999 with `think: 256` |
| pricing typo (no) | 0.00003 |
| how-to language setting (no) | 0.00002 |
| dark mode request (no) | 0.00013 |
| single-user avatar glitch (no) | 0.00003 |
| footer links wrap on phones (no) | 0.00004 |
| slow checkout for some mobile users, retry works (no, borderline) | 0.0024, but sev ladder score 1.45 (rung 1 at 0.545, rung 2 at 0.455) |

Zero observed errors at any noul threshold between 0.01 and 0.99; the gap is about 0.9996. That says the fixtures are easy, not that 0.5 is a good production threshold.

## Repeat-and-agree: what the server actually does
- Repeating the identical request with a different `uid` returned bit-identical values (typo item: 3.447e-05 in both cases; outage item: 0.9997759865 in two separate cases). Fresh `uid` alone does NOT add variation, so 15 plain repeats measure nothing.
- `samples: 8` with a fresh `uid` on a clear item stayed saturated (0.99980). Spread is only visible on mid-range items; none of my fixtures were mid-range, so I have not observed the spread signal.
- Practical rule: reliability = distance from 0.5 plus agreement across paraphrased states or `samples`>1 on borderline items. Do not claim "15/15 agree" from identical replays.
- Model string logged: response `model` shows the concrete version behind the alias (`openjev-0.1`). Store it with every audit row. Pin `model: "openjev-0.1"` for the canary and compare against `openjev-latest` on a release to catch movement.

## Thresholds for acting
- Act automatically only when p >= `t_hi` or p <= `t_lo`, both fitted from labelled data with a gap; route the band between to a human. With this fixture set a provisional band is 0.05 / 0.95; refit on your own data before use.
- Drift alarm: any labelled canary crossing 0.5, or a monotonic ladder reordering, or the resolved model string changing (informational; run the audit).
- Minimum audit size: treat 12 easy items as a smoke test only. For a claim of "zero errors" report N and the confidence bound (0 errors in 500 supports roughly a 0.6 percent upper bound at 95 percent, by the rule of three).
- Never tune the threshold on the same items you use as the drift canary; hold out a set.

## Limitations
- Fixtures here are decidable and near-saturated (values near 0 or 1); real tickets will produce mid-range probabilities and the audit is most valuable there. Not measured.
- Determinism means run-to-run spread is not a reliability signal here; borderline-item spread under `samples`/`think` was not exercised.
- Only one alias mapping (`openjev-latest` to `openjev-0.1`) exists, so an actual model swap was not tested; drift detection is validated only as "same model gives same numbers".
- Latency on the shared server was 6-18 s per read while other agents were active (0.4 s idle), so a 500-item audit should run in the background.
- `think` was used on one item; its cost and effect on calibration were not compared.

## MCP layer notes
- Tool `audit_thresholds(fixtures, question, model)`: sequential calls, returns per-item p, resolved model, best `t`, gap, and overlapping items.
- Validate score criteria as a list before sending and surface unknown-model errors verbatim (covered by the error-handling usage types, not tested here).
- Persist `model` from the response, not the requested alias.
