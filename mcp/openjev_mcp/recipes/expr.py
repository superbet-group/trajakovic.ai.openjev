"""Recipe expression language (spec 2.18): hand-written tokenizer, recursive-descent parser, evaluator."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

KEYWORDS = frozenset({"if", "otherwise", "and", "or", "not", "any", "all", "grey", "abstained", "rule"})
OPS = (">=", "<=", "==", "!=", ">", "<")


class ExprError(ValueError):
    def __init__(self, message: str, token: str = "", offset: int = -1):
        self.token = token
        self.offset = offset
        super().__init__(f"{message} at offset {offset} near {token!r}" if offset >= 0 else message)


@dataclass(frozen=True)
class Signal:
    value: float | str
    p: float | None = None
    grey: bool = False
    abstained: bool = False


@dataclass(frozen=True)
class Lit:
    value: float | str


@dataclass(frozen=True)
class SigRef:
    name: str
    attr: str | None = None


@dataclass(frozen=True)
class PolRef:
    name: str


@dataclass(frozen=True)
class Compare:
    left: Lit | SigRef | PolRef
    op: str
    right: Lit | SigRef | PolRef


@dataclass(frozen=True)
class Quant:
    kind: str
    signals: tuple[str, ...]
    op: str
    right: Lit | SigRef | PolRef


@dataclass(frozen=True)
class Pred:
    kind: str
    arg: str


@dataclass(frozen=True)
class Or:
    items: tuple[Any, ...]


@dataclass(frozen=True)
class And:
    items: tuple[Any, ...]


@dataclass(frozen=True)
class Not:
    item: Any


Node = Or | And | Not | Compare | Quant | Pred


@dataclass(frozen=True)
class Clause:
    decision: str
    cond: Node | None


@dataclass(frozen=True)
class Combine:
    clauses: tuple[Clause, ...]


@dataclass(frozen=True)
class _Tok:
    kind: str
    text: str
    pos: int


def _tokenize(text: str) -> list[_Tok]:
    toks: list[_Tok] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c.isascii() and (c.isalpha() or c == "_"):
            j = i + 1
            while j < n and text[j].isascii() and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            toks.append(_Tok("kw" if word in KEYWORDS else "id", word, i))
            i = j
        elif c.isdigit() or (c == "-" and i + 1 < n and text[i + 1].isdigit()):
            j = i + 1
            while j < n and text[j].isdigit():
                j += 1
            if j + 1 < n and text[j] == "." and text[j + 1].isdigit():
                j += 1
                while j < n and text[j].isdigit():
                    j += 1
            toks.append(_Tok("num", text[i:j], i))
            i = j
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise ExprError("unterminated string", text[i:], i)
            toks.append(_Tok("str", text[i + 1:j], i))
            i = j + 1
        elif c == ".":
            if text[i + 1:i + 2] == "p" and not (text[i + 2:i + 3].isascii() and (text[i + 2:i + 3].isalnum() or text[i + 2:i + 3] == "_")):
                toks.append(_Tok("dotp", ".p", i))
                i += 2
            else:
                raise ExprError("attribute access is not allowed (only .p)", text[i:i + 12], i)
        elif c in "(),;":
            toks.append(_Tok(c, c, i))
            i += 1
        else:
            op = next((o for o in OPS if text.startswith(o, i)), None)
            if op is None:
                raise ExprError("unexpected character", c, i)
            toks.append(_Tok("op", op, i))
            i += len(op)
    toks.append(_Tok("end", "", n))
    return toks


class _Parser:
    def __init__(self, text: str, signals: frozenset[str], policy: frozenset[str], decisions: frozenset[str]):
        self.toks = _tokenize(text)
        self.i = 0
        self.signals, self.policy, self.decisions = signals, policy, decisions

    @property
    def tok(self) -> _Tok:
        return self.toks[self.i]

    def fail(self, message: str) -> ExprError:
        return ExprError(message, self.tok.text or "<end>", self.tok.pos)

    def take(self, kind: str, text: str | None = None) -> _Tok:
        t = self.tok
        if t.kind != kind or (text is not None and t.text != text):
            raise self.fail(f"expected {text or kind}")
        self.i += 1
        return t

    def at(self, kind: str, text: str | None = None) -> bool:
        return self.tok.kind == kind and (text is None or self.tok.text == text)

    def expect_end(self) -> None:
        if not self.at("end"):
            raise self.fail("trailing input")

    def combine(self) -> Combine:
        clauses: list[Clause] = []
        while True:
            t = self.take("id")
            if t.text not in self.decisions:
                raise ExprError("unknown decision", t.text, t.pos)
            if self.at("kw", "otherwise"):
                self.i += 1
                clauses.append(Clause(t.text, None))
                break
            self.take("kw", "if")
            clauses.append(Clause(t.text, self.expr()))
            if not self.at(";"):
                raise self.fail("combine must end with '<decision> otherwise'")
            self.i += 1
        self.expect_end()
        return Combine(tuple(clauses))

    def expr(self) -> Node:
        items = [self.and_expr()]
        while self.at("kw", "or"):
            self.i += 1
            items.append(self.and_expr())
        return items[0] if len(items) == 1 else Or(tuple(items))

    def and_expr(self) -> Node:
        items = [self.not_expr()]
        while self.at("kw", "and"):
            self.i += 1
            items.append(self.not_expr())
        return items[0] if len(items) == 1 else And(tuple(items))

    def not_expr(self) -> Node:
        if self.at("kw", "not"):
            self.i += 1
            return Not(self.not_expr())
        return self.atom()

    def signal(self) -> str:
        t = self.take("id")
        if t.text not in self.signals:
            raise ExprError("unknown signal", t.text, t.pos)
        return t.text

    def atom(self) -> Node:
        t = self.tok
        if t.kind == "(":
            self.i += 1
            node = self.expr()
            self.take(")")
            return node
        if t.kind == "kw" and t.text in ("any", "all"):
            self.i += 1
            self.take("(")
            names = [self.signal()]
            while self.at(","):
                self.i += 1
                names.append(self.signal())
            self.take(")")
            op = self.take("op").text
            return Quant(t.text, tuple(names), op, self.value())
        if t.kind == "kw" and t.text in ("grey", "abstained"):
            self.i += 1
            self.take("(")
            name = self.signal()
            self.take(")")
            return Pred(t.text, name)
        if t.kind == "kw" and t.text == "rule":
            self.i += 1
            self.take("(")
            d = self.take("str")
            if d.text not in self.decisions:
                raise ExprError("unknown decision", d.text, d.pos)
            self.take(")")
            return Pred("rule", d.text)
        left = self.value()
        op = self.take("op").text
        return Compare(left, op, self.value())

    def value(self) -> Lit | SigRef | PolRef:
        t = self.tok
        if t.kind == "num":
            self.i += 1
            return Lit(float(t.text))
        if t.kind == "str":
            self.i += 1
            return Lit(t.text)
        if t.kind == "id":
            self.i += 1
            if t.text in self.signals:
                if self.at("dotp"):
                    self.i += 1
                    return SigRef(t.text, "p")
                return SigRef(t.text)
            if t.text in self.policy:
                if self.at("dotp"):
                    raise self.fail(".p is only valid on a signal")
                return PolRef(t.text)
            raise ExprError("unknown identifier", t.text, t.pos)
        raise self.fail("expected a value")


def parse_expr(text: str, *, signals: frozenset[str], policy: frozenset[str],
               decisions: frozenset[str]) -> Node:
    p = _Parser(text, signals, policy, decisions)
    node = p.expr()
    p.expect_end()
    return node


def parse_combine(text: str, *, signals: frozenset[str], policy: frozenset[str],
                  decisions: frozenset[str]) -> Combine:
    return _Parser(text, signals, policy, decisions).combine()


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _cmp(a: Any, op: str, b: Any) -> bool:
    if a is None or b is None:
        return False
    if op == "==":
        return a == b and _num(a) == _num(b)
    if op == "!=":
        return not (a == b and _num(a) == _num(b))
    if not (_num(a) and _num(b)):
        return False
    return {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[op]


def _val(v: Lit | SigRef | PolRef, signals: Mapping[str, Signal], policy: Mapping[str, Any]) -> Any:
    if isinstance(v, Lit):
        return v.value
    if isinstance(v, PolRef):
        return policy.get(v.name)
    s = signals.get(v.name)
    if s is None:
        return None
    return s.p if v.attr == "p" else s.value


def evaluate(node: Node, *, signals: Mapping[str, Signal], policy: Mapping[str, float | str],
             rule_decision: str | None) -> bool:
    def ev(n: Node) -> bool:
        if isinstance(n, Or):
            return any(ev(x) for x in n.items)
        if isinstance(n, And):
            return all(ev(x) for x in n.items)
        if isinstance(n, Not):
            return not ev(n.item)
        if isinstance(n, Compare):
            return _cmp(_val(n.left, signals, policy), n.op, _val(n.right, signals, policy))
        if isinstance(n, Quant):
            rhs = _val(n.right, signals, policy)
            hits = [_cmp(_val(SigRef(s), signals, policy), n.op, rhs) if s in signals else None for s in n.signals]
            if n.kind == "all":
                return all(h is True for h in hits)
            return any(h is True for h in hits)
        if n.kind == "rule":
            return rule_decision == n.arg
        s = signals.get(n.arg)
        return s is not None and (s.grey if n.kind == "grey" else s.abstained)

    return ev(node)


def decide(combine: Combine, **env: Any) -> tuple[str, int]:
    for i, c in enumerate(combine.clauses):
        if c.cond is None or evaluate(c.cond, **env):
            return c.decision, i
    raise ExprError("combine has no matching clause", "", -1)


def _fmt(v: Any) -> str:
    if v is None:
        return "missing"
    if _num(v):
        return f"{round(v, 4):g}"
    return repr(v)


def explain(node: Node, **env: Any) -> str:
    signals, policy = env["signals"], env["policy"]

    def side(v: Lit | SigRef | PolRef) -> str:
        if isinstance(v, SigRef):
            return f"{v.name}{'.p' if v.attr else ''}={_fmt(_val(v, signals, policy))}"
        return _fmt(_val(v, signals, policy))

    def ex(n: Node) -> str:
        if isinstance(n, Or):
            return next((ex(x) for x in n.items if evaluate(x, **env)), ex(n.items[0]))
        if isinstance(n, And):
            return " and ".join(ex(x) for x in n.items)
        if isinstance(n, Not):
            return f"not ({ex(n.item)})"
        if isinstance(n, Compare):
            return f"{side(n.left)} {n.op} {side(n.right)}"
        if isinstance(n, Quant):
            hit = [s for s in n.signals if s in signals and _cmp(_val(SigRef(s), signals, policy), n.op, _val(n.right, signals, policy))]
            names = hit if n.kind == "any" and hit else n.signals
            if n.kind == "any" and hit:
                names = hit[:1]
            return ", ".join(f"{side(SigRef(s))} {n.op} {side(n.right)}" for s in names)
        if n.kind == "rule":
            return f"rule('{n.arg}')"
        return f"{n.kind}({n.arg})"

    return ex(node)
