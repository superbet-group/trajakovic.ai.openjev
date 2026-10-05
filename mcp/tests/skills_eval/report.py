"""Composite scoring and report rendering for the openjev-skills evaluation.

``compose`` turns per-component results into the composite/partial/pass verdict;
``render_markdown`` writes the human report. Pure functions, no I/O except ``write``.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

WEIGHTS = {"static": 0.15, "offline": 0.25, "trigger": 0.20, "live": 0.40}
FLOORS = {"static": 0.95, "offline": 0.98, "trigger": 0.80, "live": 0.70}
TARGETS = {"composite": 0.85, "uplift": 0.20, "floors": FLOORS}
LETTER = {"static": "S", "offline": "F", "trigger": "T", "live": "L"}


def _score(comp: dict | None) -> float | None:
    if not comp or comp.get("error") or comp.get("skipped") is True or not isinstance(comp.get("score"), (int, float)):
        return None
    return float(comp["score"])


def live_uplift(live: dict | None) -> float | None:
    """The gated uplift: implicit subset when reported, else the overall paired uplift."""
    if not live:
        return None
    imp = live.get("implicit")
    if isinstance(imp, dict) and isinstance(imp.get("uplift"), (int, float)):
        return float(imp["uplift"])
    up = live.get("uplift")
    return float(up) if isinstance(up, (int, float)) else None


def compose(components: dict[str, dict]) -> dict:
    """Return {composite, composite_partial, partial, floors_ok, uplift, pass, failures, scores}."""
    scores = {k: _score(components.get(k)) for k in WEIGHTS}
    ran = [k for k, v in scores.items() if v is not None]
    wsum = sum(WEIGHTS[k] for k in ran)
    partial_val = sum(WEIGHTS[k] * scores[k] for k in ran) / wsum if wsum else 0.0
    partial = len(ran) < len(WEIGHTS)
    composite = None if partial else round(sum(WEIGHTS[k] * scores[k] for k in ran), 4)
    failures: list[str] = []
    for k in ran:
        if scores[k] < FLOORS[k]:
            failures.append(f"{k} {scores[k]:.3f} below floor {FLOORS[k]}")
    for k in WEIGHTS:
        if k not in ran and components.get(k, {}).get("error"):
            failures.append(f"{k} errored: {components[k]['error']}")
    uplift = live_uplift(components.get("live"))
    if "live" in ran and (uplift is None or uplift < TARGETS["uplift"]):
        failures.append(f"uplift {uplift if uplift is None else round(uplift, 3)} below target {TARGETS['uplift']}")
    floors_ok = not any("below floor" in f or "errored" in f for f in failures)
    if composite is not None and composite < TARGETS["composite"]:
        failures.append(f"composite {composite:.3f} below target {TARGETS['composite']}")
    return {
        "composite": composite,
        "composite_partial": round(partial_val, 4),
        "partial": partial,
        "floors_ok": floors_ok,
        "uplift": uplift,
        "scores": scores,
        "pass": (not partial) and not failures,
        "failures": failures,
    }


def _fmt(v, nd=3):
    return "-" if v is None else f"{v:.{nd}f}"


def _failed(comp: dict, limit: int = 8) -> list[str]:
    out = []
    for c in comp.get("checks", []) or []:
        if c.get("ok") is False and not c.get("skipped"):
            loc = f" [{c['file']}:{c['line']}]" if c.get("file") else ""
            out.append(f"{c.get('id', '?')} {c.get('skill') or '(global)'}: {c.get('detail', '')}{loc}")
    return out[:limit]


def render_markdown(rep: dict) -> str:
    comps = rep["components"]
    v = rep["verdict"]
    L = [f"# OpenJev skills evaluation `{rep['run_id']}`", "",
         f"git `{rep.get('git_rev')}`, plugin version `{rep.get('plugin_version')}`, created {rep['created_at']}", "",
         "| Component | Score | Weight | Floor | Status |", "|---|---|---|---|---|"]
    for k, w in WEIGHTS.items():
        s = v["scores"].get(k)
        if s is None:
            err = comps.get(k, {}).get("error")
            status = f"ERROR: {err}" if err else "not run"
        else:
            status = "ok" if s >= FLOORS[k] else "BELOW FLOOR"
        L.append(f"| {LETTER[k]} {k} | {_fmt(s)} | {w} | {FLOORS[k]} | {status} |")
    L += ["",
          f"- Composite: {_fmt(v['composite'])} (target {TARGETS['composite']})"
          + (f"; partial composite over components that ran: {_fmt(v['composite_partial'])}" if v["partial"] else ""),
          f"- Uplift (live, implicit subset, skill arm minus no-plugin arm): {_fmt(v['uplift'])} (target {TARGETS['uplift']})",
          f"- Verdict: **{'PASS' if v['pass'] else 'FAIL' if not v['partial'] else 'PARTIAL (no verdict)'}**"]
    for f in v["failures"]:
        L.append(f"  - {f}")
    for k in ("static", "offline"):
        bad = _failed(comps.get(k, {}))
        if bad:
            L += ["", f"## Top {k} failures", ""] + [f"- {b}" for b in bad]
    tr = comps.get("trigger")
    if tr and not tr.get("error") and tr.get("skipped") is not True:
        L += ["", "## Trigger", "",
              f"- accuracy {_fmt(tr.get('score'))}, abstain rate {tr.get('abstain_rate', '-')}, prompts {tr.get('n', '-')}"]
        for pair in (tr.get("confusions") or [])[:8]:
            L.append(f"- confusion: {pair}")
    lv = comps.get("live")
    if lv and not lv.get("error") and lv.get("skipped") is not True:
        L += ["", "## Live", "", f"- skill arm {_fmt(lv.get('score'))}, cost ${lv.get('cost_usd', 0)}"]
        if lv.get("skill_tool_rate") is not None:
            L.append(f"- Skill tool_use rate (skill arm): {_fmt(lv.get('skill_tool_rate'))} overall, {_fmt(lv.get('skill_tool_rate_implicit'))} on implicit prompts "
                     "(explicit /skill prompts are slash-expanded and never call the Skill tool, so a low overall rate is expected)")
        for sub in ("implicit", "explicit", "procedural"):
            if isinstance(lv.get(sub), dict):
                d = lv[sub]
                L.append(f"- {sub}: skill {_fmt(d.get('skill'))}, baseline {_fmt(d.get('baseline'))}, uplift {_fmt(d.get('uplift'))}"
                         + (f", 90% CI {d['ci90']}" if d.get("ci90") else ""))
        weak = sorted((s for s in lv.get("scenarios", []) if isinstance(s.get("skill_score"), (int, float))),
                      key=lambda s: s["skill_score"])[:5]
        for s in weak:
            L.append(f"- weakest {s.get('id')}: {s['skill_score']:.2f}")
    ctx = rep.get("contrast")
    if ctx:
        L += ["", "## Before and mutated contrast", "", "| Variant | S | F | T | Note |", "|---|---|---|---|---|"]
        for name, d in ctx.items():
            L.append(f"| {name} | {_fmt(d.get('static'))} | {_fmt(d.get('offline'))} | {_fmt(d.get('trigger'))} | {d.get('note', '')} |")
    return "\n".join(L) + "\n"


def write(rep: dict, base: Path) -> Path:
    """Write report.json/report.md under base/<run_id>/ and copy to base/latest/."""
    out = base / rep["run_id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str) + "\n")
    (out / "report.md").write_text(render_markdown(rep))
    latest = base / "latest"
    if latest.exists():
        shutil.rmtree(latest)
    shutil.copytree(out, latest)
    return out
