"""Live: each phase-3 (part A) recipe's test_file through the engine against a real OpenJev (not CI). Sequential, one model.

    OPENJEV_LIVE=1 .venv/bin/python -m pytest -q mcp/tests/live/test_live_recipes_p3a.py [-k alert_triage]
A case runs when its state is shaped like the recipe's (test_mcp_recipes_p3a.inputs_from_case) and its expectations
name questions the recipe asks (re-keyed to the case's ids by case_expect); the rest are skipped. Cases that expect an
HTTP error status are sent raw."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import recipe_harness as rh  # noqa: E402
import test_mcp_recipes_p3a as p3a  # noqa: E402

from openjev_mcp.http import OpenJevClient  # noqa: E402

RECIPES = p3a.IDS
pytestmark = pytest.mark.live


@pytest.mark.anyio
@pytest.mark.parametrize("recipe_id", RECIPES)
async def test_recipe_test_file(recipe_id, monkeypatch):
    monkeypatch.setattr(rh, "inputs_from_case", p3a.inputs_from_case)
    monkeypatch.setattr(rh, "check_expect", lambda rid, outcome, expect: _check(rid, outcome, expect))
    recipe = rh.load(recipe_id)
    env = {"OPENJEV_MCP_RETRIES": "1"}
    if os.environ.get("OPENJEV_BASE_URL"):
        env["OPENJEV_BASE_URL"] = os.environ["OPENJEV_BASE_URL"]
    from openjev_mcp.config import load_config
    config = load_config(env)
    client = OpenJevClient(config)
    results = []
    try:
        for case in rh.cases_of(recipe.test_file).values():
            results.append(await rh.run_case(recipe, case, client, config))
    finally:
        await client.aclose()
    ran = [r for r in results if r["status"] != "skip"]
    for r in results:
        print(f"{recipe_id} {r['id']}: {r['status']} {r['detail']}")
    if not ran:
        pytest.skip(f"{recipe_id}: no case of {recipe.test_file} maps onto the recipe")
    bad = [f"{r['id']}: {r['detail']}" for r in ran if r["status"] == "fail"]
    assert not bad, f"{recipe_id} {len(ran) - len(bad)}/{len(ran)} passed: " + " | ".join(bad)


_REAL_CHECK = rh.check_expect


def _check(rid, outcome, expect):
    return _REAL_CHECK(rid, *p3a.case_expect(rid, outcome, expect))
