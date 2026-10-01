# OpenJev / Jev usage types (research merge)

Merged from four research sweeps (TypeSafe official docs, community repos and lists, blog and forum write-ups, and adjacent logprob-classifier practice). 24 distinct usage types, each a different task shape or decision. This file is the input for the MCP tool set, the skill pack, and the 10-tests-per-type suite (24 x 10 = 240 tests against the live server).

## Evidence levels

| Level | Meaning |
|---|---|
| E1 | OpenJev or Codiv specific: a page or repo that targets OpenJev / api.codiv.ai / the local OpenJev server |
| E2 | Jev community project whose README or code a researcher actually opened |
| E3 | TypeSafe official docs, cookbook, blog (self-reported numbers, not customer deployments) |
| E4 | Listed in a curated awesome-list only; the project page was not opened |
| E5 | Adjacent practice: logprob classifiers, guardrails, judges, routers from other vendors; no Jev involved |

A type lists every level that applies. Read "E5 only" as "the task shape is well established, but nobody has shown it with Jev".

## Table

| # | Slug | Title | Evidence | Key sources |
|---|---|---|---|---|
| 1 | 01-support-ticket-triage | Ticket, email and message triage | E1 E2 E3 | codiv.ai/docs; dev.to valyuai guide; daveebbelaar/ai-cookbook 07-ticket-triage.py; realpython jev_desk.py |
| 2 | 02-confidence-gated-action | Confidence-gated act / ask / escalate | E2 E3 E5 | docs.typesafe.ai cookbooks; darwintechlab/openjev; pydantic-ai typesafe docs; OpenAI logprobs cookbook |
| 3 | 03-agent-tool-call-gate | Coding-agent pre-tool-use safety gate | E2 E4 E5 | shiftynick/jev-axi; Anthropic auto-mode post; awesome-jev verification list |
| 4 | 04-untrusted-content-injection-screen | Prompt-injection screen of fetched content and tool output | E2 E4 E5 | jev-axi README; agent-guard-plugins; Horizon-Labs model card |
| 5 | 05-done-claim-completion-gate | "Am I really done?" gate for agents | E2 E4 E5 | TheoOliveira/pi-jev; jev-belay (list); LLM-as-a-Verifier |
| 6 | 06-semantic-code-lint | Plain-English lint, diff and commit checks | E3 E4 | use-case-map "Semantic code linting"; jev-pref, jev-commit, Sniff Test (list) |
| 7 | 07-issue-pr-triage-dedupe | Issue and PR triage, duplicate detection, review-finding filtering | E2 E5 | cephalization/jev-triage; guillermoscript/repo-assistant; claude-plugins-official #1852 |
| 8 | 08-model-effort-routing | Per-turn model and effort routing | E2 E3 E4 | 0xNatoshi/jev-codex-router; LangChain ModelRouterMiddleware; use-case-map |
| 9 | 09-tool-skill-selection | Tool, skill and MCP selection from a roster | E2 E3 | typesafe skill_suggestion cookbook; pi-jev jev_find_tools/jev_find_skill |
| 10 | 10-nl-to-typed-call | Natural language to typed function call | E3 | typesafe function_calling cookbook; pydantic-ai TypeSafeModel |
| 11 | 11-extraction-by-selection | Extraction by selection and span classification | E3 E4 | jev-1.13 jaggedness page; sde_cascade and autoformat cookbooks; JevSpan (list) |
| 12 | 12-judge-and-test-assertions | LLM-as-judge, eval and pytest assertions | E2 E3 E5 | DeepEval integration; LangChain jev-agent-evals blog; G-Eval docs |
| 13 | 13-log-alert-triage | Log, alert and incident triage | E2 E4 E5 | mailent README; jev-logtriage (list); DevOpsBoys classifier; OncallX paper |
| 14 | 14-semantic-grep-relevance | Grep by meaning, file relevance, context pruning | E2 E3 E4 | allebee/jevgrep; semantic_find cookbook; fast-jev-compaction (list) |
| 15 | 15-rag-filter-rerank | RAG passage filtering, reranking and sufficiency | E2 E3 E5 | hotchpotch/jev-reranker; classifying_rag_passages cookbook; CRAG paper; Qwen3-Reranker |
| 16 | 16-claim-grounding-check | Claim, citation and literature verification | E3 E4 E5 | citation_check cookbook; Vectara HHEM; dev.to literature-screening pattern |
| 17 | 17-moderation-guardrails | Input/output guardrails and moderation with an "uncertain" outcome | E2 E3 E5 | CodeAlive-AI/mastra-jev-moderation; llm_guardrails and consistency cookbooks; NeMo Guardrails; Llama Guard |
| 18 | 18-composite-rubric-scoring | Weighted rubric scoring: candidates, leads, documents | E1 E3 E4 | jose-troche/live-rubric (OpenJev on Codiv); composite-scoring pattern; jev-resume-screening (list) |
| 19 | 19-entity-match-memory-dedupe | Entity resolution, record linking, memory consolidation | E2 E3 E4 | entity_alignment cookbook; remembra-ai/remembra; jlink (list) |
| 20 | 20-taxonomy-classification | Hierarchical / high-cardinality classification | E3 E4 | hierarchical_classification cookbook; tax-doc-classifier, jev-tree (list) |
| 21 | 21-bulk-labeling-active-learning | Dataset labeling, uncertainty sampling, features for ML | E2 E3 E5 | sutro-sh/jev-align; autoresearch_feature_discovery cookbook; Refuel Autolabel; arXiv 2609.24052 |
| 22 | 22-image-and-ui-decisions | Image, screenshot, browser and desktop action decisions | E1 E2 E3 | codiv.ai/docs/models; Ying-Kai-Liao/jev-browser; moritzkremb/jev-voice-browser; trycua/cua jev_adapter.py; glaforge.dev |
| 23 | 23-think-multistep-navigation | Multi-step decisions that need `think` (graph, game, planning) | E1 E2 E3 | razorback16/openjev README (think/steps/samples); jexp/neo4jev; OneVOneJev; typesafe blog (Wikiracing, Doom) |
| 24 | 24-calibration-threshold-audit | Calibration audit, threshold fitting, repeat-and-agree | E2 E3 E4 | kunko-ai-labs/judge-audit; consistency cookbooks; jevcal, jev-ood-calibration (list) |

