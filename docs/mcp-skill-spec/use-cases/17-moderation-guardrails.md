# Use case 17: Input/output guardrails and moderation with an "uncertain" outcome

Test file: `tests/cases/17-moderation-guardrails.json` (17 cases, 17/17 passing against the live MLX server, openjev-latest).

## Purpose
Put a blocking yes/no plus a category read in front of an agent (inbound messages, tickets, comments, scraped text) or behind a model (LLM drafts before posting). OpenJev returns calibrated probabilities, so the code can produce three outcomes rather than two: allow, block, or uncertain (route to human review). Typical tasks: block phishing/spam/harassment before an agent handles a message; screen LLM replies before posting; send borderline posts to a review queue.

## When to reach for OpenJev instead of reasoning in prose
- The agent is adding a moderation, safety or PII check in front of or behind a model. Do not write "if the message looks abusive then...". Ask the question and branch on the number.
- Any time the check must be repeatable and testable: same state, same numbers.
- Not for hard rules. Run deterministic checks first (blocklists of known domains, regexes for card numbers and API keys, size limits). Use OpenJev for the semantic remainder.

## Recommended question schema
State layout: label the channel and direction, then the raw text. Include sender/subject when they carry signal (lookalike domains).

```
Inbound email to support@shop.example
From: ...
Subject: ...

<body>
```

```json
{
  "model": "openjev-latest",
  "state": "Inbound email to support@shop.example\nFrom: security-team@paypa1-verify.example\nSubject: URGENT: Your account will be closed\n\nVerify your password now at http://paypa1-verify.example/login",
  "questions": {
    "block": {
      "type": "noul",
      "instructions": "Should this message be blocked because it is phishing, scam, spam, harassment or hate directed at a person, or a threat, rather than ordinary legitimate communication?",
      "criteria": {
        "true": "Phishing, scam, spam, harassment, hate or threats: block it",
        "false": "Ordinary legitimate communication, including blunt criticism or venting that does not target a person"
      }
    },
    "category": {
      "type": "choice",
      "instructions": "Which moderation category best fits this message?",
      "criteria": {
        "phishing": "tries to trick the reader into revealing credentials, paying, or clicking a deceptive link",
        "spam": "unsolicited bulk promotion or advertising",
        "harassment": "insults, threats or abuse aimed at a person or group",
        "none": "ordinary legitimate content"
      }
    },
    "harm": {
      "type": "score",
      "instructions": "How harmful would it be to let this message through to its recipient?",
      "criteria": ["harmless", "mildly unpleasant or annoying", "clearly abusive, deceptive or unwanted", "dangerous: credential theft, financial fraud or credible threat"]
    }
  }
}
```
Always keep a `none` option in the category choice; without it, every message is forced into a harmful bucket. Send only the questions you need: `block` alone for the hot path, add `category` when routing, add `harm` when prioritizing the review queue. Score levels here are 0..3. Adapt category names to the product (add `pii`, `self_harm`, `hate`, ...); keep one-line descriptions that say what the text does.

## The "uncertain" outcome (thresholds for acting)
OpenJev does not output "uncertain"; the caller derives it. Observed values are strongly bimodal: clear positives gave block >= 0.97, clear negatives <= 0.13 (most 0.00). So a wide uncertain band is rarely populated; make it cheap.
```
p = answers.block.noul
if p >= 0.85:              BLOCK   (category = answers.category.choice)
elif p <= 0.15:            ALLOW
else:                      UNCERTAIN -> human review queue
# category-level uncertainty, independent of p:
if p >= 0.85 and answers.category.confidence < 0.6:  BLOCK, label category "unclear"
```
- Observed: prize-scam SMS (mod-16) gave block 1.0 but category split phishing 0.32 / spam 0.68. The split is the honest signal: block, but do not auto-label with confidence. Threshold `confidence < 0.6` on the choice flags it.
- Strict profile (financial, children): block at p >= 0.5, allow only at p <= 0.05.
- Lenient profile (community chat): block at p >= 0.95, review 0.3 to 0.95.
- Borderline flips: for messages landing in the uncertain band, re-read with `samples: 3` (mod-12, mod-13) or `think: 256`; keep the more cautious of the results, or send to review if they disagree. Do this only on the band, never on every message (about 4 to 11 s on the shared server versus about 0.4 s for a plain read).
- Fail-open behind a deadline: wrap the call in a timeout (recommend 2 s inbound, 5 s outbound). On timeout, 4xx/5xx or 429/529 return `UNCERTAIN` with reason `guardrail_unavailable`. Policy choice per surface: input filters on low-risk channels may fail open (allow and log), but LLM output going to customers and anything credential-adjacent should fail closed (hold for review). Make it a config flag, default closed for outbound. Cases mod-14 and mod-15 pin the 400 shapes: an unknown model gives 400 "Unknown model"; a choice with empty criteria gives 400. The MCP layer must validate model and options before calling and translate errors into `UNCERTAIN`.

