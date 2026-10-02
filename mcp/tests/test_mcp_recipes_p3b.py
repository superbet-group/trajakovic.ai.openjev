"""Phase-3 builtin recipes, part B (P18): semantic_filter, rag_gate, rubric_score, entity_match, memory_decide,
taxonomy_classify, bulk_label, ui_decision, multistep_tick, threshold_audit. Load, replay of the captured spec
examples, policy tables on stub answers, fail modes. No model. `inputs_of` rebuilds recipe inputs from a case
request; the live file reuses it."""
from __future__ import annotations

import json
import os
import re

import pytest
import recipe_harness as rh
import stubs

from openjev_mcp.recipes.engine import build_requests

IDS = ("semantic_filter", "rag_gate", "rubric_score", "entity_match", "memory_decide", "taxonomy_classify",
       "bulk_label", "ui_decision", "multistep_tick", "threshold_audit")
FALLBACK = {"semantic_filter": "keep", "rag_gate": "drop", "rubric_score": "review", "entity_match": "related",
            "memory_decide": "add", "taxonomy_classify": "parent", "bulk_label": "needs_review", "ui_decision": "confirm",
            "multistep_tick": "tie", "threshold_audit": "report"}
OPEN = {"semantic_filter": "keep", "rubric_score": "review", "taxonomy_classify": "parent", "multistep_tick": "tie"}
DECISIONS = {"semantic_filter": {"keep", "uncertain", "drop"}, "rag_gate": {"keep", "drop", "quarantine", "conflict"},
             "rubric_score": {"review", "reject", "shortlist"}, "entity_match": {"same", "related", "different"},
             "memory_decide": {"add", "duplicate", "supersede"}, "taxonomy_classify": {"leaf", "parent", "human"},
             "bulk_label": {"label", "needs_review", "audit"}, "ui_decision": {"click", "confirm", "done", "blocked", "refresh"},
             "multistep_tick": {"move", "beam", "stop", "tie"}, "threshold_audit": {"report"}}
OPTIONS = ("steps", "samples", "think", "sequential")


def doc(rid: str) -> dict:
    path = os.path.join(os.path.dirname(stubs.__file__), "..", "openjev_mcp", "recipes", "builtin", f"{rid}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# inputs from a case request ------------------------------------------------------------------------------------------

def _opts(qs: dict, qid: str) -> list[tuple[str, str]]:
    return list((qs[qid].get("criteria") or {}).items())


def inputs_of(rid: str, req: dict) -> dict | None:
    """Recipe inputs rebuilt from a case request (None when the request is not shaped like this recipe's)."""
    try:
        return _inputs_of(rid, req)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, StopIteration):
        return None


