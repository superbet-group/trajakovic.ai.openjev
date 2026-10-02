"""Pure calibration maths of spec 2.15: threshold fitting, reliability bins, Brier, ECE, histograms, drift.
No I/O, no rounding (the tool rounds). Observation = (conf, correct) for the bins; (p, label) for the noul fit."""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from openjev_mcp.batch.store import question_hash as _qh

GRID = tuple(round(0.05 * i, 2) for i in range(1, 20))     # t in {0.05, 0.1, ..., 0.95}
BINS, HIST_BINS = 10, 20
SMALL_N = 100                                               # below this the band is never narrower than [0.05, 0.95]


def question_hash(questions: Mapping) -> str:
    """sha256 of the canonical JSON (sorted keys): independent of key order."""
    return _qh(dict(questions))


def _cell(x: float, lo: float, hi: float, n: int) -> int:
    return min(n - 1, max(0, math.floor(round((x - lo) / (hi - lo) * n, 9))))


def fit_noul(pos: Sequence[tuple[str, float]], neg: Sequence[tuple[str, float]]) -> dict:
    """pos/neg = [(id, p)] of true/false labels. separable iff max(p | false) < min(p | true); t_fit = midpoint."""
    mx = max((p for _, p in neg), default=None)
    mn = min((p for _, p in pos), default=None)
    if mx is None or mn is None:
        return {"separable": False, "max_negative": mx, "min_positive": mn, "gap": None, "t_fit": None, "overlap_ids": []}
    sep = mx < mn
    overlap = [] if sep else [i for i, p in pos if p <= mx] + [i for i, p in neg if p >= mn]
    return {"separable": sep, "max_negative": mx, "min_positive": mn, "gap": mn - mx, "t_fit": (mx + mn) / 2,
            "overlap_ids": overlap}


def precision_coverage(pos: Sequence[tuple[str, float]], neg: Sequence[tuple[str, float]],
                       grid: Sequence[float] = GRID) -> list[dict]:
    # deviation: 2.15 precision_coverage: coverage = recall of the true class (spec does not define it); adds errors per t
    """At each t of GRID, 'yes' = p >= t: precision = TP / flagged, coverage = TP / n_pos (recall), errors = FP + FN."""
    out = []
    for t in grid:
        tp = sum(1 for _, p in pos if p >= t)
        fp = sum(1 for _, p in neg if p >= t)
        out.append({"t": t, "precision": tp / (tp + fp) if tp + fp else None,
                    "coverage": tp / len(pos) if pos else None, "errors": fp + len(pos) - tp})
    return out


def suggested_band(pos: Sequence[tuple[str, float]], neg: Sequence[tuple[str, float]], n: int) -> dict:
    # deviation: 2.15 suggested band: spec wording is ambiguous; built to reproduce the ex-cal report ([0.05, 0.95] at n=7)
    """Widest [no_at, yes_at] with zero observed errors (p <= no_at is 'no', p >= yes_at is 'yes'): no_at up to the lowest
    positive, yes_at down to the highest negative. Until n >= 100 it is never narrower than [0.05, 0.95]."""
    lo_pos = min((p for _, p in pos), default=1.0)
    hi_neg = max((p for _, p in neg), default=0.0)
    if n >= SMALL_N:
        no_at = max((p for _, p in neg if p < lo_pos), default=0.0)
        yes_at = min((p for _, p in pos if p > hi_neg), default=1.0)
    else:
        no_at, yes_at = min(0.05, lo_pos), max(0.95, hi_neg)
    return {"no_at": no_at, "yes_at": yes_at}


def confusion(pairs: Iterable[tuple[Any, Any]]) -> dict[str, dict[str, int]]:
    """pairs = [(label, predicted)] -> {label: {predicted: count}}."""
    out: dict[str, dict[str, int]] = {}
    for label, pred in pairs:
        row = out.setdefault(str(label), {})
        row[str(pred)] = row.get(str(pred), 0) + 1
    return out


def ladder_monotonic(pairs: Iterable[tuple[int, float]]) -> bool | None:
    """pairs = [(label level, expected score)]: the mean expected score never decreases with the label level.
    None with fewer than two levels."""
    by: dict[int, list[float]] = {}
    for level, score in pairs:
        by.setdefault(level, []).append(score)
    means = [sum(v) / len(v) for _, v in sorted(by.items())]
    return None if len(means) < 2 else all(b >= a for a, b in zip(means, means[1:]))


def reliability_bins(obs: Iterable[tuple[float, bool]]) -> list[dict]:
    """10 equal bins over [0.5, 1.0] of conf (below 0.5 counts in the first); acc and conf are null when empty."""
    acc = [[0, 0, 0.0] for _ in range(BINS)]    # count, correct, conf sum
    for conf, correct in obs:
        b = acc[_cell(conf, 0.5, 1.0, BINS)]
        b[0] += 1
        b[1] += bool(correct)
        b[2] += conf
    return [{"lo": 0.5 + 0.5 * i / BINS, "hi": 0.5 + 0.5 * (i + 1) / BINS, "count": c,
             "acc": ok / c if c else None, "conf": s / c if c else None} for i, (c, ok, s) in enumerate(acc)]


def brier(obs: Sequence[tuple[float, bool]]) -> float | None:
    return sum((c - bool(ok)) ** 2 for c, ok in obs) / len(obs) if obs else None


def ece(bins: Sequence[Mapping]) -> float | None:
    total = sum(b["count"] for b in bins)
    return sum(b["count"] / total * abs(b["acc"] - b["conf"]) for b in bins if b["count"]) if total else None


def confidence_hist(confs: Iterable[float]) -> list[int]:
    out = [0] * HIST_BINS
    for c in confs:
        out[_cell(c, 0.0, 1.0, HIST_BINS)] += 1
    return out


def entropy_hist(ents: Iterable[float], max_entropy: float) -> list[int]:
    out = [0] * HIST_BINS
    for h in ents:
        out[_cell(h, 0.0, max_entropy, HIST_BINS)] += 1
    return out


def entropy(probs: Iterable[float]) -> float:
    return -sum(p * math.log(p) for p in probs if p > 0)


def _pred(item: Mapping, qid: str, kind: str, multi: bool) -> Any:
    """The predicted side of one stored item: noul p >= 0.5; choice/score the stored `pred`."""
    src = (lambda k: (item.get(k) or {}).get(qid)) if multi else item.get
    return (src("p") is not None and src("p") >= 0.5) if kind == "noul" else src("pred")


def drift(prev: Mapping, cur: Mapping) -> dict:
    """Two calibrate reports/records: changed resolved model, changed question hash, ids whose predicted side flipped,
    and the per-question change of the noul gap."""
    kinds = {q: v.get("type") for q, v in (cur.get("per_question") or {}).items()}
    multi = len(kinds) > 1
    before = {str(i["id"]): i for i in prev.get("items") or []}
    flipped = sorted({str(it["id"]) for it in cur.get("items") or [] if str(it["id"]) in before for q, k in kinds.items()
                      if _pred(before[str(it["id"])], q, k, multi) != _pred(it, q, k, multi)})
    gaps = {}
    for q, v in (cur.get("per_question") or {}).items():
        a, b = ((prev.get("per_question") or {}).get(q) or {}).get("gap"), v.get("gap")
        if a is not None and b is not None:
            gaps[q] = b - a
    out = {"model_changed": prev.get("model_resolved") != cur.get("model_resolved"),
           "previous_model": prev.get("model_resolved"), "model": cur.get("model_resolved"),
           "question_changed": prev.get("question_hash") != cur.get("question_hash"),
           "flipped_ids": flipped, "gap_delta": gaps}
    return out
