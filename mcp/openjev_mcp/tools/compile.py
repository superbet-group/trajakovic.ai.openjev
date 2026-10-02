"""compile: turn a prose intent into a linted draft request from the recipe library (spec 2.14).
OpenJev reads route and type; deterministic templates instantiate; free-text descriptions stay slots for the human.
Never calls chat (MLX chat drops newlines and returns empty or broken JSON, bugs 12.3/12.4)."""
from __future__ import annotations

import json
import re
from typing import Any

from openjev_mcp import schemas
from openjev_mcp.config import Config
from openjev_mcp.derive import Band, is_escape
from openjev_mcp.errors import invalid_input
from openjev_mcp.lint import lint_request
from openjev_mcp.prompts import PromptArg, PromptSpec
from openjev_mcp.recipes.engine import Recipe
from openjev_mcp.recipes.registry import load_all
from openjev_mcp.tools import ToolContext, ToolSpec
from openjev_mcp.tools.calibrate import calibrate
from openjev_mcp.tools.read import ask, run_read

NONE = "none"
QTYPES = ("noul", "choice", "score", "not_typed")
PROFILES = ("strict", "default")
GATES = frozenset({"command_gate", "done_gate", "injection_screen", "moderation", "rag_gate"})
SLOT = "<SLOT:{}>"
ROUTE_INSTRUCTIONS = ("Which recipe from the library best matches what the human wants to decide? "
                      "Pick none if the intent is not a decision over given inputs.")
NONE_DESCRIPTION = ("Not a typed decision: the intent asks to write or generate text, compute or count, search for "
                    "new information, or answer an open question")
QTYPE_CRITERIA = {
    "noul": "a yes/no answer: the sub-decision asks whether something is true, present, allowed or needed",
    "choice": "one option from a closed list of named alternatives the human can enumerate in advance (teams, "
              "categories, candidate ids, actions)",
    "score": "a degree on an ordered scale: how much, how severe, how good, how complete",
    "not_typed": "the answer must be written, computed or copied: free text such as a summary or reply, a count, a "
                 "sum, a date, or an exact value quoted from the input",
}
REDIRECT = {"not_typed": "extract candidates in code, then select_extraction (recipe) over them; or compute it in code"}

