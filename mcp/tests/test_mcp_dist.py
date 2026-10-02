import os
import subprocess
import sys
from importlib import metadata

import pytest

import openjev_mcp

BIN = os.path.dirname(sys.executable)


def test_requirements_stay_light():
    reqs = " ".join(metadata.requires("openjev-mcp") or []).lower()
    for banned in ("transformers", "tokenizers", "openjev"):
        assert banned not in reqs.replace("openjev-mcp", ""), banned
    assert "mcp<3,>=2.2" in reqs or "mcp>=2.2,<3" in reqs


def test_version_constants():
    # deviation: P14: installed dist-info is refreshed only by `pip install -e`, so compare with pyproject.toml, not metadata
    pyproject = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
    assert openjev_mcp.__version__ == "1.4.0" and 'version = "1.4.0"' in open(pyproject).read()
    assert openjev_mcp.TOOL_NAMES == ("ask", "yes_no", "classify", "score", "filter", "batch", "ask_image", "lint",
                                          "compile", "calibrate", "recipe", "status", "generate", "batch_results")
    assert openjev_mcp.CORE_TOOL_NAMES == ("ask", "yes_no", "classify", "score", "lint", "status")
    assert openjev_mcp.PROTOCOL_VERSIONS[0] == "2026-07-28"


@pytest.mark.parametrize("args", [["--version"], ["version"]])
def test_openjev_cli_version(args):
    r = subprocess.run([os.path.join(BIN, "openjev"), *args], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert r.stdout.strip() == f"openjev-mcp {openjev_mcp.__version__}"


def test_openjev_cli_help():
    r = subprocess.run([os.path.join(BIN, "openjev"), "--help"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "usage" in r.stdout.lower()


def test_skeleton_imports_are_light():
    code = ("import sys, openjev_mcp, openjev_mcp.tools, openjev_mcp.recipes; "
            "print(sorted(m for m in ('mcp', 'httpx') if m in sys.modules))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"


def test_no_package_init_shadowing():
    here = os.path.dirname(__file__)
    assert not os.path.exists(os.path.join(here, "__init__.py"))
    assert not os.path.exists(os.path.join(here, "..", "__init__.py"))


def test_tools_module_has_only_types():
    from openjev_mcp import tools
    assert not hasattr(tools, "TOOLS")
    assert {"ToolContext", "ToolSpec", "Handler", "UnknownTool"} <= set(vars(tools))
