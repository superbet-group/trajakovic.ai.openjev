"""Phase-2 builtin recipes (P07): load, replay of the captured spec examples, policy tables on stub answers,
fail modes, routing kill switch, load-time rejections. No model."""
from __future__ import annotations

import copy
import json
import os

import httpx
import pytest
import recipe_harness as rh
import stubs

from openjev_mcp.recipes.engine import RecipeError, build_requests, load_recipe

IDS = ("command_gate", "act_or_ask", "injection_screen", "done_gate", "moderation", "model_routing",
       "skill_selection", "typed_call")
MIN = {
    "act_or_ask": {"request": "reset the database", "planned_action": "ls"},
    "injection_screen": {"text": "hello", "source": "WebFetch result: https://x.example"},
    "done_gate": {"task": "t", "timeline": ["1. Read a.py"], "final_message": "done"},
    "moderation": {"channel": "Inbound email", "text": "hello", "categories": rh.DEFAULT_CATEGORIES},
    "model_routing": {"summary": "rename a variable"},
    "skill_selection": {"prompt": "make a deck", "roster": [{"id": "pdf", "description": "PDF files"}, {"id": "pptx", "description": "slide decks"}]},
    "typed_call": {"sentence": "restart the api", "functions": [{"name": "restart", "description": "restart instances"}, {"name": "scale", "description": "change replicas"}]},
}
FALLBACK = {"act_or_ask": ("ask", "ask"), "injection_screen": ("uncertain", "quarantine"), "done_gate": ("allow_stop", "allow_stop"),
            "moderation": ("uncertain", "uncertain"), "model_routing": ("sonnet", "sonnet"), "skill_selection": ("none", "none"),
            "typed_call": ("ask", "ask")}
# u* example -> (recipe, documented decision)
REPLAYS = {"u02": ("act_or_ask", "ask"), "u04": ("injection_screen", "quarantine"), "u05": ("done_gate", "block"),
           "u08": ("model_routing", "haiku"), "u09": ("skill_selection", "none"), "u17": ("moderation", "block")}