def _inputs_of(rid: str, req: dict) -> dict | None:
    state, qs = req["state"], req["questions"]
    kinds = {q["type"] for q in qs.values()}
    single = next(iter(qs)) if len(qs) == 1 else None
    if rid == "semantic_filter":
        m = re.fullmatch(r"TASK: ([^\n]*)\nTAGGED LINES:\n(.*)", state, re.S)
        t = re.fullmatch(r"Which tagged line is (.*)\?", qs["line"]["instructions"])
        s = re.fullmatch(r"Does the tagged log contain at least one line that shows (.*)\?", qs["exists"]["instructions"])
        if not (m and t and s):
            return None
        gloss = qs["line"]["criteria"]
        rows = [re.match(r"\[([^\]]+)\] (.*)", ln) for ln in m[2].split("\n")]
        return {"task": m[1], "target": t[1], "shows": s[1],
                "lines": [{"id": r[1], "text": r[2], "gloss": gloss.get(r[1], r[2])} for r in rows]}
    if rid == "rag_gate":
        m = re.fullmatch(r"(User query|Premise the agent is about to assert): ([^\n]*)\n\nRetrieved passage \[(.*?)\]:\n(.*)", state, re.S)
        if not m:
            return None
        out = {"text": m[4], "source": m[3], "profile": "premise" if "contradicts" in qs else "strict"}
        out["premise" if m[1].startswith("Premise") else "query"] = m[2]
        if set(qs) == {"relevant", "evidence"}:
            out["profile"] = "relevance"
        return out
    if rid == "rubric_score":
        label, _, text = state.partition("\n")
        if kinds != {"score"} or not text or not all(isinstance(q["criteria"], list) for q in qs.values()):
            return None
        return {"label": label, "text": text, "dimensions": [{"name": k, "instructions": q["instructions"], "levels": q["criteria"]} for k, q in qs.items()]}
    if rid == "entity_match":
        lines = state.split("\n")
        if "match" not in qs or len(lines) != 2:
            return None
        return {"a": lines[0].split(": ", 1)[1], "b": lines[1].split(": ", 1)[1]}
    if rid == "memory_decide":
        m = re.fullmatch(r"STORED MEMORIES(?: \((.*?)\))?:\n(.*)\n\nNEW FACT: (.*)", state, re.S)
        if not m or "target" not in qs:
            return None
        topics = dict(_opts(qs, "target"))
        nb = [{"id": ln.split(": ", 1)[0], "text": ln.split(": ", 1)[1], "topic": topics[ln.split(": ", 1)[0]]} for ln in m[2].split("\n")]
        return {"new_fact": m[3], "neighbours": nb, **({"note": m[1]} if m[1] else {})}
    if rid == "taxonomy_classify":
        m = re.fullmatch(r"This is an? (\w+) (\w+)\. Which \1 subcategory .*", qs[single]["instructions"]) if single else None
        if not m:
            return None
        return {"text": state, "parent": m[1], "noun": m[2], "children": [{"id": k, "description": v} for k, v in _opts(qs, single)]}
    if rid == "bulk_label":
        m = re.fullmatch(r"Row (\w+): (.*)", state, re.S)
        if not m or "topic" not in qs:
            return None
        return {"rows": [{"id": m[1], "text": m[2]}], "taxonomy": [{"id": k, "description": v} for k, v in _opts(qs, "topic")]}
    if rid == "ui_decision":
        images = stubs._resolve_images(req).get("images")
        base: dict = {"images": images} if images else {}
        if set(qs) == {"next"}:
            m = re.fullmatch(r"Goal: ([^\n]*)\n((?:Page|Candidates)[^\n]*\n.*)", state, re.S)
            return {**base, "goal": m[1], "tree": m[2], "candidates": [{"id": k, "description": v} for k, v in _opts(qs, "next")]} if m else None
        prof = "login_wall" if set(qs) == {"login_wall"} else "state" if set(qs) <= {"done", "blocked", "irreversible"} else None
        if prof is None:
            return None
        m = re.fullmatch(r"Goal: ([^\n]*)\n(.*?)(?:\nProposed click: (.*))?", state, re.S)
        if m and not images:
            return {"goal": m[1], "tree": m[2], "profile": prof, **({"proposed_click": m[3]} if m[3] else {})}
        return {**base, "caption": state, "profile": prof} if images else None
    if rid == "multistep_tick":
        if single is None or qs[single]["type"] != "choice":
            return None
        return {"situation": state, "question": qs[single]["instructions"], "legal_moves": [{"id": k, "description": v} for k, v in _opts(qs, single)]}
    if rid == "threshold_audit":
        if single is None or qs[single]["type"] != "noul":
            return None
        return {"text": state, "question": qs[single]["instructions"]}
    return None


QID = {"taxonomy_classify": "sub", "multistep_tick": "move", "threshold_audit": "claim"}


def expect_for(rid: str, case: dict) -> dict:
    """The case's expect with its single question renamed to the recipe's question id."""
    ans = dict((case.get("expect") or {}).get("answers") or {})
    if rid in QID and len(case["request"]["questions"]) == 1 and len(ans) == 1:
        ans = {QID[rid]: next(iter(ans.values()))}
    return {"answers": ans}


