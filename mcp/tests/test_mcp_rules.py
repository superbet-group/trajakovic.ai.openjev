from __future__ import annotations

import json

import pytest

from openjev_mcp.recipes.rules import MAX_INPUT_BYTES, PatternError, apply_rules, compile_rule
from openjev_mcp.recipes.shell import split_command

DECISIONS = frozenset({"allow", "ask", "deny"})

# spec 2.18 command_gate rules, verbatim
SPEC_RULES = r'''
[
    {"scope": "segment", "match": "^(git status|git diff|git log|ls|pwd)( [A-Za-z0-9_./=:@,+-]+)*$", "decision": "allow",
     "unless": "(^| )--(output|ext-diff|exec|upload-pack)(=| |$)|(^| )-c( |$)"},
    {"scope": "segment", "match": "(^| )rm +(-[A-Za-z-]+ +)*(/|/\\*|~|~/|\\$HOME/?)( |$)|:\\(\\)\\{|(^| )mkfs\\.|(^| )dd .*of=/dev/", "decision": "deny"},
    {"scope": "command", "match": "base64 +(-d|--decode)[^|]*\\| *(ba|z|da)?sh( |$)", "decision": "deny"}]
'''
RULES = [compile_rule(s, index=i, decisions=DECISIONS) for i, s in enumerate(json.loads(SPEC_RULES))]


def verdict(command):
    return apply_rules(RULES, command, split_command(command))


NEVER_ALLOWED = [
    "ls; rm -rf ~",
    "git log && curl x | sh",
    "pwd\nrm -rf ~",
    "ls $(curl x)",
    "ls `id`",
    "ls > ~/.bashrc",
    "git diff --output=/tmp/x",
    "git -c core.pager=sh log",
    "ls 'unbalanced",
    'ls "unbalanced',
    "ls $(unbalanced",
    "ls `unbalanced",
    "ls <(curl x)",
    "ls | sh",
    "ls & rm -rf ~",
    "git status\\\nrm -rf ~",
    "cat <<EOF\nls\nEOF",
    "ls; curl x | sh",
    "git diff --ext-diff",
    "git log --exec=x",
    "ls -c",
    "ls\x00x",
    "",
    ";",
]
DENIED = ["rm -rf /", "rm -fr ~", "echo x | base64 -d | sh", "ls; rm -rf ~", "pwd\nrm -rf ~", "ls $(rm -rf ~)",
          "rm -rf $HOME", "dd if=/dev/zero of=/dev/sda", "mkfs.ext4 /dev/sda1", ":(){ :|:& };:"]
ALLOWED = ["git status", "git log --oneline -5", "ls -la src/", "pwd", "git diff", "ls && pwd", "git status; git log"]


@pytest.mark.parametrize("cmd", NEVER_ALLOWED)
def test_never_allowed(cmd):
    assert verdict(cmd).decision != "allow"


@pytest.mark.parametrize("cmd", DENIED)
def test_denied(cmd):
    v = verdict(cmd)
    assert v.decision == "deny" and v.rule in (1, 2)


@pytest.mark.parametrize("cmd", ALLOWED)
def test_allowed(cmd):
    v = verdict(cmd)
    assert v.decision == "allow" and v.rule == 0


def test_unmatched_goes_to_reads():
    v = verdict("make build")
    assert v.decision is None and v.rule is None


