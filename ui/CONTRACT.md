# OpenJev UI: build contract

A single-page, ChatGPT-like web app for exploring a locally running OpenJev server.
Start it with `mise run start`. It serves on http://127.0.0.1:8090 and proxies to OpenJev on
http://127.0.0.1:8080.

Three builders implement this in parallel and do not see each other's work. **This file is
the only shared truth.** Where the contract is silent, decide for yourself inside your own
files. Never edit a file that another builder owns. If you need something the contract did
not give you, implement it privately in your own files.

Hard rules for everyone:

- Do not touch `openjev/`, `tests/`, `docs/`, `openspec/`, `README.md` or `pyproject.toml`.
  All new code lives under `ui/`. The one exception is the task wiring in `mise-tasks/`.
- No JS build step. Use native ES modules. Every import path is absolute from the site root
  (for example `/js/core/bus.js`).
- CDN imports come only from these pinned URLs. Each one must degrade gracefully when it fails
  to load, because the app must be fully usable offline, only less pretty:
  - `https://cdn.jsdelivr.net/npm/vanilla-jsoneditor@3.13.0/standalone.js` (ESM; exports
    `createJSONEditor`, `createAjvValidator`, `Mode`)
  - `https://cdn.jsdelivr.net/npm/vanilla-jsoneditor@3.13.0/themes/jse-theme-dark.css`
  - `https://cdn.jsdelivr.net/npm/marked@18.0.14/lib/marked.esm.js` (ESM; named export `marked`)
  - `https://cdn.jsdelivr.net/npm/dompurify@3.4.16/dist/purify.es.mjs` (ESM; default export)
- Load a CDN module with dynamic `import()` inside try/catch, with a 6 s timeout
  (`Promise.race`). Never use a top-level static import from a CDN.
- No frameworks and no chart libraries. Charts are hand-written inline SVG.
- No emojis in the UI. Use the inline SVG icons from `/js/core/icons.js`.
- Python: use only what is already in `.venv`: fastapi 0.141, starlette 1.7, uvicorn 0.54,
  httpx 0.28 and pytest 9. Add no dependencies.

---

## 1. File layout and ownership

```
ui/
  CONTRACT.md                 architect (this file; read-only for builders)
  README.md                   A   how to run, env vars, architecture sketch
  server.py                   A   proxy + static server
  tests/test_server.py        A   pytest against a fake upstream (httpx.MockTransport)
  static/
    index.html                B   shell markup, mount points, no-flash theme script
    favicon.svg               B
    css/app.css               B   design tokens, layout, base components
    css/viz.css               C   visualizers, builder, gallery, batch, stats, inspector
    js/main.js                B   boot sequence
    js/core/bus.js            B   event bus
    js/core/dom.js            B   h(), svg(), copy, download helpers
    js/core/icons.js          B   inline SVG icon set
    js/core/format.js         B   number/time/byte/cost formatting, uid()
    js/core/metrics.js        B   entropy, confidence, server-timing parse, answer summaries
    js/core/store.js          B   settings, IndexedDB conversations, request log, totals
    js/core/api.js            B   fetch wrapper, systemOne, chatStream, health, errors, recording
    js/core/router.js         B   hash router + view registry
    js/core/theme.js          B   theme apply/toggle
    js/core/toast.js          B   toasts
    js/core/modal.js          B   modal + confirm/prompt dialogs
    js/core/drawer.js         B   right-side drawer
    js/core/slash.js          B   slash-command registry + popup menu
    js/core/errors.js         B   error card rendering + upstream-down banner
    js/app/shell.js           B   topbar, health pill, layout toggles, keyboard shortcuts
    js/app/sidebar.js         B   conversation history
    js/app/thread.js          B   thread view (systemone + chat turns)
    js/app/composer.js        B   composer (state, images, options chips, send/stop)
    js/app/chat.js            B   diffusiongemma-26b chat mode: streaming, markdown
    js/app/settings.js        B   settings modal
    js/app/welcome.js         B   empty-conversation hero
    js/jev/index.js           C   barrel: the ONLY module B imports from C
    js/jev/charts.js          C   SVG chart primitives
    js/jev/renderers.js       C   per-type result visualizers, turn meta bar
    js/jev/builder.js         C   visual question builder
    js/jev/jsonedit.js        C   vanilla-jsoneditor wrapper + textarea fallback
    js/jev/questionEditor.js  C   builder/JSON tabs, the combined editor B mounts
    js/jev/validate.js        C   client-side question-set validation + JSON schema
    js/jev/templates.js       C   template data + gallery view
    js/jev/snippets.js        C   curl / Python typesafe_sdk / fetch / openai snippets
    js/jev/inspector.js       C   raw request/response inspector (drawer)
    js/jev/compare.js         C   compare view
    js/jev/batch.js           C   batch view
    js/jev/batchImport.js     C   batch file import: CSV/TSV/JSONL/JSON parsing, sniffing, merge (pure)
    js/jev/stats.js           C   stats dashboard view + per-conversation stats strip
    js/jev/slashCommands.js   C   C's slash commands
```