## Per-type sections

Question shapes: N = noul (yes/no probability), C = choice, S = score, I = image input, T = think/samples extension.

### 1. 01-support-ticket-triage
Task shape: one text (ticket, email, chat, transcript) in; owning queue (C), frustration or urgency (S), flags like refund or churn (N); plain code routes. The canonical quickstart. Also covers email tray sorting and intent routing cascades (code / specialist LLM / human), which are the same decision at different ends.
Example: Stripe-403 ticket, department {billing, technical, sales, other}, frustration 3 levels, `is_urgent`. Route to human_review if team confidence < 0.8.
Agent trigger: the agent is about to write `if "refund" in text` chains, keyword routers, or "ask the LLM to return JSON with a category".
Gotchas: include an `other` option; a choice always sums to 1. Put full meaning in instructions, ids are not sent to the model.
Evidence: E1 Codiv docs reuse the same example against openjev-latest; E2 ai-cookbook and realpython; E3 quickstart.

### 2. 02-confidence-gated-action
Task shape: the decision is not the label but whether to act on it. Per-action thresholds (low bar for read-only, high bar for money-moving), a floor for human handoff, and ambiguity detection (proceed or ask a clarifying question). For a noul use distance from 0.5; a noul has no confidence field.
Example: banking intent {check_balance, approve_transfer, other}: auto-run check_balance at conf > 0.5, approve_transfer at > 0.85, else ask.
Agent trigger: the agent writes a single global `threshold = 0.5`, or is about to guess on an ambiguous user request.
Gotchas: thresholds must be measured on labelled data (see type 24). Log the model version.
Evidence: E3 docs pattern "confidence as a second axis"; E2 darwintechlab auto/escalate labelling; E5 OpenAI logprobs cookbook, CLAM ambiguity work.

