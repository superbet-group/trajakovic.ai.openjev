"""audit_store: record write/read rules, the openjev://audits resource (ttlMs 0, private)."""
from __future__ import annotations

import json
import os

import pytest
import stubs

from openjev_mcp import audit_store as A
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.resources import ResourceNotFound
from openjev_mcp.tools import ToolContext

pytestmark = pytest.mark.anyio

HEX = "ab" * 32
REC = {"openjev_mcp": "calibrate", "v": 1, "question_hash": "sha256:" + HEX, "model_resolved": "m", "n": 1, "per_question": {}, "items": []}


def cfg(tmp_path, **kw):
    return Config(roots=(str(tmp_path.resolve()),), audit_dir=str(tmp_path / "audits"), **kw)


def ctx_for(config):
    return ToolContext(config, None, None, None, None)


def test_is_record():
    assert A.is_record(REC)
    for bad in ({}, {"openjev_mcp": "batch", "question_hash": "x"}, {"openjev_mcp": "calibrate"}, [REC], "x", None):
        assert not A.is_record(bad)


def test_write_new_then_overwrite_only_a_record(tmp_path):
    config = cfg(tmp_path)
    target = A.write_record(str(tmp_path / "a.json"), REC, config)
    assert json.loads(target.read_text()) == REC
    A.write_record(str(tmp_path / "a.json"), {**REC, "n": 2}, config)
    assert json.loads(target.read_text())["n"] == 2
    assert not [p for p in os.listdir(tmp_path) if p.startswith(".tmp")]


@pytest.mark.parametrize("content", ['{"keep": 1}', "not json", '{"openjev_mcp": "batch", "question_hash": "x"}', "[]"])
def test_write_refuses_a_file_that_is_not_a_calibrate_record(tmp_path, content):
    victim = tmp_path / "v.json"
    victim.write_text(content)
    with pytest.raises(ToolError) as e:
        A.write_record(str(victim), REC, cfg(tmp_path))
    assert e.value.code == "OJ_INVALID_INPUT" and victim.read_text() == content


def test_write_path_rules(tmp_path):
    config = cfg(tmp_path)
    for bad in (tmp_path / "x.txt", tmp_path / ".hidden.json", tmp_path.parent / "outside.json", tmp_path / "nodir" / "x.json"):
        with pytest.raises(ToolError):
            A.write_record(str(bad), REC, config)
    (tmp_path / "real.json").write_text(json.dumps(REC))
    os.symlink(tmp_path / "real.json", tmp_path / "link.json")
    with pytest.raises(ToolError):
        A.write_record(str(tmp_path / "link.json"), REC, config)


def test_load_record(tmp_path):
    config = cfg(tmp_path)
    p = tmp_path / "r.json"
    p.write_text(json.dumps(REC))
    assert A.load_record(str(p), config) == REC
    p.write_text("{}")
    with pytest.raises(ToolError):
        A.load_record(str(p), config)
    with pytest.raises(ToolError) as e:
        A.load_record(str(tmp_path / "gone.json"), config)
    assert e.value.code == "OJ_NOT_FOUND"


def test_audits_template():
    t = A.audits_template()
    assert t["uriTemplate"] == "openjev://audits/{question_hash}" and t["mimeType"] == "application/json"
    assert t["annotations"] == {"audience": ["user", "assistant"], "priority": 0.7}
    assert A.AUDIT_URI.match("openjev://audits/sha256:" + HEX) and not A.AUDIT_URI.match("openjev://schema")


async def test_read_audit_by_hash_forms(tmp_path):
    config = cfg(tmp_path)
    A.write_record(str(tmp_path / "a.json"), REC, config)
    for key in ("sha256:" + HEX, HEX, "sha256%3A" + HEX):
        uri = "openjev://audits/" + key
        got = await A.read_audit(uri, ctx_for(config))
        assert got["ttlMs"] == 0 and got["cacheScope"] == "private"
        c = got["contents"][0]
        assert c["uri"] == uri and c["mimeType"] == "application/json" and json.loads(c["text"]) == REC


async def test_read_audit_not_found_and_hostile(tmp_path):
    config = cfg(tmp_path)
    os.makedirs(config.audit_dir)
    (tmp_path / "audits" / ("cd" * 32 + ".json")).write_text('{"not": "a record"}')
    for key in ("sha256:" + "ef" * 32, "cd" * 32, "../../etc/passwd", "sha256:zz", "", HEX[:-1]):
        with pytest.raises(ResourceNotFound):
            await A.read_audit("openjev://audits/" + key, ctx_for(config))
    with pytest.raises(ResourceNotFound):
        await A.read_audit("openjev://audits/" + HEX, ctx_for(Config(roots=(str(tmp_path),), audit_dir="")))
