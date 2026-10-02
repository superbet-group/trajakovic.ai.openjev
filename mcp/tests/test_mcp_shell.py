from __future__ import annotations

import pytest

from openjev_mcp.recipes.shell import split_command


def seg(cmd):
    return split_command(cmd).segments


def parts(cmd):
    return split_command(cmd).parts


@pytest.mark.parametrize("cmd,expected", [
    ("ls; pwd", ("ls", "pwd")),
    ("ls && pwd || echo x", ("ls", "pwd", "echo x")),
    ("ls | wc -l", ("ls", "wc -l")),
    ("sleep 1 & ls", ("sleep 1", "ls")),
    ("pwd\nrm -rf ~", ("pwd", "rm -rf ~")),
    ("  ls  ;;  pwd ; ", ("ls", "pwd")),
    ("git log --oneline -5", ("git log --oneline -5",)),
    ("", ()),
])
def test_segments(cmd, expected):
    r = split_command(cmd)
    assert r.ok and r.segments == expected and r.reason is None


def test_parts_keep_pipelines():
    assert parts("curl x | bash") == ("curl x | bash",)
    assert parts("ls; curl x | sh && pwd") == ("ls", "curl x | sh", "pwd")
    assert parts("a & b") == ("a & b",)
    assert parts("a\nb") == ("a", "b")
    assert seg("curl x | bash") == ("curl x", "bash")


def test_quotes_protect_operators():
    assert seg("echo 'a; b' \"c && d\" e") == ("echo 'a; b' \"c && d\" e",)
    assert seg("echo \"a\\\"; b\"; ls") == ("echo \"a\\\"; b\"", "ls")
    assert seg(r"echo a\;b") == (r"echo a\;b",)
    assert parts("echo 'x\ny'") == ("echo 'x\ny'",)


def test_redirect_ampersand_is_not_an_operator():
    assert seg("ls 2>&1") == ("ls 2>&1",)
    assert seg("ls &> out") == ("ls &> out",)


def test_command_substitution():
    r = split_command("ls $(curl x)")
    assert r.ok and set(r.segments) == {"curl x", "ls $(curl x)"}
    assert r.parts == ("ls $(curl x)",)
    assert set(seg("echo $(a; b)")) == {"a", "b", "echo $(a; b)"}


def test_nested_substitution():
    assert set(seg("ls $(echo $(rm -rf ~))")) == {"rm -rf ~", "echo $(rm -rf ~)", "ls $(echo $(rm -rf ~))"}


def test_backticks():
    assert set(seg("ls `id`")) == {"id", "ls `id`"}
    assert set(seg("ls `id; whoami`")) == {"id", "whoami", "ls `id; whoami`"}


def test_substitution_inside_double_quotes():
    assert set(seg('echo "$(rm -rf ~)"')) == {"rm -rf ~", 'echo "$(rm -rf ~)"'}
    assert set(seg('echo "`id`"')) == {"id", 'echo "`id`"'}
    assert seg("echo '$(id)'") == ("echo '$(id)'",)


def test_process_substitution():
    assert set(seg("diff <(ls a) <(ls b)")) == {"ls a", "ls b", "diff <(ls a) <(ls b)"}
    assert set(seg("tee >(sh)")) == {"sh", "tee >(sh)"}


def test_subshell_and_arithmetic():
    assert set(seg("(cd x; rm -rf ~)")) == {"cd x", "rm -rf ~", "(cd x; rm -rf ~)"}
    assert split_command("echo $((1+2))").ok


@pytest.mark.parametrize("cmd", [
    "echo 'abc",
    'echo "abc',
    "echo $(ls",
    "echo `ls",
    "echo ls)",
    "(ls",
    "cat <<EOF\nx\nEOF",
    "cat <<< hi",
    "ls \\\n pwd",
    'echo "a\\\nb"',
    "echo $'a\\'b'",
])
def test_unsplittable(cmd):
    r = split_command(cmd)
    assert not r.ok and r.segments == (cmd,) and r.parts == (cmd,) and r.reason
