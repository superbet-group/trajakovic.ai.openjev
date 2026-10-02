"""JSONL store: identity hashes, flock handle, resume checks, corrupt-line policy (Decision 3)."""
from __future__ import annotations

import json
import os

import pytest

from openjev_mcp.batch import store
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError

Q = {"q": {"type": "noul", "instructions": "x"}}


@pytest.fixture
def cfg(tmp_path):
    return Config(roots=(str(tmp_path.resolve()),))


@pytest.fixture
def out(tmp_path):
    return str(tmp_path.resolve() / "o.jsonl")


def header(**kw):
    qh = store.question_hash(Q)
    src = kw.pop("source", {"kind": "items", "row_count": 3})
    rid = store.run_id(qh, src, {}, "fast", 4)
    return store.make_header(run_id=rid, question_hash=qh, source=src, **kw)


def row(i, status="ok"):
    return {"index": i, "id": f"r{i}", "status": status, "state_hash": "sha256:x"}


def lines(path):
    return open(path, "rb").read().split(b"\n")


def invalid(fn, *a, **kw):
    with pytest.raises(ToolError) as e:
        fn(*a, **kw)
    assert e.value.code == "OJ_INVALID_INPUT"
    return e.value.message


def test_hashes_are_canonical():
    assert store.question_hash({"a": 1, "b": {"y": 1, "x": 2}}) == store.question_hash({"b": {"x": 2, "y": 1}, "a": 1})
    assert store.question_hash(Q).startswith("sha256:")
    base = store.run_id("sha256:q", {"k": 1}, {}, "fast", 4)
    assert base == store.run_id("sha256:q", {"k": 1}, {}, "fast", 4)
    for other in (store.run_id("sha256:q2", {"k": 1}, {}, "fast", 4), store.run_id("sha256:q", {"k": 2}, {}, "fast", 4),
                  store.run_id("sha256:q", {"k": 1}, {"think": 1}, "fast", 4),
                  store.run_id("sha256:q", {"k": 1}, {}, "server_default", 4), store.run_id("sha256:q", {"k": 1}, {}, "fast", 0)):
        assert other != base


def test_header_shape(cfg):
    h = header()
    assert h["openjev_mcp"] == "batch" and h["v"] == 2 and h["spec"] == "1.2" and h["created_at"].endswith("Z")
    from openjev_mcp import schemas
    assert set(schemas.DEFS["BatchHeader"]["required"]) <= set(h)


def test_new_file_writes_header_then_rows(cfg, out):
    h = header()
    with store.open_output(out, h, resume=False, config=cfg) as handle:
        assert handle.created and handle.lines == 1
        handle.append(row(1))
        handle.append(row(2))
        assert handle.size() == os.path.getsize(out)
        assert handle.lines == 3
    ls = lines(out)
    assert ls[-1] == b"" and json.loads(ls[0]) == h and [json.loads(x)["id"] for x in ls[1:-1]] == ["r1", "r2"]
    header_, rows, n, warnings = store.read_output(out, cfg)
    assert header_ == h and set(rows) == {"r1", "r2"} and n == 3 and warnings == []


def test_resume_same_run_appends_and_last_row_wins(cfg, out):
    h = header()
    with store.open_output(out, h, resume=False, config=cfg) as handle:
        handle.append(row(1, "error"))
    with store.open_output(out, h, resume=True, config=cfg) as handle:
        assert not handle.created and handle.rows["r1"]["status"] == "error"
        handle.append(row(1, "ok"))
    _, rows, n, _ = store.read_output(out, cfg)
    assert n == 3 and rows["r1"]["status"] == "ok"


def test_run_id_mismatch_refused(cfg, out):
    store.open_output(out, header(), resume=False, config=cfg).close()
    other = header(source={"kind": "items", "row_count": 4})
    assert invalid(store.open_output, out, other, resume=True, config=cfg) == \
        "output_path belongs to another job (questions, source or options differ); use a new output_path"


def test_resume_false_on_existing_refused_but_empty_ok(cfg, out, tmp_path):
    store.open_output(out, header(), resume=False, config=cfg).close()
    assert invalid(store.open_output, out, header(), resume=False, config=cfg) == "output exists; pass resume:true or a new path"
    empty = str(tmp_path.resolve() / "e.jsonl")
    open(empty, "w").close()
    store.open_output(empty, header(), resume=False, config=cfg).close()
    assert json.loads(lines(empty)[0])["openjev_mcp"] == "batch"


