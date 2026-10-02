"""Shared helpers for the recipe tests (P07; read-only for P17/P18): load a builtin, run it on stub answers, on a
captured spec response or over a fault, and run a case file of docs/mcp-skill-spec/tests/cases through run_recipe.
No model, no network except run_case against a live client."""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from typing import Any

import httpx
import stubs

from openjev_mcp.config import Config, load_config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.recipes.engine import Recipe, RecipeOutcome, load_builtin, run_recipe

CASES_DIR = os.path.join(stubs.SPEC_TESTS, "cases")
DEFAULT_CATEGORIES = [{"label": "phishing", "description": "tries to trick the reader into revealing credentials, paying, or clicking a deceptive link"},
                      {"label": "spam", "description": "unsolicited bulk promotion or advertising"},
                      {"label": "harassment", "description": "insults, threats or abuse aimed at a person or group"}]
FAULTS = ("refused", "overloaded", "deadline", "expired")


def load(recipe_id: str) -> Recipe:
    return load_builtin(recipe_id)


def make_client(transport, **env) -> tuple[OpenJevClient, Config]:
    config = load_config({"OPENJEV_MCP_RETRIES": "0", **env})
    return OpenJevClient(config, transport=transport), config


def cases_of(file: str) -> dict[str, dict]:
    """id -> case of a case file ('tests/cases/03-x.json', '03-x.json' or a recipe.test_file)."""
    with open(os.path.join(CASES_DIR, os.path.basename(file))) as f:
        return {c["id"]: c for c in json.load(f)["cases"]}


def captured_request(case_id: str) -> dict:
    return next(c for c in stubs._load_cases()[0] if c["id"] == case_id)["request"]


def answer(q: Mapping, value: Any = None, p: float = 0.9) -> dict:
    """One server-shaped answer. noul: value is the probability; score: the score; choice: the option (p = its probability)."""
    kind = q["type"]
    if kind == "noul":
        return {"type": "noul", "noul": 0.01 if value is None else value}
    if kind == "score":
        n = len(q["criteria"])
        top = min(n - 1, round(value or 0))
        probs = {str(i): (0.97 if i == top else 0.03 / (n - 1)) for i in range(n)}
        return {"type": "score", "score": float(value or 0), "legend": {str(i): c for i, c in enumerate(q["criteria"])},
                "probabilities": probs, "confidence": 0.9}
    keys = list(q["criteria"])
    pick = keys[0] if value is None else value
    probs = {k: (p if k == pick else (1 - p) / (len(keys) - 1)) for k in keys}
    return {"type": "choice", "choice": pick, "probabilities": probs, "confidence": p}


def answers(**by_qid) -> Callable:
    """StubEngine answers: by_qid maps a question id to a value (noul probability, score, option) or a tuple
    (option, p). A callable value gets (call_number) for per-read answers. Unnamed questions: noul 0.01, score 0,
    first option 0.9."""
    n = {"calls": 0}

    def build(questions, state, options):
        n["calls"] += 1
        out = {}
        for qid, q in questions.items():
            v = by_qid.get(qid)
            if callable(v):
                v = v(n["calls"])
            out[qid] = answer(q, *(v if isinstance(v, tuple) else (v,)))
        return out
    return build


def captured_answers(case_id: str, **fill) -> Callable:
    """StubEngine answers replaying the captured answers of a spec example, plus `fill` for the questions it lacks."""
    got = stubs.captured(case_id)["answers"]

    def build(questions, state, options):
        out = {}
        for qid, q in questions.items():
            out[qid] = got[qid] if qid in got else answer(q, *(fill[qid] if isinstance(fill.get(qid), tuple) else (fill.get(qid),)))
        return out
    return build


async def run_stub(recipe: Recipe, inputs: Mapping, ans: Callable | None = None, *, env: Mapping | None = None,
                   engine: stubs.StubEngine | None = None, **kw) -> tuple[RecipeOutcome, stubs.StubEngine]:
    engine = engine or stubs.StubEngine(answers=ans)
    client, config = make_client(stubs.asgi_transport(stubs.openjev_app(engine=engine)), **(env or {}))
    try:
        return await run_recipe(recipe, inputs, client=client, config=config, **kw), engine
    finally:
        await client.aclose()


async def run_replay(recipe: Recipe, inputs: Mapping, case_ids, **kw) -> tuple[RecipeOutcome, Any]:
    """Run against the captured responses of spec examples; a request that differs from every captured body fails."""
    transport = stubs.replay_transport(list(case_ids))
    client, config = make_client(transport)
    try:
        return await run_recipe(recipe, inputs, client=client, config=config, **kw), transport
    finally:
        await client.aclose()


def fault_transport(fault: str):
    """(transport, error code, run_recipe kwargs) of a fault: refused, overloaded, deadline, expired."""
    if fault == "refused":
        return stubs.fault_transport(exc=httpx.ConnectError("refused")), "OJ_UNREACHABLE", {}
    if fault == "overloaded":
        body = b'{"detail":{"error_type":"overloaded_error","message":"busy"}}'
        return stubs.fault_transport(529, headers={"content-type": "application/json"}, body=body), "OJ_OVERLOADED", {}
    if fault == "deadline":
        return stubs.fault_transport(200, body=b"{}", delay_s=2.0), "OJ_TIMEOUT", {"deadline_ms": 100}
    return stubs.fault_transport(200, body=b"{}"), "OJ_TIMEOUT", {"deadline_ms": 0}


