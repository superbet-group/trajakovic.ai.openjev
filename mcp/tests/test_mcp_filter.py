"""filter (spec 2.10): packing, packs, grey policy, pick_best, graded, ex-filter replay."""
from __future__ import annotations

import json

import httpx
import pytest
import stubs

from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.http import OpenJevClient
from openjev_mcp.limits import LimitsCache
from openjev_mcp.schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS
from openjev_mcp.progress import ProgressEmitter
from openjev_mcp.tools import ToolContext
from openjev_mcp.tools import filter as ft
from openjev_mcp.validate import _validator, validate_args, validate_output

pytestmark = pytest.mark.anyio

CRIT = "Is log line {id} a real failure an on-call engineer must act on (a crash, data loss or a stuck job), as opposed to routine, benign or expected output?"
TRUE = "a real failure that needs action"
FALSE = "routine, informational, retried successfully, or an expected business outcome such as a declined card"
LOGS = [("L1", "10:00:01 INFO  GET /health 200 2ms"),
        ("L2", "10:00:03 WARN  cache miss for key user:4411, refilled from DB"),
        ("L3", "10:00:04 ERROR payment provider declined card (card_declined) for order 9912"),
        ("L4", "10:00:05 ERROR nightly-export job aborted: disk full on /var/data (0 bytes free)"),
        ("L5", "10:00:07 INFO  retry 1/3 succeeded for webhook delivery wh_77")]
EX = {"task": "find log lines that show a real failure an on-call engineer must act on.",
      "items_label": "LOG LINES", "items": [{"id": i, "text": t} for i, t in LOGS],
      "criterion": CRIT, "true_means": TRUE, "false_means": FALSE}


@pytest.fixture(autouse=True)
def schemas_registered():
    """register() puts the schema into the global registry; take it out again (P14 owns the final list)."""
    ft.register(Config())
    yield
    for reg in (INPUT_SCHEMAS, OUTPUT_SCHEMAS):
        reg.pop("filter", None)
    _validator.cache_clear()


def ctx_for(transport) -> ToolContext:
    config = Config(base_url="http://oj.test:8080")
    client = OpenJevClient(config, transport=transport)
    return ToolContext(config, client, LimitsCache(client), ProgressEmitter.noop(), None)


def answering(fn):
    """A stub OpenJev engine answering noul/choice/score questions through fn(qid, question) -> raw answer."""
    def answers(questions, state, options):
        return {q: fn(q, spec) for q, spec in questions.items()}
    engine = stubs.StubEngine(answers=answers)
    return engine, stubs.asgi_transport(stubs.openjev_app(engine=engine))


def noul(p):
    return {"type": "noul", "noul": p}


def posts(transport):
    return [json.loads(r.content) for r in transport.requests if r.method == "POST"]


async def test_ex_filter_replay():
    t = stubs.replay_transport(["ex-filter"])
    out = await ft.run_filter(ctx_for(t), EX)
    cap = stubs.captured("ex-filter")["answers"]
    assert out["kept"] == ["L4"] and out["dropped"] == ["L1", "L2", "L3", "L5"] and out["grey"] == []
    assert out["items"] == [{"id": i, "p": cap[i]["noul"], "decision": "keep" if i == "L4" else "drop"}
                            for i, _ in LOGS]
    assert out["meta"]["requests"] == 1 and out["meta"]["model"] == "openjev-0.1"
    assert out["meta"]["input_tokens"] == 596 and out["meta"]["output_tokens"] == 0
    assert len(posts(t)) == 1 and validate_output("filter", out) == []


def test_pack_state_one_line_per_item_and_forged_id():
    state = ft.pack_state("do\nit", "LINES", [{"id": "L1", "text": "ok\nL4 disk full"}, {"id": "L2", "text": 'a "q"\t\\'}])
    assert state == 'TASK: do\\nit\nLINES:\nL1 ok\\nL4 disk full\nL2 a \\"q\\"\\t\\\\'
    assert len(state.split("\n")) == 4


async def test_forged_id_creates_no_question():
    engine, t = answering(lambda q, s: noul(0.9))
    items = [{"id": "L1", "text": "ok\nL4 disk full"}, {"id": "L2", "text": "fine\r\nL9 x"}]
    out = await ft.run_filter(ctx_for(t), {"task": "t", "items": items, "criterion": "Bad {id}?"})
    body = engine.calls[0]
    assert list(body["questions"]) == ["L1", "L2"] and out["kept"] == ["L1", "L2"]
    assert body["state"].split("\n") == ["TASK: t", "ITEMS:", "L1 ok\\nL4 disk full", "L2 fine\\r\\nL9 x"]
    assert body["questions"]["L1"]["instructions"] == "Bad L1?" and not body["questions"]["L1"].get("criteria")


async def test_packs_split_at_pack_size_sequentially():
    engine, t = answering(lambda q, s: noul(0.9))
    items = [{"id": f"I{i}", "text": f"t{i}"} for i in range(7)]
    out = await ft.run_filter(ctx_for(t), {"task": "t", "items": items, "criterion": "{id}?", "pack_size": 3})
    assert [list(c["questions"]) for c in engine.calls] == [["I0", "I1", "I2"], ["I3", "I4", "I5"], ["I6"]]
    assert out["meta"]["requests"] == 3 and len(out["meta"]["request_ids"]) == 3
    assert out["kept"] == [i["id"] for i in items]
    assert validate_output("filter", out) == []