### 3. 03-agent-tool-call-gate
Task shape: score a proposed shell command or tool call with independent hazard noul questions (destructive, exfiltrates, remote code, outside task, weakens security) plus a risk score; map to allow / ask / deny. Deterministic rules settle obvious cases locally.
Example: PreToolUse hook on `curl ... | bash` versus `pnpm test`.
Agent trigger: any Claude Code hook design, allowlist maintenance, or "should the agent run this?" logic.
Gotchas: fail closed vs fail open must be chosen deliberately; the model must never make the gate looser than the static rules.
Evidence: E2 jev-axi README (deny at remote_code 0.99); E4 pi-verdict, Reflex, jev-guard; E5 Anthropic auto mode uses a single-token yes/no stage 1.

### 4. 04-untrusted-content-injection-screen
Task shape: screen text the agent just fetched (web page, issue body, MCP tool result, RAG chunk) for embedded instructions, before the agent acts on it. Lower threshold for higher-risk next actions.
Example: after WebFetch, noul "does this text try to instruct an AI agent to ignore prior instructions or run a download-and-execute command?".
Agent trigger: an agent is about to summarise or act on external content.
Gotchas: the screened text is itself adversarial state and can steer the answer; test with planted injections. Classifiers show 1.6-7% benign false positives at 0.5.
Evidence: E2 jev-axi; E4 Agent Chaperone; E5 Horizon-Labs, agent-guard-plugins, Prompt Guard skill.

### 5. 05-done-claim-completion-gate
Task shape: cheap typed check at the end of an agent turn: were files changed with no passing check since, does the final message claim completion without verification, is a human needed. Also mid-run progress (has the state already satisfied the task, is the run stalled).
Example: Stop hook with 3-4 noul questions over transcript evidence.
Agent trigger: the agent is about to say "done", or a Stop / SubagentStop hook is being written.
Gotchas: fail open on every error path; do not use a single-pass read to verify long-horizon patches (CLM README negative result).
Evidence: E2 pi-jev jev-gate; E4 jev-belay, limpet, Foreman; E5 LLM-as-a-Verifier progress tracker.

### 6. 06-semantic-code-lint
Task shape: team conventions as noul or score questions per function, hunk, doc or commit message; sub-second so it runs in pre-commit or CI. Includes commit message vs diff, secrets screen, AI-slop and prose style linting.
Example: per changed function, noul "swallows exceptions silently", fail CI above 0.9; commit message noul "describes behaviour not present in the diff".
Agent trigger: the agent finds a regex-based lint, a reviewer checklist, or AGENTS.md prose rules that no tool enforces.
Gotchas: one focused claim per noul; literal reading; block only on high-precision checks (credentials), warn on the rest.
Evidence: E3 use-case map; E4 jev-lint, jev-pref, jev-commit, Sniff Test, Clean Code Judge (all listing-only). Weakest direct evidence among dev types.

### 7. 07-issue-pr-triage-dedupe
Task shape: fixed question set per issue or PR (kind C, severity S, urgent N, duplicate N, next step C), an "Unsure" queue ordered by least confidence, duplicate detection over a shortlist from embeddings or grep, and filtering AI review findings by "is this a real bug introduced by this diff".
Example: 12 candidate review comments, noul per finding, post those above 0.8, collapse the rest.
Agent trigger: bulk issue handling, review-comment generation, "is this a dupe of #N".
Gotchas: embedding similarity is a retrieval signal, not the decision. Discrete 0/25/50/75/100 rubrics interact badly with cutoffs; continuous probability avoids it.
Evidence: E2 jev-triage; E5 repo-assistant, claude-plugins-official #1852, gha-issue-triage.

### 8. 08-model-effort-routing
Task shape: read a compact task summary; choose model tier and thinking effort for the next call, with a safe default on low confidence or service error.
Example: choice {cheap, mid, frontier}, noul needs_deep_reasoning, fail open to default.
Agent trigger: an agent harness picks a model, or an agent burns frontier tokens on a trivial task.
Gotchas: savings claims are simulated (jev-codex-router reports about -60% in backtest, not measured quota).
Evidence: E2 jev-codex-router (archived); E3 use-case map, LangChain ModelRouterMiddleware; E4 jev-router, Switchboard.

