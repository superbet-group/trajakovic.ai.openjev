"""Live evaluator: haiku `claude -p` runs of the scenarios with the openjev-skills plugin vs a no-plugin baseline.

    OPENJEV_CLAUDE_LIVE=1 .venv/bin/python mcp/tests/skills_eval/live_eval.py [--only L01,L12] [--arm both|skill|baseline]
        [--runs 1] [--model haiku] [--max-cost-usd 6] [--mcp-url http://127.0.0.1:8100/mcp] [--private-instances]

(also reachable as ``run_eval.py live ...`` through :func:`main`). Exits 2 without OPENJEV_CLAUDE_LIVE=1 and spends nothing.

Design
  * Reuses claude_live unchanged: cl_claude.run_claude (WireTap, --strict-mcp-config, --setting-sources "", env stripping),
    cl_cases.Ctx, cl_servers.Instances (private mode only). The two arms differ ONLY in the plugin:
    skill = skills=True + --plugin-dir <plugins/openjev-skills>; baseline = skills=False, no plugin. Same prompt (the
    explicit `/openjev-skills:<skill> ` prefix is dropped for the baseline), tools, allow list, model and budget.
  * Default MCP: the already running daemon (--mcp-url, through WireTap). It is never started, stopped or restarted.
    --private-instances spawns an own openjev-mcp (roots = the run work dir) instead.
  * Server-side paths live under <repo>/logs/skills-eval/<run_id>/ (no dot-directories; the daemon refuses those).
    Reports go to <repo>/.tmp/skills-eval/<run_id>/live/results.json.
  * Preconditions are harness errors, not scores: apiKeySource none; openjev connected (absent for mcp:false); skill arm lists
    every openjev-skills:<name> of the plugin; baseline lists no openjev- skill.
  * Scoring: scenario score = passed checks / checks (partial credit). Arms are run as adjacent (skill, baseline) pairs;
    uplift is computed on paired scenarios only. Headline = implicit subset; explicit and procedural subsets and a
    bootstrap 90% CI of the paired uplift are reported too. Re-runs re-run both arms; every run is kept in the report.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for _p in (REPO / "mcp/tests/claude_live", REPO / "mcp/tests/live", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import live_scenarios as LS  # noqa: E402

DEFAULT_MCP_URL = "http://127.0.0.1:8100/mcp"
PLUGIN_PREFIX = "openjev-skills:"
EXPLICIT_PREFIX = re.compile(r"^/openjev-skills:[\w-]+\s+")
OUT_ROOT = REPO / ".tmp" / "skills-eval"
WORK_ROOT = REPO / "logs" / "skills-eval"
EXIT_NO_GATE, EXIT_ENV = 2, 3


# ---- MCP endpoint (default: the running daemon; never started or stopped here) ---------------------------------------

class ExternalInstance:
    """Duck-types cl_servers.McpInstance for a daemon this process does not own."""

    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.root_url = self.url[: -len("/mcp")] if self.url.endswith("/mcp") else self.url
        self.profile, self.proc = "default", None

    def audit_mark(self) -> int:
        return 0

    def audit_since(self, mark: int) -> list:
        return []

    def tail(self, n: int = 200) -> str:
        return ""

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.root_url + "/health", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False


class ExternalInstances:
    def __init__(self, url: str):
        self.inst = ExternalInstance(url)
        self.base_url = None

    def get(self, profile: str = "default") -> ExternalInstance:
        return self.inst

    def stop_all(self) -> None:   # the daemon is not ours
        pass


# ---- arms ----------------------------------------------------------------------------------------------------------------

def _skill_names(init: dict) -> list[str]:
    return [s if isinstance(s, str) else (s.get("name") or "") for s in init.get("skills") or []]


def _precondition(t, scenario: dict, arm: str, expected: list[str]) -> str | None:
    """Harness-error text, or None."""
    if not t.init or not t.result:
        return f"claude ended without init/result (rc={t.rc}); stderr: {t.stderr[-300:]!r}"
    if t.init.get("apiKeySource") != "none":
        return f"apiKeySource={t.init.get('apiKeySource')!r}, expected 'none' (subscription login)"
    srv = {s.get("name"): s.get("status") for s in t.init.get("mcp_servers") or []}
    if scenario["mcp"] and srv.get("openjev") != "connected":
        return f"mcp server openjev not connected: {srv}"
    if not scenario["mcp"] and srv.get("openjev") == "connected":
        return f"mcp:false scenario but openjev is connected: {srv}"
    names = _skill_names(t.init)
    if arm == "skill":
        missing = sorted({PLUGIN_PREFIX + n for n in expected} - set(names))
        if missing:
            return f"skill arm lacks {missing}"
    else:
        leaked = [n for n in names if "openjev-" in n]
        if leaked:
            return f"baseline arm lists openjev skills: {leaked}"
    return None


def _patch_argv():
    """mcp:false scenarios: same argv, but mcp.json lists no server (WireTap still starts, nothing connects)."""
    import cl_claude
    if getattr(cl_claude, "_orig_argv", None):
        return cl_claude
    cl_claude._orig_argv = cl_claude.argv
    cl_claude._NO_MCP = False

    def argv(o, run_dir):
        a = cl_claude._orig_argv(o, run_dir)
        if cl_claude._NO_MCP:
            (Path(run_dir) / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        return a
    cl_claude.argv = argv
    return cl_claude


def _prompt_for(scenario: dict, arm: str, cwd: Path) -> str:
    p = LS.render(scenario["prompt"], cwd)
    return p if arm == "skill" else EXPLICIT_PREFIX.sub("", p, count=1)


def _tools_for(scenario: dict) -> tuple[str, tuple]:
    """Identical for both arms. The built-in Skill tool is added to both: without it implicit skill use is impossible (the
    --tools list otherwise hides it), and in the baseline it simply has nothing to load."""
    tools = scenario["tools"]
    tools = tools if "Skill" in tools.split(",") else f"{tools},Skill"
    allow = tuple(scenario["allow"])
    return tools, allow if "Skill" in allow else (*allow, "Skill")


def run_one(scenario: dict, arm: str, run_idx: int, *, ctx_parts: dict, model: str, budget_cap: float, timeout_s: int,
            baseline_bundled: bool = False) -> dict:
    import cl_cases
    import cl_env
    from cl_claude import run_claude
    import cl_hooks

    cl_claude = _patch_argv()
    tag = f"{scenario['id']}-{arm}-r{run_idx}"
    cwd = ctx_parts["work"] / "cwd" / tag
    run_dir = ctx_parts["live_out"] / "runs" / tag
    for d in (cwd, run_dir):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    case = cl_cases.Case(id=scenario["id"], slug=scenario["slug"], group="skills_eval", feature="", spec=scenario["source"],
                         prompt=scenario["prompt"], expect=(), primary=())
    ctx = cl_cases.Ctx(case=case, attempt=1, run_id=ctx_parts["run_id"], work=ctx_parts["work"], cwd=cwd.resolve(), run_dir=run_dir,
                       data=ctx_parts["work"] / "data", fixtures=LS.FIXTURES_DIR, repo=cl_env.REPO, base_url=cl_env.BASE_URL,
                       instances=ctx_parts["instances"])
    row = {"scenario": scenario["id"], "arm": arm, "run": run_idx, "score": None, "passed": 0, "total": len(scenario["checks"]), "checks": [],
           "cost_usd": 0.0, "turns": 0, "tools": [], "skill_tool_used": False, "skills_used": [], "denials": [], "harness_error": None,
           "subtype": None, "run_dir": str(run_dir.relative_to(REPO)) if run_dir.is_relative_to(REPO) else str(run_dir),
           "final_text": "", "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        LS.run_setup(ctx, scenario)
    except Exception as e:
        row["harness_error"] = f"setup: {type(e).__name__}: {e}"
        return row
    tools, allow = _tools_for(scenario)
    budget = round(min(scenario["budget_usd"], max(budget_cap, 0.05)), 4)
    cl_claude._NO_MCP = not scenario["mcp"]
    try:
        t = run_claude(_prompt_for(scenario, arm, ctx.cwd), ctx=ctx, tools=tools, allow=allow, tier="cheap", max_turns=scenario["max_turns"],
                       timeout_s=timeout_s, plugin_dir=cl_hooks.plugin_dir(ctx) if arm == "skill" else None, skills=(arm == "skill" or baseline_bundled),
                       budget_usd=budget)
    except Exception as e:
        row["harness_error"] = f"run: {type(e).__name__}: {e}"
        return row
    finally:
        cl_claude._NO_MCP = False
    row.update(cost_usd=round(t.cost_usd, 4), turns=t.num_turns, tools=t.tools_called(), subtype=t.subtype, final_text=(t.final_text or "")[:600],
               denials=[d.get("tool_name") for d in t.permission_denials],
               skills_used=sorted({str(c.use.input.get("skill")) for c in t.of("Skill")}), skill_tool_used=bool(t.of("Skill")))
    err = _precondition(t, scenario, arm, ctx_parts["skill_names"])
    if err:
        row["harness_error"] = err
        return row
    row["checks"] = LS.evaluate(scenario, t, ctx)
    row["passed"] = sum(1 for c in row["checks"] if c["pass"])
    row["score"] = LS.score_of(row["checks"])
    return row


# ---- aggregation ---------------------------------------------------------------------------------------------------------

def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _arm_scores(rows: list[dict], arm: str) -> dict[str, float]:
    """scenario id -> mean score over its valid runs of the arm."""
    per: dict[str, list[float]] = {}
    for r in rows:
        if r["arm"] == arm and r["score"] is not None:
            per.setdefault(r["scenario"], []).append(r["score"])
    return {k: sum(v) / len(v) for k, v in per.items()}


def _bootstrap_ci(diffs: list[float], n: int = 2000, alpha: float = 0.10, seed: int = 7) -> list[float] | None:
    if len(diffs) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(n))
    return [round(means[int(alpha / 2 * n)], 4), round(means[min(n - 1, int((1 - alpha / 2) * n))], 4)]


def _subset(rows: list[dict], scs: dict[str, dict], pred) -> dict:
    ids = {i for i, s in scs.items() if pred(s)}
    sk, bl = _arm_scores(rows, "skill"), _arm_scores(rows, "baseline")
    paired = sorted(i for i in ids if i in sk and i in bl)
    diffs = [sk[i] - bl[i] for i in paired]
    return {"n": len(ids), "n_paired": len(paired), "skill": _mean([sk[i] for i in ids if i in sk]), "baseline": _mean([bl[i] for i in ids if i in bl]),
            "skill_paired": _mean([sk[i] for i in paired]), "baseline_paired": _mean([bl[i] for i in paired]),
            "uplift": _mean(diffs), "ci90": _bootstrap_ci(diffs), "scenarios": sorted(ids)}


def summarize(rows: list[dict], scenarios: list[dict]) -> dict:
    scs = {s["id"]: s for s in scenarios}
    allr = _subset(rows, scs, lambda s: True)
    per_scenario = []
    for s in scenarios:
        rr = [r for r in rows if r["scenario"] == s["id"]]
        if not rr:
            continue
        sk, bl = _arm_scores(rr, "skill"), _arm_scores(rr, "baseline")
        per_scenario.append({"id": s["id"], "slug": s["slug"], "subset": s["subset"], "skill": s["skill"], "procedural": s["procedural"],
                             "skill_score": sk.get(s["id"]), "baseline_score": bl.get(s["id"]),
                             "uplift": (round(sk[s["id"]] - bl[s["id"]], 4) if s["id"] in sk and s["id"] in bl else None)})
    return {"score": allr["skill"], "baseline_score": allr["baseline"], "uplift": allr["uplift"], "all": allr,
            "implicit": _subset(rows, scs, lambda s: s["subset"] == "implicit"),
            "explicit": _subset(rows, scs, lambda s: s["subset"] == "explicit"),
            "procedural": _subset(rows, scs, lambda s: s["procedural"]),
            "per_scenario": per_scenario,
            "harness_errors": [{"scenario": r["scenario"], "arm": r["arm"], "run": r["run"], "error": r["harness_error"]} for r in rows if r["harness_error"]],
            "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            # Skill tool_use only: slash-expanded (explicit) skills are injected into the prompt and never call the tool
            "skill_tool_rate": _mean([1.0 if r["skill_tool_used"] else 0.0 for r in rows if r["arm"] == "skill" and r["score"] is not None]),
            "skill_tool_rate_implicit": _mean([1.0 if r["skill_tool_used"] else 0.0 for r in rows if r["arm"] == "skill" and r["score"] is not None
                                               and scs.get(r["scenario"], {}).get("subset") == "implicit"])}


# ---- run -----------------------------------------------------------------------------------------------------------------

def run_live(*, only: list[str] | None = None, arm: str = "both", runs: int = 1, model: str = "haiku", max_cost_usd: float = 6.0,
             mcp_url: str = DEFAULT_MCP_URL, private: bool = False, run_id: str | None = None, scenarios_path: str | Path | None = None,
             out_root: Path | None = None, timeout_s: int | None = None, baseline_bundled_skills: bool = False, log=print) -> dict:
    import cl_env
    import cl_hooks
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    live_out = (out_root or OUT_ROOT) / run_id / "live"
    work = WORK_ROOT / run_id
    scs = LS.load_scenarios(scenarios_path or LS.SCENARIOS_PATH)
    problems = LS.validate_scenarios(scs)
    if problems:
        raise ValueError("scenarios.json outside the vocabulary:\n  " + "\n  ".join(problems))
    if only:
        want = {x.strip() for x in only}
        unknown = want - {s["id"] for s in scs}
        if unknown:
            raise ValueError(f"unknown scenario ids {sorted(unknown)}")
        scs = [s for s in scs if s["id"] in want]
    cl_env.MODEL_CHEAP = model
    live_out.mkdir(parents=True, exist_ok=True)
    (work / "data").mkdir(parents=True, exist_ok=True)

    if private:
        from cl_servers import Instances
        instances = Instances(work)
        mode = "private"
    else:
        instances = ExternalInstances(mcp_url)
        if not instances.inst.healthy():
            raise RuntimeError(f"harness: MCP daemon not healthy at {instances.inst.root_url}/health; start it yourself, this evaluator never starts or stops it")
        mode = "daemon"
    parts = {"run_id": run_id, "work": work.resolve(), "live_out": live_out, "instances": instances, "skill_names": cl_hooks.skill_names()}
    arms = ["skill", "baseline"] if arm == "both" else [arm]
    rows, spent, stopped = [], 0.0, None
    t0 = time.monotonic()
    try:
        if private:
            instances.get("default")
        for s in scs:
            for k in range(1, runs + 1):
                order = arms if k % 2 else list(reversed(arms))   # alternate which arm goes first on repeat runs
                for a in order:
                    if spent >= max_cost_usd:
                        stopped = f"cost cap ${max_cost_usd:g} reached before {s['id']}/{a}"
                        break
                    tmo = timeout_s or (420 if s["budget_usd"] >= 0.5 else 240)
                    row = run_one(s, a, k, ctx_parts=parts, model=model, budget_cap=max_cost_usd - spent, timeout_s=tmo, baseline_bundled=baseline_bundled_skills)
                    spent += row["cost_usd"]
                    rows.append(row)
                    log(f"[{s['id']}] {a:8s} r{k} " + (f"HARNESS ERROR: {row['harness_error'][:140]}" if row["harness_error"] else
                        f"score {row['score']:.2f} ({row['passed']}/{row['total']}) ${row['cost_usd']:.3f} turns={row['turns']} skill_tool_used={row['skill_tool_used']}"))
                if stopped:
                    break
            if stopped:
                break
    finally:
        instances.stop_all()
    report = {"run_id": run_id, "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": mode,
              "mcp_url": mcp_url if not private else "private", "model": model, "arms": arms, "baseline_bundled_skills": baseline_bundled_skills, "runs": runs, "only": only, "max_cost_usd": max_cost_usd,
              "plugin_dir": "plugins/openjev-skills", "skills": parts["skill_names"], "stopped": stopped, "partial": bool(stopped),
              "wall_s": round(time.monotonic() - t0, 1), "git_rev": _git_rev(), "summary": summarize(rows, scs), "rows": rows}
    (live_out / "results.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    (live_out / "report.md").write_text(render_md(report), encoding="utf-8")
    return report


def run(*, model: str = "haiku", max_cost_usd: float = 6.0, only: str | list | None = None, runs: int = 1, arm: str = "both",
        mcp_url: str = DEFAULT_MCP_URL, private_instances: bool = False, run_id: str | None = None, out_root: Path | None = None,
        baseline_bundled_skills: bool = False, **_ignored) -> dict:
    """Component entry point for run_eval.py: {score, uplift, implicit/explicit/procedural/all, scenarios, cost_usd, partial, results_path}."""
    if os.environ.get("OPENJEV_CLAUDE_LIVE") != "1":
        return {"skipped": True, "error": "OPENJEV_CLAUDE_LIVE=1 not set"}
    only = only.split(",") if isinstance(only, str) else only
    rep = run_live(only=only, arm=arm, runs=runs, model=model, max_cost_usd=max_cost_usd, mcp_url=mcp_url, private=private_instances,
                   run_id=run_id, out_root=out_root, baseline_bundled_skills=baseline_bundled_skills, log=lambda m: print("   " + m, flush=True))
    s = rep["summary"]
    return {"score": s["score"], "baseline_score": s["baseline_score"], "uplift": s["uplift"], "implicit": s["implicit"], "explicit": s["explicit"],
            "procedural": s["procedural"], "all": s["all"], "scenarios": [{**p, "score": p["skill_score"]} for p in s["per_scenario"]],
            "harness_errors": s["harness_errors"], "cost_usd": s["cost_usd"], "partial": rep["partial"], "stopped": rep["stopped"],
            "skill_tool_rate": s["skill_tool_rate"], "skill_tool_rate_implicit": s["skill_tool_rate_implicit"], "model": model, "mode": rep["mode"],
            "results_path": str((OUT_ROOT / rep["run_id"] / "live" / "results.json").relative_to(REPO))}


def _git_rev() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def _f(x) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def render_md(rep: dict) -> str:
    s = rep["summary"]
    L = [f"# Live skills evaluation {rep['run_id']}", "", f"model {rep['model']}, mode {rep['mode']}, arms {rep['arms']}, runs {rep['runs']}, cost ${s['cost_usd']:.3f}"
         f" of ${rep['max_cost_usd']:g}" + (f"; STOPPED: {rep['stopped']}" if rep["stopped"] else ""), "",
         "| subset | n | skill | baseline | uplift (paired) | CI90 |", "|---|---|---|---|---|---|"]
    for k in ("implicit", "explicit", "procedural", "all"):
        b = s[k]
        L.append(f"| {k} | {b['n']} | {_f(b['skill'])} | {_f(b['baseline'])} | {_f(b['uplift'])} (n={b['n_paired']}) | {b['ci90'] or 'n/a'} |")
    L += ["", "| scenario | subset | skill | baseline | uplift |", "|---|---|---|---|---|"]
    L += [f"| {p['id']} {p['slug']} | {p['subset']} | {_f(p['skill_score'])} | {_f(p['baseline_score'])} | {_f(p['uplift'])} |" for p in s["per_scenario"]]
    fails = [(r["scenario"], r["arm"], c["name"], c["detail"]) for r in rep["rows"] if r["arm"] == "skill" for c in r["checks"] if not c["pass"]]
    if fails:
        L += ["", "## Failed checks (skill arm)", ""] + [f"- {a} {b}: {c} {d}".rstrip() for a, b, c, d in fails]
    if s["harness_errors"]:
        L += ["", "## Harness errors (not scored)", ""] + [f"- {h['scenario']} {h['arm']} r{h['run']}: {h['error'][:200]}" for h in s["harness_errors"]]
    return "\n".join(L) + "\n"


# ---- CLI -----------------------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="live_eval", description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma separated scenario ids, e.g. L01,L12")
    ap.add_argument("--arm", choices=("both", "skill", "baseline"), default="both")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--max-cost-usd", type=float, default=6.0)
    ap.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    ap.add_argument("--private-instances", action="store_true", help="spawn an own openjev-mcp (roots = run work dir) instead of using --mcp-url")
    ap.add_argument("--baseline-bundled-skills", action="store_true",
                    help="baseline keeps Claude Code's bundled skills/slash commands (skills=True, no plugin) so the arms differ only in the plugin")
    ap.add_argument("--run-id")
    ap.add_argument("--scenarios")
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--list", action="store_true", help="list scenarios and exit (no gate needed)")
    ns = ap.parse_args(argv)
    if ns.list:
        for s in LS.load_scenarios(ns.scenarios or LS.SCENARIOS_PATH):
            print(f"{s['id']} {s['subset']:8s} {s['skill']:28s} {s['slug']}")
        return 0
    if os.environ.get("OPENJEV_CLAUDE_LIVE") != "1":
        print("live evaluation spends subscription usage: set OPENJEV_CLAUDE_LIVE=1 to run it (nothing was run)", file=sys.stderr)
        return EXIT_NO_GATE
    if not shutil.which(os.environ.get("OJ_CLAUDE_BIN", "claude")):
        print("claude CLI not found on PATH (OJ_CLAUDE_BIN)", file=sys.stderr)
        return EXIT_ENV
    try:
        rep = run_live(only=ns.only.split(",") if ns.only else None, arm=ns.arm, runs=ns.runs, model=ns.model, max_cost_usd=ns.max_cost_usd,
                       mcp_url=ns.mcp_url, private=ns.private_instances, run_id=ns.run_id, scenarios_path=ns.scenarios, timeout_s=ns.timeout, baseline_bundled_skills=ns.baseline_bundled_skills)
    except (RuntimeError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return EXIT_ENV
    print(render_md(rep))
    print(f"report: {(OUT_ROOT / rep['run_id'] / 'live' / 'results.json').relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
