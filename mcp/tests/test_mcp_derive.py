from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import pytest
from stubs import SPEC_TESTS

from openjev_mcp.derive import (Band, chunks_estimate, choice_answer, derive_answers, entropy, is_escape,
                                noul_answer, score_answer)
from openjev_mcp.errors import ToolError

with open(os.path.join(SPEC_TESTS, "spec_build", "captured.json"), encoding="utf-8") as fh:
    CAPTURED = json.load(fh)

BAND = Band()
NOUL_Q = {"type": "noul", "instructions": "x"}


def answers_of(name: str) -> dict:
    return CAPTURED[name]["body"]["answers"]


def close(a: float, b: float, places: int = 4) -> bool:
    return abs(a - b) <= 0.5 * 10 ** -places + 1e-12


def test_yes_no_replay():
    got = derive_answers(answers_of("ex-yes-no"), {"q": NOUL_Q}, BAND)["q"]
    assert got["band"] == "yes"
    assert close(got["p"], 0.9995)
    assert close(got["margin"], 0.9989)


def test_classify_abstain_replay():
    q = {"q": {"type": "choice", "criteria": ["billing", "technical", "sales", "other"]}}
    got = derive_answers(answers_of("ex-classify-abstain"), q, BAND)["q"]
    assert got["choice"] == "other"
    assert got["abstained"] is True
    assert got["runner_up"] == "technical"
    assert close(got["p_top"], 0.9996)
    assert close(got["margin"], 0.9992)
    assert close(got["confidence"], 0.9972)
    raw = answers_of("ex-classify-abstain")["q"]
    assert got["probabilities"] == raw["probabilities"]
    assert list(got["probabilities"]) == ["billing", "technical", "sales", "other"]


def test_score_replay():
    got = derive_answers(answers_of("ex-score"), {"q": {"type": "score", "criteria": list("abcde")}}, BAND)["q"]
    assert got["level"] == 4
    assert got["level_label"] == "outage or data loss, page immediately"
    assert close(got["score"], 3.9365)
    assert close(got["spread"], 0.2696)
    assert got["bimodal"] is False
    assert close(got["confidence"], 0.8515)


def test_ask_replay_order_and_values():
    questions = {"tone": {"type": "score", "criteria": ["calm", "annoyed", "furious"]},
                 "urgent": NOUL_Q,
                 "team": {"type": "choice", "criteria": ["outage", "billing", "feature"]}}
    got = derive_answers(answers_of("ex-ask"), questions, BAND)
    assert list(got) == ["tone", "urgent", "team"]
    assert got["urgent"]["band"] == "yes"
    assert close(got["urgent"]["margin"], 0.9941)
    assert got["team"]["runner_up"] == "billing"
    assert close(got["team"]["p_top"], 1.0)
    assert close(got["team"]["margin"], 1.0)
    assert close(got["team"]["entropy"], 5.5e-05, 5)
    assert got["team"]["abstained"] is False
    assert got["tone"]["level_label"] == "furious"
    assert close(got["tone"]["spread"], 0.0221)
    assert got["tone"]["bimodal"] is False


@pytest.mark.parametrize("key", ["Other", "NONE", "No_Match", "other", "other_billing", "none", "no_match", "not_stated", "none_x"])
def test_escape_keys(key):
    assert is_escape(key)


@pytest.mark.parametrize("key", ["otherwise", "another", "nonexistent", "billing", "no", "not_stated2"])
def test_not_escape_keys(key):
    assert not is_escape(key)


def test_noul_bands():
    assert noul_answer({"noul": 0.8}, BAND)["band"] == "yes"
    assert noul_answer({"noul": 0.2}, BAND)["band"] == "no"
    got = noul_answer({"noul": 0.5}, BAND)
    assert got["band"] == "grey" and got["margin"] == 0
    assert noul_answer({"noul": 0.5}, Band(yes_at=0.5))["band"] == "yes"