INPUT: dict = {
    "type": "object", "additionalProperties": False, "required": ["intent"],
    "properties": {
        "intent": {"type": "string", "minLength": 5},
        "sub_decisions": {"type": "array", "items": {"type": "string"},
                          "description": "optional split of the intent, one decision each ('whether it is urgent', 'which team')"},
        "labels": {"type": "object", "description": "known options per sub-decision: {sub_decision: {label: description} | [label]}"},
        "sample_inputs": {"type": "array", "maxItems": 10, "items": {"$ref": "#/$defs/State"}},
        "labelled_examples": {"type": "array", "items": {"type": "object", "required": ["state", "label"],
                                                         "properties": {"state": {}, "label": {"type": "object"}}}},
        "recipe": {"type": "string", "description": "skip routing and use this recipe id"},
    },
}
OUTPUT: dict = {
    "type": "object", "required": ["recipe", "draft_request", "slots", "human_questions", "lint"],
    "properties": {
        "recipe": {"type": "object", "properties": {
            "id": {"type": "string"}, "p": {"type": "number"}, "runner_up": {}, "not_a_decision": {"type": "boolean"},
            "variants": {"type": "array", "items": {"type": "string"}}}},
        "sub_decisions": {"type": "array", "items": {"type": "object", "properties": {
            "text": {}, "qtype": {"enum": list(QTYPES)}, "p": {}, "question_id": {}, "note": {},
            "redirect": {"type": "string", "description": "for not_typed: 'extract candidates in code, then select_extraction' / 'compute in code'"}}}},
        "draft_request": {"type": "object", "description": "a /v1/systemone body, placeholders as <SLOT:name>"},
        "slots": {"type": "array", "items": {"type": "object", "properties": {
            "name": {}, "path": {}, "why": {}, "example": {}}}},
        "human_questions": {"type": "array", "items": {"type": "string"}},
        "lint": {"type": "object"},
        "probe": {"type": "array", "items": {"type": "object", "properties": {"input": {}, "answers": {}}}},
        "calibration": {"type": "object", "description": "calibrate output when labelled_examples were given"},
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
}


# ---------------------------------------------------------------- typing

_NOUL = re.compile(r"^(whether|if|is|does|do|are|can|should|has|have|will)\b")
_CHOICE = re.compile(r"^(which|what kind|what type|pick)\b")
_SCORE = re.compile(r"\b(how much|how severe|how good|rate)\b")
_NOT_TYPED = re.compile(r"\b(how many|count|sum|amount|date|summary|write|extract)\b")


def pre_rule(text: str) -> str | None:
    """Deterministic typing of a sub-decision (spec 2.14 step 2); None when a read must decide."""
    t = text.strip().lower()
    if _NOUL.match(t):
        return "noul"
    if _CHOICE.match(t):
        return "choice"
    if _SCORE.search(t):
        return "score"
    if _NOT_TYPED.search(t):
        return "not_typed"
    return None


def _band(ctx: ToolContext) -> Band:
    return Band(ctx.config.band_yes_at, ctx.config.band_no_at)


async def _choose(ctx: ToolContext, state: str, qid: str, instructions: str, criteria: dict) -> dict:
    out = await run_read(ctx, state=state, questions={qid: {"type": "choice", "instructions": instructions,
                                                            "criteria": criteria}},
                         options={"samples": 1}, band=_band(ctx), lint_mode="off")
    return out.answers[qid]


async def type_sub_decision(ctx: ToolContext, text: str, *, rules: bool = True) -> tuple[str, float | None, str]:
    """-> (qtype, p, source). One sub-decision per request: packed meta-questions interfere (9/12 vs 12/12)."""
    if rules and (qtype := pre_rule(text)):
        return qtype, None, "pre-rule"
    ans = await _choose(ctx, f"A human wants this decided about an input: {text}", "qtype",
                        "What kind of answer does this sub-decision need?", QTYPE_CRITERIA)
    return ans["choice"], ans["p_top"], "read"


# ---------------------------------------------------------------- routing

def primaries(ctx_config: Config) -> dict[str, Recipe]:
    reg = load_all(ctx_config)
    return {rid: r for rid, r in reg.recipes.items() if not reg.docs[rid].get("variant_of")}


def variants_of(config: Config, rid: str) -> list[str]:
    reg = load_all(config)
    return [v for v, d in reg.docs.items() if d.get("variant_of") == rid]


async def route(ctx: ToolContext, intent: str) -> tuple[str, float, str | None]:
    criteria = {rid: r.description for rid, r in primaries(ctx.config).items()}
    if not criteria:
        raise invalid_input("recipe", "no recipes are loaded, so the intent cannot be routed")
    criteria[NONE] = NONE_DESCRIPTION
    ans = await _choose(ctx, f"Human intent: {intent}", "recipe", ROUTE_INSTRUCTIONS, criteria)
    return ans["choice"], ans["p_top"], ans["runner_up"]


# ---------------------------------------------------------------- instantiate

_MARK = re.compile(r"\{\{[#/^][^}]*\}\}")
_VAR = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
_SLOT_RE = re.compile(r"<SLOT:([^>]+)>")
_WORD = re.compile(r"[a-z]{3,}")


def _slotify(obj: Any) -> Any:
    if isinstance(obj, str):
        return _VAR.sub(lambda m: SLOT.format(m[1]), _MARK.sub("", obj))
    if isinstance(obj, dict):
        return {_slotify(k): _slotify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_slotify(v) for v in obj]
    return obj


def _profile_questions(rec: Recipe) -> dict:
    for name in PROFILES:
        if name in rec.question_profiles:
            return rec.question_profiles[name]
    if rec.question_profiles:
        return next(iter(rec.question_profiles.values()))
    for step in rec.steps:
        if isinstance(step.get("questions"), dict):
            return step["questions"]
    return {}


def _from_slots(crit: dict) -> dict:
    """$from criteria come from a caller array: one placeholder pair stands for them, plus the fixed extras."""
    name = crit["$from"]
    return {SLOT.format(f"{name}: {crit['key']}"): SLOT.format(f"{name}: {crit['text']}"), **crit.get("extra", {})}


def _template(rec: Recipe) -> dict:
    out: dict[str, Any] = {}
    qs = _profile_questions(rec)
    if "$each" in qs:
        return {SLOT.format(f"{qs['$each']}: {qs['id']}"): {
            "type": "score", "instructions": SLOT.format(f"{qs['$each']}: {qs['instructions']}"),
            "criteria": [SLOT.format(f"{qs['$each']}: level 0"), SLOT.format(f"{qs['$each']}: level 1")]}}
    for qid, q in qs.items():
        q = _slotify(json.loads(json.dumps(q)))
        crit = qs[qid].get("criteria")
        if isinstance(crit, dict) and "$from" in crit:
            q["criteria"] = _from_slots(crit)
        out[qid] = q
    return out


def _state(rec: Recipe) -> str:
    step = next((s for s in rec.steps if s.get("state_template")), None)
    if step:
        return _slotify(step["state_template"])
    req = rec.input_schema.get("required") or list(rec.input_schema.get("properties", {}))
    return SLOT.format(req[0] if req else "state")


_STOP = frozenset("which what whether how the does should that this with have kind pick customer email take "
                  "and for are who".split())


def _stems(text: str) -> set[str]:
    return {w[:4] for w in _WORD.findall(text.lower()) if w not in _STOP}


def _match(text: str, questions: dict, qtype: str, used: set[str]) -> str | None:
    want = _stems(text)
    best, score = None, 0
    for qid, q in questions.items():
        if qid in used or not isinstance(q, dict) or q.get("type") != qtype:
            continue
        n = len(want & _stems(f"{qid.replace('_', ' ')} {q.get('instructions', '')}"))
        if n > score:
            best, score = qid, n
    return best


def _labels_for(text: str, labels: dict) -> Any:
    t = text.lower()
    for key, value in labels.items():
        k = str(key).lower()
        if k in t or t in k:
            return value
    return None


def _apply_labels(q: dict, given: Any, qid: str) -> bool:
    """Put the caller's options on a choice/score question. -> True when recipe defaults were kept for some."""
    old = q.get("criteria") if isinstance(q.get("criteria"), dict) else {}
    pairs = list(given.items()) if isinstance(given, dict) else [(str(x), None) for x in given]
    if q["type"] == "score":
        q["criteria"] = [str(d or k) for k, d in pairs]
        return False
    kept = False
    crit: dict[str, str] = {}
    for label, desc in pairs:
        if not desc and old.get(label):
            desc, kept = old[label], True
        crit[label] = desc or SLOT.format(f"{label} description")
    for k, v in old.items():
        if is_escape(k) and k not in crit:
            crit[k] = v
    q["criteria"] = crit
    return kept


def _fresh(text: str, qtype: str, given: Any) -> dict:
    if qtype == "noul":
        return {"type": "noul", "instructions": f"Is it true: {text}?"}
    if qtype == "choice":
        q: dict[str, Any] = {"type": "choice", "instructions": f"{text[:1].upper()}{text[1:]}?",
                             "criteria": {SLOT.format("option 1"): SLOT.format("what option 1 means"),
                                          SLOT.format("option 2"): SLOT.format("what option 2 means")}}
    else:
        q = {"type": "score", "instructions": f"{text[:1].upper()}{text[1:]}?",
             "criteria": [SLOT.format("lowest level"), SLOT.format("highest level")]}
    if given:
        _apply_labels(q, given, "")
    return q


def _qid(text: str, taken: dict) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in ("whether", "which", "the", "is", "it", "a")]
    base = "_".join(words[:3]) or "q"
    qid, n = base, 2
    while qid in taken:
        qid, n = f"{base}_{n}", n + 1
    return qid


