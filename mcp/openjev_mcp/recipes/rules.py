"""Deterministic recipe rules on RE2 (spec 2.18): linear-time patterns, no backreferences or lookaround."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import re2

from .shell import SplitResult

MAX_PATTERN_CHARS = 512
MAX_INPUT_BYTES = 65536
SCOPES = ("segment", "command")
LOOKAROUND = ("(?=", "(?!", "(?<=", "(?<!")


class PatternError(ValueError): ...


@dataclass(frozen=True)
class Rule:
    index: int
    decision: str
    scope: str
    match: Any
    unless: Any | None
    source: str


@dataclass(frozen=True)
class RuleVerdict:
    decision: str | None
    rule: int | None
    reason: str


def _forbidden(pattern: str) -> str | None:
    i, n, in_class = 0, len(pattern), False
    while i < n:
        c = pattern[i]
        if c == "\\":
            nxt = pattern[i + 1:i + 2]
            if nxt in "123456789" and nxt:
                return "backreference"
            if nxt == "k" and pattern[i + 2:i + 3] == "<":
                return "backreference"
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
            if pattern[i + 1:i + 2] == "^":
                i += 1
            if pattern[i + 1:i + 2] == "]":
                i += 1
        elif c == "(":
            if pattern.startswith("(?P=", i):
                return "backreference"
            if any(pattern.startswith(p, i) for p in LOOKAROUND):
                return "lookaround"
        i += 1
    return None


def _compile(pattern: Any, what: str) -> Any:
    if not isinstance(pattern, str) or not pattern:
        raise PatternError(f"{what} must be a non-empty string")
    if len(pattern) > MAX_PATTERN_CHARS:
        raise PatternError(f"{what} longer than {MAX_PATTERN_CHARS} characters")
    bad = _forbidden(pattern)
    if bad:
        raise PatternError(f"{what} uses a {bad}, which RE2 rules do not allow")
    try:
        return re2.compile(pattern)
    except Exception as e:
        raise PatternError(f"{what} is not valid RE2: {e}") from e


def compile_rule(spec: Mapping[str, Any], *, index: int, decisions: frozenset[str]) -> Rule:
    decision = spec.get("decision")
    if decision not in decisions:
        raise PatternError(f"rule {index}: decision {decision!r} is not listed in decisions")
    scope = spec.get("scope", "segment")
    if scope not in SCOPES:
        raise PatternError(f"rule {index}: scope {scope!r} must be segment or command")
    match = _compile(spec.get("match"), f"rule {index} match")
    unless = spec.get("unless")
    return Rule(index, decision, scope, match, None if unless is None else _compile(unless, f"rule {index} unless"),
                spec["match"])


def _clip(text: str) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= MAX_INPUT_BYTES:
        return text, False
    return raw[:MAX_INPUT_BYTES].decode("utf-8", errors="ignore"), True


def _hit(rule: Rule, text: str) -> bool:
    return rule.match.search(text) is not None and (rule.unless is None or rule.unless.search(text) is None)


def apply_rules(rules: Sequence[Rule], command: str, split: SplitResult) -> RuleVerdict:
    whole, truncated = _clip(command)
    segments = [_clip(s)[0] for s in split.segments]
    for r in rules:
        if r.decision != "deny":
            continue
        if (r.scope == "command" and _hit(r, whole)) or (r.scope == "segment" and any(_hit(r, s) for s in segments)):
            return RuleVerdict("deny", r.index, f"deny rule {r.index} matched")
    allows = [r for r in rules if r.decision == "allow"]
    if not allows:
        return RuleVerdict(None, None, "no allow rule")
    if truncated:
        return RuleVerdict(None, None, "input truncated; no allow by rule")
    if not split.ok:
        return RuleVerdict(None, None, f"command not splittable ({split.reason}); no allow by rule")
    if not segments:
        return RuleVerdict(None, None, "no segments")
    first: int | None = None
    for s in segments:
        hit = next((r for r in allows if (r.scope == "segment" and _hit(r, s)) or (r.scope == "command" and _hit(r, whole))), None)
        if hit is None:
            return RuleVerdict(None, None, "a segment matches no allow rule")
        if first is None:
            first = hit.index
    return RuleVerdict("allow", first, "every segment matches an allow rule")
