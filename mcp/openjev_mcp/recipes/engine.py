"""Recipe engine (spec 2.16, 2.18): load and run recipes. The hook imports this module, so it stays free of mcp,
jsonschema, httpx and the HTTP client (typing only).

Format keys beyond spec 2.18 (all validated at load; a bad recipe never loads). Recipe files stay data.
Recipe level:
- question_profiles / signal_map / policy_profiles / fallback: as in phase 1.
- decision_format {decision: "inject:{skill}"}: after combine the decision text has {signal} replaced by that
  signal's value (a missing signal leaves the plain decision). Severity and combine never see the formatted text.
- outputs {name: "<combine>" | {"values": [...], "combine": "<combine>"}}: secondary combines over the final
  signals (a str form picks from `decisions`); returned in RecipeOutcome.outputs and merged into signals.
- routing true: the recipe is a routing recipe; with config.routing false it returns fallback.interactive with
  reason "routing off", degraded false, and sends no read.
- an input property marked "x-openjev-images": true (array of data URLs) is sent as `images` on every read;
  `think`/`sequential` with images is refused (E022, at load for step options, at run for read_options).
Steps:
- read: `questions` (inline, overrides question_profiles for that step), `signal_map` (step-local qid -> signal),
  `options` (steps/samples/think/sequential), `when`, `per`. Signals accumulate across steps, so a later `when`
  (grey(block)) and combine see them; the decision is the most severe over the reads.
- read with `reread: "<read step id>"`: same state and questions as that step with its own `options`; replaces the
  earlier signals of the same names and the best decision so far (the re-read stands in for the grey read).
- read_per_item: `items: "<input array>"`, one read per element (state/questions rendered with the inputs plus the
  element's fields, `item`, `index`, `.`), per-item decision by `combine`, `aggregate: worst | any:<decision> |
  none` (default worst; any:d is d if some item decided d, else the least severe item decision; none decides
  `combine` over no signals). The outcome carries items [{id, decision, signals}].
- read_twice_swapped: `swap: [input_a, input_b]`; read, then read again with the two inputs exchanged; combine sees
  <signal> and <signal>_swapped.
- question criteria {"$from": "<input array>", "key": "<field>", "text": "<field>", "extra": {key: text}}:
  expanded from the input before the read (keys unique, <= 64 chars, no newlines; a choice needs >= 2 options).
- questions {"$each": "<input array>", "id": "<field>", "type": "score", "instructions": "<field>", "criteria":
  "<field>"}: one question per element (rubric dimensions); their signals are dynamic and meant for weighted_sum.
- compute: `split` (phase 1) and `compute: "weighted_sum"` with `dimensions: "<input array of {name, weight?,
  floor?, levels?}>"`, `into: "<signal>"`, `margin` (0.3): signal <into> = sum(w * score / max) / sum(w) and
  <into>_floors = 1 when every score >= floor - margin else 0. Applied in code, never sent. A recipe with a
  weighted_sum decides once, after the last step. No text is ever evaluated as code.
Run: run_recipe(..., policy_overrides, fail_mode): overrides are checked against the recipe's policy names;
fail_mode "open" turns any error into the least severe decision (decisions[0]), "closed" or None keeps the
recipe fallback; both return degraded true. build_requests(recipe, inputs) lists the bodies of the steps that
have no `when`, with no I/O."""
from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from typing import TYPE_CHECKING, Any

from .. import wire
from ..derive import Band, derive_answers
from ..errors import ToolError, invalid_input
from .expr import Combine, ExprError, Node, Signal, decide, evaluate, explain, parse_combine, parse_expr
from .rules import PatternError, Rule, RuleVerdict, apply_rules, compile_rule
from .shell import SplitResult, split_command
from .template import TemplateError, check_template, render

if TYPE_CHECKING:
    from ..config import Config
    from ..http import HttpResult, OpenJevClient

ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
KINDS = ("deterministic", "read", "read_per_item", "read_twice_swapped", "compute")
SUPPORTED = KINDS
READS = ("read", "read_per_item", "read_twice_swapped")
COMPUTES = ("split", "weighted_sum")
OPTION_KEYS = ("steps", "samples", "think", "sequential")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
HOLE_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
MAX_ITEMS = 100
MAX_KEY = 64
FAIL_MODES = ("open", "closed")
QUESTION_TYPES = ("noul", "choice", "score")
JSON_TYPES = {"string": str, "boolean": bool, "integer": int, "number": (int, float), "array": list,
              "object": dict}


class RecipeError(ValueError): ...


@dataclass(frozen=True)
class Recipe:
    id: str
    title: str
    usage_type: str
    description: str
    input_schema: dict
    steps: tuple[dict, ...]
    rules: tuple[Rule, ...]
    policy: dict[str, float]
    policy_profiles: dict[str, dict[str, float]]
    question_profiles: dict[str, dict]
    signal_map: dict[str, dict[str, str]]
    decisions: tuple[str, ...]
    combine: Combine | None
    when: dict[str, Node]
    fail_mode: str
    fallback: dict[str, str]
    test_file: str | None
    limitations: tuple[str, ...]
    raw_inputs: frozenset[str] = frozenset()
    image_inputs: frozenset[str] = frozenset()
    decision_format: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, Combine] = field(default_factory=dict)
    routing: bool = False


@dataclass
class RecipeOutcome:
    recipe: str
    decision: str
    reason: str
    signals: dict
    thresholds_used: dict
    degraded: bool
    error: ToolError | None
    requests: int
    answers: dict
    meta: dict
    rule: dict | None
    items: list | None = None
    outputs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"recipe": self.recipe, "decision": self.decision, "reason": self.reason,
                               "signals": self.signals, "thresholds_used": self.thresholds_used,
                               "degraded": self.degraded, "requests": self.requests}
        if self.error is not None:
            out["error"] = self.error.to_dict()
        if self.answers:
            out["answers"] = self.answers
        if self.rule is not None:
            out["rule"] = self.rule
        if self.items is not None:
            out["items"] = self.items
        if self.outputs:
            out["outputs"] = self.outputs
        out["meta"] = self.meta
        return out