def _slots(draft: dict, defaults: dict[str, str]) -> list[dict]:
    slots: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def walk(obj: Any, path: str) -> None:
        if isinstance(obj, str):
            for name in _SLOT_RE.findall(obj):
                if (name, path) not in seen:
                    seen.add((name, path))
                    slots.append(_slot(name, path))
        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(k, f"{path}.{k}" if path else k)
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")

    walk(draft, "")
    for path, name in defaults.items():
        slots.append({"name": name, "path": path, "why": "descriptions carry the routing policy; defaults came from the recipe",
                      "example": next(iter(_dig(draft, path).items()), ("", ""))[1] if isinstance(_dig(draft, path), dict) else ""})
    return slots


def _dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


def _slot(name: str, path: str) -> dict:
    if path == "state":
        return {"name": name, "path": path, "why": "the text to judge", "example": f"<the {name}>"}
    return {"name": name, "path": path, "why": "needs a description only the human knows",
            "example": "label: what an input with this label looks like"}


def _human_questions(rec: Recipe, slots: list[dict], variants: list[str], config: Config) -> list[str]:
    qs: list[str] = []
    if variants:
        reg = load_all(config)
        qs.append("Which variant fits: the plain " + rec.id + " or " + "; ".join(
            f"{v} ({reg.docs[v].get('description', '')})" for v in variants) + "?")
    for s in slots:
        if s["path"] != "state" and "defaults came from the recipe" in s["why"]:
            qs.append(f"Do the default {s['name']} match your policy (who owns what)?")
        elif s["path"] != "state":
            qs.append(f"What does '{s['name']}' mean for you? ({s['path']})")
    for k, v in list(rec.policy.items())[:3]:
        qs.append(f"Recipe default {k} = {v}: does that threshold fit your risk?")
    qs += [f"Recipe limitation, does it matter to you: {x}" for x in rec.limitations[:2]]
    return list(dict.fromkeys(qs))


