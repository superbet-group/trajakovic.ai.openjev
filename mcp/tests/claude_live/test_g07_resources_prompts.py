"""g07 resources and prompts (T062-T072): claude -> MCP resources/read, resources/list, prompts/get over the wire."""
import csv
import json

import pytest

import cl_assert as A
from cl_cases import case_from_data, run_case
from cl_env import CASES_DIR, REPO

DATA = CASES_DIR / "g07_resources_prompts.json"
READ = "ReadMcpResourceTool"
BUILTIN = REPO / "mcp/openjev_mcp/recipes/builtin"


def _uris() -> list[str]:
    from openjev_mcp import resources
    return [r["uri"] for r in resources.listing()]


def _doc(t, uri):
    """The ReadMcpResourceTool call for `uri` -> (mimeType, text, parsed JSON or None)."""
    call = A.tool_called(t, READ, where={"uri": uri})[0]
    j = A.result_ok(call)
    c = (j or {}).get("contents") or []
    if not c:   # Claude Code persists results over ~100 KB to a file (openjev://patterns): take the body from the wire
        e = A.wire_called(t, "resources/read", params={"uri": uri})[0]
        c = next((m["result"]["contents"] for m in e["resp"] if isinstance(m.get("result"), dict) and m["result"].get("contents")), [])
    assert c, f"{uri}: no contents in {str(call.result.text)[:200]!r}"
    try:
        body = json.loads(c[0]["text"])
    except ValueError:
        body = None
    return c[0].get("mimeType"), c[0]["text"], body


def _wire_read(t, uri):
    return A.wire_called(t, "resources/read", params={"uri": uri})


def _wire_prompt(t, name, **args):
    A.wire_called(t, "prompts/get", name, {f"arguments.{k}": v for k, v in args.items()})
    res = [r for r in t.wire.response("prompts/get") if r.get("messages")]
    assert res, f"no prompts/get result with messages on the wire: {t.wire.errors()}"
    return res[0]


def _t062(t, ctx):
    want = set(_uris())
    assert len(want) == 6
    wire = [r["uri"] for r in t.wire.response("resources/list")[0]["resources"]]
    assert set(wire) == want and len(wire) == 6, wire
    call = A.tool_called(t, "ListMcpResourcesTool")[0]
    j = A.result_ok(call)
    got = [r.get("uri") for r in (j if isinstance(j, list) else (j or {}).get("resources", []))]
    assert set(got) == want and len(got) == 6, f"ListMcpResourcesTool uris {got}"


def _t063(t, ctx):
    mime, _, schema = _doc(t, "openjev://schema")
    assert mime == "application/schema+json", mime
    defs = schema.get("$defs") or schema.get("definitions") or {}
    assert "ToolError" in defs and {"State", "Question", "Answer"} <= set(defs), sorted(defs)
    mime, _, lim = _doc(t, "openjev://limits")
    assert mime == "application/json"
    A._check("limits", lim, "$.limits.questions", A.eq(256))
    A._check("limits", lim, "$.batch.concurrency_max", A.eq(4))
    assert "read_at" in lim, sorted(lim)   # null while limits are the documented defaults (/v1/limits 404, deviation 17)
    if lim.get("limit_source") != "default":
        A._check("limits", lim, "$.read_at", A.regex(r"^\d{4}-\d\d-\d\dT"))
    _wire_read(t, "openjev://schema")
    _wire_read(t, "openjev://limits")


def _t064(t, ctx):
    ids = {p.stem for p in BUILTIN.glob("*.json")}
    assert len(ids) == 29
    mime, _, idx = _doc(t, "openjev://recipes")
    assert mime == "application/json"
    rows = idx if isinstance(idx, list) else idx.get("recipes", idx)
    A._check("recipes index", [r["id"] for r in rows], "$", A.set_eq(ids))
    _, _, doc = _doc(t, "openjev://recipes/command_gate")
    A._check("recipe", doc, "$.id", A.eq("command_gate"))
    A._check("recipe", doc, "$.steps", A.is_type(list, dict))
    _wire_read(t, "openjev://recipes/command_gate")


def _t065(t, ctx):
    mime, _, idx = _doc(t, "openjev://templates")
    assert mime == "application/json" and isinstance(idx, list) and len(idx) == 10, idx
    _, _, tpl = _doc(t, "openjev://templates/spec_examples_escalate")
    A._check("template", tpl, "$.id", A.eq("spec_examples_escalate"))
    A._check("template", tpl, "$.questions.escalate", A.exists)
    _, _, pat = _doc(t, "openjev://patterns")
    assert isinstance(pat, dict) and pat, "patterns is not a JSON object"
    mime, text, _ = _doc(t, "openjev://guide/authoring")
    assert mime == "text/markdown" and text.strip(), mime
    for u in ("openjev://templates", "openjev://patterns", "openjev://guide/authoring"):
        _wire_read(t, u)