def read_options_of(req: dict) -> dict:
    return {k: req[k] for k in OPTIONS if k in req}


def sample(cid: str, rid: str) -> dict:
    return inputs_of(rid, rh.captured_request(cid))


# load ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rid", IDS)
def test_builtin_loads(rid):
    r = rh.load(rid)
    assert r.id == rid and r.combine is not None and r.combine.clauses[-1].cond is None
    assert r.test_file and os.path.exists(os.path.join(rh.CASES_DIR, os.path.basename(r.test_file)))
    assert set(r.decisions) == DECISIONS[rid] and r.fallback == {"interactive": FALLBACK[rid], "unattended": FALLBACK[rid]}
    assert set(r.fallback.values()) <= set(r.decisions) and r.limitations and not r.routing
    assert "# deviation:" in doc(rid)["notes"]
    if OPEN.get(rid):
        assert r.decisions[0] == OPEN[rid] and r.fail_mode == "open"


def test_memory_decide_is_a_variant_of_entity_match():
    assert doc("memory_decide")["variant_of"] == "entity_match" and "variant_of" not in doc("entity_match")
    assert doc("memory_decide")["usage_type"] == doc("entity_match")["usage_type"]


def test_delegating_recipes_name_their_tool():
    text = {r: " ".join(doc(r)["limitations"]) for r in ("semantic_filter", "bulk_label", "threshold_audit", "ui_decision")}
    assert "`filter`" in text["semantic_filter"] and "`batch`" in text["bulk_label"] and "`calibrate`" in text["threshold_audit"]
    assert "data URLs" in doc("ui_decision")["input_schema"]["properties"]["images"]["description"]


# captured examples --------------------------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("cid,rid,decision", [("u19", "memory_decide", "supersede"), ("u20", "taxonomy_classify", "parent"),
                                              ("u22-tree", "ui_decision", "click:e06")])
async def test_captured_example_replays_exactly(cid, rid, decision):
    out, transport = await rh.run_replay(rh.load(rid), sample(cid, rid), [cid])
    assert out.decision == decision and out.requests == 1 and not out.degraded and out.error is None, out.reason
    assert json.loads(transport.requests[0].content) == rh.captured_request(cid)


@pytest.mark.anyio
async def test_u15_relevance_profile_replays_exactly_to_drop():
    inputs = sample("u15", "rag_gate")
    assert inputs["profile"] == "relevance"
    out, transport = await rh.run_replay(rh.load("rag_gate"), inputs, ["u15"])
    assert out.decision == "drop" and out.requests == 1 and not out.degraded
    assert json.loads(transport.requests[0].content) == rh.captured_request("u15")
    assert out.signals["relevant"] < 0.001


@pytest.mark.anyio
async def test_u22_image_replays_exactly_with_a_data_url():
    req = stubs._resolve_images(rh.captured_request("u22-image"))
    assert req["images"][0].startswith("data:image/png;base64,")
    inputs = inputs_of("ui_decision", rh.captured_request("u22-image"))
    assert inputs["profile"] == "login_wall" and inputs["images"] == req["images"]
    out, transport = await rh.run_replay(rh.load("ui_decision"), inputs, ["u22-image"])
    assert out.decision == "blocked" and out.requests == 1 and not out.degraded
    assert json.loads(transport.requests[0].content) == req


@pytest.mark.anyio
async def test_u23_replays_exactly_with_think_from_read_options():
    out, transport = await rh.run_replay(rh.load("multistep_tick"), sample("u23", "multistep_tick"), ["u23"], read_options={"think": 512})
    assert out.decision == "move:c3" and out.requests == 1 and not out.degraded
    assert json.loads(transport.requests[0].content) == rh.captured_request("u23")
    assert out.signals["move"] == "c3"


