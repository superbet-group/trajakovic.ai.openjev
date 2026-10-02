---
name: openjev-dispatch
description: Use when choosing which tool, skill, MCP server, subcommand, function or model to use: selecting the right skill or tool from a long roster for a user prompt (or none), mapping a natural-language command ("roll back payments in staging and wait until healthy") to a function name plus enum and boolean arguments, building a chat/voice/Slack front end for a CLI or API, or picking a model tier and thinking effort before delegating a subtask or spawning a subagent.
---

# Dispatch with OpenJev

Precedence: exact matches in code first (a slash command, an explicit tool name, argparse for
flags). OpenJev for prose. Open-ended values (numbers, tickers, dates, paths, free strings) are
extracted in code, never read.

## Skill / tool selection (use case 09, 20/20)
```json
{"recipe": "skill_selection", "inputs": {"prompt": "<user prompt>", "roster": {"pptx": "Create slide decks and pitch decks in PowerPoint format", "pdf": "Read, extract, merge, split or fill PDF files", "systematic-debugging": "Investigate a bug, failing test or unexpected behavior before proposing fixes"}}}
```
The recipe adds `none` ("No skill from the roster applies; answer directly without loading any
skill") and the guard "Pick a skill only if the activity the user wants done IS what the skill does;
a topic word appearing in a code change does not count." Inject at `p_top` >= 0.8; abstain below
0.6 (keyword traps are marginal: 0.41 vs 0.36); `mode: gate` gives one noul per tool for
multi-select (activate >= 0.7). Roster cap 254 + none; pre-filter above ~100. Validate the id.

## Natural language to typed call (use case 10, 17/17)
```json
{"recipe": "typed_call", "inputs": {"sentence": "<what the user said>", "functions": [
 {"name": "rollback", "description": "revert to the previous release",
  "params": [{"name": "env", "kind": "literal", "options": {"dev": "development environment", "staging": "staging environment", "prod": "production environment"}},
             {"name": "wait", "kind": "bool", "claim": "Does the user ask the command to block until the rollout is healthy?"},
             {"name": "force", "kind": "bool", "claim": "Does the user ask to bypass safety checks or force the operation?", "destructive": true}]}]}}
```
One choice for the function (+ `no_match`), one choice per literal, one noul per bool worded as a
claim about what the user asked. Negation words ("don't", "leave out", "except", "without") -> the
recipe asks the flag both ways; disagreement -> ask the user. Act: function `p_top` >= 0.8; enums
>= 0.7 else "did you mean X or Y"; destructive flags need >= 0.9/<= 0.1 else the safe default
(`dry_run=true`, `force=false`) and a confirmation; anything touching production: show the parsed
call and get a yes.

## Model and effort routing (use case 08, 16/16)
State: `Task summary: <verb> <object>; <scope>; <root cause known/unknown>; <spec clear?>; <tests>`.
```json
{"recipe": "model_routing", "inputs": {"summary": "Task summary: Rename the local variable `usr_cnt` to `user_count` in src/stats.py (3 occurrences, one function). No behavior change. Tests already exist and pass."}}
```
Measured on that summary: tier haiku 1.0, deep 1.1e-06, cx 0.11. Rules: haiku only when deep <= 0.15 and tier haiku `p_top` >= 0.9; deep >= 0.7 -> high effort; cx >= 3.0 ->
opus class; tier `p_top` < 0.5 -> round up one tier. Fail open to the safe default (sonnet,
medium). `OPENJEV_MCP_ROUTING=off` is the kill switch.

## Worked example
Prompt "implement a sliding window maximum in O(n) using a deque, in Python" with an 18-skill roster
containing `pptx`: the keyword "sliding" must not load the slides skill. `skill_selection` -> `none`
0.999. Answer directly.