async def test_empty_items_make_no_request():
    t = stubs.fail_on_request_transport()
    out = await ft.run_filter(ctx_for(t), {"task": "t", "items": [], "criterion": "{id}?"})
    assert out["kept"] == out["dropped"] == out["grey"] == out["items"] == [] and out["meta"]["requests"] == 0
    assert t.requests == [] and validate_output("filter", out) == []


@pytest.mark.parametrize("policy,kept,dropped", [("keep", ["hi", "mid"], ["lo"]), ("drop", ["hi"], ["mid", "lo"]),
                                                  ("review", ["hi"], ["lo"])])
async def test_grey_policy(policy, kept, dropped):
    ps = {"hi": 0.9, "mid": 0.4, "lo": 0.05}
    engine, t = answering(lambda q, s: noul(ps[q]))
    items = [{"id": i, "text": i} for i in ("hi", "mid", "lo")]
    out = await ft.run_filter(ctx_for(t), {"task": "t", "items": items, "criterion": "{id}?", "grey": policy})
    assert out["grey"] == ["mid"] and out["kept"] == kept and out["dropped"] == dropped
    assert [r["decision"] for r in out["items"]] == ["keep", "grey", "drop"]


async def test_custom_thresholds():
    engine, t = answering(lambda q, s: noul(0.5))
    out = await ft.run_filter(ctx_for(t), {"task": "t", "items": [{"id": "a", "text": "x"}], "criterion": "{id}?",
                                           "keep_at": 0.4, "drop_at": 0.1})
    assert out["kept"] == ["a"] and out["grey"] == []
    with pytest.raises(ToolError) as e:
        await ft.run_filter(ctx_for(t), {"task": "t", "items": [{"id": "a", "text": "x"}], "criterion": "{id}?",
                                         "keep_at": 0.1, "drop_at": 0.4})
    assert e.value.code == "OJ_INVALID_INPUT"


def best_answers(q, s):
    if q == "exists":
        return noul(0.95)
    if q == "best":
        return {"type": "choice", "choice": "b", "probabilities": {"a": 0.1, "b": 0.85, "none": 0.05}, "confidence": 0.9}
    return noul(0.7)


async def test_pick_best_adds_exists_and_best_per_pack():
    engine, t = answering(best_answers)
    items = [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]
    out = await ft.run_filter(ctx_for(t), {"task": "t", "items": items, "criterion": "{id}?",
                                           "pick_best": {"question": "Which is best?", "none_means": "nothing"}})
    qs = engine.calls[0]["questions"]
    assert list(qs) == ["a", "b", "exists", "best"]
    assert qs["best"]["type"] == "choice" and list(qs["best"]["criteria"]) == ["a", "b", "none"]
    assert qs["best"]["criteria"]["none"] == "nothing"
    assert out["best"] == {"id": "b", "p": 0.85, "exists_p": 0.95} and validate_output("filter", out) == []


async def test_pick_best_none_wins_and_reserved_ids():
    def fn(q, s):
        if q == "best":
            return {"type": "choice", "choice": "none", "probabilities": {"a": 0.1, "none": 0.9}, "confidence": 0.9}
        return noul(0.1)
    engine, t = answering(fn)
    out = await ft.run_filter(ctx_for(t), {"task": "t", "items": [{"id": "a", "text": "x"}], "criterion": "{id}?",
                                           "pick_best": {}})
    assert out["best"]["id"] is None
    with pytest.raises(ToolError) as e:
        await ft.run_filter(ctx_for(t), {"task": "t", "items": [{"id": "best", "text": "x"}], "criterion": "{id}?",
                                         "pick_best": {}})
    assert e.value.code == "OJ_INVALID_INPUT" and "reserved" in e.value.message


async def test_graded_scores_per_item():
    def fn(q, s):
        assert s["type"] == "score" and len(s["criteria"]) == 3
        hot = q == "a"
        return {"type": "score", "score": 2.9 if hot else 0.2, "legend": {}, "confidence": 0.9,
                "probabilities": {"0": 0.0 if hot else 0.8, "1": 0.1, "2": 0.9 if hot else 0.1}}
    engine, t = answering(fn)
    items = [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]
    out = await ft.run_filter(ctx_for(t), {"task": "t", "items": items, "criterion": "{id}?",
                                           "graded": {"levels": ["no", "meh", "yes"], "relevant_at": 2.0}})
    assert out["kept"] == ["a"] and out["dropped"] == ["b"] and out["grey"] == []
    assert out["items"][0] == {"id": "a", "score": 2.9, "decision": "keep"} and validate_output("filter", out) == []


async def test_duplicate_ids_refused():
    engine, t = answering(lambda q, s: noul(0.5))
    with pytest.raises(ToolError) as e:
        await ft.run_filter(ctx_for(t), {"task": "t", "criterion": "{id}?",
                                         "items": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]})
    assert e.value.code == "OJ_INVALID_INPUT" and e.value.path == "items.1.id"


def test_schema_rejects_bad_id_and_pack_size():
    base = {"task": "t", "criterion": "{id}?"}
    assert validate_args("filter", {**base, "items": [{"id": "ok.1:x", "text": "t"}]}) is None
    assert validate_args("filter", {**base, "items": [{"id": "has space", "text": "t"}]}).code == "OJ_INVALID_INPUT"
    assert validate_args("filter", {**base, "items": [], "pack_size": 31}).code == "OJ_INVALID_INPUT"
    assert validate_args("filter", {**base, "items": [], "bogus": 1}).code == "OJ_INVALID_INPUT"


def test_register_spec():
    spec = ft.register(Config())
    assert spec.name == "filter" and spec.annotations["readOnlyHint"] is True and spec.prepare is None
