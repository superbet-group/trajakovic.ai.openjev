"""Static best-practice conformance checks for the openjev-skills plugin (component S).

Per skill: S1..S14. Global: G1..G5. Every check weighs 1; score = passed / counted.

    python mcp/tests/skills_eval/static_check.py [--json] [--skills-dir DIR] [--no-claude]

``run()`` returns {score, passed, total, checks:[{id, skill, ok, detail, file, line}], ...}.
Skipped checks (``skipped: true``) are listed but excluded from the score.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from . import skill_lib as L
except ImportError:                                   # run as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import skill_lib as L                             # type: ignore

WORKFLOW_SKILLS = ("openjev-data-prep", "openjev-data-records", "openjev-question-authoring", "openjev-calibration")
PATH_KEYS = ("path", "output_path", "case_file", "from_batch")
LEAKAGE = [
    (re.compile(r"(?<![\w.-])mcp/"), "repo path mcp/"),
    (re.compile(r"(?<![\w.-])docs/"), "repo path docs/"),
    (re.compile(r"\.venv"), ".venv"),
    (re.compile(r"mise run"), "mise run"),
    (re.compile(r"run_cases\.py"), "run_cases.py"),
    (re.compile(r"the README", re.I), "the README"),
    (re.compile(r"\bspec \d"), "spec N"),
    (re.compile(r"\(spec "), "(spec "),
]
USER_PATH = re.compile(r"/(?:Users|home|private)/")
DRIVE_PATH = re.compile(r"\b[A-Za-z]:\\")
BACKSLASH_PROSE = re.compile(r"[\w.]+\\[\w.]+\\[\w.]+")
STOP = set("the and for with into from that this when use over are its not any one all per via can will have has out are but "
           "than then them they their there which what who how why where while about after before each every other such "
           "also only own more most some same too very".split())


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, cid: str, skill: str | None, ok: bool, detail: str = "", file: str | None = None,
            line: int | None = None, skipped: bool = False) -> None:
        c = {"id": cid, "skill": skill, "ok": bool(ok), "detail": detail, "file": file, "line": line}
        if skipped:
            c["skipped"] = True
        self.checks.append(c)

    def result(self) -> dict:
        counted = [c for c in self.checks if not c.get("skipped")]
        passed = sum(c["ok"] for c in counted)
        per: dict[str, dict] = {}
        for c in counted:
            s = per.setdefault(c["skill"] or "(global)", {"passed": 0, "total": 0})
            s["total"] += 1
            s["passed"] += c["ok"]
        return {"score": round(passed / len(counted), 4) if counted else 0.0, "passed": passed,
                "total": len(counted), "skipped": len(self.checks) - len(counted),
                "per_skill": per, "checks": self.checks}


def _fail_at(items: list[tuple[str, int, str]]) -> tuple[str, str | None, int | None]:
    """items = [(file, line, text)] -> (detail, first file, first line)."""
    shown = "; ".join(f"{f}:{ln} {t}" for f, ln, t in items[:5])
    more = f" (+{len(items) - 5} more)" if len(items) > 5 else ""
    return shown + more, items[0][0], items[0][1]


def _walk_paths(node, trail=""):
    """Yield (key, value) for string values under path-like keys, including items_file.path."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in PATH_KEYS and isinstance(v, str):
                yield k, v
            else:
                yield from _walk_paths(v, f"{trail}.{k}")
    elif isinstance(node, list):
        for v in node:
            yield from _walk_paths(v, trail)


def _abs_ok(v: str) -> bool:
    return v.startswith("/") or "://" in v or v.startswith("data:")


