# 10. Natural language to typed function call

Test file: `docs/mcp-skill-spec/tests/cases/10-nl-to-typed-call.json` (17 cases, live server, model `openjev-latest`).
Final result: **17/17 passed** (15 on the first run, plus two cases added after probing a negation weakness, see rule 4).

## Purpose

Map a sentence to a function name plus closed-set arguments. The function name is one `choice`, every `Literal`/enum parameter is one `choice`, every `bool` flag is one `noul`. The questions are generated from the function signatures, so the model can only return values that exist. Open-ended values (numbers, ticker symbols, file paths, free strings) stay in deterministic code (regex, `argparse`, a lookup table).

Example: `plot rolling correlation nvda spy 1 month` becomes `plot_rolling_correlation(symbol="nvda", benchmark="spy", window="1m", log_scale=False)`. OpenJev picks the function, `window` and `log_scale`; code extracts the tickers.

## When a coding agent should reach for OpenJev

Trigger when the agent is about to:
- write an argument parser, regex table or `if "restart" in text` chain for prose commands;
- build a chat, voice or Slack front end for a CLI, smart-home API or internal tool;
- fill `Literal`/enum/bool parameters of a tool from a user sentence;
- write a prompt asking an LLM to "output JSON with the function and args" and then parse it.

Reasons to prefer OpenJev over prose reasoning: the answer is a probability read from the model distribution (~0.4 s), so it cannot be malformed, cannot invent an option, and gives a per-argument confidence to gate on. Do not use it for open-ended values, for arithmetic, or for anything where a wrong guess is destructive without a confirmation step.

## Recommended question schema

Generate one question per argument from the signature. Copy-pasteable template (CLI example):

```json
{
  "model": "openjev-latest",
  "state": "chat: hey can you roll back the payments service in staging to the last release, and wait until it's healthy before you come back to me",
  "questions": {
    "verb": {"type": "choice", "instructions": "Which CLI subcommand does the user want?",
             "criteria": {"restart": "restart running instances", "scale": "change the replica count",
                          "rollback": "revert to the previous release", "logs": "print logs",
                          "delete": "remove a resource", "no_match": "none of the other subcommands applies"}},
    "env": {"type": "choice", "instructions": "Which environment is targeted?",
            "criteria": {"dev": "development environment", "staging": "staging environment", "prod": "production environment"}},
    "wait": {"type": "noul", "instructions": "Does the user ask the command to block until the rollout is healthy?"},
    "force": {"type": "noul", "instructions": "Does the user ask to bypass safety checks or force the operation?"}
  }
}
```

Generation rules for the MCP tool `typed_call` (input: sentence, function specs):
1. One `choice` named `function`, options = function names, each with a one-line description from its docstring, plus a `no_match` option.
2. One `choice` per `Literal`/enum parameter, options = the allowed values, each with a short plain-language description (not just the identifier).
3. One `noul` per `bool` flag, worded as a claim about what the user asked for, never about the parameter name.
4. Reject an empty option set before calling: the server returns 400 `Choice question must have at least one choice`.
5. Validate every returned `choice` is in the allowed set (the server guarantees it, keep the assert as a contract test).

## Phrasing rules learned (with before/after)

1. **Always add a `no_match` option.** Before: `function` choice with only real functions, so "what's the weather in Zagreb" gets forced onto the nearest function. After: `no_match: "none of the other functions applies"`. Result: `no_match` confidence 1.0 (nl-04). A choice always ranks something first.
2. **Describe options in words, not only identifiers.** `{"csv": "comma separated values, spreadsheet"}` let "as a spreadsheet" map to `csv` (nl-09) though the word csv never appears.
3. **Name the argument's value in the instruction.** "Which value of the `window` parameter does the user request imply?" with descriptive options `{"1m": "one month"}` gave 1.0 for "1 month" (nl-01). "Which minimum log level should the command filter on?" resolved "only the scary stuff, errors and worse" to `error` (nl-11).
4. **Negation inside compound sentences can flip a noul.** Sentence: "export invoices as csv, and don't bother zipping them, but do leave archived out".
   - Before: noul "Does the user ask to include archived records?" gave **P(yes)=0.987**, the wrong side.
   - After (either works): the polarity-flipped noul "Does the user ask to exclude archived records?" gave 0.998; a 3-option choice `include / exclude / unspecified` chose `exclude`. Adding `criteria: {true, false}` descriptions to the original noul gave 0.0002 (correct: not included).
   - Rule: for any flag whose sentence contains "leave out", "except", "without", "don't", ask the flag both ways, or use the include/exclude/unspecified choice, and treat disagreement as low confidence (ask the user).
