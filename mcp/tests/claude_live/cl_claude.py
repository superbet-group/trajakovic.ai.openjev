"""`claude -p` runner (ARCHITECTURE 1.3/1.4): argv builder, process handling, transcript and wire-log parsing."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cl_env
from cl_servers import WireTap, parse_resp

PREFIX = "mcp__openjev__"


# ---- wire log --------------------------------------------------------------------------------------------------------

class Msg(dict):
    """A JSON-RPC `result` dict; `m["result"]` returns the dict itself so both `r["tools"]` and `r["result"]["tools"]` work."""

    def __missing__(self, key):
        if key == "result":
            return self
        raise KeyError(key)


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


class WireLog:
    """Entries: {method, name, headers (lower-case keys), req, params, status, resp: [JSON-RPC messages]}. Loads tap and probe formats."""

    def __init__(self, entries: list[dict] | None = None):
        self.entries = entries or []

    @classmethod
    def load(cls, path: Path | str) -> "WireLog":
        p = Path(path)
        out = []
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        out.append(cls._norm(json.loads(line)))
                    except ValueError:
                        pass
        return cls(out)

    @staticmethod
    def _norm(e: dict) -> dict:
        req = _loads(e.get("req"))
        first = req[0] if isinstance(req, list) and req else req
        first = first if isinstance(first, dict) else {}
        resp = e.get("resp")
        resp = parse_resp(resp) if isinstance(resp, str) else (resp or [])
        headers = {k.lower(): v for k, v in (e.get("headers") or {}).items()}
        params = first.get("params") if isinstance(first.get("params"), dict) else {}
        method = first.get("method") or (e.get("method") if e.get("method") not in (None, "GET", "POST", "PUT", "DELETE", "OPTIONS") else None)
        return {"method": method, "name": headers.get("mcp-name") or params.get("name"), "headers": headers,
                "req": first or None, "params": params, "status": e.get("status"), "resp": resp, "t": e.get("t")}

    def requests(self, method: str, name: str | None = None) -> list[dict]:
        return [e for e in self.entries if e["method"] == method and (name is None or e["name"] == name)]

    def _call_entry(self, tool_use_id: str) -> dict | None:
        for e in self.requests("tools/call"):
            if (e["params"].get("_meta") or {}).get("claudecode/toolUseId") == tool_use_id:
                return e
        return None

    def result(self, tool_use_id: str) -> Msg | None:
        e = self._call_entry(tool_use_id)
        if not e:
            return None
        rid = (e["req"] or {}).get("id")
        for m in e["resp"]:
            if m.get("id") == rid and isinstance(m.get("result"), dict):
                return Msg(m["result"])
        return None

    def progress(self, tool_use_id: str) -> list[dict]:
        e = self._call_entry(tool_use_id)
        if not e:
            return []
        tok = (e["params"].get("_meta") or {}).get("progressToken")
        return [m["params"] for m in e["resp"] if m.get("method") == "notifications/progress" and (tok is None or m["params"].get("progressToken") == tok)]

    def headers(self, i: int) -> dict:
        return self.entries[i]["headers"]

    def errors(self) -> list[dict]:
        """JSON-RPC error objects ({code, message, data}) from every response, with `_method` of the request."""
        return [{**m["error"], "_method": e["method"]} for e in self.entries for m in e["resp"] if isinstance(m.get("error"), dict)]

    def response(self, method: str, name: str | None = None) -> list[Msg]:
        """The JSON-RPC results (Msg) of every request of `method` (e.g. response("tools/list")[0]["tools"])."""
        out = []
        for e in self.requests(method, name):
            rid = (e["req"] or {}).get("id")
            out += [Msg(m["result"]) for m in e["resp"] if m.get("id") == rid and isinstance(m.get("result"), dict)]
        return out


# ---- transcript ------------------------------------------------------------------------------------------------------

@dataclass
class ToolUse:
    id: str
    name: str
    tool: str | None        # "batch" for mcp__openjev__batch; the plain name for built-ins ("Bash")
    input: dict


@dataclass
class ToolResult:
    id: str
    is_error: bool
    text: str
    json: Any | None
    error: dict | None
    links: list[str]


@dataclass
class Call:
    use: ToolUse
    result: ToolResult | None
    wire: dict | None
    tr: Any = field(default=None, repr=False, compare=False)   # owning Transcript (for failure summaries)


@dataclass
class Transcript:
    events: list = field(default_factory=list)
    init: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    final_text: str = ""
    subtype: str = ""
    is_error: bool = False
    num_turns: int = 0
    duration_ms: int = 0
    cost_usd: float = 0.0
    model: str = ""
    permission_denials: list = field(default_factory=list)
    wire: WireLog = field(default_factory=WireLog)
    hooks: dict = field(default_factory=dict)         # event -> parsed hook_<Event>.out lines
    hooks_in: dict = field(default_factory=dict)      # event -> parsed hook_<Event>.in lines
    stderr: str = ""
    rc: int = 0
    run_dir: Path = Path(".")
    audit: list = field(default_factory=list)         # OPENJEV_MCP_LOG lines appended during this run (D5)
    cwd: Path | None = None
    wall_ms: int = 0
    argv: list = field(default_factory=list)

    def of(self, tool: str) -> list[Call]:
        return [c for c in self.calls if c.use.tool == tool or c.use.name == tool]

    def tools_called(self) -> list[str]:
        return [c.use.tool or c.use.name for c in self.calls]


def _blocks(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def _result_of(block: dict) -> ToolResult:
    texts = [b.get("text", "") for b in _blocks(block.get("content")) if b.get("type") == "text"]
    parsed = None
    for t in reversed(texts):
        v = _loads(t) if t.strip()[:1] in "{[" else None
        if isinstance(v, (dict, list)):
            parsed = v
            break
    is_error = bool(block.get("is_error"))
    err = parsed["error"] if is_error and isinstance(parsed, dict) and isinstance(parsed.get("error"), dict) else None
    return ToolResult(block.get("tool_use_id", ""), is_error, "\n".join(texts), parsed, err, [t for t in texts if t.startswith("[Resource link:")])


def parse(events: list, wire: WireLog | None = None, run_dir: Path | None = None) -> Transcript:
    """Events of `claude -p --output-format json` (unknown event types are ignored)."""
    events = [e for e in events if isinstance(e, dict)]
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    res = next((e for e in reversed(events) if e.get("type") == "result"), {})
    uses: dict[str, ToolUse] = {}
    results: dict[str, ToolResult] = {}
    for e in events:
        content = (e.get("message") or {}).get("content")
        if e.get("type") == "assistant":
            for b in _blocks(content):
                if b.get("type") == "tool_use" and b.get("id") not in uses:
                    name = b.get("name", "")
                    uses[b["id"]] = ToolUse(b["id"], name, name[len(PREFIX):] if name.startswith(PREFIX) else name, b.get("input") or {})
        elif e.get("type") == "user":
            for b in _blocks(content):
                if b.get("type") == "tool_result":
                    results[b.get("tool_use_id", "")] = _result_of(b)
    wire = wire or WireLog()
    t = Transcript(events=events, init=init, result=res, final_text=res.get("result") or "", subtype=res.get("subtype") or "",
                   is_error=bool(res.get("is_error")), num_turns=res.get("num_turns") or 0, duration_ms=res.get("duration_ms") or 0,
                   cost_usd=float(res.get("total_cost_usd") or 0), model=init.get("model") or "",
                   permission_denials=res.get("permission_denials") or [], wire=wire, run_dir=run_dir or Path("."))
    t.calls = [Call(u, results.get(i), wire.result(i), t) for i, u in uses.items()]
    return t


# ---- argv and runner -------------------------------------------------------------------------------------------------

@dataclass
class ClaudeOpts:
    prompt: str
    model: str
    max_turns: int
    budget_usd: float
    mcp_url: str
    tools: str = ""
    allow: tuple = ("mcp__openjev",)
    skills: bool = False
    persist: bool = False
    settings: Path | None = None
    plugin_dir: Path | None = None
    cli: tuple = ()
    debug_file: Path | None = None


def argv(o: ClaudeOpts, run_dir: Path) -> list[str]:
    """ARCHITECTURE 1.3. Never --bare or --safe-mode (F4). All flags verified against `claude --help` (2.1.287): none dropped."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    mcp_json = run_dir / "mcp.json"
    mcp_json.write_text(json.dumps({"mcpServers": {"openjev": {"type": "http", "url": o.mcp_url}}}), encoding="utf-8")
    a = [cl_env.CLAUDE_BIN, "-p", o.prompt, "--output-format", "json", "--model", o.model, "--max-turns", str(o.max_turns),
         "--max-budget-usd", f"{o.budget_usd:g}", "--mcp-config", str(mcp_json), "--strict-mcp-config", "--setting-sources", "",
         "--permission-mode", "dontAsk", "--allowedTools", ",".join(o.allow), "--tools", o.tools, "--exclude-dynamic-system-prompt-sections"]
    if not o.skills:
        a.append("--disable-slash-commands")
    if not o.persist:
        a.append("--no-session-persistence")
    if o.settings:
        a += ["--settings", str(o.settings)]
    if o.plugin_dir:
        a += ["--plugin-dir", str(o.plugin_dir)]
    if o.debug_file:
        a += ["--debug-file", str(o.debug_file)]
    return a + list(o.cli)


