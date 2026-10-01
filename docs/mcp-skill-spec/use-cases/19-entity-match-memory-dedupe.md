# Usage 19: Entity resolution, record linking, memory consolidation

Test file: `tests/cases/19-entity-match-memory-dedupe.json` (16 cases). Final result against the live server: **16/16 passed** (after one rephrase, recorded below). Latency on the shared server was 2 to 15 s per multi-question request under load (normal is well under 1 s per question).

## Purpose
Second-stage judge for fuzzy matching. A cheap first pass (trigram, embedding, blocking key) proposes candidate pairs; OpenJev reads each pair and returns:
- a 3-level `score` (different / related / same) for the pair, so the middle level catches near-matches that must not be auto-merged;
- one `noul` per field (same_name, same_brewery, same_pack) so a merge tool knows which fields agree;
- for agent memory, a `choice` add / duplicate / supersede against the top-k nearest stored memories, plus a second `choice` keyed by candidate index saying which neighbour is affected.

## When a coding agent should reach for OpenJev instead of reasoning in prose
- Writing or running a fuzzy-match, dedupe, record-linkage or catalogue-merge script: use the string metric only to build candidates, then call OpenJev on each surviving pair (no hand-tuned Levenshtein cutoff).
- Writing memory-store logic (add / update / delete decisions for an agent memory, notes DB, knowledge base, CLAUDE.md-style rule files): decide against the nearest neighbours with one typed read rather than asking the LLM to narrate.
- Any "is this the same thing as that" decision where a wrong merge is costly and a wrong keep is cheap.
Do not use it for large-scale candidate generation (it reads only the state you send); do not use it to produce the merged record (it returns probabilities, not text).

## Recommended question schemas

Pair match (state holds the two records, labelled A and B, with all identifying fields):
```json
{"model":"openjev-latest",
 "state":"Dataset A record: name='ACME Corp.', address='500 Market St, San Francisco CA', domain='acme.com'\nDataset B record: name='Acme Corporation', address='500 Market Street, San Francisco, CA', domain='acme.com'",
 "questions":{
  "match":{"type":"score",
    "instructions":"Do record A and record B describe the same real-world entity? Judge identity, not similarity of wording.",
    "criteria":["different: distinct entities (a shared word or name is a coincidence)",
                "related: connected but not identical (parent/subsidiary, same brand but different product, same family)",
                "same: one entity written two ways (abbreviations, legal suffixes, casing, punctuation, transliteration, word order)"]},
  "same_name":{"type":"noul",
    "instructions":"Are the two company names the same name, ignoring casing, punctuation and legal suffix (Corp, Corporation, Inc, Ltd)?",
    "criteria":{"true":"yes, the same name modulo suffix or abbreviation","false":"no, different names"}},
  "same_address":{"type":"noul",
    "instructions":"Do the two addresses point to the same street address?",
    "criteria":{"true":"same address modulo abbreviations","false":"different addresses"}}}}
```
Score is 0-indexed: 0 different, 1 related, 2 same. Add one `noul` per field you need (`same_brewery`, `same_pack`, `same_dob`).

Memory consolidation (two choices in one request, `M1..Mk` are the nearest neighbours in state):
```json
{"model":"openjev-latest",
 "state":"STORED MEMORIES (nearest 5 by embedding):\nM1: <text>\nM2: <text>\n...\n\nNEW FACT: <text>",
 "questions":{
  "action":{"type":"choice",
    "instructions":"Given the NEW fact and the NEAREST STORED memories, what should the memory store do with the new fact?",
    "criteria":{"add":"The new fact carries information not already stored (new topic, or extra detail that does not contradict anything); store it as a new memory",
                "duplicate":"An existing memory already says the same thing; drop the new fact",
                "supersede":"The new fact contradicts or updates an existing memory (a value changed); replace the old memory"}},
  "target":{"type":"choice",
    "instructions":"Which stored memory does the NEW fact overlap with or update? Choose none if no stored memory is about the same subject.",
    "criteria":{"M1":"M1 (frontend stack): the new fact is about this same subject",
                "M2":"M2 (CI/CD and deploy pipeline): the new fact is about this same subject",
                "none":"no stored memory is about the same subject"}}}}
```
Act as: `duplicate` -> skip write; `supersede` -> overwrite `target`; `add` -> insert (ignore `target`, or keep it as a link).

## Thresholds for acting on answers (observed)
| Decision | Rule | Measured |
|---|---|---|
| Auto-merge pair | `match.score >= 1.7` AND every required field noul >= 0.85 | true pairs 1.98 to 2.00, fields 1.00 |
| Reject pair | `match.score <= 0.5` AND `same_*` noul <= 0.15 | false pairs 0.02, fields 0.00 |
| Human review / link as "related" | `0.5 < score < 1.7` (do not merge) | parent/subsidiary 1.00, sibling product 0.66, other pack size 0.94 |
| Field agrees / differs | noul >= 0.85 / <= 0.15 | all fields 0.00 or 1.00 |
| Memory action | take `choice` if `confidence >= 0.8` | 0.85 (duplicate), 1.00 for others; below 0.8 store as `add` and flag |
| Memory target | valid only when action is duplicate or supersede | correct neighbour at 1.00 in all 4 cases |
Always require both the pair score and the field noul to agree before auto-merge; either alone can be wrong on hard pairs.

