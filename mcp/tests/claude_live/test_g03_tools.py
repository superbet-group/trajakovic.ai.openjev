"""g03 (T019-T028): filter, lint, compile, generate, ask_image, ask with think through claude -p. Data: cases/g03_tools.json."""
import re

import pytest

import cl_assert as A
from cl_cases import case_from_data, run_case
from cl_env import CASES_DIR

DATA = CASES_DIR / "g03_tools.json"


def _one(t, tool):
    A.precondition(t, [tool])
    (call,) = A.tool_called(t, tool, times=1)
    return call, A.result_ok(call)


def _t019(t, ctx):
    call, out = _one(t, "filter")
    A.args_match(call, "items", A.is_type(list))
    A.args_match(call, "criterion", A.contains("{id}"))
    assert out["kept"] == ["L4"], out["kept"]
    assert sorted(out["dropped"]) == ["L1", "L2", "L3", "L5"] and out["grey"] == []
    A.result_matches(call, "meta.requests", 1)


def _t020(t, ctx):
    call, out = _one(t, "filter")
    ids = {i["id"] for i in call.use.input["items"]}
    assert ids == {"S1", "S2", "S3", "S4", "S5"}
    parts = out["kept"] + out["dropped"] + out["grey"]
    assert sorted(parts) == sorted(ids), f"each item in exactly one of kept/dropped/grey: {parts}"
    assert {i["id"] for i in out["items"]} == ids and all(i["decision"] in ("keep", "drop", "grey") and "score" in i for i in out["items"])
    assert out["best"]["id"] in ids, out["best"]
    A.args_match(call, "pick_best", A.exists())
    A.args_match(call, "graded", A.exists())


def _t021(t, ctx):
    call, out = _one(t, "lint")
    assert out["valid"] is True and out["errors"] == [], out["errors"]
    A.result_matches(call, "body_hash", A.regex(r"^sha256:[0-9a-f]{64}$"))
    A.result_matches(call, "estimate.questions", 2)
    A.result_matches(call, "estimate.chunks", A.ge(1))
    A.result_matches(call, "snippets.body.questions", A.is_type(dict))


def _t022(t, ctx):
    call, out = _one(t, "lint")
    assert out["valid"] is False, out
    first = out["errors"][0]
    assert re.fullmatch(r"E0\d\d", first["code"]) and first["code"] == "E010", first
    assert first["path"].startswith("questions.team"), first
    A.args_match(call, "autofix", True)


def _t023(t, ctx):
    call, out = _one(t, "lint")
    sn = out["snippets"]
    assert set(sn) == {"curl", "python"}, list(sn)
    assert "$OPENJEV_API_KEY" in sn["curl"] and "os.environ" in sn["python"], sn
    for s in sn.values():
        assert not re.search(r"Bearer [A-Za-z0-9_\-]{8,}", s) and not re.search(r"\bsk-[A-Za-z0-9]{8,}", s), s
    A.result_matches(call, "body_hash", A.regex(r"^sha256:"))


def _t024(t, ctx):
    A.precondition(t, ["compile"])
    a, b = A.tool_called(t, "compile", times=2)
    ra, rb = A.result_ok(a), A.result_ok(b)
    assert ra["recipe"]["id"] == "ticket_triage", ra["recipe"]
    assert [d.get("qtype") for d in ra["sub_decisions"]] == ["noul", "choice", "score", "not_typed"], ra["sub_decisions"]
    assert rb["recipe"]["id"] == "none", rb["recipe"]
    for r in (ra, rb):
        assert "draft_request" in r and "lint" in r
    A.args_match(a, "sub_decisions", A.is_type(list))


def _t025(t, ctx):
    call, out = _one(t, "generate")
    assert "4" in out["content"], out
    A.result_matches(call, "finish_reason", A.one_of("stop", "length"))
    A.args_match(call, "max_tokens", 16)


def _t026(t, ctx):
    call, out = _one(t, "ask_image")
    a = out["answers"]
    assert a["hotdog"]["p"] >= 0.9 and a["cat"]["p"] <= 0.1, a
    (img,) = out["images"]
    assert img["source"].endswith("hotdog.jpg") and img["sent_as"] == "image/jpeg" and img["bytes"] > 0 and img["reencoded"] is False, img
    A.args_match(call, "images[0].path", A.regex(r"/data/hotdog\.jpg$"))


def _t027(t, ctx):
    call, out = _one(t, "ask_image")
    assert out["answers"]["kind"]["choice"] == "consent_modal", out["answers"]
    assert len(out["images"]) == 2 and out["images"][0]["source"].endswith("ui22_cookie_modal.png"), out["images"]
    assert out["images"][1]["source"].endswith("ui22_dashboard.png")
    A.args_match(call, "images", A.is_type(list))


def _t028(t, ctx):
    call, out = _one(t, "ask")
    A.args_match(call, "options.think", 64)
    assert out["answers"]["hop"]["choice"] == "C", out["answers"]["hop"]
    A.result_matches(call, "meta.requests", A.ge(1))


EXPECT = {"T019": _t019, "T020": _t020, "T021": _t021, "T022": _t022, "T023": _t023, "T024": _t024, "T025": _t025, "T026": _t026, "T027": _t027, "T028": _t028}
CASES = [case_from_data(DATA, tid, expect=(fn,)) for tid, fn in EXPECT.items()]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.id}-{c.slug}")
def test_case(case, case_ctx):
    run_case(case, case_ctx)
