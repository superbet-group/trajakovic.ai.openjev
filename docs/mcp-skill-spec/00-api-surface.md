# OpenJev HTTP API surface: live-verified reference

Status: measured against a running server on 2026-09-29. Every "Verified live: yes" below was
executed with `curl`/HTTP against `http://127.0.0.1:8080` (sequentially, one request at a time; the
server is shared). "Verified live: no" means the behavior is read from source only and could not
be exercised without touching the server or its configuration; the reason is given.

This document is the base for the MCP server and skill pack. Sections 1-2 are the quick facts,
3-6 the per-endpoint detail, 7-9 the cross-cutting tables (fields, errors, limits), 10 the
backend differences, 11 the README-vs-reality disagreements, 12 the bugs found, 13 a curl cookbook,
14 the verification log, 15 proposed server additions (not implemented; added 2026-09-30 with
spec 1.1).

## 1. The server that was tested

| Fact | Value | Source |
|---|---|---|
| Base URL | `http://127.0.0.1:8080` | live |
| Backend | MLX, in-process, Apple silicon (`OPENJEV_BACKEND=mlx`, started by `./startOpenJev`) | `startOpenJev`, process list |
| OpenAPI title / version | `OpenJev` / `0.5.0` (`GET /openapi.json`) | live |
| Model served for `/v1/systemone` | wire name `openjev-0.1`, aliases `openjev-latest`, `jev-latest`, `jev-preview` | live |
| Model served for `/v1/chat/completions` | `diffusiongemma-26b` (and alias `diffusiongemma`) | live |
| Weights (MLX) | `mlx-community/diffusiongemma-26B-A4B-it-4bit` (default of `OPENJEV_MLX_MODEL`) | `config.py` |
| Auth | none configured: `Authorization` and `X-Origin-Secret` headers are accepted and ignored | live |
| Encoder models (laya, verdict, clm, jevk5) | not served here; `GET /v1/models` lists only 3 models | live |
| Request/response encoding | JSON only, `Content-Type: application/json` | live |

Response headers on every route that reaches the app (2xx, 4xx from handlers):

```
content-type: application/json
server-timing: model;dur=0.0, server;dur=345.9, total;dur=345.9
x-request-id: req_<32 hex>
x-typesafe-request-id: req_<32 hex>      (same value as x-request-id)
```

Exceptions: a 413 has both request ids but no `server-timing`; a 500 leaks as plain text
`Internal Server Error` with neither header (see section 12).

## 2. Endpoint inventory

| Method + path | Purpose | Documented in README | Verified live |
|---|---|---|---|
| `GET /health` | liveness, returns `{"status":"ok"}` | no (only used in tests) | yes |
| `GET /v1/models` | model list | yes | yes |
| `POST /v1/systemone` | typed decisions: state + questions -> calibrated probabilities | yes | yes |
| `POST /v1/chat/completions` | OpenAI-style text generation, model `diffusiongemma-26b`, `stream` supported | yes | yes |
| `GET /openapi.json`, `GET /docs`, `GET /redoc` | FastAPI auto docs (schema covers `/health`, `/v1/models`, `/v1/systemone`, `/v1/chat/completions`) | no | yes |

Everything else is 404 (`/v1/health`, `/v1`, `/metrics` -> `{"detail":"Not Found"}`). Wrong verbs are
405 `{"detail":"Method Not Allowed"}` (`GET /v1/systemone`, `HEAD /health`, `DELETE /v1/models`,
`OPTIONS /v1/systemone`). No CORS headers: a browser preflight gets 405, so a browser client needs
a proxy. There is no per-request batch endpoint, no embeddings endpoint, no `/v1/completions`.

Only three real operations exist for an SDK to expose: **list models**, **decide** (systemone),
**generate** (chat). Everything else in the API is a parameter of `decide`.

Proposed, not implemented: `GET /v1/limits` (section 15), which exposes the effective limits and
the backend so that clients do not hard-code them.

---

## 3. `GET /health`

Verified live: yes. Latency ~1 ms.

```
GET /health
-> 200 {"status":"ok"}
```

Notes: does not tell you the model is loaded in any deeper sense than "the process answers". On this
server the model loads before the port opens (warmup), so a 200 means reads will work.

## 4. `GET /v1/models`

Verified live: yes. Latency ~1 ms. No auth on this server.

```
GET /v1/models
-> 200
{"models":[
 {"name":"openjev-latest","description":"Alias for the newest OpenJev release. Currently openjev-0.1.","release_date":"2026-09-18"},
 {"name":"openjev-0.1","description":"OpenJev 0.1: DiffusionGemma 26B-A4B (NVFP4) on vLLM's structured reads.","release_date":"2026-09-18"},
 {"name":"diffusiongemma-26b","description":"DiffusionGemma 26B-A4B (NVFP4) text generation at POST /v1/chat/completions.","release_date":"2026-09-18"}]}
```

Constraints and quirks:

- The list does not include `jev-latest` / `jev-preview` even though they are accepted by
  `/v1/systemone`. It does not include `diffusiongemma` (chat alias) either.
- The descriptions say "NVFP4" and "vLLM's structured reads" although this server runs the MLX 4-bit
  build. The text is static, not derived from the running backend.
- A routed model (`OPENJEV_MODEL_ROUTES`) is listed even if its container is stopped; requests to it
  then get 503. Not testable here (no routes configured). Verified live: no.
- Use it as a capability probe: `openjev-latest` present means `decide` works; `diffusiongemma-26b`
  present means `chat` works.

---

## 5. `POST /v1/systemone`

The decision endpoint. Send a `state` and a dict of typed `questions`; get one answer object per
question, keyed by the same ids, in the same order.

### 5.1 Reference request and response (Verified live: yes, 410 ms first call, 250 ms repeat)

```json
POST /v1/systemone
{"model":"openjev-latest",
 "state":"Everything is down and we have a demo with our biggest client at noon.",
 "questions":{
  "urgent":{"type":"noul","instructions":"Does the customer need a reply within the hour?"},
  "team":{"type":"choice","instructions":"Which team should handle it?",
          "criteria":{"outage":"service down","billing":"charges, refunds","feature":"requests, how-to"}},
  "tone":{"type":"score","instructions":"How upset is the customer?","criteria":["calm","annoyed","furious"]}}}
```

