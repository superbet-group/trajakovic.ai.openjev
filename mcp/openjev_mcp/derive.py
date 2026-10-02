"""Derived answer fields of spec 2.3. Pure; values carry the server's floats, never rounded."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import ToolError

ESCAPE_PREFIXES = ("other", "none", "no_match", "not_stated")
CANVAS_TOKENS = 64
LINES_MAX = 10


@dataclass(frozen=True)
class Band:
    yes_at: float = 0.8
    no_at: float = 0.2
    choice_min_p: float = 0.6


def is_escape(key: str) -> bool:
    key = key.lower()
    return any(key == p or key.startswith(p + "_") for p in ESCAPE_PREFIXES)


def entropy(probs: Mapping[str, float]) -> float:
    return -sum(p * math.log(p) for p in probs.values() if p > 0)


def noul_answer(raw: dict, band: Band) -> dict:
    p = raw["noul"]
    kind = "yes" if p >= band.yes_at else "no" if p <= band.no_at else "grey"
    return {"type": "noul", "p": p, "band": kind, "margin": abs(2 * p - 1)}


def choice_answer(raw: dict, band: Band) -> dict:
    choice = raw["choice"]
    probs = raw["probabilities"]
    p_top = probs[choice]
    others = sorted(((k, v) for k, v in probs.items() if k != choice), key=lambda kv: -kv[1])
    runner_up, p_second = others[0] if others else (None, 0.0)
    return {"type": "choice", "choice": choice, "p_top": p_top, "runner_up": runner_up,
            "margin": p_top - p_second, "probabilities": probs, "confidence": raw["confidence"],
            "entropy": entropy(probs), "abstained": is_escape(choice) or p_top < band.choice_min_p}


def score_answer(raw: dict, criteria: Sequence[str] | None = None) -> dict:
    score = raw["score"]
    probs = raw["probabilities"]
    levels = {int(k): v for k, v in probs.items()}
    level = max(levels, key=levels.__getitem__)
    legend = raw.get("legend") or {}
    if str(level) in legend:
        label = legend[str(level)]
    elif criteria is not None and 0 <= level < len(criteria):
        label = criteria[level]
    else:
        label = str(level)
    spread = math.sqrt(sum(p * (k - score) ** 2 for k, p in levels.items()))
    high = [k for k, p in levels.items() if p >= 0.2]
    bimodal = any(b - a >= 2 for a in high for b in high)
    return {"type": "score", "score": score, "level": level, "level_label": label,
            "probabilities": probs, "confidence": raw["confidence"], "spread": spread,
            "bimodal": bimodal}


def _labels(question: Mapping[str, Any]) -> list[str]:
    criteria = question.get("criteria")
    if question.get("type") == "noul" or criteria is None:
        return []
    if isinstance(criteria, Mapping):
        return [str(k) for k in criteria]
    return [str(c) for c in criteria]


def derive_answers(raw_answers: Mapping[str, dict], questions: Mapping[str, dict], band: Band) -> dict:
    out: dict[str, dict] = {}
    for qid, question in questions.items():
        raw = raw_answers.get(qid) if isinstance(raw_answers, Mapping) else None
        if not isinstance(raw, dict):
            raise ToolError("OJ_PROTOCOL", f"response lacks answers.{qid}")
        kind = question.get("type")
        if raw.get("type") != kind:
            raise ToolError("OJ_PROTOCOL", f"answers.{qid}: type {raw.get('type')!r}, expected {kind!r}")
        try:
            if kind == "noul":
                out[qid] = noul_answer(raw, band)
            elif kind == "choice":
                out[qid] = choice_answer(raw, band)
            elif kind == "score":
                criteria = question.get("criteria")
                out[qid] = score_answer(raw, criteria if isinstance(criteria, list) else None)
            else:
                raise ToolError("OJ_PROTOCOL", f"answers.{qid}: unknown question type {kind!r}")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ToolError("OJ_PROTOCOL", f"answers.{qid}: malformed ({type(exc).__name__}: {exc})") from exc
    return out


def chunks_estimate(questions: Mapping[str, dict]) -> int:
    indexed = len(questions) > LINES_MAX
    chunks = 1
    used = 0
    for i, (qid, question) in enumerate(questions.items()):
        chars = len(qid) + sum(len(label) for label in _labels(question))
        if indexed:
            chars += len(str(i)) + 3
        cost = math.ceil(chars / 3.6) + 2
        if used and used + cost > CANVAS_TOKENS:
            chunks += 1
            used = 0
        used += cost
    return chunks
