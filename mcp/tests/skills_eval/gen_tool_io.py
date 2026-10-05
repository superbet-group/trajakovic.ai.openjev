#!/usr/bin/env python3
"""Render references/tool-io.md of the openjev-data-prep skill from the live schemas and captured examples.

    python mcp/tests/skills_eval/gen_tool_io.py            # write the file
    python mcp/tests/skills_eval/gen_tool_io.py --check    # exit 1 if the file differs from a fresh render

Deterministic: tool order is openjev_mcp.TOOL_NAMES, schemas come from tools_for(config), examples from
data/tool_examples.json. Run it from the repo root with the repo venv.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "mcp"))

from openjev_mcp import TOOL_NAMES  # noqa: E402
from openjev_mcp.config import load_config  # noqa: E402
from openjev_mcp.tools.dispatch import tools_for  # noqa: E402
from openjev_mcp.validate import validate_args, validate_output  # noqa: E402

EXAMPLES = HERE / "data" / "tool_examples.json"
TARGET = REPO / "plugins/openjev-skills/skills/openjev-data-prep/references/tool-io.md"

NEXT = {
    "status": "Any tool. If `healthy` is false, stop and tell the user; do not retry in a loop.",
    "ask": "`lint` to pre-check a question set, `calibrate` to fit thresholds, or `batch` to run the same questions over many states.",
    "yes_no": "`calibrate` (via a one-question `questions` map) to check the thresholds, or `filter` for many items.",
    "classify": "`batch` with the same question as a `choice` question for many states; `calibrate` to measure accuracy.",
    "score": "`calibrate` (score labels are level indexes) to check the ladder is monotonic.",
    "filter": "Feed `kept` ids (and `grey` ids if you want to review them) into `batch` with `only_ids`, or into your own next step.",
    "batch": "`batch_results` on `output_path` (review queue, stats, exports); call `batch` again with `cursor` = `next_cursor` until it is null; `calibrate` with `from_batch` once you have labels.",
    "ask_image": "`batch` with `images` to reuse the same screenshots across many rows; otherwise stop, this is a leaf call.",
    "lint": "`ask`, `batch` or `recipe` once `valid` is true; use `fixed_request` when autofix changed something.",
    "compile": "Fill the `<SLOT:...>` placeholders, `lint` the draft, then `calibrate` on 10-20 labelled examples, then `ask` or `batch`.",
    "calibrate": "Apply `suggested_band` as `thresholds`; re-run with `compare_to` after any wording change; `batch_results` is not needed.",
    "recipe": "Act on `decision`; for many inputs run the recipe once per input; `recipe` with `dry_run: true` first to see the built request.",
    "status_": "",
    "generate": "Nothing in the pipeline reads it; use it only to draft prose after the decisions are made.",
    "batch_results": "Re-run `batch` with `only_ids` for rows to retry, `calibrate` with `from_batch` for accuracy, or export csv/markdown.",
}

ERRORS = {
    "status": ["`healthy: false` or `OJ_UNREACHABLE`: OpenJev on its base_url is down. Report it; the user restarts it."],
    "ask": ["`OJ_INVALID_INPUT` with `path` such as `questions.q1.criteria`: the hint carries the schema branch that was expected.",
            "Lint errors always block (`E0xx`); warnings (`W1xx`-`W3xx`) are returned in `lint.warnings`."],
    "yes_no": ["`OJ_INVALID_INPUT` `claim`: shorter than 3 characters.", "W102: only `false_means` given; pass `true_means` too."],
    "classify": ["`OJ_INVALID_INPUT` `labels`: at least 1 label; descriptions must be non-empty.", "W201/W202: no escape option or label equal to its description."],
    "score": ["`OJ_INVALID_INPUT` `levels`: 2-10 non-empty strings, lowest first.", "W302: a level holds an escape clause ('no ... stated')."],
    "filter": ["`OJ_INVALID_INPUT` `items.N.id`: ids match `^[A-Za-z0-9_.:-]{1,32}$`.", "The criterion must contain `{id}`."],
    "batch": ["`OJ_INVALID_INPUT` 'path outside the allowed roots': `items_file.path` and `output_path` must be absolute and inside the server's roots.",
              "Give exactly one source: `items`, `items_file` or `template`; `resume: false` needs a new `output_path`.",
              "E022: `options.think` / `sequential` with `images`."],
    "ask_image": ["`OJ_BAD_IMAGE`: unreadable or unsupported image.", "`OJ_TOO_LARGE`: image over 5 MiB.", "`https` URLs are refused unless the server runs with `OPENJEV_MCP_FETCH=on`."],
    "lint": ["Give either `request` or `questions`, not neither (`OJ_INVALID_INPUT`)."],
    "compile": ["`OJ_INVALID_INPUT` `intent`: at least 5 characters.", "`recipe.not_a_decision: true`: the intent is not a typed decision; rephrase it as a question about a state."],
    "calibrate": ["Give exactly one of: `questions` + `examples`, `recipe` + `examples`, `case_file`, `from_batch`.", "Labels are true/false for noul, the option key for choice, the level index for score."],
    "recipe": ["`OJ_INVALID_INPUT` `inputs`: validated against the recipe's `input_schema`; read `openjev://recipes/{id}` first."],
    "generate": ["`OJ_INVALID_INPUT` `messages.N.role`: role is required; `max_tokens` is clamped to 8192."],
    "batch_results": ["`OJ_NOT_FOUND`: `path` does not exist yet (run `batch` first).", "Paths must be inside the allowed roots; exports are created new."],
}

# first sentence of the description is the purpose; a few tools need a pointer on top
EXTRA = {"generate": "TEXT ONLY: drafts prose on the chat model. It cannot decide anything, so never use it for a yes/no, a label or a score."}


def tname(s: dict) -> str:
    if "enum" in s:
        return "enum(" + "|".join(map(str, s["enum"])) + ")"
    if "const" in s:
        return f"const {json.dumps(s['const'])}"
    for key in ("oneOf", "anyOf"):
        if key in s and all(isinstance(b.get("properties", {}).get("type", {}).get("const"), str) for b in s[key]):
            return " or ".join(b["properties"]["type"]["const"] for b in s[key]) + " object"
        if key in s:
            return " or ".join(tname(b) for b in s[key])
    t = s.get("type")
    if isinstance(t, list):
        return "|".join(t)
    if t == "array":
        return f"array<{tname(s['items'])}>" if isinstance(s.get("items"), dict) and s["items"] else "array"
    return t or "any"


def notes(s: dict) -> str:
    out = []
    if s.get("description"):
        out.append(re.sub(r"\s+", " ", s["description"]).strip())
    for k in ("default", "minimum", "maximum", "minItems", "maxItems", "minLength", "pattern"):
        if k in s:
            out.append(f"{k} {json.dumps(s[k])}")
    text = "; ".join(out)
    return (text[:177] + "...") if len(text) > 180 else text


def clean(text: str) -> str:
    """Strip repository-internal wording from schema descriptions (skills must not leak repo details)."""
    text = re.sub(r"run_cases\.py format", "case-file format (JSON cases)", text)
    return re.sub(r"(?<=roots) \(\d+\.\d+\)", "", text)


def cell(text: str) -> str:
    text = clean(text)
    return text.replace("|", "\\|").replace("\n", " ")


def children(s: dict) -> list[tuple[str, dict, bool]]:
    """One nesting level: properties of an object, items of an array of objects, the value of a map."""
    node = s
    if s.get("type") == "array" and isinstance(s.get("items"), dict):
        node = s["items"]
    if isinstance(node.get("properties"), dict) and node["properties"]:
        req = set(node.get("required", []))
        return [(k, v, k in req) for k, v in node["properties"].items()]
    ap = node.get("additionalProperties")
    if isinstance(ap, dict) and ap:
        return [("<key>", ap, False)]
    return []


def table(schema: dict) -> list[str]:
    rows = ["| Field | Type | Required | Notes |", "|---|---|---|---|"]
    req = set(schema.get("required", []))
    for name, sub in schema.get("properties", {}).items():
        rows.append(f"| `{name}` | {cell(tname(sub))} | {'yes' if name in req else ''} | {cell(notes(sub))} |")
        if name == "meta":
            continue
        for cname, csub, creq in children(sub):
            rows.append(f"| `{name}.{cname}` | {cell(tname(csub))} | {'yes' if creq else ''} | {cell(notes(csub))} |")
    return rows


def purpose(desc: str) -> str:
    return clean(re.sub(r"\s+", " ", desc).strip())


def dump(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def render(examples: dict | None = None) -> str:
    ex = examples if examples is not None else json.loads(EXAMPLES.read_text())
    specs = {t.name: t for t in tools_for(load_config({}))}
    L = ["# OpenJev tool input and output reference", "",
         "Generated from the server's JSON Schemas and captured live calls. "
         "Do not edit by hand. Every tool also returns an MCP `structuredContent` object equal to the output shown. "
         "Paths shown as `/abs/path/to/...` stand for absolute paths inside the server's allowed roots.", "", "## Contents", ""]
    for n in TOOL_NAMES:
        L.append(f"- [`mcp__openjev__{n}`](#{n})")
    L.append("")
    for n in TOOL_NAMES:
        s = specs[n]
        L += [f"## {n}", "", f"Tool name: `mcp__openjev__{n}`. " + purpose(s.description), ""]
        if n in EXTRA:
            L += [f"**{EXTRA[n]}**", ""]
        L += ["### Input", ""] + table(s.input_schema) + ["", "### Output", ""] + table(s.output_schema) + [""]
        for i, e in enumerate(ex.get(n, []), 1):
            L += [f"### Example {i}: {e['label']}", "", "Call:", "", f"<!-- openjev-call: {n} -->", "```json", dump(e["call"]), "```", "",
                  "Result (trimmed to 2 array items; `meta` cut to model, requests, latency_ms):", "", f"<!-- openjev-result: {n} -->", "```json", dump(e["result"]), "```", ""]
        L += ["### Common errors", ""] + [f"- {x}" for x in ERRORS[n]]
        for e in ex.get("_errors", {}).get(n, []):
            L += ["", f"Real error from a live call ({e['note']}):", "", "<!-- openjev-example -->", "```json", dump({"call": e["call"], "error": e["error"]}), "```"]
        L += ["", f"### Usually next", "", NEXT[n], ""]
    return "\n".join(L).rstrip() + "\n"


def check_examples(ex: dict) -> list[str]:
    bad = []
    for n in TOOL_NAMES:
        for i, e in enumerate(ex.get(n, []), 1):
            err = validate_args(n, e["call"])
            if err is not None:
                bad.append(f"{n}#{i} call: {err.path}: {err.message}")
            bad += [f"{n}#{i} result: {m}" for m in validate_output(n, e["result"])]
    for n in TOOL_NAMES:
        if not ex.get(n):
            bad.append(f"{n}: no example")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit 1 when the rendered file differs")
    ap.add_argument("--out", default=str(TARGET))
    a = ap.parse_args()
    ex = json.loads(EXAMPLES.read_text())
    bad = check_examples(ex)
    if bad:
        print("example validation failed:\n  " + "\n  ".join(bad), file=sys.stderr)
        return 2
    text = render(ex)
    out = Path(a.out)
    if a.check:
        if not out.exists() or out.read_text() != text:
            print(f"{out} is out of date; run gen_tool_io.py", file=sys.stderr)
            return 1
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"wrote {out} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
