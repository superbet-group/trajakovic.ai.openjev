"""Assertion library (ARCHITECTURE 1.5). Every failure message ends with summary(t): tools, error codes, final text, run_dir."""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import cl_env

PREFIX = "mcp__openjev__"
MISSING = type("Missing", (), {"__repr__": lambda s: "<missing>", "__bool__": lambda s: False})()


class HarnessError(AssertionError):
    """Precondition / infrastructure failure: not a case failure (error_class harness)."""


# ---- summary ---------------------------------------------------------------------------------------------------------

def summary(t) -> str:
    lines = [f"--- transcript: subtype={t.subtype} turns={t.num_turns} cost=${t.cost_usd:.4f} model={t.model} denials={len(t.permission_denials)}"]
    for c in t.calls:
        r = c.result
        state = "no result" if r is None else (f"ERR {(r.error or {}).get('code', '?')}" if r.is_error else "ok")
        lines.append(f"  {c.use.tool or c.use.name}({json.dumps(c.use.input, ensure_ascii=False)[:200]}) -> {state}")
    lines.append(f"  final: {(t.final_text or '')[:300]!r}")
    lines.append(f"  run_dir: {t.run_dir}")
    return "\n".join(lines)


def fail(t, msg: str, harness: bool = False):
    raise (HarnessError if harness else AssertionError)(msg + ("\n" + summary(t) if t is not None else ""))


def _fail_call(call, msg: str):
    fail(getattr(call, "tr", None), msg)


def weak(fn):
    """D4: marks a prose-only expectation; every case needs at least one non-weak expect."""
    fn.weak = True
    return fn


# ---- jsonpath-ish getter and predicates --------------------------------------------------------------------------------

_TOK = re.compile(r"\[(\*|-?\d+|'[^']*'|\"[^\"]*\")\]|\.([^.\[\]]+)")


def _tokens(path: str) -> list:
    p = path.strip()
    if p.startswith("$"):
        p = p[1:]
    elif p and p[0] not in ".[":
        p = "." + p
    toks, pos = [], 0
    while pos < len(p):
        m = _TOK.match(p, pos)
        if not m:
            raise ValueError(f"bad path {path!r} at {pos}")
        if m.group(1) is not None:
            g = m.group(1)
            toks.append("*" if g == "*" else int(g) if re.fullmatch(r"-?\d+", g) else g[1:-1])
        else:
            toks.append(m.group(2))
        pos = m.end()
    return toks


def _walk(v: Any, toks: list) -> Any:
    if not toks:
        return v
    t, rest = toks[0], toks[1:]
    if t == "*":
        if not isinstance(v, (dict, list, tuple)):
            return MISSING
        out = [_walk(x, rest) for x in (v.values() if isinstance(v, dict) else v)]
        return [x for x in out if x is not MISSING]
    if isinstance(t, int):
        return _walk(v[t], rest) if isinstance(v, (list, tuple)) and -len(v) <= t < len(v) else MISSING
    if isinstance(v, dict):
        key = t
        for i in range(len(rest) + 1):   # keys with dots ("accuracy_at_0.5") are re-joined from consecutive tokens
            if key in v:
                r = _walk(v[key], rest[i:])
                if r is not MISSING:
                    return r
            if i < len(rest) and isinstance(rest[i], str) and rest[i] != "*":
                key = f"{key}.{rest[i]}"
            else:
                break
    return MISSING


def get(obj: Any, path: str) -> Any:
    """`$.answers.L1.p`, `$.results[0].id`, `$.probabilities.*` (list of values), `len($.kept)`; MISSING when absent."""
    path = path.strip()
    m = re.fullmatch(r"len\((.*)\)", path)
    if m:
        v = get(obj, m.group(1))
        return len(v) if v is not MISSING and hasattr(v, "__len__") else MISSING
    return _walk(obj, _tokens(path))


