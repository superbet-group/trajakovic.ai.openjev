# Usage 14: Grep by meaning, file relevance, context pruning

Test file: `tests/cases/14-semantic-grep-relevance.json` (14 cases, all semantic-grep/relevance/pruning). Final result against the live server: **14/14 passed** (after one rephrase, recorded below).

## Purpose
Replace brittle regexes and "read everything and decide" with one typed read per item. Each log line, file, diff hunk, code snippet or stale tool result gets its own `noul` question. The calibrated P(yes) is thresholded to filter, open, drop or fail a shell gate. A `choice` over tagged line ids picks the single best line. An `exists` noul says whether anything relevant is present at all.

## When a coding agent should reach for OpenJev instead of reasoning in prose
- About to `Read` many files (5 or more) to find the relevant ones: score them first, open only those above 0.6.
- Paging through a long log, or `tail -f` filtered to real errors, or a grep whose regex would need alternations to cover variants (`except.*pass` vs bare `except: return None`).
- Reviewing a diff and wanting only the hunks that touch a concern (auth, migrations, public API).
- Compacting context: deciding which old tool results are still needed.
- Needing a gate: "fail CI if any line is a real error" (exit code from thresholded noul).
Do not use it to extract text (it returns probabilities, not spans). Use it to decide which items to keep; then read the kept ones normally.

## Recommended question schemas

Per-item relevance (one question per item id, all in one request; state holds the task plus the numbered items):
```json
{"model":"openjev-latest",
 "state":"TASK: <what you are doing>\n\nCANDIDATE FILES (path: first lines):\nF1 <path>: <head>\nF2 <path>: <head>",
 "questions":{
  "F1":{"type":"noul",
        "instructions":"Would a developer working on the TASK need to open and read file F1 to complete it?",
        "criteria":{"true":"file is likely part of the code path involved","false":"file is unrelated to the task"}},
  "F2":{"...":"same shape, id swapped"}}}
```
Log-line gate: same shape, instruction "Is log line L4 a real error an on-call engineer must act on (a failure, crash or data loss), as opposed to routine, benign or expected output?".

Best line plus exists (always send both; add a `none` option to the choice):
```json
{"model":"openjev-latest",
 "state":"TASK: <what to find>\nTAGGED LINES:\n[a1] ...\n[a2] ...",
 "questions":{
  "exists":{"type":"noul","instructions":"Does any tagged line show <target>?",
            "criteria":{"true":"at least one line shows it","false":"no line shows it"}},
  "line":{"type":"choice","instructions":"Which tagged line shows <target>? Choose none if no line does.",
          "criteria":{"a1":"<short gloss>","a2":"<short gloss>","none":"no line shows <target>"}}}}
```
Graded relevance (when you need ranking, not just a cut): `score` with 5 described levels, e.g. `["0 unrelated","1 same area but does not answer","2 mentions in passing","3 contains part of the logic","4 is the primary implementation"]`. Score is 0-indexed; take `score >= 2.5` as relevant.

Context pruning: `noul` per tool result, "For the CURRENT GOAL, must tool result T3 stay in the context (would dropping it hurt the next steps)?" with the goal stated first in the state.

## Thresholds for acting on answers (observed values in parentheses)
| Decision | Rule |
|---|---|
| Open file / keep hunk / keep line | noul >= 0.6 (positives measured 0.77 to 1.00) |
| Drop | noul <= 0.2 (negatives measured 0.00 to 0.01) |
| Grey zone 0.2 to 0.6 | keep by default (cheap) or re-read with `samples:4`; on pruning, keep |
| Shell gate (`exit 1` if any error) | any line noul >= 0.7 |
| "Nothing found" | exists <= 0.15 and choice == `none` (measured 0.0, 1.0 confidence) |
| Best line | choice with confidence >= 0.5, and only if exists >= 0.8 |
| Graded score | relevant at score >= 2.5 (measured 3.1 to 3.5 for true positives, 0.01 for negatives) |
Results were near-binary; mid values (0.77 for a mild "keep" of a grep with no matches) mark the genuinely marginal items, so the grey-zone handling matters.

