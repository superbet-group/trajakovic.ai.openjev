"""calibration.py: pure maths against hand-computed fixtures (spec 2.15 'Calibration metrics')."""
from __future__ import annotations

import math

import pytest

from openjev_mcp import calibration as cal
from openjev_mcp.batch.store import question_hash as batch_hash

# (conf, correct): 0.52 and 0.5 -> bin 0; 0.6 -> bin 2; 0.95 x2 and 1.0 -> bin 9; 0.4 (< 0.5) counts in the first bin
OBS = [(0.52, True), (0.5, False), (0.4, False), (0.6, True), (0.95, True), (0.95, False), (1.0, True)]


def test_reliability_bins_hand_computed():
    bins = cal.reliability_bins(OBS)
    assert len(bins) == 10 and bins[0]["lo"] == 0.5 and bins[9]["hi"] == pytest.approx(1.0)
    assert [b["count"] for b in bins] == [3, 0, 1, 0, 0, 0, 0, 0, 0, 3]
    assert bins[0]["acc"] == pytest.approx(1 / 3) and bins[0]["conf"] == pytest.approx((0.52 + 0.5 + 0.4) / 3)
    assert bins[2]["acc"] == 1.0 and bins[2]["conf"] == 0.6
    assert bins[9]["acc"] == pytest.approx(2 / 3) and bins[9]["conf"] == pytest.approx((0.95 + 0.95 + 1.0) / 3)
    assert bins[1]["acc"] is None and bins[1]["conf"] is None


def test_brier_and_ece_hand_computed():
    brier = (0.48 ** 2 + 0.5 ** 2 + 0.4 ** 2 + 0.4 ** 2 + 0.05 ** 2 + 0.95 ** 2 + 0) / 7
    assert cal.brier(OBS) == pytest.approx(brier)
    ece = 3 / 7 * abs(1 / 3 - 1.42 / 3) + 1 / 7 * abs(1 - 0.6) + 3 / 7 * abs(2 / 3 - 2.9 / 3)
    assert cal.ece(cal.reliability_bins(OBS)) == pytest.approx(ece)
    assert cal.brier([]) is None and cal.ece(cal.reliability_bins([])) is None


def test_bin_edges_are_not_hit_by_float_error():
    # 0.6 = 0.5 + 2 * 0.05 and 0.55 sit exactly on edges: each belongs to the bin that starts there
    bins = cal.reliability_bins([(0.55, True), (0.6, True), (0.65, True), (0.9, True)])
    assert [b["count"] for b in bins][:4] == [0, 1, 1, 1] and bins[8]["count"] == 1


def test_histograms_hand_computed():
    confs = [0.0, 0.049, 0.05, 0.5, 0.97, 1.0]
    h = cal.confidence_hist(confs)
    assert len(h) == 20 and h[0] == 2 and h[1] == 1 and h[10] == 1 and h[19] == 2 and sum(h) == 6
    e = cal.entropy_hist([0.0, math.log(2) / 2, math.log(2)], math.log(2))
    assert e[0] == 1 and e[10] == 1 and e[19] == 1 and sum(e) == 3
    assert cal.entropy([0.5, 0.5]) == pytest.approx(math.log(2)) and cal.entropy([1.0, 0.0]) == 0


def test_fit_noul_separable_and_overlap():
    pos, neg = [("a", 0.9), ("b", 0.8)], [("c", 0.1), ("d", 0.3)]
    fit = cal.fit_noul(pos, neg)
    assert fit["separable"] and fit["max_negative"] == 0.3 and fit["min_positive"] == 0.8
    assert fit["gap"] == pytest.approx(0.5) and fit["t_fit"] == pytest.approx(0.55) and fit["overlap_ids"] == []
    bad = cal.fit_noul([("a", 0.9), ("b", 0.2)], [("c", 0.1), ("d", 0.5)])
    assert not bad["separable"] and bad["gap"] == pytest.approx(-0.3) and bad["overlap_ids"] == ["b", "d"]
    one = cal.fit_noul(pos, [])
    assert not one["separable"] and one["t_fit"] is None and one["gap"] is None


