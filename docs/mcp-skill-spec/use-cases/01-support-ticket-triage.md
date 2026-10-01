# Use case 01: Ticket, email and message triage

Test file: `tests/cases/01-support-ticket-triage.json` (20 cases, 20/20 passing against the live MLX server, model `openjev-latest`).

## Purpose
One text in (ticket, email, chat message, transcript). Typed answers out: the owning queue (`choice`), frustration (`score`), flags such as refund, churn, urgent, needs_reply (`noul`). Plain code then routes on the numbers. This is the canonical OpenJev quickstart. It also covers email tray sorting and intent-routing cascades (code / specialist LLM / human).

## When a coding agent should reach for OpenJev
Trigger: the agent is about to write any of these:
- a keyword `if/else` chain or regex router over free text ("if 'refund' in text ...");
- an LLM prompt that returns JSON containing a category, urgency or sentiment;
- a sentiment or "is this angry" heuristic.

Why OpenJev wins: keyword rules fail on paraphrase ("two identical payments hit my card" contains no "refund"/"billing") and on politeness ("Dear team, I would be most grateful ..." while threatening to leave). JSON-from-LLM gives unparseable output and no confidence. OpenJev returns calibrated probabilities from one forward pass (~0.4-1 s for several questions), so the routing code is a threshold on a float.

## Recommended question schema (copy-paste)
```json
{
  "model": "openjev-latest",
  "state": "<ticket subject + body>",
  "questions": {
    "dept": {"type": "choice", "instructions": "Which team should own this ticket?",
      "criteria": {
        "billing": "charges, invoices, refunds, payment methods, plan pricing",
        "technical": "bugs, errors, outages, integrations, performance, login problems",
        "sales": "pre-purchase questions, quotes, upgrades, demos, enterprise pricing",
        "other": "anything else, or too vague to tell"}},
    "frustration": {"type": "score", "instructions": "How frustrated is the customer?",
      "criteria": ["calm or neutral", "somewhat annoyed", "angry or furious"]},
    "urgent": {"type": "noul", "instructions": "Does this need attention today (outage, money lost, deadline, or a blocked customer)?"},
    "refund": {"type": "noul", "instructions": "Is the customer asking for money back (a refund or a credit)?"},
    "churn": {"type": "noul", "instructions": "Is the customer threatening to cancel or leave?"}
  }
}
```
Fan-out of all five questions in one call is fine and preferred over five calls (case triage-08). Intent cascade, live-chat cancellation and email tray variants are in cases triage-09a/b, triage-13 and triage-10a/b (`order_status` / `complaint` / `cancel` / `other`; `action` / `fyi` / `newsletter` / `personal` plus a `reply` noul).

## Phrasing rules learned
1. **Always include an `other` option, and describe it as "anything else, or too vague to tell".** Probe on state `"Please help"`:
   - with `other` ("anything else, or too vague to tell"): `other` = 1.00;
   - without any `other` option: forced pick, `technical` 0.94 with confidence 0.77. A choice cannot abstain, so the confidence is misleading. Before: 3 options, after: 4 options with `other`. (case triage-06)
2. **Ask about facts in the text, one fact per noul.** "Is the customer asking for money back" and "Is the customer threatening to cancel" are separate questions; a compound "refund or cancel?" would blur the two. Refund and churn behave independently (triage-08: refund 0.00, churn 0.00 on an annoyed invoice ticket).
3. **Give `urgent` a concrete definition.** Bare "Is this urgent?" is model-opinion; "needs attention today (outage, money lost, deadline, blocked customer)" scored 0.03 on a missing-VAT-number invoice request and 1.00 on a checkout outage. A ticket with a Friday deadline scored 0.88 (mixed, triage-08), so treat 0.5-0.9 as "soon".
4. **Score levels: name the extremes plainly, keep 3 levels.** `score` is the expected level index (0-based), so frustration is 0.0 to 2.0. Polite-but-furious text scored 2.0 (triage-15) and sarcasm 1.67 (triage-12).
5. **Do not let the department drive the flags.** Route on `dept` only; a furious refund-and-cancel-or-chargeback ticket came out `technical` (0.95) because the root cause was logouts. Decide priority from `frustration`/`refund`/`churn`, not from dept.
6. **Paraphrase stability:** three different wordings of a double charge all gave billing 1.00 and refund >= 0.9999 (triage-07a-c).
7. **Short chat messages work with the same schema.** A lowercase, typo-laden "pls cancel my subscription before it renews tomorrow" routed to `cancel` and churn >= 0.8 with no wording changes (triage-13). Put `cancel` as its own intent option so it is not absorbed by `complaint`.

## Thresholds for acting on answers
| Answer | Act when |
|---|---|
| `dept.choice` | `confidence >= 0.7` route automatically; below that (or `other`) send to a human triage queue |
| `frustration.score` | `>= 1.5` escalate to a senior agent; `<= 0.5` standard SLA |
| `urgent.noul`, `refund.noul`, `churn.noul` | `>= 0.8` yes; `<= 0.2` no; between = ambiguous, human review |
| `reply.noul` (email) | `>= 0.8` put in reply-needed tray |

Observed values were extreme (mostly < 0.01 or > 0.99), so the middle band is rare and worth surfacing.

## Limitations
- Choice confidence is over the options given. It cannot say "none of these" unless you provide `other`.
- `dept` on multi-issue tickets is a single pick (triage-08 asserts only `billing` or `technical`); ask a noul per department if you need multi-label.
- Almost everything was tested in English (one German case, triage-14).
- Case latency varied 0.4 s to 13 s when the shared server was busy; timing is not asserted.
- No KNOWN LIMITATION case: every attempted case passed on first phrasing (no label or wording changes were needed).
- Non-English: one German duplicate-charge ticket routed billing with refund >= 0.8 (triage-14). Treat as a single data point, not coverage of other languages.
- Error handling (unknown model 400, `choice` without `criteria` 422) is not tested here, since it is not triage; it belongs to the API-contract tests. The MCP layer should still validate criteria before sending.

## Extensions exercised
`think: true` on the churn/refund ticket gave the same answers (triage-11, refund 1.00, churn 1.00; slower, use only when a decision is borderline).

## Final result
20/20 cases pass (`run_cases.py tests/cases/01-support-ticket-triage.json`).