def _summary(a: dict) -> str:
    if a["type"] == "choice":
        return f"{a['choice']} ({round(a['p_top'], 2)})"
    if a["type"] == "score":
        return f"{round(a['score'], 1)} = {a['level_label']}"
    return str(round(a["p"], 2))


# ---------------------------------------------------------------- the tool

def _no_decision(p: float, runner: str | None) -> dict:
    return {"recipe": {"id": NONE, "p": p, "runner_up": runner, "not_a_decision": True},
            "sub_decisions": [], "draft_request": {}, "slots": [], "human_questions": [],
            "lint": {"valid": True, "errors": [], "warnings": []},
            "next_steps": ["this is generation or computation, not an OpenJev read: write it yourself, use the "
                           "generate tool for a short draft, or compute it in code"]}


async def compile_tool(ctx: ToolContext, args: dict) -> dict:
    config, intent = ctx.config, args["intent"]
    reg = load_all(config)
    if args.get("recipe"):
        rid, p, runner = args["recipe"], 1.0, None
        if rid not in reg.recipes:
            raise invalid_input("recipe", f"no such recipe: {rid}", f"one of: {', '.join(reg.ids())}")
    else:
        rid, p, runner = await route(ctx, intent)
        if rid == NONE:
            return _no_decision(p, runner)
    rec = reg.get(rid)
    variants = variants_of(config, rid)

    template = _template(rec)
    subs: list[dict] = []
    questions: dict[str, Any] = {}
    used: set[str] = set()
    kept: dict[str, str] = {}
    labels = args.get("labels") or {}
    fresh: list[tuple[dict, str, Any]] = []
    for text in args.get("sub_decisions") or []:
        qtype, sp, how = await type_sub_decision(ctx, text)
        row: dict[str, Any] = {"text": text, "qtype": qtype, "p": sp, "question_id": None, "note": how}
        subs.append(row)
        if qtype == "not_typed":
            row["redirect"] = REDIRECT["not_typed"]
            continue
        qid = _match(text, template, qtype, used)
        given = _labels_for(text, labels)
        if qid:
            used.add(qid)
            q = json.loads(json.dumps(template[qid]))
            if given and qtype != "noul" and _apply_labels(q, given, qid):
                kept[f"questions.{qid}.criteria"] = f"{qid} descriptions"
            questions[qid] = q
            row.update(question_id=qid, note=f"{how}; matches the recipe's {qid}")
        else:
            fresh.append((row, qtype, given))
    if not used:   # nothing matched the recipe: keep its questions beside the new ones
        questions = {**template, **questions}
    for row, qtype, given in fresh:
        qid = _qid(row["text"], questions)
        questions[qid] = _fresh(row["text"], qtype, given)
        row.update(question_id=qid, note=row["note"] + "; no recipe question fits, new question")
    if not subs:
        questions = template

    draft: dict[str, Any] = {"model": config.model, "state": _state(rec), "questions": questions}
    slots = _slots(draft, kept)
    lint = lint_request(draft, limits=ctx.limits.peek(), profile="gate" if rid in GATES or rid.endswith("_gate") else "default",
                        autofix=False)
    out: dict[str, Any] = {
        "recipe": {"id": rid, "p": p, "runner_up": runner, "not_a_decision": False, **({"variants": variants} if variants else {})},
        "draft_request": draft, "slots": slots, "human_questions": _human_questions(rec, slots, variants, config),
        "lint": {"valid": lint.valid, "errors": [f.to_dict() for f in lint.errors],
                 "warnings": [f.to_dict() for f in lint.warnings]}}
    if subs:
        out["sub_decisions"] = subs
    open_slots = any(_SLOT_RE.search(s) for s in _strings(questions))
    steps = ["answer human_questions"]
    if open_slots:
        steps.insert(0, "fill the <SLOT:...> placeholders in draft_request (probe and calibrate wait for them)")
    elif args.get("sample_inputs"):
        out["probe"] = []
        for i, sample in enumerate(args["sample_inputs"]):
            # the verified ex-compile-draft read has no samples key (the server default is one read)
            res = await ask(ctx, {"state": sample, "questions": questions, "lint": "off"})
            out["probe"].append({"input": i, "answers": {q: _summary(a) for q, a in res["answers"].items()}})
    if args.get("labelled_examples") and not open_slots:
        out["calibration"] = await calibrate(ctx, {"questions": questions, "examples": args["labelled_examples"]})
    elif args.get("labelled_examples"):
        steps.append("fill the slots, then call compile again with labelled_examples to calibrate")
    if not args.get("labelled_examples"):
        steps.append("add 10-20 labelled examples and run calibrate")
    steps.append("save as a recipe file")
    out["next_steps"] = steps
    return out


