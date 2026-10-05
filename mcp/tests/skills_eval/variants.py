"""Skill-tree variants for before/after and sensitivity runs of the scorer.

  current  the canonical plugin skills (unchanged, no copy)
  head     the 11 skills as committed at the pinned BEFORE_COMMIT under mcp/skills (no hub), via git show
  mutant   a copy of ``current`` with deterministic damage the scorer must notice

Variants are materialised outside git under .tmp/skills-eval/variants/<name>/skills.
``skills_root(variant)`` returns that directory so the static, offline and trigger
checkers can be pointed at it. Pure stdlib; no network and no live runs.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from . import skill_lib as L
except ImportError:                      # run as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import skill_lib as L

VARIANTS = ("current", "head", "mutant")
VARIANTS_DIR = L.REPO / ".tmp" / "skills-eval" / "variants"
HEAD_SKILLS_PREFIX = "mcp/skills"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=L.REPO, check=True, capture_output=True,
                          text=True).stdout


def _fresh(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


# ---------- head ----------

def build_head() -> Path:
    """Extract every file under mcp/skills at BEFORE_COMMIT (the pre-refactor 11 skills)."""
    root = _fresh(VARIANTS_DIR / "head" / "skills")
    names = _git("ls-tree", "-r", "--name-only", L.BEFORE_COMMIT, HEAD_SKILLS_PREFIX).split()
    for name in names:
        dest = root / name[len(HEAD_SKILLS_PREFIX) + 1:]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_git("show", f"{L.BEFORE_COMMIT}:{name}"), encoding="utf-8")
    return root


# ---------- mutant ----------

_DESC = re.compile(r"^(description:\s*)(.*)$", re.M)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _desc_value(text: str) -> str:
    return _DESC.search(text).group(2)


def _set_desc(text: str, value: str) -> str:
    return _DESC.sub(lambda m: m.group(1) + value, text, count=1)


def _mut_rotate_descriptions(root: Path, log: list[str]) -> None:
    files = sorted(root.glob("*/SKILL.md"))
    if len(files) < 2:
        return
    texts = [_read(f) for f in files]
    descs = [_desc_value(t) for t in texts]
    for i, f in enumerate(files):
        f.write_text(_set_desc(texts[i], descs[(i + 1) % len(files)]), encoding="utf-8")
    log.append(f"rotated descriptions across {len(files)} skills")


def _first_block(root: Path, pred):
    for f in sorted(root.glob("*/SKILL.md")) + sorted(root.glob("*/references/*.md")):
        text = _read(f)
        for b in L.iter_blocks(text, str(f)):
            if pred(b):
                return f, text, b
    return None


def _mut_score_criteria(root: Path, log: list[str]) -> None:
    """Turn the first score criteria list into a dict (server answers E013)."""
    pat = re.compile(r'("type"\s*:\s*"score".*?"criteria"\s*:\s*)\[([^\]]*)\]', re.S)

    def has(b):
        return b.lang == "json" and pat.search(b.text) is not None

    hit = _first_block(root, has)
    if not hit:
        log.append("score criteria: no target found")
        return
    f, text, b = hit

    def repl(m):
        items = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2))
        body = ", ".join(f'"{i}": "{t}"' for i, t in enumerate(items))
        return m.group(1) + "{" + body + "}"

    new_block = pat.sub(repl, b.text, count=1)
    f.write_text(text.replace(b.text, new_block, 1), encoding="utf-8")
    log.append(f"score criteria list -> dict in {f.parent.name}/{f.name}")


def _mut_relative_output_path(root: Path, log: list[str]) -> None:
    pat = re.compile(r'"output_path"\s*:\s*"(/[^"]*)"')
    for f in sorted(root.glob("*/SKILL.md")) + sorted(root.glob("*/references/*.md")):
        text = _read(f)
        for b in L.iter_blocks(text, str(f)):
            if b.lang == "json" and pat.search(b.text):
                nb = pat.sub('"output_path": "out/results.jsonl"', b.text, count=1)
                f.write_text(text.replace(b.text, nb, 1), encoding="utf-8")
                log.append(f"relative output_path in {f.parent.name}/{f.name}")
                return
    log.append("relative output_path: no target found")


def _mut_unlabelled_block(root: Path, log: list[str]) -> None:
    pat = re.compile(r"^[ \t]*<!--\s*openjev-(?:call|result|questions)[^>]*-->[ \t]*\n", re.M)
    for f in sorted(root.glob("*/SKILL.md")):
        if f.parent.name == L.HUB:
            continue                                   # keep the hub's dry-run block intact
        text = _read(f)
        m = pat.search(text)
        if m:
            f.write_text(text[:m.start()] + text[m.end():], encoding="utf-8")
            log.append(f"removed one label in {f.parent.name}/SKILL.md")
            return
    log.append("unlabelled block: no target found")


def _mut_hub_references(root: Path, log: list[str]) -> None:
    ref = root / L.HUB / "references"
    if ref.is_dir():
        shutil.rmtree(ref)
        log.append("removed hub references/")
    else:
        log.append("hub references/: not present")


def _mut_second_person(root: Path, log: list[str]) -> None:
    for f in sorted(root.glob("*/SKILL.md")):
        if f.parent.name == L.HUB:
            continue
        text = _read(f)
        val = _desc_value(text)
        q = '"' if val.startswith('"') else ""
        inner = val[1:-1] if q else val
        new = f'{q}You can use this skill to help me. {inner}{q}'
        f.write_text(_set_desc(text, new), encoding="utf-8")
        log.append(f"second-person description in {f.parent.name}")
        return


def _mut_repo_leak(root: Path, log: list[str]) -> None:
    for f in sorted(root.glob("*/SKILL.md")):
        if f.parent.name == L.HUB:
            continue
        f.write_text(_read(f).rstrip("\n") + "\n\nSee mcp/spec.md and /Users/someone/openjev/mcp/README.md.\n",
                     encoding="utf-8")
        log.append(f"repo-relative and absolute home paths in {f.parent.name}")
        return


def build_mutant() -> Path:
    """Copy current skills and apply deterministic damage. Returns the skills dir."""
    if not L.SKILLS_DIR.is_dir():
        raise SystemExit(f"current skills dir missing: {L.SKILLS_DIR}")
    root = VARIANTS_DIR / "mutant" / "skills"
    _fresh(root.parent)
    shutil.copytree(L.SKILLS_DIR, root)
    log: list[str] = []
    # order matters: structural block edits first, description edits last
    _mut_score_criteria(root, log)
    _mut_relative_output_path(root, log)
    _mut_unlabelled_block(root, log)
    _mut_hub_references(root, log)
    _mut_repo_leak(root, log)
    _mut_second_person(root, log)
    _mut_rotate_descriptions(root, log)
    (root.parent / "mutations.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    return root


# ---------- api ----------

def skills_root(variant: str = "current", rebuild: bool = True) -> Path:
    """Directory holding ``<skill>/SKILL.md`` for the variant, materialising it if needed."""
    if variant == "current":
        return L.SKILLS_DIR
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; choose from {', '.join(VARIANTS)}")
    path = VARIANTS_DIR / variant / "skills"
    if rebuild or not path.is_dir():
        return build_head() if variant == "head" else build_mutant()
    return path


def mutations(variant: str = "mutant") -> list[str]:
    f = VARIANTS_DIR / variant / "mutations.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("variant", choices=VARIANTS, nargs="?", default="mutant")
    a = ap.parse_args(argv)
    root = skills_root(a.variant)
    print(root)
    for m in mutations(a.variant):
        print("  -", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