```json
200 server-timing: model;dur=0.0, server;dur=410.6, total;dur=410.6
{"model":"openjev-0.1",
 "answers":{
  "urgent":{"type":"noul","noul":0.9970309924636945},
  "team":{"type":"choice","choice":"outage",
          "probabilities":{"outage":0.99999606,"billing":3.33e-06,"feature":6.09e-07},"confidence":0.99995},
  "tone":{"type":"score","score":1.99967,"legend":{"0":"calm","1":"annoyed","2":"furious"},
          "probabilities":{"0":7.98e-05,"1":0.000171,"2":0.999749},"confidence":0.99773}},
 "usage":{"input_tokens":165,"output_tokens":0}}
```

Properties confirmed live:

- `model` in the response is always the wire name `openjev-0.1`, whichever alias was sent.
- `answers` keys keep the request's question order (also for odd ids: empty string, spaces, unicode,
  300-char ids, an id literally named `q1`; all worked. Ids never reach the model).
- **Deterministic**: the noise seed is a hash of `(state, questions, images)`. Three identical
  requests gave byte-identical bodies. Changing `state` by one trailing space changes the result.
  `steps`, `samples`, `think`, `sequential` are not part of the seed.
- Unknown extra top-level fields (`temperature`, `foo`) are ignored (200).
- `state` may be a string, an object, or an array (objects/arrays are `json.dumps`-ed into the
  prompt). A number, boolean or null is a 422. An empty string state is accepted (200).
- `instructions` and every `criteria` value may be a string, object, array or null; non-strings are
  JSON-serialized into the prompt. Score `legend` echoes the original JSON values unchanged.
- Unicode works (`"Račun je dvaput naplaćen"` -> noul 0.988 on "is this billing?").

### 5.2 Question types

#### `noul` (yes/no) - Verified live: yes

```json
{"type":"noul","instructions":"Is this urgent?"}
{"type":"noul","instructions":"Is this urgent?","criteria":{"true":"needs a reply within the hour","false":"can wait"}}
```

Response `{"type":"noul","noul":P(yes)}` with P in [0,1]. There is no `confidence` field for noul.

| Variant | Result on the test state | Tokens | Latency |
|---|---|---|---|
| no criteria | 200, noul 0.99960 | 91 | 355 ms cold, ~260 |
| `criteria` with both `true`/`false` | 200, 0.99798 | 103 | 269 ms |
| `criteria` with only `true` | 200, 0.99510 | 98 | 268 ms |
| `criteria` values as JSON objects/arrays | 200, 0.99998 | 121 | 148 ms |
| no `instructions` at all | 200, 0.428 (unfocused; the model gets the placeholder "Answer about the state.") | 92 | 259 ms |
| `criteria` as a list | **422** `model_attributes_type` at `loc [body,questions,<id>,noul,criteria]` | - | 1 ms |

`instructions` is optional for all types in the schema, but a question without instructions is
close to meaningless; the SDK layer should require it.

#### `choice` - Verified live: yes

```json
{"type":"choice","instructions":"Which team?","criteria":{"outage":"service down","billing":"charges"}}
```

Response `{"type":"choice","choice":<top key>,"probabilities":{<key>:p,...},"confidence":c}`; the
probabilities sum to 1; `confidence = 1 - H(p)/ln K`.

| Variant | Result | Tokens | Latency |
|---|---|---|---|
| 1 option | answered locally ("forced"): `{"choice":"only","probabilities":{"only":1.0},"confidence":1.0}` | **0** | **1 ms** (no model call) |
| 2 options | 200, outage 0.99906, confidence 0.989 | 101 | 267 ms |
| 10 options | 200, outage 0.99920, conf 0.9967 | 169 | 306 ms |
| 255 options | 200 (7.5 KB body, top option `o40` on a meaningless question) | large | **2785 ms** |
| 256 options | **400** `{"detail":"Too many choices. Must have at most 255 choices."}` | - | 1 ms |
| 0 options (`criteria:{}`) | **400** `{"detail":"Choice question must have at least one choice: c"}` | - | 1 ms |
| descriptions `null` | 200 (only the keys are shown to the model) | 94 | 259 ms |
| descriptions as JSON objects/arrays | 200 | 115 | 272 ms |
| `criteria` missing | **422** `missing` at `loc [body,questions,<id>,choice,criteria]` | - | 1 ms |
| `criteria` as a list | **422** `dict_type` | - | 1 ms |

Option labels are the *keys*; the model sees single-token letters `A..Z, a..z, AA..` (255 verified
by `choice_labels` computed from the real tokenizer). Keys therefore only matter as output names;
put the meaning in the descriptions.

#### `score` - Verified live: yes

```json
{"type":"score","instructions":"How upset is the customer?","criteria":["calm","annoyed","furious"]}
```

Response `{"type":"score","score":Σ i·p_i,"legend":{"0":<crit0>,...},"probabilities":{"0":p0,...},"confidence":c}`.
**Levels are 0-indexed** (`score` 1.9997 means level 2 "furious"). To get a 1-10 rating pass 10
levels and add 1 to `score` on the client.

| Levels | Result | Tokens | Latency |
|---|---|---|---|
| 1 | answered locally: `score 0.0`, `probabilities {"0":1.0}`, confidence 1.0 | **0** | **1 ms** |
| 2 | 200, score 0.9905, conf 0.922 | 101 | 269 ms |
| 3 | 200, score 1.987 | 108 | 271 ms |
| 5 | 200, score 3.709 (two neighbouring levels 0.275/0.719), conf 0.609 | 122 | 277 ms |
| 10 | 200, score 7.878, conf 0.725 | 157 | 294 ms |
| 11 | **400** `{"detail":"Too many score levels. Must have at most 10 levels."}` | - | 1 ms |
| 0 (`[]`) | **422** `too_short` at `loc [body,questions,<id>,score,criteria]` | - | 1 ms |
| JSON-object levels | 200, `legend` keeps `{"level":"low"}`, `["crit","p0"]` verbatim | 114 | 272 ms |
| `criteria` missing | **422** `missing` | - | 1 ms |
| `criteria` as dict | **422** `list_type` | - | 1 ms |

The score is an *expected value*, not an argmax. A bimodal distribution yields a mid score with low
confidence (score 3.7 above sits between levels 3 and 4). Read `probabilities` for the shape.

#### Unknown / missing `type`

- `{"type":"rank",...}` -> **400** `{"detail":{"error_type":"api_usage_error","message":"Invalid request."}}`
- no `type` -> **422** `union_tag_not_found`, `msg` "Unable to extract tag using discriminator 'type'".