def test_choice_single_key():
    got = choice_answer({"choice": "a", "probabilities": {"a": 1.0}, "confidence": 1.0}, BAND)
    assert got["runner_up"] is None
    assert got["margin"] == 1.0
    assert got["entropy"] == 0.0


def test_choice_low_p_abstains():
    raw = {"choice": "a", "probabilities": {"a": 0.5, "b": 0.3, "c": 0.2}, "confidence": 0.1}
    got = choice_answer(raw, BAND)
    assert got["abstained"] is True
    assert got["runner_up"] == "b"
    assert math.isclose(got["margin"], 0.2)
    assert choice_answer(raw, Band(choice_min_p=0.4))["abstained"] is False


def test_entropy_uniform():
    assert math.isclose(entropy({"a": 0.5, "b": 0.5, "c": 0.0}), math.log(2))


def test_score_bimodal():
    probs = {"0": 0.0, "1": 0.0, "2": 0.0, "3": 0.275, "4": 0.0, "5": 0.719}
    got = score_answer({"score": 4.4, "probabilities": probs, "confidence": 0.6}, ["a"] * 6)
    assert got["bimodal"] is True
    assert got["level"] == 5
    assert got["level_label"] == "a"


def test_score_adjacent_not_bimodal():
    probs = {"0": 0.0, "1": 0.45, "2": 0.5, "3": 0.05}
    got = score_answer({"score": 1.6, "probabilities": probs, "confidence": 0.5}, None)
    assert got["bimodal"] is False
    assert got["level_label"] == "2"


def test_score_label_from_criteria_without_legend():
    probs = {"0": 0.1, "1": 0.9}
    got = score_answer({"score": 0.9, "probabilities": probs, "confidence": 0.8}, ["low", "high"])
    assert got["level_label"] == "high"


def test_missing_key_is_protocol_error():
    with pytest.raises(ToolError) as info:
        derive_answers({}, {"q": NOUL_Q}, BAND)
    assert info.value.code == "OJ_PROTOCOL"
    assert info.value.message == "response lacks answers.q"


def test_type_mismatch_is_protocol_error():
    with pytest.raises(ToolError) as info:
        derive_answers({"q": {"type": "noul", "noul": 0.5}}, {"q": {"type": "choice"}}, BAND)
    assert info.value.code == "OJ_PROTOCOL"


def test_malformed_answer_is_protocol_error():
    with pytest.raises(ToolError) as info:
        derive_answers({"q": {"type": "noul"}}, {"q": NOUL_Q}, BAND)
    assert info.value.code == "OJ_PROTOCOL"
    with pytest.raises(ToolError):
        derive_answers({"q": {"type": "choice", "choice": "z", "probabilities": {"a": 1.0}, "confidence": 1}},
                       {"q": {"type": "choice"}}, BAND)


def test_chunks_estimate_reference_points():
    ten_noul = {f"q{i}": NOUL_Q for i in range(10)}
    assert chunks_estimate(ten_noul) == 1
    levels = ["no", "low", "mid", "high", "top"]
    ten_scores = {f"q{i}": {"type": "score", "criteria": levels} for i in range(10)}
    assert chunks_estimate(ten_scores) == 2
    assert chunks_estimate({f"q{i}": NOUL_Q for i in range(256)}) == 22
    assert chunks_estimate({}) == 1


def test_no_heavy_imports():
    code = ("import sys, openjev_mcp.derive, openjev_mcp.envelope, openjev_mcp.progress;"
            "bad = [m for m in ('httpx', 'mcp', 'jsonschema') if m in sys.modules];"
            "sys.exit(1 if bad else 0)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_capitalised_escape_label_abstains():
    raw = {"type": "choice", "choice": "Other", "probabilities": {"Other": 0.9, "billing": 0.1}, "confidence": 0.9}
    assert choice_answer(raw, Band())["abstained"] is True