def _bad(rid: str, msg: str) -> RecipeError:
    return RecipeError(f"recipe {rid}: {msg}")


def _need(doc: Mapping[str, Any], rid: str, key: str, kind: type | tuple) -> Any:
    if key not in doc:
        raise _bad(rid, f"missing required field {key!r}")
    if not isinstance(doc[key], kind) or isinstance(doc[key], bool) and kind is not bool:
        raise _bad(rid, f"{key!r} has the wrong type")
    return doc[key]


def _key_ok(k: Any) -> bool:
    return isinstance(k, str) and 0 < len(k) <= MAX_KEY and k == k.strip() and "\n" not in k


def _array_input(rid: str, where: str, name: Any, props: dict) -> str:
    spec = props.get(name) if isinstance(name, str) else None
    if not isinstance(spec, dict) or spec.get("type") != "array":
        raise _bad(rid, f"{where}: {name!r} must name an array property of input_schema")
    return name


def _from_spec(rid: str, where: str, crit: dict, props: dict) -> None:
    if not set(crit) <= {"$from", "key", "text", "extra"} or not all(isinstance(crit.get(k), str) for k in ("key", "text")):
        raise _bad(rid, f"{where}.criteria: $from needs string key and text (and optional extra)")
    _array_input(rid, f"{where}.criteria.$from", crit["$from"], props)
    extra = crit.get("extra", {})
    if not isinstance(extra, dict) or not all(_key_ok(k) and isinstance(v, str) and v.strip() for k, v in extra.items()):
        raise _bad(rid, f"{where}.criteria.extra must map valid option keys to texts")


def _each_spec(rid: str, where: str, qs: dict, props: dict) -> None:
    if (not set(qs) <= {"$each", "id", "type", "instructions", "criteria"} or qs.get("type") != "score"
            or not all(isinstance(qs.get(k), str) for k in ("id", "instructions", "criteria"))):
        raise _bad(rid, f"{where}: $each needs string id, instructions, criteria fields and type score")
    _array_input(rid, f"{where}.$each", qs["$each"], props)


def _questions(rid: str, where: str, qs: Any, props: Mapping[str, Any] | None = None) -> dict:
    props = props or {}
    if isinstance(qs, dict) and "$each" in qs:
        _each_spec(rid, where, qs, props)
        return qs
    if not isinstance(qs, dict) or not qs:
        raise _bad(rid, f"{where} must be a non-empty object of questions")
    for qid, q in qs.items():
        if not isinstance(q, dict) or q.get("type") not in QUESTION_TYPES or not isinstance(q.get("instructions"), str):
            raise _bad(rid, f"{where}.{qid} needs type (noul|choice|score) and instructions")
        if q["type"] != "noul" and not q.get("criteria"):
            raise _bad(rid, f"{where}.{qid} needs criteria")
        crit = q.get("criteria")
        if isinstance(crit, dict) and "$from" in crit:
            if q["type"] != "choice":
                raise _bad(rid, f"{where}.{qid}: criteria $from is for choice questions")
            _from_spec(rid, f"{where}.{qid}", crit, props)
        _check_strings(q, frozenset())
    return qs


def _check_strings(obj: Any, raw_allowed: frozenset[str]) -> None:
    if isinstance(obj, str):
        if "{{" in obj:
            check_template(obj, raw_allowed=raw_allowed)
    elif isinstance(obj, dict):
        for v in obj.values():
            _check_strings(v, raw_allowed)
    elif isinstance(obj, list):
        for v in obj:
            _check_strings(v, raw_allowed)


def _step_options(rid: str, st: dict, images: bool) -> None:
    opts = st.get("options", {})
    if not isinstance(opts, dict):
        raise _bad(rid, f"step {st['id']!r}: options must be an object")
    if not set(opts) <= set(OPTION_KEYS):
        raise _bad(rid, f"step {st['id']!r}: unknown options {sorted(set(opts) - set(OPTION_KEYS))}")
    if images and (opts.get("think") or opts.get("sequential")):
        raise _bad(rid, f"step {st['id']!r}: E022 think/sequential need a text state; the recipe takes images")