class P:
    """A predicate with a description. `exists` / `absent` are instances: usable bare or called (`exists()` returns itself)."""

    def __init__(self, desc: str, fn):
        self.desc, self.fn = desc, fn

    def __call__(self, *a):
        if not a:
            return self
        try:
            return bool(self.fn(a[0]))
        except (TypeError, ValueError, KeyError, re.error):
            return False

    def __repr__(self):
        return self.desc


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def eq(x): return P(f"== {x!r}", lambda v: v is not MISSING and v == x)
def approx(x, tol=1e-6): return P(f"~= {x!r} +-{tol}", lambda v: _num(v) and abs(v - x) <= tol)
def gt(x): return P(f"> {x!r}", lambda v: _num(v) and v > x)
def ge(x): return P(f">= {x!r}", lambda v: _num(v) and v >= x)
def lt(x): return P(f"< {x!r}", lambda v: _num(v) and v < x)
def le(x): return P(f"<= {x!r}", lambda v: _num(v) and v <= x)
def between(lo, hi): return P(f"in [{lo!r}, {hi!r}]", lambda v: _num(v) and lo <= v <= hi)
def one_of(*xs):
    xs = tuple(xs[0]) if len(xs) == 1 and isinstance(xs[0], (list, tuple, set, frozenset)) else xs
    return P(f"in {list(xs)!r}", lambda v: v is not MISSING and v in xs)
def contains(x): return P(f"contains {x!r}", lambda v: v is not MISSING and (x in v if isinstance(v, (str, list, tuple, dict, set)) else False))
def regex(pat, flags=0): return P(f"matches /{pat}/", lambda v: isinstance(v, str) and re.search(pat, v, flags) is not None)
def is_type(*ts): return P(f"is {'/'.join(t.__name__ for t in ts)}", lambda v: v is not MISSING and isinstance(v, ts))
def set_eq(xs): return P(f"set == {sorted(map(str, xs))!r}", lambda v: isinstance(v, (list, tuple, set)) and set(v) == set(xs))
def sums_to(x=1, tol=1e-2): return P(f"sums to {x!r} +-{tol}", lambda v: isinstance(v, (list, tuple)) and v and all(_num(i) for i in v) and abs(sum(v) - x) <= tol)


exists = P("exists", lambda v: v is not MISSING and v is not None)
absent = P("absent", lambda v: v is MISSING or v is None)


def _pred(p) -> P:
    return p if isinstance(p, P) or callable(p) else eq(p)


def _check(subject: str, obj: Any, path: str, pred) -> Any:
    v = get(obj, path)
    p = _pred(pred)
    if not p(v):
        raise AssertionError(f"{subject} {path}: got {v!r}, expected {getattr(p, 'desc', p)}")
    return v


# ---- transcript checks -----------------------------------------------------------------------------------------------

def _short(name: str) -> str:
    return name[len(PREFIX):] if name.startswith(PREFIX) else name


def precondition(t, tools=()):
    """F2 checks (harness error class): subscription login, openjev connected, expected tools listed."""
    if t.subtype == "timeout":
        return
    if not t.init:
        fail(t, f"harness: no system/init event (rc={t.rc}); stderr: {t.stderr[-400:]!r}", harness=True)
    if t.init.get("apiKeySource") != "none":
        fail(t, f"harness: apiKeySource={t.init.get('apiKeySource')!r}, expected 'none' (subscription login)", harness=True)
    srv = {s.get("name"): s.get("status") for s in t.init.get("mcp_servers") or []}
    if srv.get("openjev") != "connected":
        fail(t, f"harness: mcp server openjev not connected: {srv}", harness=True)
    have = set(t.init.get("tools") or [])
    missing = [x for x in tools if (x if "__" in x else PREFIX + x) not in have and x not in have]
    if missing:
        fail(t, f"harness: tools missing from init.tools: {missing}", harness=True)


def _match_where(use, where) -> bool:
    for path, pred in (where or {}).items():
        if not _pred(pred)(get(use.input, path)):
            return False
    return True


