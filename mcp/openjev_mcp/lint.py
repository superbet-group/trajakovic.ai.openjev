"""Pure request linter (spec 2.13): what the server rejects, phrasing anti-patterns, autofix, estimate, snippets."""
from __future__ import annotations

import base64
import binascii
import copy
import math
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import wire
from .derive import chunks_estimate, is_escape
from .limits import ALIAS_TARGET, ALIASES_ACCEPTED, MLX_PROMPT_TOKENS, VLLM_PROMPT_TOKENS, Limits

ERROR_CODES = frozenset({
    "E001", "E002", "E003", "E004", "E005", "E006", "E010", "E011", "E012", "E013", "E014", "E015", "E016", "E017",
    "E020", "E021", "E022", "E023", "E024", "E025", "E026", "E027", "E028", "E030", "E031", "E032"})
WARNING_CODES = frozenset({
    "W101", "W102", "W103", "W104", "W105", "W106", "W201", "W202", "W203", "W204", "W205", "W206",
    "W301", "W302", "W303", "W304", "W305", "W401", "W402", "W403", "W404", "W405", "W406",
    "W501", "W502", "W503", "W504", "W601", "W602", "W603", "W604", "W605"})
# deviation: 2.11/2.13: batch import findings (E030-E032, W601-W605) are raised by batch.importers, never by lint_request;
# CODES stays the lint-tool set so test_mcp_lint keeps pinning it to the 2.13 table minus PHASE2
BATCH_CODES = frozenset({"E030", "E031", "E032", "W601", "W602", "W603", "W604", "W605"})
CODES = (ERROR_CODES | WARNING_CODES) - BATCH_CODES
RULES = {
    "W101": "R4", "W102": "R4", "W103": "R8", "W104": "R3", "W105": "R2", "W106": "R2", "W201": "R5", "W202": "R6",
    "W203": "R7", "W204": "R6", "W205": "R17", "W206": "R12", "W301": "R9", "W302": "R10", "W303": "R1",
    "W304": "R9", "W305": "R11", "W401": "R13", "W402": "R15", "W403": "R15", "W404": "R15", "W405": "R15",
    "W406": "R15", "W501": "R14", "W502": "R14", "W503": "R16", "W504": "R13"}
# deviation: 2.13: the batch import codes name no guide rule, so RULES has no entry for them

BATCH_FIELDS = ("concurrency", "sampling", "regrey_samples")
QUESTION_TYPES = ("noul", "choice", "score")
TYPE_FIXES = {"boolean": "noul", "category": "choice", "enum": "choice"}
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
DATA_URL = re.compile(r"^data:([^;,]*);base64,(.*)$", re.S)
MAX_SCORE_LEVELS = 10
ESCAPE_KEY = "other"
ESCAPE_TEXT = "anything else, or too vague to tell"
CHARS_PER_TOKEN = 3.6
PROMPT_OVERHEAD_TOKENS = 77
IMAGE_TOKENS = 256
CHUNK_MS = 300
PREFILL_FREE_TOKENS = 1000
PREFILL_TOKENS_PER_S = 1300
SAMPLE_MS = 60
THINK_MS = 12
SEQUENTIAL_FACTOR = 1.6
STATE_TOKENS_WARN = 4000
THINK_WARN = 1024
HOT_PATH_MAX = 4
PREFILTER_WARN = 50
LATENCY_WARN = 120

NEGATION = re.compile(r"\b(fails? to|failed to|does not|do not|did not|doesn't|don't|didn't|isn't|aren't|wasn't|"
                      r"weren't|can't|cannot|won't|never|without|no longer|not)\b", re.I)
COMPOUND = re.compile(r"\b(?:and also|and|or|as well as)\s+(?:(?:is|are|was|were|does|do|did|has|have|had|can|could|"
                      r"will|would|should|must|may|might|also|then)\b|(?:it|they|he|she|this|that|these|those|"
                      r"the \w+)\s+\w+|\w+ed\b)", re.I)
VAGUE = re.compile(r"\b(good|bad|ok|okay|fine|quality|clear|nice|proper|appropriate)\b", re.I)
DEFINED = re.compile(r"\b(meaning|means|i\.e\.|e\.g\.|such as|defined|namely)\b|[:(]", re.I)
OPINION_START = re.compile(r"^\s*(is|are|was|were|does|do|should|can|could|would)\b", re.I)
OPINION_WORDS = 5
WH_START = re.compile(r"^\s*(which|what|how)\b", re.I)
WH_WORDS = 3
WHICH_NOUN = re.compile(r"which (\w+)\?", re.I)
AMBIGUITY_GATE = re.compile(r"what (?:does|do|is meant by)\b.*\bmean|what is meant by|which (?:meaning|interpretation)|"
                            r"\b(?:is|are) (?:this|it|the request|the task) (?:clear|ambiguous|unambiguous)", re.I)
