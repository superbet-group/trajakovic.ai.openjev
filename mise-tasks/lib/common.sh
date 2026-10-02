# Shared helpers for OpenJev mise tasks. Sourced, never executed (keep mode 644).
# shellcheck shell=bash

if [ -z "${MISE_PROJECT_ROOT:-}" ]; then
  echo "error: run this through mise, e.g. mise run start" >&2
  exit 1
fi

OJ_ROOT="$(cd "$MISE_PROJECT_ROOT" && pwd -P)"
OJ_PY="$OJ_ROOT/.venv/bin/python"
OJ_RUN_DIR="${OPENJEV_RUN_DIR:-$OJ_ROOT}"
mkdir -p "$OJ_RUN_DIR"

export OPENJEV_PORT="${OPENJEV_PORT:-8080}"
export UI_PORT="${UI_PORT:-8090}"
export OPENJEV_MCP_PORT="${OPENJEV_MCP_PORT:-8100}"
OJ_SERVER_URL="http://127.0.0.1:$OPENJEV_PORT"
OJ_UI_URL="http://127.0.0.1:$UI_PORT"
OJ_MCP_URL="http://127.0.0.1:$OPENJEV_MCP_PORT"

OJ_SERVER_PIDFILE="$OJ_RUN_DIR/.openjev.pid"
OJ_SERVER_LOG="$OJ_RUN_DIR/.openjev.log"
OJ_UI_PIDFILE="$OJ_RUN_DIR/.openjev-ui.pid"
OJ_UI_LOG="$OJ_RUN_DIR/.openjev-ui.log"
OJ_MCP_PIDFILE="$OJ_RUN_DIR/.openjev-mcp.pid"
OJ_MCP_LOG="$OJ_RUN_DIR/.openjev-mcp.log"

OJ_MODEL="${OPENJEV_MLX_MODEL:-mlx-community/diffusiongemma-26B-A4B-it-4bit}"
OJ_MIN_RAM_GB=24
OJ_LOAD_RAM_GB=18
OJ_MODEL_DISK_GB=25
OJ_VENV_DISK_GB=3

# ---------- output ----------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  _OJ_B=$'\033[1m'; _OJ_G=$'\033[32m'; _OJ_Y=$'\033[33m'; _OJ_R=$'\033[31m'; _OJ_N=$'\033[0m'
else
  _OJ_B=""; _OJ_G=""; _OJ_Y=""; _OJ_R=""; _OJ_N=""
fi
oj_info() { printf '%sopenjev:%s %s\n' "$_OJ_B" "$_OJ_N" "$*"; }
oj_ok()   { printf '%sopenjev:%s %s%s%s\n' "$_OJ_B" "$_OJ_N" "$_OJ_G" "$*" "$_OJ_N"; }
oj_warn() { printf '%sopenjev:%s %swarning: %s%s\n' "$_OJ_B" "$_OJ_N" "$_OJ_Y" "$*" "$_OJ_N" >&2; }
oj_die()  { printf '%sopenjev:%s %serror: %s%s\n' "$_OJ_B" "$_OJ_N" "$_OJ_R" "$*" "$_OJ_N" >&2; exit 1; }

# ---------- service description ----------
oj_svc() {
  case "$1" in
    server)
      SVC_LABEL="OpenJev"; SVC_PIDFILE="$OJ_SERVER_PIDFILE"; SVC_LOG="$OJ_SERVER_LOG"
      SVC_PORT="$OPENJEV_PORT"; SVC_URL="$OJ_SERVER_URL"
      SVC_HEALTH="$OJ_SERVER_URL/v1/models"; SVC_KIND=server; SVC_MATCH="-m openjev" ;;
    ui)
      SVC_LABEL="UI"; SVC_PIDFILE="$OJ_UI_PIDFILE"; SVC_LOG="$OJ_UI_LOG"
      SVC_PORT="$UI_PORT"; SVC_URL="$OJ_UI_URL"
      SVC_HEALTH="$OJ_UI_URL/ui/api/config"; SVC_KIND=ui; SVC_MATCH="ui/server.py" ;;
    mcp)
      SVC_LABEL="MCP"; SVC_PIDFILE="$OJ_MCP_PIDFILE"; SVC_LOG="$OJ_MCP_LOG"
      SVC_PORT="$OPENJEV_MCP_PORT"; SVC_URL="$OJ_MCP_URL"
      SVC_HEALTH="$OJ_MCP_URL/health"; SVC_KIND=mcp; SVC_MATCH="-m openjev_mcp" ;;
    *) oj_die "internal: unknown service '$1'" ;;
  esac
}

