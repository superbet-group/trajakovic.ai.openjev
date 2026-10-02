"""Review reasons, confidences, seeded audit sample and per-question statistics (spec 2.11, 2.21). Pure."""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

REVIEW_DEFAULT = {"choice_p_below": 0.8, "noul_grey": (0.15, 0.85), "score_spread_above": 0.6}
AUDIT_DEFAULT = {"rate": 0.03, "seed": 0}


def _rule(review_rule: Mapping[str, Any] | None) -> dict:
    return {**REVIEW_DEFAULT, **{k: v for k, v in (review_rule or {}).items() if v is not None}}


def _levels(answer: Mapping[str, Any], question: Mapping[str, Any] | None) -> int:
    n = len(answer.get("probabilities") or {})
    criteria = (question or {}).get("criteria")
    return n or (len(criteria) if isinstance(criteria, (list, dict)) else 0)


def question_review(qid: str, answer: Mapping[str, Any], question: Mapping[str, Any] | None,
                    rule: Mapping[str, Any]) -> tuple[str | None, float]:
    """(reason or None, review confidence) of one derived answer: choice p_top, noul margin, score 1 - spread/levels."""
    kind = answer.get("type")
    if kind == "noul":
        lo, hi = rule["noul_grey"]
        return ("noul_grey" if lo < answer["p"] < hi else None), answer.get("margin", abs(2 * answer["p"] - 1))
    if kind == "choice":
        reason = "abstained" if answer.get("abstained") else "choice_p_below" if answer["p_top"] < rule["choice_p_below"] else None
        return reason, answer["p_top"]
    if kind == "score":
        levels = _levels(answer, question)
        conf = 1.0 - answer["spread"] / levels if levels else 0.0
        return ("score_spread_above" if answer["spread"] > rule["score_spread_above"] else None), conf
    return None, 1.0


def review_reasons(answers: Mapping[str, Mapping], questions: Mapping[str, Mapping],
                   review_rule: Mapping[str, Any] | None = None) -> list[str]:
    rule = _rule(review_rule)
    found = (question_review(q, a, questions.get(q), rule)[0] for q, a in answers.items())
    return list(dict.fromkeys(r for r in found if r))


def review_confidence(answers: Mapping[str, Mapping], questions: Mapping[str, Mapping] | None = None,
                      review_rule: Mapping[str, Any] | None = None) -> float | None:
    """Minimum review confidence over the questions (None without answers)."""
    rule = _rule(review_rule)
    confs = [question_review(q, a, (questions or {}).get(q), rule)[1] for q, a in answers.items()]
    return min(confs) if confs else None


def answer_confidence(answer: Mapping[str, Any]) -> float | None:
    """2.21: noul |2p-1|, choice p_top, score probability of the argmax level."""
    kind = answer.get("type")
    if kind == "noul":
        return abs(2 * answer["p"] - 1)
    if kind == "choice":
        return answer["p_top"]
    if kind == "score":
        probs = answer.get("probabilities") or {}
        return probs.get(str(answer["level"]), probs.get(answer["level"], max(probs.values(), default=None)))
    return None


def in_audit(seed: int, row_id: str, rate: float) -> bool:
    """sha256(seed, id) < rate: reproducible, independent of order and concurrency."""
    digest = hashlib.sha256(f"{seed}\0{row_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2 ** 64 < rate


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def per_question(rows: Iterable[Mapping], questions: Mapping[str, Mapping]) -> dict:
    ok = [r for r in rows if r.get("status") == "ok" and r.get("answers")]
    out: dict[str, dict] = {}
    for qid, q in questions.items():
        ans = [r["answers"][qid] for r in ok if qid in r["answers"]]
        kind = q.get("type")
        if kind == "noul":
            out[qid] = {"type": "noul", "n": len(ans), "mean_p": _mean([a["p"] for a in ans]),
                        "yes": sum(a["band"] == "yes" for a in ans), "no": sum(a["band"] == "no" for a in ans),
                        "grey": sum(a["band"] == "grey" for a in ans),
                        "mean_margin": _mean([a.get("margin", abs(2 * a["p"] - 1)) for a in ans])}
        elif kind == "choice":
            counts = Counter(a["choice"] for a in ans)
            out[qid] = {"type": "choice", "n": len(ans), "counts": dict(counts),
                        "top2": [k for k, _ in counts.most_common(2)],
                        "mean_confidence": _mean([a["confidence"] for a in ans]),
                        "abstained": sum(bool(a.get("abstained")) for a in ans)}
        elif kind == "score":
            scores = [a["score"] for a in ans]
            mean = _mean(scores)
            hist = Counter(str(a["level"]) for a in ans)
            out[qid] = {"type": "score", "n": len(ans), "mean": mean,
                        "std": math.sqrt(_mean([(s - mean) ** 2 for s in scores])), "histogram": dict(hist),
                        "mean_confidence": _mean([a["confidence"] for a in ans])}
    return out


def review_queue(rows: Iterable[Mapping], questions: Mapping[str, Mapping] | None = None,
                 review_rule: Mapping[str, Any] | None = None) -> list[dict]:
    """needs_review rows, ascending confidence then index; one entry per row, for its least confident flagged question."""
    rule, questions = _rule(review_rule), questions or {}
    queue: list[tuple[float, int, dict]] = []
    for row in rows:
        if not row.get("needs_review"):
            continue
        entry: dict[str, Any] = {"id": row["id"], "question": None, "reason": (row.get("review_reasons") or ["error"])[0],
                                 "confidence": 0.0}
        if row.get("status") == "ok":
            flagged = []
            for qid, a in (row.get("answers") or {}).items():
                reason, conf = question_review(qid, a, questions.get(qid), rule)
                if reason:
                    flagged.append((conf, qid, reason))
            if flagged:
                conf, qid, reason = min(flagged)
                entry.update(question=qid, reason=reason, confidence=conf)
            else:
                entry["confidence"] = review_confidence(row.get("answers") or {}, questions, rule) or 0.0
        else:
            entry["reason"] = "error"
        queue.append((entry["confidence"], row.get("index", 0), entry))
    return [e for _, _, e in sorted(queue, key=lambda t: t[:2])]
