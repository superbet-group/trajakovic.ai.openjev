"""calibrate tool (spec 2.15): ex-cal-1..7 replay, sources, from_batch parity, store/compare_to, chunking, think, holdout."""
from __future__ import annotations

import json
import os
import time

import httpx
import pytest
import stubs

from openjev_mcp import audit_store
from openjev_mcp.batch import store
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache, default_limits
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.prompts import PromptError
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools import calibrate as C

pytestmark = pytest.mark.anyio

SPEC_CASES = os.path.join(stubs.SPEC_TESTS, "cases", "00-spec-examples.json")
Q = {"q": {"type": "noul", "instructions": "Is this a billing issue?", "criteria": {"true": "about charges", "false": "anything else"}}}


def make_ctx(transport, tmp_path, **cfg_kw):
    config = Config(base_url="http://oj.test", roots=(str(tmp_path.resolve()),), audit_dir=str(tmp_path / "audits"), **cfg_kw)
    client = OpenJevClient(config, transport=transport, jitter=lambda: 0.0)
    limits = LimitsCache(client, ttl_s=1e9)
    limits._limits, limits._fetched = default_limits(), time.monotonic()

    return ToolContext(config, client, limits, ProgressEmitter(None, min_interval_s=0.0), None)


def spec_cases():
    cases = [c for c in json.load(open(SPEC_CASES))["cases"] if c["id"].startswith("ex-cal-")]
    return sorted(cases, key=lambda c: c["id"])


def cal_args():
    cases = spec_cases()
    return {"questions": cases[0]["request"]["questions"], "options": {"samples": 1},
            "examples": [{"id": c["id"], "state": c["request"]["state"], "label": {"escalate": c["label"]}} for c in cases]}


def qa(p):
    return {"q": {"type": "noul", "noul": p}}


def echo(table):
    """Transport answering by the trailing number of the state: table[n] = p (or a raw answers dict)."""
    def handler(request):
        body = json.loads(request.content)
        v = table[int(body["state"].split()[-1])]
        return httpx.Response(200, json={"model": "m-1", "answers": v if isinstance(v, dict) else qa(v), "usage": {}})
    return stubs.RecordingTransport(handler)


def examples(labels):
    return [{"id": f"r{i}", "state": f"text {i}", "label": {"q": lab}} for i, lab in enumerate(labels, 1)]


async def test_ex_cal_replay_reproduces_the_report(tmp_path):
    ctx = make_ctx(stubs.replay_transport(), tmp_path)
    out = await C.calibrate(ctx, cal_args())
    assert out["model_resolved"] == "openjev-0.1" and out["n"] == 7 and out["question_hash"].startswith("sha256:")
    pq = out["per_question"]["escalate"]
    want = {"type": "noul", "n_pos": 3, "n_neg": 4, "accuracy_at_0.5": 1.0, "separable": True, "max_negative": 0.002, "min_positive": 0.9994,
            "gap": 0.9974, "t_fit": 0.5007, "suggested_band": {"no_at": 0.05, "yes_at": 0.95}, "overlap_ids": [],
            "most_borderline": "ex-cal-7", "zero_error_upper_bound_95": 0.429}
    assert {k: pq[k] for k in want} == want
    assert "precision_coverage" not in pq
    assert out["items"] == [{"id": "ex-cal-1", "label": True, "p": 0.9994}, {"id": "ex-cal-2", "label": True, "p": 0.9996},
                            {"id": "ex-cal-3", "label": True, "p": 0.9997}, {"id": "ex-cal-4", "label": False, "p": 3.8e-05},
                            {"id": "ex-cal-5", "label": False, "p": 3.4e-05}, {"id": "ex-cal-6", "label": False, "p": 0.00022},
                            {"id": "ex-cal-7", "label": False, "p": 0.002}]
    assert out["warnings"] == ["n=7: smoke test only; 0 errors in 7 bounds the error rate at ~43% (rule of three)"]
    assert len(pq["calibration"]["bins"]) == 10 and len(pq["distributions"]["entropy_hist"]) == 20


async def test_question_hash_ignores_key_order(tmp_path):
    a = cal_args()
    out1 = await C.calibrate(make_ctx(stubs.replay_transport(), tmp_path), a)
    q = a["questions"]["escalate"]
    b = {**a, "questions": {"escalate": {k: q[k] for k in reversed(list(q))}}}
    out2 = await C.calibrate(make_ctx(stubs.replay_transport(), tmp_path), b)
    assert out1["question_hash"] == out2["question_hash"]