def doc(rid: str) -> dict:
    path = os.path.join(os.path.dirname(stubs.__file__), "..", "openjev_mcp", "recipes", "builtin", f"{rid}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sample(cid: str, rid: str) -> dict:
    return rh.inputs_from_case(rid, {"request": rh.captured_request(cid)})


@pytest.mark.parametrize("rid", IDS)
def test_builtin_loads(rid):
    r = rh.load(rid)
    assert r.id == rid and r.combine is not None and r.combine.clauses[-1].cond is None
    assert r.test_file and os.path.exists(os.path.join(rh.CASES_DIR, os.path.basename(r.test_file)))
    assert r.routing is (rid in ("model_routing", "skill_selection"))
    assert set(r.fallback.values()) <= set(r.decisions) and r.limitations
    assert "# deviation:" in doc(rid)["notes"]


def test_decision_lists_match_2_18():
    want = {"act_or_ask": {"act", "ask", "escalate"}, "injection_screen": {"pass", "uncertain", "quarantine"},
            "done_gate": {"allow_stop", "block", "escalate"}, "moderation": {"allow", "block", "uncertain"},
            "model_routing": {"haiku", "sonnet", "opus"}, "skill_selection": {"inject", "none", "abstain"},
            "typed_call": {"call", "confirm", "ask", "no_match"}}
    for rid, decisions in want.items():
        assert set(rh.load(rid).decisions) == decisions
    assert rh.load("skill_selection").decision_format == {"inject": "inject:{skill}"}
    assert rh.load("model_routing").outputs.keys() == {"effort"}


@pytest.mark.parametrize("rid", list(MIN))
def test_questions_are_verbatim_from_the_captured_requests(rid):
    src = {"act_or_ask": "u02", "injection_screen": "u04", "done_gate": "u05", "moderation": "u17",
           "model_routing": "u08", "skill_selection": "u09"}.get(rid)
    if src is None:
        pytest.skip("typed_call asks the function only")
    got = rh.load(rid).question_profiles["strict"]
    want = rh.captured_request(src)["questions"]
    assert list(got) == list(want)
    for qid, q in want.items():
        if rid == "moderation" and qid == "category":
            assert {k: v for k, v in got[qid].items() if k != "criteria"} == {k: v for k, v in q.items() if k != "criteria"}
        elif rid == "skill_selection":
            assert {k: v for k, v in got[qid].items() if k != "criteria"} == {k: v for k, v in q.items() if k != "criteria"}
        else:
            assert got[qid] == q


@pytest.mark.anyio
@pytest.mark.parametrize("cid", list(REPLAYS))
async def test_captured_example_replays_to_its_decision(cid):
    rid, decision = REPLAYS[cid]
    out, transport = await rh.run_replay(rh.load(rid), sample(cid, rid), [cid])
    assert out.decision == decision and out.requests == 1 and not out.degraded and out.error is None, out.reason
    assert json.loads(transport.requests[0].content) == rh.captured_request(cid)


@pytest.mark.anyio
async def test_replayed_signals():
    out, _ = await rh.run_replay(rh.load("act_or_ask"), sample("u02", "act_or_ask"), ["u02"])
    assert out.signals["go"] == "ask" and out.signals["go_p"] == pytest.approx(0.9896, abs=5e-5)
    out, _ = await rh.run_replay(rh.load("injection_screen"), sample("u04", "injection_screen"), ["u04"])
    assert out.signals["injects"] == pytest.approx(0.9998, abs=5e-5) and out.signals["harm"] == pytest.approx(2.9937, abs=5e-5)
    out, _ = await rh.run_replay(rh.load("model_routing"), sample("u08", "model_routing"), ["u08"])
    assert out.outputs == {"effort": "low"} and out.signals["effort"] == "low" and out.signals["tier"] == "haiku"
    out, _ = await rh.run_replay(rh.load("skill_selection"), sample("u09", "skill_selection"), ["u09"])
    assert out.signals["skill"] == "none" and out.signals["skill_p"] == pytest.approx(0.999, abs=5e-4)


@pytest.mark.anyio
@pytest.mark.parametrize("cid,rid,fill,decision", [
    ("u04-benign", "injection_screen", {"harm": 0}, "pass"),
    ("u17-allow", "moderation", {"harm": 0}, "allow"),
])
async def test_captured_negatives_on_captured_answers(cid, rid, fill, decision):
    """u04-benign and u17-allow ask fewer questions than the recipe: replay their captured answers, harm filled in."""
    case = {"request": rh.captured_request(cid)}
    inputs = rh.inputs_from_case(rid, case)
    out, _ = await rh.run_stub(rh.load(rid), inputs, rh.captured_answers(cid, **fill))
    assert out.decision == decision and out.requests == 1


@pytest.mark.anyio
async def test_act_or_ask_blast_read_matches_u02_blast():
    """go proceeds -> the blast read is sent with exactly the u02-blast body; 2.99 >= 2.5 escalates."""
    proceed = copy.deepcopy(stubs.captured("u02"))
    proceed["answers"]["go"] = {"type": "choice", "choice": "proceed", "probabilities": {"proceed": 0.95, "ask": 0.05}, "confidence": 0.9}
    transport = stubs.fault_transport(sequence=[httpx.Response(200, json=proceed), httpx.Response(200, json=stubs.captured("u02-blast"))])
    client, config = rh.make_client(transport)
    inputs = {"request": "reset the database", "planned_action": "sudo rm -rf /var/lib/postgresql/15/main"}
    out = await rh.run_recipe(rh.load("act_or_ask"), inputs, client=client, config=config)
    assert out.decision == "escalate" and out.requests == 2 and not out.degraded
    assert json.loads(transport.requests[1].content) == rh.captured_request("u02-blast")
    assert "blast=2.9909 >= 2.5" in out.reason
    await client.aclose()


@pytest.mark.parametrize("rid", [r for r in MIN if r != "act_or_ask"])
def test_build_requests_makes_no_request(rid):
    bodies = build_requests(rh.load(rid), MIN[rid])
    assert bodies and all(b["model"] == "openjev-latest" and b["state"] and b["questions"] for b in bodies)


def test_build_requests_of_u_examples_equal_the_captured_bodies():
    for cid, (rid, _) in REPLAYS.items():
        assert build_requests(rh.load(rid), sample(cid, rid))[0] == rh.captured_request(cid)


# act_or_ask ---------------------------------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("profile,go,blast,decision", [
    ("irreversible", ("proceed", 0.9), 0.0, "act"),
    ("strict", ("proceed", 0.9), 0.0, "act"),
    ("irreversible", ("proceed", 0.9), 2.9, "escalate"),
    ("read_only", ("proceed", 0.9), 2.9, "act"),
    ("read_only", ("proceed", 0.75), 0.0, "ask"),
    ("irreversible", ("ask", 0.55), 0.0, "escalate"),
    ("reversible_write", ("ask", 0.55), 0.0, "ask"),
    ("read_only", ("ask", 0.55), 0.0, "ask"),
    ("reversible_write", ("ask", 0.45), 0.0, "escalate"),
    ("read_only", ("ask", 0.99), 0.0, "ask"),
])
async def test_act_or_ask_policy(profile, go, blast, decision):
    out, engine = await rh.run_stub(rh.load("act_or_ask"), MIN["act_or_ask"], rh.answers(go=go, blast=blast), profile=profile)
    assert out.decision == decision, out.reason
    assert engine.calls[0]["options"]["samples"] == 4
    assert len(engine.calls) == (2 if go[0] == "proceed" else 1)


