"""compile (spec 2.14): replays of ex-compile-*, the deterministic pre-rules, instantiation, probe, calibrate, prompt."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
import stubs

from openjev_mcp import prompts
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.tools import ToolContext, compile as comp
from openjev_mcp.validate import validate_args, validate_output

pytestmark = pytest.mark.anyio

CASES, CAPTURED = stubs._load_cases()
CASE = {c["id"]: c for c in CASES}
FIXTURE = {
    "id": "support_fixture", "title": "Support triage fixture", "usage_type": "01-ticket-triage",
    "description": "Route one customer email to a team",
    "input_schema": {"type": "object", "required": ["email"], "properties": {"email": {"type": "string"}}},
    "steps": [{"id": "read", "kind": "read", "state_template": "{{email}}"}],
    "question_profiles": {"strict": CASE["ex-compile-draft"]["request"]["questions"]},
    "policy": {"escalate_at": 1.5}, "decisions": ["route", "escalate"],
    "combine": "escalate if frustration >= escalate_at; route otherwise", "fail_mode": "closed",
    "fallback": {"interactive": "route", "unattended": "route"}, "fallback_decision": "route", "test_file": None,
    "limitations": ["fixture limitation"]}
VARIANT = {**FIXTURE, "id": "support_fixture_dup", "description": "Is this email a duplicate of an earlier one",
           "variant_of": "support_fixture"}


@pytest.fixture(autouse=True)
def registered():
    """P19 registers the tool; here the schemas are registered for the test and removed after (global dicts)."""
    from openjev_mcp import schemas
    comp.register(Config())
    yield
    schemas.INPUT_SCHEMAS.pop("compile", None)
    schemas.OUTPUT_SCHEMAS.pop("compile", None)


@pytest.fixture
def recipes_dir(tmp_path):
    (tmp_path / "support_fixture.json").write_text(json.dumps(FIXTURE))
    return str(tmp_path)


def ctx_for(transport, **kw) -> ToolContext:
    config = Config(base_url="http://oj.test:8080", **kw)
    client = OpenJevClient(config, transport=transport)
    return ToolContext(config, client, LimitsCache(client), ProgressEmitter.noop(), None)


def posts(transport):
    return [r for r in transport.requests if r.method == "POST"]


def choice(key: str, options, p: float = 0.9) -> dict:
    rest = (1 - p) / max(len(options) - 1, 1)
    return {"type": "choice", "choice": key, "probabilities": {o: (p if o == key else rest) for o in options},
            "confidence": p}


def engine(fn) -> stubs.RecordingTransport:
    """A transport answering POST /v1/systemone with fn(body) -> answers; everything else is a failure."""
    def handler(request: httpx.Request) -> httpx.Response:
        transport.requests.append(request)
        if request.url.path != "/v1/systemone":
            return httpx.Response(404, json={"detail": "x"})
        body = json.loads(request.content)
        return httpx.Response(200, json={"model": "openjev-0.1", "answers": fn(body),
                                         "usage": {"input_tokens": 10, "output_tokens": 0}})
    transport = stubs.RecordingTransport(handler)
    return transport


def typing_engine(table: dict, route: str | None = None):
    """qtype by the sub-decision text in the state, recipe by `route`."""
    def fn(body):
        q = next(iter(body["questions"]))
        if q == "qtype":
            kind = next(v for k, v in table.items() if k in body["state"])
            return {"qtype": choice(kind, list(comp.QTYPE_CRITERIA))}
        if q == "recipe":
            return {"recipe": choice(route, list(body["questions"]["recipe"]["criteria"]))}
        return stubs.default_answers(body["questions"], body["state"], {})
    return engine(fn)


# ---------------------------------------------------------------- pre-rules and typing

@pytest.mark.parametrize("text,qtype", [
    ("whether the ticket is urgent", "noul"), ("if it needs a refund", "noul"), ("does it leak a secret", "noul"),
    ("which team should handle it", "choice"), ("what kind of issue it is", "choice"), ("pick a priority", "choice"),
    ("how much the customer is annoyed", "score"), ("how severe the outage is", "score"), ("rate the reply", "score"),
    ("how many errors are in the log", "not_typed"), ("the refund amount", "not_typed"),
    ("a summary", "not_typed"), ("write a reply", "not_typed"), ("the invoice date", "not_typed"),
    ("how angry the customer is", None), ("the tone", None)])
def test_pre_rules(text, qtype):
    assert comp.pre_rule(text) == qtype


@pytest.mark.parametrize("n,text,qtype", [
    (1, "whether the ticket is urgent", "noul"), (2, "which team should handle it", "choice"),
    (3, "how angry the customer is", "score"), (4, "how many errors are in the log", "not_typed")])
async def test_ex_compile_qtype_replays(n, text, qtype):
    cid = f"ex-compile-qtype-{n}"
    t = stubs.replay_transport([cid])
    got, p, how = await comp.type_sub_decision(ctx_for(t), text, rules=False)
    assert (got, how) == (qtype, "read")
    assert p == pytest.approx(CAPTURED[cid]["body"]["answers"]["qtype"]["probabilities"][qtype])
    assert len(posts(t)) == 1
    assert json.loads(posts(t)[0].content)["samples"] == 1


async def test_pre_rule_skips_the_read():
    t = stubs.fail_on_request_transport()
    assert await comp.type_sub_decision(ctx_for(t), "whether the ticket is urgent") == ("noul", None, "pre-rule")
    assert not t.requests


# ---------------------------------------------------------------- routing

@pytest.fixture
def library(monkeypatch):
    """The 24 primary recipe descriptions of spec 2.18, as the ex-compile-recipe cases carry them."""
    crit = CASE["ex-compile-recipe"]["request"]["questions"]["recipe"]["criteria"]
    fake = {k: SimpleNamespace(description=v) for k, v in crit.items() if k != "none"}
    monkeypatch.setattr(comp, "primaries", lambda config: fake)
    return fake


async def test_ex_compile_recipe_route(library):
    t = stubs.replay_transport(["ex-compile-recipe"])
    rid, p, runner = await comp.route(ctx_for(t), CASE["ex-compile-recipe"]["request"]["state"].removeprefix("Human intent: "))
    assert (rid, runner) == ("ticket_triage", "taxonomy_classify") and p == pytest.approx(0.9999, abs=1e-4)
    assert len(library) == 24


async def test_ex_compile_recipe_none_returns_not_a_decision(library):
    t = stubs.replay_transport(["ex-compile-recipe-none"])
    out = await comp.compile_tool(ctx_for(t), {"intent": "write me a release announcement for version 2.0"})
    assert out["recipe"]["id"] == "none" and out["recipe"]["not_a_decision"] is True
    assert out["recipe"]["p"] == pytest.approx(0.9994, abs=1e-4)
    assert out["draft_request"] == {} and "not an OpenJev read" in out["next_steps"][0]
    assert validate_output("compile", out) == []
    assert len(posts(t)) == 1


async def test_route_ignores_variants_and_lists_them(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(FIXTURE))
    (tmp_path / "b.json").write_text(json.dumps(VARIANT))
    seen = []

    def fn(body):
        seen.append(body)
        q = next(iter(body["questions"]))
        if q == "recipe":
            return {"recipe": choice("support_fixture", list(body["questions"]["recipe"]["criteria"]))}
        return stubs.default_answers(body["questions"], body["state"], {})
    t = engine(fn)
    out = await comp.compile_tool(ctx_for(t, recipes_dir=str(tmp_path)), {"intent": "sort out support emails"})
    crit = seen[0]["questions"]["recipe"]["criteria"]
    assert "support_fixture_dup" not in crit and crit["none"] == comp.NONE_DESCRIPTION and list(crit)[-1] == "none"
    assert crit["support_fixture"] == FIXTURE["description"]
    assert out["recipe"]["variants"] == ["support_fixture_dup"]
    assert any("support_fixture_dup" in q and "Which variant" in q for q in out["human_questions"])


# ---------------------------------------------------------------- draft, probe, lint

EMAIL = CASE["ex-compile-draft"]["request"]["state"]


async def test_ex_compile_draft_probe_and_lint(recipes_dir):
    t = stubs.replay_transport(["ex-compile-draft"])
    out = await comp.compile_tool(ctx_for(t, recipes_dir=recipes_dir),
                                  {"intent": "tell me if a support email is angry", "recipe": "support_fixture",
                                   "sample_inputs": [EMAIL]})
    assert out["draft_request"]["questions"] == CASE["ex-compile-draft"]["request"]["questions"]
    assert out["draft_request"]["state"] == "<SLOT:email>" and out["draft_request"]["model"] == "openjev-latest"
    assert out["lint"]["valid"] is True and out["lint"]["errors"] == []
    assert out["probe"] == [{"input": 0, "answers": {"dept": "tech (1.0)", "frustration": "2.0 = angry or furious",
                                                     "churn": "1.0"}}]
    assert out["recipe"] == {"id": "support_fixture", "p": 1.0, "runner_up": None, "not_a_decision": False}
    assert [s["path"] for s in out["slots"]] == ["state"] and out["slots"][0]["name"] == "email"
    assert any("fixture limitation" in q for q in out["human_questions"])
    assert any("escalate_at" in q for q in out["human_questions"])
    assert out["next_steps"][-1] == "save as a recipe file"
    assert validate_output("compile", out) == []
    assert len(posts(t)) == 1


async def test_draft_lints_clean_standalone():
    from openjev_mcp.lint import lint_request
    from openjev_mcp.limits import default_limits
    rep = lint_request(CASE["ex-compile-draft"]["request"], limits=default_limits(), autofix=False)
    assert rep.valid and not rep.errors


async def test_routing_flow_with_sub_decisions_labels_and_redirect(recipes_dir):
    t = typing_engine({"angry": "score", "team": "choice", "how many": "not_typed", "tone": "choice"},
                      route="support_fixture")
    out = await comp.compile_tool(ctx_for(t, recipes_dir=recipes_dir), {
        "intent": "tell me if a support email is angry and who should take it",
        "sub_decisions": ["how angry the customer is", "which team should take it", "how many emails it has",
                          "the tone of the reply"],
        "labels": {"which team": ["billing", "tech", "sales"]}})
    subs = {s["text"]: s for s in out["sub_decisions"]}
    assert out["recipe"]["id"] == "support_fixture" and out["recipe"]["p"] == pytest.approx(0.9)
    assert subs["how angry the customer is"]["qtype"] == "score" and subs["how angry the customer is"]["note"].startswith("read")
    assert subs["which team should take it"]["qtype"] == "choice"
    assert subs["which team should take it"]["question_id"] == "dept"
    assert subs["how many emails it has"]["qtype"] == "not_typed" and subs["how many emails it has"]["note"] == "pre-rule"
    assert "select_extraction" in subs["how many emails it has"]["redirect"]
    q = out["draft_request"]["questions"]
    assert list(q["dept"]["criteria"]) == ["billing", "tech", "sales", "other"]   # recipe descriptions and escape kept
    assert q["dept"]["criteria"]["tech"].startswith("bugs, errors")
    assert {"name": "dept descriptions", "path": "questions.dept.criteria"} .items() <= out["slots"][-1].items()
    assert subs["how angry the customer is"]["question_id"] is None or subs["how angry the customer is"]["question_id"] in q
    assert validate_output("compile", out) == []
    assert "/v1/chat/completions" not in [r.url.path for r in t.requests]   # never chat


async def test_labels_without_description_become_slots_and_block_probe(recipes_dir):
    t = typing_engine({"urgency": "score"})
    out = await comp.compile_tool(ctx_for(t, recipes_dir=recipes_dir), {
        "intent": "classify support emails by urgency", "recipe": "support_fixture",
        "sub_decisions": ["which priority level it has"],
        "labels": {"priority": ["p1", "p2"]}, "sample_inputs": ["x"]})
    # "which ..." is a pre-rule choice; priority matches no recipe question, so a new one is added beside the recipe's
    qid = next(s["question_id"] for s in out["sub_decisions"])
    crit = out["draft_request"]["questions"][qid]["criteria"]
    assert crit == {"p1": "<SLOT:p1 description>", "p2": "<SLOT:p2 description>"}
    assert {"p1 description", "p2 description"} <= {s["name"] for s in out["slots"]}
    assert "probe" not in out and out["next_steps"][0].startswith("fill the <SLOT")
    assert not [r for r in posts(t)]   # pre-rule typed, recipe given, probe held back: no read at all


async def test_noul_and_score_fresh_questions(recipes_dir):
    t = engine(lambda b: {"qtype": choice("noul", list(comp.QTYPE_CRITERIA))})
    out = await comp.compile_tool(ctx_for(t, recipes_dir=recipes_dir), {
        "intent": "check the shipping mail", "recipe": "support_fixture",
        "sub_decisions": ["whether the parcel is lost", "the shipping speed"],
        "labels": {"shipping speed": {"slow": "more than a week", "fast": "next day"}}})
    qs = out["draft_request"]["questions"]
    assert qs["parcel_lost"] == {"type": "noul", "instructions": "Is it true: whether the parcel is lost?"}
    assert [x["qtype"] for x in out["sub_decisions"]] == ["noul", "noul"]   # pre-rule, then one read
    assert qs["shipping_speed"]["type"] == "noul" and len(posts(t)) == 1
    assert validate_output("compile", out) == []


async def test_typed_call_from_criteria_become_slots():
    out = await comp.compile_tool(ctx_for(stubs.fail_on_request_transport()),
                                  {"intent": "turn a command into a call", "recipe": "typed_call"})
    crit = out["draft_request"]["questions"]["function"]["criteria"]
    assert "no_match" in crit and any(k.startswith("<SLOT:functions") for k in crit)
    assert {s["path"] for s in out["slots"]} >= {"questions.function.criteria.<SLOT:functions: name>"}
    assert "probe" not in out and "calibration" not in out


async def test_unknown_recipe_is_refused_before_any_request():
    t = stubs.fail_on_request_transport()
    with pytest.raises(ToolError) as e:
        await comp.compile_tool(ctx_for(t), {"intent": "something here", "recipe": "nope"})
    assert e.value.code == "OJ_INVALID_INPUT" and not t.requests


async def test_calibration_runs_on_a_complete_draft(recipes_dir):
    t = engine(lambda b: stubs.default_answers(b["questions"], b["state"], {}))
    ex = [{"state": f"mail {i}", "label": {"churn": i % 2 == 0}} for i in range(6)]
    out = await comp.compile_tool(ctx_for(t, recipes_dir=recipes_dir), {
        "intent": "angry support email check", "recipe": "support_fixture", "labelled_examples": ex})
    assert out["calibration"]["n"] == 6 and "churn" in out["calibration"]["per_question"]
    assert "add 10-20 labelled examples and run calibrate" not in out["next_steps"]


# ---------------------------------------------------------------- schema and prompt

def test_schema_registration_and_input_validation():
    spec = comp.register(Config())
    assert spec.name == "compile" and spec.annotations["readOnlyHint"] is True
    assert validate_args("compile", {"intent": "abc"}) is not None   # minLength 5
    assert validate_args("compile", {"intent": "a real intent", "sample_inputs": ["x"] * 11}) is not None
    assert validate_args("compile", {"intent": "a real intent", "recipe": "typed_call"}) is None


async def test_author_question_prompt_seeds_with_compile(recipes_dir):
    spec = comp.AUTHOR_QUESTION
    assert spec.name == "author_question" and [(a.name, a.required) for a in spec.arguments] == [
        ("intent", True), ("examples", False)]
    t = typing_engine({}, route="support_fixture")
    old = list(prompts.PROMPTS)
    prompts.PROMPTS[:] = [spec]
    try:
        res = await prompts.get("author_question", {"intent": "sort support emails", "examples": "a\nb"},
                                ctx_for(t, recipes_dir=recipes_dir))
        with pytest.raises(prompts.PromptError):
            await prompts.get("author_question", {}, ctx_for(t))
    finally:
        prompts.PROMPTS[:] = old
    text = res["messages"][0]["content"]["text"]
    assert res["messages"][0]["role"] == "user" and "sort support emails" in text and "support_fixture" in text
    assert "Example inputs from the human:\na\nb" in text and "draft_request" in text


async def test_author_question_prompt_survives_a_dead_server():
    t = stubs.fault_transport(exc=httpx.ConnectError("down"))
    msgs = await comp._author_question({"intent": "sort support emails"}, ctx_for(t, retries=0))
    assert "compile was unavailable" in msgs[0]["content"]["text"]
