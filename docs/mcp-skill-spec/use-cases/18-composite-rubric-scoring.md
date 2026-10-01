# Usage type 18: Multi-dimension rubric scoring with client-side weights (candidates, leads, documents)

Final result: 18/18 cases pass against the live server (`tests/cases/18-composite-rubric-scoring.json`: 16 live cases plus 2 error cases). Coverage by domain: candidates 5 (01-05), leads 4 (06-09), documents 4 (10-13), cross-domain guard 1 (14), weights-field limitation 1 (15), multi-dimension extension 1 (16), errors 2 (17-18). The rewrite passed on the first run with no thresholds loosened. Caveat: outcomes are mostly near the extremes (0, 1, 3), so this suite proves plumbing and phrasing, not fine-grained calibration.

**What "weighted" means here.** OpenJev has no weight parameter. A top-level `weights` object and a per-question `weight` key are accepted (HTTP 200) and silently ignored (case 15, the single KNOWN LIMITATION case); the response carries no composite. Weighting is arithmetic the client does on the returned per-dimension scores. Cases 04 and 05 are the weighting demonstration: two different candidates read on the same two dimensions. Observed A (Python specialist, no leadership) python 3.00, leadership 0.00; B (manager, weak Python) python 0.89, leadership 2.19. Normalised by 3, with weights python .7 / leadership .3 the composites are A 0.70 vs B 0.43 (A wins); with .3 / .7 they are A 0.30 vs B 0.60 (B wins). The ranking flips from weights alone, with no second inference. The runner cannot assert a composite, so the tests bound the per-dimension scores that make the flip provable.

**Which question types apply.** Weighted sums apply to `score` dimensions (candidates, documents, and lead urgency/fit in case 09). Lead qualification with `noul` gates and a `choice` route uses thresholds and boolean logic instead of sums (cases 06-08): "weights" there are gate cut-offs, not multipliers. If you want a weighted lead score, ask `score` questions (case 09).

## Purpose
Break a fuzzy judgment ("is this a good candidate / qualified lead / good draft?") into independent dimensions, each a typed `score` (expected level read from the distribution) or `noul` evidence gate, then combine them in code with weights. Re-weighting is arithmetic on stored numbers and needs zero new inference. One request can carry 3 to 6 dimensions (a 6-dimension read with `samples: 3, think: 256` took about 14 s on the shared server; a 3-dimension read 3-6 s).

## When a coding agent should reach for OpenJev
Trigger: the agent is asked to rate, rank, qualify, grade or shortlist free text (resumes, inbound leads, PR descriptions, design docs, essays, support replies), or to rescore a document on each edit. Instead of writing a paragraph of prose judgment, send the text once with the rubric and get numbers it can sort, weight, threshold and diff between runs. Do not use it when the input lacks the evidence (a score question will still return a number) or for exact checks that regex/schema can do.

## Recommended question schema
Score levels are a LIST, ordered worst to best; the returned `score` is 0-indexed (4 levels give 0..3). Each level must describe observable evidence.
```json
{
  "model": "openjev-latest",
  "state": "RESUME\n<full text>",
  "questions": {
    "python_depth": {"type": "score",
      "instructions": "Rate the depth of Python expertise demonstrated by concrete evidence in this resume.",
      "criteria": ["no evidence of Python",
                   "basic scripting or coursework only",
                   "solid application development in Python with some libraries",
                   "deep expertise: internals, performance profiling, C extensions, or core-library/open-source contributions"]},
    "leadership": {"type": "score",
      "instructions": "Rate the leadership scope demonstrated by concrete evidence in this resume.",
      "criteria": ["no evidence of leading anyone", "informal mentoring or leading small tasks",
                   "led a team or a project end to end", "managed multiple teams or set org-level technical direction"]},
    "has_evidence": {"type": "noul",
      "instructions": "Does the text contain any evidence about a person's work experience or leadership?",
      "criteria": {"true": "the text describes a person's work history or leadership",
                   "false": "the text has no information about a person's work history"}}
  }
}
```
Lead qualification (gates, not sums): two `noul` gates (`has_budget`, `has_authority`) plus a `choice` route with criteria `{sales, nurture, support, discard}`, each described as a sentence about the lead. Document rescoring: one score per dimension (clarity, alternatives, testing_plan) with levels such as `["absent or TBD","one vague sentence","reasonable outline","detailed plan with unit, load and failure-injection tests"]`.

