"""batch.importers: every 6.6 'Batch import' fixture. No network, no model."""
from __future__ import annotations

import json
import os
import shutil

import pytest

from openjev_mcp.batch import importers as I
from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "batch")


@pytest.fixture
def root(tmp_path):
    return tmp_path


@pytest.fixture
def cfg(root):
    return Config(roots=(str(root),), transport="stdio")


def put(root, name, data):
    p = root / name
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return str(p)


def run(cfg, path, max_items=5000, **kw):
    return I.import_source({"path": path, **kw}, cfg, max_items=max_items)


def states(r):
    return [i.state for i in r.items]


def codes(r):
    return [w["code"] for w in r.report["warnings"]]


def fails(cfg, path, code, **kw):
    with pytest.raises(ToolError) as e:
        run(cfg, path, **kw)
    assert e.value.code == "OJ_INVALID_INPUT" and e.value.message.startswith(code), e.value.message
    assert e.value.server_detail["finding"]["code"] == code
    return e.value


def test_csv_delimiters(root, cfg):
    for name, d in (("a.csv", ","), ("b.csv", ";"), ("c.csv", "\t")):
        r = run(cfg, put(root, name, f"id{d}text\n1{d}hello\n2{d}world\n"))
        assert states(r) == ["hello", "world"] and r.report["delimiter"] == d and r.report["format"] == "csv"
        assert [i.id for i in r.items] == ["1", "2"]
        assert "W602" in codes(r)               # id/text: "text" matched by name, still reported as a guess


def test_quoted_delimiters_and_newlines():
    r = I.parse_csv('a;b\n"x;y";"line1\nline2"\n"he said ""hi""";2\n', ";")
    assert r[0] == [["a", "b"], ["x;y", "line1\nline2"], ['he said "hi"', "2"]]
    assert I.sniff_delimiter('"a,b,c";x\n1;2') == ";"
    assert I.sniff_delimiter("a,b;c\n") == ","      # tie
    assert I.sniff_delimiter("a\tb;c\n") == "\t"    # tie, tab before ;


def test_fixture_csv_file(cfg, root):
    p = shutil.copy(os.path.join(FIX, "tickets.csv"), root / "tickets.csv")
    r = run(cfg, str(p), state_field="body", id_field="id")
    assert states(r) == ["I can't log in, help", "Charged twice\non one card", "ok"]
    assert [i.id for i in r.items] == ["T-1", "T-2", "T-3"] and r.report["columns"] == ["id", "subject", "body"]
    assert "W602" not in codes(r)
    assert states(run(cfg, str(p), state_field="*"))[2] == {"id": "T-3", "subject": 'Say "hi"', "body": "ok"}


def test_tsv(root, cfg):
    r = run(cfg, put(root, "x.tsv", "a\tbody\n1\tone, two\n"))
    assert states(r) == ["one, two"] and r.report["format"] == "tsv" and r.report["delimiter"] == "\t"


def test_jsonl_key_discovery_over_500(root, cfg):
    lines = [json.dumps({"id": i}) for i in range(499)] + [json.dumps({"id": 499, "text": "late"})]
    lines += [json.dumps({"id": 500, "message": "after the scan window"})]
    r = run(cfg, put(root, "k.jsonl", "\n".join(lines)), state_field="text")
    assert r.report["columns"] == ["id", "text"] and states(r) == ["late"] and "W605" in codes(r)
    r = run(cfg, put(root, "k2.jsonl", '{"foo": 1, "body": "b"}\n{"foo": 2, "body": "c"}\n'))
    assert states(r) == ["b", "c"] and r.report["state_field"] == "body" and "W602" in codes(r)


def test_jsonl_whole_object_strings_and_bad_lines(root, cfg):
    r = run(cfg, put(root, "w.jsonl", '{"a": 1, "b": 2}\n"plain"\nnot json\n'), state_field="*")
    assert states(r) == [{"a": 1, "b": 2}, "plain"]
    w = [x for x in r.report["warnings"] if x["code"] == "W605"][0]
    assert "unparseable" in w["message"]
    r = run(cfg, put(root, "w2.jsonl", '{"a": 1, "b": 2}\n{"a": 3, "b": 4}\n'))
    assert states(r)[0] == {"a": 1, "b": 2} and "W602" in codes(r)


