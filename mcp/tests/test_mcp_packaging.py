"""Distribution metadata, console scripts and package data (TASKS 1.1)."""
from __future__ import annotations

import os
import re
import subprocess
from importlib import metadata, resources

import pytest
import stubs

BIN = os.path.join(stubs.REPO, ".venv", "bin")
SCRIPTS = {"openjev-mcp": "openjev_mcp.server:main", "openjev-hook": "openjev_mcp.hook:main",
           "openjev": "openjev_mcp.cli:main"}


def test_requirements_stay_light():
    names = {re.split(r"[ ;<>=!~\[(]", r, 1)[0].lower().replace("_", "-") for r in metadata.requires("openjev-mcp") or []
             if "extra ==" not in r}
    assert names, "no requirements found"
    assert not names & {"transformers", "tokenizers", "openjev", "torch", "mlx", "vllm"}


@pytest.mark.parametrize("name,target", SCRIPTS.items())
def test_entry_point_resolves(name, target):
    eps = [e for e in metadata.entry_points(group="console_scripts") if e.name == name and e.value == target]
    assert eps, f"console script {name} -> {target} missing"
    assert callable(eps[0].load())


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_help_exits_zero(name):
    run = subprocess.run([os.path.join(BIN, name), "--help"], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0 and "usage" in run.stdout.lower(), run.stderr


def test_openjev_version():
    run = subprocess.run([os.path.join(BIN, "openjev"), "--version"], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0 and "1.4.0" in run.stdout


def test_command_gate_package_data():
    path = resources.files("openjev_mcp") / "recipes" / "builtin" / "command_gate.json"
    assert path.is_file() and path.read_text(encoding="utf-8").lstrip().startswith("{")


def test_pillow_only_in_the_images_and_test_extras():
    import tomllib   # the installed metadata can lag an editable install's pyproject
    with open(os.path.join(stubs.REPO, "mcp", "pyproject.toml"), "rb") as f:
        proj = tomllib.load(f)["project"]
    assert not [d for d in proj["dependencies"] if d.lower().startswith("pillow")]
    extras = {k for k, v in proj["optional-dependencies"].items() if any(d.lower().startswith("pillow") for d in v)}
    assert extras == {"images", "test"} and proj["optional-dependencies"]["images"] == ["Pillow>=10"]