def _load_step(rid: str, st: dict, i: int, props: dict, raw_inputs: frozenset[str], images: bool, decset: frozenset,
               earlier: dict[str, dict]) -> None:
    kind = st["kind"]
    if kind == "compute":
        which = st.get("compute")
        if which is None:
            if st["id"] != "split":
                raise _bad(rid, f"step {st['id']!r}: the only compute step without 'compute' is 'split'")
        elif which == "weighted_sum":
            _array_input(rid, f"step {st['id']!r}.dimensions", st.get("dimensions"), props)
            if not isinstance(st.get("into"), str) or not NAME_RE.match(st["into"]):
                raise _bad(rid, f"step {st['id']!r}: into must be a signal name")
            m = st.get("margin", 0.3)
            if not isinstance(m, (int, float)) or isinstance(m, bool) or m < 0:
                raise _bad(rid, f"step {st['id']!r}: margin must be a number >= 0")
        else:
            raise _bad(rid, f"step {st['id']!r}: unknown compute {which!r} (known: {', '.join(COMPUTES)})")
        return
    if kind not in READS:
        return
    _step_options(rid, st, images)
    if "signal_map" in st and not (isinstance(st["signal_map"], dict) and all(isinstance(v, str) for v in st["signal_map"].values())):
        raise _bad(rid, f"step {st['id']!r}: signal_map must map question ids to signal names")
    if "reread" in st:
        ref = earlier.get(st["reread"]) if isinstance(st["reread"], str) else None
        if kind != "read" or ref is None or ref["kind"] != "read" or "reread" in ref or ref.get("per"):
            raise _bad(rid, f"step {st['id']!r}: reread must name an earlier plain read step (not per, not a reread)")
        if not st.get("options") or "questions" in st or "state_template" in st:
            raise _bad(rid, f"step {st['id']!r}: a reread takes options and nothing else but when")
        return
    if not isinstance(st.get("state_template"), str):
        raise _bad(rid, f"step {st['id']!r}: state_template is required")
    check_template(st["state_template"], raw_allowed=raw_inputs)
    if "questions" in st:
        _questions(rid, f"steps.{st['id']}.questions", st["questions"], props)
    if kind == "read_per_item":
        _array_input(rid, f"step {st['id']!r}.items", st.get("items"), props)
        agg = st.get("aggregate", "worst")
        if not (agg in ("worst", "none") or isinstance(agg, str) and agg.startswith("any:") and agg[4:] in decset):
            raise _bad(rid, f"step {st['id']!r}: aggregate must be worst, none or any:<decision>")
        if st.get("per"):
            raise _bad(rid, f"step {st['id']!r}: per is not allowed on read_per_item")
    if kind == "read_twice_swapped":
        sw = st.get("swap")
        if (not isinstance(sw, list) or len(sw) != 2 or sw[0] == sw[1] or not all(isinstance(x, str) and x in props for x in sw)):
            raise _bad(rid, f"step {st['id']!r}: swap must name two different input properties")


def _step_names(st: dict, base: Mapping[str, str], qs: Mapping[str, Any]) -> set[str]:
    if "$each" in qs:
        return set()
    names = {st.get("signal_map", base).get(q, q) for q in qs}
    if st["kind"] == "read_twice_swapped":
        names |= {n + "_swapped" for n in names}
    return names


