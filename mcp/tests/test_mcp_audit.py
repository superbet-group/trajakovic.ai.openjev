from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from openjev_mcp.audit import REQUEST_META, AuditLog, read_record
from openjev_mcp.wire import canonical_hash

BODY = {"model": "openjev-0.1", "state": {"b": 1, "a": "secret"},
        "questions": {"q": {"type": "noul", "instructions": "x"}}}


def outcome():
    return SimpleNamespace(raw={"model": "openjev-0.1-r"}, answers={"q": {"type": "noul", "p": 0.9}},
                           meta={"request_ids": ["r1", "r2"], "latency_ms": 12.5},
                           results=[SimpleNamespace(request_id="r1"), SimpleNamespace(request_id="r2")])


def test_record_shape_without_state():
    rec = read_record("yes_no", BODY, outcome(), log_states=False, decision="yes")
    assert list(rec) == ["ts", "tool", "question_hash", "state_hash", "model_resolved", "request_id",
                         "answers", "decision", "latency_ms", "degraded"]
    assert rec["tool"] == "yes_no"
    assert rec["model_resolved"] == "openjev-0.1-r"
    assert rec["request_id"] == "r2"
    assert rec["decision"] == "yes" and rec["degraded"] is False
    assert rec["latency_ms"] == 12.5
    assert "secret" not in json.dumps(rec)


def test_record_log_states_adds_state():
    rec = read_record("ask", BODY, outcome(), log_states=True, degraded=True)
    assert rec["state"] == BODY["state"]
    assert rec["degraded"] is True and rec["decision"] is None


def test_hashes_independent_of_key_order():
    other = {"questions": {"q": {"instructions": "x", "type": "noul"}}, "state": {"a": "secret", "b": 1}}
    a = read_record("ask", BODY, outcome(), log_states=False)
    b = read_record("ask", other, outcome(), log_states=False)
    assert a["question_hash"] == b["question_hash"] == canonical_hash(BODY["questions"])
    assert a["state_hash"] == b["state_hash"] == canonical_hash(BODY["state"])
    assert a["question_hash"].startswith("sha256:")


def test_request_id_falls_back_to_results():
    out = outcome()
    out.meta = {"latency_ms": 1}
    assert read_record("ask", BODY, out, log_states=False)["request_id"] == "r2"


def test_write_appends_whole_lines(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(str(path))
    log.write({"a": 1})
    log.write({"b": "é"})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"a": 1}, {"b": "é"}]
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_unwritable_path_disables_after_one_line(tmp_path, capsys):
    log = AuditLog(str(tmp_path / "missing" / "audit.jsonl"))
    log.write({"a": 1})
    log.write({"a": 2})
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1
    assert "audit log" in err


def test_concurrent_writers_produce_whole_lines(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(str(path))
    payload = "x" * 4000

    def work(n: int):
        for i in range(25):
            log.write({"n": n, "i": i, "pad": payload})

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200
    assert all(json.loads(line)["pad"] == payload for line in lines)


def test_record_carries_trace_context_from_request_meta():
    token = REQUEST_META.set({"traceparent": "00-aa-bb-01", "tracestate": "k=v", "baggage": "x=1"})
    try:
        rec = read_record("ask", BODY, outcome(), log_states=False)
    finally:
        REQUEST_META.reset(token)
    assert rec["traceparent"] == "00-aa-bb-01" and rec["tracestate"] == "k=v" and "baggage" not in rec
    assert "traceparent" not in read_record("ask", BODY, outcome(), log_states=False)