### 5.3 Extension fields

All optional; SDKs from TypeSafe never send them.

#### `images` - Verified live: yes

Two forms, freely mixed in one array:

```json
"images":["data:image/jpeg;base64,/9j/4AAQ...", {"content_type":"image/jpeg","base64":"/9j/4AAQ..."}]
```

Measured with `tests/data/hotdog.jpg` (384x188, 12,860 bytes):

| Case | Result | Tokens | Latency |
|---|---|---|---|
| data URL, hotdog/cat noul | 200, hotdog 0.9982, cat 0.00028 | 355 | 728 ms cold, 191 ms warm |
| `{content_type, base64}` | 200, identical numbers | 355 | 191 ms |
| 2 images (one of each form) | 200 | 610 | 1202 ms |
| 8 images (the cap), default | 200 | 2140 | **4201 ms** |
| 8 images, `samples:1` | 200 | 2140 | 77 ms (prefill cached) |
| PNG / WebP / GIF conversions | 200 (hotdog 0.999 / 0.997 / 0.992) | 355 | ~710-740 ms |
| JPEG bytes declared `image/png` | 200 (type is only checked against the allow-list, not sniffed) | 355 | 709 ms |
| 2048x1536 JPEG | 200 | 368 | 815 ms |
| 16x16 PNG | 200 | 358 | 824 ms |
| `images:[]` or `images:null` | 200, treated as no images | text only | - |
| `steps:4` with an image | 200 | 355 | 207 ms |
| `samples:1` with an image | 200 | 355 | 49 ms warm, ~584 ms with a new text |
| `think:128` with an image | **400** `{"detail":"think needs a text state; send images without it"}` | - | 1 ms |
| `sequential:true` with an image | **400** `{"detail":"sequential needs a text state; send images without it"}` | - | 1 ms |
| `think:0` / `sequential:false` with an image | 200 (only a truthy value conflicts) | 355 | 191 ms |

Image token cost: baseline text (2 noul, state "Look at the photo.") is 99 tokens; one image adds
**256**, two add 511, eight add 2041, i.e. ~255-256 per image (269 for a 2048x1536 image). The README
says "about 280".

Image validation (all before the model runs, 1-20 ms):

| Input | Status + body |
|---|---|
| 9 images | 400 `{"detail":"at most 8 images per request"}` |
| string not `data:...;base64,...` (plain text, `https://` URL) | 400 `{"detail":"an image is a data:image/...;base64 string or a {content_type, base64} object"}` |
| `data:image/svg+xml;base64,...` | 400 `{"detail":"image type 'image/svg+xml' is not supported; use JPEG, PNG, WebP or GIF"}` |
| `application/pdf` in object form | 400 same "not supported" message |
| `image/jpg`, `IMAGE/JPEG`, `image/jpeg;charset=utf-8` | 400 same "not supported" message. The allow-list is exact and case-sensitive: `image/jpeg`, `image/png`, `image/webp`, `image/gif` only |
| non-base64 payload (`@@@@`) | 400 `{"detail":"image data is not valid base64"}` |
| base64 field with a `data:` prefix inside the object form | 400 `image data is not valid base64` |
| object missing `base64` | 422, two-branch union error (`images.0.str` and `images.0.ImageObject.base64`) |
| > 5,242,880 bytes decoded (tested with 5 MB + 10) | 400 `{"detail":"image data is larger than the 5242880 byte limit"}` (rejected on encoded length, before decoding) |
| **valid base64 that is not an image, or a truncated JPEG, with an allowed type** | **500 `Internal Server Error` (text/plain, no request id)**: see section 12 |

#### `steps` (1-8) - Verified live: yes

More denoise passes per read, sharing one prefill. Same tokens, more GPU time. 3-question request:
steps 1 -> 252 ms, 4 -> 425 ms, 8 -> 543 ms (default reads). Values move probabilities toward more
extreme (noul 0.99703 -> 0.99996 at steps 4). `steps:0` and `steps:9` are 422 (`greater_than_equal`/
`less_than_equal`, `ctx.ge:1`/`ctx.le:8`).

#### `samples` (1-32) - Verified live: yes

Read N times with different noise and average; replaces the automatic re-reads.

| Setting | Latency (3q, cached prefill) | input_tokens |
|---|---|---|
| default (auto) | 250-420 ms | 165 |
| `samples:1` | **63 ms** | 165 |
| `samples:4` | 250 ms | 660 (4 x) |
| `samples:8` | 497 ms | 1320 |
| `samples:32` | 1988 ms | (32 x) |

Key facts: (a) `samples:4` returns **exactly** the default's numbers when the default triggered its
re-reads (both = 4 reads with the same seeds), so default == "1 read, plus 3 free re-reads if the
first read had entropy > 0.1"; (b) re-reads under default are **not billed**, `samples` are;
(c) `samples:1` is 3-4x faster and is the right mode for latency-sensitive calls when the answer is
expected to be clear; (d) with an uncached prompt the prefill dominates: 3q, unique state,
`samples:1` measured 201 ms vs 321 ms p50 default (n=8); 1 noul: 147 vs 274 ms. `samples:0`/`33` -> 422.

#### `think` (0-4096) - Verified live: yes

The model writes a thought (hard cap = the number), then reads the answers after it.

| `think` | Latency | input_tokens | output_tokens |
|---|---|---|---|
| 0 (or absent) | 250 ms | 165 | 0 |
| 64 | 1006 ms | 405 | 64 |
| 256 | 2939-3907 ms | 556 | 215 (model stopped early) |
| 1024 | 3062 ms | 562 | 221 |
| 4096 | 3469 ms | 521 | 180 |
| 256 + `samples:1` | 2840 ms | 597 | 256 |
| 128 (p50 of 4, unique states) | 1636 ms (1475-2094) | - | - |

`output_tokens` = generated thought tokens, capped by `think`; the thought text is **not returned**
on MLX (documented). Input is billed twice (thought pass + read pass) plus the thought. On 30
questions `think:64` -> 2685 ms, 1652 in / 128 out (one thought per chunk; with `sequential:true`
one thought total: 2151 in / 64 out). `think:-1`/`4097` -> 422.

#### `sequential` (bool) - Verified live: yes

Only matters when the questions do not fit one canvas (section 9): chunks are read in order and each
chunk's chosen labels are written into the prompt before the next read.