def tool_called(t, name: str, where: dict | None = None, times=None) -> list:
    """Calls of `name` (short openjev name or built-in) whose input satisfies `where`; `times` an int or (lo, hi)."""
    name = _short(name)
    allc = t.of(name)
    hit = [c for c in allc if _match_where(c.use, where)]
    lo, hi = (1, None) if times is None else (times, times) if isinstance(times, int) else times
    if len(hit) < lo or (hi is not None and len(hit) > hi):
        inputs = [json.dumps(c.use.input, ensure_ascii=False)[:160] for c in allc]
        fail(t, f"tool_called({name}, where={where}, times={times}): {len(hit)} matching of {len(allc)} calls; inputs: {inputs}")
    return hit


def no_tool_called(t, names=None):
    called = [c for c in t.calls if names is None or (c.use.tool or c.use.name) in [_short(n) for n in names] or c.use.name in names]
    if called:
        fail(t, f"expected no tool call{'' if names is None else f' of {list(names)}'}, got {[c.use.tool or c.use.name for c in called]}")


def tool_not_called(t, name: str):
    no_tool_called(t, [name])


def result_ok(call):
    if call.result is None:
        _fail_call(call, f"{call.use.name}: no tool_result")
    if call.result.is_error:
        _fail_call(call, f"{call.use.name}: expected ok, got error {call.result.error or call.result.text[:300]!r}")
    return call.result.json


def result_error(call, code: str | None = None, path=None, retryable=None, msg=None, hint=None):
    r = call.result
    if r is None or not r.is_error:
        _fail_call(call, f"{call.use.name}: expected isError, got {'no result' if r is None else 'ok'}")
    e = r.error
    if e is None:
        _fail_call(call, f"{call.use.name}: error result without a ToolError JSON: {r.text[:300]!r}")
    if code is not None and e.get("code") != code:
        _fail_call(call, f"{call.use.name}: error code {e.get('code')!r}, expected {code!r}: {e}")
    if path is not None:
        v = e.get("path")
        ok = _pred(path)(v) if not isinstance(path, str) else (isinstance(v, str) and path in v)
        if not ok:
            _fail_call(call, f"{call.use.name}: error path {v!r} does not match {path!r}")
    if retryable is not None and e.get("retryable") is not retryable:
        _fail_call(call, f"{call.use.name}: retryable={e.get('retryable')!r}, expected {retryable!r}")
    for key, needle in (("message", msg), ("hint", hint)):
        if needle is not None:
            v = e.get(key) or ""
            ok = needle(v) if callable(needle) else needle.lower() in v.lower()
            if not ok:
                _fail_call(call, f"{call.use.name}: error {key} {v!r} does not contain {needle!r}")
    return e


def result_matches(call, path: str, pred):
    if call.result is None or call.result.json is None:
        _fail_call(call, f"{call.use.name}: no JSON result to match {path}")
    try:
        return _check(f"{call.use.name} result", call.result.json, path, pred)
    except AssertionError as ex:
        _fail_call(call, str(ex))


def args_match(call, path: str, pred):
    try:
        return _check(f"{call.use.name} input", call.use.input, path, pred)
    except AssertionError as ex:
        _fail_call(call, str(ex))


def call_order(t, names):
    seq = [c.use.tool or c.use.name for c in t.calls]
    it = iter(seq)
    if not all(_short(n) in it for n in names):
        fail(t, f"call_order: {list(names)} is not a subsequence of {seq}")


def cursor_chain(t, tool: str):
    cs = t.of(_short(tool))
    if not cs:
        fail(t, f"cursor_chain: no {tool} calls")
    for i, c in enumerate(cs):
        j = result_ok(c)
        nxt = (j or {}).get("next_cursor")
        if i + 1 < len(cs):
            got = cs[i + 1].use.input.get("cursor")
            if not nxt or got != nxt:
                fail(t, f"cursor_chain: call {i + 2} cursor {got!r} != call {i + 1} next_cursor {nxt!r}")
        elif nxt is not None:
            fail(t, f"cursor_chain: last {tool} call still has next_cursor {str(nxt)[:60]!r}")
    return cs