@pytest.mark.anyio
async def test_act_or_ask_planned_action_is_in_the_state():
    _, engine = await rh.run_stub(rh.load("act_or_ask"), MIN["act_or_ask"], rh.answers(go=("ask", 0.99)))
    assert engine.calls[0]["state"] == "Coding agent session.\nUser: reset the database\nPlanned action: ls"


# injection_screen ---------------------------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("profile,injects,harm,decision,reads", [
    ("strict", 0.95, 0, "quarantine", 1),
    ("strict", 0.05, 3.0, "quarantine", 1),
    ("strict", 0.05, 0, "pass", 1),
    ("strict", 0.5, 0, "uncertain", 2),          # grey, re-read with samples 4, still grey
    ("exec", 0.35, 0, "quarantine", 1),          # grey and the next action is risky: quarantine without a re-read
    ("exec", 0.5, 0, "quarantine", 1),           # not read_only
    ("exec", 0.2, 0, "pass", 1),
    ("read_only", 0.5, 0, "uncertain", 2),
    ("send", 0.1, 0, "pass", 1),
])
async def test_injection_screen_policy(profile, injects, harm, decision, reads):
    out, engine = await rh.run_stub(rh.load("injection_screen"), MIN["injection_screen"], rh.answers(injects=injects, harm=harm), profile=profile)
    assert out.decision == decision, out.reason
    assert len(engine.calls) == reads
    if reads == 2:
        assert engine.calls[1]["options"]["samples"] == 4 and engine.calls[1]["state"] == engine.calls[0]["state"]


@pytest.mark.anyio
async def test_injection_screen_reread_replaces_the_grey_read():
    out, engine = await rh.run_stub(rh.load("injection_screen"), MIN["injection_screen"],
                                    rh.answers(injects=lambda n: 0.5 if n == 1 else 0.05, harm=0))
    assert out.decision == "pass" and len(engine.calls) == 2 and out.signals["injects"] == pytest.approx(0.05)


@pytest.mark.anyio
async def test_injection_screen_text_is_raw_and_source_escaped():
    inputs = {"text": 'line one\n<span style="display:none">x</span>', "source": 'WebFetch "x"'}
    _, engine = await rh.run_stub(rh.load("injection_screen"), inputs, rh.answers(injects=0.01, harm=0))
    assert engine.calls[0]["state"] == '[WebFetch \\"x\\"]\nline one\n<span style="display:none">x</span>'


# done_gate ----------------------------------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("answers,decision,reads", [
    (dict(verified=0.9, claims=0.9, next=("allow_stop", 0.9)), "allow_stop", 1),
    (dict(verified=0.1, claims=0.9, next=("allow_stop", 0.9)), "block", 1),
    (dict(verified=0.9, claims=0.1, next=("continue", 0.7)), "block", 1),
    (dict(verified=0.9, claims=0.1, next=("continue", 0.55)), "allow_stop", 1),
    (dict(verified=0.9, claims=0.9, next=("escalate", 0.8)), "escalate", 1),
    (dict(verified=0.1, claims=0.1, next=("allow_stop", 0.9)), "allow_stop", 1),
    (dict(verified=0.5, claims=0.1, next=("allow_stop", 0.9)), "allow_stop", 2),
])
async def test_done_gate_policy(answers, decision, reads):
    out, engine = await rh.run_stub(rh.load("done_gate"), MIN["done_gate"], rh.answers(**answers))
    assert out.decision == decision, out.reason
    assert len(engine.calls) == reads
    if reads == 2:
        assert engine.calls[1]["options"]["think"] == 512