30 noul questions: default (parallel chunks) 599 ms / 751 tokens; `sequential:true` 956 ms / 1361
tokens; `sequential + samples:1` 158 ms / 1361 tokens. With <= one chunk it is a no-op (identical
output to default). Non-bool value (`"maybe"`) -> 422 `bool_parsing`.

### 5.4 Chunking and question limits - Verified live: yes

- Hard cap: **256 questions** (`OPENJEV_MAX_QUESTIONS`). 257 -> 400 `{"detail":"at most 256 questions per request"}`. 256 questions with `samples:1` succeeded in 5396 ms, 7284 tokens.
- Reads are chunked by canvas (64 tokens), not by a fixed count. Computed with the real tokenizer:
  up to **10** questions use the "lines" format in one read (a 10-question all-noul request = 1 chunk;
  10 questions with 5-level scores split 9+1). From 11 questions on the compact "indexed" format is
  used: noul/choice/score chunks are **17, then 14, 14, 14..., shrinking to 11** as the `q<N>` ids grow
  to 3 digits (256 noul -> 22 chunks). README's "about 12 per read" is an average; plan on 10-17.
- Latency: 10q 294 ms, 12q 290 ms, 24q 868 ms, 30q 599 ms, 60q 1488 ms (default). Chunks run one after
  another on MLX (one runtime thread), so latency is ~ number of chunks x per-read time.
- Quality did not degrade across chunks for a parity test: 10/11/12/13/24/30/60 questions were all
  answered correctly (60/60).
- An answer template that cannot fit the canvas raises a 400 (`answer template is N tokens...`); not
  triggered in testing.

### 5.5 Context limits - Verified live: yes

- MLX prompt cap: `OPENJEV_MLX_MAX_PROMPT` = 32,768 tokens. A ~40k-token state -> **400**
  `{"detail":"the request is 40080 tokens; the limit is 32768"}` in 45 ms (checked before any model work).
  Image prompts are checked after the processor expands them.
- Prefill speed: a ~5.7k-token state took 4.48 s (`samples:1`) ~ 1.3k tokens/s. Budget accordingly;
  a state near the cap would take on the order of 20-25 s (extrapolated, not run, to spare the shared server).
- Prefill cache: an LRU of 16,384 tokens keyed by (system text, state, image digests). Identical
  or re-read prompts skip the prefill (63 ms vs 200 ms).
- `usage.input_tokens` counts prompt tokens including image tokens, summed over chunks and over
  billed `samples`; the automatic re-reads are free; forced (1-option) questions cost 0.

### 5.6 Request body size - Verified live: yes

Body cap 64 MiB (`OPENJEV_MAX_BODY_BYTES`); a 70 MB body -> **413**
`{"detail":{"error_type":"api_usage_error","message":"request body is larger than 67108864 bytes"}}`
in 2 ms, with `x-request-id` but no `server-timing`. Applies to `/v1/chat/completions` too. (Each
image is capped at 5 MB decoded, 8 images = ~40 MB of base64 < 64 MiB.)

---

## 6. `POST /v1/chat/completions`

OpenAI-style generation from the same DiffusionGemma. Model `diffusiongemma-26b` (alias
`diffusiongemma`). `openjev-latest` here is a 404. No auth on this server.

### 6.1 Non-streaming (Verified live: yes; 190-330 ms for short replies, ~1 s for 25 words)

```json
POST /v1/chat/completions
{"model":"diffusiongemma-26b","max_tokens":64,"messages":[{"role":"user","content":"What is 2+2? Answer with one number."}]}
-> 200
{"id":"chatcmpl-88ba6e9f...","object":"chat.completion","created":1790702968,"model":"diffusiongemma-26b",
 "choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"4"},"logprobs":null}],
 "usage":{"prompt_tokens":25,"completion_tokens":1,"total_tokens":26}}
```

Field behavior observed:

| Request field | Behavior |
|---|---|
| `messages` (system / user / assistant multi-turn) | works; content may be a string or a list of `{type:text}` parts |
| `max_tokens` | default 1024 when absent; capped silently at 8192 (`max_tokens:99999` -> 200, no error); must be a positive integer; `max_completion_tokens` is accepted as a fallback |
| `stop` | honored only for single-token stop strings: `stop:["3"]` on "count 1 to 10" returned `"1 2 "` (`finish_reason: stop`) |
| `stream` / `stream_options` | see 6.2 |
| `response_format` `json_object` / `json_schema` | turned into a system instruction; the first JSON value in the reply is extracted and re-serialized. Observed `{"name": "Alice Vance", "age": 28}`, `{"a": 1, "b": 2}`, `["red", "blue", "green"]` (schema not enforced by decoding; it is only in the prompt) |
| `tools`, `tool_choice` | **ignored on MLX**: with a `get_weather` tool, the model answered in prose ("I do not have access to real-time information..."). No `tool_calls` ever appear |
| `logprobs`, `top_logprobs` | ignored on MLX (`"logprobs":null`) |
| `temperature`, `seed`, `n`, penalties, `min_p`, `logit_bias` | accepted and dropped; always one greedy choice (`temperature=0.0` inside) |
| `chat_template_kwargs.enable_thinking:true` | accepted; the thought is not returned (answer `"17 * 23 = 391"`) |
| image parts (`image_url`) in messages | **silently ignored on MLX**: asked "What food is in this image?" the model replied "Please provide the image you are referring to." Use `/v1/systemone` `images` for vision |

`finish_reason` is `stop` or `length` (`max_tokens:5` -> `length`, 5 completion tokens).
`usage.prompt_tokens`/`completion_tokens`/`total_tokens` present. `id` = `chatcmpl-<24 hex>`.
`server-timing` reports total time (model dur is 0.0 on MLX).

### 6.2 Streaming (Verified live: yes)

