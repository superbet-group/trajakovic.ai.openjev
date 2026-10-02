"""g05 calibrate and audits (T041-T048): claude -p -> own openjev-mcp -> real OpenJev. Ground truth: cases 00 ex-cal-1..7, 24 cal-* (no cal-10)."""
import csv
import json
import sys

import pytest

import cl_assert as A
from cl_cases import case_from_data, run_case
from cl_env import CASES_DIR, REPO

sys.path.insert(0, str(REPO / "mcp"))
from openjev_mcp.batch.stats import in_audit  # noqa: E402

DATA = CASES_DIR / "g05_calibrate.json"
SPEC_CASES = REPO / "docs/mcp-skill-spec/tests/cases"
EX_CAL = [f"ex-cal-{i}" for i in range(1, 8)]
CAL24 = ["cal-01-labelled-positive", "cal-02-labelled-negative", "cal-03-fit-leak", "cal-03-fit-dataloss", "cal-03-fit-howto", "cal-03-fit-feature"]
HOLDOUT = 0.5


def _cases(name: str) -> dict:
    return {c["id"]: c for c in json.loads((SPEC_CASES / name).read_text(encoding="utf-8"))["cases"]}


def _write_case_file(ctx, name: str, ids: list[str], out: str) -> dict:
    """The run_cases.py file the tool reads, copied entry by entry from the spec case file (never retyped)."""
    src = _cases(name)
    (ctx.cwd / out).write_text(json.dumps({"cases": [src[i] for i in ids]}), encoding="utf-8")
    return src


def _setup_excal(ctx):
    _write_case_file(ctx, "00-spec-examples.json", EX_CAL, "ex-cal.json")


def _seed_record(ctx, store: str):
    _setup_excal(ctx)
    r = ctx.call_tool("calibrate", {"case_file": str(ctx.cwd / "ex-cal.json"), "options": {"samples": 1}, "store": str(ctx.cwd / store)})
    assert not r["is_error"], r["text"][:300]
    ctx.seed["hash"] = r["structured"]["question_hash"]
    ctx.seed["seed_model"] = r["structured"]["model_resolved"]


