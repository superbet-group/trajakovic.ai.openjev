# Use case 22: Image, screenshot, browser and desktop action decisions

Test file: `tests/cases/22-image-and-ui-decisions.json` (11 image cases, 11/11 passing against the live MLX server, openjev-latest). The text-only accessibility-tree cases (next-element, irreversible, done/blocked) moved to `tests/cases/22b-accessibility-tree-ui-decisions.json` (5 cases, 5/5 passing, ids `tree-01..05`); they are not image tests. The old mirror pair Place order / Edit cart was folded into one case (`tree-03`, Place order positive).
Fixtures: `tests/data/ui22_*.png` (synthetic 800x500 screenshots made by `tests/data/gen22.py`: login wall, dashboard, 500 error page, cookie modal, delete-confirm dialog) plus the shared `tests/data/hotdog.jpg`. The case builder is `tests/gen22_cases.py`.

## Purpose
Let a browser or computer-use agent turn "what am I looking at and what should I click" into typed reads. Two input styles:
1. Image mode: up to 8 screenshots (data URLs) plus typed questions (login wall? error page? which page kind? is this dialog irreversible?).
2. Candidate mode: the UI as text (accessibility tree, DOM slice, OCR table) with stable ids `e01..e40`. A `choice` picks the next element, `noul` questions answer done / blocked / irreversible. Code validates that the returned id is in the candidate set, then acts.

Candidate mode is faster (about 0.3 to 0.5 s on an idle server) and more precise than image mode; prefer it whenever a tree exists. Use image mode when there is no DOM (canvas, remote desktop, a UI test-failure screenshot, an uploaded photo).

## When to reach for OpenJev instead of reasoning in prose
- The agent is about to write "this looks like a login wall / error page / consent dialog" about a screenshot. Ask, act on the number.
- Choosing which of N ids to click: do not free-write the id; ask a `choice` whose keys are exactly the candidate ids.
- Before any click on a payment, delete, send or publish control: ask the irreversible `noul`. High means stop and confirm with the human.
- Loop control in a browser task: after each step ask `done` and `blocked` instead of "I think we are finished".
- Classifying uploaded images against a fixed taxonomy.
- Not for: reading text out of an image (OCR, extraction of order numbers, prices; use a real OCR/DOM read), pixel coordinates, or judging fine visual detail (alignment, colour shade).

## Recommended question schema
State layout, candidate mode (always include the goal and the ids with their role and name):
```
Goal: <what the user wants>
Page: <url> (accessibility tree)
e01 link 'Home'
e06 combobox 'Size' (value: none selected)
e07 button 'Add to cart' (disabled until a size is chosen)
```
Next element (keys must be exactly the candidate ids; give each a short description):
```json
{"model": "openjev-latest",
 "state": "Goal: add the blue shoes, size 42, to the cart.\nPage: shop.example.com/product/x\ne03 searchbox 'Search'\ne06 combobox 'Size' (none selected)\ne07 button 'Add to cart' (disabled until a size is chosen)",
 "questions": {
  "next": {"type": "choice",
           "instructions": "Which element should the agent interact with next to make progress on the goal?",
           "criteria": {"e03": "search box", "e06": "size dropdown, where the size is chosen", "e07": "add to cart button"}}}}
```
Irreversible / done / blocked (one flag each, sent together):
```json
{"questions": {
  "irreversible": {"type": "noul", "instructions": "Does clicking this element commit an action that cannot be undone, such as submitting payment, deleting data or sending a message?"},
  "done": {"type": "noul", "instructions": "Does the page state show that the goal is fully accomplished?"},
  "blocked": {"type": "noul", "instructions": "Is the agent blocked by something it cannot get past without human help, such as a CAPTCHA, a two-factor prompt or a permissions error?"}}}
```
For irreversible, append `Proposed click: e04 button 'Place order'` to the state.

Image mode:
```json
{"model": "openjev-latest",
 "state": "Screenshot of the page the browser agent just loaded.",
 "images": ["data:image/png;base64,..."],
 "questions": {
  "login_wall": {"type": "noul", "instructions": "Does this screenshot show a login wall or sign-in prompt that blocks the content the user wants to read?"},
  "kind": {"type": "choice", "instructions": "What kind of page does this screenshot show?",
           "criteria": {"login_wall": "a sign-in or paywall page asking for credentials or a subscription",
                        "dashboard": "a working application page with data, metrics or lists",
                        "error_page": "an HTTP error or crash page such as 404 or 500",
                        "consent_modal": "a cookie or privacy consent dialog covering the page"}}}}
```
Pass a short text `state` along with images (what the screenshot is, the task, and for multiple images their order).