ESCAPE_CLAUSE = re.compile(r"\b(unless|except|apart from|or not stated|if not stated|not mentioned|"
                           r"no\b[^.;]{0,40}\bmentioned|no\b[^.;]{0,40}\bstated)\b", re.I)
PREFILTER_NOTE = re.compile(r"shortlist|pre-?filter|candidate|filtered|top[ -]\d+", re.I)
BLOCKING_ID = re.compile(r"block|deny|destruct|danger|unsafe|malicious|inject|forbid|harm|secret|credential|exfil|"
                         r"irrevers", re.I)
SECTION_LABEL = re.compile(r"^\s*(\[[^\]\n]{1,60}\]|#{1,6} \S|[A-Za-z][\w /().-]{0,40}:(?:\s|$))")
UNTRUSTED_MARKER = re.compile(r"```|\[(?:WebFetch|Web|Tool)[^\]]*\]|<tool_result|^\s*(?:SYSTEM|ASSISTANT)\s*:", re.I | re.M)
UNTRUSTED_CHARS = 1500
STOP = frozenset("""this that with from have into when what which will would should could about there their them they
    than then also each both other such only some more most over under after before where while these those being
    been does done your ours very just like""".split())
TIERS = (
    (("none", "no", "fine", "ok", "trivial"), ("low", "minor", "mild", "small"), ("moderate", "medium", "some"),
     ("major", "high", "significant", "large"), ("severe", "critical", "catastrophic", "extreme", "urgent")),
    (("calm", "neutral", "happy"), ("annoyed", "frustrated", "upset"), ("angry", "furious", "enraged")),
)
EVIDENCE_EXAMPLE = {"critical": "outage or data loss for all users", "catastrophic": "irreversibly destroys production data",
                    "severe": "most users cannot use the product", "high": "a core feature is broken for many users"}


@dataclass
class Finding:
    code: str
    path: str
    message: str
    fix: str | None = None
    rule: str | None = None
    autofixed: bool | None = None
    limit_source: str | None = None        # limit-dependent codes only

    def to_dict(self) -> dict:
        return {k: v for k, v in (("code", self.code), ("path", self.path), ("message", self.message),
                                  ("fix", self.fix), ("rule", self.rule), ("autofixed", self.autofixed),
                                  ("limit_source", self.limit_source)) if v is not None}

    def line(self) -> str:
        return f"{self.code} {self.path}: {self.message}"


@dataclass
class LintReport:
    valid: bool                            # no errors; autofixed errors stay listed
    errors: list[Finding]
    warnings: list[Finding]
    fixed_request: dict | None             # only when autofix changed something
    estimate: dict


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _flag(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return bool(v)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z0-9]{3,}", text.lower())


def _content(text: str) -> set[str]:
    return {w for w in _words(text) if w not in STOP}


def _template(text: str, key: str) -> str:
    out = text.lower().replace(key.lower(), "<id>") if key else text.lower()
    return re.sub(r"\b[a-z]{0,3}\d+\b", "<id>", out)


def _clip(text: str, n: int = 60) -> str:
    return text if len(text) <= n else text[: n - 3] + "..."


def _json_len(obj: Any) -> int:
    try:
        return len(wire.dumps_text(obj))
    except (TypeError, ValueError):
        return len(str(obj))


def _images(req: Mapping[str, Any]) -> list:
    images = req.get("images")
    return images if isinstance(images, list) else []


def _prompt_tokens(req: Mapping[str, Any]) -> int:
    chars = _json_len(req.get("state")) + _json_len(req.get("questions"))
    return math.ceil(chars / CHARS_PER_TOKEN) + PROMPT_OVERHEAD_TOKENS + IMAGE_TOKENS * len(_images(req))


def _chunks(questions: Any) -> int:
    if not isinstance(questions, Mapping) or not questions:
        return 0
    clean = {str(qid): {"type": q.get("type"),
                        "criteria": q["criteria"] if isinstance(q.get("criteria"), (Mapping, list)) else None}
             if isinstance(q, Mapping) else {} for qid, q in questions.items()}
    return chunks_estimate(clean)


def estimate(request: Mapping[str, Any]) -> dict:
    if not isinstance(request, Mapping):
        return {"questions": 0, "chunks": 0, "input_tokens_approx": 0, "latency_ms_idle_approx": 0, "billed_reads": 1}
    questions = request.get("questions")
    n = len(questions) if isinstance(questions, Mapping) else 0
    chunks = _chunks(questions)
    samples = _int(request.get("samples")) or 1
    think = _int(request.get("think")) or 0
    tokens = _prompt_tokens(request)
    billed = samples if samples > 1 else 1
    input_tokens = tokens * billed * (2 if think > 0 else 1)
    latency = chunks * CHUNK_MS + chunks * SAMPLE_MS * (billed - 1) + THINK_MS * think
    latency += max(0, tokens - PREFILL_FREE_TOKENS) * 1000 // PREFILL_TOKENS_PER_S
    if _flag(request.get("sequential")) and chunks > 1:
        latency = int(latency * SEQUENTIAL_FACTOR)
    return {"questions": n, "chunks": chunks, "input_tokens_approx": input_tokens,
            "latency_ms_idle_approx": int(latency), "billed_reads": billed}