Builder C imports only from `/js/core/*.js` (B's exports, listed in §3). B imports from C only
through `/js/jev/index.js`, and only with a dynamic `import()` (see §3.10). Neither side imports
`/js/app/*` from C.

---

## 2. Backend: `ui/server.py` (builder A)

### 2.1 Run

- `mise run start` starts `.venv/bin/python ui/server.py` in the background (pidfile `.openjev-ui.pid`, log `.openjev-ui.log`) next to OpenJev and opens the browser; `mise run test` runs `ui/tests`. Tasks live in `mise-tasks/`.
- CLI flags: `--host`, `--port` and `--open` (open a browser tab after startup). Flags override
  the env vars.
- Env vars:
  - `OPENJEV_URL`: upstream, default `http://127.0.0.1:8080`, trailing `/` stripped.
  - `OPENJEV_API_KEY`: when set, injected as `Authorization: Bearer <key>`.
  - `OPENJEV_ORIGIN_SECRET`: optional; when set, sent as `X-Origin-Secret`.
  - `UI_HOST`: default `127.0.0.1`.
  - `UI_PORT`: default `8090`.
- On startup, print a banner that shows the UI URL, the upstream URL, whether auth is
  configured, and the result of one upstream `GET /v1/models`: `reachable (N models, 12 ms)` or
  `NOT reachable — start it with: mise run start`. The server must start even when the
  upstream is down.
- Structure: `create_app(openjev_url: str, api_key: str = "", origin_secret: str = "",
  static_dir: Path = <ui/static>, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI`.
  Tests pass `transport=httpx.MockTransport(...)`. `main()` parses args/env and calls
  `_Server(uvicorn.Config(app, host, port, log_level="warning")).run()`. Ctrl+C or SIGTERM exits cleanly with code 0 and no traceback; a second Ctrl+C more than 1 s later force-quits while requests are open (hint: "ojui: waiting for N open request(s)"); a failed startup exits with code 3.
- Log one line per proxied request to stdout:
  `POST /v1/systemone 200 312ms up=298ms req_abc…`.

### 2.2 Routes, in registration order

1. **`GET /ui/api/config`** returns 200:
   ```json
   {
     "uiVersion": "0.5.0",
     "openjevUrl": "http://127.0.0.1:8080",
     "proxyBase": "",
     "authConfigured": false,
     "defaults": {"model": "openjev-latest", "chatModel": "diffusiongemma-26b"},
     "limits": {"maxImages": 8, "maxImageBytes": 5242880, "maxQuestions": 256,
                "stepsMax": 8, "samplesMax": 32, "thinkMax": 4096,
                "chatMaxTokensDefault": 1024, "chatMaxTokensCap": 8192,
                "choiceMaxOptions": 255, "scoreMaxLevels": 10},
     "hints": {"start": "mise run start", "logs": "mise run logs",
               "status": "mise run status", "stop": "mise run stop"}
   }
   ```
   `proxyBase` is always `""`, meaning the browser calls the same origin.

2. **`GET /ui/api/health`** calls upstream `GET /v1/models` with auth and a 2.5 s timeout.
   It always returns 200 with:
   ```json
   {
     "ok": true,
     "checkedAt": 1759262400000,
     "upstream": {"url": "http://127.0.0.1:8080", "reachable": true, "status": 200,
                  "latencyMs": 3.1, "requestId": "req_…", "serverTiming": "model;dur=0.0, …",
                  "error": null, "errorType": null},
     "models": [{"name": "openjev-latest", "description": "…", "release_date": "…"}],
     "authConfigured": false,
     "proxy": {"uptimeS": 12.3, "requests": 42}
   }
   ```
   - `ok` is `reachable && status == 200`.
   - Connection refused: `reachable: false`, `errorType: "upstream_unreachable"`,
     `error: "<ExceptionName>: <msg>"`, `models: []`.
   - Timeout: `errorType: "upstream_timeout"`.
   - 401/403: `reachable: true`, `ok: false`, `errorType: "auth"`, and `error` set to the
     upstream's `detail.message`.

3. **`/v1/{path:path}`** for methods GET, POST, PUT, PATCH, DELETE and OPTIONS is a
   transparent passthrough to `OPENJEV_URL + "/v1/" + path + ("?" + query if query)`.
   - Request headers sent upstream: `content-type` and `accept` from the client, plus
     `accept-encoding: identity`.
   - Authorization: if the client sent `Authorization`, forward it unchanged. This lets the
     UI's "auth override" setting provoke 401/403. Otherwise, if `OPENJEV_API_KEY` is set,
     send `Bearer <key>`. Otherwise send none.
   - Send `X-Origin-Secret` if configured.
   - Body: the raw bytes from `await request.body()`.
   - Always use `client.send(req, stream=True)`. Return a `StreamingResponse(r.aiter_raw())`
     that closes the upstream response in a `BackgroundTask`. This is the same code path for
     JSON and SSE, so chat streaming works token by token.
   - Response status: the upstream's status, unchanged (200/400/401/403/413/422/429/503/529…).
   - Response headers: copy everything except hop-by-hop headers (`connection`,
     `keep-alive`, `transfer-encoding`, `te`, `trailer`, `upgrade`, `proxy-*`) and
     `content-length`/`content-encoding`. Then set:
     - `server-timing`: the upstream value with `, upstream;dur=<ms>` appended, where `<ms>`
       is the time from sending upstream until the upstream response headers arrive, with one
       decimal. If upstream sent no Server-Timing, the header is only `upstream;dur=<ms>`.
     - `x-ojui-upstream-ms: <ms>`
     - `cache-control: no-store`
     - `x-accel-buffering: no`
   - Upstream connect failure (`httpx.ConnectError`, `httpx.ConnectTimeout`) returns **502**:
     `{"detail": {"error_type": "upstream_unreachable", "message": "OpenJev is not reachable at <url>: <exc>", "hint": "mise run start"}}`
   - Read timeout returns **504** `upstream_timeout`. Any other `httpx.HTTPError` returns
     **502** `upstream_error`.
   - Timeouts: `httpx.Timeout(connect=3.0, read=900.0, write=120.0, pool=10.0)`. A `think`
     run on MLX can take minutes.
   - If the client disconnects mid-stream, the upstream response must be closed. Nothing may
     leak.

4. **Static files**: `StaticFiles(directory=static_dir, html=True)` mounted at `/` last.
   - Every static response gets `cache-control: no-store` through a small middleware. This is
     a dev tool and must not serve stale modules.
   - `.js` and `.mjs` must be served as `text/javascript`. Call
     `mimetypes.add_type("text/javascript", ".js")` before mounting.
   - Unknown paths return 404. There is no SPA fallback, because the router uses the hash.

### 2.3 Tests (`ui/tests/test_server.py`)

Use `fastapi.testclient.TestClient` or `httpx.ASGITransport`, with a fake upstream built from
`httpx.MockTransport`. Cover these cases:

- Passthrough of a 200 JSON response, with `upstream;dur=` appended to the upstream
  Server-Timing.
- A 422 passes through with its body unchanged.
- The key is injected when configured, and the client's Authorization wins when present.
- Upstream down returns a 502 with `error_type: upstream_unreachable`.
- Health returns `ok: true` and `ok: false` shapes.
- An SSE body arrives in multiple chunks.
- `/` serves index.html, and `.js` has content-type `text/javascript`.

---

## 3. Frontend interfaces

All frontend code is ES2022 and runs in current Chrome, Safari and Firefox. Modules export
named functions only; there are no default exports. Every async function from B's core
resolves; none rejects, except where noted.

### 3.1 DOM mount points (`index.html`, B)

```html
<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>OpenJev Playground</title>
  <link rel="icon" href="/favicon.svg">
  <script>/* no-flash: read ojui.settings.v1.theme, resolve 'system' via matchMedia, set data-theme */</script>
  <link rel="stylesheet" href="/css/app.css">
  <link rel="stylesheet" href="/css/viz.css">
</head>
<body>
  <div id="app" class="app">
    <aside id="sidebar" class="sidebar"></aside>
    <main id="main" class="main">
      <header id="topbar" class="topbar"></header>
      <div id="banner" class="banner" hidden></div>
      <section id="view" class="view"></section>
    </main>
    <aside id="drawer" class="drawer" hidden></aside>
  </div>
  <div id="modal-root"></div>
  <div id="toast-root"></div>
  <script type="module" src="/js/main.js"></script>
</body>
</html>
```

When the thread view is active, B renders this inside `#view`:

```html
<div class="thread-wrap">
  <div class="thread-header" id="thread-header"></div>   <!-- title, mode badge, Σ stats toggle -->
  <div class="conv-stats" id="conv-stats" hidden></div>  <!-- C: renderConversationStats() output -->
  <div class="thread" id="thread"></div>                  <!-- turns -->
  <div class="composer" id="composer">
    <div class="qeditor-panel" id="qeditor-panel">        <!-- collapsible; systemone mode only -->
      <div id="qeditor"></div>                            <!-- C: mountQuestionEditor() -->
    </div>
    <!-- state input, image tray, option chips, send/stop: B -->
  </div>
</div>
```

The thread column is max 920px wide and centered. The composer is sticky at the bottom.

### 3.2 Design tokens (`app.css`, B). C must use only these variables

Dark is the default. Light is set with `[data-theme="light"]` on `<html>`.

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg` | `#0b0d12` | `#f7f8fa` | page |
| `--bg-elev` | `#12151c` | `#ffffff` | cards, sidebar |
| `--bg-sunken` | `#07080c` | `#eef0f4` | code, inputs, chart wells |
| `--bg-hover` | `#1a1e27` | `#e8ebf0` | hover |
| `--fg` | `#e6e9ef` | `#15181e` | text |
| `--fg-muted` | `#9aa3b2` | `#5a6272` | secondary text |
| `--fg-faint` | `#5d6575` | `#9aa1ad` | axis labels, hints |
| `--border` | `#232836` | `#dde1e8` | borders |
| `--accent` | `#7c9cff` | `#3b5bdb` | primary accent |
| `--accent-2` | `#4fd1c5` | `#0f9d8f` | secondary accent (chat, streaming) |
| `--accent-fg` | `#0b0d12` | `#ffffff` | text on accent |
| `--ok` | `#3ecf8e` | `#1f9d62` | success, high confidence |
| `--warn` | `#f5b544` | `#b7791f` | medium confidence, warnings |
| `--err` | `#f06a6a` | `#c92a2a` | errors, low confidence |
| `--type-noul` | `#f59f6b` | `#d9650f` | noul chip/colour |
| `--type-choice` | `#7c9cff` | `#3b5bdb` | choice |
| `--type-score` | `#c58af9` | `#8e44d9` | score |
| `--font-sans` | `ui-sans-serif, system-ui, -apple-system, "Segoe UI", Inter, sans-serif` | same | |
| `--font-mono` | `ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace` | same | numbers, ids, JSON |
| `--radius` / `--radius-sm` | `12px` / `7px` | same | |
| `--shadow` | `0 8px 30px rgba(0,0,0,.35)` | `0 8px 30px rgba(20,30,60,.10)` | |
| `--gap` | `12px` | same | |

B also provides these base classes, which C reuses (C adds only `viz-*`, `qb-*`, `tpl-*`,
`batch-*`, `stats-*`, `insp-*` and `cmp-*` classes in `viz.css`):

`.btn`, `.btn.primary`, `.btn.ghost`, `.btn.danger`, `.btn.sm`, `.icon-btn`, `.chip`,
`.chip.active`, `.badge`, `.card`, `.input`, `.textarea`, `.select`, `.tabs`/`.tab`/`.tab.active`,
`.kbd`, `.mono`, `.muted`, `.faint`, `.num` (tabular-nums mono), `.table`, `.skeleton`
(shimmer), `.row` (flex, gap), `.col`, `.spacer`, `.scroll`, `.hidden`.

The look is geeky but beautiful: dense mono numerals, 1px borders, a subtle radial accent glow
behind the welcome hero, 150 ms transitions, and a visible focus ring
(`outline: 2px solid var(--accent)`).

### 3.3 `core/bus.js` (B)

```js
export const bus;                                  // EventTarget
export function emit(name, detail) {}              // dispatches CustomEvent(name, {detail})
export function on(name, fn) { return off; }       // fn(detail); returns unsubscribe
```

Event names. Every event carries a single `detail` object.

| Event | Detail | Emitted by |
|---|---|---|
| `oj:settings-changed` | `{settings, changed: string[]}` | store |
| `oj:theme-changed` | `{theme, resolved: 'dark'\|'light'}` | theme |
| `oj:route` | `{name, params}` | router |
| `oj:conversations-changed` | `{}` | store (list add/rename/delete/pin/import) |
| `oj:conversation-updated` | `{id, conversation}` | store |
| `oj:turn-updated` | `{convId, turn}` | store (append/update of a turn) |
| `oj:request-done` | `{record}` (RequestRecord §4.6) | api |
| `oj:health` | `{health}` (§2.2 shape, or `{ok:false, proxyDown:true}`) | api |
| `oj:composer-load` | `Draft` (partial §4.4) + `{newConversation?: bool, submit?: bool}` | anyone → composer |
| `oj:composer-questions-changed` | `{questions, valid, errors}` | C question editor → composer |
| `oj:open-inspector` | `{convId, turnId}` | thread → C |
| `oj:toast` | `{message, kind?: 'info'\|'ok'\|'warn'\|'err', timeout?}` | anyone |
| `oj:templates-changed` | `{op: 'put'\|'delete'\|'hide'\|'sync', id?}` | store (user template saved/deleted, built-in hidden, another tab changed them) |

Handling `oj:composer-load`:

- If `newConversation` is set, create a systemone conversation first and navigate to it.
- Apply every provided field (`state`, `stateIsJson`, `questions`, `images`, `options`,
  `parentTurnId`, `title`).
- If `submit` is set, send immediately.

This event is how C loads templates and runs re-asks without importing B's app code.

### 3.4 `core/dom.js`, `core/icons.js`, `core/format.js`, `core/toast.js`, `core/modal.js`, `core/drawer.js` (B)

```js
// dom.js
export function h(tag, attrs = {}, ...children) {}   // attrs: class, style (obj|str), dataset, on<Event> fns, other → setAttribute; children: Node|string|number|null|arrays
export function svg(tag, attrs = {}, ...children) {} // same, SVG namespace
export function clear(el) {}
export async function copyText(text) {}              // → boolean; toasts "Copied"
export function download(filename, data, mime = 'application/json') {} // data: string|Blob|object(→JSON 2-space)
export function escapeHtml(s) {}
export function debounce(fn, ms) {}

// icons.js
export function icon(name, size = 16) {}             // → SVGElement, stroke=currentColor
// names: plus chat bolt trash edit copy download upload settings stats templates batch compare
// inspect image send stop sun moon monitor check x alert refresh chevron-down chevron-right
// menu search pin code json play sparkles clock cpu eye eye-off link grip  (unknown → circle)

// format.js
export function uid(prefix) {}          // `${prefix}_${Date.now().toString(36)}${random6}`
export function fmtInt(n) {}            // 12,345
export function fmtMs(ms) {}            // "0.8 ms", "42 ms", "1.24 s", "2m 03s"; null → "—"
export function fmtProb(p, d = 3) {}    // "0.998"
export function fmtPct(p, d = 1) {}     // "99.8%"
export function fmtBytes(n) {}          // "1.2 MB"
export function fmtCost(amount, currency = '$') {}  // "$0.000123" (adaptive precision), 0 → "$0"
export function fmtRelTime(ts) {}       // "just now", "5 min ago", "yesterday", date
export function fmtTokens(n) {}         // "1.2k", "3.4M"

// toast.js
export function toast(message, {kind = 'info', timeout = 2500} = {}) {}

// modal.js
export function openModal({title, body /*Node*/, actions /*[{label, kind, onClick}]*/, wide, beforeClose /*() => bool|Promise<bool>*/}) {} // → {close}
// beforeClose is awaited for Esc, backdrop and the X button (not an action's own close); false keeps it open.
// Esc from inside an open .qb-menu is left to the dropdown.
export async function confirmDialog(message, {danger} = {}) {}   // → boolean
export async function promptDialog(message, defaultValue = '') {} // → string|null

// drawer.js  (#drawer, slides in from the right, 560px, resizable, Esc closes)
export function openDrawer({title, render /*(el) => cleanup?*/}) {}
export function closeDrawer() {}
```

### 3.5 `core/metrics.js` (B). Pure functions; C depends on these exact semantics

```js
export const IMAGE_TOKENS_EST = 280;
export function parseServerTiming(header) {}
  // "model;dur=41.2, server;dur=2.8, total;dur=44.0, upstream;dur=46.1"
  // → {model:41.2, server:2.8, total:44.0, upstream:46.1, raw:header}; missing → null fields; null header → all null
export function entropy(probs) {}            // probs: number[]; Σ -p ln p (nats), ignores p<=0
export function entropyBits(probs) {}        // entropy / ln 2
export function normConfidence(probs) {}     // 1 - H/ln K ; K = probs.length; K<=1 → 1
export function noulProbs(p) {}              // [p, 1-p]
export function perplexity(probs) {}         // exp(H) = "effective number of options"
export function jsd(p, q) {}                 // Jensen–Shannon divergence in bits, aligned arrays
export function answerStats(answer, question) {}
  // → { type, probs: [{key, label, p}], top: key, topP, secondP, margin, confidence, entropyBits,
  //     perplexity, value }
  //   noul:   probs [{key:'yes',label:'yes',p},{key:'no',label:'no',p:1-p}], top 'yes'|'no',
  //           confidence = normConfidence([p,1-p]) (server does not send one), value = p
  //   choice: probs from answer.probabilities in server order, label = key, confidence = answer.confidence,
  //           value = answer.choice
  //   score:  probs keys '0'..'n-1', label = answer.legend[key] (stringified if non-string),
  //           confidence = answer.confidence, value = answer.score (expected level), plus
  //           mean = score, std = sqrt(Σ p (i-mean)^2), mode = argmax index
export function summarizeAnswers(answers, questions) {}
  // → [{qid, type, top, topP, confidence, entropyBits}] in answers' key order
export function estimateTokens(body) {}      // rough pre-send estimate: ceil(JSON chars of state+questions / 3.6) + 280·images
export function costOf(usage, settings) {}   // usage {input_tokens, output_tokens} or chat {prompt_tokens, completion_tokens}
  // → (in/1e6)·settings.pricePerMInput + (out/1e6)·settings.pricePerMOutput
export function percentile(values, p) {}     // p in [0,100], linear interpolation; [] → null
export async function sha256Hex(text) {}     // crypto.subtle; returns first 16 hex chars; fallback FNV-1a hex
```

### 3.6 `core/store.js` (B)

The settings cache is synchronous. Everything else is async and backed by IndexedDB.

```js
export async function initStore() {}           // opens IDB; falls back to in-memory + localStorage if IDB fails
export function getSettings() {}               // → Settings (§4.1), defaults merged
export function setSettings(patch) {}          // persists, emits oj:settings-changed; → Settings
export async function listConversations() {}   // → ConversationSummary[] pinned first, then updatedAt desc
export async function getConversation(id) {}   // → Conversation | null
export async function createConversation({mode = 'systemone', title} = {}) {} // → Conversation (options from settings defaults)
export async function saveConversation(conv) {}          // bumps updatedAt; emits conversation-updated + conversations-changed
export async function updateConversation(id, patch) {}   // shallow merge; → Conversation
export async function deleteConversation(id) {}
export async function appendTurn(convId, turn) {}        // emits oj:turn-updated; auto-titles an "Untitled" conv from first turn
export async function updateTurn(convId, turnId, patch) {} // shallow merge into turn; emits oj:turn-updated; → turn
export async function deleteTurn(convId, turnId) {}
export async function exportConversations(ids /* string[] | 'all' */) {} // → ExportFile (§4.7)
export async function importConversations(file /* ExportFile */) {}     // → {imported, skipped}; id collision → new id
export async function logRequest(record) {}              // stores RequestRecord; caps store at 5000 (drop oldest)
export async function listRequests({since = 0, limit = 5000} = {}) {} // → RequestRecord[] ts asc
export async function clearRequests() {}
export function getTotals() {}                           // → Totals (§4.6), sync from localStorage
export function addToTotals(record) {}                   // called by api.js only
export function resetTotals() {}

// user templates (§5.6): sync, localStorage, every change emits oj:templates-changed
export function listUserTemplates() {}           // → UserTemplate[] (clones), newest updatedAt first
export function getUserTemplate(id) {}           // → UserTemplate | null
export function saveUserTemplate(t) {}           // insert or update (uid('tpl') when no id); → saved clone.
                                                 // Throws on a missing title/questions, 200 templates, or a full localStorage
export function deleteUserTemplate(id) {}        // → boolean
export function getHiddenTemplateIds() {}        // → built-in ids the user hid
export function setTemplateHidden(id, hidden) {}
```

`exportConversations('all')` also carries `templates: UserTemplate[]`; `importConversations`
saves the ones whose id is not present yet.

Persistence keys:

| Where | Key | Owner | Content |
|---|---|---|---|
| localStorage | `ojui.settings.v1` | B | Settings |
| localStorage | `ojui.totals.v1` | B | Totals |
| localStorage | `ojui.lastConv` | B | last open conversation id |
| localStorage | `ojui.layout.v1` | B | `{sidebarCollapsed, qeditorOpen, drawerWidth}` |
| localStorage | `ojui.templates.v1` | B | `{v: 1, items: UserTemplate[], hidden: string[]}` (not IndexedDB: no version bump, no images) |
| localStorage | `ojui.batch.last.v1` | C | last batch job (inputs + results, images stripped) |
| localStorage | `ojui.compare.presets.v1` | C | user compare presets |
| localStorage | `ojui.stats.range.v1` | C | selected stats range |
| IndexedDB `ojui` (version 1) | store `conversations`, keyPath `id`, index `updatedAt` | B | Conversation |
| IndexedDB `ojui` | store `requests`, keyPath `id`, index `ts` | B | RequestRecord |

### 3.7 `core/api.js` (B). The only place that calls `fetch` on `/v1/*` and `/ui/api/*`

```js
export async function getConfig() {}      // → Config (§2.2); cached after first call; on failure returns built-in defaults with openjevUrl 'http://127.0.0.1:8080'
export function getCachedConfig() {}      // sync; null before first load
export async function health() {}         // → Health; emits oj:health; proxy unreachable → {ok:false, proxyDown:true, checkedAt}
export function startHealthPolling() {}   // every 10 s while ok, 4 s while down, immediately on window focus; emits oj:health on change
export function getLastHealth() {}        // sync

export function buildSystemOneBody(draft, options) {}
  // draft {state, stateIsJson, questions, images}; options {model, steps, samples, think, sequential}
  // → {model, state, questions, images?, steps?, samples?, think?, sequential?}
  //   state: stateIsJson ? JSON.parse(state) (throws SyntaxError — caller shows it) : state
  //   images: draft.images.map(i => i.dataUrl) only when non-empty
  //   steps/samples/think included only when not null; sequential included only when true

export async function systemOne(body, meta = {}) {}
  // meta: {signal, source: 'thread'|'compare'|'batch'|'template'|'other', convId, turnId, label}
  // → SystemOneResult:
  //   {ok, status, data /* response JSON or null */, error /* NormalizedError|null */, http /* HttpInfo */}
  // Records a RequestRecord (logRequest + addToTotals) and emits oj:request-done for every call, including errors and aborts.

export async function chatStream(body, {signal, onDelta, onUsage} = {}, meta = {}) {}
  // body: {model:'diffusiongemma-26b', messages, max_tokens, stream:true, response_format?}
  // Parses SSE ("data: {...}" lines, "data: [DONE]"); onDelta(textChunk, fullTextSoFar); onUsage(usage)
  // → ChatResult {ok, text, finishReason, usage /* {prompt_tokens, completion_tokens, total_tokens}|null */,
  //               error, http /* HttpInfo + {ttftMs, streamMs, chunks} */}
  // A non-2xx response is read as JSON and normalized. Records a RequestRecord with source 'chat'.

export async function listModels() {}     // GET /v1/models → {ok, models, error}
export function normalizeError({status, bodyText, headers, endpoint, exception}) {} // → NormalizedError (§4.5)
export const authOverride;                // read from settings.authOverride each call: when non-empty, send `Authorization: <value>`
```

`HttpInfo` is:

```js
{ status, requestId, serverTiming: {model, server, total, upstream, raw},
  clientMs, startedAt, requestBytes, responseBytes, retryAfter }
```

`clientMs` runs from just before `fetch` until the body has been read completely.

### 3.8 `core/router.js`, `core/theme.js`, `core/slash.js`, `core/errors.js` (B)

```js
// router.js — hash routes
//   #/                          → thread view of last conversation, or welcome if none
//   #/c/<convId>                → thread
//   #/templates                 → C view 'templates'
//   #/batch                     → C view 'batch'
//   #/stats                     → C view 'stats'
//   #/compare/<convId>/<turnId>[/<turnId2>] → C view 'compare'
export function registerView(name, {title, icon, mount /*(el, params) => unmount?*/}) {}
export function navigate(hash) {}
export function currentRoute() {}       // → {name: 'thread'|'templates'|'batch'|'stats'|'compare'|'welcome', params}
export function initRouter() {}

// theme.js
export function initTheme() {}          // reads settings.theme; listens to matchMedia; sets html[data-theme]
export function setTheme(theme /* 'system'|'dark'|'light' */) {}
export function resolvedTheme() {}      // 'dark'|'light'

// slash.js
export function registerSlash({name, args = '', description, run /* async (argString, ctx) */}) {}
  // ctx = {conversation /* current or null */, settings, composer: {getDraft, loadDraft, submit, clear}, navigate}
export function listSlash() {}
// Menu: opens when the state textarea's content starts with "/"; filters by prefix; ↑/↓, Enter/Tab picks, Esc closes.
// Enter on a complete "/cmd args" line runs it instead of sending.

// errors.js
export function renderError(error /* NormalizedError */, {onRetry, onFix} = {}) {} // → HTMLElement card (§6)
export function renderUpstreamBanner(health) {}  // fills #banner or hides it
```

### 3.9 Composer (`app/composer.js`, B). Its API is exported for slash `ctx` and B-internal use

```js
export function getDraft() {}           // → Draft (§4.4)
export function loadDraft(partial) {}   // same semantics as oj:composer-load
export async function submit() {}
export function clearComposer() {}
export function conversationStates(conv, draft, {includeDraft = true} = {}) {}
  // → {states, fromTurns, draftIncluded}: distinct systemone user states, oldest first
  //   (rerun turns skipped), then the unsent draft (JSON-parsed when it is a JSON draft); an empty
  //   `{}`/`[]` JSON draft (the blank JSON editor) is skipped
export function openConversationInBatch() {}
  // → boolean: hands the open decision to #/batch (§5.9); slash ctx.composer.openInBatch
```

The composer consists of:

- **Question editor panel**, systemone only. It is collapsible, and its header shows
  `N questions · noul 1 · choice 1 · score 1` plus a red dot when the set is invalid. The panel
  mounts C's `mountQuestionEditor` into `#qeditor`.
- **State input**:
  - An autogrowing textarea with a `Text | JSON` toggle. In JSON mode, use C's
    `mountJsonEditor` (text mode) with inline parse errors.
  - An image tray showing thumbnails, `name · 1.2 MB · ~280 tok`, and a remove control.
  - An attach button. Drag and drop onto the whole thread view works, and so does paste from
    the clipboard.
  - Accepted types are JPEG, PNG, WebP and GIF, with a maximum of 8 images.
  - An image larger than 5 MB is re-encoded to JPEG at quality 0.9 and at most 2048px on its
    longer side. Tell the user when this happens.
- **Option chips**, which edit `conversation.options` (§4.3):
  - `model ▾` (from health.models, excluding `diffusiongemma-26b`)
  - `steps 1..8|default`, `samples 1..32|default`, `think 0..4096|off`, `sequential`
  - `≈ N tok` (from `estimateTokens`)
  - A warning chip when images are combined with think or sequential. The server returns 400
    for that combination.
- **Send and stop**:
  - Enter sends, and Shift+Enter inserts a newline. Cmd/Ctrl+Enter always sends.
  - The Send button becomes Stop while a request is in flight (AbortController).
  - Esc stops a request in flight.
- **Chat mode**: textarea plus Send/Stop, a `max_tokens` chip, a `JSON mode` toggle (sends
  `response_format: {type: 'json_object'}`), and a `system prompt` chip that opens a modal.

Sending a systemone turn:

1. Build the body with `buildSystemOneBody`. Show a JSON parse error inline and stop.
2. Run `C.validateQuestions`. If it reports errors, show them inline with a **Send anyway**
   button, which sends the body unchanged so that the server's 400/422 can be explored.
   Otherwise, keep the draft and do not send.
3. Append a pending Turn, call `api.systemOne`, then update the turn with the response or error.
4. Keep the question set in the composer after sending, because repeated asking is the normal
   loop. State and images follow `settings.clearStateOn`:
   - `'answer'` (default): the text box clears once the answer arrives (`r.ok`). It is left alone
     when the user typed or changed images while waiting. Errors, stops and 4xx/5xx keep the
     text so it can be fixed and resent. If the user navigated away, the saved draft is cleared
     instead, as long as it still holds the sent state.
   - `'send'`: clear immediately on send.
   - `'never'`: keep the text.

### 3.10 C's barrel `/js/jev/index.js`. B loads it with `await import('/js/jev/index.js')` in try/catch

```js
export async function init() {}
  // registers views ('templates','batch','stats','compare') via registerView,
  // registers C slash commands, subscribes to oj:open-inspector. Idempotent.

export function mountQuestionEditor(el, {questions, onChange /*(questions, {valid, errors})*/}) {}
  // → QuestionEditorHandle:
  //   { get() → QuestionSet, set(questions), validate() → {valid, errors},
  //     highlightErrors(serverErrors /* 422 detail list */), setTab('builder'|'json'), focus(), destroy(),
  //     saveAsTemplate() → Promise<UserTemplate|null> }
  // Option templateContext?: () => {state, stateIsJson, batchStates, options, title} pre-fills
  // "Save as template" (composer: this conversation's states + draft; batch: the parsed job).
  // Also emits oj:composer-questions-changed on every change (debounced 150 ms).

export function mountJsonEditor(el, {value, mode = 'tree' /*'tree'|'text'|'table'*/, readOnly = false,
                                     schema = null, onChange /*(value, {valid, error})*/}) {}
  // → { get() → any (throws if invalid text), set(value), setMode(mode), isFallback: boolean, destroy() }
  // vanilla-jsoneditor when available, else a mono <textarea> with live JSON.parse lint showing line:col.

export function validateQuestions(questions) {}  // → {valid, errors: [{path: string[], message}]}

export function renderResult(turn, ctx) {}
  // turn.status === 'ok' only. ctx: {conversation, settings, turnIndex, parentTurn /* Turn|null */, compact}
  // → HTMLElement (summary strip + heat row + per-question cards; §5.3)

export function renderTurnMeta(turn, ctx) {}
  // any kind/status → HTMLElement: one-line mono meta bar (§5.5)

export function renderConversationStats(conversation, ctx) {} // → HTMLElement for #conv-stats

export function renderTemplateStrip(el, {limit = 6} = {}) {}   // cards on the welcome screen; click → oj:composer-load
export const templates;               // the 13 built-ins (read-only)
export function getTemplate(id) {}    // built-in (hidden ones too) or user template → Template | null
export function allTemplates({includeHidden = false} = {}) {} // user templates (newest first), then visible built-ins

export function openInspector({conversation, turn}) {}          // uses openDrawer

export function buildSnippet(kind /* 'curl'|'python'|'fetch'|'http'|'openai' */, turn, config) {} // → string

export function openInBatch(handoff) {}
  // one-shot handoff to #/batch, read once by the batch view (`takeBatchHandoff`). Handoff object:
  //   {title, questions, options?, batchStates?, stateIsJson?, batchInput?: {format, input},
  //    imageRefs?: ImageRef[], images?: 'demo-shapes', source?: 'conversation'|...}
  //   batchInput is used as is (format + textarea text); imageRefs apply to every state.
  //   B's fallback: navigate('#/batch').

export function turnToMarkdown(turn, {turnIndex} = {}) {}
  // → string: `**#<n> state**`, the state as a `>` blockquote (JSON in a ```json fence),
  //   a `| question | type | answer |` table (answerText long form), then `model · N in / M out tok`.
  //   B's fallback: JSON of {state, answers}.

export const questionTypes;  // ['noul','choice','score'] with {label, color var, description}
```

**B's fallback when the import fails**, so the core still works:

- `renderResult` becomes `<pre class="mono">` of `response.answers`.
- The question editor becomes a plain JSON textarea.
- Views 'templates', 'batch', 'stats' and 'compare' show "module failed to load" with the
  error.
- B shows one toast of kind `err`.

---

## 4. Data model

### 4.1 Settings (`ojui.settings.v1`)

```js
{
  theme: 'system',                 // 'system'|'dark'|'light'
  defaultModel: 'openjev-latest',
  defaultOptions: {steps: null, samples: null, think: null, sequential: false}, // null = omit (server default)
  chatModel: 'diffusiongemma-26b',
  chatMaxTokens: 1024,
  chatSystemPrompt: '',
  pricePerMInput: 0,               // currency per 1M input tokens
  pricePerMOutput: 0,              // currency per 1M output tokens
  currency: '$',
  authOverride: '',                // '' = let proxy decide; else sent verbatim as Authorization
  clearStateOnSend: false,         // deprecated: migrated on load (true → clearStateOn 'send'), read by nothing
  clearStateOn: 'answer',          // 'answer'|'send'|'never': when the composer's state box is emptied (§3.9)
  threadLayout: 'v2',              // 'v2' decision cards | 'classic' chat bubbles (§5.2); switches live
  jsonEditorMode: 'tree',
  questionEditorTab: 'builder',
  showRawByDefault: false,
  autoRetryOverloaded: true        // 529/429: auto-retry once after retry-after
}
```

### 4.2 QuestionSet

This is exactly the wire `questions` object:

```js
{ "<qid>": {type: 'noul',   instructions?: Described, criteria?: {true?: Described, false?: Described}},
  "<qid>": {type: 'choice', instructions?: Described, criteria: {"<option>": Described}},
  "<qid>": {type: 'score',  instructions?: Described, criteria: [Described, ...] /* 1..10 */} }
// Described = string | object | array | null. The builder edits strings; non-strings are preserved and
// shown as a "complex — edit in JSON" badge.
```

### 4.3 Conversation

```js
{
  id: 'c_…', title: 'Untitled', mode: 'systemone' | 'chat',
  createdAt, updatedAt, pinned: false,
  options: {model: 'openjev-latest', steps: null, samples: null, think: null, sequential: false}, // systemone
  system: '',                                                   // chat mode system prompt
  draft: Draft | null,                                          // unsent composer content, saved debounced 500 ms
  turns: Turn[]
}
// ConversationSummary = {id, title, mode, createdAt, updatedAt, pinned, turnCount, lastPreview}
```

The auto-title comes from the first turn: the first 48 characters of the state text (or
`JSON: <first key>`) for systemone, or of the user message for chat. A template's title wins
when a template created the conversation.

### 4.4 Draft

```js
{ state: '', stateIsJson: false, questions: {}, images: ImageRef[],
  options?: {model, steps, samples, think, sequential},   // when present, overwrites conversation.options
  parentTurnId?: string, title?: string }
// ImageRef = {id, name, type, bytes, width, height, dataUrl}
```

### 4.5 Turn

```js
// systemone
{
  id: 't_…', kind: 'systemone', createdAt,
  status: 'pending' | 'ok' | 'error' | 'aborted',
  source: 'thread' | 'compare' | 'batch' | 'template',
  label: null | string,                   // e.g. "steps=4" for compare-saved turns
  parentTurnId: null | string,            // "re-ask with edits" lineage
  request: {model, state, questions, images?, steps?, samples?, think?, sequential?}, // exact body sent
  imagesMeta: [{name, type, bytes, width, height}],
  response: null | {model, answers, usage: {input_tokens, output_tokens}},
  http: HttpInfo | null,
  error: NormalizedError | null,
  bodyHash: 'a1b2…',                      // sha256Hex(JSON.stringify(request)), for the reproducibility check
  labels: {}                              // {[qid]: {correct: boolean, expected?: any}}, set by C's feedback buttons
}
// answers[qid]:
//   noul   {type:'noul',   noul: P(yes)}
//   choice {type:'choice', choice, probabilities: {name: p}, confidence}
//   score  {type:'score',  score: Σ i·pᵢ, legend: {"0": level0, …}, probabilities: {"0": p, …}, confidence}

// chat
{
  id, kind: 'chat', createdAt, status, source: 'thread',
  user: 'text', assistant: 'text (streamed, final)', finishReason: 'stop'|'length'|null,
  request: {model, messages, max_tokens, stream: true, response_format?},
  usage: null | {prompt_tokens, completion_tokens, total_tokens},
  http: HttpInfo & {ttftMs, streamMs, chunks},
  error: NormalizedError | null
}
```

For chat, `messages` are rebuilt on each send in this order: the system prompt (if any), then
every earlier ok turn's user and assistant messages, then the new user message.

**NormalizedError**

```js
{ status: 0 | 400 | 401 | 403 | 413 | 422 | 429 | 502 | 503 | 504 | 529 | …,
  kind: 'validation'|'bad_request'|'auth'|'forbidden'|'too_large'|'rate_limited'|'overloaded'|
        'unavailable'|'upstream_down'|'timeout'|'network'|'aborted'|'client'|'unknown',
  title, message, errorType /* server error_type or null */,
  details /* 422: [{loc, msg, type, input}] ; else null */,
  requestId, retryAfter /* seconds|null */, hint /* string|null */, raw /* body text, truncated 20k */ }
```

The mapping below covers both `detail` shapes (a string, or `{error_type, message}`) and the
chat shape `{error: {message, type}}`:

| Condition | kind | title |
|---|---|---|
| 422, `detail` is a list | `validation` | Request failed validation |
| 400 | `bad_request` | The server can't ask this (for example, too many options, unknown model, images with think) |
| 401 | `auth` | API key rejected |
| 403 | `forbidden` | API key required or origin not allowed |
| 413 | `too_large` | Request body too large |
| 429 | `rate_limited` | Rate limited |
| 529 | `overloaded` | OpenJev is overloaded |
| 503 | `unavailable` | Inference backend unavailable |
| 502 with `upstream_unreachable` | `upstream_down` | OpenJev is not running |
| 504 | `timeout` | Upstream timed out |
| fetch TypeError | `network` | UI server unreachable |
| AbortError | `aborted` | Stopped |
| JSON state parse or client validation | `client` | Fix the request |

### 4.6 RequestRecord and Totals

```js
RequestRecord = {
  id: 'r_…', ts, endpoint: '/v1/systemone' | '/v1/chat/completions',
  source, convId, turnId, label,
  model, status, ok, errorKind,
  clientMs, serverTiming: {model, server, total, upstream},
  requestBytes, responseBytes,
  usage: {inputTokens, outputTokens},       // systemone: input_tokens/output_tokens; chat: prompt/completion
  imageCount, estImageTokens,               // imageCount·280
  questionCount, questionTypes: {noul, choice, score},
  options: {steps, samples, think, sequential},
  answers: summarizeAnswers(...) | null,    // systemone ok only
  chat: {ttftMs, streamMs, chunks, tokensPerSec} | null,  // tokensPerSec = completion_tokens / ((streamMs-ttftMs)/1000)
  bodyHash
}
Totals (ojui.totals.v1) = {
  since, requests, ok, errors, aborted,
  inputTokens, outputTokens, imageCount, estImageTokens, questions,
  clientMsSum, serverTotalMsSum, chatCompletionTokens, chatStreamMsSum,
  byModel: {[model]: {requests, inputTokens, outputTokens}},
  byStatus: {[status]: n}, byEndpoint: {[endpoint]: n}
}
```

Cost is always derived at render time from tokens and the current prices. It is never stored.

### 4.7 ExportFile

```js
{ format: 'ojui-export', version: 1, exportedAt, app: 'openjev-ui', conversations: Conversation[] }
```

Import accepts an ExportFile, a single Conversation, or an array of Conversations. The sidebar
offers export of all conversations or of one, and import from a `.json` file picker or dropped
file. Export includes images. A checkbox, "strip images", replaces each dataUrl with `null`.

---

## 5. Features

### 5.1 Shell (B)

- **Topbar**:
  - Sidebar toggle.
  - Conversation title (inline rename on double-click).
  - Mode badge: `System One` or `Chat · diffusiongemma-26b`.
  - Nav buttons: Templates, Batch, Stats.
  - Health pill:
    - green: `openjev-0.1 · 3 ms`
    - amber: `auth` or `slow`
    - red: `down`
    - Clicking the pill opens a popover with the upstream URL, models and auth state.
  - Theme cycle (system, dark, light).
  - Settings gear.
- **Sidebar**:
  - "New decision" (systemone) and "New chat" buttons, plus a search filter.
  - Groups: Pinned, Today, Yesterday, Last 7 days, Older.
  - Each row shows a mode icon, the title, and a mono `N turns`. On hover, a `⋯` menu offers
    rename, pin, duplicate, export, delete.
  - Delete asks for confirmation.
  - Footer: Import, Export all, and a totals line (`Σ 123 req · 45.6k tok · $0`) that links to
    `#/stats`.
- **Settings modal**:
  - Theme.
  - Default model, from `/v1/models` with descriptions.
  - Default steps, samples, think and sequential, each with an inline explanation taken from
    the README's Extensions table.
  - Chat defaults.
  - Prices per 1M input and output tokens, and currency.
  - Auth override, with a warning that it is sent through the proxy.
  - Read-only connection info: OpenJev URL, proxy origin, auth configured, UI version.
  - Danger zone: clear the request log, reset totals, delete all conversations.
- **Keyboard shortcuts**:

  | Shortcut | Action |
  |---|---|
  | `Cmd/Ctrl+K` | focus composer |
  | `Cmd/Ctrl+Shift+O` | new decision |
  | `Cmd/Ctrl+B` | sidebar |
  | `Cmd/Ctrl+J` | toggle Builder/JSON |
  | `Cmd/Ctrl+I` | inspect last turn |
  | `Esc` | stop, or close the drawer or modal |
  | `?` (outside inputs) | shortcuts modal |

- **Welcome** (empty conversation or no conversation): a hero with the tagline "Fast,
  calibrated, typed decisions", three one-click example chips (these call `oj:composer-load`),
  `renderTemplateStrip`, and a 3-line "how it works" explainer based on the README canvas
  diagram.

### 5.2 Thread (B)

**Systemone turn, v2 layout** (`settings.threadLayout: 'v2'`, the default): one decision card
per turn, so the prompt and its answers can be screenshotted or copied as one block.

```
div.turn.turn-systemone.turn-v2.status-<s>#turn-<id>[data-turn-id]
  article.dcard                    the card (screenshot region)
    header.dcard-head              #n · time · source / label / re-ask link … model + option badges
    section.dcard-prompt           accent rule on the left: "STATE [· JSON] [· N images]",
                                   the state (clamped, show more), image thumbnails, and the
                                   question chips until the answer is ok
    div.dcard-sep
    section.dcard-answers          pending skeleton | renderError | "Stopped" | renderResult
    footer.dcard-foot              renderTurnMeta
  div.turn-toolbar                 outside the article, so screenshots leave the buttons out
```

The toolbar is the same as the classic one, plus **Copy as text** on ok turns (both layouts):
it copies `C.turnToMarkdown(turn)` (§3.10). Changing `threadLayout` in Settings re-renders the
thread without a reload. In a systemone thread the header has a **batch** button (before
`stats`) that hands the decision to batch (§5.9). Once a turn reaches `status-ok`, the composer's state box clears
(setting `clearStateOn`, default `'answer'`, §3.9); failed or stopped turns keep the text.

**Systemone turn, classic layout** (`threadLayout: 'classic'`):

- The user bubble, right-aligned, shows:
  - the state as text, or as pretty JSON when it is an object;
  - image thumbnails;
  - a row of question chips (`qid` coloured by type);
  - option badges (`steps 4`, `samples 8`, `think 512`, `seq`);
  - a `↳ re-ask of #3` link when `parentTurnId` is set, and a label chip when `label` is set.
- The assistant block, left-aligned, depends on status:
  - pending: a skeleton with a live elapsed timer;
  - error: `renderError`;
  - aborted: a muted "Stopped";
  - ok: `renderResult`.
  - In every state, `renderTurnMeta` goes below.
- A hover toolbar on each turn offers:
  - **Edit & re-ask**: `loadDraft({...turn.request as draft, parentTurnId: turn.id})`
  - **Rerun**: the identical body. Because the seed is fixed, the answers must match; that is
    the reproducibility check.
  - **Compare…**: `navigate('#/compare/<conv>/<turn>')`
  - **Inspect**: emits `oj:open-inspector`
  - **Copy curl**, **Copy Python**: `buildSnippet`
  - **Copy JSON response**
  - **Copy as text** (ok turns): Markdown of the state and an answers table
  - **Delete turn**

**Chat turn**:

- User bubble, then the assistant text. While streaming, render Markdown incrementally with
  marked and DOMPurify, throttled to 50 ms. The fallback is escaped text with fenced code
  blocks in `<pre>`.
- A blinking caret while streaming, and a live `ttft 180 ms · 23.4 tok/s · 96 tok` counter.
  Before usage arrives, count chunks as approximate tokens.
- In JSON mode, the finished text is parsed and shown with `mountJsonEditor` read-only in tree
  mode.
- Code blocks have copy buttons.
- The toolbar offers Copy, Regenerate, Inspect, and **"Judge with System One"**. The judge
  action emits `oj:composer-load` with `{newConversation: true, state: assistantText, questions: <the 'rubric' template questions>}`.

The thread autoscrolls only while the user is already near the bottom. It renders its turns
again on `oj:turn-updated` for the current conversation, replacing only that turn's node.

### 5.3 Result visualizers (C, `renderResult`)

- **Summary strip**: `3 answers · mean conf 0.82 · min 0.66 (tone) · 106 in tok · 295 ms`.
- **Confidence heat row**: one 14×14 rounded square per question, coloured with
  `confColor(c)`. Hovering shows `qid · conf`; clicking scrolls to that card. `confColor`
  interpolates `--err` (0), `--warn` (0.5) and `--ok` (1) in OKLCH, or in RGB as a fallback.
- **Per-question card**:
  - Header: `qid` in mono, a type chip, and the instructions (muted, clamped to 2 lines, click
    to expand).
  - Footer: a confidence ring (a 28px donut coloured by `confColor`), `H = 0.41 bits`,
    `ppl 1.3`, `margin 0.84`, and correct/wrong feedback buttons.
  - Feedback writes `turn.labels[qid]` through `store.updateTurn`. For a choice or score, a
    "wrong" verdict opens a small picker for the expected option.
- **noul**:
  - A semicircle gauge from 0 to 1, with a needle at P(yes) and a shaded uncertainty band from
    0.35 to 0.65.
  - A big number `P(yes) 0.9998` and a verdict pill: `YES` if ≥ 0.65, `NO` if ≤ 0.35,
    otherwise `UNSURE`.
  - Log-odds `logit +8.29` (show `±∞` when p rounds to 0 or 1).
  - If the question has criteria, show "yes means: …" and "no means: …".
- **choice**:
  - Horizontal bars sorted by probability, descending. The top bar uses `--type-choice` at
    full opacity; the others use 35% opacity. Each bar shows the option name in mono, its
    description on hover, and the percentage to 1 decimal place.
  - Show `K options · top-2 margin`.
  - When there are more than 12 options, collapse to the top 8 plus "show all".
- **score**:
  - A vertical histogram with one bar per level. The x labels are the legend texts, truncated.
  - Overlay a vertical line at the expected value, with a marker and the label `E = 0.97`, on a
    continuous 0..K-1 axis. Also show `mode = annoyed · σ = 0.31` and a normalized bar
    `E/(K-1)`.
- **Delta vs parent**: when `ctx.parentTurn` is set, each matching `qid` shows the change,
  for example `Δ +0.12` for noul P(yes), top-choice probability or score E. Changes smaller
  than 0.01 are muted. A choice whose top option changed gets a `flipped` badge. Keys that
  exist only in the parent or only in the child get `new` or `removed` badges.
- **Compact mode**: when there are more than 12 questions, or `ctx.compact` is set, render a
  table instead of cards: `qid | type | answer | conf ring | mini bar`.
- **Thought**: when `usage.output_tokens > 0`, show a note: `think: 312 thought tokens (the
  server does not return the thought text)`.

### 5.4 Question editor (C)

It has two tabs, **Builder** and **JSON**, with a toolbar showing the question count, "Add
question ▾" (noul, choice, score), "From template ▾", "Paste JSON", and "Save as template".

- "From template ▾" lists the user's templates first (hint `mine · N q`), a separator, then
  the visible built-ins.
- "Save as template" needs a non-empty, valid question set (otherwise a warning toast). It
  opens the template editor (§5.6) in create mode, pre-filled with the questions plus the
  host's `templateContext()`: in the composer, the draft state, every distinct state of the
  conversation as batch states, the conversation options (never `model`) and its title. The
  same button shows in batch, pre-filled from the batch job.

**Builder**: one card per question with:

- a `qid` input (validated; duplicates are an error; the recommended pattern is
  `^[A-Za-z_][\w-]*$`, and anything else only warns);
- a type select, with a colour stripe on the card's left edge;
- an instructions textarea;
- up, down, duplicate and delete buttons, and collapse.

Criteria editors by type:

- **noul**: optional "true means" and "false means" inputs.
- **choice**: key/description rows with add, remove and reorder; "bulk paste" (one option per
  line, with `name: description` supported); a counter `7 / 255`.
- **score**: an ordered list of levels (1–10) with add, remove and reorder, and presets:
  - `low/medium/high`
  - `1–5 Likert`
  - `calm/annoyed/furious`
  - `no/partly/yes`
  - `0–10 (11 levels is invalid: show why)`

Type conversion keeps the instructions. Choice keys become score levels, score levels become
`{level: null}` choices, and noul drops its criteria after a confirm dialog.

**JSON tab**: `mountJsonEditor` with tree, text and table modes (the mode is persisted in
`settings.jsonEditorMode`). It uses the JSON Schema for a QuestionSet from `validate.js` through
`createAjvValidator` when that is available. Switching from JSON to Builder is blocked while
the JSON is invalid; show the parse error.

**Validation** (`validateQuestions`), mirroring the server:

- 1 to 256 questions.
- Each type is noul, choice or score.
- Choice criteria are a non-empty object with at most 255 options.
- Score criteria are an array of 1 to 10 levels.
- Noul criteria keys are only `true` and `false`.

Errors show inline on the right card and field. `highlightErrors` maps a 422 `loc`
(`["body","questions",qid,type,...rest]`) to the same spots.

### 5.5 Turn meta bar (C, `renderTurnMeta`)

This is a mono, `--fg-faint` line whose parts can be clicked or hovered:

`#4 · openjev-0.1 · 106 in / 0 out tok · +280 img est · $0 · 295 ms client · srv 295.0 (model n/a) · up 297.1 · 2.1 KB→0.4 KB · req_356e… · seed-stable ✓`

- Hovering over the latency shows a small waterfall bar (§5.7).
- **`model n/a`**: the MLX backend reports `model;dur=0.0`. When `model == 0` and
  `total > 5 ms`, show `model n/a` rather than 0.
- **`seed-stable`**: appears when another ok turn or request in the same conversation has the
  same `bodyHash`. Show `✓` if its answers are identical within 1e-9, otherwise `✗ drift` in
  `--warn`.
- **Chat**: `prompt / completion tok · ttft · tok/s · finish`.

### 5.6 Templates (C, `#/templates` + `renderTemplateStrip`)

Each template is:

```js
{ id, title, category, description, state /* string|object */, stateIsJson,
  questions, options?, images? /* 'demo-shapes' */, batchStates? /* string[] 5–10 */ }
```

The gallery is a grid of cards: a title, a category chip, question-type chips, a snippet of the
state, and three actions: "Use here" (loads the current conversation), "New conversation", and
"Open in Batch". There is a search box and category filters. There must be at least 12
templates, each with realistic content and `batchStates`:

1. `ticket-triage`: the README's urgent (noul), team (choice), tone (score) example.
2. `sentiment`: polarity (choice pos/neg/neutral/mixed), intensity (score 1–5), sarcasm (noul).
3. `pr-risk`: a JSON state `{title, files_changed, diff_summary, tests_touched}`; risk
   (score low/med/high/critical), area (choice), needs_security_review (noul),
   breaking_change (noul).
4. `shell-safety`: a state that is a shell command; destructive (noul), network_egress
   (noul), privilege (choice none/sudo/root), verdict (choice allow/confirm/block).
5. `log-actionability`: a log line; actionable (noul), severity (score 5 levels), component
   (choice).
6. `moderation`: harassment, self-harm, spam and PII as nouls, plus action (choice
   allow/review/remove).
7. `language-id`: language (a choice of about 20 languages), confidence_script (noul "Latin
   script?").
8. `intent-routing`: intent (a choice of about 10 intents, e.g. `billing.refund`,
   `account.reset_password`, `sales.upgrade`), needs_human (noul).
9. `image-yes-no`: `images: 'demo-shapes'`. C generates a 512×512 PNG on a canvas at load
   time: a red circle, a blue square and yellow text "42". Questions: red_circle (noul),
   triangle (noul), dominant_color (choice), count_shapes (score 0–4).
10. `rubric`: an answer to grade; correctness, completeness, clarity and concision as scores
    (5 levels each), plus a verdict choice. The chat "Judge" action uses this template.
11. `think-math`: a multi-step word problem; `options: {think: 512}`; answer (choice of 4
    numbers).
12. `sequential-chain`: 20 or more related questions that depend on each other;
    `options: {sequential: true}`.
13. `json-order` (optional): a JSON order object; fraud_risk, ship_priority, gift (noul).

**User templates.** Built-ins are read-only: they can be duplicated (into an editable copy)
or hidden. User templates can be edited, duplicated and deleted (after a confirm). They live
in localStorage (`ojui.templates.v1`, §3.6) with this shape:

```js
{ id: 'tpl_…', title /* 1..80 chars */, category /* lowercased, default 'mine' */, description,
  state /* string, or object when stateIsJson */, stateIsJson, questions /* ≥ 1 */,
  options? /* {steps?, samples?, think?, sequential?}, never model */,
  batchStates /* string[], JSON text when stateIsJson; ≤ 1000 */,
  batchFormat? /* 'jsonl' for a text template whose states need it */,
  images? /* 'demo-shapes', kept by a duplicate */, builtin: false, from? /* source id */,
  createdAt, updatedAt }
```

- The gallery has a "New template" button, user cards first with a `mine` badge and
  edit / duplicate / delete icon buttons, and a "hidden (N)" chip (only when N > 0) that lists
  the hidden built-ins with an Unhide button. It re-renders on `oj:templates-changed`.
- The editor modal (`jev/templateEditor.js`) has title, category (with suggestions),
  description, state (+ "JSON state"), batch states with a format select and a live
  "N states" count (`parseStates`), options, and a full question editor. Save stays open and
  shows the errors inline when the title, questions, JSON state or batch states are invalid.
  Esc, a backdrop click and the X button go through a `beforeClose` guard: with unsaved
  changes it asks "Discard the changes to this template?". Esc inside an open dropdown only
  closes the dropdown.
- "From template ▾", the batch template select and `/template` see user templates too.
- `renderTemplateStrip` shows up to 2 of the newest user templates before the built-ins.

### 5.7 Inspector (C, drawer via `oj:open-inspector` or `openInspector`)

Tabs:

- **Request**: `mountJsonEditor` read-only. Image data URLs are replaced by
  `"<image/png 123 KB — click to reveal>"` placeholders unless the "show image data" toggle is
  on.
- **Response**: read-only JSON, and the error `raw` when the turn failed.
- **Timing**:
  - A waterfall of horizontal segments on one time axis:
    - browser↔proxy: `clientMs − upstream`
    - proxy↔OpenJev: `upstream − total`
    - server: `server`
    - model: `model`, hatched "not reported" when it is n/a
  - Plus a table of raw Server-Timing, `x-request-id`, status, and request/response bytes.
- **Snippets**: sub-tabs for curl, Python (typesafe_sdk), JS fetch, raw HTTP, and OpenAI Python
  for chat. Each has a copy button and a "Download request.json" button.
- **Canvas**: an explainer of how OpenJev reads this request.
  - List the questions as the model sees them (`q1, q2, …`, since ids never go to the model),
    with each slot's label tokens: `yes/no`, `A/B/C…` mapped to option names, and `0/1/2…`.
  - Then an ASCII-style canvas diagram in the spirit of the README's, filled with this
    request's real probabilities.
  - Note that choices above 12 or questions above about 12 per read are chunked, and whether
    `sequential` was set.
- **Repro**: every request in the log with the same `bodyHash`, with its timestamps,
  latencies and whether its answers were identical.

**Snippet rules**:

- The base URL is `config.openjevUrl`, the direct upstream and not the proxy.
- When `config.authConfigured` is true, add `-H "Authorization: Bearer $OPENJEV_API_KEY"`.
- If the body is larger than 100 KB (images), curl uses `-d @request.json` with a comment.

Python snippet format:

```python
import os
from typesafe_sdk import TypeSafeClient

client = TypeSafeClient(base_url="http://127.0.0.1:8080",
                        api_key=os.environ.get("OPENJEV_API_KEY", "local"))
r = client.system_one(
    <state as a Python literal>,
    <questions as a Python literal>,
    model="openjev-latest",
    extra_body={"steps": 4},          # only the extension fields that are set, images included
)
print(r.nouls["urgent"].noul, r.choices["team"].choice, r.scores["tone"].score)  # per actual qids/types
```

The JSON-to-Python literal converter maps `true/false/null` to `True/False/None` and indents by
4 spaces.

### 5.8 Compare (C, `#/compare/<convId>/<turnId>[/<turnId2>]`)

- **With two turn ids**: show them side by side with no request made.
- **With one turn id**: the base is that turn's request.
  - Add 2 to 4 variants from presets or a custom override form (model, steps, samples, think,
    sequential, and "shuffle choice option order").
  - Presets:
    - `steps 1 vs 4`
    - `samples 1 vs 8`
    - `think 0 vs 512`
    - `models`: the base against each other System One model in `/v1/models`, excluding
      `diffusiongemma-26b` and aliases of the same model
    - `order sensitivity`: the base against 3 random permutations of every choice's criteria
      order. Probabilities are mapped back by option name.
  - "Run all" executes the variants sequentially, since MLX reads one at a time, through
    `api.systemOne` with `source: 'compare'` and `label` set to the variant's name.
- **Layout**: one column per run, with a header showing the variant label, latency, tokens and
  status. One row per `qid`:
  - noul: P(yes) dot plots on a shared axis;
  - choice: aligned bars per option, in the base's option order;
  - score: E markers on a shared axis.
  - Each row also has a divergence cell: the maximum JSD against the base in bits, coloured
    by magnitude, plus a `flipped` badge when the top answer differs.
- **Footer**: latency and token bars per run, and "Save runs to conversation", which appends
  turns with `source: 'compare'` and a `label`.

### 5.9 Batch (C, `#/batch`)

- **Left pane**:
  - A question set: its own `mountQuestionEditor` instance, prefilled from the last conversation
    draft or a template.
  - Options chips.
  - States input with a format selector:
    - `lines`: one state per line;
    - `blank-line blocks`;
    - `JSONL`: each line is a JSON state; objects can send one field, chosen in the column select
      (default `*`, the whole object);
    - `CSV / TSV`: comma, tab or `;` separated (detected from the header row); pick a column.
  - The state count, and an estimated token total.
  - **Upload file** in the States header, or drop files anywhere on the view (an overlay shows
    while dragging). Files are read in the browser (`js/jev/batchImport.js`); nothing is uploaded.
    - Accepted: `.jsonl/.ndjson`; `.txt/.log/.md` (lines, or blank-line blocks); `.csv/.tsv`;
      `.json` (an array, or an object such as `{states|items|…: […]}`); an `ojui-batch` export.
      Format comes from the extension, then the MIME type, then the content. Images in a drop go
      to the image tray. Conversation exports (`ojui-export`) are refused with a pointer to the sidebar.
    - A preview modal lists each file (format, states, skipped rows, first error, notes) and a
      Send column/field select, then **Replace** (Load when the input is blank) or **Append**.
      Append converts to JSONL unless the formats match or both are CSV with the same header.
      An `ojui-batch` export also offers **Restore batch** (questions, options and states), not
      while a run is going.
    - Limits: 5 MB per file and 5000 states (beyond the cap states are dropped, with a message).
      Binary files and spreadsheets (`.xlsx`, `.ods`, …) are refused. UTF-16 with a BOM and
      Windows-1252 are decoded.
- **Controls**: Run, Pause/Resume, Abort, and concurrency 1–4 (default 1). The progress bar
  shows done/total, errors, elapsed time, ETA and a live req/s figure.
- **Results table**:
  - One row per state: index, a state snippet, one column per question, and per-row latency
    and tokens.
  - Cell formats: noul `0.998`, choice `billing 97%`, score `E 1.03`.
  - Each cell is tinted by `confColor(confidence)` at low alpha.
  - Sort by any column, and filter with "low confidence < x".
  - Clicking a row opens a full-screen state-detail modal: the v2 decision card (`js/jev/turnCard.js`)
    plus the inspector tabs (Request, Response, Timing, ...) from `mountInspector`. Left/Right/J/K move
    through rows in table order, Esc closes, copy actions (text, JSON, curl, Python) and Retry on
    error or stopped rows. Data comes from the run's snapshot (`job.sent`); images are not kept after a reload.
  - The left pane collapses to a 40px rail via a toggle in its header; the state is saved in
    localStorage `ojui.batch.layout.v1`. A handoff re-expands it without changing the saved preference.
    Under 1100px the pane stacks as before.
- **Aggregates row**: noul mean P(yes), the choice distribution as a stacked mini-bar, the
  score mean with σ, and mean confidence.
- **Export**: CSV (flattened: `qid.choice`, `qid.p_<option>`, `qid.score`, `qid.confidence`,
  `qid.noul`, latency_ms, input_tokens), JSON (full answers), and "Copy as Markdown table".
- **Persistence**: the last job's inputs and results go to `ojui.batch.last.v1` (including the
  JSONL `field`), without images. A job too large for localStorage shows a warning once.
  Batch supports images too (the same images for every state) through an image drop on the
  pane. Every request goes through `api.systemOne` with `source: 'batch'`.
- **Handoff from a decision** (thread header **batch** button, or `/batch` in a systemone
  composer; `openConversationInBatch`, §3.9):
  - Copies the current questions, the conversation options, the draft images (applied to every
    state) and every distinct user state, oldest first, with the unsent draft last.
  - States go in newline mode (`lines`). A state with a line break switches to `blank-line
    blocks`; a state with a blank line inside, or any JSON state, switches to `JSONL`, so no
    text is lost (`statesToBatchInput`).
  - When the current job has finished rows, a confirm asks before replacing it; Cancel keeps
    the old job. Template handoffs replace without asking, as before.
  - A toast reports the state count and format, whether the draft was included, and the images.

### 5.10 Stats dashboard (C, `#/stats`) and conversation stats (C)

- **Range selector**: session (since page load), 1 h, 24 h, 7 d, or all. It reads
  `listRequests`, plus `getTotals` for the all-time KPIs, which survive deleting
  conversations.
- **KPI tiles** (mono big numbers with a sparkline each):
  - requests (ok, err);
  - input tokens, output (thought) tokens, and estimated image tokens;
  - estimated cost at the current prices;
  - latency p50, p95 and p99 (client);
  - server total p50;
  - mean overhead (`clientMs − total`);
  - chat tok/s (p50);
  - error rate;
  - questions asked;
  - tokens per question.
- **Charts**, all in inline SVG from `charts.js`: `sparkline`, `histogram`, `bars`, `ring`,
  `gauge`, `stackedBar`, `scatter` and `reliability`. Each has hover tooltips and axis ticks in
  `--fg-faint`.
  1. Latency histogram (log-x buckets), split by endpoint.
  2. Latency over time (scatter, coloured by status), with a rolling p50 line.
  3. Timing breakdown: mean stacked bar of model, server, proxy↔up and browser↔proxy, per
     model.
  4. Request rate: requests per minute over the range (bars).
  5. Tokens: input against output over time (stacked area or bars). Also a scatter of tokens
     against question count, and tokens against image count, with a fitted slope labelled
     "≈ N tok/question" and "≈ N tok/image". This is the empirical image-token cost.
  6. Confidence distribution: a histogram of every answer's confidence in 20 bins, split by
     type.
  7. Entropy distribution: a histogram of entropy in bits.
  8. **Calibration-ish**:
     - Top-probability bins (0.5–1.0 in 10 bins) against the fraction of answers the user
       labelled correct (`turn.labels`), as a reliability diagram with the diagonal and a count
       under each bin.
     - Also the Brier score and ECE over labelled answers.
     - When nothing is labelled, show the top-p histogram with the hint "label answers with
       correct/wrong to get a real reliability diagram".
  9. Breakdown tables: by model, by source, by status code, and by options
     (`steps`/`samples`/`think`) showing the mean latency and tokens of each.
- **Buttons**: "Export request log (JSON/CSV)", "Reset totals", "Clear log", each confirmed.
- **`renderConversationStats`**: a strip under the thread header, toggled by the Σ button. It
  shows turns, ok/err, tokens in and out, cost, mean and p95 latency, and mean confidence, plus
  sparklines of latency per turn and confidence per turn.

### 5.11 Slash commands

B registers these:

| Command | Action |
|---|---|
| `/new` | new decision |
| `/chat` | new chat |
| `/rename <title>` | rename |
| `/delete` | delete |
| `/export` | export |
| `/import` | import |
| `/theme dark\|light\|system` | set theme |
| `/model <name>` | set model |
| `/steps <1-8\|default>` | set steps |
| `/samples <1-32\|default>` | set samples |
| `/think <0-4096\|off>` | set think |
| `/seq on\|off` | set sequential |
| `/price <in> [out]` | set prices |
| `/settings` | open settings |
| `/stats` | open stats |
| `/rerun` | rerun |
| `/clear` | clear the composer |
| `/help` | list the commands |

C registers these in `init()`:

| Command | Action |
|---|---|
| `/template <id\|fuzzy>` | load a template |
| `/templates` | open the gallery |
| `/save-template` | save the active question editor's questions as a template |
| `/batch` | in a systemone composer: hand the decision to batch (§5.9); otherwise open batch |
| `/compare [preset]` | compare |
| `/inspect` | inspect the last turn |
| `/curl` | copy curl |
| `/python` | copy Python |
| `/add noul\|choice\|score [qid]` | add a question |
| `/json` | JSON tab |
| `/builder` | Builder tab |

---

## 6. Error UX (B: `renderError`, the banner; C uses `renderError` in batch and compare cells)

Each error card shows:

- a status badge (`422`);
- the title;
- the message;
- `error_type`, when present;
- the request id with a copy button;
- a "Raw" disclosure containing `raw`;
- a hint line;
- actions.

| Kind | What the card adds | Actions |
|---|---|---|
| `validation` (422) | a list of `loc` rendered as `questions › urgent › criteria` with `msg` | **Fix in editor**: load the request into the composer and call `highlightErrors(details)` |
| `bad_request` (400) | Message verbatim. Hints: "Unknown model" → pick a model from the models popover; "images" with think or sequential → turn off think or sequential; "too many options" or "levels" → limits (255 options, 10 levels) | Edit & re-ask |
| `auth` (401) / `forbidden` (403) | "The UI proxy sends OPENJEV_API_KEY. Start it with the server's key: `OPENJEV_API_KEY=… mise run restart`." Show whether auth is configured, and whether an auth override is active | Open settings |
| `too_large` (413) | "Body is N MB; remove or downscale images" | — |
| `rate_limited` (429) / `overloaded` (529) | A retry-after countdown. When `autoRetryOverloaded` is set, retry once automatically when the countdown ends | Retry now |
| `unavailable` (503) | "The inference backend (or a routed model's container) is down" | Retry |
| `upstream_down` (502) | Same content as the banner | Retry |
| `timeout` (504) | "No answer within 900 s" | Retry |
| `network` | "The UI server itself is unreachable. Start it with `mise run start`." | Retry |

**Upstream-down banner** (`#banner`, driven by `oj:health`):

- When `ok === false`, a red strip reads "OpenJev is not reachable at http://127.0.0.1:8080".
  It shows copyable command chips for `mise run start` and `mise run logs`, the
  note "first start loads the model (~16 GB), may take a minute", a live "next check in Ns"
  countdown, and a Retry now button.
- If `errorType === 'auth'`, the banner is amber and shows the auth hint instead.
- If `proxyDown`, it reads "UI server unreachable".
- The banner hides on recovery and shows a toast "OpenJev is back".
- The Send button stays enabled while upstream is down. The request fails fast with a 502
  card, which is itself a learning moment.

---

## 7. Acceptance checklist (the integrator verifies these live)

Setup: `mise run start` (it waits for `/v1/models` and starts the UI).

1. `mise run start` serves the UI on 127.0.0.1:8090 with upstream reachable (banner in `.openjev-ui.log`).
   `mise run test` passes.
2. `curl -si localhost:8090/v1/models` returns the upstream JSON, with `server-timing`
   containing `upstream;dur=` and `x-request-id`. `curl localhost:8090/ui/api/health` returns
   `ok: true` with models.
3. `curl -si localhost:8090/js/main.js` has `content-type: text/javascript`. The page loads with
   no console errors, in both dark and light themes, and has no theme flash on reload.
4. From the Templates gallery, "Ticket triage" → "New conversation" → Send. A noul gauge, choice
   bars and a score histogram render with confidence rings and a heat row. The meta bar shows
   input tokens and latency, and `model n/a` on MLX.
5. The Builder and JSON tabs round-trip. Edit a choice option in JSON tree mode, switch to
   Builder, and it is there. Invalid JSON blocks the switch. Blocking the CDN (DevTools
   offline for jsdelivr) still gives the textarea fallback.
6. Edit & re-ask with a changed option: the new turn shows `↳ re-ask of #1` and Δ badges.
   Rerun the same turn: `seed-stable ✓`.
7. Compare with the `steps 1 vs 4` preset: both runs complete and show per-question
   divergence. "Save runs" appends labelled turns.
8. Attach an image by paste and by drag/drop, then run the `image-yes-no` template with its
   generated demo image. Setting think with an image shows the warning chip, and sending shows
   a 400 card with the hint.
9. A 422: in JSON text mode, set a question's `criteria` for score to a string, bypassing
   client validation with the "send anyway" option on client-validation errors, which B must
   offer. The card lists `loc`, and "Fix in editor" highlights the field. A 400: a choice with
   `criteria: {}` sent anyway returns "no options". Unknown model via `/model nope` returns a
   400 with the hint.
10. A 401/403: set Settings → Auth override to `Bearer wrong`. Against an upstream with
    `OPENJEV_API_KEY` set, the card shows the hint. Without a key on the upstream the override
    is ignored, and that is fine. `mise run stop`: within 5 s the red banner appears
    with the start hint, and Send gives a 502 `upstream_down` card. `mise run start`:
    the banner clears on its own.
11. Chat: "New chat" and ask something. Tokens stream in visibly, with ttft and tok/s and a
    working Stop. Usage appears in the meta bar. JSON mode renders a tree. "Judge with System
    One" opens a rubric decision.
12. Batch: the `sentiment` template's batchStates (10 or more) with concurrency 1. Progress,
    ETA and the table all work. Sort by confidence. CSV export opens in a spreadsheet with one
    column per `qid.field`. Drop a `.jsonl` of objects on the view, pick the `text` field in the
    preview, Load, Run.
13. Stats: KPIs are non-zero, all charts render, "tok/image" is estimated after the image run,
    and labelling 3 answers correct/wrong populates the reliability diagram. Totals survive a
    page reload and deleting every conversation.
14. History: rename, pin, delete (with confirm), export all, import that file into a fresh
    profile (private window), and the conversations reappear. Search filters them. Reloading
    restores the last open conversation and its unsent draft.
15. Slash: typing `/` shows the menu. `/steps 4` updates the chip, `/template shell` loads
    shell-safety, and `/curl` copies a curl that runs verbatim in a terminal against :8080.
    The Python snippet runs with `.venv/bin/python` (typesafe_sdk installed) and prints the
    answers.
16. Keyboard: Cmd+K, Cmd+J, Cmd+I, Esc and `?` all work. Layout is usable at a width of
    1280px, and the sidebar collapses to an overlay below 900px.
17. `git status` shows changes only under `ui/` plus `mise-tasks/`.

---

## 8. Size and quality budget

- **A**: about 250 lines in server.py and about 150 in tests.
- **B**: about 2,800 lines of JS and 900 of CSS.
- **C**: about 3,200 lines of JS and 600 of CSS.

Every module starts with a 2–4 line header comment saying what it owns. No `console.log` noise;
`console.warn` is fine for fallbacks. Every user-provided string goes into the DOM through
`textContent` or `h()`, never `innerHTML`, except for DOMPurify-sanitized Markdown.
