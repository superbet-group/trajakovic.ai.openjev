"""Live: each phase-2 recipe's test_file through the engine against a real OpenJev (not CI). Sequential, one model.

    OPENJEV_LIVE=1 .venv/bin/python -m pytest -q mcp/tests/live/test_live_recipes_p2.py [-k command_gate]
A case runs when its state is shaped like the recipe's (recipe_harness.inputs_from_case) and its expectations name
questions the recipe asks; the rest are skipped. Cases that expect an HTTP error status are sent raw."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import recipe_harness as rh  # noqa: E402

from openjev_mcp.http import OpenJevClient  # noqa: E402

RECIPES = ("command_gate", "act_or_ask", "injection_screen", "done_gate", "moderation", "model_routing",
           "skill_selection", "typed_call")
pytestmark = pytest.mark.live


@pytest.mark.anyio
@pytest.mark.parametrize("recipe_id", RECIPES)
async def test_recipe_test_file(recipe_id):
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
    if recipe_id == "command_gate":
        assert len(ran) == 14
