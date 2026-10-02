---
name: openjev-multistep
description: Use when a decision is one tick of a sequential process: which file, link, node or neighbour to open next while navigating a repo, docs, wiki or graph toward a goal; which move to play among legal moves; which step of a migration or plan comes next; whether the current node already contains the goal; or when a single OpenJev read returned low confidence and the decision needs lookahead.
---

# Multi-step navigation and planning ticks

Precedence: code enumerates legal moves/neighbours, keeps the visited set, caps the loop and
validates every returned choice. OpenJev only ranks the options you give it.

## Per tick
1. `goal_reached` noul on the current node first, asked as definition vs reference (nav-03: a node that
   only calls the target logic scored <= 0.15 with this wording):
```json
{"goal_reached": {"type": "noul", "instructions": "Does the CURRENT NODE itself define how long to wait before a retry (the delay computation), as opposed to only calling code that does?", "criteria": {"true": "the node contains the delay/backoff computation", "false": "the node only calls or imports the delay computation from elsewhere"}}}
```
   Stop at >= 0.85; keep walking at <= 0.3; between, read the node fully.
2. Hop/move choice with keys = legal moves only, one description each; state has `GOAL:`,
   `CURRENT NODE:`/`CONTENT:`, `VISITED:` with rejection reasons, `NEIGHBORS (legal next hops):`.
3. Follow at confidence >= 0.8. 0.5-0.8: re-read with `think: 512` (text only) and `samples: 4`;
   still < 0.8 -> explore the top 2 (beam). < 0.5 -> tie, code tiebreak.
4. Plans: state the constraint that makes the order decidable (the model reads facts, it does not
   recall unstated best practice); `sequential: true` when later answers depend on earlier ones.
5. Puzzles: one noul per item (e.g. "is task C ready given DONE = A, B?"), count in code.

`think` raised confidence where lookahead matters (tic-tac-toe 0.34 -> 1.00) and never hurt, but it
costs 3-20 s and is not reproducible: repeat it when it decides something costly.

## Worked example
Tic-tac-toe, X to move, legal moves a3, b3, c1, c2, c3; `think: 512` -> c3 (winning) at 1.00.
