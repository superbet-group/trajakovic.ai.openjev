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

Starts OpenJev on http://127.0.0.1:8080 and the UI on http://127.0.0.1:8090 in the background, waits for the model to load, and opens the UI in your browser.

## Tasks

| Command | What it does |
| --- | --- |
| `mise run install` | Install or update everything |
| `mise run start` (default) | Start OpenJev + UI, open the browser |
| `mise run stop` | Stop both |
| `mise run restart` | Stop, then start |
| `mise run status` | Is it running and ready? |
| `mise run logs` | Follow the logs (`logs server` or `logs ui` for one) |
| `mise run test` | Run the tests (no model needed) |
| `mise run benchmark` | Latency and accuracy against the running server |

## Settings

Set these env vars in front of a task, e.g. `OPENJEV_LOG_LEVEL=debug mise run restart`.

| Variable | Meaning |
| --- | --- |
| `OPENJEV_PORT` | OpenJev port (8080) |
| `UI_PORT` | UI port (8090) |
| `OPENJEV_LOG_LEVEL` | `debug` logs request and response bodies |
| `OPENJEV_API_KEY` | Require a key; the UI forwards it |
| `OPENJEV_NO_BROWSER=1` | Don't open the browser |
| `OPENJEV_START_TIMEOUT` | Seconds `start` waits for the model (600) |
| `HF_HOME` | Where the model is stored |
| `OPENJEV_SKIP_RAM_CHECK=1` | Try on a Mac with less than 24 GB |
| `OPENJEV_REINSTALL=1` | Force a package reinstall on `install` |
| `OPENJEV_SKIP_MODEL_DOWNLOAD=1` | Skip the model download on `install` |

More: [API](docs/api.md) · [Self-hosting, NVIDIA/Docker, all settings](docs/self-hosting.md) · [Playground UI](docs/ui.md)

## License

Apache-2.0; model and third-party credits in [docs/self-hosting.md](docs/self-hosting.md).
