#!/usr/bin/env python3
"""Offline functional validator for the OpenJev skills (component F of the skills evaluation).

Every labelled block in every SKILL.md and reference file is validated in-process with the server's own
code against a stub OpenJev (mcp/tests/stubs.py). No network and no model: the stub engine answers, and the
server config points at a fake host, so OPENJEV_BASE_URL is never read.

    .venv/bin/python mcp/tests/skills_eval/offline_check.py [--json] [--skills-dir DIR] [--mcp-url URL]

Checks (each counts 1; F = passed / total):
  openjev-call: <tool>      validate_args; read tools run on the stub and the body that reached the engine is
                            linted with profile strict (no errors); batch dry run with fixture files; recipe
                            dry run whose built requests lint clean; filter criterion holds {id}; batch_results
                            against a stub-seeded output; lint verdict equals the labelled result next to it
  openjev-result: <tool>    validate_output
  openjev-questions         lint (strict) with a dummy state
  openjev-recipe-inputs: id the recipe's input_schema, then a dry run
  openjev-items: <fmt>      the batch importer reads it (row_count > 0)
  openjev-example           JSON parses
  ## Edges rows             From path exists in the From tool's output schema, To path in the To tool's input schema
  openjev:// URIs           resolve to something the server serves
  tool-io.md                gen_tool_io.py --check exits 0
Lint warnings are counted per skill (reported, not failures). --mcp-url replays lint verdicts against a live
server (diagnostic, unscored).
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import skill_lib as S  # noqa: E402

S.ensure_server_importable()
for _p in (str(S.REPO), str(S.MCP_DIR / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stubs  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402

from openjev_mcp import wire  # noqa: E402
from openjev_mcp.batch.importers import import_source  # noqa: E402
from openjev_mcp.config import Config  # noqa: E402
from openjev_mcp.errors import ToolError  # noqa: E402
from openjev_mcp.http import OpenJevClient  # noqa: E402
from openjev_mcp.limits import LimitsCache  # noqa: E402
from openjev_mcp.lint import lint_request  # noqa: E402
from openjev_mcp.progress import ProgressEmitter  # noqa: E402
from openjev_mcp.recipes import engine as recipe_engine  # noqa: E402
from openjev_mcp.schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS  # noqa: E402
from openjev_mcp.tools import ToolContext  # noqa: E402
from openjev_mcp.tools.dispatch import call_tool, tools_for  # noqa: E402
from openjev_mcp.validate import validate_args, validate_output  # noqa: E402

READ_TOOLS = ("ask", "yes_no", "classify", "score", "ask_image")
DATA_EXTS = (".csv", ".tsv", ".jsonl", ".json", ".txt", ".log", ".md", ".ndjson")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
DEFAULT_QUESTIONS = {
    "urgent": {"type": "noul", "instructions": "The customer needs a reply today.",
               "criteria": {"true": "money lost, an outage or a deadline", "false": "no urgency stated"}},
    "dept": {"type": "choice", "instructions": "Which team owns this?",
             "criteria": {"billing": "charges and refunds", "technical": "bugs and outages", "other": "anything else"}},
    "sev": {"type": "score", "instructions": "How severe is the problem?",
            "criteria": ["no impact", "minor impact", "major impact"]},
}


# ---------------------------------------------------------------- fixtures

def _png(path: Path) -> None:
    w = h = 64
    raw = b"".join(b"\x00" + bytes([200, 200, 200]) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _columns(spec: dict) -> list[str]:
    cols: list[str] = []
    for c in (spec.get("id_field"),):
        if c:
            cols.append(c)
    tpl = spec.get("state_template") or ""
    for name in re.findall(r"\{([^{}]+)\}", tpl):
        if name != "id" and name not in cols:
            cols.append(name)
    sf = spec.get("state_field")
    if sf and sf != "*" and sf not in cols:
        cols.append(sf)
    if not cols or (len(cols) == 1 and cols[0] == spec.get("id_field")):
        cols.append("text")
    return cols


def _write_items(path: Path, spec: dict, rows: int = 5, offset: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    fmt = spec.get("format", "auto")
    cols = _columns(spec)
    recs = [{c: f"{c} value {i} of the sample row" for c in cols} for i in range(offset + 1, offset + rows + 1)]
    for i, r in enumerate(recs, offset + 1):
        if spec.get("id_field"):
            r[spec["id_field"]] = f"R{i}"
    if fmt == "lines":
        path.write_text("\n".join(f"line {i}: sample log entry" for i in range(1, rows + 1)) + "\n")
    elif fmt == "blocks":
        path.write_text("\n\n".join(f"block {i} line one\n  block {i} line two" for i in range(1, rows + 1)) + "\n")
    elif ext in (".csv", ".tsv"):
        sep = "," if ext == ".csv" else "\t"
        path.write_text("\n".join([sep.join(cols), *(sep.join(r[c] for c in cols) for r in recs)]) + "\n")
    elif ext in (".jsonl", ".ndjson"):
        path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    elif ext == ".json":
        key = spec.get("array_key")
        path.write_text(json.dumps({key: recs} if key else recs))
    else:
        path.write_text("\n".join(f"line {i}: sample text" for i in range(1, rows + 1)) + "\n")


def _is_placeholder(s) -> bool:
    return isinstance(s, str) and (s.startswith("/abs/") or "/path/to/" in s)


class Workdir:
    """A per-block directory under the temp root; maps placeholder paths into it."""

    def __init__(self, root: Path, n: int):
        self.dir = root / f"b{n}"
        self.dir.mkdir(parents=True, exist_ok=True)

    def map(self, s: str) -> Path:
        rel = s.split("/path/to/", 1)[1] if "/path/to/" in s else Path(s).name
        return self.dir / rel.lstrip("/")


# ---------------------------------------------------------------- schema path resolution (Edges)

def _deref(node, root: dict, depth: int = 0):
    while isinstance(node, dict) and "$ref" in node and depth < 20:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return {}
        cur = root
        for part in ref[2:].split("/"):
            cur = cur.get(part, {}) if isinstance(cur, dict) else {}
        node, depth = cur, depth + 1
    return node if isinstance(node, dict) else {}


def _variants(node, root: dict) -> list[dict]:
    node = _deref(node, root)
    out = [node]
    for key in ("oneOf", "anyOf", "allOf"):
        for b in node.get(key, []) or []:
            out.extend(_variants(b, root))
    return out


_OPEN = object()


def _pick(node, name: str, root: dict):
    """Child schema called `name`, _OPEN when the node accepts any key, None when it cannot exist."""
    vs = _variants(node, root)
    for v in vs:
        props = v.get("properties")
        if isinstance(props, dict) and name in props:
            return props[name]
    for v in vs:
        ap = v.get("additionalProperties")
        if isinstance(ap, dict):
            return ap if ap else _OPEN
    has_props = any(isinstance(v.get("properties"), dict) and v["properties"] for v in vs)
    closed = any(v.get("additionalProperties") is False for v in vs)
    if not has_props and not closed:
        return _OPEN
    return None


def resolve_path(schema: dict, tokens: list[str]) -> str | None:
    """None when the dotted path exists in the schema, else the reason."""
    node = schema
    for tok in tokens:
        name, arr = (tok[:-2], True) if tok.endswith("[]") else (tok, False)
        if node is _OPEN:
            return None
        child = _pick(node, name, schema) if name else node
        if child is None:
            return f"no field {name!r}"
        if child is _OPEN:
            return None
        node = child
        if arr:
            vs = _variants(node, schema)
            items = next((v["items"] for v in vs if isinstance(v.get("items"), dict)), None)
            if items is None:
                if any(v.get("type") == "array" for v in vs):
                    return None
                return f"{name!r} is not an array"
            node = items if items else _OPEN
    return None


# ---------------------------------------------------------------- the checker

class Checker:
    def __init__(self, skills: list, tmp: Path):
        self.skills = skills
        self.tmp = tmp
        self.checks: list[dict] = []
        self.warn: Counter = Counter()
        self.warn_codes: dict[str, Counter] = {}
        self.diag_lints: list[dict] = []     # (label, request) pairs for the --mcp-url diagnostic
        self.n = 0
        self.seed_n = 0
        self.engine = stubs.StubEngine()
        config = Config(base_url="http://openjev.invalid", roots=(str(tmp.resolve()),), retries=0)
        client = OpenJevClient(config, transport=stubs.asgi_transport(stubs.openjev_app(engine=self.engine)))
        self.ctx = ToolContext(config, client, LimitsCache(client), ProgressEmitter(None), None)

    # --- bookkeeping
    def rec(self, skill: str, file: str, line: int, kind: str, ok: bool, msg: str = "") -> bool:
        self.checks.append({"skill": skill, "file": file, "line": line, "kind": kind, "ok": bool(ok),
                            "message": "" if ok else msg})
        return bool(ok)

    def warnings(self, skill: str, report) -> None:
        for f in getattr(report, "warnings", []) or []:
            self.warn[skill] += 1
            self.warn_codes.setdefault(skill, Counter())[getattr(f, "code", "?")] += 1

    async def call(self, tool: str, args: dict) -> tuple[bool, dict, str]:
        res = await call_tool(self.ctx, tool, args)
        text = ""
        try:
            text = res["content"][0]["text"]
        except Exception:
            pass
        return (not res.get("isError")), res.get("structuredContent") or {}, text[:300]

    async def strict_lint(self, skill: str, bodies: list[dict], require_state: bool = True):
        limits = await self.ctx.limits.get()
        errors: list[str] = []
        for b in bodies:
            rep = lint_request(b, limits=limits, profile="strict", autofix=False, require_state=require_state)
            self.warnings(skill, rep)
            errors += [f"{f.code} {f.path}: {f.message}" for f in rep.errors]
        return errors

    # --- seeding for batch_results / calibrate from_batch
    async def seed_output(self, questions: dict | None) -> str:
        self.seed_n += 1
        wd = Workdir(self.tmp, 9000 + self.seed_n)
        out = wd.dir / "seed.jsonl"
        items = [{"id": f"t{i}", "state": f"TICKET: sample ticket number {i} about billing"} for i in range(1, 9)]
        ok, _, text = await self.call("batch", {"items": items, "questions": questions or DEFAULT_QUESTIONS,
                                                "output_path": str(out)})
        if not ok and questions:
            ok, _, text = await self.call("batch", {"items": items, "questions": DEFAULT_QUESTIONS,
                                                    "output_path": str(out)})
        if not ok:
            raise RuntimeError(f"could not seed a batch output: {text}")
        return str(out)

    # --- argument materialisation
    def materialize(self, tool: str, args: dict, wd: Workdir) -> tuple[dict, list[str]]:
        a = copy.deepcopy(args)
        notes: list[str] = []

        def out_path(s):
            if not _is_placeholder(s):
                return s
            p = wd.map(s)
            p.parent.mkdir(parents=True, exist_ok=True)
            return str(p)

        if tool == "batch":
            spec = a.get("items_file")
            if isinstance(spec, dict) and _is_placeholder(spec.get("path")):
                new = []
                for key in ("path", "also"):
                    vals = spec.get(key)
                    for v in ([vals] if key == "path" else (vals or [])):
                        p = wd.map(v)
                        if p.suffix.lower() not in DATA_EXTS:
                            p = p.with_suffix(".txt") if not spec.get("format") else p
                        _write_items(p, spec, offset=100 * len(new))
                        new.append(str(p))
                spec["path"] = new[0]
                if "also" in spec:
                    spec["also"] = new[1:]
            if isinstance(a.get("output_path"), str):
                a["output_path"] = out_path(a["output_path"])
            for e in a.get("export") or []:
                if isinstance(e, dict) and isinstance(e.get("path"), str):
                    e["path"] = out_path(e["path"])
            for im in a.get("images") or []:
                if isinstance(im, dict) and _is_placeholder(im.get("path")):
                    p = wd.map(im["path"])
                    _png(p)
                    im["path"] = str(p)
            if a.get("cursor"):
                a.pop("cursor")
                notes.append("cursor dropped for the dry run")
            a["dry_run"] = True
        elif tool == "ask_image":
            for im in a.get("images") or []:
                if isinstance(im, dict) and _is_placeholder(im.get("path")):
                    p = wd.map(im["path"])
                    _png(p)
                    im["path"] = str(p)

        def walk(o):
            if isinstance(o, dict):
                return {k: walk(v) for k, v in o.items()}
            if isinstance(o, list):
                return [walk(v) for v in o]
            if _is_placeholder(o):
                p = wd.map(o)
                if p.suffix.lower() in IMAGE_EXTS:
                    _png(p)
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                return str(p)
            return o

        if tool in ("batch_results", "calibrate"):
            return a, notes          # handled by the caller (seeded outputs)
        return walk(a), notes

    # --- per-block checks
    async def check_call(self, sk, b: S.Block, nearest_q: dict | None, nxt: S.Block | None) -> None:
        tool, f, ln = b.tool, b.file, b.line
        if not isinstance(b.data, dict):
            self.rec(sk.name, f, ln, f"call:{tool}:json", False, "block is not a JSON object")
            return
        err = validate_args(tool, b.data)
        if not self.rec(sk.name, f, ln, f"call:{tool}:args", err is None,
                        f"{getattr(err, 'path', '')}: {getattr(err, 'message', '')}"):
            return
        self.n += 1
        wd = Workdir(self.tmp, self.n)
        try:
            await self._runtime(sk, b, wd, nearest_q, nxt)
        except Exception as e:                                  # a crash in the checker is a failed check, loudly
            self.rec(sk.name, f, ln, f"call:{tool}:run", False, f"{type(e).__name__}: {e}")

    async def _runtime(self, sk, b: S.Block, wd: Workdir, nearest_q, nxt) -> None:
        tool, f, ln, name = b.tool, b.file, b.line, sk.name
        args, _ = self.materialize(tool, b.data, wd)
        if tool in READ_TOOLS:
            n0 = len(self.engine.calls)
            ok, _, text = await self.call(tool, args)
            if not self.rec(name, f, ln, f"call:{tool}:run", ok, text):
                return
            bodies = []
            for c in self.engine.calls[n0:]:
                model = (c["options"] or {}).get("model") or self.ctx.config.model
                imgs = [i["image_url"]["url"] if isinstance(i, dict) and "image_url" in i else i
                        for i in (c["images"] or [])]
                bodies.append(wire.build_body(model, c["state"], c["questions"], c["options"], imgs))
            errs = await self.strict_lint(name, bodies)
            self.rec(name, f, ln, f"call:{tool}:lint-strict", not errs, "; ".join(errs[:3]))
        elif tool == "lint":
            ok, out, text = await self.call("lint", args)
            self.rec(name, f, ln, "call:lint:run", ok, text)
            if ok:
                for fnd in out.get("warnings", []):
                    self.warn[name] += 1
                    self.warn_codes.setdefault(name, Counter())[fnd.get("code", "?")] += 1
                self.diag_lints.append({"where": f"{f}:{ln}", "args": args, "valid": out.get("valid")})
                if nxt is not None and nxt.label == "result" and nxt.tool == "lint" and isinstance(nxt.data, dict) \
                        and "valid" in nxt.data and nxt.line - ln < 40:
                    self.rec(name, f, ln, "call:lint:verdict-matches-result", nxt.data["valid"] == out.get("valid"),
                             f"result block says valid={nxt.data['valid']}, the linter says valid={out.get('valid')}")
        elif tool == "batch":
            ok, out, text = await self.call("batch", args)
            self.rec(name, f, ln, "call:batch:dry-run", ok, text)
            if ok:
                errs = validate_output("batch", out)
                self.rec(name, f, ln, "call:batch:dry-run-output", not errs, "; ".join(errs[:2]))
                if args.get("questions") is not None:
                    errs = await self.strict_lint(name, [wire.build_body(self.ctx.config.model, "TEXT: sample", args["questions"],
                                                                         args.get("options"))])
                    self.rec(name, f, ln, "call:batch:lint-strict", not errs, "; ".join(errs[:3]))
        elif tool == "recipe":
            await self._recipe(name, f, ln, b.data["recipe"], b.data["inputs"], {**args, "dry_run": True},
                               kind="call:recipe")
        elif tool == "filter":
            crit = b.data.get("criterion")
            self.rec(name, f, ln, "call:filter:criterion-has-id", isinstance(crit, str) and "{id}" in crit,
                     f"criterion must be a string containing {{id}}, got {crit!r}")
            ok, out, text = await self.call("filter", args)
            self.rec(name, f, ln, "call:filter:run", ok, text)
        elif tool == "batch_results":
            a = copy.deepcopy(b.data)
            a["path"] = await self.seed_output(nearest_q)
            if isinstance(a.get("compare_to"), dict):
                a["compare_to"]["path"] = await self.seed_output(nearest_q)
            if isinstance(a.get("export"), dict) and _is_placeholder(a["export"].get("path")):
                p = wd.map(a["export"]["path"])
                p.parent.mkdir(parents=True, exist_ok=True)
                a["export"]["path"] = str(p)
            ok, out, text = await self.call("batch_results", a)
            self.rec(name, f, ln, "call:batch_results:run", ok, text)
        elif tool == "calibrate":
            a = copy.deepcopy(b.data)
            if isinstance(a.get("store"), str):
                a["store"] = str(wd.map(a["store"])) if _is_placeholder(a["store"]) else a["store"]
                Path(a["store"]).parent.mkdir(parents=True, exist_ok=True)
            if "case_file" in a or "from_batch" in a:
                return                                          # file-driven forms: argument validation is the check
            ok, out, text = await self.call("calibrate", a)
            self.rec(name, f, ln, "call:calibrate:run", ok, text)
        elif tool == "compile":
            ok, out, text = await self.call("compile", args)
            self.rec(name, f, ln, "call:compile:run", ok, text)
        elif tool == "status":
            ok, out, text = await self.call("status", args)
            self.rec(name, f, ln, "call:status:run", ok, text)
        # generate: argument validation only (it needs the chat model)

    async def _recipe(self, name, f, ln, rid, inputs, call_args, kind) -> None:
        ok, out, text = await self.call("recipe", call_args)
        if not self.rec(name, f, ln, f"{kind}:dry-run", ok, text):
            return
        built = out.get("built_requests") or []
        errs = await self.strict_lint(name, [r for r in built if isinstance(r, dict)])
        self.rec(name, f, ln, f"{kind}:built-requests-lint", not errs, "; ".join(errs[:3]))

    async def check_result(self, sk, b: S.Block) -> None:
        errs = validate_output(b.tool, b.data) if b.data is not None else ["not parsed"]
        self.rec(sk.name, b.file, b.line, f"result:{b.tool}", not errs, "; ".join(errs[:3]))

    async def check_questions(self, sk, b: S.Block) -> None:
        if not isinstance(b.data, dict):
            self.rec(sk.name, b.file, b.line, "questions", False, "block is not a JSON object")
            return
        ok, out, text = await self.call("lint", {"questions": b.data, "state": "TEXT: sample", "profile": "strict",
                                                 "autofix": False})
        if not ok:
            self.rec(sk.name, b.file, b.line, "questions:lint", False, text)
            return
        for fnd in out.get("warnings", []):
            self.warn[sk.name] += 1
            self.warn_codes.setdefault(sk.name, Counter())[fnd.get("code", "?")] += 1
        errs = [f"{e['code']} {e['path']}: {e['message']}" for e in out.get("errors", [])]
        self.rec(sk.name, b.file, b.line, "questions:lint", not errs, "; ".join(errs[:3]))
        self.diag_lints.append({"where": f"{b.file}:{b.line}",
                                "args": {"questions": b.data, "state": "TEXT: sample", "profile": "strict", "autofix": False},
                                "valid": out.get("valid")})

    async def check_recipe_inputs(self, sk, b: S.Block) -> None:
        rid = b.recipe
        try:
            rec = recipe_engine.load_builtin(rid)
        except recipe_engine.RecipeError as e:
            self.rec(sk.name, b.file, b.line, "recipe-inputs:id", False, str(e))
            return
        errs = sorted(Draft202012Validator(rec.input_schema).iter_errors(b.data), key=lambda e: list(map(str, e.path)))
        ok = self.rec(sk.name, b.file, b.line, "recipe-inputs:schema", not errs,
                      "; ".join(f"{'.'.join(map(str, e.absolute_path)) or '$'}: {e.message}" for e in errs[:2]))
        if ok:
            self.n += 1
            wd = Workdir(self.tmp, self.n)
            inputs = self.materialize("recipe", {"inputs": b.data}, wd)[0]["inputs"]
            await self._recipe(sk.name, b.file, b.line, rid, inputs,
                               {"recipe": rid, "inputs": inputs, "dry_run": True}, kind="recipe-inputs")

    def check_items(self, sk, b: S.Block) -> None:
        fmt = b.arg
        ext = {"csv": ".csv", "tsv": ".tsv", "jsonl": ".jsonl", "json": ".json", "lines": ".txt", "blocks": ".txt"}[fmt]
        self.n += 1
        p = Workdir(self.tmp, self.n).dir / f"sample{ext}"
        p.write_text(b.text + "\n")
        spec = {"path": str(p)}
        if fmt in ("lines", "blocks"):
            spec["format"] = fmt
        try:
            res = import_source(spec, self.ctx.config, max_items=5000)
        except ToolError as e:
            self.rec(sk.name, b.file, b.line, f"items:{fmt}", False, f"{e.code}: {e.message}")
            return
        rows = res.report.get("row_count", len(res.items))
        self.rec(sk.name, b.file, b.line, f"items:{fmt}", rows > 0, f"importer read {rows} rows")

    # --- driver
    async def run_skill(self, sk) -> None:
        blocks = sk.blocks()
        for i, b in enumerate(blocks):
            if b.label_error:
                self.rec(sk.name, b.file, b.line, "label", False, b.label_error)
                continue
            if b.label is None:
                continue
            if b.lang == "json" and b.label in ("call", "result", "questions", "recipe-inputs", "example"):
                if not self.rec(sk.name, b.file, b.line, f"{b.label}:json", b.error is None, f"invalid JSON: {b.error}"):
                    continue
            if b.label == "call":
                nearest_q = None
                for prev in reversed(blocks[:i]):
                    if prev.file == b.file and prev.label == "call" and prev.tool == "batch" \
                            and isinstance(prev.data, dict) and isinstance(prev.data.get("questions"), dict):
                        nearest_q = prev.data["questions"]
                        break
                nxt = next((x for x in blocks[i + 1:] if x.file == b.file and x.label), None)
                await self.check_call(sk, b, nearest_q, nxt)
            elif b.label == "result":
                await self.check_result(sk, b)
            elif b.label == "questions":
                await self.check_questions(sk, b)
            elif b.label == "recipe-inputs":
                await self.check_recipe_inputs(sk, b)
            elif b.label == "items":
                self.check_items(sk, b)

    def check_uris(self, sk) -> None:
        for uri, f, ln in sk.uris():
            why = S.uri_problem(uri)
            self.rec(sk.name, f, ln, "uri", why is None, f"{uri}: {why}")

    def check_edges(self, sk) -> None:
        for p in sk.files():
            for e in S.parse_edges(p.read_text(encoding="utf-8")):
                f, ln = sk.relfile(p), e["line"]
                for side, cell in (("from", e["from"]), ("to", e["to"])):
                    if cell is None:
                        self.rec(sk.name, f, ln, f"edge:{side}", False, "cell has no backticked path")
                        continue
                    if cell.startswith("openjev://"):
                        why = S.uri_problem(cell)
                        self.rec(sk.name, f, ln, f"edge:{side}", why is None, f"{cell}: {why}")
                        continue
                    tool, _, rest = cell.partition(".")
                    if tool not in S.TOOLS:
                        self.rec(sk.name, f, ln, f"edge:{side}", False, f"{cell}: {tool!r} is not an openjev tool")
                        continue
                    schema = (OUTPUT_SCHEMAS if side == "from" else INPUT_SCHEMAS)[tool]
                    why = resolve_path(schema, [t for t in rest.split(".") if t]) if rest else None
                    which = "output" if side == "from" else "input"
                    self.rec(sk.name, f, ln, f"edge:{side}", why is None, f"{cell}: not in the {tool} {which} schema ({why})")

    def check_tool_io(self) -> None:
        hub = next((s for s in self.skills if s.is_hub), None)
        if hub is None or not (hub.dir / "references" / "tool-io.md").is_file():
            return
        gen = HERE / "gen_tool_io.py"
        if not gen.is_file():
            return
        target = hub.dir / "references" / "tool-io.md"
        proc = subprocess.run([sys.executable, str(gen), "--check", "--out", str(target)], capture_output=True, text=True,
                              timeout=120)
        self.rec(hub.name, S.rel(target, hub.root), 1, "tool-io-sync", proc.returncode == 0,
                 (proc.stderr or proc.stdout).strip()[:300])

    async def run_all(self) -> None:
        await self.ctx.limits.get()
        for sk in self.skills:
            await self.run_skill(sk)
            self.check_uris(sk)
            self.check_edges(sk)
        self.check_tool_io()


# ---------------------------------------------------------------- live diagnostic (unscored)

async def _live_diag(url: str, pairs: list[dict]) -> dict:
    out = {"url": url, "compared": 0, "mismatches": [], "error": None}
    try:
        from mcp import ClientSession
        try:
            from mcp.client.streamable_http import streamable_http_client as connect
        except ImportError:                                      # older mcp SDKs
            from mcp.client.streamable_http import streamablehttp_client as connect

        async with connect(url) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                for p in pairs:
                    res = await session.call_tool("lint", p["args"])
                    sc = getattr(res, "structuredContent", None) or getattr(res, "structured_content", None)
                    if sc is None:
                        sc = json.loads(res.content[0].text)
                    live = sc.get("valid")
                    out["compared"] += 1
                    if live != p["valid"]:
                        out["mismatches"].append({"where": p["where"], "in_process": p["valid"], "live": live})
    except Exception as e:                                       # diagnostic only
        subs = getattr(e, "exceptions", None)
        while subs:                                              # unwrap anyio task-group groups
            e = subs[0]
            subs = getattr(e, "exceptions", None)
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------- entry points

def run(skills_dir: str | Path | None = None, mcp_url: str | None = None, root: Path | None = None) -> dict:
    """Run every F check; returns {component, score, passed, total, checks, failures, lint_warnings, by_skill}."""
    skills = S.discover(skills_dir, root)
    tmp = Path(tempfile.mkdtemp(prefix="oj-offline-")).resolve()
    try:
        chk = Checker(skills, tmp)
        asyncio.run(chk.run_all())
        diag = asyncio.run(_live_diag(mcp_url, chk.diag_lints)) if mcp_url else None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    checks = chk.checks
    passed = sum(c["ok"] for c in checks)
    by_skill: dict[str, dict] = {}
    for c in checks:
        d = by_skill.setdefault(c["skill"], {"passed": 0, "total": 0})
        d["total"] += 1
        d["passed"] += c["ok"]
    result = {
        "component": "offline", "score": (passed / len(checks)) if checks else 0.0, "passed": passed, "total": len(checks),
        "skills": [s.name for s in skills], "by_skill": by_skill,
        "failures": [c for c in checks if not c["ok"]], "checks": checks,
        "lint_warnings": {k: {"count": v, "codes": dict(chk.warn_codes.get(k, {}))} for k, v in sorted(chk.warn.items())},
    }
    if diag is not None:
        result["live_diagnostic"] = diag
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    ap.add_argument("--skills-dir", help="skills directory to check (default: the plugin's skills)")
    ap.add_argument("--mcp-url", help="replay lint verdicts against a live MCP server (unscored diagnostic)")
    a = ap.parse_args(argv)
    res = run(a.skills_dir, a.mcp_url)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"offline F = {res['score']:.4f}  ({res['passed']}/{res['total']} checks)")
        for k, v in res["by_skill"].items():
            print(f"  {k:32s} {v['passed']}/{v['total']}")
        for c in res["failures"]:
            print(f"FAIL {c['file']}:{c['line']} [{c['kind']}] {c['message']}")
        if res["lint_warnings"]:
            print("lint warnings (not failures): " + ", ".join(f"{k}={v['count']}" for k, v in res["lint_warnings"].items()))
        if "live_diagnostic" in res:
            print("live diagnostic:", json.dumps(res["live_diagnostic"]))
    return 0 if res["score"] >= 0.98 else 1


if __name__ == "__main__":
    sys.exit(main())
