"""Batch output JSONL: run identity, flock-guarded append handle, tolerant reader (spec 2.11; Decision 3)."""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openjev_mcp import wire
from openjev_mcp.config import Config
from openjev_mcp.errors import invalid_input
from openjev_mcp.paths import resolve_read, resolve_write

SPEC = "1.2"
EXTS = frozenset({".jsonl", ".ndjson"})


def question_hash(questions: dict) -> str:
    return wire.canonical_hash(questions)


def run_id(question_hash: str, source: Any, options: Any, sampling: str, regrey_samples: int) -> str:
    return wire.canonical_hash({"questions": question_hash, "source": source, "options": options,
                                "sampling": sampling, "regrey_samples": regrey_samples})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def make_header(*, run_id: str, question_hash: str, options: dict | None = None, sampling: str = "fast",
                thresholds: dict | None = None, review_rule: dict | None = None, audit: dict | None = None,
                source: dict | None = None, created_at: str | None = None, questions: dict | None = None) -> dict:
    return {"openjev_mcp": "batch", "v": 2, "spec": SPEC, "run_id": run_id, "question_hash": question_hash,
            "options": options or {}, "sampling": sampling, "thresholds": thresholds or {},
            "review_rule": review_rule or {}, "audit": audit or {}, "source": source or {},
            "created_at": created_at or now_iso(), **({"questions": questions} if questions else {})}


def _scan(data: bytes) -> tuple[dict | None, dict[str, dict], int, list[str], int]:
    """(header, last row per id, valid lines, warnings, good length). A bad line is refused except an unterminated last
    one (interrupted write), which is ignored with a warning."""
    header, rows, warnings, n, pos = None, {}, [], 0, 0
    lines = data.split(b"\n")
    for i, raw in enumerate(lines):
        last = i == len(lines) - 1
        if last and not raw:
            break
        try:
            rec = json.loads(raw)
            if not isinstance(rec, dict):
                raise ValueError("not an object")
        except ValueError:
            if last:
                warnings.append(f"output_path ends with an incomplete line {i + 1} (interrupted write); ignored")
                break
            raise invalid_input("output_path", f"output_path line {i + 1} is not valid JSON; fix or truncate the file",
                                "fix or truncate the file") from None
        n += 1
        pos += len(raw) + (0 if last else 1)
        if i == 0 and "openjev_mcp" in rec:
            header = rec
        elif "id" in rec:
            rows[str(rec["id"])] = rec
    return header, rows, n, warnings, pos


def read_output(path: str, config: Config) -> tuple[dict | None, dict[str, dict], int, list[str]]:
    """(header, last row by id, valid line count, warnings). The file is read, never locked."""
    header, rows, n, warnings, _ = _scan(resolve_read(path, config).read_bytes())
    return header, rows, n, warnings


class OutputHandle:
    def __init__(self, path: Path, fd: int, header: dict, rows: dict[str, dict], lines: int, warnings: list[str],
                 created: bool):
        self.path, self.header, self.rows, self.lines, self.warnings, self.created = path, header, rows, lines, warnings, created
        self._fd: int | None = fd

    def append(self, row: dict) -> None:
        """One write of the full line plus newline."""
        if self._fd is None:
            raise ValueError("output handle is closed")
        buf = memoryview((wire.dumps_text(row) + "\n").encode("utf-8"))
        while buf:
            buf = buf[os.write(self._fd, buf):]
        self.lines += 1
        if "id" in row:
            self.rows[str(row["id"])] = row

    def size(self) -> int:
        return os.fstat(self._fd).st_size if self._fd is not None else self.path.stat().st_size

    def close(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)   # drops the flock

    def __enter__(self) -> OutputHandle:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def open_output(path: str, header: dict, *, resume: bool, config: Config) -> OutputHandle:
    """Exclusive flock for the call. New or empty file: header written. Existing file: needs resume, same run_id; an
    interrupted last line is truncated away before the next append."""
    target = resolve_write(path, config, exts=EXTS, new_only=False)
    existed = os.path.lexists(target)
    fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise invalid_input("output_path", "output_path is in use by another batch call",
                                "wait for the other call or use a new output_path") from None
        size = os.fstat(fd).st_size
        if existed and size and not resume:
            raise invalid_input("output_path", "output exists; pass resume:true or a new path",
                                "pass resume:true or a new path")
        rows, lines, warnings = {}, 0, []
        if size:
            data = os.pread(fd, size, 0)
            found, rows, lines, warnings, good = _scan(data)
            if found is None:
                raise invalid_input("output_path", "output_path has no batch header; use a new output_path")
            if found.get("run_id") != header["run_id"]:
                raise invalid_input("output_path", "output_path belongs to another job (questions, source or options "
                                    "differ); use a new output_path", "use a new output_path")
            if good < size:
                os.ftruncate(fd, good)
            if good and not data[:good].endswith(b"\n"):
                os.write(fd, b"\n")
            header = found
        else:
            os.write(fd, (wire.dumps_text(header) + "\n").encode("utf-8"))
            lines = 1
        return OutputHandle(target, fd, header, rows, lines, warnings, not size)
    except BaseException:
        os.close(fd)
        raise