def test_precision_coverage_grid():
    pos, neg = [("a", 0.9), ("b", 0.2)], [("c", 0.5), ("d", 0.1)]
    rows = {r["t"]: r for r in cal.precision_coverage(pos, neg)}
    assert len(rows) == 19 and min(rows) == 0.05 and max(rows) == 0.95
    assert rows[0.5] == {"t": 0.5, "precision": 0.5, "coverage": 0.5, "errors": 2}
    assert rows[0.95]["precision"] is None and rows[0.95]["coverage"] == 0 and rows[0.95]["errors"] == 2
    assert rows[0.05]["errors"] == 2 and rows[0.15]["precision"] == pytest.approx(2 / 3) and rows[0.15]["errors"] == 1


def test_suggested_band_never_narrower_until_100():
    pos, neg = [("a", 0.9994)], [("c", 0.002)]
    assert cal.suggested_band(pos, neg, 7) == {"no_at": 0.05, "yes_at": 0.95}
    assert cal.suggested_band(pos, neg, 99) == {"no_at": 0.05, "yes_at": 0.95}
    # a true item at 0.03 forces no_at below it (zero observed errors); a false item at 0.97 forces yes_at above it
    assert cal.suggested_band([("a", 0.03)], [("c", 0.97)], 10) == {"no_at": 0.03, "yes_at": 0.97}
    big = cal.suggested_band([("a", 0.9), ("b", 0.8)], [("c", 0.1), ("d", 0.3)], 100)
    assert big == {"no_at": 0.3, "yes_at": 0.8}


def test_confusion_and_ladder():
    assert cal.confusion([("a", "a"), ("a", "b"), (1, 1)]) == {"a": {"a": 1, "b": 1}, "1": {"1": 1}}
    assert cal.ladder_monotonic([(0, 0.2), (1, 0.9), (2, 1.8), (2, 2.0)]) is True
    assert cal.ladder_monotonic([(0, 0.2), (1, 1.9), (2, 1.8)]) is False
    assert cal.ladder_monotonic([(1, 0.3), (1, 0.4)]) is None


def test_question_hash_is_key_order_independent_and_shared():
    a = {"q": {"type": "noul", "instructions": "x y z", "criteria": {"true": "t", "false": "f"}}}
    b = {"q": {"criteria": {"false": "f", "true": "t"}, "instructions": "x y z", "type": "noul"}}
    assert cal.question_hash(a) == cal.question_hash(b) == batch_hash(a) and cal.question_hash(a).startswith("sha256:")
    assert cal.question_hash(a) != cal.question_hash({"q": {**a["q"], "instructions": "x y w"}})


def test_drift_reports_model_flips_gap_and_hash():
    def rec(model, p3, gap, qh="sha256:a"):
        return {"model_resolved": model, "question_hash": qh, "per_question": {"q": {"type": "noul", "gap": gap}},
                "items": [{"id": "1", "label": True, "p": 0.9}, {"id": "3", "label": False, "p": p3}, {"id": "9", "label": True, "p": 0.9}]}
    d = cal.drift(rec("m-1", 0.1, 0.8), rec("m-2", 0.7, 0.5, "sha256:b"))
    assert d["model_changed"] and d["previous_model"] == "m-1" and d["model"] == "m-2" and d["question_changed"]
    assert d["flipped_ids"] == ["3"] and d["gap_delta"] == {"q": pytest.approx(-0.3)}
    same = cal.drift(rec("m-1", 0.1, 0.8), rec("m-1", 0.1, 0.8))
    assert not same["model_changed"] and not same["question_changed"] and same["flipped_ids"] == [] and same["gap_delta"] == {"q": 0}


def test_drift_multi_question_and_choice_predictions():
    def rec(pred, p):
        return {"model_resolved": "m", "question_hash": "h", "per_question": {"a": {"type": "noul"}, "c": {"type": "choice"}},
                "items": [{"id": "1", "label": {"a": True, "c": "x"}, "p": {"a": p, "c": 0.9}, "pred": {"c": pred}}]}
    assert cal.drift(rec("x", 0.9), rec("y", 0.9))["flipped_ids"] == ["1"]
    assert cal.drift(rec("x", 0.9), rec("x", 0.2))["flipped_ids"] == ["1"]
    assert cal.drift(rec("x", 0.9), rec("x", 0.8))["flipped_ids"] == []
