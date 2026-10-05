"""Shared skill extractor for the skills evaluation.

Discovers skills, parses their single-line frontmatter, and yields the structure
the checkers need: labelled fenced blocks (with file:line), reference files and the
links to them, ``openjev://`` URIs, and tool mentions (bare and fully qualified).

Pure stdlib; no network, no server process.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]                      # <repo>/mcp/tests/skills_eval -> <repo>
MCP_DIR = REPO / "mcp"
PLUGINS_DIR = REPO / "plugins"
PLUGIN_DIR = PLUGINS_DIR / "openjev-skills"
SKILLS_DIR = PLUGIN_DIR / "skills"
BEFORE_COMMIT = "ee9f812"                   # last commit holding the pre-refactor skills under mcp/skills (the "before" contrast)
MCP_PLUGIN_DIR = PLUGINS_DIR / "openjev-mcp"
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"
DATA_DIR = HERE / "data"

HUB = "openjev-data-prep"
TOOLS = ("status", "ask", "yes_no", "classify", "score", "filter", "compile", "generate",
         "lint", "recipe", "batch", "batch_results", "calibrate", "ask_image")
LABELS = ("call", "result", "questions", "recipe-inputs", "example", "items")
STATIC_URIS = ("openjev://schema", "openjev://limits", "openjev://recipes", "openjev://templates",
               "openjev://patterns", "openjev://guide/authoring")

_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+.-]*)\s*$")
_LABEL = re.compile(r"^\s*<!--\s*openjev-(call|result|questions|recipe-inputs|example|items)"
                    r"(?::\s*([\w.\-]+))?\s*-->\s*$")
_ANY_LABEL = re.compile(r"^\s*<!--\s*openjev-")
_QUALIFIED = re.compile(r"mcp__(?:plugin_openjev-mcp_)?openjev__(" + "|".join(TOOLS) + r")\b")
_BARE = re.compile(r"`(" + "|".join(TOOLS) + r")(?:[.(][^`]*)?`")
_URI = re.compile(r"openjev://[A-Za-z0-9_/{}.\-*]*[A-Za-z0-9_/}*]")
_LINK = re.compile(r"\]\(([^)\s]+)\)")
_EDGE_CELL = re.compile(r"`([^`]+)`")


def rel(path: Path, root: Path | None = None) -> str:
    for base in (root or REPO, REPO):
        try:
            return str(Path(path).resolve().relative_to(base.resolve()))
        except ValueError:
            continue
    return str(path)


@dataclass
class Block:
    label: str | None          # call | result | questions | recipe-inputs | example | items | None
    arg: str | None            # tool, recipe id or items format
    lang: str                  # fence language ("json", "csv", ...)
    text: str
    file: str
    line: int                  # 1-based line of the opening fence
    label_line: int | None = None
    data: object = None        # parsed JSON (lang json only)
    error: str | None = None   # JSON parse error, if any
    label_error: str | None = None   # malformed or incomplete label, if any

    @property
    def tool(self) -> str | None:
        return self.arg if self.label in ("call", "result") else None

    @property
    def recipe(self) -> str | None:
        return self.arg if self.label == "recipe-inputs" else None


@dataclass
class Skill:
    name: str
    dir: Path
    path: Path
    text: str
    frontmatter: dict = field(default_factory=dict)
    fm_problems: list = field(default_factory=list)
    body_start: int = 0                        # 1-based line of first body line
    root: Path = REPO

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()

    @property
    def description(self) -> str:
        return self.frontmatter.get("description", "")

    @property
    def body(self) -> str:
        return "\n".join(self.lines[self.body_start - 1:])

    @property
    def is_hub(self) -> bool:
        return self.name == HUB

    def references(self) -> list[Path]:
        d = self.dir / "references"
        return sorted(d.rglob("*.md")) if d.is_dir() else []

    def files(self) -> list[Path]:
        return [self.path, *self.references()]

    def relfile(self, p: Path) -> str:
        return rel(p, self.root)

    def blocks(self) -> list[Block]:
        out: list[Block] = []
        for p in self.files():
            out.extend(iter_blocks(p.read_text(encoding="utf-8"), self.relfile(p)))
        return out

    def links(self, path: Path | None = None) -> list[tuple[str, int]]:
        """Markdown link targets outside code fences as (target, line)."""
        p = path or self.path
        out = []
        for i, line, in_fence in _scan(p.read_text(encoding="utf-8")):
            if in_fence:
                continue
            out.extend((m.group(1), i) for m in _LINK.finditer(line))
        return out

    def uris(self) -> list[tuple[str, str, int]]:
        """(uri, file, line) for every openjev:// URI in SKILL.md and references."""
        out = []
        for p in self.files():
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                for m in _URI.finditer(line):
                    out.append((m.group(0).rstrip(".,;:)"), self.relfile(p), i))
        return out

    def tool_mentions(self, path: Path | None = None) -> dict:
        """Mentions in the body of one file (frontmatter and fences excluded).

        Returns {"qualified": {tool: [line, ...]}, "bare": {tool: [line, ...]}}.
        A bare mention is a backticked short name such as `filter` or `batch.next_cursor`.
        """
        p = path or self.path
        text = p.read_text(encoding="utf-8")
        start = self.body_start if p == self.path else 1
        q: dict[str, list[int]] = {}
        b: dict[str, list[int]] = {}
        for i, line, in_fence in _scan(text):
            if in_fence or i < start:
                continue
            masked = _QUALIFIED.sub(lambda m: q.setdefault(m.group(1), []).append(i) or " ", line)
            for m in _BARE.finditer(masked):
                b.setdefault(m.group(1), []).append(i)
        return {"qualified": q, "bare": b}


