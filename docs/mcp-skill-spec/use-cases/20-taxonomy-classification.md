# 20. Hierarchical and high-cardinality classification

Case file: `tests/cases/20-taxonomy-classification.json`. Final result against the live server: **17 of 18 pass**. The one failure is the intentional `KNOWN LIMITATION` case (tax-18).

## Purpose
Put an item (ticket, diff, form, patent abstract, product text) into one node of a large or nested label set without a giant flat prompt. Two patterns:
1. Level-by-level traversal: one `choice` per tree node, batched in one request. Optionally beam search over K paths, scoring a path by the geometric mean (or product) of its edge probabilities.
2. Flat classification over dozens to hundreds of options, gated by confidence, with a broader parent (`other_<parent>`) as the fallback.

Probabilities come from the model distribution, so a low top probability is a real signal to stop at the parent instead of guessing a leaf.

## When a coding agent should reach for OpenJev
- A flat classifier has too many labels, or you are about to hand-write a category tree with keyword rules or a long "pick one of these 80 labels" prompt.
- Routing to owners, components, labels, CODEOWNERS areas, doc sections, tax or patent form types.
- You need "I do not know, escalate to the parent" as a first-class outcome instead of a forced leaf.
- Pruning a subtree cheaply: a `noul` "does this belong under X?" before descending.

Do not use it to enumerate the tree (code owns the taxonomy) or to invent new categories.

## Recommended question schemas

Root plus every plausible child level in ONE request (level-1 answer selects which child answers you read; the extra child answers are cheap):
```json
{"model":"openjev-latest","state":"<the item text>",
 "questions":{
  "root":{"type":"choice","instructions":"Which top-level area is the most likely owner of the fix?",
   "criteria":{"backend":"server-side code, APIs, business logic, database access","frontend":"browser UI, rendering, CSS, client-side JavaScript","infra":"deployment, CI/CD, cloud resources, networking, containers","security":"vulnerabilities, authentication weaknesses, secrets exposure","other":"none of the above or not enough information"}},
  "if_backend":{"type":"choice","instructions":"Assuming the owner is backend, which backend subcategory owns the fix?",
   "criteria":{"api":"HTTP endpoints, request validation, serialization","database":"queries, migrations, schema, connection pools","cache":"cache invalidation, Redis, memoization","other_backend":"a backend problem that fits none of the listed children"}}}}
```
Beam score for path root->child: `sqrt(P(root) * P(child))` (geometric mean of edges), or just the product. Keep the K best, descend only where P(root) is non-trivial.

Confidence gate with parent fallback (per level):
```json
{"sub":{"type":"choice","instructions":"This is a backend issue. Which backend subcategory best describes it? Choose other_backend if no listed child clearly fits.",
 "criteria":{"api":"...","database":"...","other_backend":"a backend problem that fits none of the listed children"}}}
```
Rule: accept the leaf if its probability is >= 0.6, otherwise report the parent.

Subtree pruning:
```json
{"in_security":{"type":"noul","instructions":"Does this issue belong under the security category?",
 "criteria":{"true":"the issue is a vulnerability or exposes sensitive data","false":"the issue is not a security matter"}}}
```

Flat high cardinality: put ALL options in `criteria` with meaningful descriptions (the description is what the model reads). Bare ids with generic descriptions ("category number 7") work only when the state matches exactly one described option.