def snippets(body: Mapping[str, Any], base_url: str) -> dict:
    text = wire.body_bytes(body).decode("utf-8")
    url = base_url.rstrip("/") + "/v1/systemone"
    curl = (f"curl -sS -X POST {shlex.quote(url)} \\\n  -H 'Content-Type: application/json' \\\n"
            f"  -H \"Authorization: Bearer $OPENJEV_API_KEY\" \\\n  --data-binary {shlex.quote(text)}")
    python = ("import os\nimport urllib.request\n\n"
              f"BODY = {text!r}\n"
              f"req = urllib.request.Request(\n    {url!r},\n    data=BODY.encode(\"utf-8\"),\n"
              "    headers={\"Content-Type\": \"application/json\",\n"
              "             \"Authorization\": \"Bearer \" + (os.environ.get(\"OPENJEV_API_KEY\") or \"\")},\n)\n"
              "print(urllib.request.urlopen(req).read().decode(\"utf-8\"))\n")
    return {"body": dict(body), "curl": curl, "python": python}


class _Linter:
    def __init__(self, limits: Limits, profile: str, autofix: bool, require_state: bool = True):
        self.limits = limits
        self.profile = profile
        self.autofix = autofix
        self.require_state = require_state
        self.errors: list[Finding] = []
        self.warnings: list[Finding] = []
        self.req: dict = {}
        self.model: Any = None
        self.caps: dict = {}
        self.model_known = False
        self.kinds: dict[str, str] = {}

    # findings

    def err(self, code: str, path: str, message: str, fix: str | None = None, *, fixed: bool = False,
            limited: bool = False) -> None:
        self.errors.append(Finding(code, path, message, fix, autofixed=True if fixed and self.autofix else None,
                                   limit_source=self.limits.source if limited else None))

    def warn(self, code: str, path: str, message: str, fix: str | None = None, *, limited: bool = False) -> None:
        self.warnings.append(Finding(code, path, message, fix, rule=RULES.get(code),
                                     limit_source=self.limits.source if limited else None))

    def limited(self, code: str, path: str, message: str, fix: str | None = None) -> None:
        """Server limits make it an error; the documented defaults only a warning (spec 2.2)."""
        if self.limits.source == "server":
            self.err(code, path, message, fix, limited=True)
        else:
            self.warn(code, path, message, fix, limited=True)

    def capability(self, code: str, path: str, message: str, fix: str | None, *, drop: str | None = None) -> None:
        """Unsupported option: an error when the model is known, else a warning; the autofix drops the option."""
        if self.model_known:
            self.err(code, path, message, fix, fixed=drop is not None)
            if drop is not None and self.autofix:
                self.req.pop(drop, None)
        else:
            self.warn(code, path, message, fix)

    # run

    def run(self, request: Any) -> LintReport:
        if not isinstance(request, Mapping):
            self.err("E002", "request", "request is not an object; the server returns 422",
                     "send a JSON object with model, state and questions")
            return self.report(request)
        self.req = copy.deepcopy(dict(request))
        self.check_model()
        self.check_state()
        if "weights" in self.req:
            self.err("E026", "weights", "top-level weights are silently ignored by the server (200 OK)",
                     "strip it and compute weights in code", fixed=True)
            if self.autofix:
                del self.req["weights"]
        self.check_options()
        self.check_images()
        self.check_questions()
        self.check_options_after()
        self.check_state_text()
        self.check_prompt()
        if self.profile == "gate":
            self.promote()
        return self.report(request)

    def report(self, request: Any) -> LintReport:
        fixed = None
        if isinstance(request, Mapping) and self.req != dict(request):
            fixed = self.req
        subject = fixed if fixed is not None else request
        return LintReport(not self.errors, self.errors, self.warnings, fixed, estimate(subject))

    def promote(self) -> None:
        """Gate: every question is a blocking one, so its phrasing warnings become errors."""
        keep = []
        for f in self.warnings:
            if f.path.startswith("questions.") and f.code[1] in "1234":
                self.errors.append(f)
            else:
                keep.append(f)
        self.warnings = keep

    # top level

    def check_model(self) -> None:
        model = self.req.get("model")
        self.model = model if isinstance(model, str) else None
        if self.model is None:
            self.err("E024", "model", "model is missing or not a string; the server returns 422",
                     "set model to openjev-latest", fixed=True)
            if self.autofix:
                self.req = {"model": "openjev-latest", **{k: v for k, v in self.req.items() if k != "model"}}
            self.model = "openjev-latest"
        else:
            known = self.limits.known_models
            if known is not None and model not in (*known, *ALIASES_ACCEPTED, "openjev-latest"):
                self.err("E024", "model", f"Unknown model: {model}; the server returns 400",
                         f"use openjev-latest (served: {', '.join(known)})" if known else "use openjev-latest",
                         fixed=True)
                if self.autofix:
                    self.req["model"] = "openjev-latest"
        resolved = ALIAS_TARGET.get(self.model, self.model)
        known = self.limits.known_models
        self.model_known = bool(known is not None and (self.model in known or resolved in known)
                                or self.limits.source == "server" and resolved in self.limits.models)
        self.caps = self.limits.capabilities(self.model)

    def check_state(self) -> None:
        if "state" not in self.req and not self.require_state:
            return
        state = self.req.get("state")
        if state is None or isinstance(state, (bool, int, float)) or (
                isinstance(state, str) and not state.strip()) or (isinstance(state, (dict, list)) and not state):
            self.err("E001", "state", "state is missing, empty or not text/object/list; the server returns 422",
                     "send the evidence as a non-empty string")

    def check_options(self) -> None:
        req = self.req
        for key, lo, hi in (("samples", 1, 32), ("steps", 1, 8), ("think", 0, 4096)):
            if req.get(key) is None:
                continue
            v = req[key]
            n = _int(v)
            if n is None:
                try:
                    n = int(v) if not isinstance(v, bool) and (isinstance(v, str) or float(v).is_integer()) else None
                except (TypeError, ValueError, OverflowError):
                    n = None
            if n is None:
                self.err("E020", key, f"{key} must be an integer {lo}-{hi}; the server returns 422")
            elif _int(v) is None or not lo <= n <= hi:
                clamped = min(hi, max(lo, n))
                self.err("E020", key, f"{key} {v!r} must be an integer {lo}-{hi}; the server returns 422",
                         f"use {clamped}", fixed=True)
                if self.autofix:
                    req[key] = clamped
        seq = req.get("sequential")
        if seq is not None and not isinstance(seq, bool):
            fix = None
            if isinstance(seq, str) and seq.strip().lower() in ("true", "1", "yes", "false", "0", "no") or seq in (0, 1):
                fix = _flag(seq)
            self.err("E021", "sequential", f"sequential {seq!r} is not a boolean; the server returns 422 bool_parsing",
                     f"use {str(fix).lower()}" if fix is not None else "use true or false", fixed=fix is not None)
            if fix is not None and self.autofix:
                req["sequential"] = fix
        has_images = bool(_images(req))
        for key in ("think", "sequential"):
            if has_images and (_int(req.get(key)) or 0 if key == "think" else _flag(req.get(key))):
                self.err("E022", key, f"{key} needs a text state; the server returns 400 with images",
                         f"drop {key} (convert the UI to a text tree) or drop the images", fixed=True)
                if self.autofix:
                    del req[key]
        caps = self.caps
        if has_images and not caps["images"]:
            self.capability("E028", "images", f"{self.model} does not support images",
                            "use openjev-latest for images", drop="images")
        for key in ("steps", "samples"):
            if (_int(req.get(key)) or 0) > 1 and not caps[key]:
                self.capability("E028", key, f"{self.model} does not support {key} > 1",
                                f"drop {key}, or use openjev-latest", drop=key)
        if not has_images:
            if (_int(req.get("think")) or 0) > 0 and not caps["think"]:
                self.capability("E028", "think", f"{self.model} does not support think",
                                "drop think, or use openjev-latest", drop="think")
            if _flag(req.get("sequential")) and not caps["sequential"]:
                self.capability("E028", "sequential", f"{self.model} does not support sequential",
                                "drop sequential, or use openjev-latest", drop="sequential")

    def check_images(self) -> None:
        images = self.req.get("images")
        if images is None or images == []:
            return
        if not isinstance(images, list):
            self.err("E023", "images", "images must be an array")
            return
        cap = self.limits.values["images"]
        if len(images) > cap:
            self.limited("E023", "images", f"{len(images)} images; the limit is {cap}", f"send at most {cap} images")
        max_bytes = self.limits.values["image_bytes"]
        for i, image in enumerate(images):
            path = f"images.{i}"
            if isinstance(image, str):
                m = DATA_URL.match(image)
                if m is None:
                    what = "an https URL is not fetched by the server; " if image.startswith("http") else ""
                    self.err("E023", path, what + "an image is a data:image/...;base64 string or a "
                             "{content_type, base64} object", "re-encode it as a data URL (ask_image does this)")
                    continue
                ctype, data = m.group(1), m.group(2)
            elif isinstance(image, dict) and isinstance(image.get("content_type"), str) and isinstance(image.get("base64"), str):
                ctype, data = image["content_type"], image["base64"]
            else:
                self.err("E023", path, "an image is a data:image/...;base64 string or a {content_type, base64} object",
                         "re-encode it as a data URL (ask_image does this)")
                continue
            if ctype not in IMAGE_TYPES:
                self.err("E023", path, f"image type {ctype!r} is not supported; use JPEG, PNG, WebP or GIF",
                         "the type is an exact lowercase match: image/jpeg, image/png, image/webp or image/gif")
            size = len(data) * 3 // 4 - data[-2:].count("=")
            if size > max_bytes:
                self.limited("E023", path, f"image data is larger than the {max_bytes} byte limit",
                             "downscale and re-encode it")
                continue
            try:
                if not data:
                    raise ValueError("empty")
                base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError):
                self.err("E023", path, "image data is not valid base64", "send plain base64 without a data: prefix")

    # questions

    def check_questions(self) -> None:
        questions = self.req.get("questions")
        if not isinstance(questions, dict) or not questions:
            what = ("is missing" if questions is None else "is a list; use an object keyed by question id"
                    if isinstance(questions, list) else "is empty" if questions == {} else "must be an object")
            self.err("E002", "questions", f"questions {what}; the server returns 422")
            return
        cap = self.limits.values["questions"]
        if len(questions) > cap:
            self.limited("E003", "questions", f"{len(questions)} questions; the limit is {cap}",
                         "split into several requests (batch/filter)")
        for qid, q in questions.items():
            self.check_question(str(qid), q)
        self.cross_question(questions)

    def check_question(self, qid: str, q: Any) -> None:
        base = f"questions.{qid}"
        if not isinstance(q, dict):
            self.err("E004", base, "question is not an object; the server returns 422")
            return
        if "weight" in q:
            self.err("E026", f"{base}.weight", "per-question weight is silently ignored by the server (200 OK)",
                     "strip it and compute weights in code", fixed=True)
            if self.autofix:
                del q["weight"]
        kind = q.get("type")
        if kind is None:
            self.err("E004", base, "question type is missing; the server returns 422 union_tag_not_found",
                     "set type to noul, choice or score")
            return
        if kind not in QUESTION_TYPES:
            fix = TYPE_FIXES.get(kind) if isinstance(kind, str) else None
            self.err("E005", f"{base}.type", f"unknown type {kind!r}; the server returns 400 'Invalid request.'",
                     f"use {fix}" if fix else "use noul, choice or score", fixed=fix is not None)
            if fix is None:
                return
            if self.autofix:
                q["type"] = fix
            kind = fix
        self.kinds[qid] = kind
        key = "criteria"
        if kind != "noul" and "criteria" not in q and "options" in q:
            self.err("E006", f"{base}.options", "the field is criteria, not options; the server returns 422 missing criteria",
                     "rename options to criteria", fixed=True)
            key = "options"
            if self.autofix:
                for k in list(q):
                    q[("criteria" if k == "options" else k)] = q.pop(k)
                key = "criteria"
        crit = q.get(key)
        ins = q.get("instructions") if isinstance(q.get("instructions"), str) else ""
        new = {"noul": self.check_noul, "choice": self.check_choice, "score": self.check_score}[kind](qid, q, crit, ins)
        if new is not None and self.autofix:
            q["criteria"] = new
        if kind == "noul" or ins:
            self.check_instructions(base, kind, ins, new if new is not None else crit)

    def check_noul(self, qid: str, q: dict, crit: Any, ins: str) -> Any:
        base = f"questions.{qid}"
        if crit is None:
            self.warn("W101", base, "noul without criteria", "add criteria {true, false} that define both poles")
            return None
        if not isinstance(crit, dict):
            self.err("E017", f"{base}.criteria", "noul criteria must be an object {true, false}; "
                     "the server returns 422 model_attributes_type", "use {\"true\": ..., \"false\": ...}")
            return None
        out = {k: v for k, v in crit.items() if k in ("true", "false")}
        extra = [k for k in crit if k not in ("true", "false")]
        for k in extra:
            target = {"yes": "true", "no": "false"}.get(str(k).strip().lower())
            if target and target not in out:
                out[target] = crit[k]
        if extra:
            self.err("E027", f"{base}.criteria", f"noul criteria keys {extra} are silently dropped by the server "
                     "(only 'true' and 'false' are read)", "use 'true' and 'false' (yes -> true, no -> false)",
                     fixed=True)
        if not out:
            self.warn("W101", base, "noul without criteria", "add criteria {true, false} that define both poles")
        elif len(out) == 1:
            pole = next(iter(out))
            self.warn("W102", f"{base}.criteria", f"only the '{pole}' pole is described",
                      "describe both poles; name the near-miss in 'false'")
        return out if extra else None

    def check_choice(self, qid: str, q: dict, crit: Any, ins: str) -> Any:
        base = f"questions.{qid}"
        path = f"{base}.criteria"
        new = None
        if crit is None:
            self.err("E010", path, "choice criteria is missing; the server returns 422",
                     "give an object {option: description}")
            return None
        if isinstance(crit, list):
            fixable = all(isinstance(x, str) for x in crit)
            self.err("E011", path, "choice criteria is a list; the server returns 422 dict_type",
                     "use {label: label} and then describe each label" if fixable else "use an object {option: description}",
                     fixed=fixable)
            if not fixable:
                return None
            crit = new = {x: x for x in crit}
        elif not isinstance(crit, dict):
            self.err("E010", path, "choice criteria must be an object {option: description}")
            return None
        n = len(crit)
        if n == 0:
            self.err("E012", path, f"choice has no options; the server returns 400 'Choice question must have at "
                     f"least one choice: {qid}'", "add options, or short-circuit the call")
            return new
        limit = self.limits.values["choice_options"]
        over = n > limit
        if over:
            self.limited("E015", path, f"{n} options; the limit is {limit}", "pre-filter to a shortlist, or use a tree")
        cap = self.caps.get("max_choices")
        if cap is not None and n > cap and not over:
            self.capability("E028", path, f"{self.model} accepts at most {cap} choice options, got {n}",
                            "pre-filter the options")
        weak = [k for k, v in crit.items() if v is None or v == "" or isinstance(v, str) and (
            v.strip().lower() == str(k).strip().lower() or len(v.split()) < 3)]
        if weak:
            keys = [k for k in weak if crit[k] is not None and crit[k] != ""
                    and str(crit[k]).strip().lower() == str(k).strip().lower()]
            missing = [k for k in weak if crit[k] is None or crit[k] == ""]
            if len(keys) == len(weak):
                msg = "option descriptions equal their keys"
            elif len(missing) == len(weak):
                msg = "option descriptions are missing"
            else:
                msg = "option descriptions are missing, equal to their keys or shorter than 3 words"
            self.warn("W202", path, msg, "describe what inputs of each option look like")
        self.template_overlap(path, crit)
        if not any(is_escape(str(k).lower()) for k in crit):
            self.warn("W201", path, "no escape option; a choice cannot abstain",
                      f'add "{ESCAPE_KEY}": "{ESCAPE_TEXT}"')
            if ESCAPE_KEY not in crit and n < limit:
                new = {**crit, ESCAPE_KEY: ESCAPE_TEXT}
        if n > LATENCY_WARN:
            self.warn("W205", path, f"{n} options: choices above {LATENCY_WARN} take seconds under load",
                      "pre-filter to a shortlist (embeddings, keywords), then choose")
        elif n > PREFILTER_WARN and not PREFILTER_NOTE.search(ins):
            self.warn("W205", path, f"{n} options and no pre-filter note", "pre-filter to a shortlist, then choose")
        if AMBIGUITY_GATE.search(ins) and not {"ask", "proceed"} <= {str(k).lower() for k in crit}:
            self.warn("W206", f"{base}.instructions", "a domain choice is used as an ambiguity gate",
                      "ask a dedicated proceed | ask choice: 'would it be safe to act without asking?'")
        return new

    def template_overlap(self, path: str, crit: dict) -> None:
        texts = {str(k): v for k, v in crit.items() if isinstance(v, str) and v.strip() and not is_escape(str(k).lower())}
        groups: dict[str, list[str]] = {}
        for k, v in texts.items():
            t = _template(v, k)
            if len(t.replace("<id>", "").strip()) >= 3 and "<id>" in t:
                groups.setdefault(t, []).append(k)
        same = [ks for ks in groups.values() if len(ks) > 1]
        if same:
            self.warn("W203", path, f"options {', '.join(same[0])} share one description template that differs only "
                      "in an id", "give each option a content gloss, e.g. 'M2 (CI/CD pipeline): the fact is about this subject'")
            return
        items = [(k, _content(v)) for k, v in texts.items()]
        for i, (ka, wa) in enumerate(items):
            for kb, wb in items[i + 1:]:
                shared = wa & wb
                if len(shared) >= 2 and len(shared) >= 0.5 * min(len(wa), len(wb)):
                    self.warn("W204", path, f"options {ka} and {kb} overlap ({', '.join(sorted(shared)[:4])})",
                              "describe each option contrastively; say what it is not")
                    return

    def check_score(self, qid: str, q: dict, crit: Any, ins: str) -> Any:
        base = f"questions.{qid}"
        path = f"{base}.criteria"
        new = None
        if crit is None:
            self.err("E010", path, "score criteria is missing; the server returns 422", "give a list of levels, lowest first")
            return None
        if isinstance(crit, dict):
            try:
                keys = sorted(crit, key=int)
            except (TypeError, ValueError):
                keys = sorted(crit, key=str)
            self.err("E013", path, "score criteria is an object; the server returns 422 'Input should be a valid list'",
                     "use a list ordered lowest first", fixed=True)
            crit = new = [crit[k] for k in keys]
        elif not isinstance(crit, list):
            self.err("E013", path, "score criteria must be a list; the server returns 422", "use a list ordered lowest first")
            return None
        if not crit:
            self.err("E014", path, "score criteria is empty; the server returns 422 too_short", "give at least 2 levels")
            return new
        if len(crit) > MAX_SCORE_LEVELS:
            self.err("E016", path, f"{len(crit)} score levels; the maximum is {MAX_SCORE_LEVELS}",
                     f"reduce to {MAX_SCORE_LEVELS} levels")
        levels = [x if isinstance(x, str) else "" for x in crit]
        self.level_order(path, levels)
        for i, level in enumerate(levels):
            m = ESCAPE_CLAUSE.search(level)
            if m:
                self.warn("W302", f"{path}.{i}", f"level {i} holds an escape clause ('{m.group(0)}')",
                          "state clean, mutually exclusive levels with observable evidence")
        if len(levels) == 2 and all(re.split(r"\W+", x.strip().lower())[0] in ("yes", "no", "true", "false")
                                    for x in levels):
            self.warn("W303", path, "score used for a yes/no (2 levels named yes/no)", "use a noul question")
        if len(levels) >= 2 and all(_bare(x) for x in levels):
            last = re.split(r"[\s:]+", levels[-1].strip())[0] or "top"
            example = EVIDENCE_EXAMPLE.get(last.lower(), "what you would observe at this level")
            self.warn("W304", path, f"levels '{'/'.join(x.strip() for x in levels)}' carry no observable evidence",
                      f"describe each level, e.g. '{last}: {example}'")
        return new

    def level_order(self, path: str, levels: list[str]) -> None:
        firsts = [(re.findall(r"[a-z]+", x.lower()) or [""])[0] for x in levels]
        if len(set(x.strip().lower() for x in levels if x.strip())) < len([x for x in levels if x.strip()]):
            self.warn("W301", path, "two levels are identical; levels must be mutually exclusive",
                      "give every level its own evidence")
            return
        for scale in TIERS:
            rank = {w: i for i, tier in enumerate(scale) for w in tier}
            ranks = [rank[w] for w in firsts if w in rank]
            if len(ranks) >= 2:
                if ranks != sorted(ranks):
                    self.warn("W301", path, "levels are not ordered lowest first by their scale words",
                              "order the list from the lowest to the highest level")
                return

    def check_instructions(self, base: str, kind: str, ins: str, crit: Any) -> None:
        path = f"{base}.instructions"
        words = len(ins.split())
        defined = bool(DEFINED.search(ins)) or words > 14 or (
            kind == "noul" and isinstance(crit, dict) and bool(crit.get("true")))
        if kind == "noul":
            m = NEGATION.search(ins)
            if m:
                self.warn("W103", path, f"negated claim ('{m.group(0)}')",
                          "ask the positive, literal claim; ask negation-prone flags both ways")
            if words >= 5 and COMPOUND.search(ins):
                self.warn("W104", path, "compound claim: two predicates in one question", "split into one question per fact")
            vague = VAGUE.search(ins)
            if vague and not defined:
                self.warn("W105", path, f"vague quality claim ('{vague.group(0)}') without a definition",
                          "define the claim by observable evidence and add criteria.true / criteria.false")
            elif not vague and not defined and OPINION_START.match(ins) and words <= OPINION_WORDS:
                self.warn("W106", path, f"'{ins}' is an opinion without a definition",
                          "define what makes it true and add criteria.true / criteria.false")
        elif words <= WH_WORDS and WH_START.match(ins):
            m = WHICH_NOUN.fullmatch(ins.strip())
            fix = f"'Which {m.group(1)} should own this?'" if m else "name the item and the purpose of the question"
            self.warn("W106", path, f"'{ins}' does not say for what", fix)

    def cross_question(self, questions: dict) -> None:
        ids = [str(q) for q in questions]
        valid = {qid: q for qid, q in ((str(k), v) for k, v in questions.items()) if isinstance(q, dict)}
        text = {qid: q.get("instructions") if isinstance(q.get("instructions"), str) else "" for qid, q in valid.items()}
        scores = [qid for qid in ids if self.kinds.get(qid) == "score"]
        if len(scores) >= 2:
            gate = any(self.kinds.get(qid) == "noul" and "evidence" in (qid + text[qid]).lower() for qid in valid)
            if not gate:
                for qid in scores:
                    self.warn("W305", f"questions.{qid}", "score dimension has no evidence gate in a rubric set",
                              "add a noul 'has_evidence' for the dimension and compose in code")
        if len(ids) > 10 and len({_template(t, qid) for qid, t in text.items()}) > 10:
            self.warn("W401", "questions", f"{len(ids)} questions with heterogeneous topics",
                      "one decision per request, or pack items that share one template")
        nouls = [qid for qid in ids if self.kinds.get(qid) == "noul"]
        for qid in nouls:
            if BLOCKING_ID.search(qid):
                sib = next((o for o in nouls if o != qid and len(_content(text[o]) & _content(text[qid])) >= 2), None)
                if sib:
                    self.warn("W401", f"questions.{qid}", f"blocking claim co-asked with the overlapping '{sib}'",
                              "ask a claim that can block in its own request, or only with unrelated siblings")
        self.packed_ids(ids, text)

    def packed_ids(self, ids: list[str], text: dict[str, str]) -> None:
        groups: dict[str, list[str]] = {}
        for qid in ids:
            stem = re.sub(r"[_-]?\d+$", "", qid) if re.search(r"\d$", qid) else qid.rsplit("_", 1)[0] if "_" in qid else ""
            if len(stem) >= 3 and qid != stem:
                groups.setdefault(stem, []).append(qid)
        for stem, members in groups.items():
            if len(members) < 3:
                continue
            same = len({text.get(m, "").strip().lower() for m in members}) == 1
            if same and not any(re.search(rf"\b{re.escape(m[len(stem):].strip('_-'))}\b", text.get(m, ""), re.I)
                                for m in members):
                self.warn("W504", "questions", f"ids {', '.join(members[:3])}... look like items but the instructions "
                          "do not mention them", "put the item id or its content in each question's instructions")

    def check_options_after(self) -> None:
        req = self.req
        think = _int(req.get("think")) or 0
        samples = _int(req.get("samples")) or 0
        if think > THINK_WARN:
            self.warn("W402", "think", f"think {think} exceeds {THINK_WARN}; 256-512 is enough for lookahead",
                      "lower think; repeat the read once if it decides a gate")
        kinds = [self.kinds.get(str(q)) for q in req["questions"]] if isinstance(req.get("questions"), dict) else []
        if _flag(req.get("sequential")) and 0 < len(kinds) <= 10 and all(k in ("noul", "choice") for k in kinds):
            self.warn("W403", "sequential", f"sequential with {len(kinds)} noul/choice questions is one chunk: a no-op",
                      "drop sequential; it matters above 10 questions")
        if self.profile == "gate" and (think > HOT_PATH_MAX or samples > HOT_PATH_MAX):
            self.warn("W404", "think" if think > HOT_PATH_MAX else "samples",
                      "think or samples above 4 on a gate hot path", "use samples: 1 and no think on hot paths")
        concurrency = _int(req.get("concurrency"))
        if concurrency is not None and concurrency > 1 and self.limits.backend in ("mlx", "unknown"):
            self.warn("W405", "concurrency", f"concurrency {concurrency} on a {self.limits.backend} backend: reads serialise",
                      "use concurrency 1 unless status shows vLLM")
        if any(f in req for f in BATCH_FIELDS) and think > 0:
            self.warn("W406", "think", "think in a batch run: each row is non-reproducible, resumed rows may differ",
                      "run a second pass over the review queue instead")

    def check_state_text(self) -> None:
        state = self.req.get("state")
        blocks: list[str] = []
        if isinstance(state, str):
            blocks = [b for b in re.split(r"\n\s*\n", state.strip()) if len(b.strip()) >= 20]
        elif isinstance(state, list):
            blocks = [b for b in state if isinstance(b, str) and len(b.strip()) >= 20]
        if len(blocks) >= 2 and not any(SECTION_LABEL.match(b) for b in blocks):
            self.warn("W501", "state", f"state holds {len(blocks)} texts without section labels",
                      "label each text, e.g. 'DIFF:' and 'MESSAGE:'")
        if isinstance(state, (str, list, dict)) and math.ceil(_json_len(state) / CHARS_PER_TOKEN) > STATE_TOKENS_WARN:
            self.warn("W503", "state", f"state is over {STATE_TOKENS_WARN} tokens (prefill runs ~1.3k tokens/s)",
                      "collapse long text into clusters, or send only the relevant part")
        questions = self.req.get("questions")
        if not isinstance(questions, dict):
            return
        for qid, q in questions.items():
            if not isinstance(q, dict):
                continue
            parts = [q.get("instructions")]
            crit = q.get("criteria")
            parts += list(crit.values()) if isinstance(crit, dict) else crit if isinstance(crit, list) else []
            for part in parts:
                if isinstance(part, str) and (UNTRUSTED_MARKER.search(part) or len(part) > UNTRUSTED_CHARS):
                    self.warn("W502", f"questions.{qid}", "untrusted-looking text in instructions/criteria",
                              "put untrusted text in state, prefixed with its source")
                    break

    def check_prompt(self) -> None:
        tokens = _prompt_tokens(self.req)
        cap = self.caps.get("max_prompt_tokens") or self.limits.values.get("prompt_tokens")
        if cap is not None:
            if tokens > cap:
                self.limited("E025", "state", f"estimated prompt of {tokens} tokens exceeds the limit of {cap}",
                             "shorten the state or send the relevant part only")
        elif tokens > MLX_PROMPT_TOKENS:
            self.warn("E025", "state", f"estimated prompt of {tokens} tokens exceeds {MLX_PROMPT_TOKENS:,} (MLX) "
                      f"and {'also ' if tokens > VLLM_PROMPT_TOKENS else 'may exceed '}{VLLM_PROMPT_TOKENS:,} (vLLM); "
                      "the backend is unknown", "shorten the state or send the relevant part only", limited=True)


def _bare(level: str) -> bool:
    s = level.strip()
    return bool(re.fullmatch(r"\d+", s)) or (":" not in s and len(s.split()) <= 2)


def lint_request(request: Mapping[str, Any], *, limits: Limits, profile: str = "default",
                 autofix: bool = True, require_state: bool = True) -> LintReport:
    return _Linter(limits, profile, autofix, require_state).run(request)