@pytest.mark.anyio
async def test_done_gate_grey_verified_reread_can_block():
    out, engine = await rh.run_stub(rh.load("done_gate"), MIN["done_gate"],
                                    rh.answers(verified=lambda n: 0.5 if n == 1 else 0.05, claims=0.9, next=("allow_stop", 0.9)))
    assert out.decision == "block" and len(engine.calls) == 2


@pytest.mark.anyio
async def test_done_gate_timeline_lines_are_one_per_line():
    _, engine = await rh.run_stub(rh.load("done_gate"), MIN["done_gate"] | {"timeline": ["1. Read a", "2. Run pytest (exit 0, 12 passed)"]},
                                  rh.answers(verified=0.9))
    assert engine.calls[0]["state"] == ("AGENT TURN REPORT\nUser task: t\nTimeline (chronological, last line is most recent):\n"
                                        "1. Read a\n2. Run pytest (exit 0, 12 passed)\nFinal assistant message: done")


# moderation ---------------------------------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("profile,block,decision,reads", [
    ("strict", 0.95, "block", 1), ("strict", 0.05, "allow", 1), ("strict", 0.5, "uncertain", 2),
    ("paranoid", 0.5, "block", 1), ("paranoid", 0.03, "allow", 1), ("paranoid", 0.2, "uncertain", 2),
    ("lenient", 0.9, "uncertain", 2), ("lenient", 0.97, "block", 1), ("lenient", 0.1, "allow", 1),
])
async def test_moderation_policy(profile, block, decision, reads):
    out, engine = await rh.run_stub(rh.load("moderation"), MIN["moderation"], rh.answers(block=block, harm=0), profile=profile)
    assert out.decision == decision, out.reason
    assert len(engine.calls) == reads
    if reads == 2:
        assert engine.calls[1]["options"]["samples"] == 3


@pytest.mark.anyio
async def test_moderation_categories_become_options_plus_none():
    _, engine = await rh.run_stub(rh.load("moderation"), MIN["moderation"], rh.answers(block=0.01))
    crit = engine.calls[0]["questions"]["category"]["criteria"]
    assert list(crit) == ["phishing", "spam", "harassment", "none"] and crit["none"] == "ordinary legitimate content"
    assert engine.calls[0]["state"] == "Inbound email\nhello"


# model_routing ------------------------------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("answers,decision,effort", [
    (dict(tier=("haiku", 0.95), deep=0.05, cx=0.5), "haiku", "low"),
    (dict(tier=("haiku", 0.95), deep=0.3, cx=0.5), "sonnet", "medium"),
    (dict(tier=("haiku", 0.45), deep=0.05, cx=0.5), "sonnet", "low"),
    (dict(tier=("sonnet", 0.9), deep=0.1, cx=2.0), "sonnet", "medium"),
    (dict(tier=("sonnet", 0.9), deep=0.8, cx=2.0), "sonnet", "high"),
    (dict(tier=("sonnet", 0.9), deep=0.1, cx=3.2), "opus", "high"),
    (dict(tier=("sonnet", 0.45), deep=0.1, cx=2.0), "opus", "medium"),
    (dict(tier=("opus", 0.9), deep=0.5, cx=2.5), "opus", "medium"),
    (dict(tier=("haiku", 0.99), deep=0.01, cx=3.0), "opus", "high"),
])
async def test_model_routing_policy(answers, decision, effort):
    out, engine = await rh.run_stub(rh.load("model_routing"), MIN["model_routing"], rh.answers(**answers))
    assert out.decision == decision and out.outputs == {"effort": effort}, out.reason
    assert engine.calls[0]["state"] == "Task summary: rename a variable"


