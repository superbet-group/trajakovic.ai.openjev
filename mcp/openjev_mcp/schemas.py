"""Tool input/output JSON Schemas (spec 2.2, 2.6-2.9, 2.13, 2.17), inlined: no $ref, no $defs."""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
_REF_PREFIX = "#/$defs/"

# spec 2.2 $defs, verbatim; also published whole as openjev://schema
DEFS: dict[str, dict] = {'State': {'description': 'What the questions are about. Label sections (USER MESSAGE:, DIFF:, CANDIDATES:). '
                          'Objects/arrays are JSON-dumped into the prompt.',
           'oneOf': [{'type': 'string'}, {'type': 'object'}, {'type': 'array'}]},
 'NoulQuestion': {'type': 'object',
                  'additionalProperties': False,
                  'required': ['type', 'instructions'],
                  'properties': {'type': {'const': 'noul'},
                                 'instructions': {'type': 'string',
                                                  'minLength': 3,
                                                  'description': 'One literal claim or yes/no question.'},
                                 'criteria': {'type': 'object',
                                              'additionalProperties': False,
                                              'properties': {'true': {'type': 'string', 'minLength': 1},
                                                             'false': {'type': 'string', 'minLength': 1}},
                                              'description': 'What yes and no mean. Name the near-miss in false.'}}},
 'ChoiceQuestion': {'type': 'object',
                    'additionalProperties': False,
                    'required': ['type', 'instructions', 'criteria'],
                    'properties': {'type': {'const': 'choice'},
                                   'instructions': {'type': 'string', 'minLength': 3},
                                   'criteria': {'type': 'object',
                                                'minProperties': 2,
                                                'maxProperties': 255,
                                                'additionalProperties': {'type': 'string', 'minLength': 1},
                                                'description': 'option key -> description. Keys are output labels '
                                                               'only; descriptions carry the meaning. Include an '
                                                               'escape option.'}}},
 'ScoreQuestion': {'type': 'object',
                   'additionalProperties': False,
                   'required': ['type', 'instructions', 'criteria'],
                   'properties': {'type': {'const': 'score'},
                                  'instructions': {'type': 'string', 'minLength': 3},
                                  'criteria': {'type': 'array',
                                               'minItems': 2,
                                               'maxItems': 10,
                                               'items': {'type': 'string', 'minLength': 1},
                                               'description': 'Ordered levels, worst/lowest first. Answer is the '
                                                              '0-indexed expected level.'}}},
 'Question': {'oneOf': [{'$ref': '#/$defs/NoulQuestion'},
                        {'$ref': '#/$defs/ChoiceQuestion'},
                        {'$ref': '#/$defs/ScoreQuestion'}]},
 'QuestionSet': {'type': 'object',
                 'minProperties': 1,
                 'maxProperties': 256,
                 'propertyNames': {'pattern': '^[A-Za-z0-9_.:-]{1,64}$'},
                 'additionalProperties': {'$ref': '#/$defs/Question'}},
 'ReadOptions': {'type': 'object',
                 'additionalProperties': False,
                 'properties': {'model': {'type': 'string',
                                          'description': 'Default OPENJEV_MCP_MODEL (openjev-latest). Valid names '
                                                         'are those GET /v1/models lists (served and routed models, '
                                                         'e.g. openjev-0.1, laya-1.0, verdict-1.4) plus the unlisted '
                                                         'aliases jev-latest, jev-preview; never a hard-coded list.'},
                                'samples': {'type': 'integer',
                                            'minimum': 1,
                                            'maximum': 32,
                                            'description': 'N billed reads averaged. 1 = fastest. Omit = 1 read + 3 '
                                                           'free re-reads when uncertain.'},
                                'steps': {'type': 'integer', 'minimum': 1, 'maximum': 8},
                                'think': {'type': 'integer',
                                          'minimum': 0,
                                          'maximum': 4096,
                                          'description': 'Thought budget in tokens; text-only states. 256-512 for '
                                                         'lookahead, rules, arithmetic.'},
                                'sequential': {'type': 'boolean',
                                               'description': "Chunks see earlier chunks' answers. Only matters when "
                                                              'the questions span 2+ canvas chunks (above 10 '
                                                              'questions, or fewer when score-heavy); text-only.'},
                                'timeout_ms': {'type': 'integer', 'minimum': 100, 'maximum': 600000}}},
 'Band': {'type': 'object',
          'additionalProperties': False,
          'properties': {'yes_at': {'type': 'number', 'minimum': 0.5, 'maximum': 1, 'default': 0.8},
                         'no_at': {'type': 'number', 'minimum': 0, 'maximum': 0.5, 'default': 0.2},
                         'choice_min_p': {'type': 'number', 'minimum': 0, 'maximum': 1, 'default': 0.6}}},
 'NoulAnswer': {'type': 'object',
                'required': ['type', 'p', 'band', 'margin'],
                'properties': {'type': {'const': 'noul'},
                               'p': {'type': 'number'},
                               'band': {'enum': ['yes', 'no', 'grey']},
                               'margin': {'type': 'number', 'description': '|2p-1|'}}},
 'ChoiceAnswer': {'type': 'object',
                  'required': ['type', 'choice', 'p_top', 'probabilities', 'confidence', 'margin', 'abstained'],
                  'properties': {'type': {'const': 'choice'},
                                 'choice': {'type': 'string'},
                                 'p_top': {'type': 'number'},
                                 'runner_up': {'type': ['string', 'null']},
                                 'margin': {'type': 'number', 'description': 'p_top - p_second'},
                                 'probabilities': {'type': 'object', 'additionalProperties': {'type': 'number'}},
                                 'confidence': {'type': 'number', 'description': 'server value, 1 - H/ln K'},
                                 'entropy': {'type': 'number'},
                                 'abstained': {'type': 'boolean',
                                               'description': 'true when the escape option won or p_top < '
                                                              'choice_min_p'}}},
 'ScoreAnswer': {'type': 'object',
                 'required': ['type', 'score', 'level', 'level_label', 'probabilities', 'confidence'],
                 'properties': {'type': {'const': 'score'},
                                'score': {'type': 'number', 'description': '0-indexed expected level'},
                                'level': {'type': 'integer', 'description': 'argmax level'},
                                'level_label': {'type': 'string'},
                                'probabilities': {'type': 'object', 'additionalProperties': {'type': 'number'}},
                                'confidence': {'type': 'number'},
                                'spread': {'type': 'number', 'description': 'sqrt(sum p_k (k-score)^2)'},
                                'bimodal': {'type': 'boolean',
                                            'description': 'two non-adjacent levels each >= 0.2'}}},
 'Answer': {'oneOf': [{'$ref': '#/$defs/NoulAnswer'},
                      {'$ref': '#/$defs/ChoiceAnswer'},
                      {'$ref': '#/$defs/ScoreAnswer'}]},
 'Meta': {'type': 'object',
          'properties': {'model': {'type': 'string',
                                   'description': 'resolved model from the response, e.g. openjev-0.1'},
                         'request_ids': {'type': 'array', 'items': {'type': 'string'}},
                         'requests': {'type': 'integer'},
                         'latency_ms': {'type': 'number'},
                         'server_ms': {'type': 'number'},
                         'input_tokens': {'type': 'integer'},
                         'output_tokens': {'type': 'integer'},
                         'chunks_estimate': {'type': 'integer', 'description': 'estimate (+-25%), 2.3'},
                         'body_hashes': {'type': 'array',
                                         'items': {'type': 'string'},
                                         'description': 'sha256:<hex> of the exact bytes of each request body, same '
                                                        'order as request_ids'},
                         'server_timing': {'type': 'object',
                                           'properties': {'model_ms': {'type': 'number'},
                                                          'server_ms': {'type': 'number'},
                                                          'total_ms': {'type': 'number'}},
                                           'description': 'server-timing header, summed over requests'},
                         'timeout_ms_used': {'type': 'integer'},
                         'warnings': {'type': 'array', 'items': {'type': 'string'}}}},
 'LintFinding': {'type': 'object',
                 'required': ['code', 'path', 'message'],
                 'properties': {'code': {'type': 'string'},
                                'path': {'type': 'string'},
                                'message': {'type': 'string'},
                                'fix': {'type': 'string'},
                                'rule': {'type': 'string', 'description': 'guide rule id, section 3'},
                                'autofixed': {'type': 'boolean'},
                                'limit_source': {'enum': ['server', 'default'],
                                                 'description': 'limit-dependent codes only (2.2)'}}},
 'QuestionStats': {'description': 'per-question statistics over ok rows, by question type',
                   'oneOf': [{'type': 'object',
                              'required': ['type', 'n'],
                              'properties': {'type': {'const': 'noul'},
                                             'n': {'type': 'integer'},
                                             'mean_p': {'type': 'number', 'description': 'mean P(yes)'},
                                             'yes': {'type': 'integer'},
                                             'no': {'type': 'integer'},
                                             'grey': {'type': 'integer'},
                                             'mean_margin': {'type': 'number',
                                                             'description': 'mean |2p-1| (noul has no server '
                                                                            'confidence)'}}},
                             {'type': 'object',
                              'required': ['type', 'n'],
                              'properties': {'type': {'const': 'choice'},
                                             'n': {'type': 'integer'},
                                             'counts': {'type': 'object',
                                                        'additionalProperties': {'type': 'integer'}},
                                             'top2': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 2},
                                             'mean_confidence': {'type': 'number',
                                                                 'description': 'mean server confidence'},
                                             'abstained': {'type': 'integer'}}},
                             {'type': 'object',
                              'required': ['type', 'n'],
                              'properties': {'type': {'const': 'score'},
                                             'n': {'type': 'integer'},
                                             'mean': {'type': 'number',
                                                      'description': 'mean expected level (0-indexed)'},
                                             'std': {'type': 'number',
                                                     'description': 'population std of the expected level'},
                                             'histogram': {'type': 'object',
                                                           'additionalProperties': {'type': 'integer'},
                                                           'description': 'level -> count of argmax levels'},
                                             'mean_confidence': {'type': 'number',
                                                                 'description': 'mean server confidence'}}}]},
 'BatchHeader': {'description': 'first line of a batch output .jsonl (2.11)',
                 'type': 'object',
                 'required': ['openjev_mcp', 'v', 'spec', 'run_id', 'question_hash', 'created_at'],
                 'properties': {'openjev_mcp': {'const': 'batch'},
                                'v': {'const': 2},
                                'spec': {'type': 'string'},
                                'run_id': {'type': 'string',
                                           'description': 'sha256:<question_hash + source + options + sampling + '
                                                          'regrey_samples>'},
                                'question_hash': {'type': 'string', 'description': 'sha256:<canonical questions>'},
                                'options': {'type': 'object'},
                                'sampling': {'enum': ['fast', 'server_default']},
                                'thresholds': {'type': 'object'},
                                'review_rule': {'type': 'object'},
                                'audit': {'type': 'object'},
                                'source': {'type': 'object',
                                           'properties': {'kind': {'enum': ['items', 'items_file', 'template']},
                                                          'path': {'type': 'string', 'description': 'relative'},
                                                          'format': {'type': 'string'},
                                                          'delimiter': {'type': ['string', 'null']},
                                                          'state_field': {'type': ['string', 'null']},
                                                          'id_field': {'type': ['string', 'null']},
                                                          'row_count': {'type': 'integer'}}},
                                'created_at': {'type': 'string', 'description': 'ISO 8601'}}},
 'BatchRow': {'description': 'one line of a batch output .jsonl; always full; the last row per id wins (2.11)',
              'type': 'object',
              'required': ['index', 'id', 'status', 'state_hash'],
              'properties': {'index': {'type': 'integer'},
                             'id': {'type': 'string'},
                             'status': {'enum': ['ok', 'error']},
                             'state': {'type': 'string',
                                       'description': 'state text; absent when include_state is false'},
                             'state_hash': {'type': 'string', 'description': 'sha256:<state>'},
                             'answers': {'type': 'object',
                                         'additionalProperties': {'$ref': '#/$defs/Answer'},
                                         'description': 'question id -> full Answer, incl. probabilities, '
                                                        'confidence, entropy, derived fields'},
                             'needs_review': {'type': 'boolean'},
                             'review_reasons': {'type': 'array', 'items': {'type': 'string'}},
                             'audit': {'type': 'boolean'},
                             'model': {'type': 'string'},
                             'usage': {'type': 'object',
                                       'properties': {'input_tokens': {'type': 'integer'},
                                                      'output_tokens': {'type': 'integer'}}},
                             'latency_ms': {'type': 'number'},
                             'server_timing': {'type': 'object',
                                               'properties': {'model_ms': {'type': 'number'},
                                                              'server_ms': {'type': 'number'},
                                                              'total_ms': {'type': 'number'}}},
                             'request_id': {'type': ['string', 'null']},
                             'body_hash': {'type': 'string', 'description': 'sha256:<exact bytes sent>'},
                             'retried': {'oneOf': [{'type': 'null'},
                                                   {'type': 'object',
                                                    'properties': {'status': {'type': 'integer'},
                                                                   'kind': {'type': 'string'},
                                                                   'attempts': {'type': 'integer'}}}]},
                             'error': {'oneOf': [{'type': 'null'}, {'$ref': '#/$defs/ToolError'}]},
                             'ts': {'type': 'string', 'description': 'ISO 8601'}}},
 'ToolError': {'type': 'object',
               'required': ['code', 'message'],
               'properties': {'code': {'type': 'string'},
                              'http_status': {'type': ['integer', 'null']},
                              'message': {'type': 'string'},
                              'hint': {'type': 'string'},
                              'path': {'type': ['string', 'null']},
                              'retryable': {'type': 'boolean'},
                              'retry_after_s': {'type': ['number', 'null']},
                              'request_id': {'type': ['string', 'null']},
                              'server_detail': {}}}}