## Thresholds for acting (observed values in brackets)
- Page-state noul (login wall, error, blocked, done): act at >= 0.8 true, treat <= 0.2 as false, in between re-read once with `samples` higher or a fresh screenshot. [observed: 1.000 / 0.000 on clear cases; 0.997 on the multi-image case.]
- Irreversible: >= 0.5 means require human confirmation; only auto-click when <= 0.15. [Place order 1.000, Edit cart 0.000, Delete permanently 1.000.] Fail toward asking.
- Choice: accept when confidence >= 0.8 [observed 0.989 to 0.998]; below that, take a fresh snapshot or ask the human. Always check `choice in candidate_ids` before acting; the server only returns keys you sent, but the tree may have changed since the read (stale ids), so re-validate against the live DOM at click time.
- Done: never stop on `done` alone when the task is irreversible; also require a deterministic signal (confirmation URL or text) when one exists.

## Phrasing rules learned
1. Keys of `choice.criteria` are the output names: use the real ids (`e21`) and describe each in words. Ids alone are meaningless to the model, so put the role and accessible name in both the state list and the criteria description. Forty candidates worked fine (22b tree-02: picked `e21` "Billing and invoices" at 0.989) at about 0.5 to 0.7 s idle.
2. One flag per noul. done and blocked are separate reads and can differ (22b tree-04 done 1.0 blocked 0.0; tree-05 the reverse).
3. State what "irreversible" means with concrete verbs ("submitting payment, deleting data or sending a message"). Put the proposed element in the state, not in the question, so the same question text is reusable.
4. Put the task in the state for choices where the goal decides the answer (ui-06: with the task "read without agreeing to tracking" the choice is Reject `e2` at 0.991; without it, Accept and Reject are both plausible).
5. Describe disabled or precondition states in the tree text ("disabled until a size is chosen"); the model then correctly picks the size dropdown `e06` (0.998) over the tempting Add to cart.
6. For multi-image requests say which image the question is about ("Answer about the second") and give the order in the state (ui-07).
7. Ask about page kind with a `choice` over 3 to 6 short, mutually exclusive descriptions, rather than several overlapping noul flags.
8. Keep image cases and accessibility-tree cases in separate files: the tree cases test no vision. No before/after rewrites were needed: all cases passed on the first phrasing. Mirror cases (same question, positive and negative candidate) add little; one positive plus a threshold rule is enough. Synthetic screenshots are clean and high-contrast; expect more noise on real screenshots.

## Extensions and edges
- `images`: max 8, each `data:image/{jpeg,png,webp,gif};base64,...` or `{content_type, base64}`. An `https://` URL is a 400 (ui-11): the MCP layer must download and base64-encode it, and downscale big screens (each image costs about 256 tokens regardless of size, larger images cost more latency, not much more tokens).
- `think` and `sequential` are rejected with images (400 "think needs a text state", ui-10). For hard UI decisions, convert the page to a candidate text (tree/OCR) and use `think` there.
- 9 images is a 400 "at most 8 images per request" (ui-09). Batch or pick key frames.
- Latency budget: about 2 s per image read; the run here measured 4.6 to 17 s per request because the server was shared with other agents. Use `samples: 1` for repeated reads of the same screenshot with new questions (prefill is cached). The runner does not assert latency, the MCP layer should set a client timeout (suggest 30 s) and fail closed to "ask the human".
- MCP layer errors to handle: 400 image count, 400 image type (svg, `image/jpg` spelling), 400 non-base64, 400 think+image. Translate to a clear tool error, do not retry unchanged.

## Limitations
- Clean synthetic screenshots only; real pages with dense text, small fonts or dark themes were not tested. Text inside images is read by the vision tower, not OCR: do not trust it for exact strings.
- The model gives no coordinates. Clicking needs an id-to-element map from the DOM/accessibility tree or a separate grounding step.
- Candidate lists over about 255 options are rejected (400); trim the tree to visible, interactive nodes first (aim for under 60).
- Probabilities saturate near 0 and 1 on clear cases, so the mid-range is rare; use the ask-the-human path for anything in 0.2 to 0.8.
- Ids go stale after any navigation or re-render; validate against the live page at act time.
- Not tested: SVG screenshots (rejected by the server), animated GIFs, 8-image batches (documented in 00-api-surface.md at 4.2 s).

## Final result
11/11 image cases pass: 6 image-mode reads (login wall pos/neg, page-kind x2, irreversible dialog, choice among visible elements), 1 multi-image, 1 photo taxonomy, 3 error cases (9 images, think+image, URL image). Companion 22b: 5/5 tree cases. No KNOWN LIMITATION cases.