`stream:true` -> `200 text/event-stream`, `transfer-encoding: chunked`, then SSE lines:

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"model":"diffusiongemma-26b","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null,"logprobs":null}]}
data: {... "delta":{"content":"1."} ...}
data: {... "delta":{"content":" Red2."} ...}
data: {... "delta":{}, "finish_reason":"stop" ...}
data: {... "choices":[], "usage":{"prompt_tokens":17,"completion_tokens":11,"total_tokens":28}}
data: [DONE]
```

- The final usage chunk is **always** sent (the server forces `include_usage: true`), not only when
  requested.
- Time to first byte ~1 ms (the role chunk is emitted before generation); the model denoises whole
  blocks, so content arrives in bursts. `server-timing` on a stream covers only the headers
  (0.6 ms), not the generation.
- If the prompt is over the 32,768-token cap the 400 is returned as normal JSON *before* the
  stream opens (verified).
- Client disconnects cancel generation at the next block (source; not exercised live).

### 6.3 Chat errors - Verified live: yes (except 529/503)

Chat errors use the **OpenAI** shape `{"error":{"message","type","code"}}`, not Jev's `{"detail":...}`.

| Trigger | Status | Body |
|---|---|---|
| invalid JSON body | 400 | `{"error":{"message":"The request body is not valid JSON.","type":"invalid_request_error","code":null}}` |
| body not an object / `messages` missing, empty, not a list | 400 | `messages must be a non-empty array.` |
| `model` missing / not a string | 400 | `model is required and must be a string.` |
| unknown model (`gpt-4`, `openjev-latest`) | **404** | `Model 'gpt-4' not found. Available: diffusiongemma-26b.` `code:"model_not_found"` |
| `max_tokens` 0, -5, `"10"`, 3.5, `true` | 400 | `max_tokens must be a positive integer, got <value>` |
| prompt over 32,768 tokens | 400 | `the request is 40013 tokens; the limit is 32768` |
| message without `role` | **500** plain text `Internal Server Error` (no request id): section 12 |
| message with a bogus role (`wizard`) | 200 (treated like a user turn) |
| over capacity (`running >= 8 + 32`) | 529 + `retry-after: 2` | Verified live: no (would need flooding) |
| body > 64 MiB | 413 | Jev-shaped `{"detail":{...}}` (middleware, not the OpenAI shape) |

Generation concurrency: `OPENJEV_GEN_MAX_INFLIGHT`=8 nominal, but the MLX runtime is one thread, so
generations queue behind each other and behind `/v1/systemone` reads.

---

## 7. Request field reference (`POST /v1/systemone`)

| Field | Type | Range / values | Default | Effect | Verified |
|---|---|---|---|---|---|
| `model` | string | `openjev-latest`, `openjev-0.1`, `jev-latest`, `jev-preview` | required | picks the model; unknown -> 400 `api_usage_error`. Response echoes `openjev-0.1` | yes |
| `state` | string \| object \| array | non-null; any size up to 32,768 prompt tokens | required | the text/data the questions are asked about. Objects and arrays are JSON-dumped. Empty string allowed | yes |
| `questions` | dict id -> question | 1..256 entries | required | ids are free-form strings, returned in order, never shown to the model | yes |
| `questions[id].type` | `"noul"`, `"choice"`, `"score"` | - | required | discriminator; other value -> 400, absent -> 422 | yes |
| `questions[id].instructions` | string \| object \| array \| null | - | null (shown as "Answer about the state.") | the question text | yes |
| `questions[id].criteria` (noul) | `{true?, false?}` (each string \| object \| array \| null) | - | null | describes what yes / no mean | yes |
| `questions[id].criteria` (choice) | dict option -> description (string \| object \| array \| null) | 1..255 keys (1 = forced answer, 0 = 400) | required | options; keys become output labels | yes |
| `questions[id].criteria` (score) | list of level descriptions (string \| object \| array) | 1..10 items (1 = forced, 0 = 422, 11 = 400) | required | ordered levels; output is 0-indexed | yes |
| `images` | list of data-URL strings or `{content_type, base64}` | 0..8 items, each <= 5,242,880 bytes decoded; types jpeg/png/webp/gif | null | images placed before the state; ~256 input tokens each | yes |
| `steps` | int | 1..8 | 1 | denoise passes per read; extra GPU time, same tokens | yes |
| `samples` | int | 1..32 | absent (= 1 read + up to 3 free re-reads if entropy > 0.1) | N billed reads averaged; `1` = fastest | yes |
| `think` | int | 0..4096 | 0 | thought budget in tokens; needs text-only state | yes |
| `sequential` | bool | true/false | false | chunks conditioned on earlier chunks' answers; needs text-only state | yes |
| any other key | - | - | - | ignored | yes |

Chat (`POST /v1/chat/completions`): `model` (required string; `diffusiongemma-26b`|`diffusiongemma`),
`messages` (required non-empty list), `max_tokens`/`max_completion_tokens` (positive int, default
1024, silently capped 8192), `stream`, `stream_options`, `stop`, `top_p`/`top_k` (passed through to the request; effect on MLX not tested, generation is greedy), `response_format`, `chat_template_kwargs`. Ignored on MLX: `tools`,
`tool_choice`, `logprobs`, `top_logprobs`, image parts.

## 8. Error reference

| Status | Content-Type / body shape | Trigger (verified live unless noted) |
|---|---|---|
| **422** | JSON `{"detail":[{"type","loc","msg","input","ctx?"}]}` (FastAPI list; `input` trimmed to 500 chars / depth 4 / 20 items) | missing `state`/`model`/`questions`; `questions:{}` (`too_short`) or a list (`dict_type`); `state` null/number (three union errors at `body.state.str`, `.dict[str,any]`, `.list[any]`); `model` not a string; choice/score without `criteria`; score `criteria:[]`; wrong-shaped `criteria`; missing question `type` (`union_tag_not_found`); `steps`/`samples`/`think` out of range; `sequential` not bool; image object missing a field; **invalid JSON** (`json_invalid`, `loc:["body",1]`); empty body (`missing` at `["body"]`); array body; valid JSON sent with a non-JSON content-type (`model_attributes_type`, `input` is the raw bytes repr) |
| **400** plain-detail | JSON `{"detail":"<message>"}` (string) - Jev's "plain text" reason, but served as `application/json` | choice with 0 options: `Choice question must have at least one choice: <id>`; `Too many choices. Must have at most 255 choices.`; `Too many score levels. Must have at most 10 levels.`; `at most 256 questions per request`; `at most 8 images per request`; bad image string / type / base64 / size (section 5.3); `think needs a text state; send images without it` (same with `sequential`); `the request is N tokens; the limit is 32768`; `the model rejected this request: ...` (upstream 4xx, vLLM only) |
| **400** `api_usage_error` | JSON `{"detail":{"error_type":"api_usage_error","message":"..."}}` | `Unknown model: <name>` (for `jev-1.13.0`, `gpt-4`, `diffusiongemma-26b`, `""`); unknown question `type` -> `Invalid request.` |
| **413** | JSON `{"detail":{"error_type":"api_usage_error","message":"request body is larger than 67108864 bytes"}}` | body > `OPENJEV_MAX_BODY_BYTES` (default 64 MiB; the number in the message is the configured value) (any `/v1/` POST). No `server-timing` |
| **401 / 403** | JSON `{"detail":{"error_type":"authentication_error"\|"permission_error","message"}}` | only when `OPENJEV_API_KEY` (403 missing header, 401 wrong key) / `OPENJEV_ORIGIN_SECRET` (403) is set. **Verified live: no** (auth is off here; covered by `tests/test_api.py`) |
| **404** | `{"detail":"Not Found"}` for unknown paths; chat unknown model uses the OpenAI shape with `code:"model_not_found"` | verified |
| **405** | `{"detail":"Method Not Allowed"}` | wrong verb; browser CORS preflight |
| **500** | `text/plain` `Internal Server Error`, no request id, no `server-timing` | undecodable image with an allowed content type; chat message without `role` (section 12) |
| **503** | `{"detail":{"error_type":"api_error","message":"inference backend unavailable: <ExcType>"}}`, `retry-after: 2` | vLLM/forward backend down. **Verified live: no** |
| **529** | `{"detail":{"error_type":"overloaded_error","message":"OpenJev is at capacity. Retry shortly."}}`, `retry-after: 1` (systemone) / OpenAI shape, `retry-after: 2` (chat) | queue full. **Verified live: no** (flooding a shared server) |
| **429** | README lists it; **the code never emits 429**. Rate limits, if any, live in a gateway (Codiv) | not observable here |

Client rule of thumb: 4xx except 429/529 is a client bug and must not be retried; 500 on an image
call means "re-encode the image"; 503/529 are retryable with the `retry-after` seconds.

Map errors on the pair (status, `detail.error_type`), not on the status alone. 403 has two meanings
in `openjev/api.py` `check_auth`: `authentication_error` "Must supply an API key!" means no
`Authorization` header while `OPENJEV_API_KEY` is set; `permission_error` "Direct access to this
origin is not allowed." means a missing or wrong `X-Origin-Secret` (`OPENJEV_ORIGIN_SECRET`). A
401 is always `authentication_error` (wrong key). 413, 400 `api_usage_error` and 529 also carry an
`error_type`. The 400 plain-string `detail`, the 422 list, 404 and the `text/plain` 500 do not.

## 9. Limits and capacity summary

| Limit | Value | Setting | Verified |
|---|---|---|---|
| Questions per request | 256 | `OPENJEV_MAX_QUESTIONS` | yes |
| Choice options | 255 (1 = forced, min 1) | fixed (`MAX_CHOICES`) | yes |
| Score levels | 10 (1 = forced) | fixed | yes |
| Questions per read (chunk) | 10 (lines format) then 17 / 14 / 11 (indexed format), canvas 64 tokens | `OPENJEV_CANVAS` | yes (computed with the real tokenizer, latency-consistent) |
| Images per request | 8 | `OPENJEV_MAX_IMAGES` | yes |
| Image size | 5,242,880 bytes decoded | `OPENJEV_MAX_IMAGE_BYTES` | yes |
| Image types | `image/jpeg`, `image/png`, `image/webp`, `image/gif` (exact, lowercase) | fixed | yes |
| Body size | 64 MiB | `OPENJEV_MAX_BODY_BYTES` | yes |
| Prompt size (MLX) | 32,768 tokens | `OPENJEV_MLX_MAX_PROMPT` | yes |
| `steps` / `samples` / `think` | 1-8 / 1-32 / 0-4096 | fixed | yes |
| Chat `max_tokens` | default 1024, cap 8192 | `OPENJEV_GEN_MAX_TOKENS` | cap yes (silent clamp); default 1024 from source only |
| MLX concurrency | 1 read at a time (single runtime thread) | - | source; timing-consistent |
| Waiting decisions before 529 | 512 | `OPENJEV_MAX_QUEUE` | no |
| Generations in flight / queued | 8 / 32 | `OPENJEV_GEN_MAX_INFLIGHT/QUEUE` | no |

These are the values of this server (DiffusionGemma, MLX, default environment). Most are
environment settings. The encoder backends differ (`ENCODER_MODELS` in `openjev/config.py`,
`openjev/encoders.py`): laya-1.0 is text-only with 1,024 tokens, verdict-1.4 takes 512 tokens and
at most 24 choices, clm-v0.1 2,048 tokens, jevk5-0.2 16,384 tokens. A model routed through
`OPENJEV_MODEL_ROUTES` has the limits of the container that serves it. No endpoint exposes any of
this today; section 15 proposes one.

### Observed latency (MLX, this machine, sequential)

| Request | Latency |
|---|---|
| health / models | ~1 ms |
| 1 noul, cached prefill, `samples:1` | ~50-65 ms |
| 1 noul, new state, `samples:1` | 147 ms |
| 1 noul, new state, default | 274 ms |
| 3 questions, new state, `samples:1` | 201 ms |
| 3 questions, new state, default | 321 ms p50 (202-390) |
| 3 questions, repeated request (prefill cached) | 250 ms default, 63 ms `samples:1` |
| forced (1-option choice / 1-level score) | 1 ms |
| 10 / 30 / 60 questions default | 294 / 599 / 1488 ms |
| 255-option choice | 2.8 s |
| 1 image (first sight) / warm | 0.7 s / 0.19 s |
| 8 images default / `samples:1` warm | 4.2 s / 77 ms |
| `steps:8` | 543 ms |
| `samples:32` | 2.0 s |
| `think` 128 / 256 | 1.6 s / 2.9-3.9 s |
| 5.7k-token state, `samples:1` | 4.5 s |
| chat, short reply | 190-330 ms; ~25-word reply ~0.7-1 s |

The README's ~0.4 s "typical read" is right for the default mode with a fresh state; 0.05-0.2 s is
reachable with `samples:1` and a warm prefill.

## 10. What the MLX backend does differently from vLLM

| Aspect | vLLM (README) | MLX (observed / source) |
|---|---|---|
| Reads in flight | up to 64 | 1; requests, samples, chunks and chat generations all serialize on one thread |
| `Server-Timing: model;dur` | model time summed over reads | **always `0.0`**; everything lands in `server` (= total). The MLX engine never records model time: `MlxEngine` bypasses `Engine._post`, where the timing lives. Do not infer the backend from this: it would flip once the gap is fixed (use `backend` from section 15) |
| Latency (3q) | 27-94 ms | 200-400 ms |
| Chat `tools` / `tool_choice` | supported (gemma4 tool-call parser) | ignored, never returns `tool_calls` |
| Chat `logprobs` | supported | ignored (`null`) |
| Chat thinking output | reasoning parser | thought never returned |
| Chat multimodal input | n/a in README | image parts ignored silently |
| Chat `stop` | strings | single-token stops only |
| Chat output text | normal | newlines are dropped (section 12) |
| Upstream 4xx | 400 `the model rejected this request` | not applicable; undecodable image -> 500 |
| Prompt cap | vLLM `--max-model-len` (65,536) | 32,768 (`OPENJEV_MLX_MAX_PROMPT`) |
| Prefill reuse | vLLM prefix cache | 16,384-token LRU keyed by exact (system, state, images) |
| Re-reads + `samples` | parallel | sequential, but share one prefill and one vision pass |
| `/v1/models` text | accurate | still says "NVFP4 ... on vLLM" |
| Bit-for-bit numbers | - | same prompts, canvases and seeds; probabilities not guaranteed identical to vLLM |

## 11. README vs observed behavior

| # | README says | Observed | Severity |
|---|---|---|---|
| 1 | "about 280 input tokens per image" | 255-256 per image (269 for a 2048x1536 image) | low |
| 2 | `Server-Timing` `model` is time spent on the model | `model;dur=0.0` on every response on MLX | medium for clients that log it |
| 3 | "chunks of about 12 per read" | canvas-driven: 10 in the lines format, then 17/14/11 | low |
| 4 | "`400` with a plain-text reason" | the reason is a JSON string in `{"detail":"..."}` with `application/json` | low, but clients must not parse text/plain |
| 5 | Error list: 401/403/429/529 | 429 is never emitted by the code; 401/403 only with auth on; 404 (chat unknown model), 405, 413, 500 are undocumented | medium |
| 6 | "`think` and `sequential` need a text state. A request that combines them with `images` gets a 400" | true only for truthy values; `think:0` and `sequential:false` with images are 200 | none |
| 7 | "`max_tokens` ... cap of 8192" | above the cap is silently clamped, no 400 | low |
| 8 | README API table lists 3 endpoints | `GET /health`, `/openapi.json`, `/docs`, `/redoc` also exist | low (helpful) |
| 9 | "The MLX backend ignores `tools` and `logprobs` and does not return the thought" | confirmed; **also** ignores image parts in chat and drops newlines from every reply (12.3), and can return empty replies (12.4). Not mentioned | high for chat users |
| 10 | "`GET /v1/models` ... `jev-latest`, `jev-preview` are accepted" | accepted by `/v1/systemone` (verified) but not listed; `diffusiongemma` chat alias is neither listed nor documented | low |
| 11 | Models table says `openjev-latest` runs on "text and images" and "vLLM or MLX" | true; but `/v1/models` descriptions still say NVFP4/vLLM | low |
| 12 | "`samples: 1` gives one read, the fastest answer" | confirmed (63 ms vs 250 ms); also confirmed default == 4 reads when the first has entropy > 0.1 | none |
| 13 | "Reads up to 64 in flight" for MLX table: "one at a time" | consistent; the `OPENJEV_MAX_INFLIGHT` setting does not raise MLX concurrency | none |
| 14 | "At most 8 generations run at once" | on MLX, one at a time; 8 only bounds the wait queue | low |
| 15 | Score "1-10 levels" | confirmed exactly (0 -> 422, 11 -> 400); note the 0-vs-11 asymmetry of status codes | low |

## 12. Bugs and surprises found

### 12.1 500 on undecodable images (`/v1/systemone`)
Any base64 payload that passes validation (`image/jpeg` etc., valid base64, under 5 MB) but is not a
decodable image (random bytes, a truncated JPEG) reaches `PIL.Image.open` on the MLX runtime thread,
raises `PIL.UnidentifiedImageError`, and surfaces as `500 Internal Server Error` (text/plain, no
`x-request-id`, no `server-timing`). The server survived (subsequent requests fine). Expected
contract: a 400 `{"detail":"image is not a decodable ..."}`. Client mitigation: decode/re-encode the
image (e.g. with PIL) before sending; treat a 500 on an image call as "bad image".
Reproduce: `images:["data:image/jpeg;base64," + base64("hello world not an image")]`.

### 12.2 500 on a chat message without `role`
`{"messages":[{"content":"hi"}]}` -> 500 plain text. Bogus role values (`wizard`) are accepted.
Client mitigation: always send `role`.

### 12.3 Chat replies lose every newline on MLX
`"1. Red2. Blue3. Green"` instead of a list on separate lines; code blocks come back as
```` ```pythondef add(a, b):    return a + b``` ````. Cause (verified): the MLX generator passes
`thought_open + thought_close` token ids as `skip_special_token_ids`; `thought_open` encodes to
`[<|channel>, thought, "\n"]` and token 107 (`\n`) is in that set, so every newline token is dropped
(streaming and non-streaming). The word "thought" survives (different token). Consequence: do not
use `/v1/chat/completions` for anything that needs line structure (code, lists, JSON pretty-print)
on this server; JSON mode still works because `extract_json` re-serializes.

### 12.4 Intermittent empty chat replies
Identical requests (`"Say hi in one word."`, `max_tokens` absent or 32) returned
`content:""`, `completion_tokens:0` five times in a row, then `"Hi!"`, and later
`"Hi"` x 6. Likely tied to the scaffold/thought-channel markers being generated first and then
skipped. A client must treat empty `content` with `completion_tokens: 0` as a retryable failure.
Also observed once: JSON mode lost its opening brace (`"  \"name\": ...}"`) and `extract_json`
could not repair it.

### 12.5 Other surprises
- `samples:N` and the default are the same code path: default = 1 read + 3 free re-reads if the
  entropy of the first read exceeds 0.1. Latency is therefore *bimodal*: confident questions are 4x
  cheaper than uncertain ones.
- A 1-option choice / 1-level score is answered without touching the model (1 ms, 0 tokens) and,
  in a mixed request, does not slow the rest.
- Answers are deterministic per request body; there is no way to get "another opinion" except
  changing the state text or using `samples` (which averages, it does not resample).
- `noul` has no `confidence`; derive it as `abs(2*noul-1)` if needed (not the server's definition).
- `Server-Timing` on streamed chat measures only time to headers.
- Score `confidence` is normalized by ln K; with 2 levels a `0.90/0.10` split has confidence 0.53.
- Very long option lists work but cost seconds: 255 options took 2.8 s and returned 7.5 KB.

### 12.6 Debug logging writes request bodies (from source, working tree of 2026-09-30)

With `OPENJEV_LOG_LEVEL=debug` (`mise run startOpenJevDebug`), the request middleware in
`openjev/api.py` logs every `/v1/` POST body before validation, and every JSON response body
(`body_for_log`: image base64 is replaced by a size marker, everything else is kept, capped at
`OPENJEV_LOG_BODY_CHARS`, default 20,000 characters). States are therefore written to
`.openjev.log`. So are bodies the server then rejects, although the comment above `log_invalid`
says "a rejected body is never logged"; that comment holds only at `info`. At `info`, one line per
request, with counts only. Consequence for clients: a debug-level server is not a place for real
data. The MCP spec's "states are hashed, not logged" applies to the MCP side only. Not verified
live (the change is uncommitted); covered by `tests/test_api.py::test_debug_log_has_request_and_response_bodies`.

## 13. curl cookbook (all executed against the live server)

```bash
B=http://127.0.0.1:8080