def _load(doc: Mapping[str, Any], rid: str) -> Recipe:
    if not ID_RE.match(rid):
        raise _bad(rid, "id must match ^[a-z][a-z0-9_]{2,40}$")
    for key in ("title", "usage_type", "description"):
        _need(doc, rid, key, str)
    schema = _need(doc, rid, "input_schema", dict)
    steps_in = _need(doc, rid, "steps", list)
    policy = _need(doc, rid, "policy", dict)
    decisions = _need(doc, rid, "decisions", list)
    if not decisions or not all(isinstance(d, str) for d in decisions) or len(set(decisions)) != len(decisions):
        raise _bad(rid, "decisions must be a non-empty list of unique strings")
    decset = frozenset(decisions)
    props = schema.get("properties", {})
    if not isinstance(props, dict) or not isinstance(schema.get("required", []), list):
        raise _bad(rid, "input_schema needs properties (object) and required (list)")
    raw_inputs = frozenset(k for k, v in props.items() if isinstance(v, dict) and v.get("x-openjev-raw") is True)
    image_inputs = frozenset(k for k, v in props.items() if isinstance(v, dict) and v.get("x-openjev-images") is True)
    for k in image_inputs:
        if props[k].get("type") != "array":
            raise _bad(rid, f"input {k!r}: x-openjev-images needs type array (data URLs)")
    routing = doc.get("routing", False)
    if not isinstance(routing, bool):
        raise _bad(rid, "routing must be a boolean")

    profiles_policy = doc.get("policy_profiles", {})
    if not isinstance(profiles_policy, dict) or not all(isinstance(v, dict) for v in profiles_policy.values()):
        raise _bad(rid, "policy_profiles must map a profile to a policy object")
    policy_names = frozenset(policy) | frozenset(k for v in profiles_policy.values() for k in v)

    rules: list[Rule] = []
    steps: list[dict] = []
    whens: dict[str, str] = {}
    seen: dict[str, dict] = {}
    for i, st in enumerate(steps_in):
        if not isinstance(st, dict) or not isinstance(st.get("id"), str) or st.get("kind") not in KINDS:
            raise _bad(rid, f"steps[{i}] needs id and kind ({'|'.join(KINDS)})")
        if st["id"] in seen:
            raise _bad(rid, f"duplicate step id {st['id']!r}")
        if st["kind"] == "deterministic":
            specs = st.get("rules")
            if not isinstance(specs, list) or not specs:
                raise _bad(rid, f"step {st['id']!r}: rules must be a non-empty list")
            for spec in specs:
                if not isinstance(spec, dict):
                    raise _bad(rid, f"step {st['id']!r}: a rule must be an object")
                rules.append(compile_rule(spec, index=len(rules), decisions=decset))
        _load_step(rid, st, i, props, raw_inputs, bool(image_inputs), decset, seen)
        seen[st["id"]] = st
        if "when" in st:
            if not isinstance(st["when"], str):
                raise _bad(rid, f"step {st['id']!r}: when must be a string")
            whens[st["id"]] = st["when"]
        steps.append(dict(st))

    reads = [s for s in steps if s["kind"] in READS and "reread" not in s]
    qprofiles = doc.get("question_profiles", {})
    if not isinstance(qprofiles, dict):
        raise _bad(rid, "question_profiles must be an object")
    for name, qs in qprofiles.items():
        _questions(rid, f"question_profiles.{name}", qs, props)
    if not qprofiles:
        inline = next((s["questions"] for s in reads if "questions" in s and "$each" not in s["questions"]), None)
        if inline is not None:
            qprofiles = {"strict": inline}
    if any("questions" not in s for s in reads) and not qprofiles:
        raise _bad(rid, "a read step needs questions or question_profiles")
    smap = doc.get("signal_map", {})
    if not isinstance(smap, dict):
        raise _bad(rid, "signal_map must be an object")
    for name, m in smap.items():
        if name not in qprofiles or not isinstance(m, dict) or not all(isinstance(v, str) for v in m.values()):
            raise _bad(rid, f"signal_map.{name} must map question ids of profile {name!r} to signal names")
        for qid in m:
            if qid not in qprofiles[name]:
                raise _bad(rid, f"signal_map.{name}: no question {qid!r}")
    names = {smap.get(name, {}).get(qid, qid) for name, qs in qprofiles.items() if "$each" not in qs for qid in qs}
    for s in reads:
        if "questions" in s:
            names |= _step_names(s, {}, s["questions"])
        elif s["kind"] == "read_twice_swapped":
            names |= {n + "_swapped" for n in names}
    for s in steps:
        if s.get("compute") == "weighted_sum":
            names |= {s["into"], s["into"] + "_floors"}
    signals = frozenset(names)

    combine = None
    if doc.get("combine") is not None:
        combine = parse_combine(_need(doc, rid, "combine", str), signals=signals, policy=policy_names,
                                decisions=decset)
    elif reads:
        raise _bad(rid, "a recipe with a read step needs a combine")
    when = {sid: parse_expr(text, signals=signals, policy=policy_names, decisions=decset)
            for sid, text in whens.items()}

    dfmt = doc.get("decision_format", {})
    if not isinstance(dfmt, dict) or not all(k in decset and isinstance(v, str) for k, v in dfmt.items()):
        raise _bad(rid, "decision_format must map listed decisions to template strings")
    for k, v in dfmt.items():
        for hole in HOLE_RE.findall(v):
            if hole not in signals:
                raise _bad(rid, f"decision_format.{k}: {{{hole}}} is not a signal")
    outs_in = doc.get("outputs", {})
    if not isinstance(outs_in, dict):
        raise _bad(rid, "outputs must be an object")
    outputs: dict[str, Combine] = {}
    for oname, spec in outs_in.items():
        if not NAME_RE.match(oname) or oname in signals:
            raise _bad(rid, f"outputs.{oname}: needs a fresh lowercase name")
        values = decset
        if isinstance(spec, dict):
            vals = spec.get("values")
            if (not isinstance(vals, list) or not vals or not all(isinstance(v, str) for v in vals)
                    or not isinstance(spec.get("combine"), str)):
                raise _bad(rid, f"outputs.{oname}: needs values (list of strings) and combine")
            values, spec = frozenset(vals), spec["combine"]
        if not isinstance(spec, str):
            raise _bad(rid, f"outputs.{oname} must be a combine string or {{values, combine}}")
        outputs[oname] = parse_combine(spec, signals=signals, policy=policy_names, decisions=values)

    fail_mode = doc.get("fail_mode", "closed")
    if fail_mode not in FAIL_MODES:
        raise _bad(rid, "fail_mode must be open or closed")
    fallback = doc.get("fallback")
    if fallback is None and isinstance(doc.get("fallback_decision"), str):
        fallback = {"interactive": doc["fallback_decision"], "unattended": doc["fallback_decision"]}
    if (not isinstance(fallback, dict) or set(fallback) != {"interactive", "unattended"}
            or not all(v in decset for v in fallback.values())):
        raise _bad(rid, "fallback must give an interactive and an unattended decision from decisions")
    limitations = doc.get("limitations", [])
    if not isinstance(limitations, list) or not all(isinstance(x, str) for x in limitations):
        raise _bad(rid, "limitations must be a list of strings")
    test_file = doc.get("test_file")
    if test_file is not None and not isinstance(test_file, str):
        raise _bad(rid, "test_file must be a string")
    return Recipe(
        id=rid, title=doc["title"], usage_type=doc["usage_type"], description=doc["description"],
        input_schema=schema, steps=tuple(steps), rules=tuple(rules), policy=dict(policy),
        policy_profiles={k: dict(v) for k, v in profiles_policy.items()},
        question_profiles={k: dict(v) for k, v in qprofiles.items()},
        signal_map={k: dict(v) for k, v in smap.items()}, decisions=tuple(decisions), combine=combine,
        when=when, fail_mode=fail_mode, fallback=dict(fallback), test_file=test_file,
        limitations=tuple(limitations), raw_inputs=raw_inputs, image_inputs=image_inputs,
        decision_format=dict(dfmt), outputs=outputs, routing=routing)


def load_recipe(doc: Mapping[str, Any]) -> Recipe:
    rid = doc.get("id") if isinstance(doc, Mapping) else None
    name = rid if isinstance(rid, str) else "<unnamed>"
    if not isinstance(doc, Mapping):
        raise _bad(name, "a recipe must be a JSON object")
    try:
        return _load(doc, name)
    except (PatternError, TemplateError, ExprError) as e:
        raise RecipeError(f"recipe {name}: {e}") from e


def load_builtin(recipe_id: str) -> Recipe:
    if not ID_RE.match(recipe_id):
        raise RecipeError(f"recipe {recipe_id}: not a recipe id")
    path = resources.files("openjev_mcp.recipes").joinpath("builtin", f"{recipe_id}.json")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RecipeError(f"recipe {recipe_id}: no such built-in recipe") from None
    except ValueError as e:
        raise RecipeError(f"recipe {recipe_id}: invalid JSON ({e})") from e
    return load_recipe(doc)


