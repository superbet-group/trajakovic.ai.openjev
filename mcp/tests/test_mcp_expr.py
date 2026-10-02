from __future__ import annotations

import pytest

from openjev_mcp.recipes.expr import (
    And, Combine, Compare, ExprError, Not, Or, PolRef, Pred, Quant, Signal, SigRef,
    decide, evaluate, explain, parse_combine, parse_expr,
)

SIGNALS = frozenset({"destructive", "exfiltrates", "remote_code", "weakens_security", "out_of_scope", "risk",
                     "verdict", "block"})
POLICY = frozenset({"deny_hazard", "deny_verdict_p", "deny_risk", "allow_hazard_max", "allow_scope_max",
                    "allow_risk_max"})
DECISIONS = frozenset({"allow", "ask", "deny"})
KW = dict(signals=SIGNALS, policy=POLICY, decisions=DECISIONS)

COMBINE = ("deny if rule('deny'); allow if rule('allow'); deny if any(destructive, exfiltrates, remote_code) >= deny_hazard_x; "
           "deny if verdict == 'deny' and verdict.p >= deny_verdict_p; deny if risk >= deny_risk; "
           "allow if all(destructive, exfiltrates, remote_code, weakens_security) <= allow_hazard_max "
           "and out_of_scope <= allow_scope_max and risk <= allow_risk_max and verdict == 'allow'; ask otherwise")
POL = {"deny_hazard": 0.85, "deny_verdict_p": 0.6, "deny_risk": 2.3, "allow_hazard_max": 0.2,
       "allow_scope_max": 0.3, "allow_risk_max": 1.0}


def pe(text):
    return parse_expr(text, **KW)


def ev(text, signals=None, rule=None):
    return evaluate(pe(text), signals=signals or {}, policy=POL, rule_decision=rule)


def sig(**kw):
    return {k: v if isinstance(v, Signal) else Signal(v) for k, v in kw.items()}


def spec_combine():
    return parse_combine(COMBINE.replace("deny_hazard_x", "deny_hazard"), **KW)


def test_spec_strings_parse():
    c = spec_combine()
    assert isinstance(c, Combine) and len(c.clauses) == 7
    assert c.clauses[-1].cond is None and c.clauses[-1].decision == "ask"
    pe("not rule('allow') and not rule('deny')")
    pe("grey(block)")


def test_precedence():
    assert isinstance(pe("risk > 1 or risk < 0 and risk == 0.5"), Or)
    n = pe("risk > 1 or risk < 0 and risk == 0.5")
    assert isinstance(n.items[1], And)
    assert isinstance(pe("not risk > 1 and risk < 3").items[0], Not)
    assert ev("not (risk > 1 or risk < 0)", sig(risk=0.5))
    assert not ev("not risk > 1 or risk < 0", sig(risk=2.0))
    assert ev("risk > 1 or risk < 2 and risk > 5", sig(risk=1.5)) is True
    assert ev("(risk > 1 or risk < 2) and risk > 5", sig(risk=1.5)) is False


def test_ast_shapes():
    n = pe("any(destructive, risk) >= deny_hazard")
    assert n == Quant("any", ("destructive", "risk"), ">=", PolRef("deny_hazard"))
    assert pe("verdict.p >= 0.5") == Compare(SigRef("verdict", "p"), ">=", pe("verdict.p >= 0.5").right)
    assert pe("rule('deny')") == Pred("rule", "deny")


def test_quantifiers():
    s = sig(destructive=0.1, exfiltrates=0.9, remote_code=0.0)
    assert ev("any(destructive, exfiltrates) >= 0.85", s)
    assert not ev("all(destructive, exfiltrates) >= 0.85", s)
    assert ev("all(destructive, exfiltrates, remote_code) <= 0.95", s)
    assert not ev("all(destructive, risk) <= 0.95", s)
    assert ev("any(destructive, risk) <= 0.2", s)
    assert not ev("any(risk, weakens_security) <= 0.2", s)


def test_predicates():
    s = {"block": Signal(0.5, grey=True), "risk": Signal(1.0, abstained=True)}
    assert ev("grey(block)", s)
    assert not ev("grey(risk)", s)
    assert ev("abstained(risk)", s)
    assert not ev("abstained(destructive)", s)
    assert ev("rule('deny')", s, rule="deny")
    assert not ev("rule('deny')", s, rule="allow")
    assert not ev("rule('deny')", s)