curl -s $B/health
curl -s $B/v1/models

# noul with criteria, fastest mode
curl -s $B/v1/systemone -H 'Content-Type: application/json' -d '{
 "model":"openjev-latest","samples":1,
 "state":"I was charged twice this month.",
 "questions":{"is_billing":{"type":"noul","instructions":"Is this a billing issue?",
   "criteria":{"true":"about charges, refunds, invoices","false":"anything else"}}}}'

# choice + score (0-indexed) + server timing
curl -s -D- $B/v1/systemone -H 'Content-Type: application/json' -d '{
 "model":"openjev-latest","state":"The invoice looks wrong again. Second time this quarter.",
 "questions":{
  "team":{"type":"choice","instructions":"Which team?","criteria":{"billing":"charges","outage":"service down","feature":"how-to"}},
  "tone":{"type":"score","instructions":"How upset?","criteria":["calm","annoyed","furious"]}}}'

# image (object form)
python3 - <<'EOF' | curl -s $B/v1/systemone -H 'Content-Type: application/json' -d @-
import base64,json
b=base64.b64encode(open("tests/data/hotdog.jpg","rb").read()).decode()
print(json.dumps({"model":"openjev-latest","state":"Look at the photo.","images":[{"content_type":"image/jpeg","base64":b}],
 "questions":{"hotdog":{"type":"noul","instructions":"The photo shows a hot dog"}}}))
