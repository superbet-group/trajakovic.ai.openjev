"""Recipe engine generalisation (phase 2/3): per-step questions, reread, read_per_item, read_twice_swapped, criteria
$from, decision_format, outputs, weighted_sum, images, routing off, build_requests, fail modes. Synthetic recipes."""
from __future__ import annotations

import copy

import httpx
import pytest
import stubs

from openjev_mcp.config import load_config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.recipes.engine import RecipeError, build_requests, load_recipe, run_recipe

IMG = "data:image/png;base64,iVBORw0KGgo="


def noul(p):
    return {"type": "noul", "noul": p}


def choice(key, keys, p=0.9):
    return {"type": "choice", "choice": key,
            "probabilities": {k: (p if k == key else (1 - p) / (len(keys) - 1)) for k in keys}, "confidence": p}


def score(v, n=4):
    probs = {str(i): (0.9 if i == round(v) else 0.1 / (n - 1)) for i in range(n)}
    return {"type": "score", "score": v, "legend": {str(i): str(i) for i in range(n)}, "probabilities": probs,
            "confidence": 0.9}


def scripted(fn=None):
    """Transport answering every read; fn(body, n) -> {qid: answer} overrides, rest is default_answers."""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content)
        transport.bodies.append(body)
        answers = stubs.default_answers(body["questions"], body["state"], {})
        answers.update((fn(body, state["n"]) if fn else None) or {})
        state["n"] += 1
        return httpx.Response(200, json={"model": "openjev-0.1", "answers": answers,
                                         "usage": {"input_tokens": 10, "output_tokens": 0}})

    transport = stubs.RecordingTransport(handler)
    transport.bodies = []
    return transport


def make(transport, **env):
    config = load_config({"OPENJEV_MCP_RETRIES": "0", **env})
    return OpenJevClient(config, transport=transport), config


def recipe_doc(**over):
    doc = {
        "id": "synth_one", "title": "t", "usage_type": "x", "description": "d",
        "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
        "steps": [{"id": "read", "kind": "read", "state_template": "TASK {{task}}",
                   "questions": {"bad": {"type": "noul", "instructions": "Is it bad?"}}}],
        "policy": {"hi": 0.8}, "decisions": ["ok", "no"],
        "combine": "no if bad >= hi; ok otherwise", "fail_mode": "closed",
        "fallback": {"interactive": "no", "unattended": "no"},
    }
    doc.update(over)
    return doc


async def run(doc, inputs, fn=None, env=None, **kw):
    transport = scripted(fn)
    client, config = make(transport, **(env or {}))
    try:
        out = await run_recipe(load_recipe(doc), inputs, client=client, config=config, **kw)
    finally:
        await client.aclose()
    return out, transport


def bad(doc, match):
    with pytest.raises(RecipeError, match=match):
        load_recipe(doc)


def with_steps(*steps, **over):
    d = recipe_doc(**over)
    d["steps"] = list(steps)
    return d


READ = {"id": "read", "kind": "read", "state_template": "T {{task}}",
        "questions": {"bad": {"type": "noul", "instructions": "Is it bad?"}}}


# ---- per-step questions, accumulation, reread ----

def two_step_doc(**over):
    s2 = {"id": "more", "kind": "read", "when": "grey(bad)", "state_template": "T2 {{task}}",
          "questions": {"worse": {"type": "noul", "instructions": "Worse?"}}}
    return with_steps(READ, s2, decisions=["ok", "ask", "no"],
                      combine="no if worse >= hi; ask if grey(bad) and worse < 0.5; ok otherwise",
                      fallback={"interactive": "ask", "unattended": "no"}, **over)


@pytest.mark.anyio
async def test_step_questions_accumulate_and_when_sees_earlier_signals():
    out, tr = await run(two_step_doc(), {"task": "x"}, lambda b, n: {"bad": noul(0.5)} if "bad" in b["questions"] else {"worse": noul(0.9)})
    assert [list(b["questions"]) for b in tr.bodies] == [["bad"], ["worse"]]
    assert out.decision == "no" and out.signals["bad"] == 0.5 and out.signals["worse"] == 0.9
    assert out.requests == 2 and not out.degraded