async def test_case_file_source(tmp_path):
    cases = spec_cases()
    path = tmp_path / "cal.json"
    path.write_text(json.dumps({"use_case": "x", "cases": cases}))
    out = await C.calibrate(make_ctx(stubs.replay_transport(), tmp_path), {"case_file": str(path)})
    ref = await C.calibrate(make_ctx(stubs.replay_transport(), tmp_path), cal_args())
    assert out["per_question"]["escalate"]["gap"] == ref["per_question"]["escalate"]["gap"] and out["n"] == 7
    assert out["question_hash"] == ref["question_hash"]


async def test_case_file_labels_choice_and_lte(tmp_path):
    q = {"c": {"type": "choice", "instructions": "Which kind?", "criteria": {"a": "x", "b": "y", "other": "z"}}}
    cf = {"cases": [{"id": "k1", "request": {"state": "t 1", "questions": q}, "expect": {"answers": {"c": {"choice": "a"}}}},
                    {"id": "k2", "request": {"state": "t 2", "questions": q}, "expect": {"answers": {"c": {"choice_in": ["a", "b"]}}}}]}
    items, qs, labels, opts, warns = C._case_file(str(_w(tmp_path, cf)), Config(roots=(str(tmp_path.resolve()),)))
    assert labels == {"k1": {"c": "a"}} and len(items) == 1 and warns and qs == q


def _w(tmp_path, doc, name="c.json"):
    p = tmp_path / name
    p.write_text(json.dumps(doc))
    return p


async def test_label_validation(tmp_path):
    ctx = make_ctx(echo({1: 0.9}), tmp_path)
    for bad in ({"examples": examples(["maybe"]), "questions": Q}, {"examples": [{"state": "t 1", "label": {"zz": True}}], "questions": Q},
                {"questions": Q}, {"questions": Q, "recipe": "x", "examples": examples([True])}, {}, {"case_file": "a.json", "examples": []}):
        with pytest.raises(ToolError) as e:
            await C.calibrate(ctx, bad)
        assert e.value.code == "OJ_INVALID_INPUT"


async def test_yes_no_strings_and_overlap_report(tmp_path):
    ps = {1: 0.9, 2: 0.7, 3: 0.4, 4: 0.1}
    out = await C.calibrate(make_ctx(echo(ps), tmp_path),
                            {"questions": Q, "examples": examples(["yes", "no", "1", "0"]), "options": {"samples": 1}})
    pq = out["per_question"]["q"]
    assert pq["separable"] is False and pq["overlap_ids"] == ["r3", "r2"] and pq["accuracy_at_0.5"] == 0.5
    pc = {r["t"]: r for r in pq["precision_coverage"]}
    assert pc[0.5]["precision"] == 0.5 and pc[0.5]["coverage"] == 0.5 and len(pc) == 19
    assert pq["zero_error_upper_bound_95"] is None


async def test_choice_and_score_questions(tmp_path):
    qs = {"c": {"type": "choice", "instructions": "Which kind?", "criteria": {"a": "x", "b": "y"}},
          "s": {"type": "score", "instructions": "How bad?", "criteria": ["fine", "meh", "bad"]}}

    def handler(request):
        n = int(json.loads(request.content)["state"].split()[-1])
        lvl = {1: 0, 2: 1, 3: 2}[n]
        probs = {str(i): 0.8 if i == lvl else 0.1 for i in range(3)}
        ch = "a" if n < 3 else "b"
        return httpx.Response(200, json={"model": "m-1", "answers": {
            "c": {"type": "choice", "choice": ch, "probabilities": {"a": 0.9 if ch == "a" else 0.1, "b": 0.1 if ch == "a" else 0.9}, "confidence": 0.9},
            "s": {"type": "score", "score": float(lvl), "probabilities": probs, "confidence": 0.8}}, "usage": {}})

    exs = [{"id": f"r{i}", "state": f"t {i}", "label": {"c": c, "s": s}} for i, c, s in ((1, "a", 0), (2, "b", 1), (3, "b", 2))]
    out = await C.calibrate(make_ctx(stubs.RecordingTransport(handler), tmp_path), {"questions": qs, "examples": exs})
    c, s = out["per_question"]["c"], out["per_question"]["s"]
    assert c["accuracy"] == pytest.approx(0.6667, abs=1e-4) and c["confusion"] == {"a": {"a": 1}, "b": {"a": 1, "b": 1}}
    assert s["accuracy"] == 1.0 and s["ladder_monotonic"] is True and s["distributions"]["max_entropy"] == pytest.approx(1.0986, abs=1e-4)
    assert out["items"][0]["label"] == {"c": "a", "s": 0} and out["items"][0]["p"]["c"] == 0.9
    assert c["most_borderline"] == "r1" and c["calibration"]["bins"][8]["count"] == 3