Composite in code (never ask the model for the total):
```python
w = {"python_depth": .5, "leadership": .2, "system_design": .3}
composite = sum(w[k] * ans[k]["score"] / 3 for k in w)   # normalise by (levels-1)
```

## Phrasing rules learned
- All questions were written in the strong form first (concrete evidence per level, both noul poles defined, options described), and all passed; the weak forms were not measured, so these are conventions, not measured gains. Example of the pattern: weak `"Rate leadership 0-3"` -> `criteria` list where every level names what a resume would show.
- Split the rubric: one property per question; do not ask "technical and leadership" together.
- Put the text under a label (`RESUME`, `INBOUND LEAD FORM`, `DRAFT DESIGN DOC`) in `state`; the rubric lives in the questions, so the same state can be re-read with new dimensions.
- Add a noul evidence gate for any score dimension where the state may lack the signal. Score questions can ignore state: on a recipe, `leadership` returned 0.01 (good) and the gate `has_evidence` 0.0, but never assume this for other dimensions; keep this guard test in your own suite.
- Route with `choice` and describe every option as a sentence including the negative ones; the student lead routed to `discard` and the early-research lead to `nurture`, both correct.
- Unrelated dimensions stay low when many are listed (case 16: a frontend resume scored `python_depth` and `data_science` <= 0.7 among 6 dimensions), so listing many dimensions does not inflate scores.
- Do not send `weights` or `weight` to the server; they are ignored without an error, which would make an agent believe weighting happened server-side.
- Document rubrics need level text that names observable artifacts (owner and due date, exact commands, quantified impact). With those, a complete postmortem scored high and a "fixes / updated some stuff" PR description scored low, and a runbook separated detailed steps from missing rollback.

## Observed values
Strong resume 3.0/3.0/3.0 (python/leadership/design); mid resume ~2.0/2.0/1.85; weak resume ~1.0/0.0/0.0. Weight-flip pair: A 3.00/0.00, B 0.89/2.19 (python/leadership). Qualified lead: budget and authority gates high, route sales; student lead: gates low, route nurture or discard; early-research lead: nurture. Documents: design draft clarity 3.0, alternatives ~3.0, testing_plan 0.0; complete postmortem high on impact, root cause and action items; near-empty PR description low; runbook steps high, rollback low.

## Thresholds for acting
- Evidence gate (`noul`): treat >= 0.8 as present, <= 0.2 as absent, in between as "needs human review".
- Score: a level counts as reached when `score >= level - 0.3`. Rank on the weighted composite, but shortlist only when every must-have dimension clears its floor (avoid letting one strong dimension hide a 0).
- Route: act on `choice` only when its probability is >= 0.6, otherwise send to a human. Never auto-reject on one read; use `samples` for borderline (composite within 0.1 of the cut).
- Rescoring documents: alert only on drops of >= 0.5 levels between edits.

## MCP layer requirements (cases 15, 17, 18)
- `criteria` for `score` as a dict returns 422 "Input should be a valid list": the tool must validate and take a list (or convert ordered labels).
- Unknown model returns 400 "Unknown model": surface verbatim.
- Strip or reject `weights`/`weight` inputs (case 15); accept weights as a tool argument only to apply them locally.
- The tool should compute composites client-side from returned scores; re-weighting must not call the server again.

## Limitations
- Scores were near-saturated; nothing here measures calibration on subtle candidates. Gender/name bias and fairness testing of hiring rubrics was not done; a hiring decision must keep a human in the loop.
- Ranking is shown for one pair (cases 04/05), not for many resumes; the earlier same-candidate monotonic pair was dropped as a near-duplicate.
- Latency 3-14 s under shared load, far above the idle 0.4 s.
- One KNOWN LIMITATION case (15): no server-side weights.
