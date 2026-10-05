---
name: openjev-dispatch
description: "Selects which skill, tool, MCP server, function or model tier handles a request with OpenJev recipes (skill_selection, typed_call, model_routing). Use when picking the right entry from a long roster for a prompt (or none), mapping a natural-language command such as 'roll back payments in staging' to a function name with enum and boolean arguments, building a chat or Slack front end for a CLI or API, or choosing a model tier and thinking effort before delegating a subtask."
---

# Dispatch with OpenJev

Tools live on the `openjev` MCP server as `mcp__openjev__<tool>` (with the openjev-mcp plugin: `mcp__plugin_openjev-mcp_openjev__<tool>`). If they are missing, load skill `openjev-data-prep` (Connect) and do not start or restart OpenJev or its MCP server without the user's go-ahead; give the user the command.

Precedence: exact matches in code first (a slash command, an explicit tool name, argparse for flags). OpenJev through `mcp__openjev__recipe` for prose. Open-ended values (numbers, tickers, dates, paths, free strings) are extracted in code, never read.

## Checklist

```
- [ ] Try exact matching in code (slash command, tool name, argparse)
- [ ] Pick the recipe: skill_selection (which entry), typed_call (which function), model_routing (which tier)
- [ ] Build the roster / function list / summary exactly as the input schema says
- [ ] Call with dry_run once, then for real
- [ ] Validate the returned id against your own list before using it
```

## Skill or tool selection

`roster` is an array of `{id, description}` objects (at most 254; pre-filter above about 100). The recipe adds a `none` option ("no entry applies, answer directly") and a guard that a topic word inside a code change does not count as the activity.

<!-- openjev-call: recipe -->
```json
{"recipe": "skill_selection", "inputs": {"prompt": "implement a sliding window maximum in O(n) using a deque, in Python", "roster": [{"id": "pptx", "description": "Create slide decks and pitch decks in PowerPoint format"}, {"id": "pdf", "description": "Read, extract, merge, split or fill PDF files"}, {"id": "systematic-debugging", "description": "Investigate a bug, failing test or unexpected behavior before proposing fixes"}]}}
```

The `decision` is `inject:<id>`, `none` or an abstain; `signals.skill` and `signals.skill_p` hold the top pick. Inject at `skill_p` of 0.8 or more; below 0.6 abstain and answer directly or ask the user. Keyword traps are marginal, so never inject on a low score. Always validate the id against the roster.

## Natural language to typed call

The `typed_call` recipe reads only the function choice (plus `no_match`). Argument values, enum choices and boolean flags are the caller's to read with `mcp__openjev__ask`, one question per parameter.

<!-- openjev-call: recipe -->
```json
{"recipe": "typed_call", "inputs": {"sentence": "roll back payments in staging and wait until healthy", "functions": [{"name": "rollback", "description": "revert a service to its previous release"}, {"name": "deploy", "description": "ship a new release of a service"}, {"name": "status", "description": "show the health of a service"}]}}
```

Decisions: `call`, `confirm`, `ask`, `no_match`. Act when `call`; on `ask` or `confirm` show the parsed call. Then read the arguments:

<!-- openjev-call: ask -->
```json
{"state": "USER COMMAND: roll back payments in staging and wait until healthy", "questions": {"env": {"type": "choice", "instructions": "Which environment does the user name?", "criteria": {"dev": "the development environment", "staging": "the staging environment", "prod": "the production environment", "not_stated": "no environment is named"}}, "wait": {"type": "noul", "instructions": "Does the user ask the command to block until the rollout is healthy?", "criteria": {"true": "the user asks to wait or block until healthy", "false": "the user does not ask to wait"}}, "force": {"type": "noul", "instructions": "Does the user ask to bypass safety checks or force the operation?", "criteria": {"true": "the user asks to force or skip checks", "false": "no forcing or bypass is requested"}}}}
```

Enums need `p_top` of 0.7 or more, otherwise ask "did you mean X or Y". Destructive flags need 0.9 or more (or 0.1 or less); else use the safe default (`dry_run` on, `force` off) and confirm. With negation words ("don't", "except", "without"), ask the flag both ways; disagreement means ask the user. Anything touching production: show the parsed call and get a yes.

## Model and effort routing

The summary is a one-line task description: `Task summary: <verb> <object>; <scope>; <root cause known?>; <spec clear?>; <tests>`.

<!-- openjev-call: recipe -->
```json
{"recipe": "model_routing", "inputs": {"summary": "Task summary: Rename the local variable usr_cnt to user_count in one function (3 occurrences); no behavior change; spec clear; tests exist and pass."}}
```

`decision` is `haiku`, `sonnet` or `opus`, and the output carries an `effort` of low, medium or high. Haiku only when the task is shallow and the tier read is confident; deep tasks get high effort; a low-confidence tier rounds up one tier. It routes on the summary only, so a lazy summary routes cheap. Fail open to the safe default (sonnet, medium). A server setting can turn routing off, in which case sonnet comes back without a read.

## Worked example

Prompt "implement a sliding window maximum in O(n) using a deque" against a roster containing `pptx`: the word "sliding" must not load the slides skill. The `skill_selection` call above returns `none` with high confidence. Answer directly.