def _scan(text: str):
    """Yield (lineno, line, in_fence) where fence delimiter lines count as in-fence."""
    fence = None
    for i, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if fence is None:
            if m:
                fence = m.group(2)[0] * len(m.group(2))
                yield i, line, True
                continue
            yield i, line, False
        else:
            yield i, line, True
            if m and m.group(2).startswith(fence) and not m.group(3):
                fence = None


def iter_blocks(text: str, file: str = "") -> list[Block]:
    lines = text.splitlines()
    out: list[Block] = []
    i = 0
    while i < len(lines):
        m = _FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        marker, lang = m.group(2), m.group(3)
        close = marker[0] * len(marker)
        j = i + 1
        while j < len(lines):
            cm = _FENCE.match(lines[j])
            if cm and cm.group(2).startswith(close) and not cm.group(3):
                break
            j += 1
        body = "\n".join(lines[i + 1:j])
        label = arg = None
        label_line = None
        label_error = None
        if i > 0:
            prev = lines[i - 1]
            lm = _LABEL.match(prev)
            if lm:
                label, arg, label_line = lm.group(1), lm.group(2), i
                if label in ("call", "result") and arg not in TOOLS:
                    label_error = f"openjev-{label} needs a tool name from the 14 tools, got {arg!r}"
                elif label == "recipe-inputs" and not arg:
                    label_error = "openjev-recipe-inputs needs a recipe id"
                elif label == "items" and arg not in ("csv", "tsv", "jsonl", "json", "lines", "blocks"):
                    label_error = f"openjev-items needs csv|tsv|jsonl|json|lines|blocks, got {arg!r}"
            elif _ANY_LABEL.match(prev):
                label_error = f"unrecognised label comment: {prev.strip()}"
        blk = Block(label, arg, lang.lower(), body, file, i + 1, label_line, label_error=label_error)
        if blk.lang == "json":
            try:
                blk.data = json.loads(body)
            except ValueError as e:
                blk.error = str(e)
        out.append(blk)
        i = j + 1
    return out