@pytest.mark.anyio
async def test_when_is_total_missing_signal_is_false():
    # step 2 never runs (bad not grey) so `worse` is missing: comparisons on it are false, never an error
    out, tr = await run(two_step_doc(), {"task": "x"}, lambda b, n: {"bad": noul(0.01)})
    assert len(tr.bodies) == 1 and out.decision == "ok" and "worse" not in out.signals


def test_step_questions_override_profiles():
    doc = recipe_doc(question_profiles={"strict": {"bad": {"type": "noul", "instructions": "profile"}}})
    doc["steps"][0]["questions"] = {"bad": {"type": "noul", "instructions": "step"}}
    assert build_requests(load_recipe(doc), {"task": "x"})[0]["questions"]["bad"]["instructions"] == "step"


@pytest.mark.anyio
async def test_reread_replaces_grey_signal_and_decision():
    reread = {"id": "again", "kind": "read", "reread": "read", "when": "grey(bad)", "options": {"samples": 3}}
    doc = with_steps(READ, reread)

    def fn(b, n):
        return {"bad": noul(0.5 if n == 0 else 0.01)}
    out, tr = await run(doc, {"task": "x"}, fn)
    assert [b.get("samples") for b in tr.bodies] == [None, 3]
    assert tr.bodies[0]["questions"] == tr.bodies[1]["questions"] and tr.bodies[0]["state"] == tr.bodies[1]["state"]
    assert out.decision == "ok" and out.signals["bad"] == 0.01     # grey 0.5 would otherwise be 'ok' too; replaced value shows


def test_reread_load_errors():
    bad(with_steps(READ, {"id": "a", "kind": "read", "reread": "nope", "options": {"samples": 3}}), "reread must name")
    bad(with_steps(READ, {"id": "a", "kind": "read", "reread": "read"}), "takes options")
    bad(with_steps({"id": "a", "kind": "read", "reread": "b", "options": {"samples": 3}}, READ), "reread must name")
    bad(with_steps(READ, {"id": "a", "kind": "read", "reread": "read", "options": {"samples": 3}, "questions": {}}), "takes options")


def test_option_and_signal_map_errors():
    bad(with_steps({**READ, "options": {"bogus": 1}}), "unknown options")
    bad(with_steps({**READ, "options": []}), "options must be an object")
    bad(with_steps({**READ, "signal_map": {"bad": 3}}), "signal_map")
    bad(with_steps({**READ, "state_template": "{{{task}}}"}), "not allowed")


# ---- read_per_item ----

def item_doc(**step_over):
    step = {"id": "each", "kind": "read_per_item", "items": "claims", "state_template": "CLAIM {{text}} / {{task}} #{{index}}",
            "questions": {"bad": {"type": "noul", "instructions": "Is {{task}} bad?"}}, **step_over}
    d = with_steps(step, decisions=["ok", "review", "no"], combine="no if bad >= hi; review if bad >= 0.3; ok otherwise",
                   fallback={"interactive": "review", "unattended": "no"})
    d["input_schema"] = {"type": "object", "properties": {"task": {"type": "string"}, "claims": {"type": "array"}},
                         "required": ["task", "claims"]}
    return d


ITEMS = [{"id": "c1", "text": "alpha"}, {"id": "c2", "text": "beta"}, {"text": "gamma"}]
PER = {"alpha": 0.01, "beta": 0.5, "gamma": 0.9}


def per_fn(b, n):
    text = b["state"].split(" ")[1]
    return {"bad": noul(PER[text])}


@pytest.mark.anyio
async def test_read_per_item_items_and_worst_aggregate():
    out, tr = await run(item_doc(), {"task": "T", "claims": ITEMS}, per_fn)
    assert [i["id"] for i in out.items] == ["c1", "c2", "2"]
    assert [i["decision"] for i in out.items] == ["ok", "review", "no"]
    assert out.items[1]["signals"] == {"bad": 0.5}
    assert out.decision == "no" and out.requests == 3 and out.to_dict()["items"] == out.items
    assert tr.bodies[0]["state"] == "CLAIM alpha / T #0" and tr.bodies[0]["questions"]["bad"]["instructions"] == "Is T bad?"