async def test_from_batch_equals_examples_run_with_no_request(tmp_path):
    table = {1: 0.97, 2: 0.9, 3: 0.2, 4: 0.01, 5: 0.6}
    labs = [True, True, False, False, False]
    exs = examples(labs)
    ctx = make_ctx(echo(table), tmp_path)
    ref = await C.calibrate(ctx, {"questions": Q, "examples": exs, "options": {"samples": 1}})
    # an equivalent finished batch: header + ok rows with derived answers
    qh = store.question_hash(Q)
    out = tmp_path / "b.jsonl"
    lines = [store.make_header(run_id="r", question_hash=qh)]
    for i, p in table.items():
        lines.append({"index": i, "id": f"r{i}", "status": "ok", "model": "m-1", "answers": {"q": {"type": "noul", "p": p, "band": "yes", "margin": 0}}})
    out.write_text("".join(json.dumps(x) + "\n" for x in lines))
    lab = tmp_path / "labels.csv"
    lab.write_text("id,esc\n" + "".join(f"r{i},{str(l).lower()}\n" for i, l in enumerate(labs, 1)))
    quiet = make_ctx(stubs.fail_on_request_transport(), tmp_path)
    got = await C.calibrate(quiet, {"from_batch": {"output_path": str(out), "labels_path": str(lab), "label_fields": {"q": "esc"}}})
    for k in ("model_resolved", "n", "per_question", "items", "question_hash"):
        assert got[k] == ref[k], k


async def test_from_batch_jsonl_labels_and_missing(tmp_path):
    out = tmp_path / "b.jsonl"
    out.write_text(json.dumps(store.make_header(run_id="r", question_hash="sha256:abc")) + "\n" + json.dumps(
        {"index": 1, "id": "a", "status": "ok", "model": "m", "answers": {"q": {"type": "noul", "p": 0.9}}}) + "\n")
    lab = tmp_path / "l.jsonl"
    lab.write_text('{"id": "a", "esc": "yes"}\n{"id": "zz", "esc": "no"}\n')
    got = await C.calibrate(make_ctx(stubs.fail_on_request_transport(), tmp_path),
                            {"from_batch": {"output_path": str(out), "labels_path": str(lab), "label_fields": {"q": "esc"}}})
    assert got["n"] == 1 and got["question_hash"] == "sha256:abc" and any("not in the batch output" in w for w in got["warnings"])
    with pytest.raises(ToolError):
        await C.calibrate(make_ctx(stubs.fail_on_request_transport(), tmp_path),
                          {"from_batch": {"output_path": str(out), "labels_path": str(lab), "label_fields": {"q": "nope"}}})


async def test_store_and_drift_on_changed_model(tmp_path):
    rec = tmp_path / "audit.json"
    ctx = make_ctx(stubs.replay_transport(), tmp_path)
    first = await C.calibrate(ctx, {**cal_args(), "store": str(rec)})
    data = json.loads(rec.read_text())
    assert audit_store.is_record(data) and data["items"][0]["model"] == "openjev-0.1" and first["stored"] == str(rec.resolve())
    assert (tmp_path / "audits" / (first["question_hash"][7:] + ".json")).exists()
    # same questions on a new resolved model with one flipped answer
    prev = json.loads(rec.read_text())
    prev["model_resolved"] = "openjev-0.0"
    prev["items"][6]["p"] = 0.9
    rec.write_text(json.dumps(prev))
    second = await C.calibrate(make_ctx(stubs.replay_transport(), tmp_path), {**cal_args(), "compare_to": str(rec), "store": str(rec)})
    d = second["drift"]
    assert d["model_changed"] is True and d["previous_model"] == "openjev-0.0" and d["model"] == "openjev-0.1"
    assert d["flipped_ids"] == ["ex-cal-7"] and d["gap_delta"]["escalate"] is not None
    assert any("resolved model changed" in w for w in second["warnings"])
    assert json.loads(rec.read_text())["model_resolved"] == "openjev-0.1"


