"""g02 core reads: ask, yes_no, classify, score (T009-T018). Ground truth: spec cases 00/01/02 (pinned) or unambiguous states."""
import pytest
import run_live as rl

import cl_assert as A
from cl_cases import case_from_data, run_case
from cl_env import CASES_DIR
from openjev_mcp.derive import is_escape

DATA = CASES_DIR / "g02_reads.json"
C00, C01, C02 = (rl.load(f) for f in ("00-spec-examples.json", "01-support-ticket-triage.json", "02-confidence-gated-action.json"))


def _req(table, cid):
    return table[cid]["request"]


def _yn_args(req, **extra):
    q = req["questions"].get("q") or next(iter(req["questions"].values()))
    crit = q.get("criteria") or {}
    a = {"state": req["state"], "claim": q["instructions"]}
    if crit:
        a.update(true_means=crit["true"], false_means=crit["false"])
    return {**a, **extra}


def _keys(*ks):
    return A.P(f"keys == {sorted(ks)}", lambda v: isinstance(v, dict) and set(v) == set(ks))


def _once(t, tool):
    (c,) = A.tool_called(t, tool, times=1)
    A.result_ok(c)
    return c


def _meta_ok(c, requests=1):
    A.result_matches(c, "meta.model", A.is_type(str))
    A.result_matches(c, "meta.requests", A.eq(requests))
    A.result_matches(c, "meta.request_ids", lambda v: isinstance(v, list) and len(v) == requests)


def _t009(t, ctx):
    c = _once(t, "ask")
    A.result_matches(c, "answers.q.type", "noul")
    A.result_matches(c, "answers.q.p", A.lt(0.2))
    A.result_matches(c, "answers.q.band", "no")
    A.result_matches(c, "meta.request_ids", lambda v: isinstance(v, list) and len(v) == 1 and bool(v[0]))
    A.result_matches(c, "meta.model", lambda v: isinstance(v, str) and bool(v))


def _t010(t, ctx):
    c = _once(t, "ask")
    A.result_matches(c, "answers", _keys("urgent", "team", "tone"))
    A.result_matches(c, "answers.urgent.type", "noul")
    A.result_matches(c, "answers.urgent.p", A.between(0, 1))
    A.result_matches(c, "answers.team.probabilities.*", A.sums_to(1, 0.02))
    A.result_matches(c, "answers.team.choice", A.one_of("outage", "billing", "feature"))
    A.result_matches(c, "answers.tone.score", A.between(0, 2))
    A.result_matches(c, "answers.tone.level", A.one_of(0, 1, 2))
    A.result_matches(c, "answers.tone.level_label", A.one_of("calm", "annoyed", "furious"))
    _meta_ok(c, 1)


def _t011(t, ctx):
    c = _once(t, "ask")
    A.args_match(c, "options.samples", 4)
    A.wire_called(t, "tools/call", "ask", {"arguments.options.samples": A.eq(4)})
    A.result_matches(c, "answers.q.p", A.between(0, 1))
    A.result_matches(c, "meta.requests", A.ge(1))


def _t012(t, ctx):
    c = _once(t, "yes_no")
    A.result_matches(c, "decision", "yes")
    A.result_matches(c, "p", A.ge(0.8))
    A.result_matches(c, "thresholds_used.yes_at", A.approx(0.8))
    A.result_matches(c, "thresholds_used.no_at", A.approx(0.2))
    A.result_matches(c, "margin", A.between(0, 1))


def _t013(t, ctx):
    c = _once(t, "yes_no")
    A.args_match(c, "yes_at", A.approx(0.95))
    A.args_match(c, "no_at", A.approx(0.05))
    A.args_match(c, "true_means", A.is_type(str))
    A.args_match(c, "false_means", A.is_type(str))
    A.result_matches(c, "thresholds_used.yes_at", A.approx(0.95))
    A.result_matches(c, "thresholds_used.no_at", A.approx(0.05))
    r = c.result.json
    want = "yes" if r["p"] >= 0.95 else "no" if r["p"] <= 0.05 else "uncertain"
    A.result_matches(c, "decision", want)
    A.result_matches(c, "margin", A.approx(abs(2 * r["p"] - 1), 1e-6))


