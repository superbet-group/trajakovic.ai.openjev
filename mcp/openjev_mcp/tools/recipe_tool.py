"""recipe: run a named recipe from the library (spec 2.16, 2.18). Recipes are data; this tool validates the inputs,
builds the request(s), reads, applies the policy table in code and returns a decision."""
from __future__ import annotations

import dataclasses
import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from openjev_mcp import schemas
from openjev_mcp.config import Config
from openjev_mcp.errors import invalid_input
from openjev_mcp.recipes import engine
from openjev_mcp.recipes.expr import evaluate
from openjev_mcp.recipes.registry import load_all
from openjev_mcp.tools import ToolContext, ToolSpec

DRY_RUN_DECISION = "dry_run"   # deviation: spec 2.16: dry_run returns decision "dry_run" (the output schema requires a decision)

INPUT = {
    "type": "object", "additionalProperties": False,
    "required": ["recipe", "inputs"],
    "properties": {
        "recipe": {"type": "string", "description": "a recipe id; the enum lists the ones this server loaded"},
        "inputs": {"type": "object", "description": "validated against the recipe's input_schema (resource openjev://recipes/{id})"},
        "profile": {"type": "string", "description": "recipe-defined: strict | default | lenient"},
        "policy": {"type": "object", "description": "threshold overrides, keys from the recipe's policy table"},
        "fail_mode": {"enum": ["open", "closed"], "description": "default from the recipe"},
        "dry_run": {"type": "boolean", "default": False, "description": "return the built request(s) without calling"},
        "options": {"$ref": "#/$defs/ReadOptions"},
    },
}
OUTPUT = {
    "type": "object", "required": ["recipe", "decision", "signals", "degraded"],
    "properties": {
        "recipe": {"type": "string"},
        "decision": {"type": "string", "description": "recipe-specific enum, e.g. allow|ask|deny"},
        "reason": {"type": "string"}, "signals": {"type": "object"}, "thresholds_used": {"type": "object"},
        "degraded": {"type": "boolean", "description": "true when the decision is the fail_mode fallback after an error"},
        "error": {"$ref": "#/$defs/ToolError"}, "requests": {"type": "integer"},
        "built_requests": {"type": "array", "items": {"type": "object"}},
        "answers": {"type": "object"}, "meta": {"$ref": "#/$defs/Meta"},
    },
}


def _schema_error(recipe: engine.Recipe, message: str, path: str) -> Any:
    return invalid_input(path, message, json.dumps(recipe.input_schema, separators=(",", ":")))


def _validate_inputs(recipe: engine.Recipe, inputs: Any) -> None:
    err = best_match(Draft202012Validator(recipe.input_schema).iter_errors(inputs))
    if err is not None:
        path = ".".join(["inputs", *map(str, err.absolute_path)])
        raise _schema_error(recipe, f"{path}: {err.message}", path)


def _dry_recipe(rec: engine.Recipe, inputs: Any) -> engine.Recipe:
    """deviation: P03 build_requests skips every read that has a `when` (command_gate's only read does), so a dry_run
    would list nothing. Reads whose guard is already true before any read (rule guards, no signals yet) are
    unguarded for the dry run; reads guarded on signals stay out, as they depend on an earlier answer."""
    free = {sid for sid, node in rec.when.items()
            if evaluate(node, signals={}, policy=rec.policy, rule_decision=None)}
    return dataclasses.replace(rec, when={k: v for k, v in rec.when.items() if k not in free}) if free else rec


async def recipe(ctx: ToolContext, args: dict) -> dict:
    reg = load_all(ctx.config)
    try:
        rec = reg.get(args["recipe"])
    except engine.RecipeError as e:
        raise invalid_input("recipe", str(e), f"one of: {', '.join(reg.ids())}") from None
    inputs = args["inputs"]
    _validate_inputs(rec, inputs)
    options = dict(args.get("options") or {})
    config = ctx.config
    if options.get("model"):
        config = dataclasses.replace(config, model=options["model"])
    timeout_ms = options.get("timeout_ms")
    read_options = {k: v for k, v in options.items() if k in engine.OPTION_KEYS} or None
    kw = {"profile": args.get("profile"), "policy": args.get("policy")}
    try:
        if args.get("dry_run"):
            built = engine.build_requests(_dry_recipe(rec, inputs), inputs, config=config, **kw)
            return {"recipe": rec.id, "decision": DRY_RUN_DECISION, "reason": "dry_run: nothing was sent",
                    "signals": {}, "degraded": False, "requests": 0, "built_requests": built,
                    "meta": {"model": config.model, "request_ids": [], "requests": 0, "latency_ms": 0,
                             "input_tokens": 0, "output_tokens": 0, "warnings": []}}
        out = await engine.run_recipe(rec, inputs, client=ctx.client, config=config, profile=kw["profile"],
                                      timeout_ms=timeout_ms, read_options=read_options,
                                      policy_overrides=kw["policy"], fail_mode=args.get("fail_mode"))
    except engine.RecipeError as e:
        raise _schema_error(rec, str(e), "inputs") from None
    return out.to_dict()


def register(config: Config) -> ToolSpec:
    ids = load_all(config).ids()
    schema = json.loads(json.dumps(INPUT))
    schema["properties"]["recipe"] = {"enum": ids, "description": INPUT["properties"]["recipe"]["description"]}
    schemas.register_tool_schemas("recipe", schema, OUTPUT)
    annotations = {"title": "Run a recipe", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}
    return ToolSpec(
        "recipe", "Run a recipe",
        "Run a named recipe (gate a command, screen text, done-gate, route, moderate, ...): builds the request(s) "
        "from typed inputs, reads, applies the recipe's policy table in code and returns a decision. dry_run "
        "returns the built requests without calling. Inputs follow openjev://recipes/{id}.",
        schemas.INPUT_SCHEMAS["recipe"], schemas.OUTPUT_SCHEMAS["recipe"], annotations, recipe)