@pytest.mark.parametrize("key", list(I.ARRAY_KEYS))
def test_wrapped_json_array_key_defaults(root, cfg, key):
    r = run(cfg, put(root, "w.json", json.dumps({"meta": [9], key: ["a", "b"]})))
    assert states(r) == ["a", "b"] and r.report["format"] == "json"


def test_wrapped_json_explicit_array_key_and_array(root, cfg):
    assert states(run(cfg, put(root, "w.json", json.dumps({"states": ["x"], "mine": ["a", "b"]})), array_key="mine")) == ["a", "b"]
    assert states(run(cfg, put(root, "a.json", json.dumps([{"text": "t1"}, {"text": "t2"}])))) == ["t1", "t2"]
    with pytest.raises(ToolError):
        run(cfg, put(root, "n.json", json.dumps({"x": [1]})), array_key="nope")


def test_single_object(root, cfg):
    r = run(cfg, put(root, "o.json", json.dumps({"subject": "s", "n": 1})))
    assert states(r) == [{"subject": "s", "n": 1}]
    assert states(run(cfg, put(root, "o2.json", json.dumps({"subject": "s", "text": "t"})))) == ["t"]


def test_lines_and_blocks(root, cfg):
    r = run(cfg, put(root, "l.txt", "one\n\ntwo\nthree\n"), format="lines")
    assert states(r) == ["one", "two", "three"]
    r = run(cfg, put(root, "b.txt", "a1\na2\n\n\n\nb1\n\nc1\nc2\n"), format="blocks")
    assert states(r) == ["a1\na2", "b1", "c1\nc2"] and r.report["format"] == "blocks"
    # sniffed by content for a text file
    assert run(cfg, put(root, "b2.md", "a1\na2\n\nb1\n")).report["format"] == "blocks"
    assert run(cfg, put(root, "l2.log", "x\ny\n")).report["format"] == "lines"
    assert run(cfg, put(root, "l3.txt", "a, b, c\nd, e, f\n")).report["format"] == "lines"   # prose is never csv


def test_sniff_order(root, cfg):
    # content wins nothing over an explicit format, and a .txt jsonl-looking body is jsonl
    assert run(cfg, put(root, "s1.txt", '{"text":"a"}\n{"text":"b"}\n')).report["format"] == "jsonl"
    assert run(cfg, put(root, "s2.txt", '[{"text":"a"},{"text":"b"}]')).report["format"] == "json"
    r = run(cfg, put(root, "s4.json", '{"text":"a"}\n{"text":"b"}\n'))      # json that is really jsonl
    assert r.report["format"] == "jsonl" and states(r) == ["a", "b"]
    r = run(cfg, put(root, "s5.jsonl", json.dumps({"items": ["p", "q"]}, indent=2)))   # pretty document as .jsonl
    assert states(r) == ["p", "q"]


def test_utf16_bom_and_utf8_bom(root, cfg):
    r = run(cfg, put(root, "u.csv", "﻿text\nzüé\n".encode("utf-16")))
    assert r.report["encoding"] == "utf-16" and states(r) == ["züé"] and "W603" not in codes(r)
    r = run(cfg, put(root, "u8.csv", b"\xef\xbb\xbftext\nok\n"))
    assert r.report["encoding"] == "utf-8" and r.report["columns"] == ["text"]


def test_cp1252_w603(root, cfg):
    r = run(cfg, put(root, "c.csv", "text\ncafé\n".encode("cp1252")))
    assert states(r) == ["café"] and r.report["encoding"] == "cp1252" and "W603" in codes(r)
    r = run(cfg, put(root, "c2.csv", "text\ncafé\n".encode("cp1252")), encoding="cp1252")
    assert "W603" not in codes(r)
    with pytest.raises(ToolError):
        run(cfg, put(root, "c3.csv", "text\ncafé\n".encode("cp1252")), encoding="utf-8")


