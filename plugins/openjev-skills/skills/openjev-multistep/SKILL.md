---
name: openjev-multistep
description: "Decides one tick of a sequential process with OpenJev (multistep_tick, choice reads with lookahead): which file, link, node or neighbour to open next while navigating a repo, docs, wiki or graph toward a goal, which legal move to play, which plan or migration step comes next, and whether the current node already contains the goal. Use when a decision repeats step by step toward a goal, or when a single OpenJev read returned low confidence and needs lookahead."
---

# Multi-step navigation and planning ticks

Tools live on the `openjev` MCP server as `mcp__openjev__<tool>` (with the openjev-mcp plugin: `mcp__plugin_openjev-mcp_openjev__<tool>`). If they are missing, load skill `openjev-data-prep` (Connect) and do not start or restart OpenJev or its MCP server without the user's go-ahead; give the user the command.

Precedence: code enumerates legal moves and neighbours, keeps the visited set, caps the loop and validates every returned choice. OpenJev only ranks the options you give it, through `mcp__openjev__recipe` (`multistep_tick`) or `mcp__openjev__ask`.

## Per-tick checklist

```
- [ ] Code builds the legal moves list (id + one-line description each)
- [ ] Ask goal_reached on the current node first (stop at 0.85 or more)
- [ ] Call multistep_tick with goal, current node, visited set and the legal moves
- [ ] Follow a `move:<id>`; on `beam` explore the top two; on `tie` break the tie in code
- [ ] Add the node to visited with the reason, cap the loop, repeat
```

## 1. Goal reached?

Ask it as definition versus reference: a node that only calls the target logic must score low.

<!-- openjev-call: yes_no -->
```json
{"state": "GOAL: find where the retry delay is computed\nCURRENT NODE: retry.py\nCONTENT:\ndef run(job):\n    delay = backoff(job.attempt)\n    sleep(delay)", "claim": "Does the current node itself define how long to wait before a retry (the delay computation), as opposed to only calling code that does?", "true_means": "the node contains the delay or backoff computation", "false_means": "the node only calls or imports the delay computation from elsewhere"}
```

Stop at p of 0.85 or more; keep walking at 0.3 or less; in between read the node fully.

## 2. Pick the next move

The state is one caller-composed text: `GOAL:`, `CURRENT NODE:` with `CONTENT:`, `VISITED:` with rejection reasons, and the board or neighbours. `legal_moves` is an array of `{id, description}` (at most 120); only those ids can be returned.

<!-- openjev-call: recipe -->
```json
{"recipe": "multistep_tick", "inputs": {"situation": "GOAL: find where the retry delay is computed\nCURRENT NODE: retry.py (calls backoff(attempt) from timing.py)\nVISITED: README.md (no code), main.py (only wires the job)", "question": "Which neighbour should be opened next to reach the goal?", "legal_moves": [{"id": "timing.py", "description": "time and backoff helpers"}, {"id": "queue.py", "description": "job queue and workers"}, {"id": "config.py", "description": "settings loader"}]}}
```

The `decision` is `move:<id>`, `beam` or `tie`; `stop` is never returned here (that is the goal_reached read). Follow at confidence of 0.8 or more. Between 0.5 and 0.8 the engine re-reads once with lookahead; if still below 0.8 you get `beam`: explore the top two. Below 0.5 is a tie: pick in code.

## 3. Plans and puzzles

State the constraint that makes the order decidable; the model reads facts, it does not recall unstated best practice. Use `options.sequential: true` when later answers depend on earlier ones. For puzzles ask one noul per item ("is task C ready given DONE = A, B?") and count in code. For a single hard choice use `options.think` through `mcp__openjev__ask`:

<!-- openjev-call: ask -->
```json
{"state": "Tic-tac-toe, X to move.\nBOARD:\nX O .\nO X .\n. . .\nLEGAL MOVES: c3, c1, a3, b3, c2", "questions": {"move": {"type": "choice", "instructions": "Which legal move should X play now to win?", "criteria": {"c3": "completes the main diagonal and wins", "c1": "a square in the first row, third column", "a3": "a square in the first column, third row", "b3": "a square in the second column, third row", "c2": "a square in the third column, second row", "other": "none of these moves"}}}, "options": {"think": 512}}
```

`think` raised confidence where lookahead matters (0.34 to 1.00 on this kind of position) and never hurt, but it costs 3 to 20 seconds, is text-only and is not reproducible: repeat it when it decides something costly.

## Worked example

Graph walk toward the retry-delay code: `retry.py` fails goal_reached (it only calls `backoff`), the tick returns `move:timing.py`, the next node passes goal_reached at 0.9, stop.