def _strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def register(config: Config) -> ToolSpec:
    schemas.register_tool_schemas("compile", INPUT, OUTPUT)
    annotations = {"title": "Compile a statement", "readOnlyHint": True, "idempotentHint": False, "openWorldHint": False}
    return ToolSpec(
        "compile", "Compile a statement",
        "Turn a prose intent ('tell me if support emails are angry and who should take them') into a linted draft "
        "/v1/systemone request built from the recipe library. Routes with one read, types each sub-decision with one "
        "read, instantiates a template, lists the slots only a human can fill and the questions to ask, and can probe "
        "the draft on sample_inputs or calibrate it on labelled_examples. Never writes text with the chat model.",
        schemas.INPUT_SCHEMAS["compile"], schemas.OUTPUT_SCHEMAS["compile"], annotations, compile_tool)


# ---------------------------------------------------------------- prompt author_question

INTERVIEW = """You are helping a human write an OpenJev statement (a typed question over an input), not free text.

Intent: {intent}
{examples}
Seed from the compile tool (routing, typing and a linted draft are done; the slots are the parts only the human knows):
{seed}

Interview the human one question at a time:
1. Confirm the decision: what exactly is decided about what input, and what is done with the answer.
2. For each sub-decision, confirm the type: noul (yes/no), choice (one of a closed list), score (a degree on an ordered
   scale). A summary, a count, a date or a quoted value is not a typed decision: do that in code.
3. Fill every slot with the human's own wording: option descriptions carry the policy, so ask for concrete examples.
4. Put the statement in a request with the lint tool, then run `compile` with sample_inputs and `calibrate` with 10-20
   labelled examples before relying on it.
"""


async def _author_question(args: dict, ctx: ToolContext) -> list[dict]:
    examples = args.get("examples")
    try:
        seed = json.dumps(await compile_tool(ctx, {"intent": args["intent"]}), indent=1, ensure_ascii=False)
    except Exception as e:   # the interview still works without the seed (OpenJev down, no recipes)
        seed = f"(compile was unavailable: {type(e).__name__}; start from the recipe library with the recipes resource)"
    text = INTERVIEW.format(intent=args["intent"], seed=seed,
                            examples=f"Example inputs from the human:\n{examples}\n" if examples else "")
    return [{"role": "user", "content": {"type": "text", "text": text}}]


AUTHOR_QUESTION = PromptSpec(
    "author_question", "Author a question",
    "Interview the human to write an OpenJev statement (question set) for an intent, seeded with compile output.",
    (PromptArg("intent", "what the human wants decided, in prose", required=True),
     PromptArg("examples", "optional example inputs, one per line")),
    _author_question)