def test_xlsx_and_binary_e030(root, cfg):
    fails(cfg, put(root, "s.xlsx", b"PK\x03\x04"), "E030")
    assert "export the sheet as CSV" in fails(cfg, put(root, "t.ods", b"x"), "E030").message
    assert "binary file" in fails(cfg, put(root, "z.zip", b"PK"), "E030").message
    assert "binary file" in fails(cfg, put(root, "bin.txt", b"abc\x00\x01def\n"), "E030").message


def test_ojui_batch_restore_and_v2(root, cfg):
    p = shutil.copy(os.path.join(FIX, "ojui_batch_v2.json"), root / "b.json")
    r = run(cfg, str(p))
    assert r.report["format"] == "ojui-batch" and states(r) == ["site is down", {"subject": "refund", "body": "please"}]
    assert r.questions["urgent"]["type"] == "noul" and r.options == {"samples": 1}
    assert codes(r) == ["W604", "W604"]
    msgs = [w["message"] for w in r.report["warnings"]]
    assert any("version 2" in m for m in msgs) and any("images are not restored" in m for m in msgs)
    v1 = {"format": "ojui-batch", "version": 1, "questions": {}, "rows": [{"state": "a"}]}
    r = run(cfg, put(root, "v1.json", json.dumps(v1)))
    assert states(r) == ["a"] and r.questions is None and codes(r) == []


def test_ojui_export_e032(root, cfg):
    shutil.copy(os.path.join(FIX, "ojui_export.json"), root / "e.json")
    fails(cfg, str(root / "e.json"), "E032")
    fails(cfg, str(root / "e.json"), "E032", format="json")


def test_ids_duplicates_empty(root, cfg):
    r = run(cfg, put(root, "i.csv", "k,text\nA,x\nB,y\n"), id_field="k", state_field="text")
    assert [i.id for i in r.items] == ["A", "B"] and [i.index for i in r.items] == [1, 2]
    e = fails(cfg, put(root, "d.csv", "k,text\nA,x\nB,y\nA,z\n"), "E031", id_field="k", state_field="text")
    assert "'A'" in e.message
    fails(cfg, put(root, "e1.txt", "  \n\n"), "E031")
    fails(cfg, put(root, "e2.csv", "text\n"), "E031")
    fails(cfg, put(root, "e3.jsonl", '{"text": ""}\n'), "E031")
    with pytest.raises(ToolError):
        run(cfg, put(root, "i2.csv", "a,b\n1,2\n"), id_field="nope")


def test_default_ids_are_positions(root, cfg):
    r = run(cfg, put(root, "p.txt", "a\nb\nc\n"))
    assert [(i.index, i.id) for i in r.items] == [(1, "1"), (2, "2"), (3, "3")]


def test_merge_csv_same_and_different_headers(root, cfg):
    a = put(root, "a.csv", "id,text\n1,a1\n2,a2\n")
    b = put(root, "b.csv", "id,text\n3,b1\n")
    r = run(cfg, a, also=[b], id_field="id")
    assert states(r) == ["a1", "a2", "b1"] and [i.id for i in r.items] == ["1", "2", "3"] and r.report["format"] == "csv"
    c = put(root, "c.csv", "subject,body\ns,c1 is the long body text\n")
    r = run(cfg, a, also=[c])
    assert states(r) == ["a1", "a2", "c1 is the long body text"] and codes(r).count("W602") == 2
    r = run(cfg, a, also=[put(root, "j.jsonl", '{"text": "j1"}\n'), put(root, "l.txt", "plain\n")])
    assert states(r) == ["a1", "a2", "j1", "plain"] and r.report["format"] == "mixed"
    with pytest.raises(ToolError):
        run(cfg, a, also=[a] * 8)
    fails(cfg, put(root, "d1.csv", "k,text\nA,x\n"), "E031", also=[put(root, "d2.csv", "k,text\nA,y\n")], id_field="k")


