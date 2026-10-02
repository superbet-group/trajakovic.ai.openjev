# OpenJev Playground (UI)

A single-page web app for trying a running OpenJev server: build typed questions, send them to
`/v1/systemone`, and read the calibrated answers. No build step and no npm. A small Python
server (`ui/server.py`) serves `ui/static/` and proxies `/v1/*` to OpenJev, so there is no CORS
and the API key stays on the server.

## Run it

```sh
mise run start    # OpenJev on :8080 (MLX) and the Playground on http://127.0.0.1:8090; opens your browser
```

| Task | What it does |
|---|---|
| `mise run start` / `stop` / `restart` | Start, stop or restart OpenJev and the UI in the background |
| `mise run status` | Whether both answer |
| `mise run logs [server\|ui]` | Follow the logs |
| `OPENJEV_LOG_LEVEL=debug mise run restart` | Restart with request and response bodies logged |
| `mise run test` | All tests (server, UI proxy, batch import) |
| `mise run benchmark` | 100 labelled questions: latency and accuracy |

The UI starts even when OpenJev is down; the top bar shows a health pill and the failing
request tells you to run `mise run start`.

Against a server that needs a key: `OPENJEV_API_KEY=sk-... mise run restart`. Other settings:
`OPENJEV_URL` (default `http://127.0.0.1:8080`), `OPENJEV_ORIGIN_SECRET`, `UI_HOST`, `UI_PORT`.
Details in [`ui/README.md`](../ui/README.md).

## What is in it

**Decisions** (System One). Write a state as text or JSON, attach up to 8 images (paste, drop
or attach), and set `model`, `steps`, `samples`, `think` and `sequential` per conversation.
Questions come from a visual **Builder** or a **JSON** editor (`Mod+J` switches), validated in
the browser the way the server validates them. A 422 from the server is mapped back onto the
field that caused it.

**Results.** Each type has its own view:

| Type | Shows |
|---|---|
| `noul` | gauge with log-odds |
| `choice` | probability bars, winner, confidence ring |
| `score` | histogram with the expected value |

Every answer also gets entropy, perplexity and margin, and a re-ask shows the change against
the parent turn. A rerun checks whether the answer is seed-stable. The meta bar under each
turn has tokens, estimated cost, client and server latency, a timing waterfall, size and the
request id.

**Chat.** A chat with `diffusiongemma-26b` on `/v1/chat/completions`: streamed Markdown, time to
first token, tokens per second, JSON mode, a system prompt, and "Judge with System One".

**Views** (hash routes):

| Route | View |
|---|---|
| `#/` | Conversations and the composer |
| `#/templates` | Template gallery: ticket triage, sentiment, PR risk, shell safety, moderation, intent routing, image questions, rubric, think and sequential examples. Save your own with `/save-template` |
| `#/compare/a/b` | The same request under different options, with a per-question JSD |
| `#/batch` | Many states through one question set. Load JSONL, TXT, CSV/TSV or JSON by upload or drag and drop (read locally). CSV export |
| `#/stats` | KPIs, latency percentiles, token and confidence distributions, a reliability diagram from your correct/wrong labels |

The **Inspector** (`Mod+I`) shows the raw request and response and copies them as curl,
Python (`typesafe_sdk`), fetch or OpenAI snippets.

**History** lives in IndexedDB in your browser: search, pin, rename, export and import.
Prices per 1M input and output tokens are set in Settings; cost is always recomputed from the
recorded token counts.

## Shortcuts and slash commands

`Mod` is Cmd on macOS and Ctrl elsewhere. Press `?` for the list.

| Key | Action |
|---|---|
| `Mod+K` | Focus the composer |
| `Mod+Shift+O` | New decision |
| `Mod+B` | Toggle the sidebar |
| `Mod+J` | Builder / JSON editor |
| `Mod+I` | Inspect the last turn |
| `Enter` / `Shift+Enter` | Send / newline (`Mod+Enter` always sends) |
| `Esc` | Stop a request, or close the drawer or modal |
| `/` | Slash commands |

Slash commands: `/new`, `/chat`, `/template`, `/templates`, `/save-template`, `/add`, `/json`,
`/builder`, `/model`, `/steps`, `/samples`, `/think`, `/seq`, `/rerun`, `/compare`, `/batch`,
`/stats`, `/inspect`, `/curl`, `/python`, `/rename`, `/delete`, `/export`, `/import`, `/theme`,
`/price`, `/settings`, `/clear`, `/help`.

## Known upstream quirks

These come from the OpenJev MLX backend, not the UI.

- Chat on MLX drops single newlines, so lists and code fences can run together.
- Chat tokens arrive in one burst, so the UI shows `tok/s e2e` when a decode-only rate would be
  meaningless.
- `model;dur=0.0` on MLX: the meta bar shows `model n/a`.

## Layout

```
ui/
  server.py       proxy + static server (FastAPI, uvicorn, httpx from the project .venv)
  static/         the SPA: index.html, css/, js/core (shell), js/app (chat UI), js/jev (visualizers)
  tests/          pytest for the proxy, node --test for batch import
  CONTRACT.md     the build contract the pieces were written against
```
