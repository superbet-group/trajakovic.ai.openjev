# 16. Claim, citation and literature verification

Case file: `tests/cases/16-claim-grounding-check.json` (18 cases). Result against the live server: **18 of 18 pass** on the full-file rerun after the audit fixes (cases 01 and 02 were rewritten as non-trivial quote checks, and the 422 error case was moved to its numbered position 15). Every rewritten draft passed on the first run. There is no KNOWN LIMITATION case because none failed.

## Purpose
An agent writes something derived from source material (a changelog bullet, a citation, a summary, an abstract screen) and must check that the claim is actually backed by the source. Code does the exact checks (is this quote a substring?), OpenJev does the semantic check (does the source support, contradict, or stay silent?) and returns a probability the agent can gate on.

## When a coding agent should reach for OpenJev instead of natural language
Trigger: the agent just wrote, or is about to write, a citation, summary, release note or "according to X" statement from source material.
- Before publishing a changelog or release note: one `choice` per bullet against the diff or commit list.
- After writing a citation or paraphrase of a paper, doc page or RFC: check the claim against the cited passage.
- After summarizing an incident, ticket or log: one `noul` groundedness gate per summary (split compound summaries into single claims).
- Screening many abstracts or docs against inclusion/exclusion criteria: one `noul` per criterion, aggregated in code.
Do not do "re-read and reason whether this is supported" in prose. A verdict with a calibrated probability is cheaper, batchable and testable.

Order of operations (code first):
1. If the claim contains a quoted string, run a substring or normalized-whitespace match in code. No match means the quote is fabricated or altered; stop, do not ask the model.
2. Only for paraphrases, or quotes that matched but need context, call OpenJev.
3. Numbers, versions, dates and identifiers in the claim: also grep them in the source in code; the model is a second net, not the first.

## Recommended question schemas

Three-way support check (the default; copy as is, change only the instructions):
```json
{"model":"openjev-latest",
 "state":"CHANGELOG BULLET: \"Default request timeout increased from 30s to 60s.\"\n\nDIFF (src/http/client.py):\n-    def __init__(self, base_url, timeout=30):\n+    def __init__(self, base_url, timeout=45, retries=3):",
 "questions":{"v":{"type":"choice",
  "instructions":"Judging only from the DIFF, does it support, contradict, or say nothing about the CHANGELOG BULLET?",
  "criteria":{"supports":"the source text states or clearly entails the claim",
              "contradicts":"the source text states something that conflicts with the claim",
              "says_nothing":"the source text does not address the claim at all"}}}}
```
Groundedness gate for a summary (one summary, all statements):
```json
{"grounded":{"type":"noul",
 "instructions":"Is every factual statement in the SUMMARY TO CHECK supported by the SOURCE? Consider numbers, causes and durations.",
 "criteria":{"true":"all statements are supported by the source",
             "false":"at least one statement is unsupported or contradicts the source"}}}
```
Overstatement guard (strength of wording):
```json
{"v":{"type":"noul",
 "instructions":"Is the CLAIM fully supported by the SOURCE, without any stronger wording than the source uses?",
 "criteria":{"true":"the source states this with the same strength",
             "false":"the claim is stronger than the source or unsupported"}}}
```
Screening (one noul per criterion, in one request, combine with code):
```json
{"rct":{"type":"noul","instructions":"Is the study a randomized controlled trial?",
        "criteria":{"true":"the paper describes random allocation to groups","false":"the paper is observational, non-randomized, or not a trial"}},
 "adults":{"type":"noul","instructions":"Are the participants adults?",
        "criteria":{"true":"participants are adults","false":"participants are children or animals or not described"}}}
```
Bullets batch: put all bullets in one `state`, label them A, B, C, and ask one identical `choice` per label (case claim-14).