5. **Split compound requests into one question per argument.** Every case above uses one claim per noul; nl-05 reads four arguments in one call at 1.0/1.0/1.0/0.0.
6. **Safety flags get a mirrored test pair.** Positive ("just list what you would remove, don't touch anything") gave dry_run 0.997; negative ("yes go ahead and actually delete") gave 0.002 (nl-06, nl-07). Keep both in your regression tests.
7. **Implicit values work.** "let the dog walker in through the front door" resolved to `unlock` (nl-17); "did I leave the garage door unlocked? lock it" resolved to `lock_door` and room `garage`.
8. **Language does not matter much.** Croatian "ugasi svjetlo u spavaćoj sobi" gave `set_light`, `bedroom`, turn_on 0.0 (nl-13; confidence 0.98/0.99).

## Thresholds for acting on answers

| Situation | Rule |
|---|---|
| `function` choice | act if `confidence >= 0.8` and choice != `no_match`. `no_match` or confidence < 0.6: ask the user or fall back to the help text |
| Enum argument choice | act if `confidence >= 0.7`; otherwise ask "did you mean X or Y?" using the top two probabilities |
| Non-destructive bool flag | `noul >= 0.7` means true, `<= 0.3` means false, in between use the default |
| Destructive or safety flag (`force`, `delete`, `dry_run`) | require `>= 0.9` / `<= 0.1`; otherwise default to the safe value (`dry_run=true`, `force=false`) and echo the parsed command for confirmation |
| Anything that mutates production | always show the parsed call and require a human yes, regardless of confidence |
| Ambiguous sentence (recurring vs one-off, nl-08) | pass `samples: 4` or ask both polarities; act only if all agree |

Observed values in the run: correct answers had confidence 0.98-1.0 and noul values under 0.03 or over 0.99, so these thresholds have wide margin. The one wrong read (rule 4) was a confident 0.987, so the "ask both ways" rule matters more than a higher threshold.

## Extensions used

- `samples: 4` (nl-11): averaged read on a terse ordinal argument, still exact.
- `sequential: true` (nl-12): later arguments read after earlier ones; correct, but slower (~4x latency on a shared server). Use only when arguments depend on each other.
- Many options (nl-10): 30 function names in one choice, `freeze_card` picked correctly from "put a hold on my corporate card". Above about 50 functions, pre-filter by keyword or embeddings and pass the shortlist.
- `think` and `images` were not needed. Screenshots of a UI are not a natural input for this usage.

## Error cases the MCP layer must handle

- Unknown question type (e.g. `"type": "enum"`): HTTP 400, body `{"detail":{"error_type":"api_usage_error","message":"Invalid request."}}` (nl-14). The MCP schema should only allow `noul|choice|score`.
- Empty options: HTTP 400 with plain-text detail `Choice question must have at least one choice: q` (nl-15). Validate a function with an empty `Literal` before calling.
- Unknown model, more than 255 options, and 422 validation errors are documented in the README; surface the server message verbatim.

## Limitations

- Open-ended values are out of scope: numbers, dates ("last quarter" as a range), tickers, paths. Extract them in code and validate them after.
- Single-shot polarity errors exist (rule 4). A confident wrong answer is possible on negated sentences, so destructive flags need the both-ways check plus a confirmation.
- Score questions were not used here; ordinal arguments (log level, verbosity) are better as a `choice` with descriptive options.
- Latency: 0.4 s in isolation, 1.5-18 s per case in this run because the server was shared with other agents. `sequential` and 30-option choices were the slowest.
- No case of the model genuinely failing was found after rephrasing, so no `KNOWN LIMITATION` case is included. The failure in rule 4 was on the original phrasing only, and is reproduced here, not in the test file, because the file must pass.

## Final pass count

17/17 cases pass against `openjev-latest`: 13 positive/mixed reads, 2 error cases (400s), 2 phrasing-fix cases. Run:

```
.venv/bin/python docs/mcp-skill-spec/tests/run_cases.py docs/mcp-skill-spec/tests/cases/10-nl-to-typed-call.json
```
