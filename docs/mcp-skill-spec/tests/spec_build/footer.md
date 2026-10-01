## Shared rules (all OpenJev skills)
- Precedence: a deterministic check first (tests, parser, grep, schema, allowlist); then an OpenJev
  read; your own judgement only when OpenJev is unavailable or the decision is not typed. When you
  fall back, say so and why.
- Tools missing or unreachable: if a tool this skill names is not available (server not registered,
  toolset `core` without it) or returns `OJ_UNREACHABLE`, `OJ_UNAVAILABLE`, `OJ_TIMEOUT` or
  `OJ_OVERLOADED` after its retries: a gate recipe returns its declared fail-mode decision
  (`degraded: true`) and you follow it; if only `recipe`/`filter`/`batch`
  is missing, run the recipe's questions (resource `openjev://recipes/{id}`) with `ask`
  and apply its policy yourself in the listed order; otherwise use the declared fallback and tell
  the user OpenJev was not used. Never start, stop or restart the OpenJev server.
- Disagreeing with a confident read: overrule it only with deterministic evidence (a test result,
  the file's actual content, a parser); otherwise ask the human and quote the number. Confident
  reads can be wrong (0.987 on a negated flag, 0.9986 on an ambiguous request).
