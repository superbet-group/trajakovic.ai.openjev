"""Published JSON Schemas: inlined, Draft 2020-12, portable roots (TASKS 1.7, 1.29)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import openjev_mcp
from openjev_mcp import CORE_TOOL_NAMES
from openjev_mcp import schemas
from openjev_mcp.schemas import (DEFS, INPUT_SCHEMAS, OUTPUT_SCHEMAS, SCHEMA_RESOURCE, SCHEMA_URI,
                                 inline)

SPEC = Path(__file__).resolve().parents[2] / "docs" / "mcp-skill-spec" / "OPENJEV_MCP_SKILLS_SPEC.md"
DEF_NAMES = ["State", "NoulQuestion", "ChoiceQuestion", "ScoreQuestion", "Question", "QuestionSet",
             "ReadOptions", "Band", "NoulAnswer", "ChoiceAnswer", "ScoreAnswer", "Answer", "Meta",
             "LintFinding", "QuestionStats", "BatchHeader", "BatchRow", "ToolError"]
ALL = [("in", t, s) for t, s in INPUT_SCHEMAS.items()] + [("out", t, s) for t, s in OUTPUT_SCHEMAS.items()]


def keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from keys(v)


def test_tool_keys_match_tool_names():
    assert set(INPUT_SCHEMAS) == set(OUTPUT_SCHEMAS) >= set(CORE_TOOL_NAMES)
    assert len(ALL) >= 2 * len(CORE_TOOL_NAMES)


@pytest.mark.parametrize("kind,tool,schema", ALL, ids=[f"{k}-{t}" for k, t, _ in ALL])
def test_schema_walk(kind, tool, schema):
    assert schema["$schema"] == SCHEMA_URI
    assert schema["type"] == "object"
    assert not {"$ref", "$defs"} & set(keys(schema))
    if kind == "in":
        assert not {"oneOf", "anyOf", "allOf"} & set(schema)
    Draft202012Validator.check_schema(schema)
    json.dumps(schema)


def test_nested_one_of_kept():
    assert "oneOf" in INPUT_SCHEMAS["ask"]["properties"]["state"]
    assert "oneOf" in INPUT_SCHEMAS["classify"]["properties"]["escape"]
    assert "oneOf" in OUTPUT_SCHEMAS["ask"]["properties"]["answers"]["additionalProperties"]


def test_shapes_kept_for_prepare_hooks():
    assert INPUT_SCHEMAS["score"]["properties"]["levels"]["type"] == "array"
    assert INPUT_SCHEMAS["classify"]["properties"]["labels"]["type"] == "object"


def test_inputs_do_not_alias_defs():
    INPUT_SCHEMAS["ask"]["properties"]["options"]["x-probe"] = 1
    try:
        assert "x-probe" not in DEFS["ReadOptions"]
    finally:
        del INPUT_SCHEMAS["ask"]["properties"]["options"]["x-probe"]


def test_1_2_optional_fields_present():
    assert INPUT_SCHEMAS["lint"]["properties"]["emit"]["items"]["enum"] == ["body", "curl", "python"]
    status = OUTPUT_SCHEMAS["status"]["properties"]
    assert "capabilities" in status and "mcp" in status
    meta = DEFS["Meta"]["properties"]
    assert {"body_hashes", "server_timing", "timeout_ms_used", "chunks_estimate"} <= set(meta)
    assert "body_hash" in OUTPUT_SCHEMAS["lint"]["properties"]


def test_inline_nested_refs_and_sibling_merge():
    defs = {"A": {"type": "object", "description": "a", "properties": {"b": {"$ref": "#/$defs/B"}}},
            "B": {"type": "string", "minLength": 1}}
    src = {"properties": {"x": {"$ref": "#/$defs/A", "description": "mine"},
                          "y": {"items": [{"$ref": "#/$defs/B"}]}},
           "$defs": {"ignored": {}}}
    out = inline(src, defs)
    assert out == {"$schema": SCHEMA_URI,
                   "properties": {"x": {"type": "object", "description": "mine",
                                        "properties": {"b": {"type": "string", "minLength": 1}}},
                                  "y": {"items": [{"type": "string", "minLength": 1}]}}}
    assert "$ref" in src["properties"]["x"] and "$defs" in src
    out["properties"]["x"]["properties"]["b"]["minLength"] = 9
    assert defs["B"]["minLength"] == 1
    assert inline(src, defs) == inline(src, defs)


def test_inline_unknown_def():
    with pytest.raises(KeyError):
        inline({"properties": {"x": {"$ref": "#/$defs/Nope"}}}, {})
    with pytest.raises(KeyError):
        inline({"$ref": "https://example.com/x"}, {})


def test_inline_default_defs_roots_schema():
    out = inline({"type": "object", "properties": {"s": {"$ref": "#/$defs/State"}}})
    assert list(out)[0] == "$schema" and out["properties"]["s"] == DEFS["State"]


def test_schema_resource():
    assert schemas.SCHEMA_URI == "https://json-schema.org/draft/2020-12/schema"
    assert SCHEMA_RESOURCE["$schema"] == SCHEMA_URI and SCHEMA_RESOURCE["$id"] == "openjev://schema"
    assert SCHEMA_RESOURCE["$defs"] is DEFS
    assert list(DEFS) == DEF_NAMES
    json.loads(json.dumps(SCHEMA_RESOURCE))
    Draft202012Validator.check_schema({**SCHEMA_RESOURCE, "$id": "openjev://schema"})


def test_defs_match_spec_verbatim():
    if not SPEC.exists():
        pytest.skip("spec not present")
    text = SPEC.read_text()
    block = re.search(r"```json\n(\{\n \"\$schema\".*?\n)```", text, re.S).group(1)
    assert json.loads(block)["$defs"] == DEFS


def _obj(**props):
    return {"type": "object", "properties": props}


def test_register_tool_schemas_inlines_and_stores():
    try:
        schemas.register_tool_schemas("t_reg", _obj(q={"$ref": "#/$defs/Mine"}, s={"$ref": "#/$defs/NoulQuestion"}),
                                      _obj(), {"Mine": {"type": "integer"}})
        got = INPUT_SCHEMAS["t_reg"]
        assert got["$schema"] == SCHEMA_URI and got["properties"]["q"] == {"type": "integer"}
        assert got["properties"]["s"]["required"] == ["type", "instructions"] and "$ref" not in json.dumps(got)
        assert OUTPUT_SCHEMAS["t_reg"]["type"] == "object"
    finally:
        INPUT_SCHEMAS.pop("t_reg", None)
        OUTPUT_SCHEMAS.pop("t_reg", None)


@pytest.mark.parametrize("key", ["oneOf", "anyOf", "allOf"])
def test_register_rejects_root_combinators(key):
    with pytest.raises(ValueError, match=key):
        schemas.register_tool_schemas("t_bad", {"type": "object", key: [{}]}, _obj())
    assert "t_bad" not in INPUT_SCHEMAS


def test_register_rejects_non_object_root_and_leftover_ref():
    with pytest.raises(ValueError, match="object"):
        schemas.register_tool_schemas("t_bad", {"type": "array"}, _obj())
    with pytest.raises(KeyError):
        schemas.register_tool_schemas("t_bad", _obj(a={"$ref": "#/$defs/Missing"}), _obj())
    with pytest.raises(KeyError):
        schemas.register_tool_schemas("t_bad", _obj(a={"$ref": "http://x/y"}), _obj())
    with pytest.raises(ValueError, match=r"\$ref"):
        schemas.register_tool_schemas("t_bad", _obj(a={"const": {"$ref": 1}, "properties": {"$ref": {"type": "string"}}}), _obj())
    assert "t_bad" not in INPUT_SCHEMAS