@pytest.mark.anyio
async def test_read_per_item_aggregate_modes():
    out, _ = await run(item_doc(aggregate="any:review"), {"task": "T", "claims": ITEMS[:2]}, per_fn)
    assert out.decision == "review"
    out, _ = await run(item_doc(aggregate="any:no"), {"task": "T", "claims": ITEMS[:2]}, per_fn)
    assert out.decision == "ok"                     # none decided 'no': least severe item decision
    out, _ = await run(item_doc(aggregate="none"), {"task": "T", "claims": ITEMS}, per_fn)
    assert out.decision == "ok" and len(out.items) == 3
    out, tr = await run(item_doc(), {"task": "T", "claims": []})
    assert out.items == [] and out.requests == 0 and out.decision == "ok"


@pytest.mark.anyio
async def test_read_per_item_bad_items_degrade():
    out, _ = await run(item_doc(), {"task": "T", "claims": ["not an object"]})
    assert out.degraded and out.error.code == "OJ_INVALID_INPUT" or out.items is not None
    out, _ = await run(item_doc(), {"task": "T", "claims": [{"id": "x"}] * 101})
    assert out.degraded and "limit is 100" in out.error.message and out.decision == "review"


def test_read_per_item_load_errors():
    bad(item_doc(items="nope"), "must name an array property")
    bad(item_doc(aggregate="any:zzz"), "aggregate must be")
    bad(item_doc(per=True), "per is not allowed")
    bad(item_doc(state_template=None), "state_template")


# ---- read_twice_swapped ----

def swap_doc():
    step = {"id": "pair", "kind": "read_twice_swapped", "swap": ["a", "b"], "state_template": "A {{a}} B {{b}}",
            "questions": {"winner": {"type": "choice", "instructions": "Which is better?", "criteria": {"A": "first", "B": "second"}}}}
    d = with_steps(step, decisions=["tie", "A", "B"],
                   combine="A if winner == 'A' and winner_swapped == 'B'; B if winner == 'B' and winner_swapped == 'A'; tie otherwise")
    d["input_schema"] = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]}
    d["fallback"] = {"interactive": "tie", "unattended": "tie"}
    return d


@pytest.mark.anyio
async def test_read_twice_swapped_agree_and_disagree():
    ab = ["A", "B"]
    out, tr = await run(swap_doc(), {"a": "one", "b": "two"}, lambda b, n: {"winner": choice("A" if n == 0 else "B", ab)})
    assert [b["state"] for b in tr.bodies] == ["A one B two", "A two B one"]
    assert out.decision == "A" and out.signals["winner"] == "A" and out.signals["winner_swapped"] == "B"
    out, _ = await run(swap_doc(), {"a": "one", "b": "two"}, lambda b, n: {"winner": choice("A", ab)})
    assert out.decision == "tie" and out.requests == 2


def test_read_twice_swapped_load_errors():
    for sw in (None, ["a"], ["a", "a"], ["a", "zz"]):
        d = swap_doc()
        d["steps"][0]["swap"] = sw
        bad(d, "swap must name two")
    d = swap_doc()
    d["combine"] = "A if winner_swapped2 == 'B'; tie otherwise"
    bad(d, "winner_swapped2")


# ---- criteria $from ----

def criteria_doc(**crit):
    c = {"$from": "roster", "key": "id", "text": "desc", "extra": {"none": "no skill applies"}, **crit}
    d = with_steps({"id": "pick", "kind": "read", "state_template": "{{task}}",
                    "questions": {"skill": {"type": "choice", "instructions": "Pick", "criteria": c}}},
                   decisions=["none", "inject"], combine="inject if skill != 'none' and skill.p >= hi; none otherwise",
                   decision_format={"inject": "inject:{skill}"})
    d["input_schema"] = {"type": "object", "properties": {"task": {"type": "string"}, "roster": {"type": "array"}}, "required": ["task"]}
    d["fallback"] = {"interactive": "none", "unattended": "none"}
    return d


ROSTER = [{"id": "pdf", "desc": "make pdfs"}, {"id": "xlsx", "desc": "spreadsheets"}]


@pytest.mark.anyio
async def test_criteria_from_inputs_and_decision_format():
    out, tr = await run(criteria_doc(), {"task": "t", "roster": ROSTER}, lambda b, n: {"skill": choice("xlsx", list(b["questions"]["skill"]["criteria"]))})
    assert tr.bodies[0]["questions"]["skill"]["criteria"] == {"pdf": "make pdfs", "xlsx": "spreadsheets", "none": "no skill applies"}
    assert out.decision == "inject:xlsx" and out.signals["skill"] == "xlsx"
    out, _ = await run(criteria_doc(), {"task": "t", "roster": ROSTER}, lambda b, n: {"skill": choice("none", list(b["questions"]["skill"]["criteria"]))})
    assert out.decision == "none"                     # not in decision_format: plain decision


