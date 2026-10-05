"""Live scenario model for the skills evaluation: loader, setup ops, predicate builder and the check vocabulary.

Contract with data/scenarios.json (schema_version 1):
  setup ops : copy_fixtures{files}, write_text{path,text}, seed_batch_output{items_fixture,questions,output,n_done}, none
  checks    : tool_called, no_tool_called, first_call, args_match, result_matches, result_ok, call_order, before_any_read,
              cursor_chain, jsonl_rows, import_no_warnings, final_text_contains, no_permission_denials, ids_subset, max_errors
  pred      : {"op": eq|ne|lt|le|gt|ge|between|contains|regex|one_of|exists|absent|abs_path, "value": ...}
Only the literal token ``{cwd}`` is rendered (other braces, e.g. the ``{id}`` in a filter criterion, are data).

Every check is a pure function of the Transcript and the Ctx and returns (passed, detail); none raises. A scenario score
is passed / len(checks) (partial credit). Checks are wired onto cl_assert (get/predicates/jsonl_rows) so path syntax and
semantics match the claude_live suite.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DATA_DIR = HERE / "data"
FIXTURES_DIR = DATA_DIR / "fixtures"
SCENARIOS_PATH = DATA_DIR / "scenarios.json"

for _p in (REPO / "mcp/tests/claude_live", REPO / "mcp/tests/live"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cl_assert as A  # noqa: E402

READ_TOOLS = ("ask", "yes_no", "classify", "score", "filter", "batch", "ask_image", "recipe")
PROCEDURAL_CHECKS = frozenset({"first_call", "before_any_read", "call_order", "cursor_chain", "ids_subset", "max_errors"})
SETUP_OPS = frozenset({"copy_fixtures", "write_text", "seed_batch_output", "none"})
CHECK_NAMES: set[str] = set()   # filled by @check below


# ---- loading -----------------------------------------------------------------------------------------------------------

def load_scenarios(path: Path | str = SCENARIOS_PATH) -> list[dict]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = d["scenarios"] if isinstance(d, dict) else d
    for s in out:
        s.setdefault("mcp", True)
        s.setdefault("tools", "Read,Glob,Write")
        s.setdefault("allow", ["mcp__openjev", "Read", "Glob", "Write"])
        s.setdefault("max_turns", 8)
        s.setdefault("budget_usd", 0.25)
        s["procedural"] = any(c.get("check") in PROCEDURAL_CHECKS for c in s["checks"])
    return out


def validate_scenarios(scs: list[dict]) -> list[str]:
    """Static problems (unknown setup op / check id / pred op); empty list when the file is within the vocabulary."""
    bad = []
    for s in scs:
        for op in s["setup"]:
            if op.get("op") not in SETUP_OPS:
                bad.append(f"{s['id']}: unknown setup op {op.get('op')!r}")
        for c in s["checks"]:
            if c.get("check") not in CHECK_NAMES:
                bad.append(f"{s['id']}: unknown check {c.get('check')!r}")
            for key in ("pred",):
                p = c.get(key)
                if p is not None and p.get("op") not in _OPS:
                    bad.append(f"{s['id']}: unknown pred op {p.get('op')!r}")
            for k, p in (c.get("args") or {}).items():
                if p.get("op") not in _OPS:
                    bad.append(f"{s['id']}: unknown pred op {p.get('op')!r} in first_call.args.{k}")
    return bad


# ---- rendering and predicates ------------------------------------------------------------------------------------------

def render(v: Any, cwd: Path | str) -> Any:
    if isinstance(v, str):
        return v.replace("{cwd}", str(cwd))
    if isinstance(v, list):
        return [render(x, cwd) for x in v]
    if isinstance(v, dict):
        return {k: render(x, cwd) for k, x in v.items()}
    return v


def _same_path(a: str, b: str) -> bool:
    if os.path.normpath(a) == os.path.normpath(b):
        return True
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False


def _abs_path(x: str):
    return A.P(f"abs path == {x!r}", lambda v: isinstance(v, str) and os.path.isabs(v) and _same_path(v, x))


def _ne(x):
    return A.P(f"!= {x!r}", lambda v: v is A.MISSING or v != x)


_OPS: dict[str, Callable] = {
    "eq": A.eq, "ne": _ne, "lt": A.lt, "le": A.le, "gt": A.gt, "ge": A.ge,
    "between": lambda v: A.between(*v), "contains": A.contains, "regex": A.regex,
    "one_of": lambda v: A.one_of(list(v)), "exists": lambda v=None: A.exists, "absent": lambda v=None: A.absent,
    "abs_path": _abs_path,
}


def make_pred(spec: dict, cwd: Path | str):
    op = spec["op"]
    if op not in _OPS:
        raise ValueError(f"unknown pred op {op!r}")
    return _OPS[op](render(spec.get("value"), cwd))


# ---- setup -------------------------------------------------------------------------------------------------------------

def _rows_of_fixture(name: str) -> list[dict]:
    p = FIXTURES_DIR / name
    if p.suffix == ".csv":
        with open(p, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        first = list(rows[0])[0]
        return [{"id": r[first], "state": r.get("state") or r.get("text") or ""} for r in rows]
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [{"id": r.get("id") or r.get("ticket_id"), "state": r.get("state") or r.get("text") or r.get("body")} for r in rows]


def _op_copy_fixtures(ctx, op: dict) -> None:
    for dest, src in op["files"].items():
        d = Path(dest) if os.path.isabs(dest) else ctx.cwd / dest
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FIXTURES_DIR / src, d)


def _op_write_text(ctx, op: dict) -> None:
    p = Path(render(op["path"], ctx.cwd))
    p = p if p.is_absolute() else ctx.cwd / p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render(op["text"], ctx.cwd), encoding="utf-8")


def _op_seed_batch_output(ctx, op: dict) -> None:
    """Run a real (local) batch over the fixture's rows so `output` holds n_done rows; leaves the rest undone (resume case)."""
    items = _rows_of_fixture(op["items_fixture"])
    n_done = int(op["n_done"])
    args = {"items": items, "questions": op["questions"], "concurrency": 2, "output_path": str(ctx.cwd / op["output"]),
            "max_items_per_call": min(max(n_done, 1), 100)}
    r = ctx.call_tool("batch", args)
    s = r["structured"] or {}
    if r["is_error"] or (s.get("status") or {}).get("done") != n_done:
        raise RuntimeError(f"seed_batch_output {op['output']}: {r['text'][:300]}")