## Phrasing rules learned
No question needed rewording; all 17 passed on the first version. Rules that the design relies on (each verified by a case):
1. Put the negative class in the `criteria.false` text. "Ordinary legitimate communication, including blunt criticism or venting that does not target a person" is what keeps code-review harshness (mod-05, block 0.00), angry product reviews (mod-06) and teammate banter with insults (mod-17) unblocked. Without an explicit carve-out, sharp-toned text is the usual false-positive source; if you see over-blocking, add the specific carve-out rather than raising the threshold.
2. Define block in terms of who is targeted and what the text does (phishing, abuse aimed at a person), not "is this bad". Tone alone is not a violation.
3. One noul for the decision, one choice for the label, one score for prioritization. Do not ask a single choice to both decide and label; the noul gives the calibrated block probability, the choice gives routing.
4. Screen LLM output with the same schema; only the state header changes ("Draft reply written by an LLM support agent, about to be posted to the customer"). Abusive drafts blocked at 1.0 (mod-07), polite refund reply at 0.00 (mod-08).
5. Jailbreak in the state: text addressed to "the moderation model" saying it is pre-approved did not sway the read (mod-11: block >= 0.7 asserted, phishing chosen at 0.99). Still, frame the state as "User-submitted message to be moderated:" and quote the content, so the boundary is explicit. Do not rely on this alone: also run the injection screen from use case 04 for agent-facing inputs.
6. Non-English works: German phishing blocked (mod-09), Croatian order query allowed (mod-10). Keep the question and criteria in English; the state may be any language.

## Extensions and edges used
- `samples: 3` (mod-12) and `think: 256` + `samples: 3` (mod-13) for the borderline band.
- Multi-question requests (block + category + harm) in a single call.
- Error cases mod-14 (unknown model, 400) and mod-15 (empty choice options, 400).

## Limitations
- Bimodal output: the model is rarely mid-range on clear-cut text, so the uncertain band mostly captures genuinely mixed content and slightly odd formats. Do not expect a smooth dial; log the band's size after rollout and tune.
- Category ambiguity (spam vs phishing) shows up in the choice probabilities, not in the block noul. Read both.
- The model sees text only. It does not know whether a URL is on a blocklist, whether the sender is allowlisted, or whether an attachment is malicious. Combine with deterministic checks.
- Sarcasm and in-group banter are judged from wording; relationship context (mod-17 was told "teammate", "practice") should be put in the state if it matters.
- Not audited for protected-class hate speech, self-harm, or legal categories in this suite; test those categories with domain examples before relying on them.
- Latency on the shared server ranged 2.4 to 12 s per case in this run under load (typical idle read is about 0.4 s). Budget the deadline accordingly.
- MLX serves one read at a time; keep question sets small in hooks and do not fan out.

## Final result
17/17 cases pass (15 judgement cases, 2 error cases; positives: phishing, spam, harassment, LLM abuse, German phishing, jailbreak-wrapped phishing, prize scam; negatives: password-reset query, harsh code review, angry product review, polite LLM reply, Croatian query, recruiter outreach, banter, political snark). No phrasing rewrites were needed and no KNOWN LIMITATION cases.
