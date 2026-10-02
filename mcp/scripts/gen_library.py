"""Generates openjev_mcp/data/{templates.json,patterns.json,guide_authoring.md} from the spec's verified
section 5 cases (docs/mcp-skill-spec/tests/cases/*.json), captured.json and spec section 3. Deterministic:
sorted keys and ids, no clock, no network. Run from anywhere: python mcp/scripts/gen_library.py"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO / "docs" / "mcp-skill-spec"
SPEC = SPEC_DIR / "OPENJEV_MCP_SKILLS_SPEC.md"
OUT = REPO / "mcp" / "openjev_mcp" / "data"
ID = re.compile(r"^[a-z0-9_]{1,64}$")
MIN_STATES, MAX_STATES = 5, 10


def dump(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def load_cases() -> list[tuple[str, str, list[dict]]]:
    """(file prefix, usage slug, cases) per case file, sorted by file name."""
    out = []
    for f in sorted((SPEC_DIR / "tests" / "cases").glob("*.json")):
        data = json.loads(f.read_text())
        usage = "spec_examples" if f.name.startswith("00-") else slug(data["use_case"])
        out.append((f.name[:2], usage, data["cases"]))
    return out


def plain(case: dict) -> bool:
    return (case.get("method", "POST") == "POST" and case.get("endpoint", "/v1/systemone") == "/v1/systemone"
            and isinstance(case.get("request", {}).get("questions"), dict)
            and isinstance(case["request"].get("state"), str))


def opts_of(req: dict) -> dict:
    return {k: v for k, v in req.items() if k not in ("state", "questions")}


def build_templates(files, captured) -> dict:
    out: dict[str, dict] = {}
    for _, usage, cases in files:
        groups: dict[str, list[dict]] = collections.defaultdict(list)
        for c in cases:
            if plain(c):
                r = c["request"]
                groups[json.dumps([r["questions"], opts_of(r)], sort_keys=True)].append(c)
        for key, grp in groups.items():
            if len(grp) < MIN_STATES:
                continue
            grp = sorted(grp, key=lambda c: c["id"])[:MAX_STATES]
            questions, options = json.loads(key)
            qids = sorted(questions)
            tid = f"{usage}_{'_'.join(slug(q) for q in qids)}"[:64].rstrip("_")
            assert ID.match(tid) and tid not in out, tid
            out[tid] = {
                "id": tid,
                "title": f"{usage.replace('_', ' ').capitalize()}: {', '.join(qids)}",
                "description": f"{len(grp)} example states for the question set {', '.join(qids)} "
                               f"({usage.replace('_', ' ')}); verified cases {grp[0]['id']} .. {grp[-1]['id']}.",
                "questions": questions, "options": options,
                "states": [{"id": slug(c["id"])[:64], "state": c["request"]["state"]} for c in grp],
                "source_case": grp[0]["id"],
                "source_cases": [c["id"] for c in grp],
                "captured": all(c["id"] in captured for c in grp),
            }
            assert len({s["id"] for s in out[tid]["states"]}) == len(grp), tid
    return dict(sorted(out.items()))


def reduce(ans: dict) -> dict:
    keep = ("type", "choice", "noul", "score", "confidence")
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in ans.items() if k in keep}


def build_patterns(files, captured) -> dict:
    out: dict[str, dict] = {}
    for prefix, usage, cases in files:
        defs: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        expects: dict[str, list] = collections.defaultdict(list)
        meas: dict[str, list] = collections.defaultdict(list)
        for c in sorted(cases, key=lambda c: c["id"]):
            if not plain(c):
                continue
            for qid, q in c["request"]["questions"].items():
                defs[qid][json.dumps(q, sort_keys=True)] += 1
                e = (c.get("expect") or {}).get("answers", {}).get(qid)
                if e:
                    expects[qid].append({"case": c["id"], **e})
                cap = captured.get(c["id"])
                if cap and qid in (cap.get("body") or {}).get("answers", {}):
                    meas[qid].append({"case": c["id"], **reduce(cap["body"]["answers"][qid])})
        for cid in sorted(captured):  # u01.. captured usage examples map to case file 01..
            m = re.match(r"u(\d\d)(?:-|$)", cid)
            if m and m.group(1) == prefix:
                for qid, ans in ((captured[cid].get("body") or {}).get("answers") or {}).items():
                    if qid in defs:
                        meas[qid].append({"case": cid, **reduce(ans)})
        for qid in sorted(defs):
            key = f"{usage}.{qid}"
            best = sorted(defs[qid].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            ent = {"usage": usage, "question_id": qid, "question": json.loads(best),
                   "variants": len(defs[qid])}
            if meas[qid]:
                ent["source"], ent["measured"] = "captured", meas[qid][:4]
            else:
                ent["source"], ent["measured"] = "expect", expects[qid][:4]
            out[key] = ent
    return dict(sorted(out.items()))


def build_guide() -> str:
    text = SPEC.read_text()
    m = re.search(r"^## 3\. Question-authoring guide\n.*?(?=\n---\n\n## 4\. )", text, re.S | re.M)
    assert m, "spec section 3 not found"
    return m.group(0).rstrip("\n") + "\n"


def main() -> None:
    files = load_cases()
    captured = json.loads((SPEC_DIR / "tests" / "spec_build" / "captured.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "templates.json").write_text(dump(build_templates(files, captured)))
    (OUT / "patterns.json").write_text(dump(build_patterns(files, captured)))
    (OUT / "guide_authoring.md").write_text(build_guide())


if __name__ == "__main__":
    main()
