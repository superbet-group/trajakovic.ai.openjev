"""Batch cursor codec and the three 2.11 checks."""
from __future__ import annotations

import base64
import json

import pytest

from openjev_mcp.batch import cursor
from openjev_mcp.errors import ToolError

ARGS = {"questions": {"q": {"type": "noul"}}, "output_path": "/x/o.jsonl", "resume": True}


def token(**kw):
    base = dict(run="sha256:r", offset=25, args=cursor.args_hash(ARGS), out_bytes=1000, out_lines=26)
    return cursor.encode(**{**base, **kw})


def test_round_trip_and_compact():
    t = token()
    assert "=" not in t and "/" not in t and "+" not in t
    d = cursor.decode(t)
    assert d == {"v": 1, "run": "sha256:r", "offset": 25, "args": cursor.args_hash(ARGS), "out": {"bytes": 1000, "lines": 26}}
    assert "/x/o.jsonl" not in base64.urlsafe_b64decode(t + "==").decode()   # no path inside


def test_excluded_args_do_not_change_the_hash():
    base = cursor.args_hash(ARGS)
    changed = {**ARGS, "cursor": "abc", "max_items_per_call": 3, "time_budget_s": 9, "concurrency": 4, "detail": "full",
               "max_inline_results": 1, "export": [{"format": "csv", "path": "/x.csv"}]}
    assert cursor.args_hash(changed) == base
    assert cursor.args_hash({**ARGS, "resume": False}) != base
    assert cursor.args_hash({**ARGS, "output_path": "/y.jsonl"}) != base


@pytest.mark.parametrize("bad", ["", "!!!", "e30", base64.urlsafe_b64encode(b"[1]").decode(),
                                 base64.urlsafe_b64encode(json.dumps({"v": 2, "run": "r", "offset": 0, "args": "a",
                                                                      "out": {"bytes": 0, "lines": 0}}).encode()).decode(),
                                 base64.urlsafe_b64encode(json.dumps({"v": 1, "run": "r", "offset": -1, "args": "a",
                                                                      "out": {"bytes": 0, "lines": 0}}).encode()).decode()])
def test_tampered_cursor(bad):
    with pytest.raises(ToolError) as e:
        cursor.decode(bad)
    assert e.value.code == "OJ_INVALID_INPUT" and e.value.message == "invalid cursor"


def test_check_ok_and_concurrency_change_accepted():
    d = cursor.decode(token())
    h = cursor.args_hash({**ARGS, "concurrency": 4, "max_items_per_call": 7})
    cursor.check(d, run_id="sha256:r", args_hash=h, out_size=1000)
    cursor.check(d, run_id="sha256:r", args_hash=h, out_size=5000)


def test_changed_arguments():
    d = cursor.decode(token())
    for kw in (dict(run_id="sha256:other", args_hash=d["args"]), dict(run_id="sha256:r", args_hash="zz")):
        with pytest.raises(ToolError) as e:
            cursor.check(d, out_size=1000, **kw)
        assert e.value.code == "OJ_INVALID_INPUT"
        assert e.value.message == "arguments changed since this cursor; drop cursor, keep resume:true"


def test_shrunk_output():
    d = cursor.decode(token())
    for size in (999, 0, None):
        with pytest.raises(ToolError) as e:
            cursor.check(d, run_id="sha256:r", args_hash=d["args"], out_size=size)
        assert e.value.message == "output file shrank since the cursor; call again without cursor"
