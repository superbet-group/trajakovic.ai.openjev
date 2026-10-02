"""Shell command splitter for the command_gate rules and reads (spec 2.18 split step)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SplitResult:
    ok: bool
    segments: tuple[str, ...]
    parts: tuple[str, ...]
    reason: str | None


class _Unsplittable(Exception): ...


class _Split:
    def __init__(self, s: str):
        self.s = s
        self.segments: list[str] = []
        self.parts: list[str] = []

    def scan(self, i: int, closer: str | None, top: bool) -> int:
        """Scan one command context from i; returns the index of its closer (len(s) at top level)."""
        s, n = self.s, len(self.s)
        seg: list[str] = []
        part: list[str] = []

        def put(text: str) -> None:
            seg.append(text)
            part.append(text)

        def flush_seg() -> None:
            t = "".join(seg).strip()
            if t:
                self.segments.append(t)
            seg.clear()

        def flush_part() -> None:
            flush_seg()
            t = "".join(part).strip()
            if t and top:
                self.parts.append(t)
            part.clear()

        def sub(start: int, close: str, skip: int) -> int:
            end = self.scan(start + skip, close, False)
            put(s[start:end + 1])
            return end + 1

        while i < n:
            c = s[i]
            nxt = s[i + 1] if i + 1 < n else ""
            if c == "\\":
                if nxt == "\n":
                    raise _Unsplittable("line continuation")
                put(s[i:i + 2])
                i += 2
            elif c == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    raise _Unsplittable("unbalanced single quote")
                put(s[i:j + 1])
                i = j + 1
            elif c == '"':
                put('"')
                i += 1
                while True:
                    if i >= n:
                        raise _Unsplittable("unbalanced double quote")
                    d = s[i]
                    if d == '"':
                        put(d)
                        i += 1
                        break
                    if d == "\\":
                        if s[i + 1:i + 2] == "\n":
                            raise _Unsplittable("line continuation")
                        put(s[i:i + 2])
                        i += 2
                    elif d == "$" and s[i + 1:i + 2] == "(":
                        i = sub(i, ")", 2)
                    elif d == "`":
                        i = sub(i, "`", 1)
                    else:
                        put(d)
                        i += 1
            elif c == "$" and nxt == "'":
                raise _Unsplittable("ANSI-C quoting")
            elif c == "`":
                if closer == "`":
                    flush_part()
                    return i
                i = sub(i, "`", 1)
            elif (c == "$" or c in "<>") and nxt == "(":
                i = sub(i, ")", 2)
            elif c == "(":
                i = sub(i, ")", 1)
            elif c == ")":
                if closer != ")":
                    raise _Unsplittable("unbalanced parenthesis")
                flush_part()
                return i
            elif c == "<" and nxt == "<":
                raise _Unsplittable("heredoc")
            elif c == "\n" or c == ";":
                flush_part()
                i += 1
            elif c == "&" and nxt == "&" or c == "|" and nxt == "|":
                flush_part()
                i += 2
            elif c == "|":
                flush_seg()
                part.append(c)
                i += 1
            elif c == "&":
                if (seg and seg[-1][-1:] in "<>") or nxt == ">":
                    put(c)
                else:
                    flush_seg()
                    part.append(c)
                i += 1
            else:
                put(c)
                i += 1
        if closer is not None:
            raise _Unsplittable("unbalanced substitution")
        flush_part()
        return n


def split_command(command: str) -> SplitResult:
    sp = _Split(command)
    try:
        sp.scan(0, None, True)
    except _Unsplittable as e:
        return SplitResult(False, (command,), (command,), str(e))
    return SplitResult(True, tuple(sp.segments), tuple(sp.parts), None)
