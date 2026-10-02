"""library.py + scripts/gen_library.py: templates, patterns, guide (spec 2.18, section 3)."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys

import jsonschema
import pytest
import stubs

from openjev_mcp import library, lint, schemas
from openjev_mcp.limits import default_limits

HERE = os.path.dirname(__file__)
GEN = os.path.join(HERE, "..", "scripts", "gen_library.py")
SPEC = os.path.join(stubs.REPO, "docs", "mcp-skill-spec", "OPENJEV_MCP_SKILLS_SPEC.md")
DATA = os.path.join(HERE, "..", "openjev_mcp", "data")
ID = re.compile(r"^[a-z0-9_]{1,64}$")
needs_spec = pytest.mark.skipif(not os.path.exists(SPEC), reason="spec checkout not available")


def _cases() -> dict[str, dict]:
    out = {}
    for f in sorted(x for x in os.listdir(os.path.join(stubs.SPEC_TESTS, "cases")) if x.endswith(".json")):
        for c in json.load(open(os.path.join(stubs.SPEC_TESTS, "cases", f)))["cases"]:
            out[c["id"]] = c
    return out


def _gen():
    spec = importlib.util.spec_from_file_location("gen_library", GEN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_library"] = mod
    spec.loader.exec_module(mod)
    return mod


def _req(t: dict, s: dict) -> dict:
    return {**t["options"], "state": s["state"], "questions": t["questions"]}


def test_index_and_ids():
    idx = library.templates_index()
    assert idx and [i["id"] for i in idx] == library.template_ids() == sorted(library.template_ids())
    for i in idx:
        assert ID.match(i["id"]) and set(i) == {"id", "title", "description", "question_count", "state_count"}
        assert 5 <= i["state_count"] <= 10 and i["question_count"] >= 1
    assert library.template("nope") is None


@pytest.mark.parametrize("tid", library.template_ids())
def test_template_valid_and_lints(tid):
    t = library.template(tid)
    assert set(t) == {"id", "title", "questions", "options", "states", "source_case"}
    assert 5 <= len(t["states"]) <= 10
    assert len({s["id"] for s in t["states"]}) == len(t["states"]) and all(ID.match(s["id"]) for s in t["states"])
    jsonschema.Draft202012Validator({"$ref": "#/$defs/QuestionSet", "$defs": schemas.DEFS}).validate(t["questions"])
    for s in t["states"]:
        rep = lint.lint_request(_req(t, s), limits=default_limits())
        assert not rep.errors, [e.line() for e in rep.errors]


@pytest.mark.parametrize("tid", library.template_ids())
def test_template_states_are_source_requests(tid):
    cases = _cases()
    t = library.template(tid)
    assert t["source_case"] in cases
    reqs = [json.dumps(c["request"], sort_keys=True) for c in cases.values() if "request" in c]
    for s in t["states"]:
        assert json.dumps(_req(t, s), sort_keys=True) in reqs


def test_captured_template_replays():
    import httpx
    t = library.template("spec_examples_escalate")
    cap = json.load(open(os.path.join(stubs.SPEC_TESTS, "spec_build", "captured.json")))
    cases = _cases()
    transport = stubs.replay_transport()

    async def run():
        async with httpx.AsyncClient(transport=transport, base_url="http://oj.test") as c:
            return [(await c.post("/v1/systemone", json=_req(t, s))).json() for s in t["states"]]

    got = asyncio.run(run())
    want = [cap[k]["body"] for k in cap if k in cases and json.dumps(cases[k]["request"], sort_keys=True)
            in {json.dumps(_req(t, s), sort_keys=True) for s in t["states"]}]
    assert len(got) == 7 and sorted(map(json.dumps, got)) == sorted(map(json.dumps, want))


def test_patterns():
    p = library.patterns()
    assert p and list(p) == sorted(p)
    for key, e in p.items():
        assert key == f"{e['usage']}.{e['question_id']}" and ID.match(e["usage"])
        assert e["source"] in ("captured", "expect") and isinstance(e["measured"], list)
        assert e["question"]["type"] in ("noul", "choice", "score")
    assert {e["source"] for e in p.values()} == {"captured", "expect"}
    assert p["support_ticket_triage.dept"]["source"] == "captured"
    p["x"] = 1
    assert "x" not in library.patterns()


def test_generator_byte_identical(tmp_path, monkeypatch):
    gen = _gen()
    monkeypatch.setattr(gen, "OUT", tmp_path)
    gen.main()
    for name in ("templates.json", "patterns.json", "guide_authoring.md"):
        assert (tmp_path / name).read_bytes() == open(os.path.join(DATA, name), "rb").read(), name


@needs_spec
def test_guide_equals_spec_section_3():
    text = open(SPEC, encoding="utf-8").read()
    m = re.search(r"^## 3\. Question-authoring guide\n.*?(?=\n---\n\n## 4\. )", text, re.S | re.M)
    assert library.guide() == m.group(0).rstrip("\n") + "\n"
    assert library.guide().startswith("## 3. Question-authoring guide") and "### 3.1" in library.guide()


def test_no_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    library._templates.cache_clear(); library._patterns.cache_clear(); library.guide.cache_clear()
    assert library.template_ids() and library.patterns() and library.guide()
