"""g09 skills via --plugin-dir (T083-T090): claude -p loads the 12 openjev-skills; assertions are on the downstream MCP call each SKILL.md prescribes."""
import json

import pytest
import run_live as rl

import cl_assert as A
from cl_cases import case_from_data, run_case
from cl_env import CASES_DIR, REPO
from run_live import make_csv

DATA = CASES_DIR / "g09_skills.json"
SPEC_CASES = REPO / "docs/mcp-skill-spec/tests/cases"
SKILLS = sorted(p.name for p in (REPO / "plugins/openjev-skills/skills").iterdir() if p.is_dir())
READS = ("ask", "yes_no", "classify", "score", "filter", "batch", "ask_image", "recipe")
C01 = rl.load("01-support-ticket-triage.json")
EX_CAL = [f"ex-cal-{i}" for i in range(1, 8)]


def _blob(c) -> str:
    return json.dumps(c.use.input, ensure_ascii=False).lower()


def _t083(t, ctx):
    names = {s if isinstance(s, str) else s.get("name") for s in t.init.get("skills", [])}
    want = {f"openjev-skills:{s}" for s in SKILLS}
    assert len(SKILLS) == 12, SKILLS
    missing = sorted(want - names)
    assert not missing, f"init.skills lacks {missing}; has {sorted(n for n in names if n)}"
    A.final_text_contains(t, "OK")


def _t084(t, ctx):
    c = A.tool_called(t, "recipe", {"recipe": A.eq("command_gate")})[0]
    A.args_match(c, "inputs.task", A.regex(r"list the files", 2))
    A.args_match(c, "inputs.command", A.contains("terraform destroy"))
    A.result_ok(c)
    A.result_matches(c, "recipe", "command_gate")
    A.result_matches(c, "decision", "deny")
    A.result_matches(c, "degraded", False)
    A.no_tool_called(t, ["Bash"])


def _t085(t, ctx):
    cs = [c for c in t.calls if c.use.tool in ("yes_no", "ask") and "build passed" in _blob(c)]
    assert cs, f"no yes_no/ask call carrying the literal claim 'the build passed'\n{A.summary(t)}"
    c = cs[0]
    r = A.result_ok(c)
    if c.use.tool == "yes_no":
        A.result_matches(c, "p", A.lt(0.2))
        A.result_matches(c, "decision", "no")
    else:   # ask: every noul answer for this claim reads no
        ps = [a["p"] for a in (r.get("answers") or {}).values() if isinstance(a, dict) and "p" in a]
        assert ps and min(ps) < 0.2, f"ask answers {r.get('answers')}"


def _setup_t086(ctx):
    ctx.seed["ticket"] = C01["triage-01"]["request"]["state"]


def _t086(t, ctx):
    ok = lambda cs: [c for c in cs if c.result and not c.result.is_error]   # a first call with a wrong inputs key may be refused, then corrected
    rc = ok(c for c in t.of("recipe") if c.use.input.get("recipe") == "ticket_triage")
    rc = [c for c in rc if (c.result.json or {}).get("decision") != "dry_run"]   # a dry-run preview decides nothing; judge the call that routed
    cl = ok(t.of("classify"))
    assert rc or cl, f"no ticket_triage recipe or classify call\n{A.summary(t)}"
    want = C01["triage-01"]["expect"]["answers"]["dept"]["choice"]
    if rc:
        A.result_ok(rc[0])
        A.result_matches(rc[0], "signals.dept", want)
        A.result_matches(rc[0], "decision", "route")
    else:
        A.result_ok(cl[0])
        A.result_matches(cl[0], "label", want)
    A.final_text_contains(t, want)


def _setup_t087(ctx):
    make_csv(ctx.cwd / "items.csv", 12)


def _t087(t, ctx):
    cs = A.tool_called(t, "batch")
    A.args_match(cs[0], "dry_run", A.eq(True))   # skill step 1: dry run first
    A.result_matches(cs[0], "$.status.stopped_reason", "dry_run")
    runs = [c for c in cs if not c.use.input.get("dry_run")]
    assert runs, f"no real batch run after the dry run\n{A.summary(t)}"
    for c in runs:
        A.result_ok(c)
        A.args_match(c, "output_path", A.eq(str(ctx.cwd / "out.jsonl")))
        assert c.use.input.get("concurrency", 1) <= 2, f"concurrency {c.use.input.get('concurrency')} > 2"
    A.result_matches(runs[-1], "$.status.stopped_reason", "complete")
    A.jsonl_rows(ctx.cwd / "out.jsonl", n=12, unique=True)


def _t088(t, ctx):
    seq = t.tools_called()
    first = next((i for i, n in enumerate(seq) if n in ("compile", "lint")), None)
    assert first is not None, f"neither compile nor lint called\n{A.summary(t)}"
    early = [n for n in seq[:first] if n in READS]
    assert not early, f"read tool(s) {early} before the first compile/lint"
    cs = t.of("compile") or t.of("lint")
    A.result_ok(cs[0])


def _setup_t089(ctx):
    src = {c["id"]: c for c in json.loads((SPEC_CASES / "00-spec-examples.json").read_text(encoding="utf-8"))["cases"]}
    (ctx.cwd / "ex-cal.json").write_text(json.dumps({"cases": [src[i] for i in EX_CAL]}), encoding="utf-8")


def _t089(t, ctx):
    c = A.tool_called(t, "calibrate", {"case_file": A.eq(str(ctx.cwd / "ex-cal.json"))})[0]
    A.result_ok(c)
    A.result_matches(c, "$.n", 7)
    A.result_matches(c, "$.per_question.escalate.accuracy_at_0.5", A.between(0, 1))
    A.result_matches(c, "$.per_question.escalate.separable", A.is_type(bool))


def _t090(t, ctx):
    rc = [c for c in t.of("recipe") if c.use.input.get("recipe") == "command_gate"]
    yn = [c for c in t.calls if c.use.tool in ("yes_no", "ask")]
    hit = rc or yn
    assert hit, f"no command_gate recipe or yes_no/ask call\n{A.summary(t)}"
    c = hit[0]
    A.result_ok(c)
    if rc:
        A.args_match(c, "inputs.command", A.contains("rm -rf build"))
        A.args_match(c, "inputs.task", A.regex(r"README", 2))
        A.result_matches(c, "decision", A.one_of("allow", "ask", "deny"))
    else:
        assert "rm -rf" in _blob(c), f"decision call does not carry the command: {_blob(c)[:200]}"
    assert t.final_text.strip(), "no final answer"
    print("skill_tool_use:", [x.use.input.get("skill") for x in t.of("Skill")])   # logged, not required


EXPECT = {"T083": (_t083, None), "T084": (_t084, None), "T085": (_t085, None), "T086": (_t086, _setup_t086), "T087": (_t087, _setup_t087),
          "T088": (_t088, None), "T089": (_t089, _setup_t089), "T090": (_t090, None)}
CASES = [case_from_data(DATA, tid, expect=(fn,), setup=su) for tid, (fn, su) in EXPECT.items()]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.id}-{c.slug}")
def test_case(case, case_ctx):
    run_case(case, case_ctx)
