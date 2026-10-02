"""Live: each phase-3 part-B recipe's test_file through the engine against a real OpenJev (not CI). Sequential, one model.

    OPENJEV_LIVE=1 .venv/bin/python -m pytest -q mcp/tests/live/test_live_recipes_p3b.py [-k rag_gate]
A case runs when test_mcp_recipes_p3b.inputs_of maps its request onto the recipe's inputs and the case expects a 200;
the rest are skipped. Expectations are held against the derived answers of the questions the recipe asks (the single
question of taxonomy_classify, multistep_tick and threshold_audit is renamed to the recipe's question id)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import recipe_harness as rh  # noqa: E402
import test_mcp_recipes_p3b as p3b  # noqa: E402

from openjev_mcp.config import load_config  # noqa: E402
from openjev_mcp.http import OpenJevClient  # noqa: E402
from openjev_mcp.recipes.engine import run_recipe  # noqa: E402

pytestmark = pytest.mark.live


@pytest.mark.anyio
@pytest.mark.parametrize("recipe_id", p3b.IDS)
async def test_recipe_test_file(recipe_id):
    recipe = rh.load(recipe_id)
    env = {"OPENJEV_MCP_RETRIES": "1"}
    if os.environ.get("OPENJEV_BASE_URL"):
        env["OPENJEV_BASE_URL"] = os.environ["OPENJEV_BASE_URL"]
    config = load_config(env)
    client = OpenJevClient(config)
    results = []
    try:
        for case in rh.cases_of(recipe.test_file).values():
            req = case["request"]
            if (case.get("expect") or {}).get("status", 200) != 200:
                continue
            inputs = p3b.inputs_of(recipe_id, req)
            if inputs is None:
                results.append((case["id"], "skip", "request is not shaped like this recipe's"))
                continue
            out = await run_recipe(recipe, inputs, client=client, config=config, read_options=p3b.read_options_of(req))
            if out.degraded:
                results.append((case["id"], "fail", out.reason))
                continue
            fails, checked = rh.check_expect(recipe_id, out, p3b.expect_for(recipe_id, case))
            results.append((case["id"], "skip" if not checked else "fail" if fails else "pass",
                            "; ".join(fails) or f"{out.decision}: {out.reason}"))
    finally:
        await client.aclose()
    for cid, status, detail in results:
        print(f"{recipe_id} {cid}: {status} {detail}")
    ran = [r for r in results if r[1] != "skip"]
    if not ran:
        pytest.skip(f"{recipe_id}: no case of {recipe.test_file} maps onto the recipe")
    bad = [f"{cid}: {detail}" for cid, status, detail in ran if status == "fail"]
    assert not bad, f"{recipe_id} {len(ran) - len(bad)}/{len(ran)} passed: " + " | ".join(bad)