def test_choice_p():
    s = {"verdict": Signal("deny", p=0.7)}
    assert ev("verdict == 'deny' and verdict.p >= deny_verdict_p", s)
    assert not ev("verdict == 'allow'", s)
    assert ev("verdict != 'allow'", s)
    assert not ev("verdict > 0.1", s)
    assert not ev("verdict.p >= 0.9", s)
    assert not ev("verdict.p >= 0.5", {"verdict": Signal("deny")})


@pytest.mark.parametrize("text", [
    "__import__('os')",
    "__import__('os').system('x') > 1",
    "risk.__class__ > 1",
    "risk.q > 1",
    "risk .p.p > 1",
    "nope > 1",
    "risk > nope",
    "abs(risk) > 1",
    "any(nope) > 1",
    "grey(deny_hazard)",
    "rule('nope')",
    "risk",
    "risk >",
    "risk > 1 garbage",
    "risk > 1)",
    "(risk > 1",
    "risk = 1",
    "risk > 'x",
    "deny_hazard.p > 1",
    "",
    "risk > 1 $",
])
def test_expr_rejected(text):
    with pytest.raises(ExprError):
        pe(text)


def test_expr_error_has_token_and_offset():
    with pytest.raises(ExprError) as e:
        pe("risk > nope")
    assert e.value.token == "nope" and e.value.offset == 7 and "nope" in str(e.value)


@pytest.mark.parametrize("text", [
    "deny if risk > 1",
    "deny if risk > 1;",
    "allow otherwise; ask otherwise",
    "allow otherwise; deny if risk > 1",
    "nope if risk > 1; ask otherwise",
    "deny risk > 1; ask otherwise",
    "deny if risk > 1; ask otherwise trailing",
    "deny if risk > 1; ask otherwise;",
    "ask otherwise if risk > 1",
    "",
])
def test_combine_rejected(text):
    with pytest.raises(ExprError):
        parse_combine(text, **KW)


def test_missing_signals_are_false():
    assert not ev("risk > 0", {})
    assert not ev("risk < 100", {})
    assert not ev("risk != 3", {})
    assert not ev("risk.p > 0", {})
    assert ev("not risk > 0", {})
    assert not ev("all(destructive, risk) <= 1", sig(destructive=0.0))
    assert not evaluate(pe("risk > deny_risk"), signals=sig(risk=3.0), policy={}, rule_decision=None)


def test_decide_first_match():
    c = spec_combine()
    env = dict(policy=POL, rule_decision=None)
    assert decide(c, signals={}, **env) == ("ask", 6)
    assert decide(c, signals={}, policy=POL, rule_decision="deny") == ("deny", 0)
    assert decide(c, signals={}, policy=POL, rule_decision="allow") == ("allow", 1)
    s = sig(destructive=0.1, exfiltrates=0.1, remote_code=0.9999)
    assert decide(c, signals=s, **env) == ("deny", 2)
    s = {"verdict": Signal("deny", p=0.7)}
    assert decide(c, signals=s, **env) == ("deny", 3)
    assert decide(c, signals=sig(risk=2.5), **env) == ("deny", 4)
    ok = sig(destructive=0.0, exfiltrates=0.0, remote_code=0.0, weakens_security=0.0, out_of_scope=0.1, risk=0.5)
    ok["verdict"] = Signal("allow", p=0.9)
    assert decide(c, signals=ok, **env) == ("allow", 5)
    ok["risk"] = Signal(1.2)
    assert decide(c, signals=ok, **env) == ("ask", 6)


def test_explain():
    s = sig(destructive=0.1, exfiltrates=0.1, remote_code=0.9999)
    env = dict(signals=s, policy=POL, rule_decision=None)
    assert explain(pe("any(destructive, exfiltrates, remote_code) >= deny_hazard"), **env) == "remote_code=0.9999 >= 0.85"
    assert explain(pe("remote_code >= deny_hazard"), **env) == "remote_code=0.9999 >= 0.85"
    s["verdict"] = Signal("deny", p=0.7)
    assert explain(pe("verdict == 'deny' and verdict.p >= deny_verdict_p"), **env) == \
        "verdict='deny' == 'deny' and verdict.p=0.7 >= 0.6"
    assert explain(pe("rule('deny')"), **env) == "rule('deny')"
    assert explain(pe("risk > 1"), **env) == "risk=missing > 1"
    assert explain(pe("all(destructive, exfiltrates) <= 0.2"), **env) == "destructive=0.1 <= 0.2, exfiltrates=0.1 <= 0.2"