## Phrasing rules learned
All rules are from results in this suite; there were no failing drafts, so the "before" forms are what the rules guard against, not observed failures.
1. Name the two texts with fixed labels (`CLAIM`/`CHANGELOG BULLET`, `SOURCE`/`DIFF`/`ABSTRACT`) and say "judging only from the SOURCE" in `instructions`. This keeps world knowledge out of the verdict. Before: "Is the claim true?" (asks about the world). After: "does the DIFF support, contradict, or say nothing about the BULLET?"
2. Use the three-way choice, not a yes/no noul, when you must tell "wrong" from "unaddressed". `says_nothing` scored 1.00 on both unrelated-bullet and unmeasured-outcome cases; a boolean would have collapsed them into "not supported".
3. Describe each choice as a positive condition about the source ("states something that conflicts with the claim"), not as the negation of another option.
4. For exact-quote checks use "Ignoring line breaks, extra spaces and markdown formatting marks, ... word for word, with no changed, added or dropped words or numbers". The audit-hardened cases passed: a quote present in a hard-wrapped, bold-marked excerpt is accepted (case 01), and a near-miss quote with a single method swapped (DELETE to POST) is rejected (case 02). Naming what to ignore (formatting) and what not to ignore (any single token) is the key; an unqualified "word for word" would risk rejecting reflowed text.
5. For summaries, put the failure modes in `instructions` ("Consider numbers, causes and durations"). A swapped number (22 to 2 minutes) was caught with this wording.
6. Compound claims: the "every statement supported" noul caught a half-supported summary, but for reliable per-claim reporting split the summary into single claims in code and run one question each.
7. Overstatement ("usually" vs "always") is only caught when the criteria say "without any stronger wording than the source uses". Ask for it explicitly.
8. Multi-question requests work: four screening criteria and three bullets in one request all resolved correctly. Use `think: 256, samples: 2` only when a single question sits in the 0.4 to 0.7 band.

## Thresholds for acting
- `choice` verdicts: act on `supports` at P >= 0.8; treat `contradicts` at P >= 0.6 as a blocking finding to show the human with the quoted source; `says_nothing` at P >= 0.6 means "uncited claim, add a source or delete". Observed on clear cases: 0.97 to 1.00.
- groundedness noul: publish at >= 0.85; block at <= 0.3; between, split the summary into single claims and re-run.
- verbatim noul: accept at >= 0.85, treat <= 0.1 as fabricated. Still gate on the code substring check first.
- screening noul: include only if every inclusion criterion >= 0.85 and every exclusion criterion <= 0.15; anything else goes to a human list ("maybe").
- `confidence` below about 0.3: distribution is flat, do not act.

## Limitations
- The model judges the text you give it. If the relevant passage is not in `state`, it will say `says_nothing`; that is a retrieval problem, not a verdict on the claim. Include the full cited passage or diff hunk.
- Long sources dilute attention; check one claim against one focused passage, not a whole paper.
- It does not verify that a URL, DOI or reference exists; only whether given text supports a claim.
- Numeric equality was reliable in these tests (swap of 22 to 2, 60 vs 45), but keep code-side grep for numbers as the first check.
- Latency on the shared server ranged 2 to 13 s per request rather than 0.4 s; use generous timeouts and sequential calls.
- Validation error: a `choice` question without `criteria` returns HTTP 422 (case claim-15). The MCP layer should validate locally and surface the error `detail`.

## Test inventory (18 cases)
01 quote present in a hard-wrapped, markdown-bold excerpt (formatting-normalized match); 02 near-miss quote with one method swapped; 03 bullet supported by diff; 04 bullet contradicted (wrong number); 05 bullet contradicted (removed vs added); 06 silent source says_nothing; 07 citation supported; 08 citation contradicted (non-significant result); 09 citation about an unmeasured outcome says_nothing; 10 four-criterion inclusion screen (all true); 11 exclusion screen, animal study; 12 faithful summary grounded; 13 summary with invented cause and duration; 14 three bullets batched with `think: 256, samples: 2`; 15 error case, choice without criteria (422); 16 compound summary, half supported; 17 overstated scope ("always" vs "usually"); 18 summary with swapped number.

Final pass count: 18/18.
