"""Recipe registry: built-ins plus OPENJEV_MCP_RECIPES under one loader (spec 2.18)."""
from __future__ import annotations

import json

import pytest

from openjev_mcp.config import load_config
from openjev_mcp.recipes import engine, registry
from openjev_mcp.recipes.registry import load_all


@pytest.fixture(autouse=True)
def fresh():
    registry.clear_cache()
    yield
    registry.clear_cache()


def gate_doc(**over) -> dict:
    return {**json.loads(engine.resources.files("openjev_mcp.recipes").joinpath("builtin", "command_gate.json")
                         .read_text(encoding="utf-8")), **over}


def extra_dir(tmp_path, files: dict):
    for name, content in files.items():
        (tmp_path / name).write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return load_config({"OPENJEV_MCP_RECIPES": str(tmp_path)})


def test_builtin_only():
    reg = load_all(load_config({}))
    assert "command_gate" in reg.ids() and reg.warnings == ()
    assert reg.get("command_gate").id == "command_gate"
    assert reg.document("command_gate")["id"] == "command_gate"


def test_index_rows():
    row = next(r for r in load_all(load_config({})).index() if r["id"] == "command_gate")
    assert set(row) == {"id", "title", "description", "decisions", "input", "variant_of"}
    assert row["decisions"] == ["allow", "ask", "deny"] and row["variant_of"] is None
    assert row["input"]["required"] == ["task", "command"] and row["input"]["properties"]["command"] == "string"


def test_unknown_id():
    with pytest.raises(engine.RecipeError):
        load_all(load_config({})).get("nope")


def test_cached_per_process():
    cfg = load_config({})
    assert load_all(cfg) is load_all(cfg)
    assert load_all(cfg).ids() == load_all(cfg).ids()


def test_good_extra_recipe_loads_with_variant_of(tmp_path):
    cfg = extra_dir(tmp_path, {"my_gate.json": gate_doc(id="my_gate", variant_of="command_gate", title="Mine")})
    reg = load_all(cfg)
    assert "my_gate" in reg.ids() and reg.warnings == ()
    row = next(r for r in reg.index() if r["id"] == "my_gate")
    assert row["variant_of"] == "command_gate" and row["title"] == "Mine"
    assert reg.ids().index("command_gate") < reg.ids().index("my_gate")


def test_bad_files_skipped_with_warning(tmp_path):
    broken = gate_doc(id="broken_one")
    del broken["steps"]
    cfg = extra_dir(tmp_path, {"good.json": gate_doc(id="good_one"), "junk.json": "{not json",
                               "broken.json": broken, "list.json": "[1]", "notes.txt": "ignored"})
    reg = load_all(cfg)
    assert "good_one" in reg.ids() and "broken_one" not in reg.ids()
    assert len(reg.warnings) == 3
    assert any("junk.json" in w and "invalid JSON" in w for w in reg.warnings)
    assert any("broken.json" in w and "skipped" in w for w in reg.warnings)
    assert any("list.json" in w for w in reg.warnings)


def test_shadowing_a_builtin_is_refused(tmp_path):
    cfg = extra_dir(tmp_path, {"command_gate.json": gate_doc(title="Evil gate")})
    reg = load_all(cfg)
    assert reg.get("command_gate").title != "Evil gate"
    assert len(reg.warnings) == 1 and "already defined" in reg.warnings[0] and "command_gate.json" in reg.warnings[0]


def test_duplicate_extra_ids_first_wins(tmp_path):
    cfg = extra_dir(tmp_path, {"a.json": gate_doc(id="dup_one", title="A"), "b.json": gate_doc(id="dup_one", title="B")})
    reg = load_all(cfg)
    assert reg.get("dup_one").title == "A" and len(reg.warnings) == 1


def test_cache_keyed_by_dir(tmp_path):
    plain = load_all(load_config({}))
    cfg = extra_dir(tmp_path, {"x.json": gate_doc(id="extra_one")})
    assert "extra_one" in load_all(cfg).ids() and "extra_one" not in plain.ids()