def _t014(t, ctx):
    c = _once(t, "yes_no")
    r = c.result.json
    p, th = r["p"], r["thresholds_used"]
    A.result_matches(c, "thresholds_used.yes_at", A.approx(0.8))
    A.result_matches(c, "thresholds_used.no_at", A.approx(0.2))
    grey = th["no_at"] < p < th["yes_at"]
    A.result_matches(c, "decision", "uncertain" if grey else "yes" if p >= th["yes_at"] else "no")
    A.result_matches(c, "margin", A.approx(abs(2 * p - 1), 1e-6))
    if r["decision"] == "uncertain":   # a final grey read means the first read was grey too: re-read once
        A.result_matches(c, "meta.requests", 2)
    A.result_matches(c, "meta.requests", A.one_of(1, 2))


_T15_LABELS = {k: v for k, v in _req(C01, "triage-01")["questions"]["dept"]["criteria"].items() if k != "other"}


def _t015(t, ctx):
    c = _once(t, "classify")
    r = c.result.json
    A.result_matches(c, "label", C01["triage-01"]["expect"]["answers"]["dept"]["choice"])
    A.result_matches(c, "abstained", False)
    A.result_matches(c, "probabilities", _keys(*_T15_LABELS, "other"))
    A.result_matches(c, "probabilities.*", A.sums_to(1, 0.02))
    A.result_matches(c, "p_top", A.approx(max(r["probabilities"].values()), 1e-9))
    A.result_matches(c, "top", r["label"])


def _t016(t, ctx):
    c = _once(t, "classify")
    r = c.result.json
    A.result_matches(c, "abstained", True)
    A.result_matches(c, "label", A.absent)
    A.result_matches(c, "top", lambda v: isinstance(v, str) and is_escape(v))
    A.result_matches(c, "reason", A.is_type(str))
    A.result_matches(c, "probabilities", A.contains(r["top"]))


def _t017(t, ctx):
    c = _once(t, "classify")
    A.args_match(c, "multi_label", True)
    labels = c.use.input["labels"]
    A.result_matches(c, "labels_multi", _keys(*labels))
    for lab in labels:
        A.result_matches(c, f"labels_multi.{lab}.p", A.between(0, 1))
        A.result_matches(c, f"labels_multi.{lab}.band", A.one_of("yes", "no", "grey"))
    for lab in ("billing", "technical"):   # triage-08: invoice question plus broken login
        A.result_matches(c, f"labels_multi.{lab}.p", A.ge(0.5))


def _t018(t, ctx):
    c = _once(t, "score")
    levels = c.use.input["levels"]
    A.args_match(c, "one_based", True)
    A.result_matches(c, "score", A.ge(4))
    A.result_matches(c, "level_label", A.one_of(levels))
    A.result_matches(c, "level", A.between(1, 5))
    A.result_matches(c, "probabilities", lambda v: isinstance(v, dict) and len(v) == 5)
    A.result_matches(c, "bimodal", A.is_type(bool))


def _mk(tid, fn, **kw):
    return case_from_data(DATA, tid, expect=(fn,), **kw)


CASES = [
    _mk("T009", _t009),
    _mk("T010", _t010, args={k: _req(C00, "ex-ask")[k] for k in ("state", "questions")}),
    _mk("T011", _t011),
    _mk("T012", _t012, args=_yn_args(_req(C00, "ex-yes-no"))),
    _mk("T013", _t013, args=_yn_args(_req(C00, "ex-yes-no"), yes_at=0.95, no_at=0.05)),
    _mk("T014", _t014, args=_yn_args(_req(C02, "gate-16-thin-evidence-fix-fails-095-bar"))),
    _mk("T015", _t015, args={"state": _req(C01, "triage-01")["state"], "question": _req(C01, "triage-01")["questions"]["dept"]["instructions"], "labels": _T15_LABELS}),
    _mk("T016", _t016),
    _mk("T017", _t017, args={"state": _req(C01, "triage-08")["state"], "question": "Which topics does this ticket touch?",
                             "labels": {k: v for k, v in _req(C01, "triage-08")["questions"]["dept"]["criteria"].items() if k in ("billing", "technical")},
                             "multi_label": True}),
    _mk("T018", _t018),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.id}-{c.slug}")
def test_case(case, case_ctx):
    run_case(case, case_ctx)