## Phrasing rules learned
1. **One item, one question, id in the instruction.** Put the shared task and all items in `state`, and refer to the item id in each instruction. Tested with 24 choice options, 7 noul lines and 160 lines of noise; all correct.
2. **Order invariance holds.** Reversing the file order gave identical values (case `files-02`). Batch freely, no need to shuffle.
3. **Noise does not degrade.** 160 irrelevant lines around one needle: exists 1.0, needle 1.0.
4. **Always pair a choice with `exists` and a `none` option.** A choice alone must pick something; with `none` it returned `none` (confidence 1.0) on an irrelevant log.
5. **Describe the true class by what the item does, and list the tricky variants.** Failing case (`semgrep-01`, C4 = bare `except: return None`, noul 0.63 < 0.7):
   - Before: `"Does snippet C4 silently swallow an error, hiding the failure from callers and logs?"` / true `"the error is discarded or hidden without logging, re-raising or reporting it"`.
   - After: `"Does the except block of snippet C4 catch the error and carry on without logging, re-raising or reporting it, so the failure vanishes?"` / true `"yes: nothing is logged, raised or reported (returning None or a default, pass and ... all count)"`. C4 rose to 1.0; C2 and C5 stayed 0.0. Cause: the first phrasing did not say that returning a default counts. Name the borderline variants in the true criterion.
6. **State the boundary of "relevant" in the criteria.** Log lines used "real failure needing action" vs "routine, benign, informational or an expected condition"; the expected card_declined ERROR (L3) correctly scored 0.0 and the retry-succeeded WARN 0.0. Tell the model what to ignore.
7. **Use `score` with described levels when ranking; keep `noul` for cut decisions.** Criteria as descriptions worked (0-indexed legend).
8. **Semantic grep for secrets: define the true class by shape, and exclude mentions.** Criteria said "a real-looking secret string is embedded in the code" vs "read from env or a vault, or only mentioned in a comment"; a comment about rotation scored 0.0 and a bearer-token dict (no `password` keyword) scored 1.0. Worked first try.
9. **Extensions.** `samples:4` on a tangential file returned 0.0 (confident no). `sequential:true` plus `think:64` over 4 questions still gave separated answers; neither is needed for routine filtering, so keep default reads (about 0.4 s, though a batch of 6 to 7 questions took 4 to 16 s on the shared server under load) and use `samples:4` only for grey-zone items.

## MCP-layer requirements exercised
- Empty choice options (for example after you filtered the id list to nothing) is a 400 `Choice question must have at least one choice`. The MCP tool should short-circuit "no candidates" locally as exists=false rather than call the server. This error path is covered by the generic error-handling use case, not by a case here (a former `err-01` was removed from this file as tangential to semantic grep).
- A single-option choice is answered locally with no model call (1 ms), so the tool need not special-case it.
- Cap batch size sensibly; latency grows with question count (roughly 1 to 2 s per 6 to 7 questions here). Choice limit is 255 options, score 10 levels.

## Shell-gating sketch
Tool `openjev_filter_lines(lines, question, threshold=0.7)` returns kept ids and the max probability; the CLI wrapper exits 1 when any line is kept, 0 when none, 2 on server error. `tail -f app.log | openjev-filter --gt 0.7 "real error needing action"` batches N lines per request.

## Limitations
- Only the state is read. Give each file's first lines or a symbol summary, not just the path; a path alone is ambiguous (`redirect_guard.py` needed its docstring).
- Probabilities are saturated (mostly 0.00 or 1.00); do not treat them as fine-grained ranks. Use `score` for ranking.
- It reads what it is given: a relevant item missing from the state cannot be found. A bare `except` inside a long snippet needs the surrounding lines included.
- Latency is per question, not free; for very large corpora pre-filter with cheap `rg` and use OpenJev to judge the survivors.
- Model can be wrong on marginal items (0.77 on a no-match grep as "keep"), hence the keep-by-default grey zone when pruning.
- No known-limitation case was needed: all 14 pass.
- `prune-02` asserts T6 (a `grep round` with no matches) only as `noul >= 0.4` (measured 0.77): it is mild negative evidence, so the test checks it is not dropped rather than that it is confidently kept.

## Test inventory (14)
log-01 real errors only; files-01 relevance threshold; files-02 order invariance; choice-01 root-cause line + exists; choice-02 nothing relevant (exists low, choice none); prune-01 large noise; hunks-01 auth-relevant diff hunks; prune-02 stale tool results; score-01 graded relevance; semgrep-01 swallowed exceptions (rephrased); choice-03 24 options; ext-01 samples:4 borderline; ext-02 sequential + think batch; semgrep-02 hardcoded secret literals (D1 and D3 in different shapes, measured 1.0; env, vault and comment mentions 0.0).

**Final pass count: 14/14.**
