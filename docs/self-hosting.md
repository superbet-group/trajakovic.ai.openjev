# Self-hosting

Moved out of the README. Back to the [README](../README.md).

Two backends serve the same `/v1/systemone`. Select one by hardware:

| | vLLM (default) | MLX |
|---|---|---|
| Hardware | NVIDIA GPU, 24 GB or more | Apple silicon, about 16 GB free (more in service, see [MLX memory](#mlx-memory)) |
| Setup | Docker image | `mise run install` |
| Reads | up to 64 in flight | one at a time |
| `images`, `steps` > 1, `think` | yes | yes |
| Text generation | yes | yes, streaming included |

## NVIDIA GPU

You need an NVIDIA GPU with at least 24 GB of memory for the NVFP4 checkpoint. We tested on an
RTX PRO 6000 Blackwell (sm_120).

The prebuilt image [`razorback16/openjev`](https://hub.docker.com/r/razorback16/openjev) runs
vLLM and the API server in one container, on CUDA 13. It pins the upstream vLLM commit below,
with the two changes in [Caveats](#caveats).

```bash
git clone https://github.com/razorback16/openjev && cd openjev
docker compose up -d          # OpenJev on 127.0.0.1:8080 when the model is loaded
curl localhost:8080/v1/models
```

Or without compose:

```bash
docker run -d --gpus all --ipc=host -p 127.0.0.1:8080:8080 \
  -v ~/.cache/huggingface:/root/.cache/huggingface razorback16/openjev:0.5.0
```

The first start downloads the weights (about 18 GB) into `~/.cache/huggingface`. To build the
images yourself, build the shared base first, then run `docker compose build`:

```bash
docker build -f docker/Dockerfile.base -t razorback16/openjev-base:cu130-torch2.13 .
docker compose build
```

vLLM listens only inside the container.
Set `OPENJEV_UPSTREAM` to use a vLLM server that you already run.

Measured on an RTX PRO 6000 at 38% of the GPU, with 3 questions per request and cache-busted
states:

| Concurrency | req/s | p50 | p95 |
|---:|---:|---:|---:|
| 1 | 10.7 | 94 ms | 94 ms |
| 16 | 43.3 | 367 ms | 369 ms |
| 32 | 51.7 | 545 ms | 618 ms |
| 64 | 57.4 | 760 ms | 1109 ms |

One request at a time, on the same GPU, with `samples: 1`:

| Request | p50 | p95 |
|---|---:|---:|
| 1 question | 27 ms | 28 ms |
| 3 questions | 31 ms | 32 ms |

Without Docker:

```bash
git clone https://github.com/vllm-project/vllm && cd vllm
VLLM_COMMIT=1b3b88ec2b7457aa030db4d0e7d8aaf04f6d0fb8   # the commit the image pins
git checkout $VLLM_COMMIT
# a choice of more than 128 options needs a larger cap, as in the image
sed -i 's/^MAX_LOGPROB_TOKEN_IDS = 128$/MAX_LOGPROB_TOKEN_IDS = 512/' vllm/sampling_params.py
VLLM_USE_PRECOMPILED=1 VLLM_PRECOMPILED_WHEEL_COMMIT=$VLLM_COMMIT pip install -e .
vllm serve nvidia/diffusiongemma-26B-A4B-it-NVFP4 --served-model-name dgemma \
  --diffusion-config '{"canvas_length": 64}' --max-logprobs 32 --enable-prefix-caching \
  --async-scheduling --attention-backend TRITON_ATTN \
  --limit-mm-per-prompt '{"image": 8, "video": 0}' \
  --enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4 \
  --override-generation-config '{"max_new_tokens": null}'
pip install -e path/to/openjev && python -m openjev
```

To match the image's image reads, also apply `docker/patches/vision_prefix_lm.py` to this
checkout. Without it, vLLM prefills images causally.

## Apple silicon

A Mac needs no vLLM and no Docker. The MLX backend runs DiffusionGemma inside the OpenJev
process through [MLX](https://github.com/ml-explore/mlx) and
[mlx-vlm](https://github.com/Blaizzy/mlx-vlm). The 4-bit weights need about 16 GB of memory
to load, and more in service (see [MLX memory](#mlx-memory)).

Set up mise as described in the [README](../README.md), then:

```bash
mise run install     # Python, packages in .venv, the MLX model
mise run start       # OpenJev on 127.0.0.1:8080, UI on 127.0.0.1:8090
```

This backend uses the same prompts, canvases and seeds as vLLM. It supports `images`,
`samples`, `sequential`, `steps`, `think` and the automatic re-reads, and it bills the same.
Each step after the first reuses one prefill of the prompt. More steps cost GPU time, not
prompt tokens. The re-reads and `samples` of one request share one vision pass.

Reads run one at a time, so this backend is for local use, not for serving. A 3-question request
takes about 0.2–0.4 s on an M3 Ultra and about 0.39 s on an M4 Max, both with the 4-bit weights.
16 concurrent requests finish at about 4 req/s.

## MLX memory

Loading the 4-bit weights costs about 16 GB. In service, MLX keeps freed GPU buffers in a pool
that grows to the peak working set. On an M4 Pro with 48 GB, one workload grew from 16.6 GB to
36.2 GB. With `OPENJEV_MLX_CACHE_LIMIT_GB=4` it stayed at 23.5 GB, with the same answers and
speed. The limit is off by default, because with the 8-bit or bf16 weights one read can need more
than 4 GB. If you set it, use a value above your working set.

`OPENJEV_MLX_PROMPT_CACHE` sets the number of cached prefills (default 12).

## Small encoder models

OpenJev also serves two small System One models that other people built and trained. The
credit is theirs. OpenJev only puts them behind the same API. Each is a bidirectional encoder
with a classification head, not a diffusion model. It reads each question in one forward pass,
so an answer is still a distribution over your options.

| Model id | Model | Size | State limit | Choices |
|---|---|---|---|---|
| `laya-1.0` | **[Laya](https://github.com/NandhaKishorM/laya)** by Nandakishor M / [Convai Innovations](https://huggingface.co/convaiinnovations). The [laya-typed-decisions](https://huggingface.co/convaiinnovations/laya-typed-decisions) checkpoint: ModernBERT-large, fine-tuned on the typed-decisions workflows. | 421M | 1,024 tokens, options included | up to 255. The options share 256 tokens, so with many options each option is cut to a few tokens. Use about 20 at most, or split the question. |
| `verdict-1.4` | **[Verdict](https://github.com/Heman10x-NGU/Verdict-open-jev)** by [Heman10x](https://huggingface.co/heman10x). The [rlcd-modernbert-151m](https://huggingface.co/heman10x/rlcd-modernbert-151m) checkpoint with Verdict's v1.4 inference engine: ModernBERT-base with a GLiClass head, calibrated per option count. | 151M | 512 tokens, options included | up to 24 |

Each model runs in its own container (`OPENJEV_BACKEND=laya` or `verdict`).
`docker compose up -d` starts both next to the vLLM container on the same GPU. The `openjev`
container sends their requests to them (`OPENJEV_MODEL_ROUTES`), so `:8080` serves all three
models. `/v1/models` lists a routed model even when its container is stopped. A request for it
then gets a 503. On a GPU both models use bf16 weights and need about 3.7 GB together. Set
`OPENJEV_GPU_UTIL` to keep that memory free.

Measured on an RTX PRO 6000 with 16 questions per request. GPU memory is the `nvidia-smi` value,
CUDA context included. A short state is about 50 tokens. A full state fills the model's limit.

| Model | GPU memory | 16 questions, short state | 16 questions, full state |
|---|---:|---:|---:|
| `laya-1.0` | 2.5 GB | 10 ms | 109 ms (16 × 1,024 tokens) |
| `verdict-1.4` | 1.2 GB | 7 ms | 21 ms (16 × 512 tokens) |

FlashAttention 2 gave no improvement. Memory was the same, speed was within 6%, and short
states were slower.

To run one model alone, on a GPU or on the CPU:

```bash
docker build -f docker/Dockerfile.laya -t openjev-laya .   # after the base, as above
docker run -d --gpus all -p 127.0.0.1:8081:8080 \
  -v ~/.cache/huggingface:/root/.cache/huggingface openjev-laya
# or without Docker:
pip install -e '.[laya]' && OPENJEV_BACKEND=laya python -m openjev
```

A server that runs one of these models alone also accepts `jev-latest` and `jev-preview` for it.

Each image has its own Dockerfile in `docker/`. All three start from `docker/Dockerfile.base`
and share its CUDA, Python and PyTorch layers (about 8.7 GB on disk). A server that runs all three stores these layers once, about 21 GB in
total.

Differences from the DiffusionGemma model:

- Text only. `images`, `steps` above 1, `samples` above 1, `think` and `sequential` get a 400.
- The server cuts a state that is longer than the limit, with no error. `usage.input_tokens`
  counts what the model read. Each question is a separate sequence, so each question bills the
  state again.
- Verdict adds an "insufficient evidence" option to each question. OpenJev removes it and scales
  the other probabilities to a sum of 1, as Jev's answer shapes require. Verdict also ignores a
  noul's `criteria`.
- Laya rounds each probability to 4 decimal places.
- On a GPU the weights are bf16, not fp32. On 36 test answers per model, no top option changed.
  Probabilities moved by at most 0.021 (Laya) and 0.007 (Verdict).
- The Verdict prompt format and temperatures come from Verdict's inference engine (v1.4). For
  the same input, the probabilities are the same as that engine's output.

For benchmarks, training, fine-tuning and known limits, read the authors' repositories:
[Laya](https://github.com/NandhaKishorM/laya) and
[Verdict](https://github.com/Heman10x-NGU/Verdict-open-jev). The `laya` container runs Laya's
[`laya`](https://pypi.org/project/laya/) package. Verdict uses
[ModernBERT](https://huggingface.co/answerdotai/ModernBERT-base) (Answer.AI, LightOn) and
[GLiClass](https://github.com/Knowledgator/GLiClass) (Knowledgator).

## CLM

[CLM](https://github.com/Contrastive-LM/CLM) (Contrastive Language Model) is Contrastive-LM's
model; the credit is theirs. The [CLM-v0.1-8B](https://huggingface.co/Contrastive-LM/CLM-v0.1-8B)
checkpoint is two small heads (a state head and an action head, 9.4M parameters each) on top of a
frozen [Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B). Qwen3-8B turns the state (with the
question appended) and each option into its last-token embedding. The heads project them to 512
dimensions, and an answer is the softmax over the scaled cosine of each option with the state.

The `clm` image (`docker/Dockerfile.clm`) runs vLLM's pooling runner for Qwen3-8B and the heads
in one container. vLLM is the same pinned commit as the main image, without its changes. The
prompt layout, the heads and the scoring come from Contrastive-LM's
[`contrastive-lm`](https://pypi.org/project/contrastive-lm/) package (0.1.0).

```bash
docker build -f docker/Dockerfile.clm -t openjev-clm .   # after the base, as above
docker run -d --gpus all --ipc host -p 127.0.0.1:8083:8080 \
  -v ~/.cache/huggingface:/root/.cache/huggingface openjev-clm
```

Next to DiffusionGemma, run `docker compose --profile clm up -d` and add
`clm-v0.1=http://clm:8080` to `OPENJEV_MODEL_ROUTES`. CLM runs its own vLLM, so lower the
`openjev` service's `OPENJEV_GPU_UTIL` to leave it room; `OPENJEV_CLM_GPU_UTIL` (default 0.12) is its
share of the GPU.

The default weights are [Qwen/Qwen3-8B-FP8](https://huggingface.co/Qwen/Qwen3-8B-FP8). On an
RTX 3090 (Ampere, no FP8 compute) vLLM runs them as weight-only FP8 through Marlin. On 581
four-way SQuAD questions, FP8 and bf16 agreed on 98.5% of top options. The mean embedding cosine
was 0.9992 and accuracy went from 89.7% to 88.8%.
[RedHatAI/Qwen3-8B-FP8-dynamic](https://huggingface.co/RedHatAI/Qwen3-8B-FP8-dynamic) does not
start on Ampere with this vLLM. Set `OPENJEV_MODEL=Qwen/Qwen3-8B` for bf16.

Measured on one RTX 3090 at `OPENJEV_GPU_UTIL=0.85`. Each request has a unique state (a SQuAD
paragraph) and 3 questions, about 550 prompt tokens in all:

| Weights | Weights in GPU memory | KV / prefix cache | 1 request at a time | 64 at a time |
|---|---:|---:|---:|---:|
| Qwen3-8B-FP8 | 7.7 GB | 78k tokens | 99 ms | 18 req/s, 9.6k prompt tokens/s |
| Qwen3-8B (bf16) | 14.1 GB | 33k tokens | 130 ms | 18 req/s, 9.8k prompt tokens/s |

The GPU is the limit: prefill is compute-bound, and weight-only FP8 does not add compute on Ampere.
What FP8 buys there is latency at low load and 2.4 times the prefix cache.
`--max-num-batched-tokens 8192` gave no gain.

Differences from the other models:

- Text only, as for the encoder models. A state longer than 2,048 tokens loses its start, not
  its end, so the question (which comes last) survives. Upstream CLM cuts the end (see
  [CLM PR #6](https://github.com/Contrastive-LM/CLM/pull/6)).
- The server keeps the embeddings and projections of recent texts (`OPENJEV_CLM_EMBED_CACHE`,
  `OPENJEV_CLM_CACHE`). `usage.input_tokens` counts only the texts it had to embed, so a repeated
  request reports 0.
- Score questions can ignore the state. Upstream reports one level winning whatever the state
  says ([CLM issue #3](https://github.com/Contrastive-LM/CLM/issues/3)), and in our checks a
  thankful customer scored "annoyed". Choice and noul questions follow the state. Evaluate score
  questions on your own data before you rely on them.

## JevK5

[JevK5](https://github.com/allebee/jevk5) is Alibi Serikbay's model; the credit is theirs. The
[JevK5](https://huggingface.co/alibiserikbay/JevK5) checkpoint (v0.2) is Qwen3.5-4B with a LoRA
distilled from Qwen3.6-27B, merged. Each question becomes a JSON prompt with its options lettered
A to P, and the answer is a softmax over those letters' next-token logits under one calibration
temperature (1.532, from the checkpoint's `jevk5_config.json`). The readout is
[SemIf's](https://github.com/TheoLeeCJ/SemIf). A question with more than 16 options takes several
passes, combined as JevK5 combines them.

The `jevk5` image (`docker/Dockerfile.jevk5`) runs the checkpoint in bf16 on vLLM, the same pinned
commit as the main image without its changes, and asks it for the letters' logprobs. The prompt
and the combining of passes come from JevK5's own [`jevk5`](https://github.com/allebee/jevk5)
package (0.2.2). JevK5's own server reads one question at a time; here vLLM batches the questions
of all requests together.

```bash
docker build -f docker/Dockerfile.jevk5 -t openjev-jevk5 .   # after the base, as above
docker run -d --gpus all --ipc host -p 127.0.0.1:8084:8080 \
  -v ~/.cache/huggingface:/root/.cache/huggingface openjev-jevk5
```

Next to DiffusionGemma, run `docker compose --profile jevk5 up -d` and add
`jevk5-0.2=http://jevk5:8080` to `OPENJEV_MODEL_ROUTES`, as for CLM. `OPENJEV_JEVK5_GPU_UTIL`
(default 0.12) is its share of the GPU.

On JevBench's 231 public items, OpenJev and JevK5's own published v0.2 run gave the same top
answer on all 231 and the same input token count on all 231, so the prompts are identical.
Probabilities differed by 0.0012 at the median and 0.055 at most (vLLM's kernels are not
transformers'). Both scored 86.6%.

Measured on one RTX 3090 at `OPENJEV_GPU_UTIL=0.85` (7.9 GB of weights, 284k KV tokens), with the
same requests as for CLM:

| Request | 1 at a time | 32–64 at a time |
|---|---:|---:|
| 3 questions (a 4-way choice, a noul, a 3-level score), about 700 prompt tokens | 116 ms | 8–11 req/s, 8k prompt tokens/s |
| one 10-way choice | 75 ms | 20 req/s |

The GPU is the limit (100% busy at its 350 W cap). `--max-num-batched-tokens 8192` gave no gain.
Qwen3.5's linear-attention layers make vLLM cache prompts in blocks of 528 tokens, so questions
about a state shorter than that do not share its prefill; longer states do.

Differences from the other models:

- Text only, as for the encoder models. A read longer than 16,384 tokens gets a 400, never a cut.
- `usage.input_tokens` counts every pass, as JevK5 does: each question reads the state again.
- The image sets `VLLM_USE_FLASHINFER_SAMPLER=0`. A read takes one greedy token and keeps only
  logprobs, and FlashInfer's sampler would need a CUDA compiler the image does not have.

## Settings

The server reads its settings from the environment.

| Variable | Default | Meaning |
|---|---|---|
| `OPENJEV_BACKEND` | `vllm` | `mlx` to run the model in-process on Apple silicon. `laya` or `verdict` for a [small encoder model](#small-encoder-models), `clm` for [CLM](#clm), `jevk5` for [JevK5](#jevk5) |
| `OPENJEV_MODEL_ROUTES` | unset | `name=url,...`: other OpenJev servers. A request for one of these model names goes to that server unchanged. |
| `OPENJEV_FORWARD_TIMEOUT` | `300` | seconds before a request forwarded to another OpenJev server is a 503 |
| `OPENJEV_LAYA_MODEL` | `convaiinnovations/laya-typed-decisions` | Laya weights: a local directory or a Hugging Face id |
| `OPENJEV_VERDICT_MODEL` | `heman10x/rlcd-modernbert-151m` | Verdict weights: a local directory or a Hugging Face id |
| `OPENJEV_DEVICE` | unset | `laya`/`verdict`/`clm`: `cuda` or `cpu` (for `clm`, the heads). Unset uses CUDA when a GPU is present |
| `OPENJEV_ENCODER_BATCH` | `16` | `laya`/`verdict`: the most questions in one forward pass. A larger request uses more passes. |
| `OPENJEV_CLM_HEAD` | `Contrastive-LM/CLM-v0.1-8B` | `clm`: the heads, a Hugging Face repo holding `CLM_v0.1-8B.pt` or a local `.pt` file |
| `OPENJEV_CLM_MAX_TOKENS` | `2048` | `clm`: longest text sent to Qwen3-8B. A longer one loses its start. |
| `OPENJEV_CLM_WORKERS` | `32` | `clm`: requests read at once, so that vLLM batches them |
| `OPENJEV_CLM_CACHE` | `256MiB` | `clm`: GPU memory for cached projections, a size or a fraction of the GPU. `0` turns it off. |
| `OPENJEV_CLM_EMBED_CACHE` | `20000` | `clm`: embeddings kept in host memory (16 KB each) |
| `OPENJEV_JEVK5_WORKERS` | `32` | `jevk5`: reads in flight to vLLM at once |
| `OPENJEV_UPSTREAM` | unset | external vLLM server URL. When set, the container does not start its own |
| `OPENJEV_MODEL` | `nvidia/diffusiongemma-26B-A4B-it-NVFP4` | weights for the built-in vLLM. `Qwen/Qwen3-8B-FP8` for `clm`, `alibiserikbay/JevK5` for `jevk5` |
| `OPENJEV_MLX_MODEL` | `mlx-community/diffusiongemma-26B-A4B-it-4bit` | MLX weights: a local directory or a Hugging Face id. Also gives the tokenizer. `8bit` and `bf16` builds are also available. |
| `OPENJEV_MLX_MAX_PROMPT` | `32768` | longest request, in tokens, before a 400 |
| `OPENJEV_MLX_CACHE_LIMIT_GB` | unset | limit on the MLX buffer pool, in GB. Unset keeps the MLX default. `0` disables the pool. See [MLX memory](#mlx-memory) |
| `OPENJEV_MLX_PROMPT_CACHE` | `12` | cached prefills, in entries. `0` keeps none |
| `OPENJEV_GPU_UTIL` | `0.9` | vLLM `--gpu-memory-utilization`. `0.85` for `clm` and `jevk5` |
| `OPENJEV_MAX_NUM_SEQS` | `64` | vLLM `--max-num-seqs` |
| `OPENJEV_MAX_MODEL_LEN` | `65536` | vLLM `--max-model-len`. `2048` for `clm`, `16384` for `jevk5` |
| `OPENJEV_VLLM_ARGS` | unset | extra `vllm serve` flags |
| `OPENJEV_CANVAS` | `64` | canvas length. Also sets the built-in vLLM's `--diffusion-config` |
| `OPENJEV_MAX_INFLIGHT` | `64` | reads in flight to vLLM |
| `OPENJEV_MAX_QUEUE` | `512` | waiting decisions before the server returns 529 |
| `OPENJEV_MAX_QUESTIONS` | `256` | questions per request, before a 400 |
| `OPENJEV_MAX_BODY_BYTES` | `67108864` | request body size limit, before a 413 |
| `OPENJEV_API_KEY` | unset | require `Authorization: Bearer <key>` |
| `OPENJEV_ORIGIN_SECRET` | unset | require an `X-Origin-Secret` header (for use behind a proxy) |
| `OPENJEV_MAX_IMAGES` | `8` | images per request. Also sets the built-in vLLM's `--limit-mm-per-prompt` |
| `OPENJEV_MAX_IMAGE_BYTES` | `5242880` | size limit per image, after base64 decoding |
| `OPENJEV_GEN_MAX_INFLIGHT` | `8` | text generations that run at once |
| `OPENJEV_GEN_MAX_QUEUE` | `32` | waiting generations before the server returns 529 |
| `OPENJEV_GEN_MAX_TOKENS` | `8192` | cap on `max_tokens` for text generation |
| `OPENJEV_WARMUP` | `1` | `0` skips the warmup requests before the API opens. Warmup saves the first users several seconds of compilation. |

## Development

```bash
mise run test                                                          # server + UI tests, no model needed
OPENJEV_LIVE_URL=http://127.0.0.1:8080 .venv/bin/python -m pytest tests/test_live.py   # end to end against a running server
OPENJEV_MLX_TEST_MODEL=path/to/weights .venv/bin/python -m pytest tests/test_mlx_model.py   # MLX against the real model
```

Run the live checks after you build an image and before a cutover. They cover each read
option, images, chat, and the encoder models that the server lists.

## Caveats

- The image pins upstream vLLM and makes two changes. A build fails if either change no longer
  applies.
  - It raises the limit of exact label ids per request from 128 to 512, for choices of up to
    255 options.
  - `docker/patches/vision_prefix_lm.py` gives image tokens bidirectional attention, as the
    checkpoint config asks. Upstream vLLM does this for Gemma4 but not yet for DiffusionGemma.
- The `clm` and `jevk5` images pin the same vLLM commit with neither change.
- Answer quality is the quality of DiffusionGemma 26B-A4B in this mode. Evaluate it on your own
  tasks before you rely on it.