@pytest.mark.anyio
@pytest.mark.parametrize("cid,candidate", [("u18-a", "A"), ("u18-b", "B")])
async def test_u18_rubric_requests_equal_and_composite_is_weighted_in_code(cid, candidate):
    inputs = sample(cid, "rubric_score")
    for d, w in zip(inputs["dimensions"], (0.7, 0.3)):
        d["weight"] = w
    assert build_requests(rh.load("rubric_score"), inputs)[0] == rh.captured_request(cid)
    out, _ = await rh.run_replay(rh.load("rubric_score"), inputs, [cid])
    assert out.requests == 1 and not out.degraded
    want = {"A": 0.70, "B": 0.43}[candidate]
    assert out.signals["composite"] == pytest.approx(want, abs=0.005) and out.signals["composite_floors"] == 1.0


@pytest.mark.anyio
async def test_u18_ranking_flips_by_weights_alone():
    comps = {}
    for cid in ("u18-a", "u18-b"):
        for tag, ws in (("py", (0.7, 0.3)), ("lead", (0.3, 0.7))):
            inputs = sample(cid, "rubric_score")
            for d, w in zip(inputs["dimensions"], ws):
                d["weight"] = w
            out, _ = await rh.run_replay(rh.load("rubric_score"), inputs, [cid])
            comps[cid, tag] = out.signals["composite"]
    assert comps["u18-a", "py"] > comps["u18-b", "py"] and comps["u18-b", "lead"] > comps["u18-a", "lead"]
    assert comps["u18-a", "lead"] == pytest.approx(0.30, abs=0.005) and comps["u18-b", "lead"] == pytest.approx(0.60, abs=0.005)


@pytest.mark.anyio
async def test_u14_state_and_best_line_replay_on_captured_answers():
    inputs = sample("u14", "semantic_filter")
    body = build_requests(rh.load("semantic_filter"), inputs)[0]
    want = rh.captured_request("u14")
    assert body["state"].replace('\\"', '"') == want["state"]
    assert body["questions"]["line"] == want["questions"]["line"] and list(body["questions"]) == ["line", "exists"]
    out, _ = await rh.run_stub(rh.load("semantic_filter"), inputs, rh.captured_answers("u14"))
    assert out.decision == "keep" and out.signals["line"] == "a3" and out.signals["exists"] == pytest.approx(1.0, abs=1e-3)


@pytest.mark.anyio
async def test_u21_rows_replay_one_request_per_row():
    req = rh.captured_request("u21")
    rows = [{"id": f"R{i}", "text": t.split(": ", 1)[1]} for i, t in enumerate(req["state"].split("\n")[1:], 1)]
    taxonomy = [{"id": k, "description": v} for k, v in req["questions"]["R1_topic"]["criteria"].items()]
    got = stubs.captured("u21")["answers"]

    def build(questions, state, options):
        row = re.match(r"Row (R\d): ", state)[1]
        return {"topic": {**got[f"{row}_topic"], "probabilities": {k: v for k, v in got[f"{row}_topic"]["probabilities"].items() if k != "_omitted_options"}}}
    out, engine = await rh.run_stub(rh.load("bulk_label"), {"rows": rows, "taxonomy": taxonomy}, build)
    assert out.decision == "label" and out.requests == 4 and not out.degraded
    assert [(i["id"], i["decision"], i["signals"]["topic"]) for i in out.items] == [
        ("R1", "label", "account"), ("R2", "label", "feature"), ("R3", "label", "billing"), ("R4", "label", "bug")]


# policy tables ------------------------------------------------------------------------------------------------------

def lines(n=3):
    return [{"id": f"a{i}", "text": f"INFO line {i}", "gloss": f"line {i}"} for i in range(1, n + 1)]


SF = {"task": "find the crash", "target": "the root cause", "shows": "a root cause", "lines": lines()}


@pytest.mark.anyio
@pytest.mark.parametrize("line,exists,decision", [(("a1", 0.9), 0.95, "keep"), (("a1", 0.4), 0.95, "uncertain"),
                                                   (("a1", 0.9), 0.5, "uncertain"), (("a1", 0.9), 0.1, "drop"),
                                                   (("a1", 0.3), 0.1, "drop")])