SCHEMA_RESOURCE: dict[str, Any] = {"$schema": SCHEMA_URI, "$id": "openjev://schema", "$defs": DEFS}


def _inline(node: Any, defs: Mapping[str, dict]) -> Any:
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    ref = node.get("$ref")
    if isinstance(ref, str):
        if not ref.startswith(_REF_PREFIX):
            raise KeyError(ref)
        out = _inline(defs[ref[len(_REF_PREFIX):]], defs)
        out.update((k, _inline(v, defs)) for k, v in node.items() if k != "$ref")
        return out
    return {k: _inline(v, defs) for k, v in node.items() if k != "$defs"}


def inline(schema: dict, defs: Mapping[str, dict] = DEFS) -> dict:
    """Deep copy with every '#/$defs/X' replaced by a copy of defs[X] (sibling keys merged);
    the root gets $schema. KeyError for an unknown def."""
    out = _inline(schema, defs)
    out.pop("$schema", None)
    return {"$schema": SCHEMA_URI, **out}


def _ref(name: str) -> dict:
    return {"$ref": _REF_PREFIX + name}


_SCORE_FIELDS = {k: v for k, v in DEFS["ScoreAnswer"]["properties"].items() if k != "type"}

_INPUT: dict[str, dict] = {
    "ask": {
        "type": "object", "additionalProperties": False,
        "required": ["state", "questions"],
        "properties": {
            "state": _ref("State"),
            "questions": _ref("QuestionSet"),
            "options": _ref("ReadOptions"),
            "thresholds": _ref("Band"),
            "lint": {"enum": ["warn", "off"], "default": "warn",
                     "description": "Lint errors always block; warnings are returned, or suppressed with off."},
            "return_raw": {"type": "boolean", "default": False},
        },
    },
    "yes_no": {
        "type": "object", "additionalProperties": False,
        "required": ["state", "claim"],
        "properties": {
            "state": _ref("State"),
            "claim": {"type": "string", "minLength": 3,
                      "description": "Question or claim, literal and positive (no 'fails to', no 'not')."},
            "true_means": {"type": "string", "description": "criteria.true: what makes it yes."},
            "false_means": {"type": "string", "description": "criteria.false: what makes it no, naming the near-miss."},
            "yes_at": {"type": "number", "default": 0.8},
            "no_at": {"type": "number", "default": 0.2},
            "options": _ref("ReadOptions"),
        },
    },
    "classify": {
        "type": "object", "additionalProperties": False,
        "required": ["state", "question", "labels"],
        "properties": {
            "state": _ref("State"),
            "question": {"type": "string", "minLength": 3},
            "labels": {"type": "object", "minProperties": 1, "maxProperties": 254,
                       "additionalProperties": {"type": "string", "minLength": 1},
                       "description": "label -> description of what an input with this label looks like. Also accepted: "
                                      "an array of strings, converted with the label as its own description plus warning W202."},
            "escape": {"oneOf": [{"type": "boolean", "const": False},
                                 {"type": "object", "required": ["label", "description"],
                                  "properties": {"label": {"type": "string"}, "description": {"type": "string"}}}],
                       "description": "Default {label:'other', description:'anything else, or too vague to tell'}; skipped "
                                      "when a label already starts with other/none/no_match/not_stated. false disables (lint W201)."},
            "min_p": {"type": "number", "default": 0.6, "description": "abstain when p_top < min_p"},
            "multi_label": {"type": "boolean", "default": False},
            "options": _ref("ReadOptions"),
        },
    },
    "score": {
        "type": "object", "additionalProperties": False,
        "required": ["state", "question", "levels"],
        "properties": {
            "state": _ref("State"),
            "question": {"type": "string", "minLength": 3},
            "levels": {"type": "array", "minItems": 2, "maxItems": 10, "items": {"type": "string", "minLength": 1},
                       "description": "lowest/worst first; each level names observable evidence, mutually exclusive, "
                                      "no escape clauses ('unless', 'no ... mentioned')"},
            "one_based": {"type": "boolean", "default": False,
                          "description": "add 1 to score/level in the output (for 1-10 ratings)"},
            "options": _ref("ReadOptions"),
        },
    },
    "lint": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "request": {"type": "object", "description": "a full /v1/systemone body; or give state + questions"},
            "state": _ref("State"),
            "questions": {"type": "object"},
            "options": {"type": "object"},
            "images": {"type": "array"},
            "profile": {"enum": ["default", "strict", "gate"], "default": "default",
                        "description": "gate: warnings about blocking questions become errors"},
            "autofix": {"type": "boolean", "default": True},
            "emit": {"type": "array", "uniqueItems": True, "items": {"enum": ["body", "curl", "python"]},
                     "description": "return the exact HTTP body and request snippets (the key is never inlined: "
                                    "curl uses $OPENJEV_API_KEY, Python os.environ)"},
        },
        "description": "Give either request, or questions (with optional state, options, images). "
                       "Checked in code (OJ_INVALID_INPUT), not with a root anyOf (2.2).",
    },
    "status": {
        "type": "object", "additionalProperties": False,
        "properties": {"probe": {"type": "boolean", "default": False,
                                 "description": "also run one samples:1 noul read to measure latency"}},
    },
}

