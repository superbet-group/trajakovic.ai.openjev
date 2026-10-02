"""g04 batch, batch_results, cursor and resume (T029-T040): claude -p -> own openjev-mcp -> real OpenJev."""
from __future__ import annotations

import json

import pytest

import cl_assert as A
import cl_cases
from cl_cases import case_from_data, run_case
from cl_env import CASES_DIR
from openjev_mcp import library
from run_live import QS, make_csv, read_rows

DATA = CASES_DIR / "g04_batch.json"
Q1 = {"urgent": QS["urgent"]}
QCHANGED = {"urgent": {**QS["urgent"], "instructions": "Is this ticket about money, payments or billing?"}}
TXT = ["Checkout returns HTTP 500 for every customer since the 14:05 deploy; payments are failing.", "Could you change the colour of the footer links to dark blue? Purely cosmetic.",
       "Customer data export is leaking other tenants' rows; security exposure confirmed.", "How do I rename a project in the settings page? Just curious.",
       "All users are blocked: login loops forever after the SSO change.", "Feature request: add a dark mode toggle to the dashboard."]


def _items(n):
    return [{"id": f"a{i + 1}", "state": TXT[i % len(TXT)]} for i in range(n)]


def _call(ctx, args, tool="batch"):
    r = ctx.call_tool(tool, args)
    if r["is_error"] or not r["structured"]:
        raise RuntimeError(f"seed {tool} failed: {r['text'][:300]}")   # setup errors are reported as harness errors
    return r["structured"]


def _seed_q(ctx):
    ctx.seed.update(q1=Q1, qs=QS, qchanged=QCHANGED)


def _out(ctx, name):
    return str(ctx.cwd / name)


def _only(t, tool="batch"):
    cs = t.of(tool)
    assert cs, f"no {tool} call\n{A.summary(t)}"
    return cs[0]


def _first_result(t, tool="batch"):
    return A.result_ok(_only(t, tool))


def _resource_link(t, call, name):
    """F17: the wire result carries a resource_link block; the model-facing text carries '[Resource link: ...] file://...'."""
    msg = t.wire.result(call.use.id)
    blocks = (msg or {}).get("content") or []
    uris = [b.get("uri", "") for b in blocks if b.get("type") == "resource_link"]
    assert any(u.startswith("file://") and u.endswith(name) for u in uris), f"no resource_link to {name}: wire {uris}, text links {call.result.links}\n{A.summary(t)}"


# ---- setups ----------------------------------------------------------------------------------------------------------

def setup_q(ctx):
    _seed_q(ctx)


def setup_t031(ctx):
    _seed_q(ctx)
    make_csv(ctx.cwd / "items.csv", 12)


def setup_t033(ctx):
    _seed_q(ctx)
    make_csv(ctx.cwd / "items.csv", 8)
    out = _out(ctx, "t033.jsonl")
    r = _call(ctx, {"items_file": {"path": str(ctx.cwd / "items.csv"), "state_field": "state", "id_field": "id"}, "questions": Q1, "concurrency": 2,
                    "output_path": out, "max_items_per_call": 3})
    assert r["status"]["done"] == 3 and r["next_cursor"], r["status"]   # partial output: 3 of 8, cursor left unused
    ctx.seed["done_before"] = 3


def setup_t034(ctx):
    _seed_q(ctx)
    r = _call(ctx, {"items": _items(5), "questions": Q1, "concurrency": 2, "output_path": _out(ctx, "t034.jsonl"), "max_items_per_call": 2})
    assert r["next_cursor"], r["status"]
    ctx.seed["cursor"] = r["next_cursor"]


def _seed_finished(ctx, name, n=6):
    path = _out(ctx, name)
    r = _call(ctx, {"items": _items(n), "questions": Q1, "concurrency": 2, "output_path": path, "max_items_per_call": 25})
    assert r["next_cursor"] is None and r["status"]["done"] == n, r["status"]
    return path


