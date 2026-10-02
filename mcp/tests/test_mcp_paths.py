import os

import pytest

from openjev_mcp.config import Config
from openjev_mcp.errors import ToolError
from openjev_mcp.paths import read_text, resolve_read, resolve_write, within_roots


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    return str(r.resolve())


def cfg(root, transport="stdio", *extra):
    return Config(roots=(root, *extra), transport=transport)


def refused(fn, code="OJ_INVALID_INPUT", match=None):
    with pytest.raises(ToolError, match=match) as e:
        fn()
    assert e.value.code == code
    return e.value


def test_read_ok_and_relative_stdio(root):
    (open(os.path.join(root, "a.csv"), "w")).write("x")
    c = cfg(root)
    assert str(resolve_read("a.csv", c)) == os.path.join(root, "a.csv")
    assert str(resolve_read(os.path.join(root, "a.csv"), c)) == os.path.join(root, "a.csv")


def test_relative_under_http_refused(root):
    open(os.path.join(root, "a.csv"), "w").close()
    err = refused(lambda: resolve_read("a.csv", cfg(root, "http")), match="relative path")
    assert "daemon" in err.message
    assert resolve_read(os.path.join(root, "a.csv"), cfg(root, "http"))


def test_dotdot_escape(root):
    refused(lambda: resolve_read(os.path.join(root, "..", "x.csv"), cfg(root)), match="outside the allowed roots")
    refused(lambda: resolve_read("../x.csv", cfg(root)), match="outside the allowed roots")


def test_extra_root_and_within_roots(root, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "b.txt").write_text("x")
    c = cfg(root, "http", str(other.resolve()))
    assert resolve_read(str(other / "b.txt"), c)
    assert within_roots(str(other), c) and not within_roots(str(tmp_path), c)
    assert not within_roots(root + "-sibling", c)


def test_symlink_out_of_root(root, tmp_path):
    out = tmp_path / "secret.csv"
    out.write_text("x")
    os.symlink(out, os.path.join(root, "link.csv"))
    refused(lambda: resolve_read("link.csv", cfg(root)), match="outside the allowed roots")


@pytest.mark.parametrize("name", [".git/config.json", ".ssh/x.json", ".claude/x.json", "sub/.hid/x.json"])
def test_write_dot_dirs(root, name):
    os.makedirs(os.path.join(root, os.path.dirname(name)), exist_ok=True)
    refused(lambda: resolve_write(name, cfg(root), exts={".json"}, new_only=True), match="dot-directory")


def test_write_dotfile_ext_existing_parent(root):
    c = cfg(root)
    refused(lambda: resolve_write(".out.json", c, exts={".json"}, new_only=True), match="dotfile")
    refused(lambda: resolve_write("o.exe", c, exts={".json"}, new_only=True), match="extension")
    refused(lambda: resolve_write("nodir/o.json", c, exts={".json"}, new_only=True), match="parent")
    refused(lambda: resolve_write("/etc/o.json", c, exts={".json"}, new_only=True), match="outside")


def test_write_existing_and_symlink(root, tmp_path):
    c = cfg(root)
    p = os.path.join(root, "o.csv")
    assert str(resolve_write("o.csv", c, exts={".csv"}, new_only=True)) == p
    open(p, "w").close()
    refused(lambda: resolve_write("o.csv", c, exts={".csv"}, new_only=True), match="already exists")
    assert resolve_write("o.csv", c, exts={".csv"}, new_only=False)
    os.symlink(tmp_path / "t.csv", os.path.join(root, "l.csv"))
    refused(lambda: resolve_write("l.csv", c, exts={".csv"}, new_only=False), match="symlink")


def test_write_symlinked_dir_out(root, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", os.path.join(root, "d"))
    refused(lambda: resolve_write("d/o.json", cfg(root), exts={".json"}, new_only=True), match="outside")


@pytest.mark.parametrize("ext", [".xlsx", ".xls", ".ods", ".numbers"])
def test_e030_spreadsheet(root, ext):
    err = refused(lambda: resolve_read(os.path.join(root, "a" + ext), cfg(root)), code="E030")
    assert err.hint == "export the sheet as CSV first"


@pytest.mark.parametrize("ext", [".pdf", ".docx", ".zip", ".gz", ".parquet", ".sqlite", ".db"])
def test_e030_binary(root, ext):
    err = refused(lambda: resolve_read(os.path.join(root, "a" + ext), cfg(root)), code="E030")
    assert err.hint == "export it as CSV or JSONL"


def test_wrong_ext_kinds_and_missing(root):
    open(os.path.join(root, "a.png"), "w").close()
    open(os.path.join(root, "a.csv"), "w").close()
    c = cfg(root)
    assert resolve_read("a.png", c, "image")
    refused(lambda: resolve_read("a.png", c), match="extension")
    refused(lambda: resolve_read("a.csv", c, "image"), match="extension")
    refused(lambda: resolve_read("nope.csv", c), code="OJ_NOT_FOUND")


def test_size_caps_sparse(root):
    for name, size in (("big.jsonl", 64 * 2**20 + 1), ("big.png", 20 * 2**20 + 1)):
        with open(os.path.join(root, name), "wb") as f:
            f.truncate(size)
    c = cfg(root)
    refused(lambda: resolve_read("big.jsonl", c), code="OJ_TOO_LARGE", match="64 MiB")
    refused(lambda: resolve_read("big.png", c, "image"), code="OJ_TOO_LARGE", match="20 MiB")
    with open(os.path.join(root, "ok.jsonl"), "wb") as f:
        f.truncate(64 * 2**20)
    assert resolve_read("ok.jsonl", c)


def test_read_text_decodings(tmp_path):
    def w(b):
        p = tmp_path / "t.txt"
        p.write_bytes(b)
        return read_text(p)

    assert w("héllo".encode()) == ("héllo", "utf-8", [])
    assert w(b"\xef\xbb\xbfabc") == ("abc", "utf-8", [])
    assert w(b"\xff\xfe" + "héllo".encode("utf-16-le")) == ("héllo", "utf-16", [])
    assert w(b"\xfe\xff" + "héllo".encode("utf-16-be")) == ("héllo", "utf-16", [])
    text, enc, warns = w("café €".encode("cp1252"))
    assert (text, enc) == ("café €", "cp1252") and warns and warns[0].startswith("W603")
