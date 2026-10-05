"""Run the openjev-skills evaluation and write the composite report.

    run_eval.py static|offline|trigger|live|all [--model haiku] [--max-cost-usd 6] [--only L01,L02]
                [--runs N] [--arm skill|baseline|both] [--mcp-url URL] [--private-instances]
                [--no-claude] [--contrast] [--out-dir DIR]

Reports: <out-dir>/<run_id>/report.{json,md}, copied to <out-dir>/latest/ (default .tmp/skills-eval).
Exit 0 when every component that ran meets its floor (all: full composite + uplift targets); 1 on failure;
2 when live is requested without OPENJEV_CLAUDE_LIVE=1.
Component modules (static_check, offline_check, trigger_eval, live_eval) each expose ``run(**kwargs)``
returning a dict with a ``score``; only keyword arguments the function accepts are passed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import report as R                                    # noqa: E402
import skill_lib as L                                 # noqa: E402

REPO = L.REPO
DEFAULT_OUT = REPO / ".tmp" / "skills-eval"
MODULES = {"static": "static_check", "offline": "offline_check", "trigger": "trigger_eval", "live": "live_eval"}


def _call(fn, **kw):
    params = inspect.signature(fn).parameters
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return fn(**kw)
    return fn(**{k: v for k, v in kw.items() if k in params and v is not None})


def run_component(name: str, **kw) -> dict:
    t0 = time.time()
    try:
        mod = importlib.import_module(MODULES[name])
    except ImportError as e:
        return {"error": f"module {MODULES[name]} unavailable: {e}", "elapsed_s": 0.0}
    if not hasattr(mod, "run"):
        return {"error": f"{MODULES[name]}.run() missing", "elapsed_s": 0.0}
    try:
        res = _call(mod.run, **kw)
    except Exception as e:                            # a crashing component is a failed run, not a zero score
        return {"error": f"{type(e).__name__}: {e}", "elapsed_s": round(time.time() - t0, 2)}
    res = dict(res) if isinstance(res, dict) else {"score": float(res)}
    res["elapsed_s"] = round(time.time() - t0, 2)
    return res


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=30, check=True).stdout
    except Exception:
        return None


def _plugin_version() -> str | None:
    try:
        return json.loads((L.PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text()).get("version")
    except Exception:
        return None


def _scores(skills_dir: Path, components: tuple[str, ...], args) -> dict:
    out = {}
    for c in components:
        kw = {"skills_dir": str(skills_dir), "run_claude": False}
        if c == "trigger":
            continue                                   # needs live OpenJev reads; only run on the real plugin
        r = run_component(c, **kw)
        out[c] = r.get("score") if not r.get("error") else None
        if r.get("error"):
            out[f"{c}_error"] = r["error"]
    return out


def contrast(args) -> dict:
    """pinned-commit 'before' skills and a mutated copy of the current skills; the mutation must fall below the floors."""
    res: dict = {}
    tmp = Path(tempfile.mkdtemp(prefix="skills-contrast-"))
    try:
        head = tmp / "head"
        head.mkdir()
        tar = subprocess.run(["git", "archive", L.BEFORE_COMMIT, "mcp/skills"], cwd=REPO, capture_output=True)
        if tar.returncode == 0 and tar.stdout:
            subprocess.run(["tar", "-x", "-C", str(head)], input=tar.stdout, check=False)
            sd = head / "mcp" / "skills"
            if sd.is_dir():
                s = _scores(sd, ("static", "offline"), args)
                res[f"before ({L.BEFORE_COMMIT} mcp/skills)"] = {**s, "note": "pre-refinement skills at the pinned commit; trigger not scored"}
        mut = tmp / "mutated"
        shutil.copytree(L.SKILLS_DIR, mut)
        for p in mut.rglob("SKILL.md"):
            t = p.read_text()
            t = t.replace("mcp__openjev__", "").replace("recedence", "order")
            t = re.sub(r"(?m)^(description:.*?)\bUse when\b", r"\1Handy for", t)
            t = re.sub(r"<!-- openjev-\w+(?::[^>]*)? -->\n", "", t)
            p.write_text(t)
        s = _scores(mut, ("static", "offline"), args)
        below = (s.get("static") is not None and s["static"] < R.FLOORS["static"]) or \
                (s.get("offline") is not None and s["offline"] < R.FLOORS["offline"])
        res["mutated copy"] = {**s, "note": "degraded on purpose; " + ("below floors as required" if below else "NOT below floors: checks too weak"),
                               "below_floors": below}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", choices=["static", "offline", "trigger", "live", "all"])
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--max-cost-usd", type=float, default=6.0)
    ap.add_argument("--only", help="comma-separated live scenario ids")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--arm", choices=["skill", "baseline", "both"], default="both")
    ap.add_argument("--mcp-url", default="http://127.0.0.1:8100/mcp")
    ap.add_argument("--private-instances", action="store_true")
    ap.add_argument("--no-claude", action="store_true", help="skip claude plugin validate in static")
    ap.add_argument("--contrast", action="store_true", help="also run the pinned 'before' and mutated-copy contrast (default for all)")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)

    wanted = list(MODULES) if a.what == "all" else [a.what]
    live_ok = os.environ.get("OPENJEV_CLAUDE_LIVE") == "1"
    if a.what == "live" and not live_ok:
        print("live evaluation spends subscription usage: set OPENJEV_CLAUDE_LIVE=1", file=sys.stderr)
        return 2
    if "live" in wanted and not live_ok:
        print("note: OPENJEV_CLAUDE_LIVE!=1, skipping live (report will be partial)", file=sys.stderr)
        wanted.remove("live")

    components: dict[str, dict] = {}
    for c in wanted:
        print(f"== {c}", flush=True)
        kw = {}
        if c == "static":
            kw["run_claude"] = not a.no_claude
        if c == "live":
            kw.update(model=a.model, max_cost_usd=a.max_cost_usd, only=a.only, runs=a.runs, arm=a.arm,
                      mcp_url=a.mcp_url, private_instances=a.private_instances)
        components[c] = run_component(c, **kw)
        r = components[c]
        print(f"   {c}: " + (f"ERROR {r['error']}" if r.get("error") else f"score {r.get('score', float('nan')):.3f}"), flush=True)

    rep = {
        "run_id": dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3],
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_rev": (_git("rev-parse", "--short", "HEAD") or "").strip() or None,
        "plugin_version": _plugin_version(),
        "command": a.what,
        "components": components,
        "weights": R.WEIGHTS,
        "targets": R.TARGETS,
    }
    if a.contrast or a.what == "all":
        rep["contrast"] = contrast(a)
    v = R.compose(components)
    rep["verdict"] = v
    rep.update({k: v[k] for k in ("composite", "composite_partial", "partial", "pass")})
    out = R.write(rep, Path(a.out_dir))
    print(f"composite {v['composite'] if v['composite'] is not None else 'n/a'}; partial composite {v['composite_partial']}; "
          f"pass={v['pass']}\nreport: {out / 'report.md'}")
    for f in v["failures"]:
        print("  -", f)
    if a.what == "all":
        return 0 if v["pass"] else 1
    return 0 if v["floors_ok"] and not any("uplift" in f for f in v["failures"]) else 1


if __name__ == "__main__":
    sys.exit(main())