async def test_semantic_filter_policy(line, exists, decision):
    out, _ = await rh.run_stub(rh.load("semantic_filter"), SF, rh.answers(line=line, exists=exists))
    assert out.decision == decision and out.requests == 1


RAG = {"text": "p", "source": "doc: x", "query": "q"}


@pytest.mark.anyio
@pytest.mark.parametrize("profile,vals,decision", [
    ("strict", dict(relevant=0.9, evidence=0.9, instructs_model=0.0), "keep"),
    ("strict", dict(relevant=0.99, evidence=0.9, instructs_model=0.9), "quarantine"),
    ("strict", dict(relevant=0.9, evidence=0.2, instructs_model=0.0), "drop"),
    ("strict", dict(relevant=0.1, evidence=0.1, instructs_model=0.0), "drop"),
    ("relevance", dict(relevant=0.9, evidence=0.6), "keep"),
    ("premise", dict(contradicts=0.9), "conflict"),
    ("premise", dict(contradicts=0.05), "keep"),
    ("premise", dict(contradicts=0.5), "drop"),
])
async def test_rag_gate_policy(profile, vals, decision):
    inputs = {**RAG, "profile": profile, **({"premise": "p"} if profile == "premise" else {})}
    out, engine = await rh.run_stub(rh.load("rag_gate"), inputs, rh.answers(**vals))
    assert out.decision == decision and out.requests == 1


@pytest.mark.anyio
async def test_rag_gate_profiles_ask_the_documented_questions():
    for profile, qids in (("strict", ["relevant", "evidence", "instructs_model"]), ("relevance", ["relevant", "evidence"]), ("premise", ["contradicts"])):
        body = build_requests(rh.load("rag_gate"), {**RAG, "profile": profile})[0]
        assert list(body["questions"]) == qids
    assert build_requests(rh.load("rag_gate"), {**RAG, "premise": "p", "profile": "premise"})[0]["state"].startswith("User query: q")


def dims(**spec):
    return [{"name": k, "instructions": f"Rate {k}.", "levels": ["none", "low", "mid", "high"], "weight": w, **({"floor": f} if f else {})}
            for k, (w, f) in spec.items()]


@pytest.mark.anyio
@pytest.mark.parametrize("scores,decision", [((3, 3), "shortlist"), ((0, 0), "reject"), ((2, 1), "review"), ((3, 0.4), "review")])
async def test_rubric_score_policy(scores, decision):
    d = dims(python=(0.5, 1.0), lead=(0.5, 1.0))
    out, _ = await rh.run_stub(rh.load("rubric_score"), {"label": "RESUME", "text": "t", "dimensions": d},
                               rh.answers(python=scores[0], lead=scores[1]))
    assert out.decision == decision and out.requests == 1


@pytest.mark.anyio
async def test_rubric_score_floor_failure_blocks_the_shortlist_and_levels_set_the_scale():
    d = dims(python=(0.5, 2.0), lead=(0.5, None))
    out, _ = await rh.run_stub(rh.load("rubric_score"), {"label": "RESUME", "text": "t", "dimensions": d}, rh.answers(python=1, lead=3))
    assert out.signals["composite"] == pytest.approx(2 / 3) and out.signals["composite_floors"] == 0.0 and out.decision == "review"
    d[0]["levels"] = ["a", "b"]
    d[0]["floor"] = None
    out, _ = await rh.run_stub(rh.load("rubric_score"), {"label": "RESUME", "text": "t", "dimensions": d}, rh.answers(python=1, lead=3))
    assert out.signals["composite"] == pytest.approx(1.0)


