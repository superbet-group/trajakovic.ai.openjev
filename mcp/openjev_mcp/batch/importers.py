"""Batch state importers (spec 2.11 import rules; Playground ui/static/js/jev/batchImport.js is the behaviour reference).

import_source(items_file spec) and import_items(inline items) -> ImportResult. Errors are ToolError OJ_INVALID_INPUT whose
message starts with the E03x code and whose server_detail["finding"] is the LintFinding dict.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import paths
from ..errors import ToolError
from ..lint import Finding

ARRAY_KEYS = ("states", "batchStates", "items", "data", "rows", "records", "examples")
TEXT_COL = re.compile(r"^(text|state|content|input|prompt|message|body|review|comment|question|sentence|description)$", re.I)
EXT_KIND = {".jsonl": "jsonl", ".ndjson": "jsonl", ".jsonlines": "jsonl", ".json": "json", ".csv": "csv", ".tsv": "tsv",
            ".tab": "tsv", ".txt": "text", ".text": "text", ".log": "text", ".md": "text"}
FORMATS = ("auto", "jsonl", "json", "csv", "tsv", "lines", "blocks", "ojui-batch")
DELIMS = ("auto", ",", "\t", ";")
ENCODINGS = ("auto", "utf-8", "utf-16", "cp1252")
MAX_ALSO = 7
KEY_SCAN, GUESS_SCAN = 500, 200
PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.:\- ]+)\}")


@dataclass
class BatchItem:
    index: int          # 1-based
    id: str
    state: Any


@dataclass
class ImportResult:
    items: list[BatchItem]
    questions: dict | None
    options: dict | None
    report: dict


@dataclass
class _File:
    name: str
    fmt: str
    kind: str                                   # table | values | texts
    encoding: str = "utf-8"
    delim: str | None = None
    header: list[str] = field(default_factory=list)
    rows: list = field(default_factory=list)    # table: list[list[str]]; values: any; texts: str
    blanks: int = 0                             # blank csv rows dropped while parsing
    bad: list[int] = field(default_factory=list)  # jsonl line numbers that did not parse
    questions: dict | None = None
    options: dict | None = None


def _finding(code: str, message: str, fix: str | None = None, path: str = "items_file.path") -> Finding:
    return Finding(code, path, message, fix)


def _fail(code: str, message: str, file: str | None = None, fix: str | None = None, path: str = "items_file.path") -> ToolError:
    return ToolError("OJ_INVALID_INPUT", f"{code}: {message}", hint=fix, path=file,
                     server_detail={"finding": _finding(code, message, fix, path).to_dict()})


def _bad(message: str, file: str | None = None, hint: str | None = None) -> ToolError:
    return ToolError("OJ_INVALID_INPUT", message, hint=hint, path=file)


# ---------------------------------------------------------------- text, CSV

def _clean(text: str) -> str:
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def parse_csv(text: str, delim: str = ",", quoting: bool = True) -> tuple[list[list[str]], int]:
    """RFC 4180 rows (all-blank rows dropped) and the number dropped. A quote opens quoting only at a field start."""
    rows: list[list[str]] = []
    row: list[str] = []
    cell, q, i, n = "", False, 0, len(text)
    while i < n:
        c = text[i]
        if q:
            if c == '"':
                if text[i + 1:i + 2] == '"':
                    cell += '"'
                    i += 1
                else:
                    q = False
            else:
                cell += c
        elif c == '"' and quoting and cell == "":
            q = True
        elif c == delim:
            row.append(cell)
            cell = ""
        elif c in "\r\n":
            if c == "\r" and text[i + 1:i + 2] == "\n":
                i += 1
            row.append(cell)
            rows.append(row)
            row, cell = [], ""
        else:
            cell += c
        i += 1
    if q:                                       # unterminated quote swallowed the rest: read again, quoting off
        return parse_csv(text, delim, False)
    if cell != "" or row:
        row.append(cell)
        rows.append(row)
    kept = [r for r in rows if any(x.strip() for x in r)]
    return kept, len(rows) - len(kept)


def sniff_delimiter(text: str, quoting: bool = True) -> str:
    """Most frequent of , tab ; on the first line outside quotes; ties go to ',' then tab."""
    n = {",": 0, "\t": 0, ";": 0}
    q = False
    for i, c in enumerate(text):
        if c == '"' and quoting:
            if q:
                q = False
            elif i == 0 or text[i - 1] in n or text[i - 1] in "\r\n":
                q = True
        elif not q and c in "\r\n":
            break
        elif not q and c in n:
            n[c] += 1
    if q:
        return sniff_delimiter(text, False)
    best = ","
    for d in ("\t", ";"):
        if n[d] > n[best]:
            best = d
    return best


def _guess_column(header: list[str], rows: list[list[str]]) -> str:
    named = next((h for h in header if TEXT_COL.match(h)), None)
    if named:
        return named
    sample = rows[:GUESS_SCAN]
    best, best_len = 0, -1.0
    for i in range(len(header)):
        mean = sum(len((r[i] if i < len(r) else "").strip()) for r in sample) / len(sample) if sample else 0.0
        if mean > best_len:
            best, best_len = i, mean
    return header[best]


# ---------------------------------------------------------------- reading

def _decode(real: Path, encoding: str) -> tuple[str, str, list[Finding]]:
    if encoding == "auto":
        text, enc, warns = paths.read_text(real)
        return text, enc, [_finding("W603", w.split(": ", 1)[-1]) for w in warns]
    raw = real.read_bytes()
    try:
        if encoding == "utf-8":
            text = raw.decode("utf-8-sig")
        elif encoding == "utf-16":
            text = raw.decode("utf-16")
        else:
            text = raw.decode("cp1252", errors="replace")
    except UnicodeDecodeError as e:
        raise _bad(f"file is not valid {encoding}", str(real), "use encoding auto or cp1252") from e
    return text, encoding, []


def _read(name: str, config, encoding: str) -> tuple[str, str, list[Finding], Path]:
    try:
        real = paths.resolve_read(name, config)
    except ToolError as e:
        if e.code != "E030":
            raise
        ext = os.path.splitext(name)[1].lower()
        if ext in paths.SHEET_EXTS:
            raise _fail("E030", "spreadsheets are not read; export the sheet as CSV first", name,
                        "export the sheet as CSV first") from e
        raise _fail("E030", "binary file; export it as CSV or JSONL", name, "export it as CSV or JSONL") from e
    text, enc, warns = _decode(real, encoding)
    if "\x00" in text and enc != "utf-16":
        raise _fail("E030", "binary file; export it as CSV or JSONL", name, "export it as CSV or JSONL")
    return _clean(text), enc, warns, real


# ---------------------------------------------------------------- sniffing and parsing

def _non_empty(text: str, limit: int) -> list[str]:
    out: list[str] = []
    for line in text.split("\n"):
        if line.strip():
            out.append(line.strip())
            if len(out) >= limit:
                break
    return out


def _parses(line: str) -> bool:
    try:
        json.loads(line)
        return True
    except ValueError:
        return False


def _ratio(text: str, limit: int, objects_only: bool = False) -> float:
    ls = _non_empty(text, limit)
    ok = [x for x in ls if (not objects_only or x[0] in "{[") and _parses(x)]
    return len(ok) / len(ls) if ls else 0.0


def _is_blocks(text: str) -> bool:
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    return len(blocks) >= 2 and any("\n" in b.strip() for b in blocks)


def _table(name: str, text: str, fmt: str, delimiter: str) -> _File:
    d = "\t" if fmt == "tsv" and delimiter == "auto" else sniff_delimiter(text) if delimiter == "auto" else delimiter
    rows, blanks = parse_csv(text, d)
    if not rows:
        raise _fail("E031", "no states found", name)
    return _File(name, fmt, "table", delim=d, header=[h.strip() for h in rows[0]], rows=rows[1:], blanks=blanks)


def _jsonl(name: str, text: str, fmt: str = "jsonl") -> _File:
    vals, bad = [], []
    for i, line in enumerate(text.split("\n"), 1):
        if line.strip():
            try:
                vals.append(json.loads(line))
            except ValueError:
                bad.append(i)
    return _File(name, fmt, "values", rows=vals, bad=bad)


def _json_file(name: str, text: str, v: Any, array_key: str | None, fmt: str, want_batch: bool) -> _File:
    if isinstance(v, dict):
        if v.get("format") == "ojui-export":
            raise _fail("E032", "import it in the Playground sidebar; it is not a batch", name)
        if v.get("format") == "ojui-batch" or want_batch:
            if v.get("format") != "ojui-batch":
                raise _bad("not an ojui-batch export (format must be \"ojui-batch\")", name)
            rows = v.get("rows") if isinstance(v.get("rows"), list) else []
            f = _File(name, "ojui-batch", "values",
                      rows=[r.get("state") for r in rows if isinstance(r, dict) and r.get("state") is not None])
            f.questions = v["questions"] if isinstance(v.get("questions"), dict) and v["questions"] else None
            f.options = v["options"] if isinstance(v.get("options"), dict) else None
            f.header = [str(v.get("version", 1)), str(v.get("imageCount") or 0)]   # carried for the W604 checks
            return f
        key = array_key if array_key else next((k for k in ARRAY_KEYS if isinstance(v.get(k), list)), None)
        if array_key and not isinstance(v.get(array_key), list):
            raise _bad(f"array_key {array_key!r} is not an array in {name}", name, "keys: " + ", ".join(v))
        return _File(name, "json", "values", rows=v[key] if key else [v])
    return _File(name, "json", "values", rows=v if isinstance(v, list) else [v])


def _parse_file(name: str, text: str, ext: str, spec: dict) -> _File:
    fmt, delimiter, array_key = spec["format"], spec["delimiter"], spec.get("array_key")
    if not text.strip():
        raise _fail("E031", "no states found", name)
    kind = EXT_KIND.get(ext)
    if fmt == "auto":
        fmt = kind if kind in ("jsonl", "json", "csv", "tsv") else None
    if fmt in ("csv", "tsv"):
        return _table(name, text, fmt, delimiter)
    if fmt == "lines":
        return _File(name, "lines", "texts", rows=[x.strip() for x in text.split("\n") if x.strip()])
    if fmt == "blocks":
        return _File(name, "blocks", "texts", rows=[x.strip() for x in re.split(r"\n\s*\n", text) if x.strip()])
    if fmt in ("json", "ojui-batch"):
        try:
            return _json_file(name, text, json.loads(text), array_key, fmt, fmt == "ojui-batch")
        except ValueError as e:
            if fmt == "json" and _ratio(text, 2000) >= 0.6:
                return _jsonl(name, text)
            raise _bad(f"invalid JSON: {e}", name) from e
    if fmt == "jsonl":
        if _ratio(text, 2000) < 0.5:            # a pretty-printed document saved as .jsonl
            try:
                return _json_file(name, text, json.loads(text), array_key, "json", False)
            except ValueError:
                pass
        return _jsonl(name, text)
    # content sniffing: lines/blocks by content for text files, else JSONL, JSON, CSV header row, blocks, lines
    head = _non_empty(text, GUESS_SCAN)
    if text.lstrip()[:1] == "[":                # one JSON array document beats a one-line "jsonl"
        try:
            return _json_file(name, text, json.loads(text), array_key, "json", False)
        except ValueError:
            pass
    if head and sum(1 for x in head if x[0] in "{[" and _parses(x)) / len(head) >= 0.9:
        return _jsonl(name, text)
    if text.lstrip()[:1] == "{":
        try:
            return _json_file(name, text, json.loads(text), array_key, "json", False)
        except ValueError:
            pass
    if kind != "text":
        d = sniff_delimiter(text) if delimiter == "auto" else delimiter
        first = text.split("\n", 1)[0]
        if d in first:
            rows, _ = parse_csv(text, d)
            if len(rows) >= 2 and len(rows[0]) >= 2 and all(len(r) == len(rows[0]) for r in rows[:5]):
                return _table(name, text, "csv", d)
    if _is_blocks(text):
        return _File(name, "blocks", "texts", rows=[x.strip() for x in re.split(r"\n\s*\n", text) if x.strip()])
    return _File(name, "lines", "texts", rows=[x.strip() for x in text.split("\n") if x.strip()])


# ---------------------------------------------------------------- resolving to states

def _keys(values: list) -> list[str]:
    keys: dict[str, None] = {}
    seen = 0
    for v in values:
        if isinstance(v, dict):
            keys.update(dict.fromkeys(v))
            seen += 1
            if seen >= KEY_SCAN:
                break
    return list(keys)


def _cell(v: Any) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def _state_value(v: Any) -> Any:
    """A usable state or None: strings trimmed, containers kept, scalars as text."""
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, (dict, list)):
        return v
    return json.dumps(v)


def _render(tpl: str, ctx: dict[str, Any], columns: list[str], name: str) -> str | None:
    used: list[str] = []

    def sub(m: re.Match) -> str:
        k = m.group(1)
        if k != "id" and k not in columns:
            raise _bad(f"state_template placeholder {{{k}}} is not a column or key", name,
                       "columns: " + ", ".join(columns) if columns else None)
        used.append(k)
        return _cell(ctx.get(k))
    out = PLACEHOLDER.sub(sub, tpl)
    if used and not any(_cell(ctx.get(k)).strip() for k in used if k != "id"):
        return None
    return out if out.strip() else None


@dataclass
class _Ctx:
    spec: dict
    out: list[list]                             # [explicit id | None, state]
    warnings: list[Finding]
    blanks: int = 0
    guessed: str | None = None
    fields: list[str | None] = field(default_factory=list)
    id_fields: list[str | None] = field(default_factory=list)
    columns: list[str] | None = None


def _resolve_group(files: list[_File], cx: _Ctx) -> None:
    first = files[0]
    name = first.name if len(files) == 1 else ", ".join(f.name for f in files)
    sf, idf, tpl = cx.spec.get("state_field"), cx.spec.get("id_field"), cx.spec.get("state_template")
    if first.kind == "texts" or first.fmt == "ojui-batch":      # whole states, nothing to pick
        for f in files:
            cx.out.extend([None, s] for s in f.rows)
        if cx.columns is None:
            cx.columns = []
        cx.fields.append(None)
        return
    if first.kind == "table":
        header, cols = first.header, first.header
        records = [r for f in files for r in f.rows]
        cx.blanks += sum(f.blanks for f in files)
        get = lambda r, k: (r[header.index(k)] if header.index(k) < len(r) else "")   # noqa: E731
        as_dict = lambda r: {h: (r[i] if i < len(r) else "") for i, h in enumerate(header)}   # noqa: E731
    else:
        records = [v for f in files for v in f.rows]
        cols = _keys(records)
        header = cols
        get = lambda r, k: r.get(k) if isinstance(r, dict) else None   # noqa: E731
        as_dict = lambda r: r if isinstance(r, dict) else {}   # noqa: E731
    if cx.columns is None:
        cx.columns = list(cols)
    for label, val in (("id_field", idf), ("state_field", None if (tpl or sf == "*") else sf)):
        if val and val not in header:
            raise _bad(f"{label} {val!r} is not a column/key of {name}", first.name, "columns: " + ", ".join(header))
    field_ = None
    if not tpl:
        field_ = sf
        if field_ is None:
            if first.kind == "table":
                field_ = _guess_column(header, records)
            elif any(isinstance(v, dict) for v in records):
                field_ = next((h for h in header if TEXT_COL.match(h)), "*")
            if field_ is not None and len(header) > 1:
                what = "the whole object is the state" if field_ == "*" else f"guessed {field_!r}"
                cx.warnings.append(_finding("W602", f"state_field omitted: {what} in {name}", "pass state_field to choose",
                                            "items_file.state_field"))
        cx.fields.append(field_)
    cx.id_fields.append(idf)
    for r in records:
        rid = _cell(get(r, idf)).strip() if idf and (first.kind == "table" or isinstance(r, dict)) else ""
        if tpl:
            ctx = {**as_dict(r), "id": rid or str(len(cx.out) + 1)}
            st: Any = _render(tpl, ctx, header, first.name)
        elif first.kind == "table":
            st = as_dict(r) if field_ == "*" else _state_value(get(r, field_))
        elif isinstance(r, dict):
            st = r if field_ == "*" else _state_value(r.get(field_))
        else:
            st = _state_value(r)
        if st is None or st == "":
            cx.blanks += 1
            continue
        cx.out.append([rid or None, st])


def _group(files: list[_File]) -> list[list[_File]]:
    groups: list[list[_File]] = []
    for f in files:
        prev = groups[-1][0] if groups else None
        same = prev is not None and prev.fmt not in ("ojui-batch",) and f.kind == prev.kind and (
            (f.kind == "table" and f.delim == prev.delim and f.header == prev.header)
            or (f.kind == "values" and f.fmt == "jsonl" and prev.fmt == "jsonl"))
        if same:
            groups[-1].append(f)
        else:
            groups.append([f])
    return groups


def _finish(out: list[list], max_items: int, warnings: list[Finding], blanks: int, bad: int) -> list[BatchItem]:
    if blanks or bad:
        what = f"{blanks} empty rows" if blanks else ""
        what += (", " if what else "") + f"{bad} unparseable JSON lines" if bad else ""
        warnings.append(_finding("W605", f"skipped {what}"))
    if not out:
        raise _fail("E031", "no states found")
    truncated = len(out) > max_items
    if truncated:
        warnings.append(_finding("W601", f"{len(out) - max_items} rows dropped above max_items={max_items}",
                                 "raise max_items or split the source", "max_items"))
    items, seen = [], set()
    for i, (rid, st) in enumerate(out[:max_items], 1):
        rid = rid or str(i)
        if rid in seen:
            raise _fail("E031", f"duplicate id {rid!r} (first duplicate at row {i})", fix="make ids unique or drop id_field",
                        path="items_file.id_field")
        seen.add(rid)
        items.append(BatchItem(i, rid, st))
    return items


def _check(spec: dict) -> dict:
    s = {"format": "auto", "delimiter": "auto", "encoding": "auto", **spec}
    for k, allowed in (("format", FORMATS), ("delimiter", DELIMS), ("encoding", ENCODINGS)):
        if s[k] not in allowed:
            raise _bad(f"items_file.{k} {s[k]!r} must be one of {list(allowed)}")
    also = s.get("also") or []
    if not isinstance(s.get("path"), str) or not isinstance(also, list) or len(also) > MAX_ALSO:
        raise _bad(f"items_file needs a path and at most {MAX_ALSO} also files")
    return s


def import_source(spec: dict, config, *, max_items: int) -> ImportResult:
    s = _check(spec)
    warnings: list[Finding] = []
    files: list[_File] = []
    for name in [s["path"], *s.get("also", [])]:
        text, enc, warns, real = _read(name, config, s["encoding"])
        warnings.extend(warns)
        f = _parse_file(name, text, real.suffix.lower(), s)
        f.encoding = enc
        if f.fmt == "ojui-batch":
            ver, images = int(f.header[0]) if f.header[0].isdigit() else 1, int(f.header[1]) if f.header[1].isdigit() else 0
            if ver > 1:
                warnings.append(_finding("W604", f"ojui-batch version {ver} is newer than 1; read as version 1"))
            if images > 0:
                warnings.append(_finding("W604", "images are not restored; pass them in phase 3"))
            f.header = []
        files.append(f)
    cx = _Ctx(s, [], warnings)
    for g in _group(files):
        _resolve_group(g, cx)
    items = _finish(cx.out, max_items, warnings, cx.blanks, sum(len(f.bad) for f in files))
    src = next((f for f in files if f.fmt == "ojui-batch"), None)
    first = files[0]
    fmts = {f.fmt for f in files}
    encs = sorted({f.encoding for f in files})
    report = {"format": first.fmt if len(fmts) == 1 else "mixed", "delimiter": first.delim,
              "encoding": encs[0] if len(encs) == 1 else ",".join(encs),
              "state_field": next((x for x in cx.fields if x), None) if not s.get("state_template") else None,
              "id_field": s.get("id_field"), "columns": cx.columns or [], "row_count": len(items),
              "truncated": len(cx.out) > max_items, "warnings": [w.to_dict() for w in warnings]}
    return ImportResult(items, src.questions if src else None, src.options if src else None, report)


def import_items(items: list[dict], *, max_items: int) -> ImportResult:
    """Inline items [{id?, state}]; the default id is the 1-based position."""
    out = [[str(it["id"]) if it.get("id") not in (None, "") else None, it["state"]] for it in items]
    warnings: list[Finding] = []
    res = _finish(out, max_items, warnings, 0, 0)
    return ImportResult(res, None, None, {"format": "items", "delimiter": None, "encoding": "utf-8", "state_field": None,
                                          "id_field": None, "columns": [], "row_count": len(res),
                                          "truncated": len(out) > max_items, "warnings": [w.to_dict() for w in warnings]})
