# Usage type 06: Plain-English lint, diff and commit checks

Case file: `tests/cases/06-semantic-code-lint.json` (generator: `tests/gen06.py`).
Final result against the live server: **20 of 21 pass**. The one failure is the deliberate `KNOWN-LIMITATION-coasked-interference` case, which documents a real defect (see Limitations).

## Purpose

Turn team conventions that no tool enforces into typed questions, one claim per question, asked per function, hunk, README paragraph or commit message. Each answer is a calibrated probability, so a pre-commit hook or CI job can block, warn or ignore by threshold. Reads take well under a second when the server is idle (the test run above saw 1 to 26 s only because other agents shared the server).

Covered here: swallowed exceptions, commit message versus staged diff, secrets screen, comment versus code, conventional-commit typing, README slop and style checks, naming quality.

## When a coding agent should reach for OpenJev instead of reasoning

Trigger on any of these:
- The agent is about to write a regex lint for something semantic ("catches Exception and does nothing", "message mentions X").
- An AGENTS.md / CONTRIBUTING rule or reviewer checklist exists that no linter enforces.
- The check must run repeatedly (every hunk, every commit, CI) and be deterministic in cost and latency; an LLM reading in prose is slow, non-uniform and not thresholdable.
- The agent needs a number to gate on, not an opinion.

Do not use it for: anything a parser or AST can decide exactly (unused imports, line length), or for whole-repo reasoning that needs many files (state is a snippet, keep it small).

## Recommended question schema (copy-pasteable)

Per-function convention check:

```json
{
  "model": "openjev-latest",
  "state": "<one function's source>",
  "questions": {
    "swallows": {"type": "noul", "instructions": "Does this function silently swallow an exception, meaning it catches an error and neither logs it, re-raises it, nor reports it to the caller?"},
    "catches_broad": {"type": "noul", "instructions": "Does the except clause catch the base Exception class or use a bare except?"}
  }
}
```

Commit message versus diff:

```json
{
  "model": "openjev-latest",
  "state": "Commit message:\n<subject and body>\n\nStaged diff:\n<git diff --staged>",
  "questions": {
    "mismatch": {"type": "noul", "instructions": "Does the commit message describe a change that is NOT present in the diff?"},
    "type": {"type": "choice", "instructions": "Which conventional-commit type best fits this change?",
             "criteria": {"feat": "adds new user-visible behavior", "fix": "corrects a bug",
                          "refactor": "restructures code with no behavior change",
                          "docs": "documentation only", "chore": "tooling, deps, config"}}
  }
}
```

Secrets screen (placeholder-aware):

```json
{
  "model": "openjev-latest",
  "state": "<added lines of the diff>",
  "questions": {
    "secret": {"type": "noul", "instructions": "Does the added code contain a real, working-looking credential written as a string literal? A placeholder such as YOUR_API_KEY_HERE, <token>, xxx or changeme is not a real credential."}
  }
}
```

README paragraph: put up to about 10 independent one-claim noul questions in one request (tested: 10 questions, all correct on a plain paragraph).

Style adherence with score (0-indexed levels, so `score` is between 0 and levels minus 1):

```json
{"naming": {"type": "score", "instructions": "How descriptive are the function and variable names in this code?",
            "criteria": ["completely opaque single letters", "mostly unclear", "mostly descriptive", "fully descriptive"]}}
```

## Phrasing rules learned

1. **Define the claim in the question itself; never ask "is this bad".**
   Before: `Is this code bad?` on a bare `except: pass` function gave 0.026 (and 0.007 on a clean one), i.e. useless.
   After: `Does this function silently swallow an exception, meaning it catches an error and neither logs it, re-raises it, nor reports it to the caller?` gave 1.0 vs 0.0.
2. **Spell out exclusions for high-precision checks.**
   Before: `...hardcoded credential, such as an API key or secret key written as a string literal?` scored 0.874 on `API_KEY = "YOUR_API_KEY_HERE"` (false positive that would block a commit).
   After: `...real, working-looking credential... A placeholder such as YOUR_API_KEY_HERE, <token>, xxx or changeme is not a real credential.` scored 0.0; a `ghp_...` token still scored 1.0. Using `criteria {true,false}` and `think: 256` also worked but the instruction rewrite alone is enough.
3. **One claim per question, literal wording.** Split "swallows AND lacks docstring AND loops" into separate nouls. The literal-reading case shows this pays off: "Can this function raise RequestException to its caller?" (0.01) and "Does it write a log message on failure?" (1.0) are decidable even when "silently" is arguable. A function that logs at debug then returns None scored 0.04 on the "silently swallow" definition, because the definition says logging counts; encode the team's policy in the definition.
4. **Ask each claim positively and literally; do not phrase checks as negations ("fails to log").** If you need the opposite claim, ask it as its own question rather than computing 1 - p. The `literal-reading-edge` case does this with two positive claims (`raises`, `logs`).
5. **For the comment-versus-code check, state the concrete difference.** `Does the comment contradict the code?` gave 0.823; naming the discrepancy ("the code returns the plain difference, which excludes one endpoint") gave 0.97-0.99.
6. **Score questions for style are weaker; prefer noul with a concrete claim.** README caveat: score can ignore state. The two naming-quality probes passed (0.87 on opaque names, 3.0 on descriptive), but treat score as advisory and validate it on known good/bad samples for your rubric.
7. **Use `choice` with descriptive criteria for typing** (conventional-commit type): `feat` and `refactor` both correct.
8. **Do not co-ask semantically overlapping questions on a claim that gates a block** (see Limitations).

## Thresholds for acting on answers

| Check | Block (exit 1) | Warn | Ignore |
|---|---|---|---|
| Secrets (placeholder-aware definition) | noul >= 0.9, then re-ask that one question alone before failing | 0.6-0.9 | < 0.6 |
| Swallowed exception, commit-vs-diff mismatch | do not block on a single read; use `samples: 4` and warn | >= 0.7 | < 0.3 |
| Prose slop, style, naming score | never block | noul >= 0.8, or score at or below the lowest two levels | rest |

Observed separations in the tests: true positives 0.68-1.0, true negatives 0.0-0.01. The lowest true positive (0.68, `return 0` fallback on caught KeyError/TimeoutError with a "swallow" definition that allows "reports to caller") is ambiguous by definition, which is why swallow checks warn rather than block. `samples: 4` on the bare-`except: pass` cleanup loop gave 1.0.

## Limitations

- **KNOWN LIMITATION, sibling interference.** The claim `Does the caller lose all information about which item failed and why?` scored 0.996 alone (and 1.0 with `think`), but 0.33 when asked in the same request as the broader swallow question. Case `KNOWN-LIMITATION-coasked-interference` reproduces it and is expected to fail until fixed. Mitigation: for any question that can block, send it in its own request (cheap, about 0.4 s) or only with unrelated siblings, and re-ask before failing.
- Placeholder recognition depends on the wording in rule 2.
- Score questions can ignore state (upstream caveat); keep them advisory.
- State must be a snippet; the model does not see the rest of the repo, so "unused elsewhere" style claims are out of scope.
- API-level error handling (unknown model 400, malformed score criteria 422) is out of scope here; it is covered by the MCP transport/validation test file, not this usage type.
- Latency in tests was dominated by a shared server; do not quote the timings as benchmarks.

## Test inventory (21 cases)

Swallow: pos, neg, multi-question, flag-only, samples=4. Commit: matches, mismatch, choice feat, choice refactor. Secrets: pos, neg env, placeholder, real token. Prose: slop pos, slop neg, ten checks. Semantics: literal reading, comment vs code. Score: poor naming, good naming. Known limitation: sibling interference.
