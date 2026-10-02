from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import openjev_mcp
from openjev_mcp import validate
from openjev_mcp.config import Config
from openjev_mcp.tools.dispatch import tools_for
from openjev_mcp.recipes.engine import load_builtin

MCP = Path(__file__).resolve().parents[1]
SKILLS = MCP / "skills"
README = MCP / "README.md"
SKILL_NAMES = ("openjev-decisions", "openjev-question-authoring", "openjev-triage-routing", "openjev-agent-gates",
               "openjev-code-checks", "openjev-dispatch", "openjev-retrieval-relevance", "openjev-multistep",
               "openjev-data-records", "openjev-ui-vision", "openjev-calibration")


ALL_TOOLS = openjev_mcp.TOOL_NAMES
RECIPE_IDS = tuple(sorted(p.stem for p in (MCP / "openjev_mcp" / "recipes" / "builtin").glob("*.json")))
TOOL_KEYS = {"recipe": "recipe", "criterion": "filter", "intent": "compile", "examples": "calibrate", "case_file": "calibrate",
             "from_batch": "calibrate", "images": "ask_image", "items_file": "batch", "items": "batch", "template": "batch"}


ENV_NAMES = (
    "OPENJEV_MCP_MODEL", "OPENJEV_MCP_TIMEOUT_MS", "OPENJEV_MCP_MAX_INFLIGHT",
    "OPENJEV_MCP_MAX_INFLIGHT_BATCH", "OPENJEV_MCP_RETRIES", "OPENJEV_MCP_LOG",
    "OPENJEV_MCP_LOG_STATES", "OPENJEV_MCP_TOOLSETS", "OPENJEV_MCP_BAND", "OPENJEV_MCP_ROOTS",
    "OPENJEV_MCP_TRANSPORT", "OPENJEV_MCP_HOST", "OPENJEV_MCP_PORT", "OPENJEV_MCP_TOKEN",
    "OPENJEV_MCP_ALLOWED_HOSTS", "OPENJEV_MCP_ALLOWED_ORIGINS", "OPENJEV_MCP_MAX_BODY_BYTES",
    "OPENJEV_MCP_DEBUG",
    "OPENJEV_MCP_RECIPES", "OPENJEV_MCP_ROUTING", "OPENJEV_MCP_FETCH", "OPENJEV_MCP_TASKS",
    "OPENJEV_MCP_AUDIT_DIR", "OPENJEV_MCP_CHAT_MODEL",
)



def read(name):
    return (SKILLS / name / "SKILL.md").read_text()


def frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "no leading frontmatter block"
    fields = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        fields[k.strip()] = v.strip()
    return fields


def tool_refs(text):
    refs = set(re.findall(r"mcp__openjev__(\w+)", text))
    refs |= {w for w in re.findall(r"`(\w+)`", text) if w in ALL_TOOLS}
    return refs


def recipe_refs(text):
    refs = set(re.findall(r"`recipe` `(\w+)`", text)) | set(re.findall(r'"recipe": "(\w+)"', text))
    refs |= {w for w in re.findall(r"`(\w+)`", text) if w in RECIPE_IDS}
    return refs


def json_blocks(text):
    for b in re.findall(r"```json\n(.*?)\n```", text, re.S):
        yield json.loads(b)


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_frontmatter(name):
    assert (SKILLS / name / "SKILL.md").is_file()
    fm = frontmatter(read(name))
    assert fm["name"] == name
    assert len(fm["description"]) > 80


def test_pack_is_exactly_the_eleven_skills():
    assert sorted(p.name for p in SKILLS.iterdir() if p.is_dir()) == sorted(SKILL_NAMES)


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_only_real_tools_and_recipes(name):
    text = read(name)
    assert set(re.findall(r"mcp__openjev__(\w+)", text)) <= set(ALL_TOOLS)
    assert tool_refs(text) <= set(ALL_TOOLS)
    for rid in recipe_refs(text):
        assert load_builtin(rid).id == rid
    for rid in re.findall(r"`recipe` `(\w+)`", text):
        assert rid in RECIPE_IDS, rid


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_states_precedence_rule(name):
    assert "recedence" in read(name)


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_json_blocks_validate_against_the_tool_schemas(name):
    tools_for(Config())   # registers the input schemas
    for block in json_blocks(read(name)):
        if not isinstance(block, dict):
            continue
        tool = next((TOOL_KEYS[k] for k in block if k in TOOL_KEYS), None)
        if tool is None or ("questions" in block and tool == "batch" and "items" not in block and "items_file" not in block and "template" not in block):
            continue
        if tool == "batch" and "items" in block and not any(k in block for k in ("questions", "template", "output_path")):
            continue   # a row example, not a call
        assert validate.validate_args(tool, block) is None, (name, tool, block)


def test_every_tool_is_taught_somewhere():
    seen = set().union(*(tool_refs(read(n)) for n in SKILL_NAMES))
    assert seen >= set(ALL_TOOLS) - {"generate"}, set(ALL_TOOLS) - {"generate"} - seen


def test_decisions_table_and_failure_bullets():
    text = read("openjev-decisions")
    assert "Precedence rule" in text and "advisory" in text
    for tool in ("filter", "batch", "batch_results", "ask_image", "compile", "calibrate"):
        assert re.search(rf"^\|.*`{tool}`", text, re.M), tool
    for recipe in ("command_gate", "act_or_ask", "injection_screen", "done_gate", "moderation", "skill_selection",
                   "model_routing", "typed_call", "claim_check", "judge_assert", "memory_decide"):
        assert re.search(rf"^\|.*`{recipe}`", text, re.M), recipe
    assert text.count("`batch`") >= 5
    for bullet in ("`batch` `OJ_INVALID_INPUT` about a cursor", "`stopped_reason: backpressure`", "`resume: true`"):
        assert bullet in text, bullet
    assert "label: null" in text and "abstained: true" in text and "`top`" in text


def test_question_authoring_uses_compile():
    text = read("openjev-question-authoring")
    assert "`compile`" in text and "openjev://guide/authoring" in text and "`calibrate`" in text


def test_data_records_is_complete():
    text = read("openjev-data-records")
    for s in ("Dry run first", "next_cursor", "resume: true", "retry_errors", "view: \"review\"", "compare_to", "include_state: false",
              "rubric_score", "memory_decide", "verify_fields", "ojui-batch"):
        assert s in text, s


def test_skills_hold_no_stale_spec_refs():
    for n in SKILL_NAMES:
        assert not re.search(r"\bspec \d|\(spec ", read(n)), n


def test_readme_contents():
    text = README.read_text()
    for s in ("8100", "/mcp", "mise run mcp", "openjev-hook pretooluse", "--transport stdio",
              "claude mcp add --transport http openjev http://127.0.0.1:8100/mcp",
              "2026-07-28", "2025-11-25", "2025-06-18",
              "--profile", "--unattended", "--timeout-ms", "--task", "--defer-allow",
              "openjev-hook stop", "openjev-hook userprompt", "openjev-hook posttooluse", "--roster", "--max-blocks", "--screen",
              "next_cursor", "resume", "absolute", "OPENJEV_MCP_TASKS", "images"):
        assert s in text, s
    for env in ENV_NAMES:
        assert f"`{env}`" in text, env


def test_readme_has_no_docker():
    assert "docker" not in README.read_text().lower()
