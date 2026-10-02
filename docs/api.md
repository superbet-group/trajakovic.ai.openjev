# API and internals

Back to the [README](../README.md).

**Fast, calibrated, typed decisions from an open model.** OpenJev is an open-source
"System One" decision server. Send it a state and typed questions (yes/no, choice, score). It
returns a probability and a confidence for each answer in tens of milliseconds. It reads the
answers directly from the model's probabilities and parses no text, so an answer cannot go
off-schema. Questions can also ask about images.

OpenJev uses the same wire API as TypeSafe's
[Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev), so their SDKs work with it
unchanged. It runs
[DiffusionGemma 26B-A4B](https://huggingface.co/nvidia/diffusiongemma-26B-A4B-it-NVFP4)
(Apache-2.0) through vLLM on an NVIDIA GPU, or through MLX on Apple silicon.

> **Hosted for free on [Codiv](https://codiv.ai)**, an inference platform for open System One
> models. Sign up and get 100M input tokens, no card required. `https://api.codiv.ai/v1/systemone`

OpenJev is an independent project. It is not affiliated with or endorsed by TypeSafe AI.

## Models

| Model id | Model | Size | Input | Choices | Runs on |
|---|---|---|---|---|---|
| `openjev-latest` (`openjev-0.1`) | [DiffusionGemma 26B-A4B](https://huggingface.co/nvidia/diffusiongemma-26B-A4B-it-NVFP4) (NVIDIA / Google), read as a diffusion canvas | 26B total, 4B active | text and images | up to 255 | vLLM (NVIDIA GPU) or MLX (Apple silicon) |
| `laya-1.0` | [Laya](https://github.com/NandhaKishorM/laya) by Nandakishor M / Convai Innovations | 421M | text, 1,024 tokens | up to 255 | PyTorch, GPU or CPU |
| `verdict-1.4` | [Verdict](https://github.com/Heman10x-NGU/Verdict-open-jev) by Heman10x | 151M | text, 512 tokens | up to 24 | PyTorch, GPU or CPU |
| `clm-v0.1` | [CLM](https://github.com/Contrastive-LM/CLM) by Contrastive-LM: contrastive heads over Qwen3-8B | 8B + 2 × 9.4M | text, 2,048 tokens | up to 255 | vLLM (NVIDIA GPU) |
| `jevk5-0.2` | [JevK5](https://github.com/allebee/jevk5) by Alibi Serikbay: Qwen3.5-4B with a distilled LoRA, read by its answer letters | 4B | text, 16,384 tokens | up to 255 | vLLM (NVIDIA GPU) |

`diffusiongemma-26b` is the same DiffusionGemma for [text generation](#text-generation). All
weights are Apache-2.0. Laya, Verdict, CLM and JevK5 are other people's models: see
[Small encoder models](self-hosting.md#small-encoder-models), [CLM](self-hosting.md#clm) and [JevK5](self-hosting.md#jevk5) for details and credit.

## Try it

```bash
pip install typesafe-sdk
export TYPESAFE_BASE_URL=https://api.codiv.ai   # or http://127.0.0.1:8080 for your own server
export TYPESAFE_API_KEY=sk-codiv-...
```

```python
from typesafe_sdk import TypeSafeClient

client = TypeSafeClient()
r = client.system_one(
    "Everything is down and we have a demo with our biggest client at noon.",
    {
        "urgent": {"type": "noul", "instructions": "Does the customer need a reply within the hour?"},
        "team":   {"type": "choice", "instructions": "Which team should handle it?",
                   "criteria": {"outage": "service down", "billing": "charges, refunds", "feature": "requests, how-to"}},
        "tone":   {"type": "score", "instructions": "How upset is the customer?",
                   "criteria": ["calm", "annoyed", "furious"]},
    },
)
r.nouls["urgent"].noul        # 1.00
r.choices["team"].choice      # "outage", confidence 1.00
r.scores["tone"].score        # 2.00 (expected level, 0-indexed)
```

Or with curl:

```bash
curl https://api.codiv.ai/v1/systemone \
  -H "Authorization: Bearer $TYPESAFE_API_KEY" -H "Content-Type: application/json" \
  -d '{"model": "openjev-latest", "state": "I was charged twice this month.",
       "questions": {"is_billing": {"type": "noul", "instructions": "Is this a billing issue?"}}}'
```

## API

| | |
|---|---|
| `POST /v1/systemone` | `{state, model, questions}` → `{model, answers, usage}` |
| `POST /v1/chat/completions` | OpenAI-style text generation with model `diffusiongemma-26b` ([below](#text-generation)) |
| `GET /v1/models` | `openjev-0.1`, its alias `openjev-latest`, `diffusiongemma-26b`, and the [small encoder models](self-hosting.md#small-encoder-models) when they run. The server also accepts `jev-latest` and `jev-preview`, so TypeSafe SDK defaults work. |

Question types:

- **`noul`** (yes/no): takes optional `criteria: {true, false}`. Returns `{noul: P(yes)}`.
- **`choice`**: takes `criteria: {name: description}`. Returns `{choice, probabilities, confidence}`.
- **`score`**: takes `criteria: [level0, level1, …]` (1–10 levels; a single level is answered
  directly, as for a choice with one option). Returns `{score: Σ i·pᵢ, legend, probabilities, confidence}`.

`confidence` is `1 − H(p)/ln K`: 1 when the model is certain, 0 when the distribution is
uniform. `usage.input_tokens` counts prompt tokens, image tokens included.
`usage.output_tokens` is 0 unless you set `think`.

Each response has a `Server-Timing` header:

```
server-timing: model;dur=41.2, server;dur=2.8, total;dur=44.0
```

`model` is the time spent on the model, summed over the request's reads. It can be more than
`total` when reads run in parallel. `server` is the remaining time: schema compile,
tokenization, validation and serialization. Neither includes your network.

Errors use the same shapes as Jev, checked against the live API:

- `422` with a FastAPI validation list for a field of the wrong shape.
- `400` with a plain-text reason for a question the server cannot ask (no options, too many
  options, or too many score levels).
- `400` `api_usage_error` for an unknown model or question type.
- `{"detail": {"error_type", "message"}}` for auth errors (`401`/`403`).
- `429` for rate limits. `529` when the server is overloaded.

Differences from Jev:

- Model names are OpenJev's own. `jev-latest` and `jev-preview` are aliases. A pinned Jev
  version such as `jev-1.13.0` gets `400` `Unknown model`.
- The server reads many questions in chunks of about 12 per read. The chunks run in parallel.

## Extensions

These optional request fields are OpenJev additions. A request without them behaves exactly
like Jev. TypeSafe's SDKs never send them. They come from the example server in
vllm-project/vllm#57250.

| Field | Values | What it does | Cost |
|---|---|---|---|
| `images` | up to 8 | Images for the questions, placed before the state. Each is a `data:image/...;base64,` URL or `{"content_type", "base64"}`. JPEG, PNG, WebP or GIF, 5 MB each. | about 280 input tokens per image |
| `steps` | 1–8, default 1 | Denoise steps per read. More steps let the answers settle against each other. | same tokens, more GPU time |
| `samples` | 1–32 | Read N times with different noise and average. This replaces the automatic re-reads. `samples: 1` gives one read, the fastest answer. | N × input tokens |
| `think` | 0–4096 tokens | The model writes a thought, then reads the answers after it. The number is a hard cap, and a longer thought is cut. Give multi-step problems 512 or more. | input tokens twice, plus the thought as output tokens |
| `sequential` | `true` | For long question lists: read the chunks in order. Each chunk sees the answers before it. | one read per chunk, in series |

```bash
curl https://api.codiv.ai/v1/systemone \
  -H "Authorization: Bearer $TYPESAFE_API_KEY" -H "Content-Type: application/json" \
  -d '{"model": "openjev-latest", "state": "Look at the photo.",
       "images": ["data:image/jpeg;base64,/9j/4AAQ..."],
       "questions": {"hotdog": {"type": "noul", "instructions": "The photo shows a hot dog"}}}'
```

`think` and `sequential` need a text state. A request that combines them with `images` gets a 400.

## Text generation

`POST /v1/chat/completions` generates text with the same model, OpenAI style. Use model
`diffusiongemma-26b`. It supports streaming and tools. The server ignores sampling fields
(`temperature`, `seed`, penalties and similar) because vLLM refuses them for diffusion models.
`max_tokens` defaults to 1024, with a cap of 8192. At most 8 generations run at once, so reads
always have room. The MLX backend ignores `tools` and `logprobs` and does not return the thought.

## How it works

DiffusionGemma is a discrete diffusion model. It denoises a full canvas of tokens in each
forward pass, instead of writing left to right. OpenJev uses this to read answers, not write them.

OpenJev builds a canvas in which only the answer slots are masked, one token per question:

```
canvas in                 one read-only pass         answer out
  q1: [?]        ──►      P(yes) 0.001        ──►    noul  0.001
  q2: [?]                 P(A) 0.000                 choice "billing"
                          P(B) 0.999                 confidence 0.997
                          P(C) 0.000
  q3: [?]                 P(0) 0.000                 score 1.00
                          P(1) 0.996
                          P(2) 0.004
```

Each label is one token: `yes`/`no` for a `noul`, `A`/`B`/`C` for a choice, `0`/`1`/`2` for a
score. The model never writes into these slots. One read-only pass gives the probability
distribution for each slot, and that distribution **is** the answer. The numbers above are a
real read of "The invoice looks wrong again. Second time this quarter.": not urgent, billing,
mildly annoyed.

The read scores only the label tokens, so an answer cannot go off-schema. The confidence comes
from the model's own distribution, not from a number that the model reports about itself.

If a slot is uncertain (entropy > 0.1), OpenJev reads three more times with fresh noise and
averages the four results. One uncertain question causes a re-read of all the questions in the
request. These extra reads add no tokens to `usage`. Set `samples: 1` to get one read only.
Question ids never go to the model. It sees `q1`, `q2`, `q3`.

The vLLM part is [vllm-project/vllm#57250](https://github.com/vllm-project/vllm/pull/57250),
merged on 2026-09-22. It adds seeded canvases, read-only steps, step caps and pinned canvas
positions for DiffusionGemma. `openjev/engine.py` adapts that PR's `structured_server.py`
example, with async I/O, bounded concurrency and backpressure.