async def run_fault(recipe: Recipe, inputs: Mapping, fault: str, **kw) -> tuple[RecipeOutcome, str]:
    transport, code, extra = fault_transport(fault)
    client, config = make_client(transport)
    try:
        return await run_recipe(recipe, inputs, client=client, config=config, **extra, **kw), code
    finally:
        await client.aclose()


def inputs_from_case(recipe_id: str, case: Mapping) -> dict | None:
    """Recipe inputs rebuilt from a case's state (None when the case is not shaped like this recipe's state)."""
    req = case.get("request") or {}
    state, qs = req.get("state"), req.get("questions", {})
    if not isinstance(state, str):
        return None

    def opts(qid):
        crit = (qs.get(qid) or {}).get("criteria") or {}
        return [(k, v) for k, v in crit.items() if k != "none"]

    if recipe_id == "command_gate":
        m = re.fullmatch(r"Task requested by the user: ([^\n]*)\n(?:([^\n]*)\n)?Proposed shell command: (.*)", state, re.S)
        if not m:
            return None
        out = {"task": m[1], "command": m[3]}
        if m[2]:
            out["context"] = m[2]
        if "destructive_regenerable" in qs:
            out["profile"] = "lenient"
        return out
    if recipe_id == "act_or_ask":
        m = re.fullmatch(r"Coding agent session\.\nUser: (.*)", state, re.S)
        return {"request": m[1]} if m else None
    if recipe_id == "injection_screen":
        m = re.fullmatch(r"\[([^\n]*)\]\n(.*)", state, re.S)
        return {"source": m[1], "text": m[2]} if m else None
    if recipe_id == "done_gate":
        m = re.fullmatch(r"AGENT TURN REPORT\nUser task: ([^\n]*)\nTimeline \(chronological, last line is most recent\):\n(.*)\n"
                         r"Final assistant message: (.*)", state, re.S)
        return {"task": m[1], "timeline": m[2].split("\n"), "final_message": m[3]} if m else None
    if recipe_id == "moderation":
        channel, _, text = state.partition("\n")
        cats = [{"label": k, "description": v} for k, v in opts("category")] or DEFAULT_CATEGORIES
        return {"channel": channel, "text": text, "categories": cats} if text else None
    if recipe_id == "model_routing":
        m = re.fullmatch(r"Task summary: (.*)", state, re.S)
        return {"summary": m[1]} if m else None
    if recipe_id == "skill_selection":
        m = re.fullmatch(r"User prompt: (.*)", state, re.S)
        roster = [{"id": k, "description": v} for k, v in opts("skill")]
        return {"prompt": m[1], "roster": roster} if m and roster else None
    return None


# recipe -> (question whose choice is the decision, ...): a case expecting that choice also fixes the decision
DECISION_QUESTION = {"command_gate": "verdict"}


def check_expect(recipe_id: str, outcome: RecipeOutcome, expect: Mapping) -> tuple[list[str], int]:
    """(failures, checked) of a case's expect.answers against the outcome's derived answers (questions the recipe
    did not ask are skipped); for command_gate the verdict expectation is also held against the decision."""
    fails, checked = [], 0
    dq = DECISION_QUESTION.get(recipe_id)
    cond = (expect.get("answers") or {}).get(dq) or {}
    allowed = [cond["choice"]] if "choice" in cond else cond.get("choice_in", [])
    if allowed:
        checked += 1
        if outcome.decision not in allowed:
            fails.append(f"decision {outcome.decision!r} not in {allowed}")
    for qid, cond in (expect.get("answers") or {}).items():
        a = outcome.answers.get(qid)
        if a is None:
            continue
        for key, want in cond.items():
            checked += 1
            if key in ("noul_gte", "noul_lte"):
                got = a.get("p")
                ok = got is not None and (got >= want if key == "noul_gte" else got <= want)
            elif key in ("score_gte", "score_lte"):
                got = a.get("score")
                ok = got is not None and (got >= want if key == "score_gte" else got <= want)
            elif key == "choice":
                got, ok = a.get("choice"), a.get("choice") == want
            elif key == "choice_in":
                got, ok = a.get("choice"), a.get("choice") in want
            else:
                checked -= 1
                continue
            if not ok:
                fails.append(f"{qid}.{key}={want!r} got {got!r}")
    return fails, checked


async def run_case(recipe: Recipe, case: Mapping, client: OpenJevClient, config: Config) -> dict:
    """One case through run_recipe (or, for a case that expects an HTTP error status, the raw request).
    Returns {id, status: pass|fail|skip, detail, outcome?}."""
    cid, expect = case["id"], case.get("expect") or {}
    if expect.get("status", 200) != 200:
        try:
            await client.systemone(case["request"], timeout_ms=config.timeout_ms)
        except ToolError as err:
            ok = err.http_status == expect["status"]
            return {"id": cid, "status": "pass" if ok else "fail", "detail": f"raw request: http {err.http_status} ({err.code})"}
        return {"id": cid, "status": "fail", "detail": f"raw request: expected http {expect['status']}, got 200"}
    inputs = inputs_from_case(recipe.id, case)
    if inputs is None:
        return {"id": cid, "status": "skip", "detail": "state is not shaped like this recipe's"}
    outcome = await run_recipe(recipe, inputs, client=client, config=config)
    if outcome.degraded:
        return {"id": cid, "status": "fail", "detail": outcome.reason, "outcome": outcome}
    fails, checked = check_expect(recipe.id, outcome, expect)
    if not checked:
        return {"id": cid, "status": "skip", "detail": "no expectation applies to this recipe's questions", "outcome": outcome}
    detail = f"{outcome.decision}: {outcome.reason}" if not fails else "; ".join(fails)
    return {"id": cid, "status": "fail" if fails else "pass", "detail": detail, "outcome": outcome}