_SETUP: dict[str, Callable] = {"copy_fixtures": _op_copy_fixtures, "write_text": _op_write_text,
                               "seed_batch_output": _op_seed_batch_output, "none": lambda ctx, op: None}


def run_setup(ctx, scenario: dict) -> None:
    for op in scenario["setup"]:
        _SETUP[op["op"]](ctx, op)


# ---- checks ------------------------------------------------------------------------------------------------------------

_CHECKS: dict[str, Callable] = {}


def check(name: str):
    def deco(fn):
        _CHECKS[name] = fn
        CHECK_NAMES.add(name)
        return fn
    return deco


def _short(n: str) -> str:
    return n[len("mcp__openjev__"):] if n.startswith("mcp__openjev__") else n


def _pick(calls: list, which: str) -> list:
    return calls[:1] if which == "first" else calls[-1:] if which == "last" else list(calls)


def _ok_json(c) -> bool:
    return c.result is not None and not c.result.is_error and c.result.json is not None


def _preds(d: dict | None, cwd) -> list[tuple[str, Any]]:
    return [(path, make_pred(p, cwd)) for path, p in (d or {}).items()]


@check("tool_called")
def _tool_called(t, ctx, c):
    where = {k: make_pred(v, ctx.cwd) for k, v in (c.get("where") or {}).items()}
    try:
        A.tool_called(t, c["name"], where or None, times=c.get("times"))
        return True, ""
    except AssertionError as e:
        return False, str(e).splitlines()[0][:240]


@check("no_tool_called")
def _no_tool_called(t, ctx, c):
    names = [_short(n) for n in c["names"]]
    hit = [x for x in t.tools_called() if _short(x) in names]
    return (not hit), (f"called {hit}" if hit else "")


@check("first_call")
def _first_call(t, ctx, c):
    calls = t.of(_short(c["tool"]))
    if not calls:
        return False, f"no {c['tool']} call"
    bad = [f"{p}: got {A.get(calls[0].use.input, p)!r}, want {pr!r}" for p, pr in _preds(c.get("args"), ctx.cwd) if not pr(A.get(calls[0].use.input, p))]
    return (not bad), "; ".join(bad)[:240]


@check("args_match")
def _args_match(t, ctx, c):
    calls = t.of(_short(c["tool"]))
    if not calls:
        return False, f"no {c['tool']} call"
    pred = make_pred(c["pred"], ctx.cwd)
    sel = _pick(calls, c.get("call", "any"))
    vals = [A.get(x.use.input, c["path"]) for x in sel]
    return any(pred(v) for v in vals), f"{c['path']}: got {vals[:3]!r}, want {pred!r}"


@check("result_matches")
def _result_matches(t, ctx, c):
    calls = [x for x in t.of(_short(c["tool"])) if _ok_json(x)]
    if not calls:
        return False, f"no successful {c['tool']} result"
    pred = make_pred(c["pred"], ctx.cwd)
    sel = _pick(calls, c.get("call", "any"))
    vals = [A.get(x.result.json, c["path"]) for x in sel]
    return any(pred(v) for v in vals), f"{c['path']}: got {str(vals[:3])[:160]}, want {pred!r}"


@check("result_ok")
def _result_ok(t, ctx, c):
    calls = t.of(_short(c["tool"]))
    if not calls:
        return False, f"no {c['tool']} call"
    sel = _pick(calls, c.get("call", "any"))
    ok = [x.result is not None and not x.result.is_error for x in sel]
    err = next((str((x.result.error or x.result.text)[:160]) for x in sel if x.result is not None and x.result.is_error), "")
    return any(ok), err