def _check_inputs(recipe: Recipe, inputs: Mapping[str, Any]) -> None:
    if not isinstance(inputs, Mapping):
        raise _bad(recipe.id, "inputs must be an object")
    schema = recipe.input_schema
    required = schema.get("required", [])
    for key in required:
        if inputs.get(key) is None:
            raise _bad(recipe.id, f"inputs.{key} is required")
    for key, spec in schema.get("properties", {}).items():
        value = inputs.get(key)
        if value is None or not isinstance(spec, dict):
            continue
        kind = spec.get("type")
        if kind in JSON_TYPES:
            ok = isinstance(value, JSON_TYPES[kind]) and (kind == "boolean" or not isinstance(value, bool))
            if not ok:
                raise _bad(recipe.id, f"inputs.{key} must be of type {kind}")
        if key in recipe.image_inputs and not all(isinstance(u, str) and u.startswith("data:") for u in value):
            raise _bad(recipe.id, f"inputs.{key} must be an array of data URLs")
        if "enum" in spec and value not in spec["enum"]:
            raise _bad(recipe.id, f"inputs.{key} must be one of {', '.join(map(str, spec['enum']))}")


@dataclass
class _Run:
    results: list[tuple[Any, dict, int]] = field(default_factory=list)   # (HttpResult, body, timeout_ms)


def _meta(run: _Run, config: Config) -> dict:
    meta: dict[str, Any] = {"requests": len(run.results), "warnings": []}
    if not run.results:
        return meta
    timing: dict[str, float] = {}
    meta["request_ids"] = [r.request_id for r, _, _ in run.results if r.request_id]
    meta["body_hashes"] = [r.body_hash for r, _, _ in run.results if r.body_hash]
    meta["latency_ms"] = sum(r.latency_ms for r, _, _ in run.results)
    usage = [(r.data.get("usage") or {}) if isinstance(r.data, dict) else {} for r, _, _ in run.results]
    meta["input_tokens"] = sum(int(u.get("input_tokens") or 0) for u in usage)
    meta["output_tokens"] = sum(int(u.get("output_tokens") or 0) for u in usage)
    last = run.results[-1][0].data
    meta["model"] = last.get("model") if isinstance(last, dict) and isinstance(last.get("model"), str) else config.model
    for r, _, _ in run.results:
        for k, v in (r.server_timing or {}).items():
            timing[k] = timing.get(k, 0.0) + v
        if r.retried:
            meta["warnings"].append(f"retried after {r.retried['code']} ({r.retried['attempts']} attempts)")
    if timing:
        meta["server_timing"] = timing
        if "total_ms" in timing:
            meta["server_ms"] = timing["total_ms"]
    meta["timeout_ms_used"] = run.results[-1][2]
    return meta


def _sig_from(names: Mapping[str, str], answers: Mapping[str, dict], suffix: str = "") -> tuple[dict[str, Signal], dict]:
    objs: dict[str, Signal] = {}
    flat: dict[str, Any] = {}
    for qid, a in answers.items():
        name = names.get(qid, qid) + suffix
        if a["type"] == "noul":
            objs[name] = Signal(a["p"], grey=a["band"] == "grey")
            flat[name] = a["p"]
        elif a["type"] == "score":
            objs[name] = Signal(a["score"])
            flat[name] = a["score"]
        else:
            objs[name] = Signal(a["choice"], p=a["p_top"], abstained=a["abstained"])
            flat[name] = a["choice"]
            flat[f"{name}_p"] = a["p_top"]
    return objs, flat


def _signals(recipe: Recipe, profile: str, answers: Mapping[str, dict]) -> tuple[dict[str, Signal], dict]:
    return _sig_from(recipe.signal_map.get(profile, {}), answers)


def _fill(obj: Any, inputs: Mapping[str, Any]) -> Any:
    if isinstance(obj, str):
        return render(obj, inputs, raw_allowed=frozenset()) if "{{" in obj else obj
    if isinstance(obj, dict):
        return {k: _fill(v, inputs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, inputs) for v in obj]
    return obj


def _rows(inputs: Mapping[str, Any], name: str) -> list[dict]:
    rows = inputs.get(name)
    if rows is None:
        return []
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise invalid_input(f"inputs.{name}", f"inputs.{name} must be an array of objects")
    return rows


def _text(row: dict, field_name: str, where: str) -> str:
    v = row.get(field_name)
    if not isinstance(v, str) or not v.strip():
        raise invalid_input(where, f"{where}: field {field_name!r} must be a non-empty string")
    return v


def _expand(questions: dict, inputs: Mapping[str, Any]) -> dict:
    """Expand $each questions and $from choice criteria from the inputs (after template fill)."""
    if "$each" in questions:
        name = questions["$each"]
        out: dict[str, Any] = {}
        for i, row in enumerate(_rows(inputs, name)):
            where = f"inputs.{name}[{i}]"
            qid = _text(row, questions["id"], where)
            crit = row.get(questions["criteria"])
            if qid in out or not isinstance(crit, list) or len(crit) < 2 or not all(isinstance(c, str) and c for c in crit):
                raise invalid_input(where, f"{where}: needs a unique {questions['id']!r} and >= 2 text {questions['criteria']!r}")
            out[qid] = {"type": "score", "instructions": _text(row, questions["instructions"], where), "criteria": crit}
        if not out:
            raise invalid_input(f"inputs.{name}", f"inputs.{name} must not be empty")
        return out
    out = {}
    for qid, q in questions.items():
        crit = q.get("criteria")
        if isinstance(crit, dict) and "$from" in crit:
            name, options = crit["$from"], {}
            for i, row in enumerate(_rows(inputs, name)):
                where = f"inputs.{name}[{i}]"
                key = row.get(crit["key"])
                if not _key_ok(key) or key in options:
                    raise invalid_input(where, f"{where}: {crit['key']!r} must be a unique option key (1-{MAX_KEY} chars)")
                options[key] = _text(row, crit["text"], where)
            for k, v in crit.get("extra", {}).items():
                if k in options:
                    raise invalid_input(f"inputs.{name}", f"inputs.{name}: option key {k!r} clashes with extra")
                options[k] = v
            if len(options) < 2:
                raise invalid_input(f"inputs.{name}", f"{qid}: a choice needs at least 2 options, got {len(options)}")
            q = {**q, "criteria": options}
        out[qid] = q
    return out