async def test_store_refuses_non_record(tmp_path):
    victim = tmp_path / "notes.json"
    victim.write_text('{"keep": "me"}')
    with pytest.raises(ToolError) as e:
        await C.calibrate(make_ctx(stubs.replay_transport(), tmp_path), {**cal_args(), "store": str(victim)})
    assert e.value.code == "OJ_INVALID_INPUT" and victim.read_text() == '{"keep": "me"}'
    with pytest.raises(ToolError):
        await C.calibrate(make_ctx(stubs.replay_transport(), tmp_path), {**cal_args(), "compare_to": str(victim)})


async def test_chunked_run_equals_single_call(tmp_path):
    table = {i: 0.95 if i % 2 else 0.05 for i in range(1, 8)}
    labs = [bool(i % 2) for i in range(1, 8)]
    base = {"questions": Q, "examples": examples(labs), "options": {"samples": 1}}
    ref = await C.calibrate(make_ctx(echo(table), tmp_path), base)
    ctx = make_ctx(echo(table), tmp_path)
    args = {**base, "max_items_per_call": 3}
    first = await C.calibrate(ctx, args)
    assert first["next_cursor"] and first["per_question"] == {} and first["status"]["done"] == 3
    second = await C.calibrate(ctx, {**args, "cursor": first["next_cursor"]})
    assert "next_cursor" in second
    last = await C.calibrate(ctx, {**args, "cursor": second["next_cursor"], "concurrency": 2})
    assert "next_cursor" not in last
    for k in ("n", "per_question", "items", "question_hash"):
        assert last[k] == ref[k], k
    with pytest.raises(ToolError):
        await C.calibrate(ctx, {**args, "options": {"samples": 2}, "cursor": first["next_cursor"]})


async def test_think_reads_twice_and_flags_disagreement(tmp_path):
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        n = seen[body["state"]] = seen.get(body["state"], 0) + 1
        p = 0.9 if body["state"] == "text 1" else (0.9 if n == 1 else 0.1)
        return httpx.Response(200, json={"model": "m-1", "answers": qa(p), "usage": {}})

    out = await C.calibrate(make_ctx(stubs.RecordingTransport(handler), tmp_path),
                            {"questions": Q, "examples": examples([True, True]), "options": {"think": 64}})
    assert seen == {"text 1": 2, "text 2": 2}
    assert out["per_question"]["q"]["non_reproducible"] == ["r2"] and out["items"][1]["non_reproducible"] is True
    assert any("non-reproducible" in w for w in out["warnings"])


async def test_holdout_is_excluded_from_the_fit(tmp_path):
    labs = [True, False] * 6
    table = {i: 0.9 if labs[i - 1] else 0.1 for i in range(1, 13)}
    out = await C.calibrate(make_ctx(echo(table), tmp_path), {"questions": Q, "examples": examples(labs), "holdout": 0.5})
    held = out["holdout"]["ids"]
    assert held and out["holdout"]["n"] == len(held) and out["per_question"]["q"]["n"] == 12 - len(held)
    assert {i["id"] for i in out["items"] if i.get("holdout")} == set(held)
    with pytest.raises(ToolError):
        await C.calibrate(make_ctx(echo(table), tmp_path), {"questions": Q, "examples": examples(labs), "holdout": 1})


async def test_target_and_errors(tmp_path):
    ps = {1: 0.9, 2: 0.7, 3: 0.4, 4: 0.1}
    out = await C.calibrate(make_ctx(echo(ps), tmp_path),
                            {"questions": Q, "examples": examples([True, False, True, False]), "target": {"max_errors": 0}})
    assert out["target_met"] is False and out["per_question"]["q"]["target"] == {"met": False, "t": None}
    ok = await C.calibrate(make_ctx(echo(ps), tmp_path),
                           {"questions": Q, "examples": examples([True, True, False, False]), "target": {"max_errors": 0, "min_precision": 1}})
    assert ok["target_met"] is True and ok["per_question"]["q"]["target"]["t"] is not None
    # an unreadable example is left out with a warning
    bad = stubs.fault_transport(500)
    res = await C.calibrate(make_ctx(bad, tmp_path, retries=0), {"questions": Q, "examples": examples([True])})
    assert res["n"] == 0 and any("failed to read" in w for w in res["warnings"])