_OUTPUT: dict[str, dict] = {
    "ask": {
        "type": "object", "required": ["answers", "meta"],
        "properties": {
            "answers": {"type": "object", "additionalProperties": _ref("Answer")},
            "meta": _ref("Meta"),
            "lint": {"type": "object", "properties": {"warnings": {"type": "array"}}},
            "raw": {"type": "object", "description": "server body, when return_raw"},
        },
    },
    "yes_no": {
        "type": "object", "required": ["decision", "p", "margin", "meta"],
        "properties": {
            "decision": {"enum": ["yes", "no", "uncertain"]}, "p": {"type": "number"}, "margin": {"type": "number"},
            "thresholds_used": {"type": "object"}, "meta": _ref("Meta"),
        },
    },
    "classify": {
        "type": "object", "required": ["label", "abstained", "p_top", "probabilities", "meta"],
        "properties": {
            "label": {"type": ["string", "null"], "description": "null when abstained"},
            "abstained": {"type": "boolean"}, "reason": {"type": "string"}, "top": {"type": "string"},
            "p_top": {"type": "number"}, "runner_up": {"type": ["string", "null"]}, "margin": {"type": "number"},
            "probabilities": {"type": "object"}, "confidence": {"type": "number"},
            "labels_multi": {"type": "object", "description": "multi_label: label -> {p, band}"},
            "meta": _ref("Meta"),
        },
    },
    "score": {
        "type": "object", "required": ["score", "level", "level_label", "probabilities", "confidence", "meta"],
        "properties": {**_SCORE_FIELDS, "meta": _ref("Meta")},
    },
    "lint": {
        "type": "object", "required": ["valid", "errors", "warnings"],
        "properties": {
            "valid": {"type": "boolean", "description": "true when the server would accept the (fixed) request"},
            "errors": {"type": "array", "items": _ref("LintFinding")},
            "warnings": {"type": "array", "items": _ref("LintFinding")},
            "fixed_request": {"type": "object"},
            "estimate": {"type": "object",
                         "properties": {"questions": {}, "chunks": {}, "input_tokens_approx": {},
                                        "latency_ms_idle_approx": {}, "billed_reads": {}},
                         "description": "chunks is the +-25% estimate of 2.3"},
            "snippets": {"type": "object",
                         "properties": {"body": {"type": "object"}, "curl": {"type": "string"}, "python": {"type": "string"}}},
            "body_hash": {"type": "string"},
        },
    },
    "status": {
        "type": "object", "required": ["healthy", "decide_models"],
        "properties": {
            "healthy": {"type": "boolean"}, "base_url": {"type": "string"}, "decide_models": {"type": "array"},
            "chat_models": {"type": "array"}, "aliases_accepted": {"type": "array"}, "resolved": {"type": "object"},
            "auth": {"enum": ["none", "bearer", "unknown"]},
            "backend": {"type": "string", "description": "from GET /v1/limits (vllm, mlx, laya, verdict, clm, jevk5), else unknown"},
            "latency_probe_ms": {"type": ["number", "null"]},
            "limits": {"type": "object", "description": "prompt_tokens may be null (backend unknown); includes the batch caps"},
            "limit_source": {"enum": ["server", "default"]},
            "capabilities": {
                "type": "object",
                "additionalProperties": {"type": "object", "properties": {
                    "images": {"type": "boolean"}, "steps": {"type": "boolean"}, "samples": {"type": "boolean"},
                    "think": {"type": "boolean"}, "sequential": {"type": "boolean"},
                    "max_prompt_tokens": {"type": ["integer", "null"]}, "max_choices": {"type": ["integer", "null"]}}},
                "description": "per decide model, from /v1/limits or the 2.2 matrix"},
            "mcp": {"type": "object", "properties": {
                "server_version": {"type": "string"}, "protocol_versions": {"type": "array", "items": {"type": "string"}},
                "batch_max_inflight": {"type": "integer"}}},
            "warnings": {"type": "array"},
        },
    },
}

