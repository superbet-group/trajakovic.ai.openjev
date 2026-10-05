"""T component: trigger accuracy of the skill descriptions.

Scores OpenJev's own ``skill_selection`` recipe over a roster made of the real SKILL.md
descriptions plus the distractors, on the held-out prompts in data/triggers.jsonl.
Needs only OpenJev (OPENJEV_BASE_URL, default http://127.0.0.1:8080); no MCP server, no
claude CLI (except the optional, unscored --claude diagnostic). Calls run sequentially.

Scoring per prompt (top-1 by ``signals.skill``):
  1.0  positive/confusable: signals.skill is the expected skill (or one of them) and the
       recipe did not abstain; negative: it picked "none" or a distractor and did not abstain
  0.5  the recipe abstained but signals.skill is right (the right side of the line)
  0.0  anything else (wrong skill, abstain with wrong skill, error/degraded)

Usage: python mcp/tests/skills_eval/trigger_eval.py [--split dev|test|all] [--limit N]
       [--skills-dir DIR] [--claude N] [--json PATH]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import skill_lib as sl  # noqa: E402

TRIGGERS = sl.DATA_DIR / "triggers.jsonl"
DISTRACTORS = sl.DATA_DIR / "distractors.json"
FLOOR = 0.80


def load_prompts(split: str = "all", limit: int | None = None) -> list[dict]:
    rows = [json.loads(line) for line in TRIGGERS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if split != "all":
        rows = [r for r in rows if r.get("split") == split]
    return rows[:limit] if limit else rows


def build_roster(skills_dir: str | Path | None = None) -> tuple[list[dict], set[str], set[str]]:
    skills = sl.discover(skills_dir)
    if not skills:
        raise SystemExit(f"no skills found under {skills_dir or sl.SKILLS_DIR}")
    own = [{"id": s.name, "description": s.description} for s in skills]
    dis = json.loads(DISTRACTORS.read_text(encoding="utf-8"))
    ids = {r["id"] for r in own}
    dis = [d for d in dis if d["id"] not in ids]
    return own + dis, ids, {d["id"] for d in dis}


def score_one(row: dict, res: dict | None, own: set[str]) -> tuple[float, str]:
    """(score, picked). picked is the skill id, 'none', or 'ERROR'."""
    if not res or res.get("degraded"):
        return 0.0, "ERROR"
    picked = (res.get("signals") or {}).get("skill") or "none"
    abstain = res.get("decision") == "abstain"
    expect = row["expect"]
    ok_set = set(expect) if isinstance(expect, list) else {expect}
    if "none" in ok_set or row["kind"] == "negative":
        good = picked not in own
    else:
        good = picked in ok_set
    if good:
        return (0.5 if abstain else 1.0), picked
    return 0.0, picked


async def _run(rows: list[dict], roster: list[dict], own: set[str], progress: bool) -> list[dict]:
    sl.ensure_server_importable()
    from openjev_mcp.config import load_config
    from openjev_mcp.http import OpenJevClient
    from openjev_mcp.limits import LimitsCache
    from openjev_mcp.progress import ProgressEmitter
    from openjev_mcp.tools import ToolContext
    from openjev_mcp.tools.dispatch import call_tool

    config = load_config()
    client = OpenJevClient(config)
    ctx = ToolContext(config, client, LimitsCache(client), ProgressEmitter.noop(), None)
    out = []
    try:
        for i, row in enumerate(rows, 1):
            t0 = time.monotonic()
            try:
                r = await call_tool(ctx, "recipe", {"recipe": "skill_selection",
                                                    "inputs": {"prompt": row["prompt"], "roster": roster}})
                res = None if r.get("isError") else r.get("structuredContent")
                err = (r.get("structuredContent") or {}).get("message") if r.get("isError") else None
            except Exception as e:  # noqa: BLE001
                res, err = None, f"{type(e).__name__}: {e}"
            ms = (time.monotonic() - t0) * 1000
            sc, picked = score_one(row, res, own)
            probs = (((res or {}).get("answers") or {}).get("skill") or {}).get("probabilities") or {}
            top = sorted(probs.items(), key=lambda kv: -kv[1])[:3]
            out.append({"id": row["id"], "kind": row["kind"], "split": row.get("split"), "expect": row["expect"],
                        "picked": picked, "decision": (res or {}).get("decision"), "score": sc,
                        "p_top": (res or {}).get("signals", {}).get("skill_p"),
                        "top3": [(k, round(v, 3)) for k, v in top], "latency_ms": round(ms), "error": err,
                        "prompt": row["prompt"]})
            if progress:
                print(f"\r  {i}/{len(rows)}", end="", file=sys.stderr, flush=True)
    finally:
        await client.aclose()
    if progress:
        print(file=sys.stderr)
    return out


def summarise(results: list[dict], own: set[str]) -> dict:
    n = len(results) or 1
    T = sum(r["score"] for r in results) / n
    tp, fp, fn = defaultdict(float), defaultdict(float), defaultdict(float)
    for r in results:
        exp = set(r["expect"]) if isinstance(r["expect"], list) else {r["expect"]}
        for s in exp & own:
            (tp if r["picked"] == s else fn)[s] += 1
        if r["picked"] in own and r["picked"] not in exp:
            fp[r["picked"]] += 1
    per_skill = {}
    for s in sorted(own):
        p = tp[s] / (tp[s] + fp[s]) if tp[s] + fp[s] else None
        rc = tp[s] / (tp[s] + fn[s]) if tp[s] + fn[s] else None
        per_skill[s] = {"precision": p, "recall": rc, "n_expected": int(tp[s] + fn[s])}
    conf = defaultdict(int)
    for r in results:
        if r["score"] == 0.0:
            exp = r["expect"][0] if isinstance(r["expect"], list) else r["expect"]
            conf[(exp, r["picked"])] += 1
    confusions = [{"expected": a, "picked": b, "count": c} for (a, b), c in sorted(conf.items(), key=lambda kv: -kv[1])]
    by_kind = {}
    for k in sorted({r["kind"] for r in results}):
        rs = [r for r in results if r["kind"] == k]
        by_kind[k] = {"n": len(rs), "score": sum(r["score"] for r in rs) / len(rs)}
    by_split = {}
    for k in sorted({str(r["split"]) for r in results}):
        rs = [r for r in results if str(r["split"]) == k]
        by_split[k] = {"n": len(rs), "score": sum(r["score"] for r in rs) / len(rs)}
    lat = [r["latency_ms"] for r in results]
    abst = sum(1 for r in results if r["decision"] == "abstain")
    return {"score": T, "n": len(results), "floor": FLOOR, "per_skill": per_skill, "confusions": confusions,
            "by_kind": by_kind, "by_split": by_split, "abstains": abst, "abstain_rate": abst / n,
            "errors": sum(1 for r in results if r["picked"] == "ERROR"),
            "mean_latency_ms": sum(lat) / len(lat) if lat else 0,
            "failures": [r for r in results if r["score"] < 1.0]}


def claude_diagnostic(rows: list[dict], skills_dir: Path, n: int, plugin_dir: Path) -> dict:
    """Unscored: ask haiku (claude -p, one turn) which skill fits; report agreement with expect."""
    names = [s.name for s in sl.discover(skills_dir)]
    agree = tried = 0
    cost = 0.0
    details = []
    for row in [r for r in rows if r["kind"] != "negative"][:n]:
        q = (f"User request: {row['prompt']}\nWhich single skill from the openjev-skills plugin would you load? "
             f"Answer with the skill name only, or none.")
        cmd = ["claude", "-p", q, "--model", "haiku", "--plugin-dir", str(plugin_dir), "--max-turns", "1",
               "--output-format", "json", "--setting-sources", ""]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            j = json.loads(p.stdout)
        except Exception as e:  # noqa: BLE001
            details.append({"id": row["id"], "error": str(e)[:80]})
            continue
        ans = (j.get("result") or "").strip().lower()
        cost += float(j.get("total_cost_usd") or 0)
        picked = next((nm for nm in names if nm in ans), "none")
        exp = row["expect"] if isinstance(row["expect"], list) else [row["expect"]]
        tried += 1
        agree += picked in exp
        details.append({"id": row["id"], "expect": exp, "picked": picked})
    return {"tried": tried, "agree": agree, "agreement": agree / tried if tried else None,
            "cost_usd": round(cost, 4), "details": details}


def run_trigger(skills_dir: str | Path | None = None, split: str = "all", limit: int | None = None,
                progress: bool = False) -> dict:
    """Entry point for run_eval.py. Returns the summary dict plus 'results'."""
    rows = load_prompts(split, limit)
    roster, own, dis = build_roster(skills_dir)
    results = asyncio.run(_run(rows, roster, own, progress))
    s = summarise(results, own)
    s["roster"] = {"skills": len(own), "distractors": len(dis)}
    s["results"] = results
    return s


def run(skills_dir: str | Path | None = None, **kwargs) -> dict:
    """Public entry point for run_eval.py. Runs trigger accuracy evaluation."""
    return run_trigger(skills_dir, progress=kwargs.get("progress", False))


def print_report(s: dict) -> None:
    print(f"T (trigger accuracy) = {s['score']:.3f} over {s['n']} prompts "
          f"(floor {s['floor']}, {'OK' if s['score'] >= s['floor'] else 'BELOW FLOOR'})")
    print("by kind : " + ", ".join(f"{k} {v['score']:.2f} (n={v['n']})" for k, v in s["by_kind"].items()))
    print("by split: " + ", ".join(f"{k} {v['score']:.2f} (n={v['n']})" for k, v in s["by_split"].items()))
    print(f"abstains: {s['abstains']} ({s['abstain_rate']:.0%}), errors: {s['errors']}, "
          f"mean latency {s['mean_latency_ms']:.0f} ms")
    print("per skill (precision / recall):")
    f = lambda x: "  - " if x is None else f"{x:.2f}"  # noqa: E731
    for k, v in s["per_skill"].items():
        print(f"  {k:32s} {f(v['precision'])} / {f(v['recall'])}  n={v['n_expected']}")
    print("confusions (expected -> picked):")
    for c in s["confusions"] or [{"expected": "-", "picked": "-", "count": 0}]:
        print(f"  {c['expected']} -> {c['picked']} x{c['count']}")
    print(f"failing prompts ({len(s['failures'])}):")
    for r in s["failures"]:
        exp = "|".join(r["expect"]) if isinstance(r["expect"], list) else r["expect"]
        print(f"  {r['id']} [{r['kind']}/{r['split']}] score={r['score']} expect={exp} picked={r['picked']} "
              f"decision={r['decision']} top3={r['top3']}{' ERR ' + str(r['error']) if r['error'] else ''}")
        print(f"      {r['prompt'][:140]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--split", choices=("dev", "test", "all"), default="all")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--skills-dir")
    ap.add_argument("--claude", type=int, default=0, help="unscored haiku diagnostic over N prompts")
    ap.add_argument("--json", help="write the full summary to this path")
    a = ap.parse_args(argv)
    if not os.environ.get("OPENJEV_BASE_URL"):
        os.environ["OPENJEV_BASE_URL"] = "http://127.0.0.1:8080"
    s = run_trigger(a.skills_dir, a.split, a.limit, progress=sys.stderr.isatty())
    if a.claude:
        s["claude_diagnostic"] = claude_diagnostic(load_prompts(a.split, None), Path(a.skills_dir or sl.SKILLS_DIR),
                                                   a.claude, sl.PLUGIN_DIR)
    print_report(s)
    if a.claude:
        d = s["claude_diagnostic"]
        print(f"claude diagnostic (unscored): agreement {d['agree']}/{d['tried']} cost ${d['cost_usd']}")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
    return 0 if s["score"] >= FLOOR else 1


if __name__ == "__main__":
    sys.exit(main())
