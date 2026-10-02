"""The tool registry and the tools/call pipeline (arch D.13, E)."""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from openjev_mcp import CORE_TOOL_NAMES
from openjev_mcp.audit import REQUEST_META
from openjev_mcp.config import Config, load_config
from openjev_mcp.envelope import error_result, success_result
from openjev_mcp.errors import ToolError
from openjev_mcp.schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS
from openjev_mcp.tools import ToolContext, ToolSpec, UnknownTool
from openjev_mcp.tools import (ask_image, batch_results, batch_tool, calibrate, compile as compile_tool,
                                filter as filter_tool, generate, read, recipe_tool)
from openjev_mcp.tools.lint_tool import lint
from openjev_mcp.tools.status import status
from openjev_mcp.validate import _validator, validate_args

log = logging.getLogger("openjev_mcp")


def _spec(name: str, title: str, description: str, handler, *, idempotent: bool = True, prepare=None) -> ToolSpec:
    annotations = {"title": title, "readOnlyHint": True, "idempotentHint": idempotent, "openWorldHint": False}
    return ToolSpec(name, title, description, INPUT_SCHEMAS[name], OUTPUT_SCHEMAS[name], annotations, handler, prepare)


def _register(name, title, description, handler, **kw):
    return lambda config: _spec(name, title, description, handler, **kw)


PHASE_TOOLS: list[Callable[[Config], ToolSpec]] = [
    _register("ask", "Ask OpenJev",
              "Send one state and a full question set (1-256 noul, choice and score questions) and get validated "
              "answers with derived fields. Use it for several questions about one state, or for think, samples, "
              "steps and sequential.", read.ask, idempotent=False),
    _register("yes_no", "Yes/no claim",
              "Decide one literal claim about a state: yes, no or uncertain, with the probability. "
              "A first read in the grey band is re-read once with samples 4.", read.yes_no),
    _register("classify", "Classify",
              "Pick one label from a closed set; an escape option is added so the answer can abstain. "
              "multi_label asks one yes/no per label instead.", read.classify, prepare=read.classify_prepare),
    _register("score", "Score on a scale",
              "Place one input on an ordered scale of 2-10 described levels, lowest first. Returns the expected "
              "score, the argmax level label, the spread and a bimodality flag.", read.score,
              prepare=read.score_prepare),
    filter_tool.register,
    batch_tool.register,
    ask_image.register,
    _register("lint", "Lint a request",
              "Validate and lint a /v1/systemone request, autofix known mistakes and estimate its cost. "
              "Makes no network request.", lint),
    compile_tool.register,
    calibrate.register,
    recipe_tool.register,
    _register("status", "OpenJev status",
              "Report OpenJev health, models, backend, limits and capabilities. probe also runs one small read "
              "to measure latency.", status),
    generate.register,
    batch_results.register,
]

_SPECS: dict[int, tuple[ToolSpec, ...]] = {}   # keyed by len(PHASE_TOOLS) so a list extended later rebuilds


def specs(config: Config) -> tuple[ToolSpec, ...]:
    """Every tool spec, built once per process (identical lists for every connection)."""
    key = len(PHASE_TOOLS)
    if key not in _SPECS:
        _SPECS[key] = tuple(f(config) for f in PHASE_TOOLS)
    return _SPECS[key]


def tools_for(config: Config | str) -> tuple[ToolSpec, ...]:
    # deviation: P02 brief: tools_for also accepts a bare toolsets string and TOOLS stays importable, so the
    # P14-owned test_mcp_dispatch.py collects until P14 migrates it
    toolsets = config if isinstance(config, str) else config.toolsets
    all_specs = specs(load_config({}) if isinstance(config, str) else config)
    for t in all_specs:
        _ensure_schemas(t)
    if toolsets == "core":
        return tuple(t for t in all_specs if t.name in CORE_TOOL_NAMES)
    return all_specs


_ORIG = {t.name: t for t in specs(load_config({}))}
_BY_NAME = dict(_ORIG)   # deviation: legacy name map; replacing an entry (tests) overrides the registry lookup


def _ensure_schemas(spec: ToolSpec) -> None:
    """Tests that exercise one tool module pop its schemas from the registries; put them back for the pipeline."""
    if spec.name not in INPUT_SCHEMAS or spec.name not in OUTPUT_SCHEMAS:   # deviation: P14: heal a popped registry entry
        INPUT_SCHEMAS[spec.name], OUTPUT_SCHEMAS[spec.name] = spec.input_schema, spec.output_schema
        _validator.cache_clear()


async def call_tool(ctx: ToolContext, name: str, arguments: Mapping[str, Any] | None) -> dict:
    spec = next((t for t in specs(ctx.config) if t.name == name), None)
    if (over := _BY_NAME.get(name)) is not None and over is not _ORIG.get(name):   # deviation: legacy override hook, see tools_for
        spec = over
    if spec is None:
        raise UnknownTool(name)
    _ensure_schemas(spec)
    ctx.links = []
    token = REQUEST_META.set(ctx.request_meta)
    try:
        args: Any = {} if arguments is None else arguments
        ctx.warnings = []
        if spec.prepare is not None and isinstance(args, Mapping):
            args, findings = spec.prepare(dict(args))
            ctx.warnings = list(findings)
        err = validate_args(name, args)
        if err is not None:
            return error_result(err)
        return success_result(await spec.handler(ctx, dict(args)), ctx.links)
    except ToolError as err:
        return error_result(err)
    except Exception as exc:
        log.exception("tool %s failed", name)
        return error_result(ToolError("OJ_INTERNAL", f"internal error in openjev-mcp: {type(exc).__name__}"))
    finally:
        REQUEST_META.reset(token)


def __getattr__(name: str):
    if name == "TOOLS":   # deviation: legacy module constant, see tools_for
        return specs(load_config({}))
    raise AttributeError(name)
