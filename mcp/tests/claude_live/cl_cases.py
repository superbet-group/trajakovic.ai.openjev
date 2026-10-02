"""Case model, Ctx, data-file loader, run_case (retry per ARCHITECTURE 2.3, results row per 1.8), catalogue and COVERAGE."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import cl_assert as A
import cl_env
import cl_results
from cl_claude import Transcript, run_claude
from cl_servers import Instances, McpInstance

sys.path.insert(0, str(cl_env.REPO / "mcp/tests/live"))
import run_live as rl  # noqa: E402  (reused: call, sc_of, make_csv, QS, read_rows, dup_gap)

GROUPS: dict[str, tuple[str, int, int]] = {
    "g01": ("protocol", 1, 8), "g02": ("reads", 9, 18), "g03": ("tools", 19, 28), "g04": ("batch", 29, 40), "g05": ("calibrate", 41, 48),
    "g06": ("recipes", 49, 61), "g07": ("resources_prompts", 62, 72), "g08": ("hooks", 73, 82), "g09": ("skills", 83, 90), "g10": ("errors", 91, 100),
}


def group_file(gid: str) -> Path:
    return cl_env.HERE / f"test_{gid}_{GROUPS[gid][0]}.py"


def _coverage() -> frozenset[str]:
    from openjev_mcp import CORE_TOOL_NAMES, TOOL_NAMES, prompts, resources  # noqa: F401
    cov = {f"tool:{t}" for t in TOOL_NAMES}
    cov |= {f"resource:{r['uri']}" for r in resources.listing()}
    cov |= {f"template:{t['uriTemplate']}" for t in resources.templates_listing()}
    cov.add("file://")
    cov |= {f"prompt:{p['name']}" for p in prompts.listing()}
    cov |= {f"recipe:{p.stem}" for p in (cl_env.REPO / "mcp/openjev_mcp/recipes/builtin").glob("*.json")}
    cov |= {f"hook:{e}" for e in ("PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit")}
    cov.add("skills:loaded")
    cov |= {f"skill:openjev-{x}" for x in ("agent-gates", "decisions", "triage-routing", "data-records", "question-authoring", "calibration")}
    cov |= {f"error:OJ_INVALID_INPUT:{x}" for x in ("schema", "path", "roots", "lint", "cursor")}
    cov |= {f"error:{c}" for c in ("OJ_UNREACHABLE", "OJ_NOT_FOUND", "OJ_TIMEOUT", "OJ_AUTH", "OJ_UNAVAILABLE", "OJ_OVERLOADED", "OJ_SERVER", "OJ_UNKNOWN_MODEL")}
    cov.add("jsonrpc:-32602")
    cov |= {f"profile:{p}" for p in ("core", "tasks", "ext", "unreachable", "notopenjev", "fault")}
    cov |= {"loop:batch_cursor", "loop:batch_resume"}
    return frozenset(cov)


COVERAGE: frozenset[str] = _coverage()


@dataclass(frozen=True)
class Case:
    id: str
    slug: str
    group: str
    feature: str
    spec: str
    prompt: str
    expect: tuple
    primary: tuple
    profile: str = "default"
    tools: str = ""
    allow: tuple = ("mcp__openjev",)
    tier: str = "cheap"
    max_turns: int = 4
    timeout_s: int = 180
    budget_usd: float | None = None
    setup: Callable | None = None
    hooks: dict | None = None
    skills: bool = False
    persist: bool = False
    xfail: str | None = None
    covers: tuple = ()
    pins: dict = field(default_factory=dict)
    args: Any = None
    cli: tuple = ()


_PH = re.compile(r"\{(cwd|work|data|fixtures|repo|args|arg:[^{}]+|seed:[^{}]+)\}")


def compact(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


@dataclass
class Ctx:
    case: Case
    attempt: int
    run_id: str
    work: Path
    cwd: Path
    run_dir: Path
    data: Path
    fixtures: Path
    repo: Path
    base_url: str
    instances: Instances
    seed: dict = field(default_factory=dict)

    def mcp(self, profile: str = "default") -> McpInstance:
        return self.instances.get(profile)

    def call_tool(self, tool: str, args: dict, profile: str = "default", timeout: int = 300) -> dict:
        """Sync SDK call straight to the instance (no WireTap): {is_error, structured, text, content}."""
        import anyio
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        url = self.mcp(profile).url

        async def go():
            async with streamable_http_client(url) as (r, w, *_):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res, _ = await rl.call(s, tool, args, timeout)
                    return {"is_error": bool(res.is_error), "structured": res.structured_content,
                            "text": "\n".join(getattr(c, "text", "") or "" for c in res.content), "content": [c.model_dump(mode="json") for c in res.content]}
        return anyio.run(go)

    def read_resource(self, uri: str, profile: str = "default") -> dict:
        import anyio
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        url = self.mcp(profile).url

        async def go():
            async with streamable_http_client(url) as (r, w, *_):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return (await s.read_resource(uri)).model_dump(mode="json")   # str accepted by the SDK 2.x
        return anyio.run(go)

    def _sub(self, v: Any) -> Any:
        if isinstance(v, str):
            return self.render(v, _args=False)
        if isinstance(v, list):
            return [self._sub(x) for x in v]
        if isinstance(v, dict):
            return {k: self._sub(x) for k, x in v.items()}
        return v

    def render(self, s: str, _args: bool = True) -> str:
        """D2: regex over {cwd} {work} {data} {fixtures} {repo} {args} {arg:KEY} {seed:KEY}; any other brace text is left alone."""
        def rep(m: re.Match) -> str:
            k = m.group(1)
            if k in ("cwd", "work", "data", "fixtures", "repo"):
                return str(getattr(self, k))
            if k == "args":
                return compact(self._sub(self.case.args)) if _args else m.group(0)
            kind, _, key = k.partition(":")
            src = self.case.args if kind == "arg" else self.seed
            if not isinstance(src, dict) or key not in src:
                raise KeyError(f"{self.case.id}: {{{k}}} has no value")
            v = self._sub(src[key]) if kind == "arg" else src[key]
            return v if isinstance(v, str) else compact(v)
        return _PH.sub(rep, s)


def load_group(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


_TUPLES = ("primary", "covers", "allow", "cli")


def case_from_data(path: Path, tid: str, *, expect, setup=None, **overrides) -> Case:
    d = load_group(path)
    raw = dict(d["cases"][tid])
    known = {f.name for f in fields(Case)}
    unknown = set(raw) - known
    if unknown:
        raise TypeError(f"{path.name} {tid}: unknown keys {sorted(unknown)}")
    kw = {**raw, **overrides}
    for k in _TUPLES:
        if k in kw:
            kw[k] = tuple(kw[k])
    kw.setdefault("primary", ())
    return Case(id=tid, group=d["group"], expect=tuple(expect) if isinstance(expect, (list, tuple)) else (expect,), setup=setup, **kw)


# ---- running ---------------------------------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expected_tools(case: Case) -> list[str]:
    from openjev_mcp import CORE_TOOL_NAMES, TOOL_NAMES
    return list(CORE_TOOL_NAMES if case.profile == "core" else TOOL_NAMES)


def _prune(ctx: Ctx) -> None:
    keep = {"argv.json", "claude.json", "wire.jsonl"}
    for p in ctx.run_dir.iterdir():
        if p.name not in keep:
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    shutil.rmtree(ctx.cwd, ignore_errors=True)


def _evidence(ctx: Ctx, case: Case) -> None:
    """Failing attempt: keep the run dir whole, add the instance log tail and the cwd listing."""
    try:
        (ctx.run_dir / "mcp-tail.txt").write_text(ctx.mcp(case.profile).tail(200), encoding="utf-8")
        (ctx.run_dir / "cwd-listing.txt").write_text("\n".join(str(p.relative_to(ctx.cwd)) for p in sorted(ctx.cwd.rglob("*"))), encoding="utf-8")
    except Exception:
        pass


def _row(case: Case, ctx: Ctx, t: Transcript | None, **kw) -> dict:
    row = {"id": case.id, "slug": case.slug, "group": case.group, "attempt": ctx.attempt, "retry_of": None, "retry_reason": None,
           "model": t.model if t else None, "tier": case.tier, "profile": case.profile, "pass": False, "failure": None, "error_class": None,
           "tools": t.tools_called() if t else [], "num_turns": t.num_turns if t else 0, "duration_ms": t.duration_ms if t else 0,
           "wall_ms": t.wall_ms if t else 0, "cost_usd": t.cost_usd if t else 0, "subtype": t.subtype if t else None,
           "denials": len(t.permission_denials) if t else 0, "run_dir": str(ctx.run_dir.relative_to(cl_results.run_root())) if ctx.run_dir.is_relative_to(cl_results.run_root()) else str(ctx.run_dir),
           "started": _now()}
    row.update(kw)
    return row


def _attempt(case: Case, ctx: Ctx):
    """One attempt. Returns (transcript, error_class|None, failure|None, expects_ran)."""
    t = None
    try:
        if case.setup:
            case.setup(ctx)
        prompt = ctx.render(case.prompt)
        settings = plugin_dir = None
        if case.hooks or case.skills:
            try:
                import cl_hooks   # H2, lazy (D6)
            except ImportError as e:
                return None, "harness", f"harness: cl_hooks (H2) unavailable: {e}"
            settings = cl_hooks.settings_for(case, ctx) if case.hooks else None
            plugin_dir = cl_hooks.plugin_dir(ctx) if case.skills else None
        t = run_claude(prompt, ctx=ctx, profile=case.profile, tools=case.tools, allow=case.allow, tier=case.tier, max_turns=case.max_turns,
                       timeout_s=case.timeout_s, settings=settings, plugin_dir=plugin_dir, skills=case.skills, persist=case.persist,
                       budget_usd=case.budget_usd, cli=case.cli)
    except A.HarnessError as e:
        return t, "harness", str(e)
    except Exception as e:   # setup / render / spawn failures are harness problems
        return t, "harness", f"harness: {type(e).__name__}: {e}"
    return t, None, None


def run_case(case: Case, ctx_factory) -> Transcript:
    attempt, prev = 1, None
    while True:
        ctx = ctx_factory(case, attempt)
        t, eclass, failure = _attempt(case, ctx)
        extra = {"retry_of": prev[0], "retry_reason": prev[1]} if prev else {}
        if t is not None and eclass is None:
            if t.subtype == "timeout":
                eclass, failure = "timeout", f"claude exceeded {case.timeout_s}s and was killed"
            else:
                try:
                    if not t.init or not t.result:
                        A.fail(t, f"harness: claude ended without init/result (rc={t.rc}); stderr: {t.stderr[-400:]!r}", harness=True)
                    A.precondition(t, _expected_tools(case))
                except A.HarnessError as e:
                    eclass, failure = "harness", str(e)
        if eclass is None:
            called = set(t.tools_called())
            primary_missing = bool(case.primary) and not (called & set(case.primary))
            if primary_missing and not t.permission_denials:
                if attempt == 1:   # A 2.3: the one automatic retry for a flaky model choice
                    cl_results.append(_row(case, ctx, t, error_class="model_choice", failure="model_did_not_call", **extra))
                    _evidence(ctx, case)
                    attempt, prev = 2, (1, "model_did_not_call")
                    continue
                eclass, failure = "model_choice", f"model never called {list(case.primary)} (after retry)\n" + A.summary(t)
        if eclass is None:
            for fn in case.expect:
                try:
                    fn(t, ctx)
                except AssertionError as e:
                    eclass = "harness" if isinstance(e, A.HarnessError) else "assertion"
                    failure = str(e) if "run_dir:" in str(e) else f"{e}\n{A.summary(t)}"
                    break
        if eclass is None:
            cl_results.append(_row(case, ctx, t, **{"pass": True}, **extra))
            if not cl_env.KEEP:
                _prune(ctx)
            return t
        _evidence(ctx, case)
        cl_results.append(_row(case, ctx, t, error_class=eclass, failure=failure[:2000], **extra))
        raise (A.HarnessError if eclass == "harness" else AssertionError)(f"[{case.id} {case.slug}] {eclass}: {failure}")


# ---- catalogue -------------------------------------------------------------------------------------------------------

def load_catalogue() -> dict[str, list[Case]]:
    """CASES of every test_gNN_*.py present, imported by path."""
    out: dict[str, list[Case]] = {}
    for gid in GROUPS:
        p = group_file(gid)
        if not p.exists():
            continue
        spec = importlib.util.spec_from_file_location(f"cl_catalogue_{gid}", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out[gid] = list(mod.CASES)
    return out