# ---------- process identity ----------
oj_pid_cwd() {
  lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1
}

# oj_cmd_is CMD KIND: does a `ps` command line start with the venv interpreter
# immediately followed by exactly `-m openjev` (KIND=server), `ui/server.py`
# (KIND=ui) or `-m openjev_mcp` (KIND=mcp), at argv boundaries? Decoys such as `less ui/server.py`,
# `git commit -m openjev-fix`, `python -m openjev.warmup` or spawn.py's own
# argv (interpreter first, then spawn.py) do not match.
oj_cmd_is() {
  local cmd="$1" kind="$2" rest="" re='^/[^ ]*/\.venv/bin/python3?( .*)?$'
  case "$cmd" in
    "$OJ_ROOT"/.venv/bin/python3\ *) rest="${cmd#"$OJ_ROOT"/.venv/bin/python3}" ;;
    "$OJ_ROOT"/.venv/bin/python\ *)  rest="${cmd#"$OJ_ROOT"/.venv/bin/python}" ;;
    .venv/bin/python3\ *)            rest="${cmd#.venv/bin/python3}" ;;
    .venv/bin/python\ *)             rest="${cmd#.venv/bin/python}" ;;
    *)
      if [[ "$cmd" =~ $re ]]; then
        rest="${BASH_REMATCH[1]}"
      else
        return 1
      fi ;;
  esac
  case "$kind" in
    server) case "$rest" in " -m openjev"|" -m openjev "*) return 0 ;; esac ;;
    ui)     case "$rest" in " ui/server.py"|" ui/server.py "*|" $OJ_ROOT/ui/server.py"|" $OJ_ROOT/ui/server.py "*) return 0 ;; esac ;;
    mcp)    case "$rest" in " -m openjev_mcp"|" -m openjev_mcp "*) return 0 ;; esac ;;
  esac
  return 1
}

# oj_pid_is_ours PID KIND (server|ui|mcp)
oj_pid_is_ours() {
  local pid="${1:-}" kind="${2:-}" cmd cwd
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  [ -n "$kind" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
  oj_cmd_is "$cmd" "$kind" || return 1
  cwd="$(oj_pid_cwd "$pid")"
  [ "$cwd" = "$OJ_ROOT" ]
}

oj_listeners() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u || true
}

# oj_find SVC: print PID, return 0 if running and ours.
oj_find() {
  local pid p
  oj_svc "$1"
  if [ -f "$SVC_PIDFILE" ]; then
    pid="$(tr -d '[:space:]' < "$SVC_PIDFILE" 2>/dev/null || true)"
    if oj_pid_is_ours "$pid" "$SVC_KIND"; then
      echo "$pid"; return 0
    fi
    # Only forget the pidfile if the identity check could really run.
    command -v lsof >/dev/null 2>&1 && command -v ps >/dev/null 2>&1 || return 1
    rm -f "$SVC_PIDFILE"
  fi
  # Port-based adoption only for the default run dir; a custom OPENJEV_RUN_DIR is isolated.
  [ -z "${OPENJEV_RUN_DIR:-}" ] || return 1
  for p in $(oj_listeners "$SVC_PORT"); do
    if oj_pid_is_ours "$p" "$SVC_KIND"; then
      echo "$p" > "$SVC_PIDFILE"
      echo "openjev: adopted running $SVC_LABEL (PID $p)" >&2
      echo "$p"; return 0
    fi
  done
  # Still loading (port not open yet) and pidfile missing: look for our process.
  for p in $(pgrep -f -- "$SVC_MATCH" 2>/dev/null || true); do
    if oj_pid_is_ours "$p" "$SVC_KIND"; then
      echo "$p" > "$SVC_PIDFILE"
      echo "openjev: adopted running $SVC_LABEL (PID $p)" >&2
      echo "$p"; return 0
    fi
  done
  return 1
}

