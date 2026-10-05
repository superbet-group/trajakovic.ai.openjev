# OpenJev

Open, Jev-compatible "System One" decision server (DiffusionGemma 26B-A4B), run locally on Apple silicon with MLX, plus a web playground.

## Requirements

- macOS 14+ on Apple silicon (M1 or newer)
- 24 GB+ memory (the MLX model needs about 16 GB to load)
- About 25 GB free disk for the first install (the model is 16.5 GB, stored in the Hugging Face cache `~/.cache/huggingface/hub`, or `HF_HOME`)
- [mise](https://mise.jdx.dev)

Nothing else: no system Python, Node or Homebrew packages are needed. mise installs Python 3.12 and Node 24 (from `mise.toml`), and `mise run install` sets up the Python packages in `.venv` and the model.

## 1. Install mise

```sh
curl https://mise.run | sh        # installs ~/.local/bin/mise (or: brew install mise)
```

## 2. Activate mise in your shell

```sh
echo 'eval "$(~/.local/bin/mise activate zsh)"' >> ~/.zshrc && exec zsh     # zsh (macOS default)
# bash: echo 'eval "$(~/.local/bin/mise activate bash)"' >> ~/.bashrc
# Homebrew mise: use `mise activate zsh` (no path)
mise doctor                      # should report: activated: yes
```

## 3. Trust the project and install

```sh
git clone git@github.com:superbet-group/trajakovic.ai.openjev.git openjev && cd openjev
mise trust                       # allow this repo's mise.toml
mise run install                 # Python 3.12, Node 24, packages in .venv, the MLX model (~16.5 GB); safe to re-run
```

`install` checks the Mac (Apple silicon, macOS version, memory, disk) and stops with a fix-it message if something is missing. Set `HF_TOKEN=...` to speed up the model download.

## 4. Run

```sh
mise run start                   # or just: mise run
```

Starts OpenJev on http://127.0.0.1:8080, the UI on http://127.0.0.1:8090 and the MCP server on http://127.0.0.1:8100 in the background, waits for the model to load, and opens the UI in your browser.

## Tasks

| Command | What it does |
| --- | --- |
| `mise run install` | Install or update everything |
| `mise run start` (default) | Start OpenJev + UI + MCP, open the browser |
| `mise run mcp` | Start only the MCP server |
| `mise run stop` | Stop all three |
| `mise run restart` | Stop, then start |
| `mise run status` | Is it running and ready? |
| `mise run logs` | Follow the logs (`logs server`, `logs ui` or `logs mcp` for one) |
| `mise run test` | Run the tests (no model needed) |
| `mise run benchmark` | Latency and accuracy against the running server |

## Settings

Set these env vars in front of a task, e.g. `OPENJEV_LOG_LEVEL=debug mise run restart`.

| Variable | Meaning |
| --- | --- |
| `OPENJEV_PORT` | OpenJev port (8080) |
| `UI_PORT` | UI port (8090) |
| `OPENJEV_MCP_PORT` | MCP server port (8100) |
| `OPENJEV_MCP_TOKEN` | Require this bearer token on the MCP endpoint |
| `OPENJEV_LOG_LEVEL` | `debug` logs request and response bodies |
| `OPENJEV_API_KEY` | Require a key; the UI forwards it |
| `OPENJEV_NO_BROWSER=1` | Don't open the browser |
| `OPENJEV_START_TIMEOUT` | Seconds `start` waits for the model (600) |
| `OPENJEV_MLX_CACHE_LIMIT_GB` | MLX buffer pool ceiling. `start` defaults it to 4, which keeps memory near 24 GB with the 4-bit model. Raise it for the 8-bit or bf16 weights, or set it empty to leave MLX alone |
| `HF_HOME` | Where the model is stored |
| `OPENJEV_SKIP_RAM_CHECK=1` | Try on a Mac with less than 24 GB |
| `OPENJEV_REINSTALL=1` | Force a package reinstall on `install` |
| `OPENJEV_SKIP_MODEL_DOWNLOAD=1` | Skip the model download on `install` |

## MCP server

A separate process on its own port, for agents (Claude Code and other MCP clients). It calls OpenJev over HTTP and loads no model. It serves 14 tools (typed reads, `filter`, resumable `batch` jobs, `ask_image`, `recipe` gates, `calibrate`), resources, prompts, four Claude Code hooks (`openjev-hook`) and 12 skills.

```sh
claude mcp add --transport http openjev http://127.0.0.1:8100/mcp
```

See [mcp/README.md](mcp/README.md) for the tool surface, stdio, hooks, batch jobs, skills and settings.

### Claude Code skills

The 12 `openjev-*` skills teach a Claude Code session in any project how to prepare data for the MCP (state and question formats, each tool's input and output, chaining, batch items files). They ship as the plugin `openjev-skills` in the marketplace `openjev` of this repository:

```sh
claude plugin marketplace add /path/to/openjev        # or owner/repo
claude plugin install openjev-skills@openjev
```

Working in this repository: `mise run skills-link` (symlinks into the gitignored `.claude/skills/`) or `claude --plugin-dir plugins/openjev-skills`. Install guide and the optional `openjev-mcp` connector plugin: [plugins/openjev-skills/README.md](plugins/openjev-skills/README.md); effectiveness score: [mcp/tests/skills_eval/README.md](mcp/tests/skills_eval/README.md).

More: [API](docs/api.md) · [Self-hosting, NVIDIA/Docker, all settings](docs/self-hosting.md) · [Playground UI](docs/ui.md)

## License

Apache-2.0; model and third-party credits in [docs/self-hosting.md](docs/self-hosting.md).