def test_max_items_w601(root, cfg):
    r = run(cfg, put(root, "m.csv", "text\n" + "\n".join(f"r{i}" for i in range(10)) + "\n"), max_items=4)
    assert states(r) == ["r0", "r1", "r2", "r3"] and r.report["truncated"] is True and r.report["row_count"] == 4
    w = [x for x in r.report["warnings"] if x["code"] == "W601"][0]
    assert "6 rows" in w["message"]
    assert run(cfg, put(root, "m2.txt", "a\nb\nc\n"), max_items=3).report["truncated"] is False
    assert len(I.import_items([{"state": "x"}] * 5, max_items=2).items) == 2


def test_empty_rows_w605(root, cfg):
    r = run(cfg, put(root, "e.csv", "id,text\n1,a\n,\n\n2,\n3,b\n"), state_field="text")
    assert states(r) == ["a", "b"]
    w = [x for x in r.report["warnings"] if x["code"] == "W605"][0]
    assert "3 empty rows" in w["message"]
    assert "W605" not in codes(run(cfg, put(root, "n.csv", "id,text\n1,a\n"), state_field="text"))


def test_w602_guess(root, cfg):
    r = run(cfg, put(root, "g.csv", "who,notes\nal,short\nbo,a much longer cell of free text\n"))
    assert r.report["state_field"] == "notes" and states(r)[0] == "short"
    w = [x for x in r.report["warnings"] if x["code"] == "W602"][0]
    assert "notes" in w["message"]
    r = run(cfg, put(root, "g2.csv", "who,Body\nal,x\n"))
    assert r.report["state_field"] == "Body"
    assert "W602" not in codes(run(cfg, put(root, "g3.csv", "who,notes\nal,x\n"), state_field="notes"))
    with pytest.raises(ToolError):
        run(cfg, put(root, "g4.csv", "who,notes\nal,x\n"), state_field="zzz")


def test_state_template(root, cfg):
    p = put(root, "t.csv", "id,subject,body\nT1,Hi,Please help\nT2,,\nT3,Bye,\n")
    r = run(cfg, p, state_template="Subject: {subject}\n\n{body} ({id})", id_field="id")
    assert states(r) == ["Subject: Hi\n\nPlease help (T1)", "Subject: Bye\n\n (T3)"]
    assert "W602" not in codes(r) and "W605" in codes(r) and r.report["state_field"] is None
    r = run(cfg, p, state_template="{id}:{subject}", state_field="body")        # template wins over state_field
    assert states(r) == ["1:Hi", "2:Bye"]
    j = put(root, "t.jsonl", '{"a": "x", "b": {"k": 1}}\n')
    assert states(run(cfg, j, state_template="{a} / {b}")) == ['x / {"k": 1}']
    with pytest.raises(ToolError):
        run(cfg, p, state_template="{nope}")


def test_paths_are_confined(root, cfg, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "x.txt"
    outside.write_text("a\n")
    with pytest.raises(ToolError) as e:
        run(cfg, str(outside))
    assert e.value.code == "OJ_INVALID_INPUT"
    assert pytest.raises(ToolError, run, cfg, str(root / "missing.txt")).value.code == "OJ_NOT_FOUND"


def test_import_items(cfg):
    r = I.import_items([{"id": "a", "state": "x"}, {"state": {"k": 1}}], max_items=10)
    assert [(i.id, i.state) for i in r.items] == [("a", "x"), ("2", {"k": 1})] and r.report["format"] == "items"
    with pytest.raises(ToolError) as e:
        I.import_items([{"id": "a", "state": "x"}, {"id": "a", "state": "y"}], max_items=10)
    assert e.value.message.startswith("E031")
    with pytest.raises(ToolError) as e:
        I.import_items([], max_items=10)
    assert e.value.message.startswith("E031")


def test_codes_registered():
    from openjev_mcp import lint
    assert I.Finding("W601", "p", "m").code in lint.WARNING_CODES
    assert {"E030", "E031", "E032"} <= lint.ERROR_CODES and {"W601", "W602", "W603", "W604", "W605"} <= lint.WARNING_CODES
    assert lint.BATCH_CODES.isdisjoint(lint.CODES)