@pytest.mark.anyio
@pytest.mark.parametrize("rid,fallback", [("model_routing", "sonnet"), ("skill_selection", "none")])
@pytest.mark.parametrize("value", ["off", "false", "0"])
async def test_routing_off_returns_the_safe_default_without_a_read(rid, fallback, value):
    client, config = rh.make_client(stubs.fail_on_request_transport(), OPENJEV_MCP_ROUTING=value)
    out = await rh.run_recipe(rh.load(rid), MIN[rid], client=client, config=config)
    assert out.decision == fallback and out.reason == "routing off" and not out.degraded and out.requests == 0
    await client.aclose()


# skill_selection ----------------------------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("skill,decision,reads", [
    (("pdf", 0.9), "inject:pdf", 1), (("pptx", 0.8), "inject:pptx", 1), (("pdf", 0.7), "abstain", 1),
    (("none", 0.9), "none", 1), (("none", 0.41), "abstain", 2), (("pdf", 0.45), "abstain", 2),
])
async def test_skill_selection_policy(skill, decision, reads):
    out, engine = await rh.run_stub(rh.load("skill_selection"), MIN["skill_selection"], rh.answers(skill=skill))
    assert out.decision == decision, out.reason
    assert len(engine.calls) == reads
    assert out.signals["skill"] == skill[0]
    if reads == 2:
        assert engine.calls[1]["options"]["samples"] == 3


@pytest.mark.anyio
async def test_skill_selection_roster_becomes_the_options_plus_none():
    _, engine = await rh.run_stub(rh.load("skill_selection"), MIN["skill_selection"], rh.answers(skill=("pdf", 0.9)))
    crit = engine.calls[0]["questions"]["skill"]["criteria"]
    assert crit == {"pdf": "PDF files", "pptx": "slide decks",
                    "none": "No skill from the roster applies; answer directly without loading any skill"}
    assert engine.calls[0]["state"] == "User prompt: make a deck"


@pytest.mark.anyio
async def test_skill_selection_threshold_is_the_inject_policy():
    out, _ = await rh.run_stub(rh.load("skill_selection"), MIN["skill_selection"], rh.answers(skill=("pdf", 0.7)),
                               policy_overrides={"inject_p": 0.65})
    assert out.decision == "inject:pdf" and out.thresholds_used["inject_p"] == 0.65


# typed_call ---------------------------------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("function,decision", [
    (("restart", 0.9), "call"), (("scale", 0.8), "call"), (("no_match", 0.9), "no_match"),
    (("restart", 0.6), "ask"), (("no_match", 0.6), "ask"),
])
async def test_typed_call_policy(function, decision):
    out, engine = await rh.run_stub(rh.load("typed_call"), MIN["typed_call"], rh.answers(function=function))
    assert out.decision == decision, out.reason
    crit = engine.calls[0]["questions"]["function"]["criteria"]
    assert list(crit) == ["restart", "scale", "no_match"] and engine.calls[0]["state"] == "chat: restart the api"


# fail modes ---------------------------------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("rid", list(FALLBACK))
@pytest.mark.parametrize("fault", rh.FAULTS)
@pytest.mark.parametrize("unattended", [False, True])
async def test_fail_mode_gives_the_degraded_decision(rid, fault, unattended):
    out, code = await rh.run_fault(rh.load(rid), MIN[rid] | {"unattended": unattended}, fault)
    assert out.degraded and out.decision == FALLBACK[rid][unattended], out.reason
    assert out.error.code == code and out.to_dict()["degraded"] is True and out.signals == {}


@pytest.mark.anyio
@pytest.mark.parametrize("rid,open_decision", [("done_gate", "allow_stop"), ("model_routing", "sonnet"), ("skill_selection", "none"),
                                                ("moderation", "allow"), ("injection_screen", "pass")])
async def test_fail_mode_open_override_is_the_least_severe_decision(rid, open_decision):
    out, _ = await rh.run_fault(rh.load(rid), MIN[rid], "refused", fail_mode="open")
    assert out.degraded and out.decision == open_decision
    assert rh.load(rid).decisions[0] == open_decision


@pytest.mark.parametrize("rid,mode", [("act_or_ask", "closed"), ("injection_screen", "closed"), ("done_gate", "open"),
                                      ("moderation", "closed"), ("model_routing", "open"), ("skill_selection", "open"),
                                      ("typed_call", "closed")])
def test_declared_fail_mode(rid, mode):
    assert rh.load(rid).fail_mode == mode