# oj_port_foreign PORT: describe a listener that is not ours.
oj_port_foreign() {
  local port="$1" p cmd
  for p in $(oj_listeners "$port"); do
    if oj_pid_is_ours "$p" server || oj_pid_is_ours "$p" ui || oj_pid_is_ours "$p" mcp; then
      continue
    fi
    cmd="$(ps -o command= -p "$p" 2>/dev/null || true)"
    echo "PID $p (${cmd:-unknown})"
    return 0
  done
  return 1
}

# ---------- http ----------
oj_http_up() {
  local code
  code="$(curl -s -o /dev/null -m 2 -w '%{http_code}' "$1" 2>/dev/null || true)"
  [ -n "$code" ] && [ "$code" != "000" ]
}

# oj_wait_up URL TIMEOUT_S PID: 0 up, 2 process died, 1 timeout
oj_wait_up() {
  local url="$1" timeout="$2" pid="$3" n=0
  case "$timeout" in ''|*[!0-9]*) oj_die "timeout must be a number of seconds (got '$timeout'; check OPENJEV_START_TIMEOUT)" ;; esac
  while :; do
    if oj_http_up "$url"; then return 0; fi
    if ! kill -0 "$pid" 2>/dev/null; then return 2; fi
    if [ "$n" -ge "$timeout" ]; then return 1; fi
    sleep 1
    n=$((n + 1))
    if [ $((n % 10)) -eq 0 ]; then echo "  still loading (${n}s)"; fi
  done
}

oj_tail() {
  [ -f "$SVC_LOG" ] && tail -n "$2" "$SVC_LOG"
  return 0
}

# ---------- spawn / stop ----------
# oj_spawn SVC: print PID
oj_spawn() {
  local pid
  oj_svc "$1"
  : > /dev/null
  if [ "$1" = server ]; then
    pid="$(PYTHONUNBUFFERED=1 OPENJEV_BACKEND="${OPENJEV_BACKEND:-mlx}" \
      "$OJ_PY" "$OJ_ROOT/mise-tasks/lib/spawn.py" --pidfile "$SVC_PIDFILE" --log "$SVC_LOG" \
      --cwd "$OJ_ROOT" -- "$OJ_PY" -m openjev)" || {
      oj_tail "$1" 30 >&2; oj_die "$SVC_LABEL failed to start (full log: $SVC_LOG)"; }
  elif [ "$1" = mcp ]; then
    pid="$(PYTHONUNBUFFERED=1 OPENJEV_BASE_URL="${OPENJEV_BASE_URL:-$OJ_SERVER_URL}" \
      "$OJ_PY" "$OJ_ROOT/mise-tasks/lib/spawn.py" --pidfile "$SVC_PIDFILE" --log "$SVC_LOG" \
      --cwd "$OJ_ROOT" -- "$OJ_PY" -m openjev_mcp --transport http --port "$OPENJEV_MCP_PORT")" || {
      oj_tail "$1" 30 >&2; oj_die "$SVC_LABEL failed to start (full log: $SVC_LOG)"; }
  else
    pid="$(PYTHONUNBUFFERED=1 OPENJEV_URL="${OPENJEV_URL:-$OJ_SERVER_URL}" \
      "$OJ_PY" "$OJ_ROOT/mise-tasks/lib/spawn.py" --pidfile "$SVC_PIDFILE" --log "$SVC_LOG" \
      --cwd "$OJ_ROOT" -- "$OJ_PY" ui/server.py)" || {
      oj_tail "$1" 30 >&2; oj_die "$SVC_LABEL failed to start (full log: $SVC_LOG)"; }
  fi
  echo "$pid"
}

