"""results/<run_id>/results.jsonl (one row per attempt, appended under flock) and summary helpers (ARCHITECTURE 1.8)."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path

import cl_env


def run_root(rid: str | None = None) -> Path:
    d = cl_env.RESULTS / (rid or cl_env.run_id())
    d.mkdir(parents=True, exist_ok=True)
    return d


def results_path(rid: str | None = None) -> Path:
    return run_root(rid) / "results.jsonl"


def append(row: dict, rid: str | None = None) -> None:
    with open(results_path(rid), "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load(rid: str | None = None) -> list[dict]:
    p = results_path(rid)
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()] if p.exists() else []


def total_cost(rows: list[dict]) -> float:
    return round(sum(r.get("cost_usd") or 0 for r in rows), 4)


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def summarize(rows: list[dict]) -> dict:
    """Per group pass/fail on each case's final attempt, total cost, p50/p95 duration, retries, failing ids with run dirs."""
    final: dict[str, dict] = {}
    for r in rows:
        final[r["id"]] = r
    groups: dict[str, dict] = {}
    for r in final.values():
        g = groups.setdefault(r.get("group") or "?", {"pass": 0, "fail": 0})
        g["pass" if r.get("pass") else "fail"] += 1
    dur = [r["duration_ms"] for r in rows if r.get("duration_ms") is not None]
    return {"groups": groups, "cases": len(final), "attempts": len(rows), "passed": sum(1 for r in final.values() if r.get("pass")),
            "failed": sum(1 for r in final.values() if not r.get("pass")), "cost_usd": total_cost(rows),
            "p50_ms": _pct(dur, 0.5), "p95_ms": _pct(dur, 0.95), "retries": sum(1 for r in rows if r.get("retry_of")),
            "failing": [{"id": r["id"], "error_class": r.get("error_class"), "failure": (r.get("failure") or "")[:300], "run_dir": r.get("run_dir")}
                        for r in final.values() if not r.get("pass")]}
