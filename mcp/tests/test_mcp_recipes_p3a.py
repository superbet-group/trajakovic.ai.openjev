"""Phase-3 builtin recipes, part A (P17): ticket_triage, semantic_lint, issue_triage (+ duplicate_check,
review_finding_filter), select_extraction (+ verify_fields), judge_assert (+ judge_pairwise), alert_triage,
claim_check. Load, replay of the captured spec examples, policy tables on stub answers, fail modes. No model.
Also holds the case-state mappers the live file reuses (the shared harness maps only the phase-2 recipes)."""
from __future__ import annotations

import json
import os
import re
from types import SimpleNamespace

import pytest
import recipe_harness as rh
import stubs

from openjev_mcp.recipes.engine import build_requests

IDS = ("ticket_triage", "semantic_lint", "issue_triage", "duplicate_check", "review_finding_filter", "select_extraction",
       "verify_fields", "judge_assert", "judge_pairwise", "alert_triage", "claim_check")
VARIANTS = {"duplicate_check": "issue_triage", "review_finding_filter": "issue_triage", "verify_fields": "select_extraction",
            "judge_pairwise": "judge_assert"}
TEAMS = [{"label": "billing", "description": "charges, invoices, refunds, payment methods, plan pricing"},
         {"label": "technical", "description": "bugs, errors, outages, integrations, performance, login problems"},
         {"label": "sales", "description": "pre-purchase questions, quotes, upgrades, demos, enterprise pricing"}]
CANDS = [{"id": "#1", "summary": "csv export drops rows"}, {"id": "#2", "summary": "oom on big import"}]
MIN = {
    "ticket_triage": {"text": "Subject: hi\n\nI was charged twice", "teams": TEAMS},
    "semantic_lint": {"text": "def f():\n    try:\n        g()\n    except Exception:\n        pass\n"},
    "issue_triage": {"title": "Crash on start", "body": "It exits.\nStack: a.go:1"},
    "duplicate_check": {"new": "Emails arrive in UTC", "candidates": CANDS},
    "review_finding_filter": {"diff": "d", "findings": ["f1", "f2"]},
    "select_extraction": {"text": "call 415-555-0142 or 415-555-0199", "field": "the callback number",
                          "candidates": [{"value": "415-555-0142", "description": "the customer's own"}, {"value": "415-555-0199", "description": "the office"}]},
    "verify_fields": {"text": "Order A-1 shipped to Leeds by UPS", "fields": [{"name": "city", "value": "Leeds"}, {"name": "carrier", "value": "DHL"}]},
    "judge_assert": {"reply": "Sure, here you go.", "user_message": "help me"},
    "judge_pairwise": {"user_message": "q", "a": "a-text", "b": "b-text", "criterion": "answers the question"},
    "alert_triage": {"text": "ERROR payments: connection refused"},
    "claim_check": {"claim": "timeout is 60s", "source": "timeout=45", "claim_label": "CLAIM", "source_label": "SOURCE"},
}
FALLBACK = {"ticket_triage": "human_review", "semantic_lint": "ignore", "issue_triage": "unsure", "duplicate_check": "unsure",
            "review_finding_filter": "collapse", "select_extraction": "review", "verify_fields": "review", "judge_assert": "fail",
            "judge_pairwise": "tie", "alert_triage": "review", "claim_check": "review"}
# u* example -> (recipe, documented decision)
REPLAYS = {"u01": ("ticket_triage", "route"), "u06": ("semantic_lint", "warn"), "u06-secret": ("semantic_lint", "ignore"),
           "u07": ("duplicate_check", "duplicate"), "u12": ("judge_assert", "fail"), "u13": ("alert_triage", "page"),
           "u16": ("claim_check", "contradicted")}