oj_stop() {
  local pid i foreign
  oj_svc "$1"
  if ! pid="$(oj_find "$1")"; then
    echo "$SVC_LABEL is not running."
    rm -f "$SVC_PIDFILE"
  else
    kill -TERM "$pid" 2>/dev/null || true
    i=0
    while [ "$i" -lt 40 ] && kill -0 "$pid" 2>/dev/null; do
      sleep 0.5
      i=$((i + 1))
    done
    if kill -0 "$pid" 2>/dev/null && oj_pid_is_ours "$pid" "$SVC_KIND"; then
      oj_warn "$SVC_LABEL (PID $pid) ignored SIGTERM for 20s; sending SIGKILL"
      kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -f "$SVC_PIDFILE"
    echo "Stopped $SVC_LABEL (PID $pid)."
  fi
  if foreign="$(oj_port_foreign "$SVC_PORT")"; then
    echo "Port $SVC_PORT is used by another process, $foreign; left untouched."
  fi
  return 0
}

# ---------- preflight ----------
oj_require_macos_arm64() {
  local major
  if [ "$(uname -s)" != "Darwin" ]; then
    oj_die "OpenJev's mise tasks support macOS on Apple silicon only; on Linux with an NVIDIA GPU see docs/self-hosting.md (Docker)"
  fi
  if [ "$(uname -m)" != "arm64" ]; then
    if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then
      oj_die "this terminal runs under Rosetta (x86_64); open a native arm64 terminal"
    fi
    oj_die "Apple silicon (M1 or newer) is required; this is an Intel Mac"
  fi
  major="$(sw_vers -productVersion | cut -d. -f1)"
  case "$major" in ''|*[!0-9]*) major=0 ;; esac
  if [ "$major" -lt 14 ]; then
    oj_die "MLX needs macOS 14 or newer (this is $(sw_vers -productVersion))"
  fi
}

oj_require_cmds() {
  local c missing=""
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || missing="$missing $c"
  done
  [ -z "$missing" ] || oj_die "missing required command(s):$missing"
}

# Tools come from the task PATH (mise puts the pinned python/node there via
# auto_install); the mise binary itself is not required to be on PATH.
oj_tool_python() { command -v python3 2>/dev/null || true; }

oj_require_mise_tool() {
  local tool="$1" bin ver major
  case "$tool" in
    python)
      bin="$(oj_tool_python)"
      [ -n "$bin" ] || oj_die "python3 is not available in the mise environment. Check that mise is installed and trusted for this project (mise trust), and that ~/.local/bin is on PATH or mise is activated; then re-run"
      ver="$("$bin" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
      case "$ver" in
        3.12.*) ;;
        *) oj_die "python 3.12 from mise is not active (found '${ver:-none}' at $bin). Check that mise is activated/trusted for this project (mise trust) and re-run" ;;
      esac ;;
    node)
      bin="$(command -v node 2>/dev/null || true)"
      [ -n "$bin" ] || oj_die "node is not available in the mise environment. Check that mise is installed and trusted for this project (mise trust); then re-run"
      ver="$("$bin" -v 2>/dev/null | sed 's/^v//')"
      major="${ver%%.*}"
      case "$major" in ''|*[!0-9]*) major=0 ;; esac
      [ "$major" -ge 22 ] || oj_die "node >= 22 from mise is not active (found '${ver:-none}' at $bin). Check that mise is activated/trusted for this project and re-run" ;;
    *) oj_die "internal: unknown tool '$tool'" ;;
  esac
}