def _t066(t, ctx):
    call = A.tool_called(t, READ, where={"uri": "openjev://nope"})[0]
    # Claude Code renders the JSON-RPC error as plain tool_result text (is_error false); the wire error is the deterministic check
    assert call.result is not None and "resource not found" in call.result.text.lower(), call.result
    err = A.wire_jsonrpc_error(t, -32602, "Resource not found")
    A._check("jsonrpc error", err, "$.data.uri", A.eq("openjev://nope"))


def _seed_batch(ctx):
    r = ctx.call_tool("batch", {"template": "spec_examples_escalate", "max_items": 3, "output_path": str(ctx.data / f"g07-{ctx.case.id}.jsonl")})
    assert not r["is_error"], r["text"][:300]
    ctx.seed["out"] = str(ctx.data / f"g07-{ctx.case.id}.jsonl")


def _t067(t, ctx):
    uri = f"file://{ctx.seed['out']}"
    mime, text, _ = _doc(t, uri)
    lines = [json.loads(x) for x in text.splitlines() if x.strip()]
    assert mime == "application/x-ndjson" and len(lines) >= 2, (mime, len(lines))
    assert lines[0].get("openjev_mcp") == "batch" and all("id" in r for r in lines[1:]), lines[0]
    A.jsonl_rows(ctx.seed["out"], n=len(lines) - 1)
    _wire_read(t, uri)
    bad = A.tool_called(t, READ, where={"uri": "file:///etc/hosts"})[0]
    assert bad.result is not None and "resource not found" in bad.result.text.lower(), bad.result
    A.wire_jsonrpc_error(t, -32602, "Resource not found")


def _t068(t, ctx):
    r = _wire_prompt(t, "start_batch", template="spec_examples_escalate")
    msgs = r["messages"]
    assert msgs[0]["role"] == "user" and "dry_run" in msgs[0]["content"]["text"]
    assert msgs[1]["content"]["resource"]["uri"] == "openjev://templates/spec_examples_escalate"
    first = A.tool_called(t, "batch")[0]
    A.args_match(first, "$.dry_run", A.eq(True))


def _t069(t, ctx):
    r = _wire_prompt(t, "review_batch", output_path=ctx.seed["out"], limit="5")
    c = r["messages"][-1]["content"]
    assert c["type"] == "resource" and c["resource"]["uri"] == "openjev://batch-review", c
    body = json.loads(c["resource"]["text"])
    A._check("review body", body, "$.output_path", A.eq(ctx.seed["out"]))
    A._check("review body", body, "$.n", A.gt(0))
    assert all(m["role"] == "user" for m in r["messages"])


def _t070(t, ctx):
    r = _wire_prompt(t, "author_question", intent="flag_angry_support_emails")
    assert r["messages"] and all(m["role"] == "user" for m in r["messages"])
    assert "flag_angry_support_emails" in r["messages"][0]["content"]["text"]


def _seed_audit(ctx):
    from openjev_mcp import library
    tpl = library.template("spec_examples_escalate")
    schema, labels = ctx.data / "g07-audit-schema.json", ctx.data / "g07-audit-labels.csv"
    schema.write_text(json.dumps(tpl["questions"]), encoding="utf-8")
    with labels.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "escalate"])
        w.writerows([(f"s{i}", i % 2) for i in range(6)])
    ctx.seed.update(schema=str(schema), labels=str(labels))


def _t071(t, ctx):
    r = _wire_prompt(t, "audit_question", schema_path=ctx.seed["schema"], labels_path=ctx.seed["labels"])
    text = r["messages"][0]["content"]["text"]
    assert "calibrate" in text and "lint" in text and ctx.seed["labels"] in text
    # The prompt hands over file paths, so the model needs Read for the question file before lint; judge its first openjev call.
    first = next((c for c in t.calls if c.use.name.startswith("mcp__openjev__")), None)
    if first is not None:
        assert first.use.tool in ("calibrate", "lint", "batch"), f"first openjev call was {first.use.tool}"


ANSWER = '{"decision":"yes","p":0.93}'


def _t072(t, ctx):
    r = _wire_prompt(t, "explain_answer", answer=ANSWER)
    assert "0.93" in r["messages"][0]["content"]["text"]
    A.no_tool_called(t)


@A.weak
def _t072_text(t, ctx):
    A.final_text_contains(t, "0.93")


CASES = [
    case_from_data(DATA, "T062", expect=(_t062,)),
    case_from_data(DATA, "T063", expect=(_t063,)),
    case_from_data(DATA, "T064", expect=(_t064,)),
    case_from_data(DATA, "T065", expect=(_t065,)),
    case_from_data(DATA, "T066", expect=(_t066,)),
    case_from_data(DATA, "T067", expect=(_t067,), setup=_seed_batch),
    case_from_data(DATA, "T068", expect=(_t068,)),
    case_from_data(DATA, "T069", expect=(_t069,), setup=_seed_batch),
    case_from_data(DATA, "T070", expect=(_t070,)),
    case_from_data(DATA, "T071", expect=(_t071,), setup=_seed_audit),
    case_from_data(DATA, "T072", expect=(_t072, _t072_text)),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.id}-{c.slug}")
def test_case(case, case_ctx):
    run_case(case, case_ctx)
