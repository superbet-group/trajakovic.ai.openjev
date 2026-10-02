"""Compare two batch outputs by Jensen-Shannon divergence, log base 2 (spec 2.21). Pure: no I/O."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def jsd(p: Sequence[float], q: Sequence[float]) -> float:
    """0 <= JSD <= 1 between two distributions over the same support (each is normalised first)."""
    sp, sq = sum(p), sum(q)
    p, q = [x / sp for x in p], [x / sq for x in q]
    m = [(a + b) / 2 for a, b in zip(p, q)]
    kl = lambda d: sum(x * math.log2(x / y) for x, y in zip(d, m) if x > 0)
    return min(1.0, max(0.0, 0.5 * kl(p) + 0.5 * kl(q)))


def _bern(a: Mapping) -> list[float]:
    return [a["p"], 1 - a["p"]]


def _pair(kind: str, a: Mapping, b: Mapping, kmap: Mapping[str, str]) -> tuple[float | None, str | None, bool, float | None]:
    """(jsd, reason when jsd is None, same top answer, |delta p|) of one id and question."""
    if kind == "noul":
        return jsd(_bern(a), _bern(b)), None, a["band"] == b["band"], abs(a["p"] - b["p"])
    if kind == "choice":
        pa = a["probabilities"]
        pb: dict[str, float] = {}
        for k, v in b["probabilities"].items():
            pb[kmap.get(k, k)] = v
        shared = [k for k in pa if k in pb]
        same = a["choice"] == kmap.get(b["choice"], b["choice"])
        delta = abs(a["p_top"] - pb.get(a["choice"], 0.0))
        if not shared or not sum(pa[k] for k in shared) or not sum(pb[k] for k in shared):
            return None, "option sets are disjoint and no key_map aligns them", same, delta
        # deviation: 2.21: partially overlapping option sets compare over the shared keys, renormalised (not null)
        return jsd([pa[k] for k in shared], [pb[k] for k in shared]), None, same, delta
    la, lb = _levels(a), _levels(b)
    if len(la) != len(lb):
        return None, f"score level counts differ ({len(la)} vs {len(lb)})", a["level"] == b["level"], None
    return jsd(la, lb), None, a["level"] == b["level"], None


def _levels(a: Mapping) -> list[float]:
    return [v for _, v in sorted(((int(k), v) for k, v in a["probabilities"].items()))]


def _qids(rows: Mapping[str, Mapping]) -> list[str]:
    return list(dict.fromkeys(q for r in rows.values() if r.get("status") == "ok" for q in (r.get("answers") or {})))


def compare(a_rows: Mapping[str, Mapping], b_rows: Mapping[str, Mapping], question_map: Mapping[str, str] | None = None,
            key_map: Mapping[str, Mapping[str, str]] | None = None) -> dict:
    """a_rows/b_rows: last row per id. question_map: a question id -> b question id. key_map: per question (a id or b id),
    b option key -> a option key."""
    qmap, kmaps = question_map or {}, key_map or {}
    common = [i for i in a_rows if i in b_rows]
    per: dict[str, dict] = {}
    for qid in _qids(a_rows):
        bq = qmap.get(qid, qid)
        kmap = kmaps.get(qid) or kmaps.get(bq) or {}
        vals: list[tuple[str, float]] = []
        flipped, agree, deltas, reasons = [], 0, [], []
        for i in common:
            ra, rb = a_rows[i], b_rows[i]
            a = (ra.get("answers") or {}).get(qid) if ra.get("status") == "ok" else None
            b = (rb.get("answers") or {}).get(bq) if rb.get("status") == "ok" else None
            if not a or not b:
                continue
            if a.get("type") != b.get("type"):
                reasons.append(f"question types differ ({a.get('type')} vs {b.get('type')})")
                flipped.append(i)
                continue
            j, why, same, delta = _pair(a["type"], a, b, kmap)
            if j is None:
                reasons.append(why)
            else:
                vals.append((i, j))
            agree += same
            if not same:
                flipped.append(i)
            if delta is not None:
                deltas.append(delta)
        n = agree + len(flipped)
        top = max(vals, key=lambda t: t[1], default=(None, None))
        per[qid] = {"jsd_mean": sum(v for _, v in vals) / len(vals) if vals else None, "jsd_max": top[1],
                    "jsd_max_id": top[0], "agreement": agree / n if n else 0.0, "flipped_ids": flipped,
                    "mean_abs_delta_p": sum(deltas) / len(deltas) if deltas else None,
                    "jsd_reason": None if vals else reasons[0] if reasons else
                    f"question {bq} has no answers in compare_to" if n == 0 else None}
    maxes = [q["jsd_max"] for q in per.values() if q["jsd_max"] is not None]
    return {"matched": len(common), "only_in_a": [i for i in a_rows if i not in b_rows],
            "only_in_b": [i for i in b_rows if i not in a_rows], "max_jsd": max(maxes, default=None),
            "per_question": per}
