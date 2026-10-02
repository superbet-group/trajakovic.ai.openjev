---
name: openjev-calibration
description: Use when choosing or changing a numeric threshold on an OpenJev answer (auto-close, escalate, block, route, merge), when asked how reliable an OpenJev question is, when building a labelled eval set or CI regression test for a question set or recipe, after rewording any OpenJev question, or when openjev-latest now resolves to a different model version (drift check). Replaces "0.7 felt right" with thresholds fitted on labelled examples.
---

# Calibrate and audit OpenJev questions

Precedence: measured thresholds over defaults; defaults over intuition.

## Procedure
1. Collect labelled items: the human's past decisions, >= 12 for a smoke test, >= 100 before
   claiming a band, including hard negatives and genuinely borderline items. Hold out a canary set
   that is never used for fitting.
2. Use the production question **byte-identical** (the threshold belongs to its `question_hash`).
3. `calibrate` with `questions` + `examples` (or a `run_cases.py` `case_file`).
   If the items were already read by `batch`, use `from_batch` with a labels file instead: no
   reads, same report. To compare two wordings before fitting anything, `batch_results`
   `compare_to` (phase 2) is enough.
4. Read: `separable`, `gap`, `t_fit`, `suggested_band`, `overlap_ids`, `most_borderline`,
   `zero_error_upper_bound_95` (0 errors in n bounds the error rate at ~3/n).
5. Not separable: report precision/coverage at several thresholds; send the overlap to a human;
   rewrite the question (skill `openjev-question-authoring`) and re-run.
6. Store the record (`store`), commit it with the schema; add a CI job that re-runs the fixtures
   when the resolved model string changes or nightly, failing on any labelled item that flips
   sides or a gap below 0.3 (`compare_to`).
7. Scores: check the ladder is monotonic; use ladder confidence to find borderline items that a
   saturated noul hides.
8. Read `calibration` (reliability bins, Brier, ECE) and `distributions`. An ECE above 0.1, or a
   reliability bin whose `acc` is far below its `conf`, means the raw probability is not a usable
   confidence for this question: gate on fitted thresholds only.

## Facts to respect
- Identical requests return identical numbers; 15 replays prove nothing. Use paraphrased states or
  `samples` > 1 on borderline items for repeat-and-agree. Exception: `think` reads vary between
  runs; read them at least twice.
- Log the response `model` (`openjev-0.1`), not the alias.
- Near-saturated fixtures (0.00/1.00) validate plumbing, not the decision boundary.

## Worked example
Escalation question on 7 labelled tickets (3 escalate, 4 not): positives >= 0.9994, negatives
<= 0.0020, gap 0.9974, separable, provisional band 0.05/0.95. The borderline slow-checkout ticket
reads 0.0020 on the noul but splits its severity ladder 0.545/0.455: route that class to a human.
n = 7 is a smoke test (error bound ~43%); collect 100+ before trusting it.
