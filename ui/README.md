# OpenJev Playground (UI)

A single-page, ChatGPT-like web app for exploring a locally running OpenJev server: build
typed question sets (noul / choice / score), send them to `/v1/systemone`, and look at
calibrated answers, token usage, latency waterfalls and a lot of other stats. It also has a chat
mode for `diffusiongemma-26b` on `/v1/chat/completions`, with live token streaming.

No build step, no npm. A small Python server (`ui/server.py`) serves `ui/static/` and proxies
`/v1/*` to OpenJev, so the browser never deals with CORS and the API key stays on the server.

## Quick start

```sh
mise run startOpenJev      # start OpenJev on :8080 (the first start loads the model and takes a while)
mise run ui                # UI on http://127.0.0.1:8090
mise run uiTest            # proxy tests (mocked upstream, no model needed)
mise run uiTestJs          # batch file import unit tests (node --test, no browser)
```

The server starts even when OpenJev is down. The startup banner tells you whether the upstream
answered `GET /v1/models`:

```
  OpenJev Playground  v0.1.0

  UI        http://127.0.0.1:8090
  OpenJev   http://127.0.0.1:8080
  auth      none (set OPENJEV_API_KEY if the server needs one)
  upstream  reachable (3 models, 16 ms)
```

Each proxied request is logged on one line:
`POST /v1/systemone 200 286ms up=286ms req_b2d63313…` (total time, time to upstream headers,
upstream request id).

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `OPENJEV_URL` | `http://127.0.0.1:8080` | Upstream OpenJev base URL (trailing `/` stripped) |
| `OPENJEV_API_KEY` | unset | Sent upstream as `Authorization: Bearer <key>` unless the browser sent its own `Authorization` |
| `OPENJEV_ORIGIN_SECRET` | unset | Sent upstream as `X-Origin-Secret` |
| `UI_HOST` | `127.0.0.1` | Bind address |
| `UI_PORT` | `8090` | Bind port |

CLI flags override the env vars: `.venv/bin/python ui/server.py --host 0.0.0.0 --port 9000 --open`
(`--open` opens a browser tab once the server is listening; `mise run ui` passes it).

Against a server that needs a key: `OPENJEV_API_KEY=sk-... mise run ui`.

Because the proxy adds the key, `/v1/*` and `/ui/api/health` refuse cross-site callers with a 403
(a foreign `Origin`, `Origin: null`, or `Sec-Fetch-Site: cross-site`), so other web pages can't
spend your key. On a loopback bind the `Host` header must also be `127.0.0.1`, `localhost` or
`::1`, which blocks DNS rebinding. Paths containing `..` get a 400, so callers can't reach
upstream routes outside `/v1`.

## Endpoints

| Path | What it does |
|---|---|
| `GET /ui/api/config` | UI version, upstream URL, whether auth is configured, defaults, limits and `mise` hints |
| `GET /ui/api/health` | Always 200. Probes upstream `/v1/models` (2.5 s timeout): `ok`, reachability, latency, request id, Server-Timing, models, `errorType` (`upstream_unreachable`, `upstream_timeout`, `auth`, `upstream_status`, `upstream_error`) |
| `/v1/*` | Transparent streaming passthrough (GET/POST/PUT/PATCH/DELETE/OPTIONS). Status and body unchanged; JSON and SSE use the same streaming path |
| `/` | Static files from `ui/static/`, all `cache-control: no-store`, `.js` as `text/javascript` |

Proxy details:

- `Server-Timing` gets `upstream;dur=<ms>` appended (the time until upstream response headers
  arrived), and `x-ojui-upstream-ms` carries the same number. Compare it with the upstream's own
  `total;dur` to see the proxy hop; the browser's own time minus `upstream` is the browser-to-proxy
  hop.
- Upstream down: `502 {"detail": {"error_type": "upstream_unreachable", "message": ..., "hint": "mise run startOpenJev"}}`.
  Read timeout: `504 upstream_timeout`. Other transport errors: `502 upstream_error`.
- Timeouts: connect 3 s, read 900 s (a `think` run on MLX can take minutes), write 120 s.
- If the browser disconnects mid-stream (Stop in chat), the upstream response is closed at once.

## Features (frontend)

- **System One threads**: state as text or JSON, up to 8 images (paste, drag and drop, attach),
  per-conversation options (`model`, `steps`, `samples`, `think`, `sequential`), and a token
  estimate before sending.
- **Question editor**: a visual builder and a JSON editor (vanilla-jsoneditor tree/text/table,
  with a textarea fallback when offline), client-side validation that mirrors the server, and
  422 errors mapped back onto the offending field.
- **Result visualizers**: a noul gauge with a log-odds readout, choice probability bars, a score
  histogram with the expected value, confidence rings, entropy, perplexity and margin, deltas
  against the parent turn on a re-ask, and a `seed-stable` reproducibility check on reruns.
- **Turn meta bar**: tokens, estimated cost, client and server latency, a timing waterfall,
  bytes and request id.
- **Chat mode** (`diffusiongemma-26b`): streamed Markdown, ttft and tok/s, JSON mode, a system
  prompt, and "Judge with System One".
- **Templates** gallery, **Compare** (the same request under different options, with per-question
  JSD), **Batch** (many states through one question set; load them from JSONL, TXT, CSV/TSV or
  JSON files with Upload or drag and drop, read locally; CSV export), **Stats** dashboard (KPIs,
  latency percentiles, token and confidence distributions, a reliability diagram from your
  correct/wrong labels), and a raw request/response **Inspector** with curl, Python, fetch and
  OpenAI snippets.
- **History** in IndexedDB: search, pin, rename, export and import. Slash commands, keyboard
  shortcuts (`?`), and dark and light themes.

Prices per 1M input and output tokens are set in Settings; cost is always computed from the
recorded token counts, so changing a price rewrites every figure.

## Layout

```
ui/
  server.py            proxy + static server (FastAPI/uvicorn/httpx from the project .venv)
  tests/test_server.py pytest against a fake upstream (httpx.MockTransport)
  tests/batch_import.test.mjs  node --test for js/jev/batchImport.js (mise run uiTestJs)
  static/              the SPA: index.html, css/, js/core (shell), js/app (chat UI), js/jev (visualizers)
  CONTRACT.md          the build contract the pieces were written against
```

The server uses only packages that are already in `.venv` (fastapi, starlette, uvicorn, httpx).

## Known upstream quirks (OpenJev, not the UI)

- **Chat on MLX drops single newlines.** The `/v1/chat/completions` stream from the MLX backend
  loses `\n` between lines (only blank lines survive), so Markdown lists and code fences in chat
  replies can render run together. `curl -N` against :8080 shows the same thing.
- **Chat tokens arrive in one burst.** DiffusionGemma generates the block, then streams it within
  a few ms. A decode-only rate (tokens / (stream − ttft)) would read as thousands of tok/s, so the
  UI shows `tok/s e2e` (tokens / total stream time) whenever the decode window is under 250 ms or
  under 10% of the stream.
- **`model;dur=0.0` on MLX.** The meta bar shows `model n/a`; model time is inside `server`.