def test_second_call_refused_while_locked_then_released(cfg, out):
    first = store.open_output(out, header(), resume=False, config=cfg)
    assert invalid(store.open_output, out, header(), resume=True, config=cfg) == "output_path is in use by another batch call"
    first.append(row(1))
    first.close()
    first.close()   # idempotent
    with store.open_output(out, header(), resume=True, config=cfg) as again:
        assert set(again.rows) == {"r1"}
    with pytest.raises(ValueError):
        first.append(row(2))


def test_path_rules(cfg, tmp_path):
    outside = str(tmp_path.resolve().parent / "x.jsonl")
    assert "outside" in invalid(store.open_output, outside, header(), resume=False, config=cfg)
    assert "extension" in invalid(store.open_output, str(tmp_path.resolve() / "o.csv"), header(), resume=False, config=cfg)


def test_corrupt_middle_line_refused(cfg, out):
    with store.open_output(out, header(), resume=False, config=cfg) as handle:
        handle.append(row(1))
        handle.append(row(2))
    data = lines(out)
    data[2] = b"{not json"
    open(out, "wb").write(b"\n".join(data))
    msg = "output_path line 3 is not valid JSON; fix or truncate the file"
    assert invalid(store.read_output, out, cfg) == msg
    assert invalid(store.open_output, out, header(), resume=True, config=cfg) == msg


def test_interrupted_last_line_ignored_then_truncated(cfg, out):
    with store.open_output(out, header(), resume=False, config=cfg) as handle:
        handle.append(row(1))
    good = os.path.getsize(out)
    with open(out, "ab") as f:
        f.write(b'{"index": 2, "id": "r2", "sta')
    _, rows, n, warnings = store.read_output(out, cfg)
    assert set(rows) == {"r1"} and n == 2 and len(warnings) == 1 and "line 3" in warnings[0]
    with store.open_output(out, header(), resume=True, config=cfg) as handle:
        assert len(handle.warnings) == 1 and os.path.getsize(out) == good
        handle.append(row(2))
    _, rows, n, warnings = store.read_output(out, cfg)
    assert set(rows) == {"r1", "r2"} and n == 3 and warnings == []


def test_complete_unterminated_last_line_kept(cfg, out):
    with store.open_output(out, header(), resume=False, config=cfg) as handle:
        handle.append(row(1))
    raw = open(out, "rb").read().rstrip(b"\n")
    open(out, "wb").write(raw)
    with store.open_output(out, header(), resume=True, config=cfg) as handle:
        handle.append(row(2))
    _, rows, n, _ = store.read_output(out, cfg)
    assert set(rows) == {"r1", "r2"} and n == 3


def test_header_missing_refused_on_resume(cfg, out):
    open(out, "w").write(json.dumps(row(1)) + "\n")
    assert "no batch header" in invalid(store.open_output, out, header(), resume=True, config=cfg)
    h, rows, n, _ = store.read_output(out, cfg)
    assert h is None and set(rows) == {"r1"}


def test_append_is_one_write(cfg, out, monkeypatch):
    writes = []
    real = os.write
    monkeypatch.setattr(os, "write", lambda fd, b: writes.append(bytes(b)) or real(fd, b))
    with store.open_output(out, header(), resume=False, config=cfg) as handle:
        writes.clear()
        handle.append({**row(1), "state": "ü\n" * 5000})
    assert len(writes) == 1 and writes[0].endswith(b"}\n") and writes[0].count(b"\n") == 1


def test_output_mode_0600_and_symlink_swap_refused(cfg, out, tmp_path, monkeypatch):
    with store.open_output(out, header(), resume=False, config=cfg):
        pass
    assert os.stat(out).st_mode & 0o777 == 0o600
    target = tmp_path / "victim.jsonl"
    target.write_text("")
    link = str(tmp_path.resolve() / "l.jsonl")
    real = store.resolve_write

    def swap(path, *a, **kw):
        res = real(path, *a, **kw)
        os.symlink(target, link)   # lands between the check and the open
        return res
    monkeypatch.setattr(store, "resolve_write", swap)
    with pytest.raises(OSError):
        store.open_output(link, header(), resume=False, config=cfg)
    assert target.read_text() == ""
