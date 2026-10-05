"""Docs against code: env names, mise tasks, skill tool names, defaults."""
from __future__ import annotations

import io
import json
import os
import re
import shlex

import stubs

import openjev_mcp
from openjev_mcp import config
from openjev_mcp.tools.dispatch import TOOLS

REPO = stubs.REPO
MCP = os.path.join(REPO, "mcp")


def read(*parts: str) -> str:
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def test_env_table_matches_config():
    readme = read("mcp", "README.md")
    table = set(re.findall(r"^\| `(OPENJEV_[A-Z_]+)`", readme, re.M))
    source = read("mcp", "openjev_mcp", "config.py")
    in_code = set(re.findall(r"\"(OPENJEV_[A-Z_]+)\"", source))
    assert table, "env table not found"
    assert table <= in_code, f"documented but not read: {sorted(table - in_code)}"
    assert {n for n in in_code if n.startswith("OPENJEV_MCP_")} <= table, "read but undocumented"
    assert {"OPENJEV_BASE_URL", "OPENJEV_API_KEY"} <= table


def test_mise_tasks_exist():
    for doc in ("README.md", os.path.join("mcp", "README.md")):
        tasks = set(re.findall(r"mise run ([a-z][a-z-]*)", read(doc)))
        assert tasks, doc
        for t in tasks:
            path = os.path.join(REPO, "mise-tasks", t)
            assert t == "default" or (os.path.isfile(path) and os.access(path, os.X_OK)), f"{doc}: mise run {t}"
    assert "default" in read("mise.toml") or os.access(os.path.join(REPO, "mise-tasks", "start"), os.X_OK)


def test_skill_tools_are_real():
    skills = os.path.join(REPO, "plugins", "openjev-skills", "skills")
    names = sorted(os.listdir(skills))
    assert len(names) == 12 and {"openjev-decisions", "openjev-calibration", "openjev-data-prep"} <= set(names)
    mentioned: set[str] = set()
    for n in names:
        mentioned |= set(re.findall(r"`(\w+)`", read("plugins", "openjev-skills", "skills", n, "SKILL.md"))) & set(openjev_mcp.TOOL_NAMES)
    assert mentioned >= set(openjev_mcp.TOOL_NAMES) - {"generate"}, sorted(set(openjev_mcp.TOOL_NAMES) - mentioned)


def test_readme_surface_table_names_every_tool():
    readme = read("mcp", "README.md")
    table = re.findall(r"^\| `(\w+)` \|", readme.split("## Surface")[1].split("## Register")[0], re.M)
    assert set(table) == set(openjev_mcp.TOOL_NAMES) and len(table) == len(openjev_mcp.TOOL_NAMES)


def test_readme_resources_and_prompts_are_real():
    from openjev_mcp import prompts, resources
    readme = read("mcp", "README.md")
    for uri in re.findall(r"`(openjev://[\w/{}]+)`", readme):
        assert uri in {r["uri"] for r in resources.RESOURCES} | {t["uriTemplate"] for t in resources.TEMPLATES}, uri
    for p in prompts.PROMPTS:
        assert f"`{p.name}`" in readme, p.name


def readme_settings():
    blocks = re.findall(r"```json\n(\{\"hooks\".*?)\n```", read("mcp", "README.md"), re.S)
    assert len(blocks) == 1
    return json.loads(blocks[0])["hooks"]


def test_readme_hooks_example_is_valid_and_obeys_the_timeout_rule():
    from openjev_mcp import hook
    hooks = readme_settings()
    assert set(hooks) == {"PreToolUse", "Stop", "UserPromptSubmit", "PostToolUse"}
    for event, entries in hooks.items():
        for entry in entries:
            for h in entry["hooks"]:
                argv = shlex.split(h["command"])
                assert argv[0].endswith("openjev-hook")
                args = hook._parser().parse_args(argv[1:])
                assert args.timeout_ms + 1000 <= h["timeout"] * 1000, event   # the CLI decides before the harness does


def test_readme_hook_runs_against_the_mock_server(monkeypatch, capsys):
    from openjev_mcp import hook
    cmd = shlex.split(readme_settings()["PreToolUse"][0]["hooks"][0]["command"])[1:]
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "rm -rf /"}, "session_id": "s", "cwd": "/tmp"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert hook.main(cmd, transport=stubs.asgi_transport(stubs.openjev_app())) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_readme_cli_check_runs_against_the_mock_server(monkeypatch, capsys, tmp_path):
    from openjev_mcp import cli
    inputs = tmp_path / "in.json"
    inputs.write_text(json.dumps({"task": "fix the auth test", "command": "pytest -q"}))
    code = cli.main(["check", "command_gate", "--inputs", str(inputs)], transport=stubs.asgi_transport(stubs.openjev_app()))
    assert code in (0, 1, 2) and json.loads(capsys.readouterr().out)


def test_tool_registry_matches_names():
    assert tuple(t.name for t in TOOLS) == openjev_mcp.TOOL_NAMES
    core = openjev_mcp.CORE_TOOL_NAMES
    assert tuple(n for n in openjev_mcp.TOOL_NAMES if n in core) == core   # core is an ordered subset (Decision 1)


def test_version_matches_pyproject():
    assert f'version = "{openjev_mcp.__version__}"' in read("mcp", "pyproject.toml")
    assert openjev_mcp.__version__ == "0.5.0" and openjev_mcp.SPEC_VERSION == "1.2"


def test_resource_uris_in_listing_are_documented_in_spec():
    from openjev_mcp import resources
    spec = read("docs", "mcp-skill-spec", "OPENJEV_MCP_SKILLS_SPEC.md")
    for r in resources.RESOURCES:
        assert f"`{r['uri']}`" in spec, r["uri"]
    for t in resources.TEMPLATES:
        assert f"`{t['uriTemplate']}`" in spec, t["uriTemplate"]


def test_port_and_path_match_defaults():
    readme = read("mcp", "README.md")
    default = config.Config()
    assert default.port == 8100 and f"127.0.0.1:{default.port}/mcp" in readme
    assert re.search(rf"`OPENJEV_MCP_PORT` \| `{default.port}`", readme)
    from openjev_mcp.http_app import MCP_PATH
    assert MCP_PATH == "/mcp"