## Phrasing rules learned
1. **Three levels with a definition each, not "0 to 2".** The `related` definition names the concrete cases (parent/subsidiary, same brand different product). The middle level was used as intended: parent vs subsidiary scored 1.00, sibling product 0.66, same beer in a different pack 0.94; none were pushed to same.
2. **Decide the merge semantics of "same" in the instruction.** Telling the model that "a subsidiary and its parent holding company are different legal entities" made `same_company` 0.00 for Google LLC vs Alphabet Inc. State your own equivalence rule (ignore legal suffix; a different pack size is not the same SKU).
3. **One noul per field, each stating what to ignore.** `same_name` "ignoring casing, punctuation and legal suffix", and for beers "the product name (the beer, ignoring pack description)". Field answers split correctly on the hard pairs: sibling beer had same_brewery 1.0 with same_name 0.0; other pack size had same_name 1.0 with same_pack 0.0.
4. **Give both records every discriminating field.** Same-name people were separated by birth date and city (score 0.02); the same person with reordered name, different date format and upper-cased city scored 2.00. A name alone would not be decidable. Add DOB, VAT id, domain, SKU, address.
5. **Nickname / transliteration equivalences work but should be said once.** Bob vs Robert (same email) scored 2.00 and Müller & Söhne vs Mueller und Soehne 2.00. In `ext-01` the nickname rule is spelled out in the field instruction.
6. **Candidate-index keyed answers need a content gloss on each option.** Failing case (`mem-01`, `mem-02`: `target` returned M1 with a wrong answer):
   - Before: `criteria` values were the identical template `"the new fact is about the same subject as M2"`, differing only in the id.
   - After: `"M2 (CI/CD and deploy pipeline): the new fact is about this same subject"` (a 2 to 4 word topic gloss per option).
   - Result: `target` M2 at 1.00 and M3 at 1.00. The same run with 8 neighbours and glosses derived from the text (`mem-05`) also passed. Cause: with identical template values the choice falls back to the first key. Always put the memory's topic in the option.
7. **Pair `action` with `target`, and always offer `none`.** `add` for an unrelated fact returned target `none` at 1.00. A detail-adding fact on a known subject (staging DB wipe schedule vs "staging is at staging.example.com") was correctly `add`, not duplicate or supersede, because `add` explicitly covers "extra detail that does not contradict anything".
8. **Extensions.** `samples:4` + `think:64` + `sequential:true` together worked on a nickname borderline, but they are not needed; default reads were already saturated. Use `samples:4` only when the pair score is in 0.5 to 1.7 and you want a steadier value.

## MCP-layer requirements exercised
- Empty candidate list (`criteria:{}` on a choice) is a 400 `Choice question must have at least one choice` (case `err-01`). The MCP tool must short-circuit: zero neighbours means action `add`, no call.
- A single neighbour makes `target` a one-option choice, answered locally by the server with no model call; still send `none` so it is a real choice.
- Choice max is 255 options, score max is 10 levels; cap k at about 10 neighbours.
- Tool shape: `openjev_match_pair(a, b, fields[], levels=3)` returns `{score, verdict: same|related|different, fields:{name:p}}`; `openjev_memory_decide(new_fact, neighbours[])` returns `{action, target_id, confidence}`.

## Limitations
- It only sees the state. Nearest neighbours must come from your retriever; if the true duplicate is not in the top-k it cannot be found.
- Probabilities are saturated (mostly 0.00 or 1.00); the score middle values (0.66, 0.94, 1.00) mark relatedness, not calibrated similarity. Do not rank candidates by them; use the pair score only as a 3-way verdict.
- Real world knowledge is used (Google LLC / Alphabet). For private entities, put the relationship into the record fields.
- Labels here are for clean, English or Latin-script records. Cases with missing fields on both sides and very short names (initials only) were not tested.
- Cost: one request per pair; 16 cases took several minutes on a shared, loaded server, so batch a pair's questions into one request and pre-filter with cheap similarity.
- No known-limitation case was needed.

## Test inventory (16)
co-01 legal-suffix variants same (+ name and address noul); co-02 Apple vs Apple Hospitality different; co-03 Google LLC vs Alphabet related; co-04 Müller & Söhne transliteration same; person-01 same name different person; person-02 reordered name and date format same; cat-01 beer duplicate, all fields agree; cat-02 sibling beer, brewery yes name no, middle level; cat-03 same beer other pack size, pack no; mem-01 duplicate + target (rephrased); mem-02 supersede + target (rephrased); mem-03 add + none; mem-04 additive detail is add; mem-05 8 neighbours keyed answers supersede; ext-01 samples:4 + think:64 + sequential; err-01 empty choice 400.

**Final pass count: 16/16.**
