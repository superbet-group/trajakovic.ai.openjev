---
name: openjev-code-checks
description: "Checks code, diffs, commit messages, docs, LLM replies, summaries and citations against semantic rules with OpenJev reads and recipes (semantic_lint, review_finding_filter, claim_check, judge_assert, judge_pairwise). Use when a rule no linter covers must be enforced: swallowed exceptions, hardcoded secrets, commit message vs diff mismatch, comment contradicting code, review finding introduced by the diff, changelog or summary supported by its source, verbatim quotes, or a pytest, eval or CI gate over LLM output (leaks, groundedness, refusal, A/B comparison)."
---

# Semantic checks on code, diffs, replies and claims

Tools live on the `openjev` MCP server as `mcp__openjev__<tool>` (with the openjev-mcp plugin: `mcp__plugin_openjev-mcp_openjev__<tool>`). If they are missing, load skill `openjev-data-prep` (Connect) and do not start or restart OpenJev or its MCP server without the user's go-ahead; give the user the command.

Precedence: parsers, linters, AST, type checkers, regex and substring checks first (unused imports, line length, exact quote match, number grep). OpenJev handles the semantic remainder through `mcp__openjev__recipe` or `mcp__openjev__ask`. Never "re-read and reason whether it is supported" in prose.

## Checklist

```
- [ ] Run the deterministic check first (quote substring, number grep, linter)
- [ ] One claim per question; ask a blocking claim in its own request
- [ ] Label every state section (COMMIT MESSAGE, DIFF, CONTEXT DOC, ...)
- [ ] dry_run a new recipe call, then call it; act on `decision` or the stated thresholds
- [ ] Re-ask with options.samples 4 before failing anything on a borderline score
```

## Semantic lint: swallowed errors, secrets, commit vs diff

The `semantic_lint` recipe holds one check per run so a blocking check is asked alone. `profile` is one of `strict` (swallows, secret, commit_mismatch, comment_contradicts, prose_slop, naming), `secret`, `commit_mismatch`, `comment_contradicts`, `prose_slop`, `naming`. For commit checks the text is `Commit message:` then the message, a blank line, `Staged diff:` and the diff.

<!-- openjev-call: recipe -->
```json
{"recipe": "semantic_lint", "inputs": {"text": "Commit message:\nFix retry backoff\n\nStaged diff:\n- delay = 1\n+ delay = 2 ** attempt"}, "profile": "commit_mismatch"}
```

Thresholds: secrets block at 0.9 or more after re-asking that question alone; swallow and mismatch warn at 0.7 or more (use `options.samples: 4` first); style scores never block. Blocking claims co-asked with siblings drift (0.996 alone fell to 0.33), so keep them single.

To write your own rule, ask one noul per claim:

<!-- openjev-call: ask -->
```json
{"state": "FUNCTION:\ndef load(path):\n    try:\n        return open(path).read()\n    except OSError:\n        return None", "questions": {"swallows": {"type": "noul", "instructions": "Does this function silently swallow an exception, meaning it catches an error and neither logs it, re-raises it, nor reports it to the caller?", "criteria": {"true": "an exception is caught and dropped without a log, re-raise or report", "false": "the error is logged, re-raised or reported to the caller"}}}}
```

## Review-finding filter

State is the diff plus the finding. Post at 0.8 or more, collapse 0.5 to 0.8, drop below 0.5. The recipe takes one request per finding:

<!-- openjev-call: recipe -->
```json
{"recipe": "review_finding_filter", "inputs": {"diff": "--- a/pay.py\n+++ b/pay.py\n+    total = price * qty\n+    return total / count", "findings": ["Division by zero when count is 0.", "Variable name total is vague."]}}
```

## Judge and test assertions

`judge_assert` checks one property per run: `strict` (grounded), `leak`, `rude`, `pii` (must be absent: fail at 0.5 or more, pass at 0.3 or less), `grounded`, `refuses`, `correct` (must hold: pass at 0.7 or more, fail at 0.3 or less). Keep a known-bad example in the same suite that must be caught, so a dead gate is noticed.

<!-- openjev-call: recipe -->
```json
{"recipe": "judge_assert", "inputs": {"user_message": "What is the refund window?", "reply": "Refunds are accepted within 30 days of purchase.", "context": "Policy: refunds are accepted within 14 days of purchase."}, "profile": "grounded"}
```

Pairwise: `judge_pairwise` runs A/B in both slot orders; accept a winner only if it wins both orders, else report a tie or position bias. `criterion` completes "Which candidate reply better ...?".

<!-- openjev-call: recipe -->
```json
{"recipe": "judge_pairwise", "inputs": {"user_message": "How do I undo my last commit?", "a": "Run git reset --soft HEAD~1 to keep your changes staged.", "b": "Just delete the repository and clone it again.", "criterion": "answers the user's question: correct, specific and safe"}}
```

Rubric means need at least 20 cases; gate on a drop of 0.3 or more. Calibrate thresholds on labelled examples with `mcp__openjev__calibrate` (skill `openjev-calibration`).

## Claim grounding: changelog, summary, citation, quote

1. Quote? Substring or normalised match in code first; no match means fabricated, stop.
2. Numbers, versions, dates? Grep them in the source in code.
3. Then the three-way read with `claim_check`. `profile`: `strict` (supports, contradicts, says_nothing), `grounded` (summary), `quote` (verbatim), `overstatement`. The source must contain the relevant passage.

<!-- openjev-call: recipe -->
```json
{"recipe": "claim_check", "inputs": {"claim": "Default request timeout increased from 30s to 60s.", "source": "-    timeout=30\n+    timeout=45", "claim_label": "CHANGELOG BULLET", "source_label": "DIFF", "source_name": "src/http/client.py"}, "profile": "strict"}
```

Act: supports at 0.8 or more, publish; contradicts at 0.6 or more, block and show the source; says_nothing at 0.6 or more, "uncited claim, add a source or delete".

## Worked example

The call above (changelog says 60s, the diff sets 45) comes back contradicts with p near 0.99. Fix the bullet to 45s before publishing, then re-run: supports.