def check_skill(rep: Report, sk: "L.Skill") -> None:
    n = sk.name
    sf = sk.relfile(sk.path)
    fm = sk.frontmatter
    desc = fm.get("description", "")
    dirname = sk.dir.name

    # S1 name
    probs = []
    if fm.get("name") != dirname:
        probs.append(f"name {fm.get('name')!r} != directory {dirname!r}")
    nm = fm.get("name", "")
    if not re.fullmatch(r"[a-z0-9-]+", nm) or len(nm) > 64:
        probs.append("name must match ^[a-z0-9-]+$ and be <= 64 chars")
    if not nm.startswith("openjev-"):
        probs.append("name must start with openjev-")
    if re.search(r"anthropic|claude", nm):
        probs.append("name contains a reserved word")
    if re.search(r"<[^>]*>", nm):
        probs.append("name contains XML")
    rep.add("S1", n, not probs, "; ".join(probs), sf, 2)

    # S2 description shape
    probs = list(sk.fm_problems)
    if not desc.strip():
        probs.append("description empty")
    if len(desc) > 1024:
        probs.append(f"description is {len(desc)} chars (> 1024)")
    if re.search(r"[<>]", desc):
        probs.append("description contains < or >")
    raw = next((ln for ln in sk.lines[:sk.body_start] if ln.startswith("description:")), "")
    if raw and not raw.split(":", 1)[1].strip().startswith('"'):
        probs.append("description must be wrapped in double quotes")
    try:
        import yaml
        fm_text = "\n".join(sk.lines[1:sk.body_start - 2])
        parsed = yaml.safe_load(fm_text) or {}
        if parsed.get("description") != desc:
            probs.append("YAML parse of frontmatter disagrees with the single-line value")
    except ImportError:
        pass
    except Exception as e:                            # noqa: BLE001
        probs.append(f"frontmatter is not valid YAML: {str(e).splitlines()[0]}")
    rep.add("S2", n, not probs, "; ".join(probs), sf, 3)

    # S3 third person + Use when
    probs = []
    words = desc.split()
    first = words[0].rstrip(",:;") if words else ""
    if not (first.isalpha() and first.endswith("s")):
        probs.append(f"first word {first!r} is not a third-person verb ending in s")
    pron = re.search(r"\b(I|you|your|You|Your)\b", desc)
    if pron:
        probs.append(f"first/second person word {pron.group(0)!r}")
    if "Use when" not in desc:
        probs.append("description lacks a 'Use when' clause")
    rep.add("S3", n, not probs, "; ".join(probs), sf, 3)

    # S4 length
    total = len(sk.lines)
    rep.add("S4", n, total < 500, f"{total} lines" if total >= 500 else "", sf)

    # S5 references one level deep and all linked
    probs = []
    refs = sk.references()
    linked = {t.split("#")[0] for t, _ in sk.links()}
    for p in refs:
        relp = p.relative_to(sk.dir).as_posix()
        if relp.count("/") != 1:
            probs.append(f"{relp} is not directly under references/")
        if relp not in linked:
            probs.append(f"{relp} is not linked from SKILL.md")
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\]\([^)]*\.md(?:#[^)]*)?\)", ln) or re.search(r"references/[\w.-]+\.md", ln):
                probs.append(f"{sk.relfile(p)}:{i} links to another .md file")
    for t in linked:
        if t.endswith(".md") and "://" not in t and not (sk.dir / t).is_file():
            probs.append(f"SKILL.md links to missing {t}")
    rep.add("S5", n, not probs, "; ".join(probs[:6]), sf)

    # S6 TOC for long references
    probs = []
    for p in refs:
        ls = p.read_text(encoding="utf-8").splitlines()
        if len(ls) > 100 and not any(l.strip().lower() == "## contents" for l in ls[:15]):
            probs.append(f"{sk.relfile(p)} has {len(ls)} lines but no '## Contents' in the first 15")
    rep.add("S6", n, not probs, "; ".join(probs), sf)

    # S7 paths
    hits: list[tuple[str, int, str]] = []
    for p in sk.files():
        f = sk.relfile(p)
        text = p.read_text(encoding="utf-8")
        for i, line, in_fence in L._scan(text):
            if USER_PATH.search(line):
                hits.append((f, i, "absolute user path"))
            if DRIVE_PATH.search(line) or (not in_fence and BACKSLASH_PROSE.search(line)):
                hits.append((f, i, "backslash path"))
    for b in sk.blocks():
        if b.data is not None and b.label not in ("result", "example"):     # results carry JSON-path 'path' fields
            for k, v in _walk_paths(b.data):
                if not _abs_ok(v):
                    hits.append((b.file, b.line, f'relative path value {k}="{v}"'))
    if hits:
        d, f, ln = _fail_at(hits)
        rep.add("S7", n, False, d, f, ln)
    else:
        rep.add("S7", n, True, "", sf)

    # S8 repo leakage
    hits = []
    for p in sk.files():
        f = sk.relfile(p)
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for rx, what in LEAKAGE:
                if rx.search(line):
                    hits.append((f, i, what))
    if hits:
        d, f, ln = _fail_at(hits)
        rep.add("S8", n, False, d, f, ln)
    else:
        rep.add("S8", n, True, "", sf)

    # S9 qualified tool mentions
    tm = sk.tool_mentions()
    missing = sorted(t for t in tm["bare"] if t not in tm["qualified"])
    rep.add("S9", n, not missing,
            "tools mentioned only by short name: " + ", ".join(missing) if missing else "", sf,
            min(tm["bare"][missing[0]]) if missing else None)

    # S10 json blocks labelled (and well formed)
    hits = []
    for b in sk.blocks():
        if b.lang == "json" and b.label is None:
            hits.append((b.file, b.line, "json block without an openjev-* label comment on the line before"))
        if b.label_error:
            hits.append((b.file, b.line, b.label_error))
        if b.lang == "json" and b.error:
            hits.append((b.file, b.line, f"invalid JSON: {b.error}"))
    if hits:
        d, f, ln = _fail_at(hits)
        rep.add("S10", n, False, d, f, ln)
    else:
        rep.add("S10", n, True, "", sf)

    # S11 precedence rule
    rep.add("S11", n, "recedence" in sk.text, "no 'Precedence' rule found" if "recedence" not in sk.text else "", sf)

    # S12 connect pointer
    ok = sk.is_hub or "openjev-data-prep" in sk.body
    rep.add("S12", n, ok, "" if ok else "no pointer to openjev-data-prep (Connect)", sf)

    # S13 checklist in workflow skills
    if n in WORKFLOW_SKILLS:
        ok = bool(re.search(r"^\s*- \[ \] ", sk.text, re.M))
        rep.add("S13", n, ok, "" if ok else "workflow skill has no '- [ ]' checklist", sf)
    else:
        rep.add("S13", n, True, "not a workflow skill", sf)

    # S14 URIs resolve
    hits = []
    for uri, f, ln in sk.uris():
        why = L.uri_problem(uri)
        if why:
            hits.append((f, ln, f"{uri}: {why}"))
    if hits:
        d, f, ln = _fail_at(hits)
        rep.add("S14", n, False, d, f, ln)
    else:
        rep.add("S14", n, True, "", sf)


# ---------- global checks ----------

def _tokens(desc: str) -> set[str]:
    return {w for w in re.findall(r"[a-z_]{3,}", desc.lower()) if w not in STOP}


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as e:
        return None, str(e)


def _prompt_strings(node, key="") -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(_prompt_strings(v, k))
    elif isinstance(node, list):
        for v in node:
            out.extend(_prompt_strings(v, key))
    elif isinstance(node, str) and ("prompt" in key.lower() or key.lower() in ("turns", "followups", "followup", "task")):
        out.append(node)
    return out


def _ngrams(words: list[str], n: int):
    for i in range(len(words) - n + 1):
        yield tuple(words[i:i + n])


def check_globals(rep: Report, skills: list["L.Skill"], run_claude: bool = True) -> None:
    # G1 claude plugin validate --strict
    targets = [(".", L.REPO), ("plugins/openjev-skills", L.PLUGIN_DIR), ("plugins/openjev-mcp", L.MCP_PLUGIN_DIR)]
    exe = shutil.which("claude")
    for label, path in targets:
        if not run_claude:
            rep.add("G1", None, True, f"{label}: skipped (--no-claude)", skipped=True)
        elif not exe:
            rep.add("G1", None, False, f"{label}: claude CLI not found on PATH, cannot run plugin validate --strict")
        elif not path.exists():
            rep.add("G1", None, False, f"{label}: path does not exist")
        else:
            try:
                r = subprocess.run([exe, "plugin", "validate", "--strict", str(path)], capture_output=True,
                                   text=True, timeout=120, cwd=L.REPO)
                out = (r.stdout + r.stderr).strip().splitlines()
                rep.add("G1", None, r.returncode == 0, f"{label}: exit {r.returncode}" +
                        ("" if r.returncode == 0 else ": " + " | ".join(out[-4:])[:400]))
            except (subprocess.TimeoutExpired, OSError) as e:
                rep.add("G1", None, False, f"{label}: {e}")

    # G2 versions
    versions = {}
    for name, d in (("openjev-skills", L.PLUGIN_DIR), ("openjev-mcp", L.MCP_PLUGIN_DIR)):
        data, err = _json(d / ".claude-plugin" / "plugin.json")
        versions[name] = data.get("version") if isinstance(data, dict) else None
        if err:
            versions[name + "!"] = err
    mk, _ = _json(L.MARKETPLACE)
    if isinstance(mk, dict):
        for p in mk.get("plugins", []):
            if "version" in p:
                versions[f"marketplace:{p.get('name')}"] = p["version"]
    sv = L.server_version()
    vals = {k: v for k, v in versions.items() if not k.endswith("!")}
    ok = bool(vals) and None not in (vals.get("openjev-skills"), vals.get("openjev-mcp")) and \
        len(set(vals.values())) == 1 and sv in vals.values()
    rep.add("G2", None, ok, f"versions {vals}, server {sv}" if not ok else f"all {sv}")

    # G3 description disjointness
    bad = []
    toks = {s.name: _tokens(s.description) for s in skills}
    for a, b in itertools.combinations(sorted(toks), 2):
        u = toks[a] | toks[b]
        j = len(toks[a] & toks[b]) / len(u) if u else 0.0
        if j >= 0.45:
            bad.append(f"{a}~{b}={j:.2f}")
    rep.add("G3", None, not bad, "Jaccard >= 0.45: " + ", ".join(bad) if bad else "")

    # G4 marketplace sources
    mk, err = _json(L.MARKETPLACE)
    if not isinstance(mk, dict):
        rep.add("G4", None, False, f"marketplace.json unreadable: {err}")
    else:
        probs = []
        for p in mk.get("plugins", []):
            src = p.get("source")
            if not isinstance(src, str):
                probs.append(f"{p.get('name')}: non-string source")
                continue
            d = (L.REPO / src).resolve()
            if not (d / ".claude-plugin" / "plugin.json").is_file():
                probs.append(f"{p.get('name')}: {src} has no .claude-plugin/plugin.json")
        if not mk.get("plugins"):
            probs.append("no plugins listed")
        rep.add("G4", None, not probs, "; ".join(probs))

    # G5 no 8-word n-gram shared with held-out prompts
    prompts: list[str] = []
    for f in sorted(L.DATA_DIR.glob("*.json*")) if L.DATA_DIR.is_dir() else []:
        if f.suffix == ".jsonl":
            for ln in f.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    try:
                        prompts.extend(_prompt_strings(json.loads(ln)))
                    except ValueError:
                        pass
        else:
            data, _ = _json(f)
            prompts.extend(_prompt_strings(data))
    if not prompts:
        rep.add("G5", None, True, "no held-out prompt data found", skipped=True)
        return
    grams: dict[tuple, str] = {}
    for p in prompts:
        w = re.findall(r"\w+", p.lower())
        for g in _ngrams(w, 8):
            grams.setdefault(g, p[:60])
    hits = []
    for s in skills:
        for p in s.files():
            f = s.relfile(p)
            words: list[tuple[str, int]] = []
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                words.extend((w, i) for w in re.findall(r"\w+", line.lower()))
            seq = [w for w, _ in words]
            for k, g in enumerate(_ngrams(seq, 8)):
                if g in grams:
                    hits.append((f, words[k][1], f"shares 8 words with held-out prompt {grams[g]!r}"))
                    break
    if hits:
        d, f, ln = _fail_at(hits)
        rep.add("G5", None, False, d, f, ln)
    else:
        rep.add("G5", None, True, f"{len(prompts)} prompts checked")


def run(skills_dir: str | Path | None = None, root: Path | None = None, globals_: bool | None = None,
        run_claude: bool = True) -> dict:
    """Run all static checks. ``globals_`` defaults to True only for the real plugin tree."""
    skills = L.discover(skills_dir, root)
    rep = Report()
    for sk in skills:
        check_skill(rep, sk)
    if globals_ is None:
        globals_ = skills_dir is None
    if globals_:
        check_globals(rep, skills, run_claude)
    out = rep.result()
    out["skills"] = [s.name for s in skills]
    if not skills:
        out["score"] = 0.0
        out["error"] = "no skills found"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    ap.add_argument("--skills-dir", help="check another skills directory (globals are skipped)")
    ap.add_argument("--globals", action="store_true", help="force global checks with --skills-dir")
    ap.add_argument("--no-claude", action="store_true", help="skip the claude plugin validate checks (G1)")
    a = ap.parse_args(argv)
    res = run(a.skills_dir, globals_=True if a.globals else None, run_claude=not a.no_claude)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        for c in res["checks"]:
            if not c["ok"] and not c.get("skipped"):
                loc = f" [{c['file']}:{c['line']}]" if c.get("file") else ""
                print(f"FAIL {c['id']} {c['skill'] or '(global)'}: {c['detail']}{loc}")
        print(f"static score {res['score']:.3f} ({res['passed']}/{res['total']} checks, {res['skipped']} skipped)")
    return 0 if res["score"] >= 0.95 else 1


if __name__ == "__main__":
    sys.exit(main())
