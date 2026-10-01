#!/usr/bin/env python3
"""Run OpenJev use-case test files against a live server. Stdlib only.

    python docs/mcp-skill-spec/tests/run_cases.py docs/mcp-skill-spec/tests/cases/*.json
    python docs/mcp-skill-spec/tests/run_cases.py --json cases/03-*.json   # machine-readable

Env: OPENJEV_BASE_URL (default http://127.0.0.1:8080), OPENJEV_API_KEY (optional bearer).

Case file shape:
{
  "use_case": "support-ticket-triage",
  "cases": [
    {
      "id": "triage-01",
      "description": "what this checks and why",
      "endpoint": "/v1/systemone",            # optional, default /v1/systemone
      "method": "POST",                       # optional, GET for /v1/models
      "request": {"model": "openjev-latest", "state": "...", "questions": {...}},
      "expect": {
        "status": 200,                        # optional, default 200
        "body_contains": "Unknown model",     # optional, substring of raw body
        "answers": {                          # per question name, all keys optional
          "q": {"noul_gte": 0.7, "noul_lte": 0.3,
                "choice": "billing", "choice_in": ["a", "b"],
                "score_gte": 1.5, "score_lte": 2.5,
                "confidence_gte": 0.5,
                "prob_gte": {"billing": 0.6}}
        },
        "content_contains_any": ["yes", "Yes"] # chat completions: message content
      }
    }
  ]
}
Inside request.images, {"$file": "tests/data/hotdog.jpg"} (path relative to repo root) is
replaced with a base64 data URL.
"""
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BASE = os.environ.get("OPENJEV_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
KEY = os.environ.get("OPENJEV_API_KEY", "")


def resolve_images(req):
    imgs = req.get("images")
    if not isinstance(imgs, list):
        return req
    out = []
    for im in imgs:
        if isinstance(im, dict) and "$file" in im:
            path = os.path.join(ROOT, im["$file"])
            ctype = mimetypes.guess_type(path)[0] or "image/jpeg"
            with open(path, "rb") as f:
                out.append(f"data:{ctype};base64,{base64.b64encode(f.read()).decode()}")
        else:
            out.append(im)
    return {**req, "images": out}


def call(case):
    endpoint = case.get("endpoint", "/v1/systemone")
    method = case.get("method", "POST")
    data = None
    if method != "GET":
        data = json.dumps(resolve_images(case.get("request", {}))).encode()
    r = urllib.request.Request(BASE + endpoint, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if KEY:
        r.add_header("Authorization", f"Bearer {KEY}")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(r, timeout=600) as resp:
            status, body = resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode()
    return status, body, (time.monotonic() - t0) * 1000


def check(case, status, body):
    exp = case.get("expect", {})
    fails = []
    if status != exp.get("status", 200):
        fails.append(f"status {status} != {exp.get('status', 200)}")
    if "body_contains" in exp and exp["body_contains"] not in body:
        fails.append(f"body lacks {exp['body_contains']!r}")
    try:
        js = json.loads(body)
    except ValueError:
        js = None
    if "content_contains_any" in exp:
        content = ""
        try:
            content = js["choices"][0]["message"]["content"] or ""
        except (TypeError, KeyError, IndexError):
            pass
        if not any(s in content for s in exp["content_contains_any"]):
            fails.append(f"content {content[:80]!r} lacks any of {exp['content_contains_any']}")
    answers = (js or {}).get("answers", {}) if isinstance(js, dict) else {}
    for q, rules in exp.get("answers", {}).items():
        a = answers.get(q)
        if a is None:
            fails.append(f"{q}: missing answer")
            continue
        for rule, want in rules.items():
            if rule == "noul_gte" and not a.get("noul", -1) >= want:
                fails.append(f"{q}: noul {a.get('noul'):.3f} < {want}")
            elif rule == "noul_lte" and not a.get("noul", 2) <= want:
                fails.append(f"{q}: noul {a.get('noul'):.3f} > {want}")
            elif rule == "choice" and a.get("choice") != want:
                fails.append(f"{q}: choice {a.get('choice')!r} != {want!r}")
            elif rule == "choice_in" and a.get("choice") not in want:
                fails.append(f"{q}: choice {a.get('choice')!r} not in {want}")
            elif rule == "score_gte" and not a.get("score", -1) >= want:
                fails.append(f"{q}: score {a.get('score'):.2f} < {want}")
            elif rule == "score_lte" and not a.get("score", 99) <= want:
                fails.append(f"{q}: score {a.get('score'):.2f} > {want}")
            elif rule == "confidence_gte" and not a.get("confidence", -1) >= want:
                fails.append(f"{q}: confidence {a.get('confidence'):.3f} < {want}")
            elif rule == "prob_gte":
                for k, v in want.items():
                    p = a.get("probabilities", {}).get(k, -1)
                    if not p >= v:
                        fails.append(f"{q}: P({k}) {p:.3f} < {v}")
    return fails, answers


def main(argv):
    as_json = "--json" in argv
    files = [a for a in argv if a != "--json"]
    results, total, passed = [], 0, 0
    for path in files:
        with open(path) as f:
            suite = json.load(f)
        for case in suite["cases"]:
            status, body, ms = call(case)
            fails, answers = check(case, status, body)
            total += 1
            passed += not fails
            results.append({"file": os.path.basename(path), "id": case["id"], "ok": not fails,
                            "fails": fails, "status": status, "ms": round(ms),
                            "answers": answers if not fails else answers or body[:300]})
            if not as_json:
                mark = "PASS" if not fails else "FAIL"
                print(f"{mark} {os.path.basename(path)}::{case['id']} ({round(ms)} ms)"
                      + ("" if not fails else "  " + "; ".join(fails)))
    if as_json:
        print(json.dumps({"total": total, "passed": passed, "results": results}, indent=1))
    else:
        print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