def setup_t038(ctx):
    _seed_q(ctx)
    ctx.seed["out"] = _seed_finished(ctx, "seed38.jsonl")


def setup_t040(ctx):
    _seed_q(ctx)
    ctx.seed["out"] = _seed_finished(ctx, "seed40a.jsonl")
    ctx.seed["out2"] = _seed_finished(ctx, "seed40b.jsonl")


# ---- expectations ----------------------------------------------------------------------------------------------------

def _t029(t, ctx):
    c = _only(t)
    A.tool_called(t, "batch", {"dry_run": True}, times=1)
    r = A.result_ok(c)
    A.result_matches(c, "$.status.stopped_reason", "dry_run")
    A.result_matches(c, "len($.preview)", A.eq(3))
    A.result_matches(c, "$.estimate", A.is_type(dict))
    A.result_matches(c, "$.first_body", A.is_type(dict))
    A.result_matches(c, "$.next_cursor", A.absent)
    assert (r.get("meta") or {}).get("requests", 0) == 0, r.get("meta")
    A.no_read(t, c)
    assert not (ctx.cwd / "t029.jsonl").exists(), "dry_run created the output file"


def _t030(t, ctx):
    c = A.tool_called(t, "batch")[-1]
    A.result_ok(c)
    A.result_matches(c, "$.status.ok", A.eq(4))
    A.result_matches(c, "$.status.errors", A.eq(0))
    A.result_matches(c, "$.results[*].id", A.set_eq(["a1", "a2", "a3", "a4"]))
    A.result_matches(c, "$.summary.per_question", lambda v: isinstance(v, dict) and set(v) == {"urgent", "kind"})
    A.result_matches(c, "$.next_cursor", A.absent)


def _t031(t, ctx):
    c = _only(t)
    A.result_ok(c)
    out = ctx.cwd / "t031.jsonl"
    header, rows = A.jsonl_rows(out, n=12, unique=True)
    assert header, "empty header line"
    assert {r["id"] for r in rows} == {f"r{i + 1:03d}" for i in range(12)}
    A.result_matches(c, "$.next_cursor", A.absent)
    A.result_matches(c, "$.output_path", A.contains("t031.jsonl"))
    assert any(str(out) in x or "t031.jsonl" in x for x in c.result.links), c.result.links
    _resource_link(t, c, "t031.jsonl")


def _t032(t, ctx):
    cs = A.cursor_chain(t, "batch")
    assert len(cs) == 3, f"expected 3 calls (5 items, 2 per call), got {len(cs)}"
    assert sum(c.result.json["status"]["done"] for c in cs) == 5
    A.jsonl_rows(ctx.cwd / "t032.jsonl", n=5, ids=[f"a{i}" for i in range(1, 6)], unique=True)


def _t033(t, ctx):
    c = A.tool_called(t, "batch", times=1)[0]
    A.result_ok(c)
    assert "cursor" not in c.use.input, "resume case must not pass a cursor"
    A.result_matches(c, "$.status.skipped", A.eq(ctx.seed["done_before"]))
    A.result_matches(c, "$.status.ok", A.eq(5))
    A.result_matches(c, "$.status.done", A.eq(8))
    A.result_matches(c, "$.next_cursor", A.absent)
    import run_live as rl
    dg = rl.dup_gap(ctx.cwd / "t033.jsonl", [f"r{i + 1:03d}" for i in range(8)])
    assert dg["n_gaps"] == 0 and dg["n_duplicate_ok"] == 0, dg


def _t034(t, ctx):
    c = _only(t)
    A.args_match(c, "cursor", A.eq(ctx.seed["cursor"]))
    A.result_error(c, "OJ_INVALID_INPUT", msg="arguments changed since this cursor", hint="resume")


def _t035(t, ctx):
    c = _only(t)
    A.args_match(c, "cursor", A.eq("not-a-cursor"))
    A.result_error(c, "OJ_INVALID_INPUT", msg="invalid cursor")