def _read_hooks(run_dir: Path) -> tuple[dict, dict]:
    out, inn = {}, {}
    for p in sorted(run_dir.glob("hook_*.*")):
        ev, kind = p.name[5:].rsplit(".", 1)
        if kind not in ("out", "in"):
            continue
        rows = []
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                v = _loads(line)
                rows.append(v if isinstance(v, dict) else {"_raw": line})
        (out if kind == "out" else inn)[ev] = rows
    return out, inn


def _kill_group(proc: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(5)
            return
        except subprocess.TimeoutExpired:
            pass


def run_claude(prompt: str, *, ctx, profile: str = "default", tools: str = "", allow=("mcp__openjev",), tier: str = "cheap", max_turns: int = 4,
               timeout_s: int = 180, settings: Path | None = None, plugin_dir: Path | None = None, skills: bool = False, persist: bool = False,
               budget_usd: float | None = None, cli=()) -> Transcript:
    inst = ctx.mcp(profile)
    mark = inst.audit_mark()
    run_dir = Path(ctx.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    budget = (budget_usd if budget_usd is not None else (1.0 if tier == "strong" else 0.25)) * cl_env.BUDGET_SCALE
    model = cl_env.MODEL_STRONG if tier == "strong" else cl_env.MODEL_CHEAP
    env = cl_env.strip_env(dict(os.environ))
    with WireTap(inst.root_url, run_dir / "wire.jsonl") as tap:
        o = ClaudeOpts(prompt, model, max_turns, budget, tap.url, tools, tuple(allow), skills, persist, settings, plugin_dir, tuple(cli),
                       run_dir / "claude-debug.log" if cl_env.DEBUG else None)
        a = argv(o, run_dir)
        (run_dir / "argv.json").write_text(json.dumps(a, indent=1), encoding="utf-8")
        timed_out, t0 = False, time.monotonic()
        with cl_env.slot():
            t0 = time.monotonic()
            proc = subprocess.Popen(a, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(ctx.cwd), env=env,
                                    start_new_session=True, text=True)
            try:
                stdout, stderr = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_group(proc)
                stdout, stderr = proc.communicate()
        wall_ms = int((time.monotonic() - t0) * 1000)
    (run_dir / "claude.json").write_text(stdout or "", encoding="utf-8")
    (run_dir / "stderr.txt").write_text(stderr or "", encoding="utf-8")
    events = _loads(stdout) if stdout and stdout.strip() else None
    events = events if isinstance(events, list) else ([events] if isinstance(events, dict) else [])
    t = parse(events, WireLog.load(run_dir / "wire.jsonl"), run_dir)
    t.hooks, t.hooks_in = _read_hooks(run_dir)
    t.stderr, t.rc, t.cwd, t.wall_ms, t.argv = stderr or "", proc.returncode, Path(ctx.cwd), wall_ms, a
    t.audit = inst.audit_since(mark)
    if timed_out:
        t.subtype, t.is_error = "timeout", True
    return t
