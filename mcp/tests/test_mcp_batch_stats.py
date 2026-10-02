"""Review reasons, confidences, audit sample, per-question statistics."""
from __future__ import annotations

import pytest

from openjev_mcp.batch import stats
from openjev_mcp.derive import Band, derive_answers

QS = {"n": {"type": "noul", "instructions": "x"},
      "c": {"type": "choice", "instructions": "x", "criteria": {"a": "a", "b": "b", "other": "o"}},
      "s": {"type": "score", "instructions": "x", "criteria": ["l0", "l1", "l2", "l3"]}}


def noul(p):
    return {"type": "noul", "noul": p}


def choice(top, probs, conf=0.9):
    return {"type": "choice", "choice": top, "probabilities": probs, "confidence": conf}


def score(i, n=4, peak=0.85):
    probs = {str(k): (peak if k == i else (1 - peak) / (n - 1)) for k in range(n)}
    return {"type": "score", "score": sum(int(k) * p for k, p in probs.items()), "probabilities": probs, "confidence": 0.7,
            "legend": {str(k): f"l{k}" for k in range(n)}}


def derived(n=0.95, c=("a", {"a": 0.95, "b": 0.03, "other": 0.02}), s=score(1)):
    return derive_answers({"n": noul(n), "c": choice(c[0], c[1]), "s": s}, QS, Band())


def test_no_reasons_when_confident():
    assert stats.review_reasons(derived(), QS) == []


def test_reasons_default_rule():
    assert stats.review_reasons(derived(n=0.5), QS) == ["noul_grey"]
    assert stats.review_reasons(derived(n=0.15), QS) == []          # at the edge: confident
    assert stats.review_reasons(derived(c=("a", {"a": 0.7, "b": 0.2, "other": 0.1})), QS) == ["choice_p_below"]
    assert stats.review_reasons(derived(c=("other", {"a": 0.02, "b": 0.03, "other": 0.95})), QS) == ["abstained"]
    assert stats.review_reasons(derived(s=score(1, peak=0.3)), QS) == ["score_spread_above"]
    both = derived(n=0.5, c=("a", {"a": 0.7, "b": 0.2, "other": 0.1}))
    assert stats.review_reasons(both, QS) == ["noul_grey", "choice_p_below"]


def test_reasons_custom_rule():
    rule = {"choice_p_below": 0.99, "noul_grey": [0.05, 0.99], "score_spread_above": 5}
    assert stats.review_reasons(derived(), QS, rule) == ["noul_grey", "choice_p_below"]


def test_review_confidence_is_min_over_questions():
    a = derived(n=0.9, c=("a", {"a": 0.6, "b": 0.3, "other": 0.1}))
    conf = stats.review_confidence(a, QS)
    assert conf == pytest.approx(min(0.8, 0.6, 1 - a["s"]["spread"] / 4))
    assert stats.review_confidence({}, QS) is None
    assert stats.review_confidence({"n": a["n"]}, QS) == pytest.approx(0.8)
    assert stats.review_confidence({"s": a["s"]}, QS) == pytest.approx(1 - a["s"]["spread"] / 4)


def test_answer_confidence():
    a = derived(n=0.1, c=("b", {"a": 0.2, "b": 0.7, "other": 0.1}), s=score(2, peak=0.6))
    assert stats.answer_confidence(a["n"]) == pytest.approx(0.8)
    assert stats.answer_confidence(a["c"]) == pytest.approx(0.7)
    assert stats.answer_confidence(a["s"]) == pytest.approx(0.6)


def test_in_audit_deterministic_and_rate():
    ids = [f"id{i}" for i in range(2000)]
    pick = [i for i in ids if stats.in_audit(3, i, 0.1)]
    assert pick == [i for i in ids if stats.in_audit(3, i, 0.1)]
    assert 120 < len(pick) < 280
    assert pick != [i for i in ids if stats.in_audit(4, i, 0.1)]
    assert not any(stats.in_audit(0, i, 0.0) for i in ids)
    assert all(stats.in_audit(0, i, 1.0) for i in ids)
    assert set(stats.in_audit(3, i, 0.05) and i for i in ids) - {False} <= set(pick)   # monotone in rate


def rows():
    out = []
    for i, (n, c, s) in enumerate([(0.95, "a", 1), (0.05, "a", 2), (0.5, "b", 1), (0.9, "a", 2)], 1):
        probs = {"a": 0.9 if c == "a" else 0.05, "b": 0.9 if c == "b" else 0.05, "other": 0.05}
        ans = derive_answers({"n": noul(n), "c": choice(c, probs, 0.8), "s": score(s)}, QS, Band())
        reasons = stats.review_reasons(ans, QS)
        out.append({"index": i, "id": f"r{i}", "status": "ok", "answers": ans, "needs_review": bool(reasons),
                    "review_reasons": reasons})
    out.append({"index": 5, "id": "r5", "status": "error", "needs_review": True, "review_reasons": ["error"]})
    return out


def test_per_question():
    pq = stats.per_question(rows(), QS)
    assert pq["n"] == {"type": "noul", "n": 4, "mean_p": pytest.approx((0.95 + 0.05 + 0.5 + 0.9) / 4), "yes": 2, "no": 1,
                       "grey": 1, "mean_margin": pytest.approx((0.9 + 0.9 + 0 + 0.8) / 4)}
    assert pq["c"]["counts"] == {"a": 3, "b": 1} and pq["c"]["top2"] == ["a", "b"] and pq["c"]["abstained"] == 0
    assert pq["c"]["mean_confidence"] == pytest.approx(0.8)
    s = pq["s"]
    assert s["n"] == 4 and s["histogram"] == {"1": 2, "2": 2} and s["std"] > 0 and s["mean"] > 0
    assert set(pq) == set(QS)


def test_per_question_empty():
    assert stats.per_question([], QS)["n"]["n"] == 0


def test_review_queue_order_and_reasons():
    q = stats.review_queue(rows(), QS)
    assert [e["id"] for e in q] == ["r3", "r5"]   # equal confidence 0: index order
    assert q[1] == {"id": "r5", "question": None, "reason": "error", "confidence": 0.0}
    assert q[0]["question"] == "n" and q[0]["reason"] == "noul_grey" and q[0]["confidence"] == pytest.approx(0.0)
    mid = rows()[:2]
    mid[0]["needs_review"], mid[0]["review_reasons"] = True, ["choice_p_below"]
    mid[0]["answers"]["c"] = derive_answers({"n": noul(0.9), "c": choice("a", {"a": 0.7, "b": 0.2, "other": 0.1}),
                                              "s": score(1)}, QS, Band())["c"]
    assert stats.review_queue(mid, QS)[0]["reason"] == "choice_p_below"