def denied(t, tool: str):
    full = tool if "__" in tool else PREFIX + tool
    hit = [d for d in t.permission_denials if d.get("tool_name") in (tool, full)]
    if not hit:
        fail(t, f"denied({tool}): permission_denials={[d.get('tool_name') for d in t.permission_denials]}")
    return hit


def no_read(t, call=None):
    """D5: no read reached OpenJev: no audit entry during the run and, with `call`, meta.requests == 0."""
    if t.audit:
        fail(t, f"no_read: {len(t.audit)} audit entries {[a.get('tool') for a in t.audit]}")
    if call is not None and call.result is not None and isinstance(call.result.json, dict):
        n = (call.result.json.get("meta") or {}).get("requests")
        if n:
            fail(t, f"no_read: {call.use.name} meta.requests={n}")


# ---- wire --------------------------------------------------------------------------------------------------------------

def wire_called(t, method: str, name: str | None = None, params: dict | None = None) -> list:
    hit = [e for e in t.wire.requests(method, name) if all(_pred(p)(get(e["params"], k)) for k, p in (params or {}).items())]
    if not hit:
        fail(t, f"wire_called({method}, name={name}, params={params}): none of {len(t.wire.requests(method, name))} matching requests; "
                f"methods seen: {sorted({e['method'] or '?' for e in t.wire.entries})}")
    return hit


def wire_header(t, header: str, pred, method: str | None = None) -> list:
    """Every JSON-RPC request (of `method` when given) carries `header` satisfying pred."""
    h, p = header.lower(), _pred(pred)
    es = [e for e in t.wire.entries if e["req"] is not None and (method is None or e["method"] == method)]
    if not es:
        fail(t, f"wire_header({header}): no requests{f' of {method}' if method else ''} recorded")
    bad = [(e["method"], e["headers"].get(h)) for e in es if not p(e["headers"].get(h, MISSING))]
    if bad:
        fail(t, f"wire_header({header}): {len(bad)} requests violate {p!r}: {bad[:5]}")
    return [e["headers"].get(h) for e in es]


def wire_jsonrpc_error(t, code: int, msg: str | None = None) -> dict:
    errs = t.wire.errors()
    hit = [e for e in errs if e.get("code") == code and (msg is None or msg.lower() in (e.get("message") or "").lower())]
    if not hit:
        fail(t, f"wire_jsonrpc_error({code}, {msg!r}): errors seen {[(e.get('code'), e.get('message')) for e in errs]}")
    return hit[0]


# ---- files -------------------------------------------------------------------------------------------------------------

def _local(path) -> Path:
    s = str(path)
    return Path(s[7:] if s.startswith("file://") else s)


def jsonl_rows(path, n: int | None = None, ids=None, unique: bool = True):
    """Batch output file: (header, rows). n = row count, ids = expected id set, unique = no id twice."""
    sys.path.insert(0, str(cl_env.REPO / "mcp/tests/live"))
    import run_live as rl
    p = _local(path)
    if not p.exists():
        raise AssertionError(f"jsonl_rows: {p} does not exist")
    header, rows = rl.read_rows(p)
    got = [r.get("id") for r in rows]
    if n is not None and len(rows) != n:
        raise AssertionError(f"jsonl_rows: {len(rows)} rows, expected {n} ({p})")
    if unique and len(set(got)) != len(got):
        raise AssertionError(f"jsonl_rows: duplicate ids {sorted({i for i in got if got.count(i) > 1})[:10]} ({p})")
    if ids is not None and set(got) != set(ids):
        raise AssertionError(f"jsonl_rows: ids differ; missing {sorted(set(ids) - set(got))[:10]}, extra {sorted(set(got) - set(ids))[:10]}")
    return header, rows


@weak
def final_text_contains(t, *needles, any_of: bool = False):
    txt = (t.final_text or "").lower()
    hits = [n for n in needles if str(n).lower() in txt]
    if (not hits) if any_of else (len(hits) != len(needles)):
        fail(t, f"final_text_contains({needles}, any_of={any_of}): found {hits}")
