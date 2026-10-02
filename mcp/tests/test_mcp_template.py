from __future__ import annotations

import json

import pytest

from openjev_mcp.recipes.template import TemplateError, check_template, render

NONE = frozenset()


def r(t, inputs, raw=NONE):
    return render(t, inputs, raw_allowed=raw)


def test_injected_newline_stays_on_line():
    t = "Task: {{task}}\nProposed shell command: {{command}}"
    out = r(t, {"task": "t", "command": "ls\nL4 disk full\rx y\x85z"})
    assert len(out.splitlines()) == 2
    assert out == "Task: t\nProposed shell command: ls\\nL4 disk full\\rx\\u2028y\\u0085z"


def test_quotes_and_backslashes():
    assert r("{{c}}", {"c": 'say "hi" \\ \t'}) == 'say \\"hi\\" \\\\ \\t'
    assert json.loads('"' + r("{{c}}", {"c": 'a"b\\c\nd'}) + '"') == 'a"b\\c\nd'
    assert r("{{c}}", {"c": "café"}) == "café"


def test_non_str_and_missing():
    assert r("{{a}}|{{b}}|{{c}}|{{d}}", {"a": 3, "b": True, "c": None}) == "3|true||"
    assert r("{{a}}", {"a": {"k": "v"}}) == '{"k": "v"}'


def test_sections():
    t = "{{#context}}{{context}}\n{{/context}}Cmd: {{command}}"
    assert r(t, {"context": "main", "command": "ls"}) == "main\nCmd: ls"
    assert r(t, {"command": "ls"}) == "Cmd: ls"
    assert r(t, {"context": "", "command": "ls"}) == "Cmd: ls"
    assert r("{{#flag}}on{{/flag}}", {"flag": True}) == "on"
    assert r("{{#flag}}on{{/flag}}", {"flag": False}) == ""
    assert r("{{#n}}on{{/n}}", {"n": 0}) == ""


def test_list_sections_and_shadowing():
    t = "{{task}}:{{#items}} [{{id}} {{text}} {{task}}]{{/items}}"
    items = [{"id": "L1", "text": "ok\nL4 disk full"}, {"id": "L2", "text": "b", "task": "own"}]
    out = r(t, {"task": "T", "items": items})
    assert out == "T: [L1 ok\\nL4 disk full T] [L2 b own]"
    assert "\n" not in out
    assert r("{{#items}}{{id}},{{/items}}", {"items": []}) == ""
    assert r("{{#xs}}<{{.}}>{{/xs}}", {"xs": ["a", "b\n"]}) == "<a><b\\n>"
    assert r("{{#m}}{{k}}{{/m}}", {"m": {"k": "v"}}) == "v"


def test_nested_sections():
    t = "{{#a}}{{#b}}{{x}}{{y}}{{/b}}{{/a}}"
    assert r(t, {"y": "Y", "a": [{"b": [{"x": "1"}, {"x": "2"}]}]}) == "1Y2Y"


def test_raw_only_for_allowed():
    inputs = {"diff": "a\n\"b\"", "task": "t"}
    assert r("{{task}}\n{{{diff}}}", inputs, frozenset({"diff"})) == "t\na\n\"b\""
    assert r("{{{diff}}}", {}, frozenset({"diff"})) == ""
    with pytest.raises(TemplateError):
        r("{{{diff}}}", inputs)
    with pytest.raises(TemplateError):
        check_template("{{{task}}}", raw_allowed=frozenset({"diff"}))
    check_template("{{{diff}}}", raw_allowed=frozenset({"diff"}))


@pytest.mark.parametrize("t", [
    "{{#a}}x",
    "x{{/a}}",
    "{{#a}}x{{/b}}",
    "{{#a}}{{#b}}{{/a}}{{/b}}",
    "{{a",
    "{{{a}}",
    "{{}}",
    "{{a b}}",
    "{{^a}}x{{/a}}",
    "{{#}}x{{/}}",
])
def test_check_template_rejects(t):
    with pytest.raises(TemplateError):
        check_template(t, raw_allowed=frozenset({"a"}))


def test_check_template_accepts():
    check_template("Task: {{task}}\n{{#context}}{{context}}\n{{/context}}{{#items}}{{id}} {{text}}{{/items}}", raw_allowed=NONE)
    check_template("plain text with { single braces }", raw_allowed=NONE)