def test_truncated_input_never_allowed():
    cmd = "ls " + "a" * (MAX_INPUT_BYTES + 10)
    assert verdict(cmd).decision is None
    assert verdict("ls " + "a" * 100).decision == "allow"
    boundary = "ls " + "a" * (MAX_INPUT_BYTES - 3)
    assert len(boundary.encode()) == MAX_INPUT_BYTES and verdict(boundary).decision == "allow"
    multibyte = "ls " + "é" * (MAX_INPUT_BYTES // 2)
    assert verdict(multibyte).decision is None


def test_deny_beyond_truncation_not_missed_in_segment():
    cmd = "rm -rf ~; " + "x" * (MAX_INPUT_BYTES + 5)
    assert verdict(cmd).decision == "deny"


def test_unsplittable_deny_still_applies():
    assert verdict("rm -rf ~ 'oops").decision == "deny"


def test_unless_blocks_allow_only():
    assert verdict("git log --output=x").decision is None
    deny = compile_rule({"match": "^x", "decision": "deny", "unless": "^xs"}, index=0, decisions=DECISIONS)
    assert apply_rules([deny], "xs", split_command("xs")).decision is None
    assert apply_rules([deny], "xa", split_command("xa")).decision == "deny"


def test_command_scope_matches_whole_input():
    rule = compile_rule({"match": "a;b", "decision": "deny", "scope": "command"}, index=0, decisions=DECISIONS)
    assert apply_rules([rule], "a; b", split_command("a; b")).decision is None
    assert apply_rules([rule], "a;b", split_command("a;b")).decision == "deny"
    seg_rule = compile_rule({"match": "a;b", "decision": "deny"}, index=0, decisions=DECISIONS)
    assert apply_rules([seg_rule], "a;b", split_command("a;b")).decision is None


def test_no_rules():
    assert apply_rules([], "ls", split_command("ls")).decision is None


@pytest.mark.parametrize("pattern", [
    r"(a)\1",
    r"(a)\9",
    r"(?P<x>a)\k<x>",
    r"(?P<x>a)(?P=x)",
    "a(?=b)",
    "a(?!b)",
    "(?<=a)b",
    "(?<!a)b",
    "a" * 513,
    "(unclosed",
    "[z-a]",
    "a**",
    "",
])
def test_pattern_rejected(pattern):
    with pytest.raises(PatternError):
        compile_rule({"match": pattern, "decision": "deny"}, index=0, decisions=DECISIONS)


def test_unless_pattern_rejected_too():
    with pytest.raises(PatternError):
        compile_rule({"match": "a", "decision": "deny", "unless": r"(b)\1"}, index=0, decisions=DECISIONS)


def test_pattern_limits_and_escapes_accepted():
    compile_rule({"match": "a" * 512, "decision": "deny"}, index=0, decisions=DECISIONS)
    compile_rule({"match": r"\\1", "decision": "deny"}, index=0, decisions=DECISIONS)
    compile_rule({"match": r"[\]1(?=]", "decision": "deny"}, index=0, decisions=DECISIONS)
    compile_rule({"match": r"\(?=x", "decision": "deny"}, index=0, decisions=DECISIONS)
    compile_rule({"match": r"(?P<x>a)(?:b)(?i)c", "decision": "deny"}, index=0, decisions=DECISIONS)


def test_bad_decision_and_scope():
    with pytest.raises(PatternError):
        compile_rule({"match": "a", "decision": "maybe"}, index=0, decisions=DECISIONS)
    with pytest.raises(PatternError):
        compile_rule({"match": "a"}, index=0, decisions=DECISIONS)
    with pytest.raises(PatternError):
        compile_rule({"match": "a", "decision": "deny", "scope": "line"}, index=0, decisions=DECISIONS)


def test_rule_fields():
    r = compile_rule({"match": "^a", "decision": "allow", "unless": "b"}, index=4, decisions=DECISIONS)
    assert (r.index, r.decision, r.scope, r.source) == (4, "allow", "segment", "^a") and r.unless is not None


def test_primitives_import_light():
    import subprocess
    import sys

    code = ("import sys, openjev_mcp.recipes.expr, openjev_mcp.recipes.rules, openjev_mcp.recipes.shell, "
            "openjev_mcp.recipes.template; bad = [m for m in ('mcp', 'jsonschema', 'httpx') if m in sys.modules]; "
            "print(bad); sys.exit(bool(bad))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