@check("call_order")
def _call_order(t, ctx, c):
    seq = [_short(x) for x in t.tools_called()]
    it = iter(seq)
    return all(_short(n) in it for n in c["tools"]), f"calls: {seq}"


@check("before_any_read")
def _before_any_read(t, ctx, c):
    seq = [_short(x) for x in t.tools_called()]
    want = {_short(n) for n in c["tools"]}
    first = next((i for i, n in enumerate(seq) if n in want), None)
    if first is None:
        return False, f"none of {sorted(want)} called; calls {seq}"
    early = [n for n in seq[:first] if n in READ_TOOLS and n not in want]
    return (not early), (f"read(s) {early} before first {sorted(want)}" if early else "")


@check("cursor_chain")
def _cursor_chain(t, ctx, c):
    calls = [x for x in t.of(_short(c["tool"])) if not x.use.input.get("dry_run")]
    if not calls:
        return False, f"no non-dry-run {c['tool']} call"
    for i, x in enumerate(calls):
        if x.result is None or x.result.is_error or not isinstance(x.result.json, dict):
            return False, f"call {i + 1} did not return a result"
        nxt = x.result.json.get("next_cursor")
        if i + 1 < len(calls):
            got = calls[i + 1].use.input.get("cursor")
            if not nxt or got != nxt:
                return False, f"call {i + 2} cursor {got!r} != next_cursor {nxt!r}"
        elif nxt:
            return False, "last call still has next_cursor"
    return True, ""


@check("jsonl_rows")
def _jsonl_rows(t, ctx, c):
    try:
        A.jsonl_rows(render(c["path"], ctx.cwd), n=c.get("n"), unique=c.get("unique", True))
        return True, ""
    except (AssertionError, OSError, ValueError, KeyError) as e:
        return False, str(e)[:240]


@check("import_no_warnings")
def _import_no_warnings(t, ctx, c):
    """The last batch result that carries an `import` block has none of the listed warning codes."""
    imps = [x.result.json["import"] for x in t.of("batch") if _ok_json(x) and isinstance(x.result.json, dict) and isinstance(x.result.json.get("import"), dict)]
    if not imps:
        return False, "no batch result with an import block"
    codes = {w.get("code") for w in imps[-1].get("warnings") or []}
    hit = sorted(codes & set(c["codes"]))
    return (not hit), (f"import warnings {hit}" if hit else "")


@check("final_text_contains")
def _final_text_contains(t, ctx, c):
    return str(c["text"]).lower() in (t.final_text or "").lower(), f"{c['text']!r} not in final text"


@check("no_permission_denials")
def _no_denials(t, ctx, c):
    names = [d.get("tool_name") for d in t.permission_denials]
    return (not names), (f"denied {names}" if names else "")


@check("ids_subset")
def _ids_subset(t, ctx, c):
    """Every id the model sent to batch (items[].id, only_ids, or rows of its output file) was kept by filter."""
    kept: set[str] = set()
    for x in t.of("filter"):
        if _ok_json(x) and isinstance(x.result.json.get("kept"), list):
            kept |= {str(i) for i in x.result.json["kept"]}
    if not kept:
        return False, "no filter.kept ids"
    sent: set[str] = set()
    for x in t.of("batch"):
        if x.use.input.get("dry_run"):
            continue
        sent |= {str(i["id"]) for i in x.use.input.get("items") or [] if isinstance(i, dict) and "id" in i}
        sent |= {str(i) for i in x.use.input.get("only_ids") or []}
        op = x.use.input.get("output_path")
        if op and Path(op).exists():
            try:
                _, rows = A.jsonl_rows(op, unique=False)
                sent |= {str(r.get("id")) for r in rows}
            except (AssertionError, OSError, ValueError):
                pass
    if not sent:
        return False, "no batch ids found"
    extra = sorted(sent - kept)
    return (not extra), (f"batch ids not kept by filter: {extra[:8]}" if extra else "")


@check("max_errors")
def _max_errors(t, ctx, c):
    n = sum(1 for x in t.of(_short(c["tool"])) if x.result is not None and x.result.is_error)
    return n <= int(c["n"]), f"{n} {c['tool']} errors, max {c['n']}"


def check_label(c: dict) -> str:
    bits = [c["check"], c.get("tool") or c.get("name"), c.get("path"), c.get("call")]
    return ":".join(str(b) for b in bits if b not in (None, ""))


def evaluate(scenario: dict, t, ctx) -> list[dict]:
    """[{name, pass, detail}] for every check; an unexpected exception in a check counts as a failed check."""
    out = []
    for c in scenario["checks"]:
        try:
            ok, detail = _CHECKS[c["check"]](t, ctx, c)
        except Exception as e:   # a malformed check must not abort the run
            ok, detail = False, f"check error: {type(e).__name__}: {e}"
        out.append({"name": check_label(c), "pass": bool(ok), "detail": "" if ok else detail})
    return out


def score_of(results: list[dict]) -> float:
    return round(sum(1 for r in results if r["pass"]) / len(results), 4) if results else 0.0