# Only for the rare case a task must call mise itself.
oj_mise_bin() {
  local m
  m="$(command -v mise 2>/dev/null || true)"
  [ -n "$m" ] || for m in "$HOME/.local/bin/mise" /opt/homebrew/bin/mise /usr/local/bin/mise; do
    [ -x "$m" ] && break
    m=""
  done
  [ -n "$m" ] || oj_die "mise was not found (looked on PATH, ~/.local/bin, /opt/homebrew/bin, /usr/local/bin). Install it from https://mise.jdx.dev, or add its directory to PATH, then re-run"
  echo "$m"
}

oj_installed() {
  [ -x "$OJ_PY" ] && "$OJ_PY" -c 'import openjev, fastapi, uvicorn, httpx, mlx_vlm' >/dev/null 2>&1
}

oj_require_installed() {
  oj_installed || oj_die "OpenJev is not installed. Run: mise run install"
}

oj_mcp_installed() {
  [ -x "$OJ_PY" ] && "$OJ_PY" -c 'import openjev_mcp, mcp, re2, jsonschema' >/dev/null 2>&1
}

oj_require_mcp_installed() {
  oj_mcp_installed || oj_die "the MCP server is not installed. Run: mise run install"
}

oj_total_ram_gb() {
  echo $(( $(sysctl -n hw.memsize) / 1073741824 ))
}

oj_avail_ram_gb() {
  vm_stat | awk '/page size of/ {for(i=1;i<=NF;i++) if($i=="of") ps=$(i+1)} /^Pages (free|inactive|speculative|purgeable)/ {v=$NF; gsub(/\./,"",v); s+=v} END {printf "%d\n", s*ps/1073741824}'
}

oj_check_ram() {
  local total avail
  total="$(oj_total_ram_gb)"
  if [ "$total" -lt "$OJ_MIN_RAM_GB" ]; then
    if [ "${OPENJEV_SKIP_RAM_CHECK:-}" = "1" ]; then
      oj_warn "this Mac has $total GB of memory; the MLX model needs about 16 GB to load and more while serving (continuing because OPENJEV_SKIP_RAM_CHECK=1)"
    else
      oj_die "this Mac has $total GB of memory; the MLX model needs about 16 GB to load and more while serving, so 24 GB is the minimum. Set OPENJEV_SKIP_RAM_CHECK=1 to try anyway"
    fi
  fi
  avail="$(oj_avail_ram_gb)"
  if [ "$avail" -lt "$OJ_LOAD_RAM_GB" ]; then
    oj_warn "only $avail GB of memory is free; loading needs about 16 GB. Close other apps if loading fails"
  fi
  return 0
}

oj_hf_hub_dir() {
  echo "${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
}

oj_model_cached() {
  local d ref snap shard
  [ -d "$OJ_MODEL" ] && return 0
  d="$(oj_hf_hub_dir)/models--$(printf %s "$OJ_MODEL" | sed 's#/#--#g')"
  [ -f "$d/refs/main" ] || return 1
  ref="$(tr -d '[:space:]' < "$d/refs/main")"
  snap="$d/snapshots/$ref"
  [ -e "$snap/config.json" ] || return 1
  if [ -e "$snap/model.safetensors.index.json" ]; then
    for shard in $(grep -o '"[^"]*\.safetensors"' "$snap/model.safetensors.index.json" | tr -d '"' | sort -u); do
      [ -e "$snap/$shard" ] || return 1
    done
    return 0
  fi
  ls "$snap"/*.safetensors >/dev/null 2>&1 || return 1
  [ -e "$(ls "$snap"/*.safetensors | head -1)" ]
}

oj_free_disk_gb() {
  local d="$1"
  while [ ! -d "$d" ] && [ "$d" != "/" ]; do d="$(dirname "$d")"; done
  df -Pk "$d" | awk 'NR==2{print int($4/1048576)}'
}