def _images(recipe: Recipe, inputs: Mapping[str, Any]) -> list[str]:
    return [u for k in sorted(recipe.image_inputs) for u in (inputs.get(k) or [])]


def _refuse_images(images: list, options: Mapping[str, Any] | None) -> None:
    if images and options and (options.get("think") or options.get("sequential")):
        raise invalid_input("options", "E022: think and sequential need a text state; the server returns 400 with images",
                            "drop think/sequential or the images")


def _reason(recipe: Recipe, decision: str, env: dict) -> str:
    assert recipe.combine is not None
    hits = [explain(c.cond, **env) for c in recipe.combine.clauses
            if c.cond is not None and c.decision == decision and evaluate(c.cond, **env)]
    return "; ".join(hits) if hits else f"{decision} otherwise"


def _format(recipe: Recipe, decision: str, flat: Mapping[str, Any]) -> str:
    tpl = recipe.decision_format.get(decision)
    if tpl is None:
        return decision
    holes = HOLE_RE.findall(tpl)
    if any(flat.get(h) is None for h in holes):      # deviation: a missing signal keeps the plain decision
        return decision
    return HOLE_RE.sub(lambda m: str(flat[m.group(1)]), tpl)


def _resolve(recipe: Recipe, inputs: Mapping[str, Any], profile: str | None, overrides: Mapping[str, Any] | None) \
        -> tuple[str, dict]:
    prof = profile or inputs.get("profile") or "strict"
    if recipe.question_profiles and prof not in recipe.question_profiles:
        raise _bad(recipe.id, f"unknown profile {prof!r} (known: {', '.join(recipe.question_profiles)})")
    policy = {**recipe.policy, **recipe.policy_profiles.get(prof, {})}
    if overrides:
        known = set(recipe.policy) | {k for v in recipe.policy_profiles.values() for k in v}
        bad = [k for k, v in overrides.items() if k not in known or isinstance(v, bool) or not isinstance(v, (int, float, str))]
        if bad:
            raise _bad(recipe.id, f"unknown or invalid policy overrides {bad} (known: {', '.join(sorted(known))})")
        policy.update(overrides)
    return prof, policy


def _step(recipe: Recipe, step: dict) -> dict:
    """The step a read uses its state and questions from (a reread borrows them)."""
    if "reread" in step:
        base = next(s for s in recipe.steps if s["id"] == step["reread"])
        return {**base, "id": step["id"], "options": step["options"], "reread": step["reread"]}
    return step


def _questions_for(recipe: Recipe, step: dict, prof: str, ctx: Mapping[str, Any]) -> tuple[dict, dict]:
    """(questions, qid -> signal name) for a read step rendered with ctx."""
    if "questions" in step:
        raw, names = step["questions"], step.get("signal_map", {})
    else:
        raw, names = recipe.question_profiles[prof], recipe.signal_map.get(prof, {})
    return _expand(raw if "$each" in raw else _fill(raw, ctx), ctx), names


def _body(recipe: Recipe, step: dict, prof: str, ctx: Mapping[str, Any], model: str, options: Mapping[str, Any],
          images: list) -> tuple[dict, dict, dict]:
    questions, names = _questions_for(recipe, step, prof, ctx)
    state = render(step["state_template"], ctx, raw_allowed=recipe.raw_inputs)
    return wire.build_body(model, state, questions, options, images), questions, names


def _item_ctx(inputs: Mapping[str, Any], i: int, el: Any) -> dict:
    return {**inputs, **(el if isinstance(el, Mapping) else {}), "item": el, ".": el, "index": i}


def _items(inputs: Mapping[str, Any], step: dict) -> list:
    items = inputs.get(step["items"])
    if items is None:
        return []
    if len(items) > MAX_ITEMS:
        raise invalid_input(f"inputs.{step['items']}", f"inputs.{step['items']} has {len(items)} items; the limit is {MAX_ITEMS}")
    return items


def _swapped(inputs: Mapping[str, Any], step: dict) -> dict:
    a, b = step["swap"]
    return {**inputs, a: inputs.get(b), b: inputs.get(a)}


def _prep(recipe: Recipe, inputs: Mapping[str, Any]) -> tuple[str, SplitResult | None, RuleVerdict]:
    verdict = RuleVerdict(None, None, "no rules")
    split: SplitResult | None = None
    split_step = next((s for s in recipe.steps if s["kind"] == "compute" and s.get("compute") is None), None)
    field_name = (split_step or {}).get("input", "command")
    if recipe.rules or split_step:
        command = inputs.get(field_name)
        split = split_command(command if isinstance(command, str) else "")
        if recipe.rules:
            verdict = apply_rules(recipe.rules, command if isinstance(command, str) else "", split)
    return field_name, split, verdict


