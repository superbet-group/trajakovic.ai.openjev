"""g06 recipe families (T049-T061): claude -p calls the `recipe` tool once per listed recipe; decision, signals and the case-file
answers (recipe_harness.check_expect semantics, held against result.answers or the per-item signals) are checked on the tool result.
Inputs and expectations come from cases/g06_recipes.json, generated from docs/mcp-skill-spec/tests/cases via recipe_harness."""
import pytest

import cl_assert as A
from cl_cases import case_from_data, run_case
from cl_env import CASES_DIR

DATA = CASES_DIR / "g06_recipes.json"


def _answers(r: dict, step: dict) -> tuple[list[str], int]:
    """check_expect on the JSON result: noul p / score / choice per question; questions the result lacks are skipped."""
    fails, checked = [], 0
    rename, answers = step.get("rename", {}), r.get("answers") or {}
    sig = ((r.get("items") or [{}])[0].get("signals") or {}) if r.get("items") else {}
    for qid, cond in step["answers"].items():
        key = rename.get(qid, qid)
        a = answers.get(key)
        if a is None and key in sig:   # per-item recipes carry the p / choice in items[0].signals
            a = {"p": sig[key], "choice": sig[key]} if not isinstance(sig[key], str) else {"choice": sig[key]}
        if a is None:
            continue
        for k, want in cond.items():
            if k in ("noul_gte", "noul_lte"):
                got = a.get("p")
                ok = isinstance(got, (int, float)) and (got >= want if k == "noul_gte" else got <= want)
            elif k in ("score_gte", "score_lte"):
                got = a.get("score")
                ok = isinstance(got, (int, float)) and (got >= want if k == "score_gte" else got <= want)
            elif k == "choice":
                got, ok = a.get("choice"), a.get("choice") == want
            elif k == "choice_in":
                got, ok = a.get("choice"), a.get("choice") in want
            else:
                continue
            checked += 1
            if not ok:
                fails.append(f"{qid}.{k}={want!r} got {got!r}")
    return fails, checked


def _plan(t, ctx):
    """Per planned recipe call: it was made once with the planned inputs, and its result matches the case expectation."""
    for step in ctx.case.args["plan"]:
        rid = step["recipe"]
        call = A.tool_called(t, "recipe", where={"recipe": A.eq(rid)}, times=1)[0]
        A.args_match(call, "inputs", A.eq(step_inputs(ctx, step)))
        r = A.result_ok(call)
        A.result_matches(call, "recipe", A.eq(rid))
        A.result_matches(call, "decision", A.eq(step["decision"]))
        A.result_matches(call, "degraded", A.eq(False))
        for k, v in (step.get("sig") or {}).items():
            A.result_matches(call, f"signals.{k}", A.eq(v))
        for k in step.get("sig_has", ()):
            A.result_matches(call, f"signals.{k}", A.exists())
        if step.get("dry"):
            A.result_matches(call, "built_requests", A.is_type(list))
            assert r["built_requests"], f"{rid}: dry_run built_requests empty"
            A.args_match(call, "dry_run", A.eq(True))
            A.args_match(call, "profile", A.eq("lenient"))
            A.result_matches(call, "requests", A.eq(0))
            A.no_read(t, call)
            continue
        fails, checked = _answers(r, step)
        if fails:
            A.fail(t, f"{rid} [{step['case']}]: " + "; ".join(fails))


def step_inputs(ctx, step):
    call = ctx.case.args[f"c{ctx.case.args['plan'].index(step) + 1}"]
    return call["inputs"]


CASES = [case_from_data(DATA, f"T{n:03d}", expect=(_plan,)) for n in range(49, 62)]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.id}-{c.slug}")
def test_case(case, case_ctx):
    run_case(case, case_ctx)