@pytest.mark.anyio
async def test_criteria_from_bad_inputs_degrade():
    for roster, msg in (([{"id": "pdf", "desc": "x"}, {"id": "pdf", "desc": "y"}], "unique option key"),
                        ([{"id": "pdf"}], "non-empty string"), ([], "at least 2"),
                        ([{"id": "a\nb", "desc": "x"}], "unique option key"), ([{"id": "none", "desc": "x"}], "clashes")):
        out, tr = await run(criteria_doc(), {"task": "t", "roster": roster})
        assert out.degraded and msg in out.error.message and out.decision == "none" and tr.bodies == [], roster


def test_criteria_from_load_errors():
    bad(criteria_doc(**{"$from": "task"}), "array property")
    bad(criteria_doc(key=3), "string key and text")
    bad(criteria_doc(extra={"": "x"}), "extra")
    bad(criteria_doc(extra={"k": ""}), "extra")
    bad(criteria_doc(bogus=1), "string key and text")
    d = criteria_doc()
    d["steps"][0]["questions"]["skill"]["type"] = "score"
    bad(d, "choice questions")
    bad(recipe_doc(decision_format={"nope": "x"}), "decision_format")
    bad(criteria_doc() | {"decision_format": {"inject": "inject:{ghost}"}}, "not a signal")


# ---- outputs ----

@pytest.mark.anyio
async def test_outputs_secondary_combines():
    d = recipe_doc(outputs={"effort": {"values": ["low", "high"], "combine": "high if bad >= 0.5; low otherwise"},
                            "same": "no if bad >= hi; ok otherwise"})
    out, _ = await run(d, {"task": "x"}, lambda b, n: {"bad": noul(0.6)})
    assert out.outputs == {"effort": "high", "same": "ok"} and out.signals["effort"] == "high"
    assert out.to_dict()["outputs"]["effort"] == "high"
    bad(recipe_doc(outputs={"bad": "ok otherwise"}), "fresh lowercase name")
    bad(recipe_doc(outputs={"e": {"values": ["x"], "combine": "y otherwise"}}), "unknown decision")
    bad(recipe_doc(outputs={"e": {"values": [], "combine": "x otherwise"}}), "values")


# ---- weighted_sum ----

def rubric_doc():
    d = with_steps(
        {"id": "dims", "kind": "read", "state_template": "{{text}}",
         "questions": {"$each": "dimensions", "id": "name", "type": "score", "instructions": "instructions", "criteria": "levels"}},
        {"id": "total", "kind": "compute", "compute": "weighted_sum", "dimensions": "dimensions", "into": "composite"},
        decisions=["reject", "review", "shortlist"], combine="shortlist if composite >= 0.6 and composite_floors == 1; "
        "review if composite >= 0.3; reject otherwise", fallback={"interactive": "review", "unattended": "reject"})
    d["input_schema"] = {"type": "object", "properties": {"text": {"type": "string"}, "dimensions": {"type": "array"}}, "required": ["text", "dimensions"]}
    return d


def dims(w1=0.7, w2=0.3, floor=None):
    lv = ["a", "b", "c", "d"]
    return [{"name": "python", "instructions": "py?", "levels": lv, "weight": w1},
            {"name": "lead", "instructions": "lead?", "levels": lv, "weight": w2, **({"floor": floor} if floor else {})}]


@pytest.mark.anyio
async def test_weighted_sum_composite_floors_and_weights_not_sent():
    fn = lambda b, n: {"python": score(3.0), "lead": score(0.0)}      # noqa: E731
    out, tr = await run(rubric_doc(), {"text": "cv", "dimensions": dims()}, fn)
    assert out.signals["composite"] == pytest.approx(0.7) and out.signals["composite_floors"] == 1.0
    assert out.decision == "shortlist" and out.requests == 1       # decided once, after the compute
    assert list(tr.bodies[0]["questions"]) == ["python", "lead"] and "weight" not in str(tr.bodies[0])
    out, _ = await run(rubric_doc(), {"text": "cv", "dimensions": dims(0.3, 0.7)}, fn)
    assert out.signals["composite"] == pytest.approx(0.3) and out.decision == "review"      # ranking flips by weights alone
    out, _ = await run(rubric_doc(), {"text": "cv", "dimensions": dims(floor=2)}, fn)
    assert out.signals["composite_floors"] == 0.0 and out.decision == "review"   # lead 0 < floor 2 - 0.3