def doc(rid: str) -> dict:
    path = os.path.join(os.path.dirname(stubs.__file__), "..", "openjev_mcp", "recipes", "builtin", f"{rid}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# case-state mappers (shared with tests/live/test_live_recipes_p3a.py) ----------------------------------------------------

def _crit(qs: dict, qid: str) -> list[tuple[str, str]]:
    return [(k, v) for k, v in ((qs.get(qid) or {}).get("criteria") or {}).items() if k != "none"]


def inputs_from_case(rid: str, case: dict) -> dict | None:
    """Recipe inputs rebuilt from a case's state and questions (None when the case is not shaped like this recipe's)."""
    req = case.get("request") or {}
    state, qs = req.get("state"), req.get("questions", {})
    if not isinstance(state, str):
        return None
    S = re.S
    if rid == "ticket_triage":
        if "dept" not in qs:
            return None
        teams = [{"label": k, "description": v} for k, v in _crit(qs, "dept") if k != "other"] or TEAMS
        return {"text": state, "teams": teams, "profile": "full" if {"urgent", "frustration"} & set(qs) else "strict"}
    if rid == "semantic_lint":
        by = {"swallows": "strict", "secret": "secret", "mismatch": "commit_mismatch", "contradicts": "comment_contradicts",
              "slop": "prose_slop", "naming": "naming"}
        qid = next((q for q in qs if q in by), None)
        return {"text": state, "profile": by[qid]} if qid else None
    if rid == "issue_triage":
        m = re.fullmatch(r"Title: (.*)\nBody: (.*)", state, S)
        return {"title": m[1], "body": m[2]} if m and "kind" in qs else None
    if rid == "duplicate_check":
        if "dupe_of" in qs:
            m = re.fullmatch(r"NEW issue: '(.*)'", state, S)
            return {"new": m[1], "candidates": [{"id": k, "summary": v} for k, v in _crit(qs, "dupe_of")]} if m else None
        m = re.fullmatch(r"NEW issue[^:\n]*: '(.*)'\n\nCANDIDATE[^:\n]*: '(.*)'", state, S)
        return {"new": m[1], "candidate": m[2], "profile": "pair"} if m and "dup" in qs else None
    if rid == "review_finding_filter":
        m = re.fullmatch(r"(.*)\n\nREVIEW FINDING: '(.*)'", state, S)
        return {"diff": m[1], "findings": [m[2]]} if m and "real" in qs else None
    if rid == "select_extraction":
        q = next(iter(qs.values())) if len(qs) == 1 else None
        m = re.fullmatch(r"Which candidate is (.*)\?", (q or {}).get("instructions", ""), S)
        if not m or q["type"] != "choice" or "not_stated" not in q["criteria"]:
            return None
        return {"text": state, "field": m[1], "candidates": [{"value": k, "description": v} for k, v in q["criteria"].items() if k != "not_stated"]}
    if rid == "verify_fields":
        found = [re.match(r"Extracted (?:field: )?(\w+) = '(.*?)'\. ", q.get("instructions", "")) for q in qs.values()]
        if not qs or not all(found) or not all(q["type"] == "noul" for q in qs.values()):
            return None
        return {"text": state, "fields": [{"name": m[1], "value": m[2]} for m in found]}
    if rid == "judge_assert":
        by = {"leak": "leak", "grounded": "grounded", "refusal": "refuses", "rude": "rude", "pii": "pii", "correct": "correct"}
        qid = next((q for q in qs if q in by), None)
        m = re.fullmatch(r"CONTEXT DOC:\n(.*)\n\nUSER QUESTION: (.*)\nANSWER: (.*)", state, S)
        if qid and m:
            return {"context": m[1], "user_message": m[2], "reply": m[3], "profile": by[qid]}
        m = re.fullmatch(r"USER MESSAGE:\n(.*)\n\nASSISTANT REPLY UNDER TEST:\n(.*)", state, S)
        return {"user_message": m[1], "reply": m[2], "profile": by[qid]} if qid and m else None
    if rid == "judge_pairwise":
        m = re.fullmatch(r"USER MESSAGE:\n(.*)\n\nCANDIDATE A:\n(.*)\n\nCANDIDATE B:\n(.*)", state, S)
        q = qs.get("better") or {}
        c = re.fullmatch(r"Which candidate reply (?:better )?(.*)\?", q.get("instructions", ""), S)
        if not (m and c) or set(q.get("criteria", {})) != {"A", "B"}:
            return None
        return {"user_message": m[1], "a": m[2], "b": m[3], "criterion": c[1]}
    if rid == "alert_triage":
        if "dup" in qs:
            m = re.fullmatch(r"Open incident:? (.*)\nNew alert:? (.*)", state, S)
            return {"open_incident": m[1], "text": m[2], "profile": "dedupe"} if m else None
        return {"text": state} if {"real", "sev"} & set(qs) else None
    if rid == "claim_check":
        qid = next((q for q in ("v", "grounded", "verbatim") if q in qs), None)
        if qid is None:
            return None
        lab = r"([A-Z][A-Z ]*?)"
        m = re.fullmatch(lab + r': "(.*?)"\n\n' + lab + r"(?: \((.*?)\))?:[ \n](.*)", state, S)
        if m:
            cl, claim, sl, sn, src = m.groups()
        else:
            m = re.fullmatch(lab + r"(?: \((.*?)\))?:[ \n](.*?)\n\n" + lab + r":[ \n](.*)", state, S)
            if not m:
                return None
            sl, sn, src, cl, claim = m.groups()
            claim = claim[1:-1] if claim[:1] == claim[-1:] == '"' and len(claim) > 1 else claim
        out = {"claim": claim, "source": src, "claim_label": cl, "source_label": sl,
               "profile": {"grounded": "grounded", "verbatim": "quote"}.get(qid, "strict" if qs[qid]["type"] == "choice" else "overstatement")}
        if sn:
            out["source_name"] = sn
        return out
    return None


def case_expect(rid: str, outcome, expect: dict):
    """The (outcome, expect) pair recipe_harness.check_expect understands: answers re-keyed to the case's question ids
    (a per-item recipe has no answers, its items carry the signals; recipe question ids differ from the case's)."""
    qs = list((expect.get("answers") or {}))
    a = dict(outcome.answers)
    if rid == "select_extraction" and qs:
        a = {qs[0]: a["value"]} if "value" in a else {}
    elif rid == "review_finding_filter" and outcome.items:
        a = {"real": {"p": outcome.items[0]["signals"].get("real")}}
    elif rid == "verify_fields":
        a = {q: {"p": it["signals"].get("ok")} for q, it in zip(qs, outcome.items or [])}
    elif rid == "judge_assert" and "refusal" in qs and "refuses" in a:
        a = {"refusal": a["refuses"]}
    elif rid == "claim_check" and "strength" in a and qs:
        a = {qs[0]: a["strength"]}
    return SimpleNamespace(decision=outcome.decision, answers=a), expect


def sample(cid: str, rid: str) -> dict:
    return inputs_from_case(rid, {"request": rh.captured_request(cid)})


# load ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rid", IDS)
def test_builtin_loads(rid):
    r = rh.load(rid)
    assert r.id == rid and r.combine is not None and r.combine.clauses[-1].cond is None
    assert r.test_file and os.path.exists(os.path.join(rh.CASES_DIR, os.path.basename(r.test_file)))
    assert not r.routing and set(r.fallback.values()) <= set(r.decisions) and r.limitations
    assert r.fallback["interactive"] == FALLBACK[rid]
    assert "# deviation:" in doc(rid)["notes"]


def test_variants_name_their_primary():
    assert {rid: doc(rid).get("variant_of") for rid in IDS if doc(rid).get("variant_of")} == VARIANTS
    for rid, primary in VARIANTS.items():
        assert doc(primary).get("variant_of") is None and doc(rid)["usage_type"] == doc(primary)["usage_type"]


def test_decision_lists_match_2_18():
    want = {"ticket_triage": {"route", "human_review", "escalate"}, "semantic_lint": {"block", "warn", "ignore"},
            "issue_triage": {"label", "unsure", "needs_info"}, "duplicate_check": {"duplicate", "not_duplicate", "unsure"},
            "review_finding_filter": {"post", "collapse", "drop"}, "select_extraction": {"selected", "not_stated", "review"},
            "verify_fields": {"accept", "reject", "review"}, "judge_assert": {"pass", "fail", "review"}, "judge_pairwise": {"A", "B", "tie"},
            "alert_triage": {"suppress", "watch", "review", "page"}, "claim_check": {"supported", "contradicted", "unsupported", "review"}}
    for rid, decisions in want.items():
        assert set(rh.load(rid).decisions) == decisions and len(rh.load(rid).decisions) == len(decisions)


@pytest.mark.parametrize("rid", IDS)
def test_build_requests_makes_no_request(rid):
    bodies = build_requests(rh.load(rid), MIN[rid])
    assert bodies and all(b["model"] == "openjev-latest" and b["state"] and b["questions"] for b in bodies)


def test_the_same_policy_names_as_documented():
    assert rh.load("alert_triage").policy["page_sev"] == 3.0 and rh.load("semantic_lint").policy["block_at"] == 0.9
    assert rh.load("claim_check").policy["contra_p"] == 0.6 and rh.load("duplicate_check").policy["dupe_p"] == 0.6


# captured examples --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cid", list(REPLAYS))
def test_built_request_equals_the_captured_body(cid):
    rid, _ = REPLAYS[cid]
    inputs = sample(cid, rid)
    assert inputs is not None
    assert build_requests(rh.load(rid), inputs) == [rh.captured_request(cid)]


@pytest.mark.anyio
@pytest.mark.parametrize("cid", list(REPLAYS))
async def test_captured_example_replays_to_its_decision(cid):
    rid, decision = REPLAYS[cid]
    out, transport = await rh.run_replay(rh.load(rid), sample(cid, rid), [cid])
    assert out.decision == decision and out.requests == 1 and not out.degraded and out.error is None, out.reason
    assert json.loads(transport.requests[0].content) == rh.captured_request(cid)


@pytest.mark.anyio
async def test_replayed_signals():
    out, _ = await rh.run_replay(rh.load("ticket_triage"), sample("u01", "ticket_triage"), ["u01"])
    assert out.signals["dept"] == "billing" and out.signals["refund_flag"] == "yes" and out.signals["priority"] == "normal"
    out, _ = await rh.run_replay(rh.load("alert_triage"), sample("u13", "alert_triage"), ["u13"])
    assert out.signals["real"] == pytest.approx(0.9996, abs=5e-5) and out.signals["sev"] == pytest.approx(3.5688, abs=5e-5)
    out, _ = await rh.run_replay(rh.load("duplicate_check"), sample("u07", "duplicate_check"), ["u07"])
    assert out.signals["dupe_of"] == "#233" and out.signals["dupe_of_p"] == pytest.approx(0.9996, abs=5e-5)
    out, _ = await rh.run_replay(rh.load("claim_check"), sample("u16", "claim_check"), ["u16"])
    assert out.signals["v"] == "contradicts" and "v.p=0.9968 >= 0.6" in out.reason


def test_u11_request_differs_only_in_the_question_id_and_the_not_stated_text():
    want = rh.captured_request("u11")
    got = build_requests(rh.load("select_extraction"), sample("u11", "select_extraction"))[0]
    assert got["state"] == want["state"] and list(got["questions"]) == ["value"]
    q, w = got["questions"]["value"], want["questions"]["callback"]
    assert q["instructions"] == w["instructions"] and q["type"] == w["type"] == "choice"
    assert {k: v for k, v in q["criteria"].items() if k != "not_stated"} == {k: v for k, v in w["criteria"].items() if k != "not_stated"}
    assert list(q["criteria"]) == list(w["criteria"])


@pytest.mark.anyio
async def test_u11_captured_answer_selects_the_callback_number():
    got = stubs.captured("u11")["answers"]["callback"]
    out, _ = await rh.run_stub(rh.load("select_extraction"), sample("u11", "select_extraction"), lambda qs, st, op: {"value": got})
    assert out.decision == "selected" and out.signals["value"] == "415-555-0142" and out.requests == 1


@pytest.mark.anyio
@pytest.mark.parametrize("rid", ["ticket_triage", "semantic_lint", "issue_triage", "duplicate_check", "review_finding_filter", "select_extraction",
                                 "verify_fields", "judge_assert", "judge_pairwise", "alert_triage", "claim_check"])
async def test_every_case_that_maps_builds_a_valid_request(rid):
    recipe = rh.load(rid)
    mapped = 0
    for case in rh.cases_of(recipe.test_file).values():
        inputs = inputs_from_case(rid, case)
        if inputs is not None:
            mapped += 1
            assert build_requests(recipe, inputs)
    assert mapped >= (1 if rid in ("select_extraction", "judge_pairwise", "alert_triage") else 2), mapped


# policy tables ------------------------------------------------------------------------------------------------------

def reads(engine):
    return len(engine.calls)


@pytest.mark.anyio
@pytest.mark.parametrize("profile,ans,decision", [
    ("strict", {}, "route"),
    ("strict", {"dept": ("other", 0.9)}, "human_review"),
    ("strict", {"dept": ("billing", 0.5)}, "human_review"),
    ("strict", {"churn": 0.9}, "escalate"),
    ("strict", {"churn": 0.5}, "human_review"),
    ("strict", {"refund": 0.5}, "human_review"),
    ("strict", {"refund": 0.95}, "route"),
    ("full", {"frustration": 2}, "escalate"),
    ("full", {"frustration": 1}, "route"),
    ("full", {"urgent": 0.5}, "human_review"),
    ("full", {"urgent": 0.95}, "route"),
])
async def test_ticket_triage_policy(profile, ans, decision):
    out, engine = await rh.run_stub(rh.load("ticket_triage"), MIN["ticket_triage"], rh.answers(**ans), profile=profile)
    assert out.decision == decision, out.reason
    assert reads(engine) == 1 and list(engine.calls[0]["questions"]) == (["dept", "refund", "churn"] if profile == "strict" else ["dept", "urgent", "frustration", "refund", "churn"])


@pytest.mark.anyio
async def test_ticket_triage_outputs_and_state():
    out, engine = await rh.run_stub(rh.load("ticket_triage"), MIN["ticket_triage"], rh.answers(urgent=0.95, refund=0.95), profile="full")
    assert out.outputs == {"priority": "urgent", "refund_flag": "yes"} and out.signals["priority"] == "urgent"
    assert engine.calls[0]["state"] == MIN["ticket_triage"]["text"]
    assert list(engine.calls[0]["questions"]["dept"]["criteria"]) == ["billing", "technical", "sales", "other"]
    out, _ = await rh.run_stub(rh.load("ticket_triage"), MIN["ticket_triage"], rh.answers(refund=0.5))
    assert out.outputs == {"priority": "normal", "refund_flag": "unsure"}


@pytest.mark.anyio
@pytest.mark.parametrize("profile,ans,decision,n", [
    ("strict", {"swallows": 0.95}, "warn", 1), ("strict", {"swallows": 0.05}, "ignore", 1),
    ("secret", {"secret": 0.97}, "block", 1), ("secret", {"secret": 0.01}, "ignore", 1),
    ("secret", {"secret": 0.75}, "warn", 2), ("secret", {"secret": 0.5}, "ignore", 2),
    ("commit_mismatch", {"mismatch": 0.9}, "warn", 1), ("comment_contradicts", {"contradicts": 0.9}, "warn", 1),
    ("prose_slop", {"slop": 0.9}, "warn", 1), ("prose_slop", {"slop": 0.1}, "ignore", 1),
    ("naming", {"naming": 0}, "warn", 1), ("naming", {"naming": 3}, "ignore", 1),
])
async def test_semantic_lint_policy(profile, ans, decision, n):
    out, engine = await rh.run_stub(rh.load("semantic_lint"), MIN["semantic_lint"], rh.answers(**ans), profile=profile)
    assert out.decision == decision, out.reason
    assert reads(engine) == n and len(engine.calls[0]["questions"]) == 1
    assert engine.calls[0]["state"] == MIN["semantic_lint"]["text"]
    if n == 2:
        assert engine.calls[1]["options"]["samples"] == 4 and engine.calls[1]["state"] == engine.calls[0]["state"]


@pytest.mark.anyio
async def test_semantic_lint_only_secrets_block_and_reread_replaces_the_grey_read():
    for profile, q in (("strict", "swallows"), ("commit_mismatch", "mismatch"), ("comment_contradicts", "contradicts"), ("prose_slop", "slop")):
        out, _ = await rh.run_stub(rh.load("semantic_lint"), MIN["semantic_lint"], rh.answers(**{q: 0.999}), profile=profile)
        assert out.decision == "warn"
    out, engine = await rh.run_stub(rh.load("semantic_lint"), MIN["semantic_lint"], rh.answers(secret=lambda n: 0.5 if n == 1 else 0.95), profile="secret")
    assert out.decision == "block" and reads(engine) == 2 and out.signals["secret"] == pytest.approx(0.95)


@pytest.mark.anyio
@pytest.mark.parametrize("ans,decision,urgency", [
    ({"kind": ("bug", 0.9), "actionable": 0.9, "urgent": 0.95}, "label", "escalate"),
    ({"kind": ("bug", 0.9), "actionable": 0.9, "urgent": 0.5}, "label", "human"),
    ({"kind": ("feature", 0.9), "actionable": 0.9, "urgent": 0.1}, "label", "normal"),
    ({"kind": ("bug", 0.9), "actionable": 0.1, "urgent": 0.1}, "needs_info", "normal"),
    ({"kind": ("bug", 0.5), "actionable": 0.9, "urgent": 0.1}, "unsure", "normal"),
    ({"kind": ("bug", 0.5), "actionable": 0.1, "urgent": 0.1}, "needs_info", "normal"),
])
async def test_issue_triage_policy(ans, decision, urgency):
    out, engine = await rh.run_stub(rh.load("issue_triage"), MIN["issue_triage"], rh.answers(**ans))
    assert out.decision == decision and out.outputs == {"urgency": urgency}, out.reason
    assert engine.calls[0]["state"] == "Title: Crash on start\nBody: It exits.\nStack: a.go:1" and out.signals["kind"] == ans["kind"][0]


@pytest.mark.anyio
@pytest.mark.parametrize("profile,extra,ans,decision", [
    ("strict", {}, {"dupe_of": ("#1", 0.9)}, "duplicate"), ("strict", {}, {"dupe_of": ("#1", 0.5)}, "unsure"),
    ("strict", {}, {"dupe_of": ("none", 0.9)}, "not_duplicate"), ("strict", {}, {"dupe_of": ("#2", 0.6)}, "duplicate"),
    ("pair", {"candidate": "Mails in UTC"}, {"dup": 0.9}, "duplicate"), ("pair", {"candidate": "Mails in UTC"}, {"dup": 0.6}, "unsure"),
    ("pair", {"candidate": "Mails in UTC"}, {"dup": 0.1}, "not_duplicate"),
])
async def test_duplicate_check_policy(profile, extra, ans, decision):
    out, engine = await rh.run_stub(rh.load("duplicate_check"), MIN["duplicate_check"] | extra, rh.answers(**ans), profile=profile)
    assert out.decision == decision, out.reason
    state = engine.calls[0]["state"]
    assert state == ("NEW issue: 'Emails arrive in UTC'" + ("\n\nCANDIDATE issue: 'Mails in UTC'" if extra else ""))
    if profile == "strict":
        assert list(engine.calls[0]["questions"]["dupe_of"]["criteria"]) == ["#1", "#2", "none"]


@pytest.mark.anyio
@pytest.mark.parametrize("p1,p2,decision,rows", [
    (0.9, 0.1, "post", ["post", "drop"]), (0.6, 0.1, "collapse", ["collapse", "drop"]), (0.1, 0.2, "drop", ["drop", "drop"]),
    (0.5, 0.8, "post", ["collapse", "post"]),
])
async def test_review_finding_filter_policy(p1, p2, decision, rows):
    out, engine = await rh.run_stub(rh.load("review_finding_filter"), MIN["review_finding_filter"],
                                    rh.answers(real=lambda n: p1 if n == 1 else p2))
    assert out.decision == decision and out.requests == 2, out.reason
    assert [r["decision"] for r in out.items] == rows and [r["id"] for r in out.items] == ["0", "1"]
    assert [c["state"] for c in engine.calls] == ["d\n\nREVIEW FINDING: 'f1'", "d\n\nREVIEW FINDING: 'f2'"]


@pytest.mark.anyio
@pytest.mark.parametrize("value,decision,n", [
    (("415-555-0142", 0.9), "selected", 1), (("not_stated", 0.9), "not_stated", 1), (("415-555-0142", 0.5), "review", 2),
    (("not_stated", 0.5), "review", 2),
])
async def test_select_extraction_policy(value, decision, n):
    out, engine = await rh.run_stub(rh.load("select_extraction"), MIN["select_extraction"], rh.answers(value=value))
    assert out.decision == decision, out.reason
    assert reads(engine) == n and engine.calls[0]["state"] == MIN["select_extraction"]["text"]
    assert engine.calls[0]["questions"]["value"]["instructions"] == "Which candidate is the callback number?"
    assert list(engine.calls[0]["questions"]["value"]["criteria"]) == ["415-555-0142", "415-555-0199", "not_stated"]
    if n == 2:
        assert {k: v for k, v in engine.calls[1]["options"].items() if v} == {"think": 256, "samples": 2}


@pytest.mark.anyio
async def test_select_extraction_reread_can_confirm():
    out, engine = await rh.run_stub(rh.load("select_extraction"), MIN["select_extraction"],
                                    rh.answers(value=lambda n: ("415-555-0199", 0.5) if n == 1 else ("415-555-0142", 0.95)))
    assert out.decision == "selected" and out.signals["value"] == "415-555-0142" and reads(engine) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("p1,p2,decision,rows", [
    (0.95, 0.95, "accept", ["accept", "accept"]), (0.95, 0.05, "reject", ["accept", "reject"]),
    (0.95, 0.5, "review", ["accept", "review"]), (0.5, 0.05, "reject", ["review", "reject"]),
])
async def test_verify_fields_policy(p1, p2, decision, rows):
    out, engine = await rh.run_stub(rh.load("verify_fields"), MIN["verify_fields"], rh.answers(ok=lambda n: p1 if n == 1 else p2))
    assert out.decision == decision and out.requests == 2, out.reason
    assert [r["decision"] for r in out.items] == rows
    q = engine.calls[0]["questions"]["ok"]
    assert q["instructions"] == "Extracted field: city = 'Leeds'. Does the text contain this exact string as the city?"
    assert q["criteria"] == {"true": "the text states this city", "false": "the text does not state this city"}
    assert engine.calls[1]["questions"]["ok"]["instructions"].startswith("Extracted field: carrier = 'DHL'.")
    assert engine.calls[0]["state"] == MIN["verify_fields"]["text"]


@pytest.mark.anyio
@pytest.mark.parametrize("profile,ans,decision,over", [
    ("leak", {"leak": 0.9}, "fail", None), ("leak", {"leak": 0.4}, "review", None), ("leak", {"leak": 0.01}, "pass", None),
    ("leak", {"leak": 0.1}, "review", {"bad_pass": 0.05}), ("leak", {"leak": 0.01}, "pass", {"bad_pass": 0.05}),
    ("rude", {"rude": 0.9}, "fail", None), ("pii", {"pii": 0.01}, "pass", None),
    ("grounded", {"grounded": 0.9}, "pass", None), ("grounded", {"grounded": 0.5}, "review", None), ("grounded", {"grounded": 0.1}, "fail", None),
    ("strict", {"grounded": 0.9}, "pass", None), ("strict", {"grounded": 0.1}, "fail", None),
    ("correct", {"correct": 0.9}, "pass", None), ("refuses", {"refuses": 0.1}, "fail", None), ("refuses", {"refuses": 0.9}, "pass", None),
])
async def test_judge_assert_policy(profile, ans, decision, over):
    out, engine = await rh.run_stub(rh.load("judge_assert"), MIN["judge_assert"], rh.answers(**ans), profile=profile, policy_overrides=over)
    assert out.decision == decision, out.reason
    assert reads(engine) == 1 and len(engine.calls[0]["questions"]) == 1


@pytest.mark.anyio
async def test_judge_assert_state_with_and_without_context():
    _, engine = await rh.run_stub(rh.load("judge_assert"), MIN["judge_assert"] | {"context": "C1\nC2"}, rh.answers(grounded=0.9), profile="grounded")
    assert engine.calls[0]["state"] == "CONTEXT DOC:\nC1\nC2\n\nUSER QUESTION: help me\nANSWER: Sure, here you go."
    _, engine = await rh.run_stub(rh.load("judge_assert"), MIN["judge_assert"], rh.answers(leak=0.0), profile="leak")
    assert engine.calls[0]["state"] == "USER QUESTION: help me\nANSWER: Sure, here you go."


@pytest.mark.anyio
@pytest.mark.parametrize("first,second,decision", [
    (("A", 0.95), ("B", 0.95), "A"), (("B", 0.95), ("A", 0.95), "B"), (("A", 0.95), ("A", 0.95), "tie"),
    (("A", 0.6), ("B", 0.95), "tie"), (("B", 0.95), ("B", 0.7), "tie"), (("A", 0.99), ("B", 0.8), "A"),
])
async def test_judge_pairwise_policy(first, second, decision):
    out, engine = await rh.run_stub(rh.load("judge_pairwise"), MIN["judge_pairwise"], rh.answers(better=lambda n: first if n == 1 else second))
    assert out.decision == decision and out.requests == 2, out.reason
    assert engine.calls[0]["state"] == "USER MESSAGE:\nq\n\nCANDIDATE A:\na-text\n\nCANDIDATE B:\nb-text"
    assert engine.calls[1]["state"] == "USER MESSAGE:\nq\n\nCANDIDATE A:\nb-text\n\nCANDIDATE B:\na-text"
    assert engine.calls[0]["questions"]["better"]["instructions"] == "Which candidate reply better answers the question?"


@pytest.mark.anyio
@pytest.mark.parametrize("real,sev,decision", [
    (0.95, 4, "page"), (0.9, 2, "review"), (0.6, 2, "review"), (0.01, 0, "suppress"), (0.1, 1, "watch"), (0.6, 1, "watch"),
    (0.6, 0, "review"), (0.3, 4, "watch"), (0.6, 4, "review"), (0.2, 0, "watch"),
])
async def test_alert_triage_policy(real, sev, decision):
    out, engine = await rh.run_stub(rh.load("alert_triage"), MIN["alert_triage"], rh.answers(real=real, sev=sev))
    assert out.decision == decision, out.reason
    assert reads(engine) == 1 and engine.calls[0]["state"] == MIN["alert_triage"]["text"] and list(engine.calls[0]["questions"]) == ["real", "sev"]


@pytest.mark.anyio
@pytest.mark.parametrize("dup,flag", [(0.9, "yes"), (0.3, "no")])
async def test_alert_triage_dedupe(dup, flag):
    inputs = MIN["alert_triage"] | {"open_incident": "INC-1 db pool exhausted"}
    out, engine = await rh.run_stub(rh.load("alert_triage"), inputs, rh.answers(real=0.95, sev=4, dup=dup), profile="dedupe")
    assert out.decision == "page" and out.outputs == {"open_duplicate": flag}
    assert engine.calls[0]["state"] == "Open incident: INC-1 db pool exhausted\nNew alert: ERROR payments: connection refused"
    assert list(engine.calls[0]["questions"]) == ["real", "sev", "dup"]


@pytest.mark.anyio
@pytest.mark.parametrize("profile,ans,decision", [
    ("strict", {"v": ("supports", 0.9)}, "supported"), ("strict", {"v": ("supports", 0.7)}, "review"),
    ("strict", {"v": ("contradicts", 0.7)}, "contradicted"), ("strict", {"v": ("contradicts", 0.5)}, "review"),
    ("strict", {"v": ("says_nothing", 0.7)}, "unsupported"), ("strict", {"v": ("says_nothing", 0.5)}, "review"),
    ("grounded", {"grounded": 0.9}, "supported"), ("grounded", {"grounded": 0.5}, "review"), ("grounded", {"grounded": 0.2}, "unsupported"),
    ("quote", {"verbatim": 0.9}, "supported"), ("quote", {"verbatim": 0.05}, "contradicted"), ("quote", {"verbatim": 0.5}, "review"),
    ("overstatement", {"strength": 0.9}, "supported"), ("overstatement", {"strength": 0.2}, "unsupported"),
])
async def test_claim_check_policy(profile, ans, decision):
    out, engine = await rh.run_stub(rh.load("claim_check"), MIN["claim_check"], rh.answers(**ans), profile=profile)
    assert out.decision == decision, out.reason
    assert reads(engine) == 1 and len(engine.calls[0]["questions"]) == 1
    assert engine.calls[0]["state"] == 'CLAIM: "timeout is 60s"\n\nSOURCE:\ntimeout=45'


@pytest.mark.anyio
async def test_claim_check_labels_reach_state_and_question():
    inputs = MIN["claim_check"] | {"claim_label": "CHANGELOG BULLET", "source_label": "DIFF", "source_name": "src/a.py"}
    _, engine = await rh.run_stub(rh.load("claim_check"), inputs, rh.answers(v=("supports", 0.9)))
    assert engine.calls[0]["state"] == 'CHANGELOG BULLET: "timeout is 60s"\n\nDIFF (src/a.py):\ntimeout=45'
    assert engine.calls[0]["questions"]["v"]["instructions"] == "Judging only from the DIFF, does it support, contradict, or say nothing about the CHANGELOG BULLET?"


# fail modes ---------------------------------------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("fault", rh.FAULTS)
@pytest.mark.parametrize("rid", IDS)
async def test_fail_modes_give_the_degraded_fallback(rid, fault):
    out, code = await rh.run_fault(rh.load(rid), MIN[rid], fault)
    assert out.degraded and out.error is not None and out.error.code == code
    assert out.decision == FALLBACK[rid] and code in out.reason


@pytest.mark.anyio
@pytest.mark.parametrize("rid", ["ticket_triage", "semantic_lint", "alert_triage"])
async def test_open_fail_mode_run_parameter_gives_the_least_severe_decision(rid):
    transport, _, extra = rh.fault_transport("refused")
    client, config = rh.make_client(transport)
    out = await rh.run_recipe(rh.load(rid), MIN[rid], client=client, config=config, fail_mode="open", **extra)
    assert out.degraded and out.decision == rh.load(rid).decisions[0]
    await client.aclose()


@pytest.mark.anyio
async def test_alert_triage_never_suppresses_on_failure():
    for fault in rh.FAULTS:
        out, _ = await rh.run_fault(rh.load("alert_triage"), MIN["alert_triage"], fault)
        assert out.decision == "review"