## Phrasing rules learned
1. Put an explicit escape option in every level: `other` at the root, `other_<parent>` below, described as a positive condition. Instruct "choose other_x if no listed child clearly fits". Result: vague ticket "it doesnt work, please fix asap" went to `other` (tax-08), and a Python 3.12 distutils build break went to `other_backend` at P 0.94 (tax-07).
2. Child descriptions must be scoped by what the item IS, not just keywords. Before: `"rendering": "layout, CSS, component display"` attracted a stale-cache question; After: `"visual display of already-correct data"` and `"client-side state management and data-fetching logic"` moved the frontend question from `rendering` to a sensible answer.
3. Words in the state can hijack a level. "image-resize worker ... leaks native memory" is pulled to `queue` (P 1.0) by the word worker (tax-18, KNOWN LIMITATION). Adding to the escape option "anything else on the server: dependency upgrades, runtime versions, memory leaks, native libraries, logging, none of the five children above" and "if it is not about API, database, auth, queue or cache, choose other_backend" only got 0.52 vs 0.48. Mitigation: define what each child is NOT, and send P<0.7 answers to the parent.
4. Counterfactual branch questions ("Assuming the owner is frontend...") are flat when the assumption is false (max P 0.48 in tax-06, and unstable between runs: `rendering`, `state` or `other_frontend`). That is fine for beam search because the path score multiplies by the small root probability, but never act on a branch answer alone. Always read child answers together with P(parent).
5. Batch all levels you might need in one request. Latency grows with question count, but beats sequential round trips. Do not batch more than about 6 to 8 child questions.
6. For batched independent documents use one question per document with identical `criteria` (tax-14, both correct at P>=0.8), instead of a compound question.
7. Root-level questions lose nothing from short, keyword-rich descriptions; leaf-level ones need contrastive descriptions ("issued by an employer" vs "paid to a contractor", tax-05).
8. Root and second-level routing both work with the same recipe (tax-16, tax-17): contrastive child descriptions plus an `other_<parent>` escape gave P>=0.6 on clear items without any tuning.
9. Use `think: 256, samples: 2` only for adversarial cases (secret leaked in CI log: security vs infra, tax-13). Both were acceptable owners; asserting either avoids a false failure.

## Thresholds for acting
- Leaf accepted: P(leaf) >= 0.6 and choice is not `other*`. Between 0.35 and 0.6: stop at the parent, or retry once with `think: 256, samples: 2`. Below 0.35: parent, flag for a human.
- Auto-route (no human): P(root) >= 0.7 and P(child) >= 0.7 (path geometric mean >= 0.7).
- Observed on clear cases: root 0.8+, 15-way forms 0.85+, 40-way libraries 0.6+, 120-way with one meaningful option 0.5+.
- `noul` pruning: descend when P >= 0.3, prune at <= 0.1 (clear negatives were <= 0.1, clear positives >= 0.85).
- Mixed-signal item (stale prices: Cache-Control plus Redis): batch answers agreed on root `backend` (P >= 0.5) and leaf `cache` (P >= 0.5). A pure beam of K=2 would still surface the frontend path for a human.

## Limitations
- Option caps (per README): 255 on TypeSafe, 128 on Codiv, 52 on the HF OpenJev build. This live server accepted 120 options; 300 options gave HTTP 400 when probed earlier. The MCP layer must read the cap for the target and split into a tree (or pre-filter by a cheap code step) above it. Exact per-server caps were not probed beyond 120/300.
- Wide flats cost seconds: 120 options took 6 to 14 s on a shared server versus about 0.4 s idle for small requests. Timeouts should be generous.
- Escape options are not honored reliably when a strong keyword points to a wrong child (tax-18). Add a human-review path for P<0.7.
- A choice question without `criteria` is HTTP 422; the MCP layer must validate before sending. This and the over-cap 400 are API validation checks, not classification demos, so they no longer live in this case file (the 300-option 400 was observed earlier; see the API validation tests).
- Placement in a tree is only as good as your descriptions; the model cannot see your CODEOWNERS or ownership rules. Put tie-break rules in `instructions`.
- The model selects a label, it does not create one. New categories are a code/human decision.

## Test inventory (18 cases)
All cases are positive classification demonstrations except tax-18 (KNOWN LIMITATION). Former error cases tax-16/tax-17 were replaced by positive examples.

tax-01 root backend; tax-02 second-level database or api; tax-03 root frontend; tax-04 15 form types (W-2); tax-05 1099-NEC vs W-2 lookalikes; tax-06 batched beam (root + two child branches) on a mixed-signal issue; tax-07 parent fallback (`other_backend`); tax-08 vague ticket goes to `other`; tax-09 noul subtree gate yes; tax-10 noul subtree gate no; tax-11 40 library options; tax-12 120 options with descriptions; tax-13 `think` + `samples` on secrets-in-CI-log; tax-14 two documents in one multi-question call; tax-15 patent abstract to section H; tax-16 root mobile (iOS crash); tax-17 second level under infra, failing readiness probe goes to `kubernetes`; tax-18 KNOWN LIMITATION (word "worker" hijacks the fallback).

Final pass count: 17/18 (tax-18 fails by design).