def _setup_from_batch(ctx):
    src = _write_case_file(ctx, "00-spec-examples.json", EX_CAL, "ex-cal.json")
    with open(ctx.cwd / "items.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "state"])
        w.writerows([i, src[i]["request"]["state"]] for i in EX_CAL)
    labelled = EX_CAL[:6]   # ex-cal-7 is read by the batch but has no label: n counts labelled rows only
    with open(ctx.cwd / "labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "esc"])
        w.writerows([i, str("noul_gte" in src[i]["expect"]["answers"]["escalate"]).lower()] for i in labelled)
    ctx.seed["labelled"] = labelled
    r = ctx.call_tool("batch", {"items_file": {"path": str(ctx.cwd / "items.csv"), "state_field": "state", "id_field": "id"},
                                "questions": src[EX_CAL[0]]["request"]["questions"], "concurrency": 2, "output_path": str(ctx.cwd / "batch.jsonl")})
    assert not r["is_error"] and r["structured"]["status"]["ok"] == 7, r["text"][:300]


def _setup_cal24(ctx):
    _write_case_file(ctx, "24-calibration-threshold-audit.json", CAL24, "cal24.json")


def _setup_drift(ctx):
    _seed_record(ctx, "prev.json")


def _t041(t, ctx):
    c = A.tool_called(t, "calibrate", {"case_file": A.eq(str(ctx.cwd / "ex-cal.json"))}, times=1)[0]
    A.result_ok(c)
    A.result_matches(c, "$.n", 7)
    A.result_matches(c, "$.per_question.escalate.accuracy_at_0.5", A.approx(1.0))
    A.result_matches(c, "$.per_question.escalate.separable", True)
    A.result_matches(c, "$.per_question.escalate.most_borderline", "ex-cal-7")
    A.result_matches(c, "$.model_resolved", A.regex(r"^openjev"))


def _t042(t, ctx):
    store = str(ctx.cwd / "audit.json")
    c = A.tool_called(t, "calibrate", {"store": A.eq(store)}, times=1)[0]
    A.result_ok(c)
    qh = A.result_matches(c, "$.question_hash", A.regex(r"^sha256:[0-9a-f]{64}$"))
    A.result_matches(c, "$.stored", A.regex(r"audit\.json$"))
    for path in (ctx.cwd / "audit.json", ctx.mcp().audit_dir / f"{qh[7:]}.json"):
        assert path.exists(), f"audit record missing: {path}"
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert rec["openjev_mcp"] == "calibrate" and rec["question_hash"] == qh and rec["n"] == 7, f"{path}: {str(rec)[:200]}"


def _t043(t, ctx):
    uri = f"openjev://audits/{ctx.seed['hash']}"
    c = A.tool_called(t, "ReadMcpResourceTool", {"uri": A.eq(uri)}, times=1)[0]
    assert c.result is not None and not c.result.is_error, f"read failed: {c.result and c.result.text[:300]}"
    body = json.loads(c.result.json["contents"][0]["text"])
    assert body["question_hash"] == ctx.seed["hash"] and body["openjev_mcp"] == "calibrate" and body["n"] == 7, str(body)[:300]
    assert body["per_question"]["escalate"]["most_borderline"] == "ex-cal-7"
    e = A.wire_called(t, "resources/read", params={"uri": A.eq(uri)})[0]
    rid = e["req"]["id"]
    res = next(m["result"] for m in e["resp"] if m.get("id") == rid and isinstance(m.get("result"), dict))
    assert res.get("ttlMs") == 0 and res.get("cacheScope") == "private", f"wire cache hint: ttlMs={res.get('ttlMs')!r} cacheScope={res.get('cacheScope')!r}"


def _t044(t, ctx):
    c = A.tool_called(t, "calibrate", {"from_batch.output_path": A.eq(str(ctx.cwd / "batch.jsonl"))}, times=1)[0]
    A.result_ok(c)
    A.result_matches(c, "$.n", len(ctx.seed["labelled"]))
    A.result_matches(c, "len($.items)", len(ctx.seed["labelled"]))
    A.result_matches(c, "$.question_hash", A.regex(r"^sha256:"))
    A.result_matches(c, "$.per_question.escalate.accuracy_at_0.5", A.between(0, 1))
    A.result_matches(c, "$.per_question.escalate.separable", A.is_type(bool))
    A.no_read(t, c)   # from_batch makes no request


def _t045(t, ctx):
    c = A.tool_called(t, "calibrate", {"case_file": A.eq(str(ctx.cwd / "cal24.json"))}, times=1)[0]
    A.result_ok(c)
    held = sorted(i for i in CAL24 if in_audit(0, i, HOLDOUT))
    assert 0 < len(held) < len(CAL24), f"holdout {HOLDOUT} must hold out some but not all ids, got {held}"
    A.result_matches(c, "$.n", len(CAL24))
    A.result_matches(c, "$.holdout.fraction", HOLDOUT)
    A.result_matches(c, "$.holdout.n", len(held))
    A.result_matches(c, "$.holdout.ids", A.set_eq(held))
    A.result_matches(c, "$.per_question.escalate.n", len(CAL24) - len(held))   # fit set excludes the canary
    A.result_matches(c, "$.per_question.escalate.t_fit", A.between(0, 1))
    A.result_matches(c, "$.per_question.escalate.suggested_band", A.exists)
    A.result_matches(c, "$.per_question.escalate.target", A.exists)
    A.result_matches(c, "$.target_met", A.is_type(bool))
    flagged = sorted(i["id"] for i in c.result.json["items"] if i.get("holdout"))
    assert flagged == held, f"items flagged holdout {flagged} != {held}"


def _t046(t, ctx):
    cs = A.tool_called(t, "calibrate", times=3)
    A.cursor_chain(t, "calibrate")
    for c in cs[:2]:
        A.result_matches(c, "$.per_question", A.eq({}))
        A.result_matches(c, "$.next_cursor", A.is_type(str))
        A.result_matches(c, "$.status.total", 7)
    A.result_matches(cs[0], "$.status.done", 3)
    A.result_matches(cs[1], "$.status.done", 6)
    A.result_matches(cs[2], "$.n", 7)
    A.result_matches(cs[2], "$.per_question.escalate.separable", True)
    A.result_matches(cs[2], "$.per_question.escalate.most_borderline", "ex-cal-7")
    A.result_matches(cs[2], "$.next_cursor", A.absent)


def _t047(t, ctx):
    c = A.tool_called(t, "calibrate", {"compare_to": A.eq(str(ctx.cwd / "prev.json"))}, times=1)[0]
    A.result_ok(c)
    A.result_matches(c, "$.drift", A.is_type(dict))
    A.result_matches(c, "$.drift.model_changed", False)
    A.result_matches(c, "$.drift.question_changed", False)
    A.result_matches(c, "$.drift.flipped_ids", A.eq([]))
    A.result_matches(c, "$.drift.gap_delta.escalate", A.between(-0.01, 0.01))   # identical reads: same gap
    A.result_matches(c, "$.question_hash", ctx.seed["hash"])


def _t048(t, ctx):
    c = A.tool_called(t, "calibrate", times=(1, 2))[0]
    A.result_error(c, "OJ_INVALID_INPUT", path=A.regex(r"^examples"), retryable=False)
    assert not t.wire.result(c.use.id) or t.wire.result(c.use.id).get("isError") is True


_SETUP = {"T041": _setup_excal, "T042": _setup_excal, "T043": lambda ctx: _seed_record(ctx, "seed.json"), "T044": _setup_from_batch,
          "T045": _setup_cal24, "T046": _setup_excal, "T047": _setup_drift}
_EXPECT = {"T041": _t041, "T042": _t042, "T043": _t043, "T044": _t044, "T045": _t045, "T046": _t046, "T047": _t047, "T048": _t048}

CASES = [case_from_data(DATA, tid, expect=(fn,), setup=_SETUP.get(tid)) for tid, fn in _EXPECT.items()]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.id}-{c.slug}")
def test_case(case, case_ctx):
    run_case(case, case_ctx)