def _t036(t, ctx):
    tpl = library.template("spec_examples_escalate")
    c = A.tool_called(t, "batch", {"template": "spec_examples_escalate"}, times=1)[0]
    A.result_ok(c)
    A.result_matches(c, "$.status.ok", A.eq(len(tpl["states"])))
    A.result_matches(c, "$.status.errors", A.eq(0))
    A.result_matches(c, "$.summary.per_question", lambda v: isinstance(v, dict) and set(v) == set(tpl["questions"]) == {"escalate"})
    A.result_matches(c, "$.results[*].id", A.set_eq([s["id"] for s in tpl["states"]]))


def _t037(t, ctx):
    c = _only(t)
    A.result_ok(c)
    A.result_matches(c, "$.exports[*].format", A.set_eq(["csv", "ojui-batch"]))
    csv_p, js_p = ctx.cwd / "t037.csv", ctx.cwd / "t037.json"
    assert csv_p.exists() and js_p.exists(), "export files missing"
    raw = csv_p.read_bytes()
    assert raw.endswith(b"\n") and b"\r\n" not in raw, "CSV rows must end with LF, not CRLF (deviation 34)"
    assert len(raw.splitlines()) >= 4, raw[:200]   # header + 3 rows
    doc = json.loads(js_p.read_text(encoding="utf-8"))
    assert doc.get("format") == "ojui-batch", list(doc)
    for e in c.result.json["exports"]:
        assert e["bytes"] > 0 and (ctx.cwd / e["path"].rsplit("/", 1)[-1]).exists(), e


def _t038(t, ctx):
    c = A.tool_called(t, "batch_results", {"view": "review"}, times=1)[0]
    r = A.result_ok(c)
    A.result_matches(c, "$.view", "review")
    A.result_matches(c, "$.review_queue", A.is_type(list))
    A.result_matches(c, "$.matched", A.eq(6))
    A.result_matches(c, "$.meta.requests", A.eq(0))
    A.no_read(t, c)
    conf = [e["confidence"] for e in r["review_queue"]]
    assert conf == sorted(conf), f"review_queue not ascending: {conf}"


def _t039(t, ctx):
    cs = A.cursor_chain(t, "batch_results")
    assert len(cs) == 3, f"expected 3 pages of 2 rows, got {len(cs)}"
    rows = [r for c in cs for r in c.result.json["rows"]]
    assert len(rows) == 6 and len({r["id"] for r in rows}) == 6, [r["id"] for r in rows]
    conf = [r["min_confidence"] for r in rows if r.get("min_confidence") is not None]
    assert conf == sorted(conf), f"min_confidence not ascending across pages: {conf}"
    for c in cs:
        A.args_match(c, "sort_by", "confidence")


def _t040(t, ctx):
    c = A.tool_called(t, "batch_results", times=1)[0]
    r = A.result_ok(c)
    A.args_match(c, "compare_to.path", A.contains("seed40b.jsonl"))
    A.result_matches(c, "$.compare.matched", A.eq(6))
    A.result_matches(c, "$.compare.per_question.urgent.agreement", A.between(0, 1))
    A.result_matches(c, "$.compare.per_question", A.is_type(dict))
    A.result_matches(c, "$.meta.requests", A.eq(0))
    A.no_read(t, c)


EXPECT = {"T029": (_t029, setup_q), "T030": (_t030, setup_q), "T031": (_t031, setup_t031), "T032": (_t032, setup_q), "T033": (_t033, setup_t033),
          "T034": (_t034, setup_t034), "T035": (_t035, setup_q), "T036": (_t036, None), "T037": (_t037, setup_q), "T038": (_t038, setup_t038),
          "T039": (_t039, setup_t038), "T040": (_t040, setup_t040)}
CASES = [case_from_data(DATA, tid, expect=(fn,), setup=su) for tid, (fn, su) in EXPECT.items()]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.id}-{c.slug}")
def test_case(case, case_ctx):
    run_case(case, case_ctx)