@pytest.mark.anyio
@pytest.mark.parametrize("score,decision", [(2.0, "same"), (1.7, "same"), (1.0, "related"), (0.5, "different"), (0.0, "different")])
async def test_entity_match_policy(score, decision):
    out, _ = await rh.run_stub(rh.load("entity_match"), {"a": "name='ACME'", "b": "name='Acme Inc'", "equivalence_rule": "legal suffix"},
                                    rh.answers(match=score))
    assert out.decision == decision


def test_entity_match_state_and_rule():
    body = build_requests(rh.load("entity_match"), {"a": "x", "b": "y", "equivalence_rule": "casing"})[0]
    assert body["state"] == "Record A: x\nRecord B: y\nDifferences that do not count: casing"
    assert build_requests(rh.load("entity_match"), {"a": "x", "b": "y"})[0]["state"] == "Record A: x\nRecord B: y"
    assert len(body["questions"]["match"]["criteria"]) == 3


NB = [{"id": "M1", "text": "a", "topic": "M1 (a)"}, {"id": "M2", "text": "b", "topic": "M2 (b)"}]


@pytest.mark.anyio
@pytest.mark.parametrize("action,p,decision", [("supersede", 0.95, "supersede"), ("duplicate", 0.9, "duplicate"),
                                               ("supersede", 0.6, "add"), ("add", 0.99, "add")])
async def test_memory_decide_policy(action, p, decision):
    out, _ = await rh.run_stub(rh.load("memory_decide"), {"new_fact": "f", "neighbours": NB},
                               rh.answers(action=(action, p), target=("M2", 0.9)))
    assert out.decision == decision and out.signals["target"] == "M2" and out.signals["action_p"] == pytest.approx(p)


@pytest.mark.anyio
async def test_memory_decide_zero_neighbours_degrades_to_add_without_a_request():
    engine = stubs.StubEngine()
    out, _ = await rh.run_stub(rh.load("memory_decide"), {"new_fact": "f", "neighbours": []}, engine=engine)
    assert out.decision == "add" and out.degraded and out.requests == 0 and out.error.code == "OJ_INVALID_INPUT"


CH = [{"id": "api", "description": "http"}, {"id": "database", "description": "sql"}, {"id": "other_backend", "description": "none fits"}]


@pytest.mark.anyio
@pytest.mark.parametrize("pick,decision", [(("database", 0.9), "leaf"), (("other_backend", 0.95), "parent"),
                                           (("database", 0.5), "parent"), (("database", 0.3), "human")])
async def test_taxonomy_classify_policy(pick, decision):
    out, engine = await rh.run_stub(rh.load("taxonomy_classify"), {"text": "t", "parent": "backend", "noun": "issue", "children": CH},
                                    rh.answers(sub=pick))
    assert out.decision == decision


@pytest.mark.anyio
async def test_taxonomy_low_confidence_rereads_with_think_and_samples():
    seen = []

    def build(questions, state, options):
        seen.append(options)
        return rh.answers(sub=(("database", 0.5) if len(seen) == 1 else ("database", 0.9)))(questions, state, options)
    out, _ = await rh.run_stub(rh.load("taxonomy_classify"), {"text": "t", "parent": "backend", "noun": "issue", "children": CH}, build)
    assert out.requests == 2 and out.decision == "leaf" and seen[1].get("think") == 256 and seen[1].get("samples") == 2


ROWS = [{"id": "1", "text": "a"}, {"id": "2", "text": "b"}, {"id": "3", "text": "c"}, {"id": "4", "text": ""}]
TAX = [{"id": "billing", "description": "money"}, {"id": "other", "description": "none"}, {"id": "empty", "description": "blank"}]