def build_requests(recipe: Recipe, inputs: Mapping[str, Any], *, profile: str | None = None,
                   policy: Mapping[str, Any] | None = None, config: Config | None = None) -> list[dict]:
    """The exact bodies a run would send before any conditional step (no `when`, no reread). No I/O.
    deviation: optional `config` supplies the model and routing flag (defaults to load_config({}))."""
    _check_inputs(recipe, inputs)
    prof, _ = _resolve(recipe, inputs, profile, policy)
    if config is None:
        from ..config import load_config
        config = load_config({})
    if recipe.routing and not config.routing:
        return []
    field_name, split, verdict = _prep(recipe, inputs)
    if verdict.decision is not None:
        return []
    images = _images(recipe, inputs)
    bodies: list[dict] = []
    for raw in (s for s in recipe.steps if s["kind"] in READS and "reread" not in s and s["id"] not in recipe.when):
        opts = raw.get("options", {})
        _refuse_images(images, opts)
        if raw["kind"] == "read_per_item":
            for i, el in enumerate(_items(inputs, raw)):
                bodies.append(_body(recipe, raw, prof, _item_ctx(inputs, i, el), config.model, opts, images)[0])
            continue
        ctxs = [inputs]
        if raw["kind"] == "read_twice_swapped":
            ctxs.append(_swapped(inputs, raw))
        elif raw.get("per") and split is not None and len(split.parts) > 1:
            ctxs += [{**inputs, field_name: p} for p in split.parts]
        for ctx in ctxs:
            bodies.append(_body(recipe, raw, prof, ctx, config.model, opts, images)[0])
    return bodies


async def run_recipe(recipe: Recipe, inputs: Mapping[str, Any], *, client: OpenJevClient, config: Config,
                     profile: str | None = None, timeout_ms: int | None = None,
                     deadline_ms: int | None = None, read_options: Mapping[str, Any] | None = None,
                     policy_overrides: Mapping[str, Any] | None = None,
                     fail_mode: str | None = None) -> RecipeOutcome:
    _check_inputs(recipe, inputs)
    if fail_mode not in (None, *FAIL_MODES):
        raise _bad(recipe.id, "fail_mode must be open or closed")
    prof, policy = _resolve(recipe, inputs, profile, policy_overrides)
    _refuse_images(_images(recipe, inputs), read_options)
    unattended = bool(inputs.get("unattended", False))
    if recipe.routing and not config.routing:
        decision = recipe.fallback["interactive"]
        return RecipeOutcome(recipe.id, decision, "routing off", {}, policy, False, None, 0, {},
                             {"requests": 0, "warnings": []}, None)
    run = _Run()
    started = time.monotonic()
    try:
        return await _execute(recipe, inputs, client, config, prof, policy, run, started, timeout_ms,
                              deadline_ms, read_options)
    except ToolError as err:
        error = err
    except Exception as exc:
        error = ToolError("OJ_INTERNAL", f"internal error in openjev-mcp: {type(exc).__name__}")
    decision = recipe.decisions[0] if fail_mode == "open" else recipe.fallback["unattended" if unattended else "interactive"]
    return RecipeOutcome(recipe.id, decision, f"{error.code}: {error.message}; fallback {decision}", {}, policy,
                         True, error, len(run.results), {}, _meta(run, config), None)


def _weighted_sum(step: dict, inputs: Mapping[str, Any], env: Mapping[str, Signal]) -> dict[str, Signal]:
    total = wsum = 0.0
    floors = True
    margin = step.get("margin", 0.3)
    for i, d in enumerate(_rows(inputs, step["dimensions"])):
        where = f"inputs.{step['dimensions']}[{i}]"
        name, sig = _text(d, "name", where), env.get(d.get("name"))
        if sig is None or not isinstance(sig.value, (int, float)):
            raise invalid_input(where, f"{where}: no score was read for dimension {name!r}")
        w = d.get("weight", 1)
        levels = d.get("levels")
        top = (len(levels) - 1) if isinstance(levels, list) and len(levels) > 1 else 3
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w < 0:
            raise invalid_input(where, f"{where}: weight must be a number >= 0")
        total += w * sig.value / top
        wsum += w
        fl = d.get("floor")
        if isinstance(fl, (int, float)) and not isinstance(fl, bool) and sig.value < fl - margin:
            floors = False
    if wsum <= 0:
        raise invalid_input(f"inputs.{step['dimensions']}", "weights must sum to more than 0")
    return {step["into"]: Signal(total / wsum), step["into"] + "_floors": Signal(1.0 if floors else 0.0)}


class _Reader:
    """One OpenJev read: body, request, derived answers, accounting."""

    def __init__(self, recipe, inputs, client, config, prof, policy, run, started, timeout_ms, deadline_ms, read_options):
        self.recipe, self.inputs, self.client, self.config, self.prof = recipe, inputs, client, config, prof
        self.policy, self.run, self.started = policy, run, started
        self.timeout_ms, self.deadline_ms, self.read_options = timeout_ms, deadline_ms, read_options
        self.images = _images(recipe, inputs)

    async def read(self, step: dict, ctx: Mapping[str, Any], suffix: str = "") -> tuple[dict, dict, dict]:
        recipe, config = self.recipe, self.config
        options = {**step.get("options", {}), **(self.read_options or {})}
        _refuse_images(self.images, options)
        body, questions, names = _body(recipe, step, self.prof, ctx, config.model, options, self.images)
        used = self.timeout_ms or config.timeout_ms
        remaining = None
        if self.deadline_ms is not None:
            remaining = int(self.deadline_ms - (time.monotonic() - self.started) * 1000)
            if remaining <= 0:
                raise ToolError("OJ_TIMEOUT", f"deadline of {self.deadline_ms} ms reached", retryable=False)
        res = await self.client.systemone(body, timeout_ms=used, think=int(options.get("think") or 0),
                                          has_images=bool(self.images), deadline_ms=remaining)
        self.run.results.append((res, body, used))
        raw = res.data.get("answers") if isinstance(res.data, dict) else None
        if not isinstance(raw, dict):
            raise ToolError("OJ_PROTOCOL", "response lacks answers", request_id=res.request_id)
        band = Band(yes_at=self.policy.get("yes_at", config.band_yes_at), no_at=self.policy.get("no_at", config.band_no_at))
        answers = derive_answers(raw, questions, band)
        objs, flat = _sig_from(names, answers, suffix)
        return answers, objs, flat


