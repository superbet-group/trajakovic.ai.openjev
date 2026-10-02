"""Batch exports (spec 2.11 Exports): csv, markdown, ojui-batch and jsonl over last-row-per-id BatchRow dicts in index order."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import paths

STATE_MD = 60
EM = "\u2014"


def _q(v: Any) -> str:
    t = "" if v is None else json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
    return '"' + t.replace('"', '""') + '"' if any(c in t for c in ',"\n\r') else t


def _state(r: dict) -> str:
    s = r.get("state")
    return "" if s is None else s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)


def _levels(q: dict) -> list:
    c = q.get("criteria")
    return list(c) if isinstance(c, (dict, list)) else []


def _prob(a: dict, key: Any, label: Any = None) -> Any:
    p = a.get("probabilities") or {}
    for k in (key, str(key), label):
        if k is not None and k in p:
            return p[k]
    return None


def _columns(questions: dict) -> list[tuple[str, Any]]:
    """(column name, fn(answer) -> cell) in the Playground order: value, confidence, then p_<option> / p_<level>."""
    cols: list[tuple[str, Any]] = []
    for qid, q in questions.items():
        kind = q.get("type")
        if kind == "noul":
            cols += [(f"{qid}.noul", (qid, lambda a: a.get("p"))), (f"{qid}.confidence", (qid, lambda a: a.get("margin")))]
        elif kind == "choice":
            cols += [(f"{qid}.choice", (qid, lambda a: a.get("choice"))), (f"{qid}.confidence", (qid, lambda a: a.get("confidence")))]
            cols += [(f"{qid}.p_{k}", (qid, lambda a, k=k: _prob(a, k))) for k in _levels(q)]
        elif kind == "score":
            cols += [(f"{qid}.score", (qid, lambda a: a.get("score"))), (f"{qid}.confidence", (qid, lambda a: a.get("confidence")))]
            labels = _levels(q)
            cols += [(f"{qid}.p_{i}", (qid, lambda a, i=i, lb=lb: _prob(a, i, lb))) for i, lb in enumerate(labels)]
    return cols


def _err(r: dict) -> str:
    e = r.get("error")
    return f"{e.get('code', '')} {e.get('message', '')}".strip() if isinstance(e, dict) else ""


def to_csv(header: dict, rows: list[dict], questions: dict) -> str:
    cols = _columns(questions)
    head = ["index", "id", "state", "status", *(n for n, _ in cols), "latency_ms", "input_tokens", "output_tokens", "request_id", "error"]
    lines = [",".join(map(_q, head))]
    for r in rows:
        ans = r.get("answers") or {}
        u = r.get("usage") or {}
        cells = [r.get("index"), r.get("id"), _state(r), r.get("status")]
        cells += [fn(ans[qid]) if isinstance(ans.get(qid), dict) else "" for _, (qid, fn) in cols]
        cells += [r.get("latency_ms"), u.get("input_tokens"), u.get("output_tokens"), r.get("request_id"), _err(r)]
        lines.append(",".join(map(_q, cells)))
    # deviation: 2.11 Exports: "RFC 4180" rows end with LF, not CRLF, as the Playground's CSV does (every reader accepts both)
    return "\n".join(lines) + "\n"


def _md(t: Any) -> str:
    return str(t).replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _cell(a: dict | None) -> str:
    if not isinstance(a, dict):
        return EM
    kind = a.get("type")
    if kind == "noul":
        return f"{a.get('p')} {a.get('band', '')}".strip()
    if kind == "choice":
        return str(a.get("choice"))
    if kind == "score":
        return f"{a.get('level')} {a.get('level_label', '')}".strip()
    return EM


def to_markdown(header: dict, rows: list[dict], questions: dict) -> str:
    qids = list(questions)
    lines = ["| " + " | ".join(["index", "id", "state", *qids, "latency_ms", "input_tokens"]) + " |",
             "|" + "|".join(["---"] * (5 + len(qids))) + "|"]
    for r in rows:
        s = _state(r)
        s = s if len(s) <= STATE_MD else s[:STATE_MD - 1] + "\u2026"
        ans = r.get("answers") or {}
        cells = [r.get("index"), r.get("id"), s, *(_cell(ans.get(q)) for q in qids),
                 EM if r.get("latency_ms") is None else r["latency_ms"], (r.get("usage") or {}).get("input_tokens", EM)]
        if r.get("status") == "error" and not ans:
            cells[3:3 + len(qids)] = [f"error {(r.get('error') or {}).get('code', '')}".strip()] + [EM] * (len(qids) - 1) if qids else []
        lines.append("| " + " | ".join(_md(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"


def to_ojui_batch(header: dict, rows: list[dict], questions: dict, options: dict | None, *, exported_at: Any, title: str) -> str:
    # deviation: 2.11 Exports: answers hold the MCP Answer shape, not the raw server response; re-import reads states only
    out = {"format": "ojui-batch", "version": 1, "exportedAt": exported_at, "title": title, "questions": questions,
           "options": options or {}, "imageCount": int((header or {}).get("image_count") or 0),
           "rows": [{"index": r.get("index"), "state": r.get("state"), "status": r.get("status"), "model": r.get("model"),
                     "answers": r.get("answers"), "usage": r.get("usage"), "clientMs": r.get("latency_ms"),
                     "serverTiming": r.get("server_timing"), "requestId": r.get("request_id"), "bodyHash": r.get("body_hash"),
                     "error": r.get("error")} for r in rows]}
    return json.dumps(out, ensure_ascii=False, indent=2) + "\n"


def to_jsonl(header: dict, rows: list[dict]) -> str:
    return "".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in [header, *rows])


def write_new(path: str, text: str, config, ext: str) -> str:
    """Create `path` (new only, inside the roots, extension `ext`) and return the resolved path."""
    target: Path = paths.resolve_write(path, config, exts=frozenset({ext}), new_only=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with open(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return str(target)
