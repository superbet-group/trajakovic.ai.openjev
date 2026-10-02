import hashlib
import json

from openjev_mcp import wire

Q = {"q": {"type": "noul", "instructions": "Is this a billing issue?"}}


def test_key_order_matches_spec_example():
    body = wire.build_body("openjev-latest", "s", Q, {"samples": 1})
    assert list(body) == ["model", "samples", "state", "questions"]


def test_option_order_and_none_dropped():
    opts = {"sequential": True, "think": 64, "samples": 3, "steps": None, "timeout_ms": 5000}
    body = wire.build_body("m", "s", Q, opts, images=["data:x"])
    assert list(body) == ["model", "samples", "think", "sequential", "state", "questions", "images"]
    assert "timeout_ms" not in body and "steps" not in body


def test_images_omitted_when_empty():
    assert "images" not in wire.build_body("m", "s", Q, None, images=[])


def test_body_bytes_separators_and_utf8():
    raw = wire.body_bytes({"a": 1, "b": ["č", {"c": None}]})
    assert raw == '{"a":1,"b":["č",{"c":null}]}'.encode("utf-8")
    assert wire.dumps_text({"a": [1, 2]}) == '{"a":[1,2]}'


def test_hashes_deterministic():
    raw = wire.body_bytes(wire.build_body("m", "s", Q))
    assert wire.body_hash(raw) == wire.body_hash(raw)
    assert wire.body_hash(raw) == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert wire.body_hash(b"x") != wire.body_hash(b"y")


def test_canonical_hash_ignores_key_order():
    a = {"x": 1, "y": {"p": 1, "q": [1, 2]}}
    b = {"y": {"q": [1, 2], "p": 1}, "x": 1}
    assert wire.canonical_hash(a) == wire.canonical_hash(b)
    assert wire.canonical_hash(a) != wire.canonical_hash({"x": 2})
    assert wire.canonical_hash(a).startswith("sha256:")


def test_body_hash_is_key_order_dependent():
    a, b = {"x": 1, "y": 2}, {"y": 2, "x": 1}
    assert wire.body_hash(wire.body_bytes(a)) != wire.body_hash(wire.body_bytes(b))


def test_text_block_roundtrip():
    obj = {"a": "š", "b": [1.5, None, True]}
    assert json.loads(wire.dumps_text(obj)) == obj