@pytest.mark.anyio
async def test_bulk_label_per_row_decisions_and_worst_aggregate():
    picks = {1: ("billing", 0.97), 2: ("billing", 0.7), 3: ("other", 0.95), 4: ("empty", 0.95)}
    out, _ = await rh.run_stub(rh.load("bulk_label"), {"rows": ROWS, "taxonomy": TAX}, rh.answers(topic=lambda n: picks[n]))
    assert [i["decision"] for i in out.items] == ["label", "needs_review", "audit", "audit"] and out.decision == "needs_review"
    assert out.requests == 4 and out.items[0]["signals"]["topic"] == "billing"
    out, _ = await rh.run_stub(rh.load("bulk_label"), {"rows": ROWS[:1], "taxonomy": TAX}, rh.answers(topic=("billing", 0.95)))
    assert out.decision == "label"


@pytest.mark.anyio
async def test_bulk_label_question_names_the_row():
    body = build_requests(rh.load("bulk_label"), {"rows": ROWS[:2], "taxonomy": TAX})
    assert body[1]["state"] == "Row 2: b" and body[1]["questions"]["topic"]["instructions"] == "What is the main topic of row 2?"


TREE = {"goal": "g", "tree": "Page: p\ne01 link 'Home'\ne02 button 'Go'", "candidates": [{"id": "e01", "description": "home"}, {"id": "e02", "description": "go"}]}


@pytest.mark.anyio
@pytest.mark.parametrize("profile,vals,decision", [
    ("strict", dict(next=("e02", 0.95)), "click:e02"),
    ("strict", dict(next=("e02", 0.6)), "refresh"),
    ("state", dict(done=0.95, blocked=0.01, irreversible=0.01), "done"),
    ("state", dict(done=0.1, blocked=0.95, irreversible=0.01), "blocked"),
    ("state", dict(done=0.95, blocked=0.01, irreversible=0.7), "confirm"),
    ("state", dict(done=0.5, blocked=0.01, irreversible=0.01), "refresh"),
    ("state", dict(done=0.1, blocked=0.5, irreversible=0.01), "refresh"),
    ("state", dict(done=0.1, blocked=0.1, irreversible=0.1), "refresh"),
    ("login_wall", dict(login_wall=0.99), "blocked"),
    ("login_wall", dict(login_wall=0.01), "refresh"),
])
async def test_ui_decision_policy(profile, vals, decision):
    out, _ = await rh.run_stub(rh.load("ui_decision"), {**TREE, "profile": profile}, rh.answers(**vals))
    assert out.decision == decision and out.requests == 1


def test_ui_decision_state_and_image_rules():
    r = rh.load("ui_decision")
    body = build_requests(r, {**TREE, "profile": "state", "proposed_click": "e02 button 'Go'"})[0]
    assert body["state"].endswith("\nProposed click: e02 button 'Go'") and body["state"].startswith("Goal: g\nPage: p")
    body = build_requests(r, {"caption": "Screenshot.", "images": ["data:image/png;base64,AAAA"], "profile": "state"})[0]
    assert body["state"] == "Screenshot." and body["images"] == ["data:image/png;base64,AAAA"]
    with pytest.raises(Exception, match="data URLs"):
        build_requests(r, {"caption": "x", "images": ["https://example.com/a.png"], "profile": "login_wall"})
    assert doc("ui_decision")["input_schema"]["properties"]["images"]["x-openjev-images"] is True


@pytest.mark.anyio
async def test_ui_decision_refuses_think_with_images():
    from openjev_mcp.errors import ToolError
    with pytest.raises(ToolError, match="E022"):
        await rh.run_stub(rh.load("ui_decision"), {"caption": "x", "images": ["data:image/png;base64,AAAA"], "profile": "login_wall"},
                          rh.answers(), read_options={"think": 64})


MOVES = [{"id": "a", "description": "play a"}, {"id": "b", "description": "play b"}]
MT = {"situation": "BOARD", "question": "Which move?", "legal_moves": MOVES}


@pytest.mark.anyio
@pytest.mark.parametrize("pick,decision,requests", [(("a", 0.95), "move:a", 1), (("b", 0.3), "tie", 2), (("b", 0.7), "beam", 2)])
async def test_multistep_tick_policy(pick, decision, requests):
    out, _ = await rh.run_stub(rh.load("multistep_tick"), MT, rh.answers(move=pick))
    assert out.decision == decision and out.requests == requests