def parse_frontmatter(text: str) -> tuple[dict, list[str], int]:
    """Return (fields, problems, body_start_line). Single-line ``key: value`` only."""
    lines = text.splitlines()
    problems: list[str] = []
    if not lines or lines[0].strip() != "---":
        return {}, ["missing opening --- frontmatter delimiter"], 1
    end = next((k for k in range(1, len(lines)) if lines[k].strip() == "---"), None)
    if end is None:
        return {}, ["missing closing --- frontmatter delimiter"], 1
    fm: dict = {}
    for k in range(1, end):
        raw = lines[k]
        if not raw.strip():
            continue
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", raw)
        if not m:
            problems.append(f"line {k + 1}: continuation or non-key line (descriptions must be one line)")
            continue
        key, val = m.group(1), m.group(2).strip()
        if val in (">", "|", ">-", "|-", ">+", "|+"):
            problems.append(f"line {k + 1}: {key} uses a block scalar (must be one line)")
            val = ""
        elif len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            try:
                val = json.loads(val)
            except ValueError:
                problems.append(f"line {k + 1}: {key} has an invalid double-quoted string")
                val = val[1:-1]
        elif len(val) >= 2 and val[0] == "'" and val[-1] == "'":
            val = val[1:-1].replace("''", "'")
        elif key == "description" and ": " in val:
            problems.append(f"line {k + 1}: unquoted description contains ': ' (invalid YAML); wrap in double quotes")
        fm[key] = val
    return fm, problems, end + 2


def load_skill(d: Path, root: Path | None = None) -> Skill:
    path = d / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    fm, problems, body_start = parse_frontmatter(text)
    return Skill(name=fm.get("name", d.name), dir=d, path=path, text=text, frontmatter=fm,
                 fm_problems=problems, body_start=body_start, root=root or REPO)


def discover(skills_dir: Path | str | None = None, root: Path | None = None) -> list[Skill]:
    base = Path(skills_dir) if skills_dir else SKILLS_DIR
    if not base.is_dir():
        return []
    return [load_skill(d, root) for d in sorted(base.iterdir()) if (d / "SKILL.md").is_file()]


# ---------- server-side catalogues used to resolve URIs ----------

def recipe_ids() -> set[str]:
    d = MCP_DIR / "openjev_mcp" / "recipes" / "builtin"
    return {p.stem for p in d.glob("*.json")} if d.is_dir() else set()


def template_ids() -> set[str]:
    f = MCP_DIR / "openjev_mcp" / "data" / "templates.json"
    try:
        return set(json.loads(f.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def server_version() -> str | None:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (MCP_DIR / "openjev_mcp" / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1) if m else None


def uri_problem(uri: str) -> str | None:
    """None when the URI resolves to something the server serves, else a reason."""
    if uri in STATIC_URIS:
        return None
    m = re.fullmatch(r"openjev://(recipes|templates|audits)/(.+)", uri)
    if not m:
        return "unknown openjev:// URI"
    kind, ident = m.groups()
    if ident.startswith("{") and ident.endswith("}"):
        return None                                     # documented placeholder
    if kind == "audits":
        return None
    known = recipe_ids() if kind == "recipes" else template_ids()
    return None if ident in known else f"no {kind[:-1]} with id {ident!r}"


# ---------- chaining.md Edges table ----------

def parse_edges(text: str) -> list[dict]:
    """Rows of the ``## Edges`` table as {from, to, when, line}; cells are backticked paths or URIs."""
    rows: list[dict] = []
    in_edges = False
    for i, line in enumerate(text.splitlines(), 1):
        if re.match(r"^##\s+Edges\b", line):
            in_edges = True
            continue
        if in_edges and re.match(r"^##\s", line):
            break
        if not in_edges or not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set("".join(cells)) <= set("-: "):
            continue
        if cells[0].lower() == "from":
            continue
        fm, to = _EDGE_CELL.search(cells[0]), _EDGE_CELL.search(cells[1])
        rows.append({"from": fm.group(1) if fm else None, "to": to.group(1) if to else None,
                     "when": cells[2] if len(cells) > 2 else "", "line": i})
    return rows


def ensure_server_importable() -> None:
    """Put <repo>/mcp on sys.path so offline checkers can import openjev_mcp."""
    if str(MCP_DIR) not in sys.path:
        sys.path.insert(0, str(MCP_DIR))