EOF

# think (text only) - about 1-4 s
curl -s $B/v1/systemone -H 'Content-Type: application/json' -d '{"model":"openjev-latest","think":256,
 "state":"Everything is down and we have a demo at noon.",
 "questions":{"urgent":{"type":"noul","instructions":"Needs a reply within the hour?"}}}'

# chat (non-stream and stream)
curl -s $B/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"diffusiongemma-26b","max_tokens":64,
 "messages":[{"role":"user","content":"What is 2+2? Answer with one number."}]}'
curl -sN $B/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"diffusiongemma-26b","max_tokens":64,"stream":true,
 "messages":[{"role":"user","content":"Name a colour of the sky."}]}'
```

## 14. Verification log (summary of what was executed)

About 190 sequential requests, all against the shared server, none flooding (one 70 MB body twice
for the 413 test, one 256-question request, one 5.7k-token state). Groups: models/health/docs and
unknown paths and verbs; the README example; noul x6 variants; choice x9 variants incl. 255/256;
score x9 variants incl. 1-11 levels; 8 model-name cases; steps/samples/think/sequential x11 plus
token accounting for 15 combinations; 13 image cases (formats, sizes, forms, 8-image cap,
combinations) and 12 image error cases; 40+ validation-error cases; chunking at 7 sizes plus
tokenizer-based chunk computation; context cap; 413; auth headers ignored; 14 chat cases, 18 chat
error cases, streaming (with/without `stream_options`), newline and empty-reply reproductions;
latency series (n=4-8 each). Not verified live: 401/403 (auth off), 429 (never emitted), 503,
529, routed models, client-disconnect cancellation, encoder backends.

## 15. Proposed server additions (not implemented)

Proposed with MCP spec 1.1 (findings F5, F6, F10). Nothing here exists yet: today every path below
answers 404.

### 15.1 `GET /v1/limits`

The effective limits and the backend, so that clients stop hard-coding the defaults of section 9
and stop guessing the backend from `Server-Timing`. The endpoint is an OpenJev extension, outside
TypeSafe's contract, which Jev SDKs never call. It sits under `/v1/`, so it applies the same auth
as the other `/v1/` routes. It is read-only and cheap: the values come from `Settings` and the
engine, with no model call.

```json
{
 "backend": "mlx",
 "server_version": "0.5.0",
 "logs_bodies": false,
 "request": {"max_questions": 256, "max_images": 8, "max_image_bytes": 5242880,
             "max_body_bytes": 67108864, "image_types": ["image/jpeg", "image/png", "image/webp", "image/gif"],
             "steps": [1, 8], "samples": [1, 32], "think": [0, 4096]},
 "models": {
  "openjev-0.1": {"max_choices": 255, "max_score_levels": 10, "max_prompt_tokens": 32768,
                  "images": true, "think": true, "sequential": true, "routed": false},
  "verdict-1.4": {"routed": true, "url_host": "verdict"}
 },
 "capacity": {"max_inflight": 64, "max_queue": 512}
}
```

Field rules:

- `backend`: the `OPENJEV_BACKEND` value (`vllm`, `mlx`, `laya`, `verdict`, `clm`, `jevk5`).
- `logs_bodies`: true when the `openjev` logger is at debug level (section 12.6).
- `models`: every served name, aliases excluded (the MCP layer resolves those through `/v1/models`).
  A routed model reports `routed: true` and the host part of its URL only; the client may call the
  routed container's own `/v1/limits` for its limits, or treat them as unknown.
- `capacity`: informational only; clients must still handle 529.
- The endpoint never reports secrets (`api_key`, `origin_secret`) or full upstream URLs.

Implementation sketch: a route in `openjev/api.py` built from `settings` and `served_models`. The
per-model caps are the engine's `choice_labels` length (vLLM, MLX) and `max_choices` for the
encoders; the prompt cap is `mlx_max_prompt` on MLX, the upstream's max model length on vLLM.
Tests: one per backend in `tests/test_api.py`, with the stub engine.