INPUT_SCHEMAS: dict[str, dict] = {name: inline(s) for name, s in _INPUT.items()}
OUTPUT_SCHEMAS: dict[str, dict] = {name: inline(s) for name, s in _OUTPUT.items()}


def register_tool_schemas(name: str, input_schema: dict, output_schema: dict,
                          extra_defs: Mapping[str, dict] | None = None) -> None:
    """Inline both schemas against DEFS + extra_defs and store them (feature packages call it from register())."""
    defs = {**DEFS, **(extra_defs or {})}
    out = []
    for kind, schema in (("input", input_schema), ("output", output_schema)):
        done = inline(schema, defs)
        if done.get("type") != "object":
            raise ValueError(f"{name} {kind} schema: root must be an object")
        bad = [k for k in ("oneOf", "anyOf", "allOf") if k in done]
        if bad:
            raise ValueError(f"{name} {kind} schema: root {bad[0]} is not allowed")
        if '"$ref"' in json.dumps(done):
            raise ValueError(f"{name} {kind} schema: $ref left after inlining")
        out.append(done)
    INPUT_SCHEMAS[name], OUTPUT_SCHEMAS[name] = out
    try:
        from .validate import _validator
        _validator.cache_clear()
    except ImportError:  # pragma: no cover
        pass
