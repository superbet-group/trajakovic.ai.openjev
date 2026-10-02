"""batch.compare: Jensen-Shannon (log2) on hand-computed fixtures. Pure."""
from __future__ import annotations

import math

import pytest

from openjev_mcp.batch.compare import compare, jsd


def noul(p):
    return {"type": "noul", "p": p, "band": "yes" if p >= 0.7 else "no" if p <= 0.3 else "grey", "margin": abs(2 * p - 1)}


def choice(probs):
    top = max(probs, key=probs.get)
    return {"type": "choice", "choice": top, "p_top": probs[top], "probabilities": probs}


def score(probs):
    lvl = max(probs, key=probs.get)
    return {"type": "score", "score": 0.0, "level": int(lvl), "probabilities": probs}


def row(i, **answers):
    return {"id": i, "index": 1, "status": "ok", "answers": answers}


def test_jsd_hand_computed():
    assert jsd([0.5, 0.5], [0.5, 0.5]) == 0.0
    assert jsd([1, 0], [0, 1]) == pytest.approx(1.0)
    # p=(1,0) q=(.5,.5): m=(.75,.25); .5*log2(1/.75) + .5*(.5*log2(.5/.75) + .5*log2(.5/.25))
    expect = 0.5 * math.log2(1 / 0.75) + 0.25 * math.log2(0.5 / 0.75) + 0.25 * math.log2(2)
    assert jsd([1, 0], [0.5, 0.5]) == pytest.approx(expect)
    assert jsd([1, 0], [0.5, 0.5]) == pytest.approx(0.3112781244591328)


def test_identical_files_give_zero():
    a = {"x": row("x", q=noul(0.9), c=choice({"a": 0.7, "b": 0.3}), s=score({"0": 0.2, "1": 0.8}))}
    out = compare(a, a)
    assert out["matched"] == 1 and out["max_jsd"] == 0.0 and out["only_in_a"] == [] == out["only_in_b"]
    for q in out["per_question"].values():
        assert q["jsd_mean"] == 0.0 and q["jsd_max"] == 0.0 and q["agreement"] == 1.0 and q["flipped_ids"] == []
        assert q["jsd_reason"] is None
    assert out["per_question"]["q"]["mean_abs_delta_p"] == 0.0 and out["per_question"]["s"]["mean_abs_delta_p"] is None


def test_noul_bernoulli_flip_and_delta():
    a, b = {"x": row("x", q=noul(1.0))}, {"x": row("x", q=noul(0.5))}
    q = compare(a, b)["per_question"]["q"]
    assert q["jsd_mean"] == pytest.approx(0.3112781244591328) and q["jsd_max_id"] == "x"
    assert q["flipped_ids"] == ["x"] and q["agreement"] == 0.0 and q["mean_abs_delta_p"] == pytest.approx(0.5)


def test_disjoint_keys_null_with_reason_and_key_map_aligns():
    a = {"x": row("x", c=choice({"bug": 0.8, "feat": 0.2}))}
    b = {"x": row("x", c=choice({"defect": 0.8, "feature": 0.2}))}
    q = compare(a, b)["per_question"]["c"]
    assert q["jsd_mean"] is None and q["jsd_max"] is None and "no key_map" in q["jsd_reason"]
    assert compare(a, b)["max_jsd"] is None
    ok = compare(a, b, key_map={"c": {"defect": "bug", "feature": "feat"}})["per_question"]["c"]
    assert ok["jsd_mean"] == 0.0 and ok["jsd_reason"] is None and ok["agreement"] == 1.0 and ok["flipped_ids"] == []


def test_choice_shared_keys_and_flip():
    a = {"x": row("x", c=choice({"a": 0.6, "b": 0.3, "z": 0.1}))}
    b = {"x": row("x", c=choice({"a": 0.2, "b": 0.6, "y": 0.2}))}
    q = compare(a, b)["per_question"]["c"]
    expect = jsd([0.6, 0.3], [0.2, 0.6])
    assert q["jsd_mean"] == pytest.approx(expect) and q["flipped_ids"] == ["x"]
    assert q["mean_abs_delta_p"] == pytest.approx(abs(0.6 - 0.2))   # a-side choice 'a': p_top vs b's p for 'a'


def test_score_levels_need_same_count():
    a = {"x": row("x", s=score({"0": 0.5, "1": 0.5}))}
    b = {"x": row("x", s=score({"0": 0.5, "1": 0.25, "2": 0.25}))}
    q = compare(a, b)["per_question"]["s"]
    assert q["jsd_mean"] is None and "level counts differ" in q["jsd_reason"]
    b2 = {"x": row("x", s=score({"0": 0.0, "1": 1.0}))}
    assert compare(a, b2)["per_question"]["s"]["jsd_mean"] == pytest.approx(jsd([0.5, 0.5], [0, 1]))


def test_question_map_only_in_and_errors():
    a = {"x": row("x", old=noul(0.9)), "y": row("y", old=noul(0.9)), "e": {"id": "e", "status": "error"}}
    b = {"x": row("x", new=noul(0.9)), "z": row("z", new=noul(0.1)), "e": {"id": "e", "status": "error"}}
    out = compare(a, b, question_map={"old": "new"})
    assert out["matched"] == 2 and out["only_in_a"] == ["y"] and out["only_in_b"] == ["z"]
    assert out["per_question"]["old"]["jsd_mean"] == 0.0 and out["per_question"]["old"]["agreement"] == 1.0
    miss = compare(a, b)["per_question"]["old"]
    assert miss["jsd_mean"] is None and "no answers" in miss["jsd_reason"]
