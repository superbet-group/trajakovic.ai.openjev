#!/usr/bin/env python
"""Runner: one pytest process per group, shared OJ_LIVE_RUN_ID, run budget, results/<run_id>/summary.json.

    .venv/bin/python mcp/tests/claude_live/run_claude_live.py --groups g01,g02 --procs 3 [-k T032] [--list]
Spends subscription usage (claude -p); needs the real OpenJev (never started or stopped here)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cl_env  # noqa: E402
import cl_results  # noqa: E402


def present_groups() -> list[str]:
    import cl_cases
    return [g for g in cl_cases.GROUPS if cl_cases.group_file(g).exists()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--groups", help="comma list, e.g. g01,g02 (default: every group file present)")
    ap.add_argument("--procs", type=int, default=3, help="concurrent pytest processes (default 3)")
    ap.add_argument("-k", dest="expr", default=None, help="pytest -k expression, passed through")
    ap.add_argument("--list", action="store_true", help="print groups and case ids; starts no claude")
    ns, extra = ap.parse_known_args(argv)
    import cl_cases
    have = present_groups()
    groups = [g.strip() for g in ns.groups.split(",")] if ns.groups else have
    bad = [g for g in groups if g not in cl_cases.GROUPS]
    if bad:
        ap.error(f"unknown groups {bad}; known {list(cl_cases.GROUPS)}")
    if ns.list:
        groups = [g.strip() for g in ns.groups.split(",")] if ns.groups else list(cl_cases.GROUPS)
        cat = cl_cases.load_catalogue() if have else {}
        for g, (name, lo, hi) in cl_cases.GROUPS.items():
            if g in groups:
                cs = cat.get(g)
                print(f"{g} {name} T{lo:03d}-T{hi:03d}: " + (", ".join(f"{c.id}-{c.slug}" for c in cs) if cs is not None else "(no group file yet)"))
        return 0
    missing = [g for g in groups if g not in have]
    if missing:
        print(f"skipping groups without a test file: {missing}", file=sys.stderr)
    todo = [g for g in groups if g in have]
    if not todo:
        print("no group files present: nothing to run")
    rid = os.environ.get("OJ_LIVE_RUN_ID") or cl_env.run_id()
    root = cl_results.run_root(rid)
    env = {**os.environ, "OPENJEV_CLAUDE_LIVE": "1", "OJ_LIVE_RUN_ID": rid}
    env.pop("OJ_LIVE_MCP_PORT", None)   # D10: each child picks its own ports
    running: dict[str, tuple[subprocess.Popen, float]] = {}
    done: dict[str, dict] = {}
    skipped_budget: list[str] = []
    queue = list(todo)
    t0 = time.monotonic()
    print(f"run_id {rid}  results {root}  groups {todo}  procs {ns.procs}  budget ${cl_env.RUN_BUDGET_USD:g}")
    while queue or running:
        over = cl_results.total_cost(cl_results.load(rid)) > cl_env.RUN_BUDGET_USD
        while queue and len(running) < ns.procs and not over:
            g = queue.pop(0)
            cmd = [cl_env.PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(cl_cases.group_file(g)), *(["-k", ns.expr] if ns.expr else []), *extra]
            log = open(root / f"pytest-{g}.log", "w")
            running[g] = (subprocess.Popen(cmd, cwd=str(cl_env.REPO), env=env, stdout=log, stderr=subprocess.STDOUT), time.monotonic())
            print(f"start {g}")
        if over and queue:
            skipped_budget, queue = queue, []
            print(f"run budget ${cl_env.RUN_BUDGET_USD:g} exceeded: not scheduling {skipped_budget}")
        for g, (p, st) in list(running.items()):
            if p.poll() is not None:
                done[g] = {"rc": p.returncode, "wall_s": round(time.monotonic() - st, 1), "log": f"pytest-{g}.log"}
                print(f"done {g} rc={p.returncode} {done[g]['wall_s']}s")
                del running[g]
        time.sleep(1)
    rows = cl_results.load(rid)
    summ = {"run_id": rid, "wall_s": round(time.monotonic() - t0, 1), "processes": done, "skipped_budget": skipped_budget,
            "budget_usd": cl_env.RUN_BUDGET_USD, **cl_results.summarize(rows)}
    (root / "summary.json").write_text(json.dumps(summ, indent=1), encoding="utf-8")
    print(f"summary {root / 'summary.json'}: {summ['passed']} passed, {summ['failed']} failed, ${summ['cost_usd']}, retries {summ['retries']}")
    for f in summ["failing"]:
        print(f"  FAIL {f['id']} [{f['error_class']}] {f['run_dir']}")
    return 1 if summ["failed"] or any(d["rc"] not in (0, 5) for d in done.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
