---
name: openjev-code-checks
description: Use when checking code, diffs, commit messages, docs, LLM replies, summaries or citations against a semantic rule rather than a syntactic one. Triggers: "does this swallow exceptions", "is there a hardcoded secret", "does the commit message match the diff", "does the comment contradict the code", "is this review comment a real bug introduced by the diff", "is the changelog/release note/summary supported by the source", "is this quote verbatim", writing a pytest/eval/CI gate over LLM output (leaks, groundedness, correctness, refusal), LLM-as-judge, comparing two prompts or models (A/B), enforcing an AGENTS.md or CONTRIBUTING rule no linter covers.
---

# Semantic checks on code, diffs, replies and claims

Precedence: parsers, linters, AST, type checkers, regex and substring checks first (unused imports,
line length, exact quote match, number grep). OpenJev for the semantic remainder. Never "re-read and
reason whether it is supported" in prose.

## Semantic lint (use case 06, 20/21)
One claim per noul, asked per function/hunk/message. Copy-paste:
```json
{"swallows": {"type": "noul", "instructions": "Does this function silently swallow an exception, meaning it catches an error and neither logs it, re-raises it, nor reports it to the caller?"},
 "secret": {"type": "noul", "instructions": "Does the added code contain a real, working-looking credential written as a string literal? A placeholder such as YOUR_API_KEY_HERE, <token>, xxx or changeme is not a real credential."},
 "mismatch": {"type": "noul", "instructions": "Does the commit message describe a change that is NOT present in the diff?"}}
```
State for commit checks: `"Commit message:\n<msg>\n\nStaged diff:\n<git diff --staged>"`.
Thresholds: secrets block at >= 0.9 after re-asking that question alone; swallow/mismatch warn at
>= 0.7 (use `samples: 4` before failing anything); style scores never block. Ask a blocking claim
in its own request (sibling interference: 0.996 alone -> 0.33 co-asked).

## Review-finding filter (use case 07)
State = diff + `REVIEW FINDING: ...`. Post at >= 0.8, collapse 0.5-0.8, drop < 0.5:
```json
{"real": {"type": "noul", "instructions": "Is the review finding a real bug that is INTRODUCED BY THE ADDED LINES of this diff (lines starting with +)? Pre-existing code and style nits do not count.",
  "criteria": {"true": "The added lines create a concrete defect that will misbehave at runtime", "false": "Style nit, speculation, already-handled case, or problem in unchanged code"}}}
```

## Judge and test assertions (use case 12, 14/14)
Label the state (`USER MESSAGE:`, `ASSISTANT REPLY UNDER TEST:`, `CONTEXT DOC:`). In pytest:
`assert noul(leak) < 0.05` (strict) with a known-bad example in the same suite that must score high.
```json
{"grounded": {"type": "noul", "instructions": "Is every factual claim in the ANSWER supported by the CONTEXT DOC?",
  "criteria": {"true": "all claims are supported by the context", "false": "at least one claim is not supported or contradicts the context"}}}
```
Pairwise: `judge_pairwise` runs A/B in both slot orders; accept a winner only if it wins both at
p >= 0.8, else report a tie/position bias. Rubric means need >= 20 cases; gate on a drop >= 0.3.

## Claim grounding (use case 16, 18/18)
1. Quote? substring/normalised match in code first; no match = fabricated, stop.
2. Numbers/versions/dates? grep them in the source in code.
3. Then the three-way read, one claim per question:
```json
{"v": {"type": "choice", "instructions": "Judging only from the DIFF, does it support, contradict, or say nothing about the CHANGELOG BULLET?",
  "criteria": {"supports": "the source text states or clearly entails the claim", "contradicts": "the source text states something that conflicts with the claim", "says_nothing": "the source text does not address the claim at all"}}}
```
Act: supports >= 0.8 publish; contradicts >= 0.6 block and show the source; says_nothing >= 0.6
"uncited claim, add a source or delete". Overstatement needs "without any stronger wording than the
source uses".

## Worked example
Changelog bullet "Default request timeout increased from 30s to 60s." vs a diff setting
`timeout=45`: `claim_check` -> contradicts 0.9968. Fix the bullet to 45s before publishing.
