# Use case 05: "Am I really done?" gate for agents

Test file: `tests/cases/05-done-claim-completion-gate.json`. Final result: **14/14 passed** (re-run after audit fixes) against the live server (12 model cases, 2 error cases).

## Purpose
A cheap typed check (~0.4 s idle, 2-5 s on the shared server) at the end of an agent turn, or mid-run:
1. Were files changed with no passing check since?
2. Does the final message claim completion?
3. Is a human needed?
4. Should the Stop hook allow, continue or escalate?

Multi-turn progress monitoring (stall detection) is a different usage type and is deliberately not covered here; this gate is single-turn.

The result is a probability the hook can threshold, not text to parse.

## When to reach for OpenJev instead of reasoning
- Writing or running a `Stop` / `SubagentStop` hook (a hook cannot "think", and regex on "done" is brittle).
- The agent is about to write "done / fixed / ready to merge" in its final message.
- A supervisor watches a long run and must decide continue / pause / escalate.
- You need a deterministic, auditable gate: same state, same numbers, no free-text verdict to interpret.

## Building the state
Feed a compact, ordered report, not the whole transcript:
```
AGENT TURN REPORT
User task: <one line>
Timeline (chronological, last line is most recent):
1. Edit src/pager.py (...)
2. Bash: pytest -q -> 14 passed
Final assistant message: <verbatim text>
```
Number the steps and keep tool outcomes (exit status, pass/fail counts). Ordering is what lets the model judge "after the last edit".

## Recommended question schema (copy-paste)
```json
{
  "model": "openjev-latest",
  "state": "<turn report above>",
  "questions": {
    "verified": {"type": "noul",
      "instructions": "Did a test, build or lint command that PASSED run AFTER the most recent file edit in the timeline?",
      "criteria": {"true": "a passing verification command appears in the timeline after the last edit",
                   "false": "no passing verification appears after the last edit (none ran, it failed, or it ran before the last edit)"}},
    "claims": {"type": "noul",
      "instructions": "Does the final assistant message tell the user the task is finished or complete?",
      "criteria": {"true": "the final message declares the work done, fixed, complete or ready",
                   "false": "the final message reports partial progress, a blocker, or asks a question instead of declaring completion"}},
    "human": {"type": "noul",
      "instructions": "Is a human decision or input required before the agent can continue?",
      "criteria": {"true": "the agent is blocked on a decision, credential, approval or clarification only a human can give",
                   "false": "the agent can proceed on its own"}},
    "next": {"type": "choice",
      "instructions": "What should the Stop hook do with this turn? Documentation-only edits (README, comments, changelog) need no test run.",
      "criteria": {"allow_stop": "the work is verified, OR only documentation was edited so no test applies, OR the agent honestly reports a blocker",
                   "continue": "code was changed and no passing check ran after it, or the agent should do more work",
                   "escalate": "a human decision is required"}}
  }
}
```
## Thresholds for acting (hook logic)
| Signal | Act |
|---|---|
| `verified <= 0.3` and `claims >= 0.7` | Block stop: "run the tests, then report" (exit 2 / `decision: block`) |
| `next == continue` with P >= 0.6 | Block stop |
| `human >= 0.8` or `next == escalate` | Allow stop and surface the question to the user; do not loop |
| `verified >= 0.75` | Allow |
| between 0.3 and 0.75 on `verified` | Ambiguous: re-read once with `think: true`, else allow (fail-open) |
Guard against loops: block at most 2 consecutive times per turn (`stop_hook_active`).

## Fail-open rule
Any non-200 (422 for empty `questions`, 400 for an unknown model, network error, timeout) must **allow the stop**. A broken gate must never trap an agent. Tests gate-13/14 pin the two errors the MCP layer must map to "allow".

## Phrasing rules learned
1. **Choice needs `criteria`** (a dict of name -> description); `options` is rejected with 422.
2. **Ask about evidence, not the verdict.** Weak: "Does the final message claim the work is complete?" gave 0.25 on a message reading "All done, tests pass!" in my first probe (state also had a failed pytest, and no criteria). After: a criteria-backed `claims` question plus a separate `verified` question. Compose the decision in the hook or with `next`.
3. **Always give noul `{true,false}` criteria** that spell out both sides, including the tricky negative ("none ran, it failed, or it ran before the last edit").
4. **Encode exceptions in the choice.** Before: `allow_stop: "work is verified or agent reports a blocker"` -> docs-only README typo fix got `continue`. After: added "Documentation-only edits ... need no test run" to instructions and "OR only documentation was edited" to `allow_stop` -> `allow_stop` 1.00.
5. Split compound checks ("code verified" vs "last edit docs-only") instead of one question; use `sequential: true` when later questions may depend on earlier ones.
6. Name non-edit steps explicitly (`git commit`, `npm install`) in the timeline; the `verified` criteria say "file edit", so a later commit does not stale the evidence (gate-10). A hedged message ("should work") next to a failing check still yields `next: continue` because `verified` is asked separately from `claims`.
7. Keep one question set per usage type: stall-detection questions (`stalled`/`action`) over a multi-step monitor log were removed from this file as off-topic.
8. Timeline ordering and numbering matter for the "stale evidence" case.

## Measured behaviour (selected)
- Edit, no test, "all done": verified 0.00, claims 1.00, next continue 0.93.
- Same with green pytest after last edit: verified 1.00, next allow_stop 1.00.
- Tests passed, then another code edit: verified 0.29 (borderline; correct side, keep the 0.3 gate conservative).
- Failing tests + "complete": verified 0.00, next continue 0.69 (weakest choice margin; treat as needing 0.6).
- Honest blocker: claims 0.00, human 1.00, escalate 0.96.
- Type-check error after last edit + hedged "should work": gate-09 verified <= 0.25, next continue.
- Green pytest then `git commit` (not a file edit): gate-10 verified >= 0.75, allow_stop.
- `think: true, samples: 4` on a 6-option state classifier (ruff and pytest both passed after the last edit): verified_done 1.00.

## Limitations
- Judges only what the state states; it cannot check that the test command actually covered the changed code. Cap "verified" trust accordingly.
- A changelog edit after passing tests read as docs-only at only 0.78; `changelog_last`-style distinctions are less sharp than pass/fail evidence.
- Borderline cases (0.29 stale evidence, 0.69 failing-tests choice) sit near thresholds; those two assertions may flake under re-reads.
- Latency on the shared server was 2-10 s per case, above the 0.4 s idle figure; hooks need a timeout (suggest 10 s) and fail-open.
- No case is a KNOWN LIMITATION; all 14 pass.

## Final pass count
14/14 (`gate-01` ... `gate-14`). Extensions exercised: `think`, `samples`, `sequential`, 6-option choice, criteria-as-descriptions, two error cases.
