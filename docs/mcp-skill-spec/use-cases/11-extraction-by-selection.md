# 11. Extraction by selection and span classification

Case file: `tests/cases/11-extraction-by-selection.json`. Final result against the live server: **13 of 13 pass**, no KNOWN LIMITATION cases. Every case is an extraction, selection, verification or span-classification task.

## Purpose
Code proposes candidates (regex matches, spans, date components, request ids). OpenJev selects among them, with an explicit `not_stated` option, or answers a noul "is this extracted value really in the text?". This replaces "ask an LLM to extract JSON, then validate it". Arithmetic, counting and date comparison stay in code.

## When a coding agent should reach for OpenJev
- You are about to write a prompt "extract these fields as JSON" and then a validator. Instead: extract candidates with regex or a cheap parser, then call OpenJev to pick or verify.
- Several candidates of the same shape exist (phones, emails, ids, timestamps) and the choice depends on meaning.
- A cheaper extractor produced fields and you want a per-field verification with a probability.
- NER-style span typing where the spans are already known.
- Date phrases: read the components as text (weekday, offset, daypart) then compute the calendar date in code.

Do not use OpenJev to count, sum, or compare dates, or to generate a span. It selects, it does not write.

## Recommended question schemas

Choice over candidates plus `not_stated`:
```json
{"model":"openjev-latest","state":"<the text>",
 "questions":{"callback":{"type":"choice",
  "instructions":"Which candidate is the number the customer asks to be called back on?",
  "criteria":{"415-555-0142":"the customer's own number","415-555-0199":"the customer's office line","1-800-555-0100":"the company's support line","not_stated":"the text gives no callback number"}}}}
```
Verify one extracted field (batch one noul per field in one request):
```json
{"total":{"type":"noul",
  "instructions":"Extracted field: total_due = '1,416.00 EUR'. Does the invoice explicitly state this as the total due?",
  "criteria":{"true":"the invoice states exactly this total due","false":"the invoice does not state this total due"}}}
```
Date components (three small choices, then compute in code):
```json
{"weekday":{"type":"choice","instructions":"Which weekday does the sender name?","criteria":{"monday":"Monday","friday":"Friday","not_stated":"no weekday named"}},
 "offset":{"type":"choice","instructions":"'this' = coming one, 'next' = one week later, 'after_next' = two weeks later.","criteria":{"this":"the coming occurrence","next":"the following week","after_next":"a week after 'next'","not_stated":"no offset"}}}
```
Span typing: one choice question per span, with the span quoted in `instructions` and identical `criteria` for every span.

## Phrasing rules learned
1. Put the extracted value in `instructions` in quotes and use "exact string" for verification. Before: `"Is it stated in the text?"` gave P=0.75 for a correct order id in a 3-field batch (failed a 0.85 threshold). After: `"Does the text contain this exact string as the order ID?"` with field-specific criteria `{"true":"the text states this order ID",...}` gave 0.999. Generic criteria shared across fields are weaker than field-specific ones.
2. Every candidate needs a description that says how it differs from the others (owner, role, status). For span boundaries, describe why the wrong spans are wrong ("missing the brand", "includes generic category words").
3. Always include `not_stated`, described as a positive condition ("no number belonging to the customer appears"). It won at P>=0.7 in both absent-value cases.
4. Explain enum semantics inside `instructions` when values are idiomatic ("'after_next' = two weeks later").
5. Use candidate strings as the criteria keys so the answer is directly usable; no mapping layer needed.
6. A compound "extract everything" question is worse than one small question per field.
7. Span boundaries (ext-09): a bare "no more and no less" instruction was weak (P=0.66 on the correct span vs 0.34 on the over-inclusive one). Fix: state the rule in `instructions` ("a product name is brand and model only; category nouns such as 'wireless mouse' are never part of it") and label each wrong candidate as wrong with the reason ("runs past the model into the generic category words"). The case now asserts P>=0.8.
8. Ask about the exact fact, not a derived one (ext-13): "the amount the customer wants refunded" was ambiguous (refund = difference, so $49.90 won at 0.39 on $89.90). "The amount actually charged on 3 May" with the list price labelled "not what was charged" is unambiguous.

## Thresholds for acting
- choice: accept when `choice != not_stated` and its probability >= 0.7 (observed 0.8+ on clear cases). Between 0.4 and 0.7, retry with `think: 256, samples: 2` or send to a human. If `not_stated` wins, treat the field as missing and do not fall back to guessing.
- verification noul: accept the extracted value at >= 0.85, reject at <= 0.15, review in between. Clear cases were 0.85+ and 0.15-.
- Any answer with `confidence` below about 0.3 means the distribution is flat: do not act.

## Limitations
- Counting by reading is unreliable: asked directly for 8 occurrences of a word, the model spread probability over 5 to 10 with confidence about 0.05. Do not ask it to count. The tested pattern (ext-12) is: code finds each match with its line, one noul per match asks "is this a real failure?", code counts the trues.
- The model selects but does not confirm span offsets. Verify in code that the chosen span is a substring of the state.
- Date arithmetic ("Friday after next" to a date) is code's job; only components are read.
- Request-validation errors (for example a choice question without `criteria` returning HTTP 422) are an API concern, not an extraction one; they are covered in the API error-handling case file, not here.
- On a busy shared server the same requests took 4-15 s instead of 0.4 s; timeouts should be generous.

## Test inventory (13 cases)
ext-01 phone among three numbers; ext-02 phone `not_stated`; ext-03 invoice total verified true; ext-04 wrong field (subtotal) rejected; ext-05 three-field batch verify; ext-06 date components; ext-07 year `not_stated`; ext-08 NER span typing (3 spans); ext-09 span boundary (brand+model vs shorter/longer candidates); ext-10 seven-option log request id; ext-11 two emails with `think: 256, samples: 2`; ext-12 classify each regex match, count in code; ext-13 amount actually charged among several money values.

Final pass count: 13/13.
