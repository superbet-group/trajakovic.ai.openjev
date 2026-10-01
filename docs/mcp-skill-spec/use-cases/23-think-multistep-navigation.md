# Usage type 23: Multi-step decisions that need think (graph, game, planning)

Final result: 13/13 cases pass against the live server (`tests/cases/23-think-multistep-navigation.json`), all of them decision cases. After an audit, the near-duplicate goal_reached negative was replaced by a distractor case, the trivial per-host number comparison was replaced by a dependency-readiness puzzle, a visited-set backtrack case was added, and the two API error cases were removed from this suite (they are validation behavior, listed below and covered by the error-handling suite). No threshold was loosened. No KNOWN LIMITATION case was needed.

## Purpose
Turn each tick of a sequential task into a typed read: pick the next hop in a graph or link structure, pick a move among server-validated legal moves, decide "goal reached?" as a noul, or split a puzzle into per-item noul questions and count in code. OpenJev only ranks the options you give it; the loop, the legality check and the counting stay in code.

## When a coding agent should reach for OpenJev here
Instead of: reasoning in prose about which file/link/move to try next, or asking an LLM for a plan and parsing it. Trigger when:
- The task is inherently sequential (walk repo/docs/wiki/Neo4j hop by hop, game tick, migration ordering).
- A single read gives low confidence (`confidence` < 0.6) or two reads disagree.
- The agent is about to write "let me open X, then maybe Y" for 3 or more candidate next steps.
Do NOT use it to enumerate legal moves, compute paths, or count (code does that), nor when the state lacks the facts (it cannot look at links you did not show it).

## Recommended question schemas
State layout that worked: `GOAL:`, `CURRENT NODE:` (+ `CONTENT:`), `NEIGHBORS (legal next hops):` or `LEGAL MOVES:` pasted from the server or graph query.

Hop / move choice (option names = exactly the legal moves; one description per option):
```json
{"model":"openjev-latest","think":512,
 "state":"GOAL: find where failed payment retries are scheduled.\nCURRENT NODE: services/billing/README.md\nCONTENT: ...\nNEIGHBORS (legal next hops): plans.py | charge_processor.py | invoice_pdf.py",
 "questions":{"next_hop":{"type":"choice",
   "instructions":"Which single neighbor should be opened next to get closest to the GOAL?",
   "criteria":{"plans.py":"open plans.py","charge_processor.py":"open charge_processor.py","invoice_pdf.py":"open invoice_pdf.py"}}}}
```
Goal reached (run it on every node before expanding):
```json
{"goal_reached":{"type":"noul",
  "instructions":"Does the CURRENT NODE itself contain the logic that schedules failed payment retries?",
  "criteria":{"true":"the node defines when and how retries are scheduled","false":"the node only mentions related topics or points elsewhere"}}}
```
Progress / backtrack signal:
```json
{"progress":{"type":"score","instructions":"How close is the CURRENT NODE to the GOAL? Judge by topical relevance of its content.",
  "criteria":["unrelated dead end, backtrack","loosely related area","closely related, likely one hop away","contains the goal"]}}
```
Per-item puzzle: one noul per item (`ready_c`, `ready_d`, ...) with the same instructions template; the agent collects `noul >= 0.5` in code. Use it only when each item needs a lookup or judgement (here: prerequisites against a DONE list, nav-08); do not use it for plain number comparisons like "is 95 > 90", which code does exactly.
Ordered plan: `"sequential": true` with `next_step` (choice) then a safety noul that sees the previous answer.

## Phrasing rules learned
- Restrict criteria to legal moves only. Suite: with 3 legal chess moves the answer was always one of them (nav-07). The model never invents an option, but if you list an illegal one it can pick it, so code must filter first and re-validate the returned choice.
- State the constraint that makes the plan order decidable. Before: "Which step should be done next?" (picked `backfill`, the wrong order for zero-downtime, conf ~0.6) -> After: state adds "new users keep signing up... a backfill before dual-write would miss rows" and asks "...so that no row is ever missed?" -> `dual_write`, conf 0.99. The model reads facts, it does not reliably recall unstated best practice.
- Test goal_reached with a hard negative, not just an easy one: a node that calls or imports the target logic (worker.py calling `compute_delay`) scored <= 0.15 once the question said "defines ... as opposed to only calling code that does" (nav-03). Ask for definition vs. reference explicitly.
- Show the visited set and why it was rejected ("throttle.py only reads RATE_LIMITS"); the model then moved to the unvisited settings.py (nav-13).
- Split "did we arrive" from "where next": a separate `goal_reached` noul gave 1.0 on the goal node and 0.0 on the README that merely points at it.
- Describe each option in criteria (`"open charge_processor.py"`), not just the key; many options (7 wikirace links) worked.
- Put a pointer sentence in the node CONTENT if the graph edge label carries the signal ("Charge attempts ... handled in charge_processor.py"); the model follows textual hints well.

## think and samples: measured
Same request with and without `think` (single read each):
- Tic-tac-toe winning move (nav-05): `think:512` -> c3, confidence 1.00. No think -> c3 but confidence 0.34. The answer was the same, the confidence was not usable without think.
- 3-node reachability (nav-10): think 768 -> C, 0.99; no think -> C, 0.89. Neutral to mildly better.
- Block-the-threat move (nav-06): think 512 -> b3, 1.00; no think -> b3, 0.97. Neutral.
So think never hurt, and it raised confidence where the board needed lookahead. Cost: about 12-20 s per call on the busy shared server vs 4-11 s without (idle server: much lower). `samples:4` (nav-11) returned a stable 0.92 on a hop with distractors (errors.py mentions RetryableError). Note the README there names backoff.py explicitly, so this shows stability, not resolution of real ambiguity. Untested: sample spread as an uncertainty number; the response has `confidence` but this suite did not extract per-sample variance.

## Thresholds for acting
- Follow the hop/move when `confidence >= 0.8`. Between 0.5 and 0.8: rerun with `think: 512` and `samples: 4`; if still < 0.8, explore the top 2 (beam of 2) rather than commit.
- Below 0.5 (nav-07 got 0.59 on a genuinely underdetermined pair of safe moves): treat options as tied, pick by a code tiebreak.
- `goal_reached`: stop at >= 0.85; keep walking at <= 0.3; in between, read the node fully or ask a second phrasing.
- Always check `choice in legal_moves` in code and cap the loop (max hops, visited set). Never let the model's answer bypass validity.

## Error cases the MCP layer must handle (not part of this suite)
Observed earlier, kept here as tool requirements; regression tests belong in the error-handling suite:
- `think: 9000` (over 4096) -> HTTP 422. Clamp or reject `think` to 0-4096 client-side.
- `think` (or `sequential`) with `images` -> HTTP 400. The tool should refuse the combination up front.

## Limitations
- The graph scenarios are small and the cue is in the text; a real multi-hop link choice with weak hints was not tested. Wikirace was tested with one hop and a target the model knows (Apollo Guidance Computer accepted; NASA also allowed).
- Beam search and full-path planning were not run; only single ticks. Depth-N behavior is unmeasured.
- Latency in this run was 4-20 s per case because the server was shared; think roughly doubles it.
- Legal-move enforcement is the caller's job; the API does not know your rules.
