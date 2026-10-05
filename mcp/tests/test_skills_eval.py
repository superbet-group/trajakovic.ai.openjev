"""CI-safe skills evaluation: static (S) and offline (F) components run in-process, no network."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "skills_eval"))

import report as R  # noqa: E402
import static_check  # noqa: E402


def test_static_score_meets_floor():
    res = static_check.run(run_claude=shutil.which("claude") is not None)   # G1 needs the claude CLI
    bad = [f"{c['id']} {c['skill']}: {c['detail']}" for c in res["checks"] if not c["ok"] and not c.get("skipped")]
    assert res["score"] >= R.FLOORS["static"], bad[:20]


def test_offline_score_meets_floor():
    offline_check = pytest.importorskip("offline_check")
    res = offline_check.run()
    bad = [f"{c.get('id')} {c.get('skill')}: {c.get('detail')}" for c in res.get("checks", []) if not c.get("ok")]
    assert res["score"] >= R.FLOORS["offline"], bad[:20]


def test_compose_weights_and_partial():
    full = {k: {"score": 1.0} for k in R.WEIGHTS}
    full["live"] = {"score": 1.0, "uplift": 0.5}
    v = R.compose(full)
    assert v["composite"] == 1.0 and v["pass"] and not v["partial"]
    part = R.compose({"static": {"score": 1.0}})
    assert part["partial"] and part["composite"] is None and not part["pass"] and part["composite_partial"] == 1.0
    low = R.compose({**full, "trigger": {"score": 0.5}})
    assert not low["pass"] and not low["floors_ok"]
    flat = R.compose({**full, "live": {"score": 0.9, "uplift": 0.05}})
    assert not flat["pass"] and flat["floors_ok"]