async def test_recipe_source(tmp_path):
    from openjev_mcp.recipes.registry import load_all
    cfg = Config(roots=(str(tmp_path.resolve()),))
    reg = load_all(cfg)
    rid = reg.ids()[0]
    ctx = make_ctx(stubs.fail_on_request_transport(), tmp_path)
    with pytest.raises(ToolError) as e:
        await C.calibrate(ctx, {"recipe": "no-such-recipe", "examples": [{"state": {}, "label": {"q": True}}]})
    assert e.value.code == "OJ_INVALID_INPUT" and rid in e.value.hint


def test_register_and_prompts(tmp_path):
    from openjev_mcp import schemas
    try:
        spec = C.register(Config())
    finally:   # register mutates the process-wide schema registry; the integration package owns the real registration
        schemas.INPUT_SCHEMAS.pop("calibrate", None)
        schemas.OUTPUT_SCHEMAS.pop("calibrate", None)
    assert spec.name == "calibrate" and spec.annotations["readOnlyHint"] is False and spec.annotations["destructiveHint"] is False
    assert "oneOf" not in spec.input_schema and "from_batch" in spec.input_schema["properties"]
    assert C.AUDIT_QUESTION.name == "audit_question" and [a.name for a in C.AUDIT_QUESTION.arguments] == ["schema_path", "labels_path"]
    assert C.EXPLAIN_ANSWER.name == "explain_answer" and C.EXPLAIN_ANSWER.arguments[0].required


async def test_audit_question_prompt(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps({"questions": Q}))
    (tmp_path / "l.csv").write_text("id,text,q\n1,a,true\n")
    ctx = make_ctx(stubs.fail_on_request_transport(), tmp_path)
    msgs = await C.AUDIT_QUESTION.build({"schema_path": str(tmp_path / "s.json"), "labels_path": str(tmp_path / "l.csv")}, ctx)
    text = msgs[0]["content"]["text"]
    assert msgs[0]["role"] == "user" and C.cal.question_hash(Q) in text and '"q": "q"' in text and "| q | noul |" in text
    (tmp_path / "bad.json").write_text("[1]")
    with pytest.raises(PromptError):
        await C.AUDIT_QUESTION.build({"schema_path": str(tmp_path / "bad.json"), "labels_path": str(tmp_path / "l.csv")}, ctx)


async def test_explain_answer_prompt(tmp_path):
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"request_id": "req_7", "tool": "ask", "answers": {"q": {"type": "score", "level": 0}}}) + "\n")
    ctx = make_ctx(stubs.fail_on_request_transport(), tmp_path, log_path=str(log))
    got = (await C.EXPLAIN_ANSWER.build({"answer": "req_7"}, ctx))[0]["content"]["text"]
    assert "req_7" in got and "0-indexed" in got and "no separate confidence" in got and "K" in got
    pasted = (await C.EXPLAIN_ANSWER.build({"answer": '{"answers": {"q": {"type": "noul", "noul": 0.9}}}'}, ctx))[0]["content"]["text"]
    assert "noul" in pasted
    for bad in ("req_missing", "{not json"):
        with pytest.raises(PromptError):
            await C.EXPLAIN_ANSWER.build({"answer": bad}, ctx)
    with pytest.raises(PromptError):
        await C.EXPLAIN_ANSWER.build({"answer": "req_7"}, make_ctx(stubs.fail_on_request_transport(), tmp_path))


async def test_concurrent_identical_chunked_runs_refused(tmp_path):
    table = {i: 0.5 for i in range(1, 8)}
    args = {"questions": Q, "examples": examples([True] * 7), "options": {"samples": 1}, "max_items_per_call": 3}
    ctx = make_ctx(echo(table), tmp_path)
    with C._Work(ctx.config, "r").lock():
        pass
    run = C.wire.canonical_hash({"calibrate": C.cur.args_hash(dict(args))})[7:23]
    with C._Work(ctx.config, run).lock():   # another call holds this run
        with pytest.raises(ToolError, match="in progress"):
            await C.calibrate(ctx, args)
    assert (await C.calibrate(ctx, args))["next_cursor"]   # released afterwards