@pytest.mark.anyio
async def test_weighted_sum_bad_inputs_degrade():
    out, _ = await run(rubric_doc(), {"text": "cv", "dimensions": dims(0, 0)})
    assert out.degraded and "weights must sum" in out.error.message and out.decision == "review"
    d = dims()
    d[0]["levels"] = ["only"]
    out, _ = await run(rubric_doc(), {"text": "cv", "dimensions": d})
    assert out.degraded and "'levels'" in out.error.message


def test_compute_load_errors():
    for over, msg in (({"dimensions": "task"}, "array property"), ({"into": "Bad Name"}, "into must"), ({"margin": -1}, "margin"),
                      ({"compute": "eval"}, "unknown compute")):
        d = rubric_doc()
        d["steps"][1].update(over)
        bad(d, msg)
    d = rubric_doc()
    d["steps"][1].pop("compute")
    bad(d, "only compute step")
    bad(with_steps({"id": "dims", "kind": "read", "state_template": "x",
                    "questions": {"$each": "nope", "id": "n", "type": "score", "instructions": "i", "criteria": "c"}}), "array property")


# ---- rules stay final ----

@pytest.mark.anyio
async def test_a_rules_deny_is_final_even_with_new_steps():
    d = item_doc()
    d["decisions"] = ["ok", "review", "no", "deny"]
    d["steps"].insert(0, {"id": "rules", "kind": "deterministic", "rules": [{"match": "^rm ", "decision": "deny", "scope": "command"}]})
    d["input_schema"]["properties"]["command"] = {"type": "string"}
    d["input_schema"]["required"] = ["task", "claims"]
    transport = stubs.fail_on_request_transport()
    client, config = make(transport)
    out = await run_recipe(load_recipe(d), {"task": "T", "claims": ITEMS, "command": "rm x"}, client=client, config=config)
    assert out.decision == "deny" and out.requests == 0 and out.rule["decision"] == "deny" and transport.requests == []
    assert build_requests(load_recipe(d), {"task": "T", "claims": ITEMS, "command": "rm x"}) == []
    await client.aclose()


# ---- images ----

def image_doc(**opts):
    d = recipe_doc()
    d["input_schema"]["properties"]["shots"] = {"type": "array", "items": {"type": "string"}, "x-openjev-images": True}
    d["steps"][0]["options"] = opts
    return d


@pytest.mark.anyio
async def test_images_are_sent_and_think_sequential_refused():
    out, tr = await run(image_doc(), {"task": "x", "shots": [IMG]})
    assert tr.bodies[0]["images"] == [IMG] and not out.degraded
    assert list(tr.bodies[0])[-1] == "images"
    out, tr = await run(image_doc(), {"task": "x"})
    assert "images" not in tr.bodies[0]
    for opt in ({"think": 64}, {"sequential": True}):
        with pytest.raises(ToolError, match="E022"):
            await run(image_doc(), {"task": "x", "shots": [IMG]}, read_options=opt)
        bad(image_doc(**opt), "E022")
    assert (await run(image_doc(), {"task": "x"}, read_options={"think": 64}))[0].requests == 1     # no images: allowed
    with pytest.raises(RecipeError, match="data URLs"):
        await run(image_doc(), {"task": "x", "shots": ["http://x"]})
    d = image_doc()
    d["input_schema"]["properties"]["shots"]["type"] = "string"
    bad(d, "needs type array")


# ---- routing off ----