### 9. 09-tool-skill-selection
Task shape: choose which of N skills, tools, MCP servers or rules deserves injection for this prompt, with a "none needed" noul so it can abstain; at most one suggestion; optional second read over the top 3.
Example: 182 skills, choice over ids plus noul `any_skill_needed`.
Agent trigger: the agent is about to load a long tool or skill list into context.
Gotchas: over 255 (or the server's option cap) options needs two-stage selection; validate the returned id against the roster.
Evidence: E3 cookbook (wrong-skill loads 16.8% to 7.3%, self-reported); E2 pi-jev.

### 10. 10-nl-to-typed-call
Task shape: map a sentence to a function name plus closed-set arguments: a choice per Literal or enum parameter, a noul per bool flag, generated from the signature. Open-ended ints and free strings stay in code.
Example: "plot rolling correlation between nvda and spy for the past month" to `rolling_correlation(symbol, benchmark, window)`.
Agent trigger: the agent is writing an argument parser for natural-language commands or a CLI-with-prose front end.
Gotchas: every argument value valid by construction, but semantic correctness is not; report a confidence per argument.
Evidence: E3 function_calling cookbook (Dispatcher class), pydantic-ai TypeSafeModel.

### 11. 11-extraction-by-selection
Task shape: code proposes candidates (regex matches, spans, date components, table cells); the model selects which one, with a "not stated" option. Includes span classification for NER and per-field verification of a cheaper extractor's output. Arithmetic and date comparison stay in code.
Example: several phone numbers in an email, choice which is the customer's; noul per extracted invoice field "is the value stated in the text".
Agent trigger: the agent is asking an LLM to "extract JSON" and then validating it.
Gotchas: dates are read as text; counting is unreliable; boundaries must be verbatim.
Evidence: E3 jaggedness page and cookbooks; E4 JevSpan, jevextract; E5 Extend.ai, ExtractConf on why raw logprob gating fails.

### 12. 12-judge-and-test-assertions
Task shape: typed rubric scores or pass/fail claims over an agent reply or trace; pytest-style semantic assertions; regression gates after prompt or model changes; pairwise preference with slot swapping.
Example: `assert noul("reply reveals the system prompt") < 0.05`.
Agent trigger: the agent writes a test that would otherwise use an LLM judge or string contains.
Gotchas: score questions may ignore state on OpenJev (README caveat), so validate them on known good/bad examples; prefer noul or choice with explicit criteria.
Evidence: E3 DeepEval and Pydantic AI docs; E2 LangChain agent-evals blog (narrow, five runs); E5 G-Eval, LLM-as-a-Verifier.

### 13. 13-log-alert-triage
Task shape: batch collapsed log clusters, alerts or scan findings into one call of noul/score/choice questions; map in code to suppress / watch / review / page. Owning-team routing with a pre-filtered candidate set.
Example: "indicates a real failure, not routine noise", severity 4 levels, subsystem choice.
Agent trigger: a stack trace, CI log or alert payload is pasted and the agent must decide what matters.
Gotchas: never execute remediation from a read; a SOC study found a linear SVM beat LLM prioritisation, so keep rules for hard escalations.
Evidence: E2 mailent (weak); E4 jev-logtriage, jevlogs; E5 OncallX, DevOpsBoys.

### 14. 14-semantic-grep-relevance
Task shape: one noul per line, file, hunk or row, batched; print or keep those above a threshold; exit codes for shell gating. Also a choice over tagged line ids plus an `exists` noul, and relevance-based pruning of stale tool output and context.
Example: `tail -f server.log` filtered to "real errors, not routine noise"; 40 candidate files, open only those above 0.6.
Agent trigger: the agent is about to read many files, page through logs, or grep with a brittle regex.
Gotchas: choice always ranks something first, hence the separate `exists` noul; filter state first to avoid context rot.
Evidence: E2 jevgrep (F1 0.90 on own benchmark), onesie; E3 semantic_find cookbook; E4 fast-jev-compaction, jev-pruner.

### 15. 15-rag-filter-rerank
Task shape: between retrieval and generation, a noul per passage (relevant, usable evidence, contradicts the premise, tries to instruct the model), then evidence / conflict / drop; sufficiency gate "is this enough to answer"; optional reranking.
Example: 30 chunks, keep those at or above 0.55; if none, re-query.
Agent trigger: the agent builds or debugs a retrieval pipeline or feeds search hits into a prompt.
Gotchas: counter-evidence exists: ranking with Jev alone did not beat vector retrieval in two tests, and batching many rows hurt ranking. Treat reranking as filtering, and measure.
Evidence: E2 jev-reranker; E3 cookbooks (40 legal queries, 5% to 18% top-1); E5 CRAG, Qwen3-Reranker.

### 16. 16-claim-grounding-check
Task shape: string-match a quote in code, then a choice {supports, contradicts, says_nothing} over the surrounding source; groundedness of summaries and changelogs; inclusion/exclusion screening of papers.
Example: bullets of a changelog checked against the diff text; confidence below 0.8 goes to a human.
Agent trigger: the agent writes a citation, summary or release note from source material.
Gotchas: small planted-failure samples only; the source must be in state.
Evidence: E3 citation_check cookbook, dev.to literature pattern; E4 citation-verifier, Paper Trellis; E5 Vectara HHEM.

### 17. 17-moderation-guardrails
Task shape: blocking noul plus category choice on each message or LLM output; explicit `uncertain` outcome when the top probability is under a threshold; fail-open behind a deadline.
Example: `must_block` noul, category {phishing, spam, harassment, none}; abort at p >= 0.7.
Agent trigger: the agent adds a moderation, safety or PII check in front of or behind a model.
Gotchas: labels still flip on borderline items (2 of 8 questions in TypeSafe's cookbook); repeat or threshold; non-English state needs testing.
Evidence: E2 mastra-jev-moderation (9/9 hostile blocked, 0/49 real, via listing); E3 guardrails and self-consistency cookbooks; E5 NeMo Guardrails, Llama Guard.

### 18. 18-composite-rubric-scoring
Task shape: break a fuzzy judgment into independent score dimensions and combine with weights in code; re-weighting needs no new inference. Applies to resumes, leads, and document quality; live rescoring on every typing pause is affordable.
Example: python_depth, team_leadership, system_design (5 levels each); or 15 rubric dimensions on a draft.
Agent trigger: the agent is asked to "rate", "rank" or "qualify" free text.
Gotchas: describe each level as a concrete situation; run bias audits before HR use (KoBBQ result: forced answers hit the stereotype 79% of the time); score questions can ignore state.
Evidence: E1 live-rubric (only OpenJev-on-Codiv user repo found); E3 composite scoring pattern; E4 jev-resume-screening.

### 19. 19-entity-match-memory-dedupe
Task shape: for a candidate pair from a cheap first pass, a score {different, related, same} plus field-level noul; merge / curate / leave unlinked. Same shape for agent memory: add / duplicate / supersede / unrelated against nearest neighbours.
Example: 450 beer-catalogue pairs; new memory fact vs top-5 memories in one request.
Agent trigger: the agent writes fuzzy-match code, dedupe scripts, or memory-store logic.
Gotchas: wrong merges cost more than misses, so keep the middle level; key questions by caller-side candidate index so the model cannot invent ids.
Evidence: E3 entity_alignment cookbook; E2 remembra; E4 jlink (F1 0.73 vs 0.69 string matching).

### 20. 20-taxonomy-classification
Task shape: traverse a taxonomy with one choice per node and beam search over K paths (geometric-mean edge probability), or classify among hundreds of options with a confidence gate and broader-parent fallback.
Example: 261 IRS form types; patent abstract through a classification tree.
Agent trigger: a flat classifier with too many labels, or a hand-built category tree.
Gotchas: option cap (Codiv says 128, TypeSafe 255, HF OpenJev 52); constrain candidates first because accuracy falls with class count.
Evidence: E3 hierarchical_classification cookbook; E4 tax-doc-classifier, jev-tree, jev-folio (self-reported).

### 21. 21-bulk-labeling-active-learning
Task shape: label thousands of rows with typed decisions, send low-confidence rows plus a random audit sample to humans, optimise question wording against those labels; or turn text into numeric features (score expectation, spread, noul probability) for a classical model.
Example: 2,000 wine notes to 67 numeric columns to CatBoost; 50k support messages with audit sample.
Agent trigger: "label this CSV", "tag these rows", "why is our classifier unsure".
Gotchas: sequential single calls are wasteful; batch questions per row. Calibrate cut on human-checked sample.
Evidence: E2 jev-align; E3 autoresearch cookbook (RMSE 3.09 to 1.77); E5 Refuel (token probability AUROC 0.83 vs verbalised 0.58), arXiv 2609.24052 crash narratives.

### 22. 22-image-and-ui-decisions
Task shape: up to 8 images (screenshot, photo) plus typed questions; or a UI represented as candidate ids (accessibility tree, DOM, OCR table) with a choice for the next action and noul for done / blocked / irreversible; code validates the id then acts.
Example: screenshot plus noul "page shows a login wall"; choice over `e01..e40` elements; pause before "Place order".
Agent trigger: the agent has a screenshot, a UI test failure image, or a browser task.
Gotchas: about 280 input tokens per image, about 2 s per image read locally; the hosted TypeSafe Jev is text-only, so do not copy its text-only limit; the HF project also called openjev is a different model.
Evidence: E1 Codiv models page (up to 8 images); E2 jev-browser (40/42 tasks, self-reported), jev-voice-browser, cua adapter; E3 Laforge multimodal post.

### 23. 23-think-multistep-navigation
Task shape: problems where one shallow read is unreliable: walking a graph or link structure hop by hop, choosing among legal moves each tick, planning where earlier reads feed later ones. On OpenJev use `think` (and samples/steps) selectively and keep validity checks in code.
Example: Neo4j hop choice with a `goal_reached` noul; WikiRace link choice; game tick with server-validated legal moves; a small arithmetic-flavoured puzzle split into per-item noul plus code counting.
Agent trigger: a single read gives low confidence or contradicts itself, or the task is inherently sequential.
Gotchas: `think` costs latency; other agents share the server; greedy search cannot recover from an early mistake (use beam). Evidence for think itself is thin: it is an OpenJev extension, not part of the TypeSafe wire API.
Evidence: E1 OpenJev README extensions; E2 neo4jev, OneVOneJev; E3 blog (Wikiracing, Doom bot). E4-level community demos for chess and drones.

### 24. 24-calibration-threshold-audit
Task shape: run the model in shadow mode against past human decisions; measure accuracy-coverage and calibration error; fit per-question thresholds; repeat the same read and use agreement or spread as a reliability signal; fail CI on drift when the model alias moves.
Example: 500 labelled tickets, choose the threshold giving zero observed errors on the auto-routed slice.
Agent trigger: the agent is about to choose a numeric threshold, or reports a model swap or new alias.
Gotchas: OpenJev appears on JevBench v1.4 at 28.6% sealed vs 81.8% public accuracy (razorback16/openjev issue 6); Choice and Score overconfident and noul underconfident in one audit. Tests should assert monotonicity and stability, not absolute accuracy.
Evidence: E2 judge-audit; E3 self-consistency cookbooks (90.8% raw, 99.2% after 0.60 threshold, self-reported); E4 jevcal, jev-ood-calibration.

## Dropped or merged

| Raw item | Fate | Why |
|---|---|---|
| Speculative fan-out | Cross-cutting pattern, not a type | Technique used by nearly every type; goes into the skill pack as a core rule (ask all plausible questions in one call) |
| Intent routing cascade | Merged into 1 | Same decision as ticket triage; only the handlers differ |
| Email triage | Merged into 1 | Same shape |
| Confidence-gated suggestions, ambiguity detection | Merged into 2 | Same decision: act vs ask |
| Agent progress tracking | Merged into 5 | Same gate, mid-run instead of end-run |
| Writing quality / AI-slop linting | Merged into 6 | Same lint shape on prose |
| Context compaction and tool-output pruning | Merged into 14 | Per-item relevance noul |
| Reranking search results | Merged into 15 | Weak evidence as a ranking tool; kept as filter |
| Citation check, literature screening, groundedness | Merged into 16 | Same "is the claim supported by evidence" read |
| Content QA, translation QA | Merged into 18 / 6 | Rubric scoring on generated text |
| Live rubric while typing | Merged into 18 | Same weighted rubric shape (kept as the E1 evidence) |
| Hierarchical beam search vs OCR page routing | Merged into 20 | Same high-cardinality classification |
| Feature extraction for ML, uncertainty sampling, research text coding | Merged into 21 | Same bulk labelling loop |
| Graph navigation, games, real-time control loops | Merged into 23 | Same sequential-decision shape |
| Structure recovery / autoformat | Merged into 11 | Choice over input-derived candidates; niche |
| Repeat-and-agree, calibration audits | Merged into 24 | Same measurement task |
| SQL-native semantic predicates | Dropped as a type | Delivery surface (pg-jev, duckdb-jev), not a new decision; the MCP could offer a batch endpoint that serves it. Listing-only evidence |
| Workflow-platform blocks (AutoGPT, ADK, Mastra, DSPy) | Dropped | Packaging of existing primitives; informs SDK design, not usage |
| Observability, gateways, self-hosting | Dropped | Deployment concern; informs server config and tracing, not usage |
| Small open decision models (Laya, Verdict, JevK5, SemIf) and vLLM mechanism | Dropped | Background about backends and benchmarks; useful in the server notes, not a task |
| Robotics, drones, trading, browser extension ad blocking, social feed judging, lead-gen media | Dropped | Demo-level or listing-only; nothing a coding agent hits day to day. Trading and robotics have no serious evidence |
| Best-of-N candidate verifier (CLM) | Dropped as a positive type | Kept as a negative boundary in 5 and 12: single-pass reads do not verify long-horizon patches |
| Opinion prediction / benchmarking (AITA, surveys) | Dropped | Research curiosity; the measurement lessons live in 24 |
| Bulk security / secret / malicious-skill detection | Split | Secrets to 6; malicious skill vetting to 4 |

## How much direct community evidence exists (honest note)

Very little, and none of it is production-scale.

- TypeSafe launched Jev on 14-15 September 2026. The ecosystem is about two weeks old at the time of this sweep. Nearly every repo dates from 16 September or later, and star counts on some listings look inflated.
- OpenJev-specific usage is thin. The only working user repo found that targets OpenJev on Codiv is jose-troche/live-rubric. Others: a WhatsApp agent test script pointed at api.codiv.ai, the quackd preset (which states nothing has ever answered a real robot), the OpenJev README itself, and Codiv's own docs that reuse the TypeSafe quickstart. Everything else is Jev on TypeSafe or another compatible server, so the wire shape transfers but calibration and accuracy do not.
- Most "official" evidence is cookbooks and a brainstorm use-case map, not customer case studies. Numbers such as 12.2x cheaper, 5% to 18% rerank, 16.8% to 7.3% skill errors, and RMSE 1.77 are self-reported and unreproduced. The dev.to guide says so itself.
- Many community entries rest on one-line awesome-list descriptions whose project pages were not opened (labelled E4). The list warns that a listing is not an endorsement and that same-day bulk submissions are unproven.
- The fourth sweep found no Jev or OpenJev mentions at all. Its 25 items are adjacent practice (E5): they show the decision shape is well established and give thresholds and pitfalls, but they are not adoption evidence.
- Independent criticism exists and is encoded in the types: forced answers cannot abstain, type safety is not correctness, ranking is unreliable in some tests, OpenJev scores far lower on sealed than public benchmark items, and score questions can ignore the state on the local server.
- Where evidence is weakest (6, 13, 23 for think, and anything E4-only), the spec should present the pattern as a hypothesis and let the tests against the live server decide.
