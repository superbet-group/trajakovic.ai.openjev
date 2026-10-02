"""batch.exporters: csv/markdown/ojui-batch/jsonl layouts, new-only writes, ojui-batch round trip. No network."""
from __future__ import annotations

import csv
import io
import json
import os

import pytest

from openjev_mcp.batch import exporters as X
from openjev_mcp.batch import importers as I
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "batch", "rows_ncs.jsonl")
QUESTIONS = {
    "urgent": {"type": "noul", "instructions": "Is this urgent?", "criteria": {"true": "outage", "false": "can wait"}},
    "team": {"type": "choice", "instructions": "Which team?", "criteria": {"payments": "cards", "platform": "servers", "other": "else"}},
    "sev": {"type": "score", "instructions": "How severe?", "criteria": ["minor", "major", "critical"]}}
OPTIONS = {"samples": 1}


@pytest.fixture
def data():
    recs = [json.loads(x) for x in open(FIX, encoding="utf-8")]
    return recs[0], recs[1:]


@pytest.fixture
def cfg(tmp_path):
    return Config(roots=(str(tmp_path),), transport="stdio")


def parse(text):
    return list(csv.reader(io.StringIO(text, newline="")))


def test_csv_columns_match_playground_order(data):
    head, rows = data
    t = parse(X.to_csv(head, rows, QUESTIONS))
    # Playground flatColumns(): noul -> noul, confidence; choice -> choice, confidence, p_<key>; score -> score, confidence, p_<i>
    assert t[0] == ["index", "id", "state", "status",
                    "urgent.noul", "urgent.confidence",
                    "team.choice", "team.confidence", "team.p_payments", "team.p_platform", "team.p_other",
                    "sev.score", "sev.confidence", "sev.p_0", "sev.p_1", "sev.p_2",
                    "latency_ms", "input_tokens", "output_tokens", "request_id", "error"]
    a, b = t[1], t[2]
    assert a[:4] == ["1", "a", "site down, refunds | broken", "ok"]
    assert a[4:] == ["0.97", "0.94", "platform", "0.7", "0.15", "0.8", "0.05", "1.8", "0.75", "0.05", "0.15", "0.8",
                     "150.5", "120", "4", "r1", ""]
    assert b[:4] == ["2", "b", "line1\nline2", "error"] and b[4:-1] == [""] * 12 + ["30000", "", "", ""]
    assert b[-1] == "OJ_TIMEOUT timed out, sorry"
    assert all(len(r) == len(t[0]) for r in t)


def test_csv_quoting_rfc4180():
    out = X.to_csv({}, [{"index": 1, "id": "x", "state": 'a "q", b\nc', "status": "ok"}], {})
    assert '"a ""q"", b\nc"' in out and out.endswith("\n") and "\r" not in out


def test_markdown_escapes_and_truncates(data):
    head, rows = data
    rows[0]["state"] = "a|b\nc " + "x" * 80
    md = X.to_markdown(head, rows, QUESTIONS).splitlines()
    assert md[0] == "| index | id | state | urgent | team | sev | latency_ms | input_tokens |"
    assert md[1] == "|---|---|---|---|---|---|---|---|"
    assert md[2].startswith("| 1 | a | a\\|b c x") and "\n" not in md[2]
    cell = md[2].split(" | ")[2]
    assert len(cell) <= 62 and cell.endswith("…")
    assert "0.97 yes | platform | 2 critical | 150.5 | 120 |" in md[2]
    assert "error OJ_TIMEOUT" in md[3] and "line1 line2" in md[3]


def test_jsonl_header_first(data):
    head, rows = data
    lines = X.to_jsonl(head, rows).splitlines()
    assert json.loads(lines[0]) == head and [json.loads(x)["id"] for x in lines[1:]] == ["a", "b"]


def test_ojui_batch_layout_and_roundtrip(data, cfg, tmp_path):
    head, rows = data
    text = X.to_ojui_batch(head, rows, QUESTIONS, OPTIONS, exported_at=1759400000000, title="triage")
    d = json.loads(text)
    assert list(d) == ["format", "version", "exportedAt", "title", "questions", "options", "imageCount", "rows"]
    assert d["format"] == "ojui-batch" and d["version"] == 1 and d["imageCount"] == 0
    assert list(d["rows"][0]) == ["index", "state", "status", "model", "answers", "usage", "clientMs", "serverTiming",
                                  "requestId", "bodyHash", "error"]
    assert d["rows"][0]["clientMs"] == 150.5 and d["rows"][0]["requestId"] == "r1" and d["rows"][0]["bodyHash"] == "sha256:b1"
    path = X.write_new(str(tmp_path / "out.json"), text, cfg, ".json")
    r = I.import_source({"path": path}, cfg, max_items=100)
    assert [i.state for i in r.items] == [x["state"] for x in rows]
    assert r.questions == QUESTIONS and r.options == OPTIONS
    assert r.report["format"] == "ojui-batch" and r.report["warnings"] == []


def test_write_new_refuses_existing_and_bad_targets(cfg, tmp_path):
    p = str(tmp_path / "e.csv")
    assert X.write_new(p, "a,b\n", cfg, ".csv") == os.path.realpath(p)
    assert open(p, encoding="utf-8").read() == "a,b\n"
    with pytest.raises(ToolError) as e:
        X.write_new(p, "again", cfg, ".csv")
    assert e.value.code == "OJ_INVALID_INPUT" and open(p).read() == "a,b\n"
    for bad in (str(tmp_path / "x.md"), str(tmp_path / ".hidden.csv"), str(tmp_path.parent / "o.csv")):
        with pytest.raises(ToolError):
            X.write_new(bad, "t", cfg, ".csv")
    (tmp_path / "t.md").write_text("")
    assert X.write_new(str(tmp_path / "n.md"), "| a |\n", cfg, ".md").endswith("n.md")


def test_make_header_keeps_questions_for_batch_results():
    from openjev_mcp.batch import store
    h = store.make_header(run_id="r", question_hash="q", questions=QUESTIONS)
    assert h["questions"] == QUESTIONS and list(h)[0] == "openjev_mcp"
    assert "questions" not in store.make_header(run_id="r", question_hash="q")