@pytest.mark.anyio
async def test_multistep_tick_reread_uses_think_and_samples_and_only_legal_moves():
    seen = []

    def build(questions, state, options):
        seen.append(options)
        return rh.answers(move=("a", 0.6 if len(seen) == 1 else 0.9))(questions, state, options)
    out, _ = await rh.run_stub(rh.load("multistep_tick"), MT, build)
    assert out.decision == "move:a" and seen[1].get("think") == 512 and seen[1].get("samples") == 4
    body = build_requests(rh.load("multistep_tick"), MT)[0]
    assert list(body["questions"]["move"]["criteria"]) == ["a", "b"] and body["questions"]["move"]["instructions"] == "Which move?"


@pytest.mark.anyio
@pytest.mark.parametrize("claim,zone", [(0.99, "high"), (0.5, "mid"), (0.01, "low")])
async def test_threshold_audit_reports_p_and_zone(claim, zone):
    out, _ = await rh.run_stub(rh.load("threshold_audit"), {"text": "ticket", "question": "Escalate now?"}, rh.answers(claim=claim))
    assert out.decision == "report" and out.signals["claim"] == pytest.approx(claim) and out.outputs == {"zone": zone}


# fail modes ---------------------------------------------------------------------------------------------------------

INPUTS = {"semantic_filter": SF, "rag_gate": RAG, "rubric_score": {"label": "RESUME", "text": "t", "dimensions": dims(python=(1, None))},
          "entity_match": {"a": "x", "b": "y"}, "memory_decide": {"new_fact": "f", "neighbours": NB},
          "taxonomy_classify": {"text": "t", "parent": "backend", "noun": "issue", "children": CH},
          "bulk_label": {"rows": ROWS[:2], "taxonomy": TAX}, "ui_decision": {**TREE, "profile": "strict"}, "multistep_tick": MT,
          "threshold_audit": {"text": "t", "question": "q?"}}


@pytest.mark.anyio
@pytest.mark.parametrize("rid", IDS)
@pytest.mark.parametrize("fault", rh.FAULTS)
async def test_each_fault_gives_the_degraded_fallback(rid, fault):
    out, code = await rh.run_fault(rh.load(rid), INPUTS[rid], fault)
    assert out.decision == FALLBACK[rid] and out.degraded and out.error.code == code


@pytest.mark.anyio
@pytest.mark.parametrize("rid", sorted(OPEN))
async def test_fail_open_gives_the_least_severe_decision(rid):
    out, _ = await rh.run_fault(rh.load(rid), INPUTS[rid], "refused", fail_mode="open")
    assert out.decision == OPEN[rid] and out.degraded


@pytest.mark.anyio
@pytest.mark.parametrize("rid", ("rag_gate", "entity_match", "ui_decision", "bulk_label"))
async def test_fail_open_is_not_safe_for_closed_recipes_unless_asked(rid):
    out, _ = await rh.run_fault(rh.load(rid), INPUTS[rid], "refused")
    assert out.decision == FALLBACK[rid]


# build_requests of case-file requests -------------------------------------------------------------------------------

@pytest.mark.parametrize("rid", IDS)
def test_case_requests_map_to_recipe_bodies(rid):
    """Every case request of the recipe's test file that inputs_of maps builds a valid body whose option keys
    equal the case's (no model)."""
    r = rh.load(rid)
    mapped = 0
    for case in rh.cases_of(r.test_file).values():
        req = case["request"]
        inputs = inputs_of(rid, req)
        if inputs is None:
            continue
        mapped += 1
        body = build_requests(r, inputs)[0]
        for qid, q in req["questions"].items():
            got = body["questions"].get(qid)
            if got and q["type"] == "choice":
                assert set(got["criteria"]) == set(q["criteria"]), (case["id"], qid)
    assert mapped >= 1, rid