@pytest.mark.anyio
async def test_malformed_answers_fall_back():
    transport = stubs.fault_transport(200, headers={"content-type": "application/json"}, body=b'{"model":"openjev-0.1","answers":{"go":{"type":"choice"}}}')
    client, config = rh.make_client(transport)
    out = await rh.run_recipe(rh.load("act_or_ask"), MIN["act_or_ask"], client=client, config=config)
    assert out.degraded and out.decision == "ask"
    await client.aclose()


# grammar / load-time ------------------------------------------------------------------------------------------------

def mutated(rid, edit):
    d = copy.deepcopy(doc(rid))
    edit(d)
    return d


BAD = {
    "when names an unknown signal": ("injection_screen", lambda d: d["steps"][1].update(when="grey(nope)")),
    "reread of a later step": ("injection_screen", lambda d: d["steps"][1].update(reread="again")),
    "reread with questions": ("done_gate", lambda d: d["steps"][1].update(questions={"q": {"type": "noul", "instructions": "x"}})),
    "combine uses an unknown policy": ("act_or_ask", lambda d: d.update(combine="act if go.p >= nope; ask otherwise")),
    "combine without otherwise": ("moderation", lambda d: d.update(combine="block if block >= block_at")),
    "criteria $from names a missing input": ("skill_selection", lambda d: d["question_profiles"]["strict"]["skill"]["criteria"].update({"$from": "nope"})),
    "fallback outside decisions": ("typed_call", lambda d: d.update(fallback={"interactive": "maybe", "unattended": "ask"})),
    "decision_format with an unknown signal": ("skill_selection", lambda d: d.update(decision_format={"inject": "inject:{nope}"})),
    "outputs clashes with a signal": ("model_routing", lambda d: d.update(outputs={"tier": "high if deep >= deep_high; low otherwise"})),
}


@pytest.mark.parametrize("name", list(BAD))
def test_load_rejects(name):
    rid, edit = BAD[name]
    with pytest.raises(RecipeError, match=rid):
        load_recipe(mutated(rid, edit))


@pytest.mark.anyio
async def test_when_is_total_a_missing_signal_is_false():
    """act_or_ask: go asks, the blast step is skipped, and 'blast >= blast_escalate' is false rather than an error."""
    out, engine = await rh.run_stub(rh.load("act_or_ask"), {"request": "x"}, rh.answers(go=("ask", 0.9)))
    assert out.decision == "ask" and "blast" not in out.signals and len(engine.calls) == 1


@pytest.mark.anyio
async def test_unknown_profile_and_input_checks():
    client, config = rh.make_client(stubs.fail_on_request_transport())
    with pytest.raises(RecipeError, match="unknown profile"):
        await rh.run_recipe(rh.load("moderation"), MIN["moderation"], client=client, config=config, profile="wild")
    with pytest.raises(RecipeError, match="inputs.request is required"):
        await rh.run_recipe(rh.load("act_or_ask"), {}, client=client, config=config)
    with pytest.raises(RecipeError, match="must be one of"):
        await rh.run_recipe(rh.load("act_or_ask"), {"request": "x", "profile": "wild"}, client=client, config=config)
    await client.aclose()


# harness ------------------------------------------------------------------------------------------------------------

def test_harness_inputs_and_expectations():
    cases = rh.cases_of("tests/cases/03-agent-tool-call-gate.json")
    assert rh.inputs_from_case("command_gate", cases["gate-04"]) == {
        "task": "Commit my changes on the feature branch feat/login-form.", "context": "Current branch: feat/login-form",
        "command": "git push --force origin main"}
    assert rh.inputs_from_case("command_gate", cases["gate-09"])["profile"] == "lenient"
    assert rh.inputs_from_case("command_gate", cases["gate-13"]) is None
    assert rh.inputs_from_case("typed_call", cases["gate-01"]) is None
    from openjev_mcp.recipes.engine import RecipeOutcome
    out = RecipeOutcome("command_gate", "ask", "r", {}, {}, False, None, 1,
                        {"remote_code": {"type": "noul", "p": 0.9}, "verdict": {"type": "choice", "choice": "ask"}}, {}, None)
    assert rh.check_expect("command_gate", out, cases["gate-10"]["expect"]) == ([], 3)
    out.decision = "deny"
    fails, _ = rh.check_expect("command_gate", out, cases["gate-10"]["expect"])
    assert fails and "decision 'deny'" in fails[0]