@pytest.mark.anyio
async def test_routing_recipe_returns_fallback_without_a_read_when_off():
    d = recipe_doc(routing=True, fallback={"interactive": "ok", "unattended": "no"})
    transport = stubs.fail_on_request_transport()
    client, config = make(transport, OPENJEV_MCP_ROUTING="off")
    out = await run_recipe(load_recipe(d), {"task": "x", "unattended": True}, client=client, config=config)
    assert (out.decision, out.reason, out.degraded, out.requests) == ("ok", "routing off", False, 0)
    assert transport.requests == [] and build_requests(load_recipe(d), {"task": "x"}, config=config) == []
    await client.aclose()
    out, tr = await run(d, {"task": "x"})                  # routing on: reads normally
    assert out.requests == 1
    out, tr = await run(recipe_doc(), {"task": "x"}, env={"OPENJEV_MCP_ROUTING": "off"})
    assert out.requests == 1                               # not a routing recipe
    bad(recipe_doc(routing="yes"), "routing must")


# ---- build_requests ----

@pytest.mark.anyio
async def test_build_requests_makes_no_request_and_matches_run():
    d = two_step_doc()
    d["steps"][0]["options"] = {"samples": 2}
    recipe = load_recipe(d)
    transport = stubs.fail_on_request_transport()
    client, config = make(transport)
    bodies = build_requests(recipe, {"task": "x"}, config=config, policy={"hi": 0.5})
    assert transport.requests == []
    assert len(bodies) == 1 and bodies[0]["samples"] == 2 and list(bodies[0]["questions"]) == ["bad"]     # step 'more' is conditional
    await client.aclose()
    _, tr = await run(d, {"task": "x"}, lambda b, n: {"bad": noul(0.01)})
    assert tr.bodies == bodies
    assert build_requests(recipe, {"task": "x"})[0]["model"] == load_config({}).model
    assert len(build_requests(load_recipe(swap_doc()), {"a": "1", "b": "2"})) == 2
    assert len(build_requests(load_recipe(item_doc()), {"task": "T", "claims": ITEMS})) == 3
    with pytest.raises(RecipeError, match="unknown or invalid policy"):
        build_requests(recipe, {"task": "x"}, policy={"zz": 1})
    with pytest.raises(RecipeError, match="required"):
        build_requests(recipe, {})
    assert build_requests(load_recipe(image_doc()), {"task": "x", "shots": [IMG]})[0]["images"] == [IMG]


# ---- policy overrides, fail modes, degraded ----

@pytest.mark.anyio
async def test_policy_overrides_change_the_combine():
    fn = lambda b, n: {"bad": noul(0.6)}      # noqa: E731
    out, _ = await run(recipe_doc(), {"task": "x"}, fn)
    assert out.decision == "ok" and out.thresholds_used["hi"] == 0.8
    out, _ = await run(recipe_doc(), {"task": "x"}, fn, policy_overrides={"hi": 0.5})
    assert out.decision == "no" and out.thresholds_used["hi"] == 0.5
    with pytest.raises(RecipeError, match="policy overrides"):
        await run(recipe_doc(), {"task": "x"}, policy_overrides={"nope": 1})


@pytest.mark.anyio
@pytest.mark.parametrize("doc,inp", [("per", None), ("swap", None), ("rubric", None), ("plain", None)])
async def test_errors_degrade_to_the_fail_mode_decision(doc, inp):
    cases = {"per": (item_doc(), {"task": "T", "claims": ITEMS}), "swap": (swap_doc(), {"a": "1", "b": "2"}),
             "rubric": (rubric_doc(), {"text": "cv", "dimensions": dims()}), "plain": (recipe_doc(), {"task": "x"})}
    d, inputs = cases[doc]
    transport = stubs.fault_transport(503, body=b"{}")
    client, config = make(transport)
    recipe = load_recipe(d)
    closed = await run_recipe(recipe, inputs, client=client, config=config)
    assert closed.degraded and closed.error is not None and closed.decision == d["fallback"]["interactive"]
    opened = await run_recipe(recipe, inputs, client=client, config=config, fail_mode="open")
    assert opened.degraded and opened.decision == d["decisions"][0]
    assert (await run_recipe(recipe, {**inputs, "unattended": True}, client=client, config=config, fail_mode="closed")).decision \
        == d["fallback"]["unattended"]
    with pytest.raises(RecipeError, match="fail_mode"):
        await run_recipe(recipe, inputs, client=client, config=config, fail_mode="ajar")
    await client.aclose()


def test_phase1_documents_still_load_unchanged():
    d = copy.deepcopy(recipe_doc())
    r = load_recipe(d)
    assert r.routing is False and r.decision_format == {} and r.outputs == {} and r.image_inputs == frozenset()