async def _execute(recipe, inputs, client, config, prof, policy, run, started, timeout_ms, deadline_ms,
                   read_options) -> RecipeOutcome:
    field_name, split, verdict = _prep(recipe, inputs)
    rule_info = None
    if verdict.decision is not None:
        rule = recipe.rules[verdict.rule]
        rule_info = {"index": rule.index, "decision": rule.decision, "pattern": rule.source}
        return RecipeOutcome(recipe.id, verdict.decision, f"{verdict.reason}: {rule.source}", {}, policy, False,
                             None, 0, {}, _meta(run, config), rule_info)

    reader = _Reader(recipe, inputs, client, config, prof, policy, run, started, timeout_ms, deadline_ms, read_options)
    best: tuple[int, str, str, dict, dict] | None = None     # severity, decision, reason, signals, answers
    sev = {d: i for i, d in enumerate(recipe.decisions)}    # decisions are listed least to most severe
    top = len(recipe.decisions) - 1
    deferred = any(s.get("compute") == "weighted_sum" for s in recipe.steps)
    env_sig: dict[str, Signal] = {}
    flat_all: dict[str, Any] = {}
    answers_all: dict[str, dict] = {}
    items_out: list | None = None

    def env_of(sigs: Mapping[str, Signal]) -> dict:
        return {"signals": sigs, "policy": policy, "rule_decision": None}

    for step in recipe.steps:
        if step["kind"] not in READS and step.get("compute") != "weighted_sum":
            continue
        cond = recipe.when.get(step["id"])
        if cond is not None and not evaluate(cond, **env_of(env_sig)):
            continue
        if step.get("compute") == "weighted_sum":
            env_sig = {**env_sig, **_weighted_sum(step, inputs, env_sig)}
            flat_all.update({k: v.value for k, v in env_sig.items() if k in (step["into"], step["into"] + "_floors")})
            continue
        step = _step(recipe, step)
        if step["kind"] == "read_per_item":
            rows, agg_decisions = [], []
            for i, el in enumerate(_items(inputs, step)):
                answers, objs, flat = await reader.read(step, _item_ctx(inputs, i, el))
                env = env_of({**env_sig, **objs})
                decision, _ = decide(recipe.combine, **env)
                rid = el.get("id") if isinstance(el, Mapping) and el.get("id") is not None else i
                rows.append({"id": str(rid), "decision": _format(recipe, decision, flat), "signals": flat})
                agg_decisions.append(decision)
            items_out = rows
            mode = step.get("aggregate", "worst")
            if mode == "none" or not agg_decisions:
                agg, _ = decide(recipe.combine, **env_of(env_sig))
            elif mode.startswith("any:") and mode[4:] in agg_decisions:
                agg = mode[4:]
            elif mode.startswith("any:"):
                agg = min(agg_decisions, key=sev.__getitem__)
            else:
                agg = max(agg_decisions, key=sev.__getitem__)
            counts = ", ".join(f"{agg_decisions.count(d)} {d}" for d in recipe.decisions if d in agg_decisions)
            cand = (sev[agg], agg, f"{len(rows)} items ({counts or 'none'}); aggregate {mode}", {}, {})
            if best is None or cand[0] > best[0]:
                best = cand
            if cand[0] == top:
                break
            continue
        if step["kind"] == "read_twice_swapped":
            a1, o1, f1 = await reader.read(step, inputs)
            a2, o2, f2 = await reader.read(step, _swapped(inputs, step), "_swapped")
            env_sig = {**env_sig, **o1, **o2}
            flat_all.update({**f1, **f2})
            answers_all.update({**a1, **{q + "_swapped": a for q, a in a2.items()}})
            if not deferred:
                env = env_of(env_sig)
                decision, _ = decide(recipe.combine, **env)
                cand = (sev[decision], decision, _reason(recipe, decision, env), dict(flat_all), dict(answers_all))
                if best is None or cand[0] > best[0]:
                    best = cand
            if best is not None and best[0] == top:
                break
            continue
        units = [(inputs.get(field_name), "")]
        if step.get("per") and split is not None and len(split.parts) > 1:
            units += [(p, f"part {i + 1}: ") for i, p in enumerate(split.parts)]
        for unit, label in units:
            answers, objs, flat = await reader.read(step, {**inputs, field_name: unit})
            env_sig = {**env_sig, **objs}
            flat_all.update(flat)
            answers_all.update(answers)
            if deferred:
                continue
            env = env_of(env_sig)
            decision, _ = decide(recipe.combine, **env)
            rank = sev[decision]
            cand = (rank, decision, label + _reason(recipe, decision, env), dict(flat_all), dict(answers_all))
            if best is None or rank > best[0] or "reread" in step:
                best = cand
            if rank == top:
                break
        if best is not None and best[0] == top:
            break
    if deferred:
        env = env_of(env_sig)
        decision, _ = decide(recipe.combine, **env)
        best = (sev[decision], decision, _reason(recipe, decision, env), dict(flat_all), dict(answers_all))
    if best is None:
        env = env_of({})
        decision, _ = decide(recipe.combine, **env)
        return RecipeOutcome(recipe.id, decision, _reason(recipe, decision, env), {}, policy, False, None, 0, {},
                             _meta(run, config), None)
    outputs = {name: decide(c, **env_of(env_sig))[0] for name, c in recipe.outputs.items()}
    signals = {**best[3], **outputs}
    return RecipeOutcome(recipe.id, _format(recipe, best[1], best[3]), best[2], signals, policy, False, None,
                         len(run.results), best[4], _meta(run, config), None, items_out, outputs)
