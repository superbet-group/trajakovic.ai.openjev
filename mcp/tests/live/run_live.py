"""Live phase-1/2/3 conformance against a real OpenJev (not CI; needs the model). Sequential, one GPU.

    OPENJEV_BASE_URL=http://127.0.0.1:8080 python mcp/tests/live/run_live.py [--mcp http://127.0.0.1:8195/mcp]   # phase-1 calls
    ... run_live.py --gate                                                     # hook over the 14 gate cases
    ... run_live.py --full [--out mcp/tests/live/results/2026-10-phase2-3.json]  # P20: everything, own MCP on 8190-8199
Drives spec examples through the MCP tools and compares with the recorded expectations and with a direct /v1/systemone read.
--full starts its own MCP (and a Tasks-on one), never touches :8100, kills both in a finally, and writes the results file."""
from __future__ import annotations

import argparse, asyncio, csv, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
CASES = ROOT / "docs/mcp-skill-spec/tests/cases"
BASE = os.environ.get("OPENJEV_BASE_URL", "http://127.0.0.1:8080")
PY = str(ROOT / ".venv/bin/python")
HOOK = str(ROOT / ".venv/bin/openjev-hook")
DATA = HERE / "data"
FIX = ROOT / "mcp/tests/fixtures/claude_code"
OUT = HERE / "results/2026-10-phase2-3.json"
# 6.4 known limitations: a failing case here is the model, as documented
# spec 6.3 rerun column: file prefix -> (cases, passing)
SPEC_63 = {"00": (63, 63), "01": (20, 20), "02": (19, 18), "03": (14, 14), "04": (18, 18), "05": (14, 14), "06": (21, 20), "07": (17, 17), "08": (16, 16), "09": (20, 20),
           "10": (17, 17), "11": (13, 13), "12": (14, 14), "13": (13, 13), "14": (14, 14), "15": (15, 15), "16": (18, 18), "17": (17, 17), "18": (18, 18), "19": (16, 16),
           "20": (18, 17), "21": (19, 19), "22": (11, 11), "22b": (5, 5), "23": (13, 13), "24": (19, 18)}
KNOWN = {"02::gate-19", "06::KNOWN-LIMITATION-coasked-interference", "20::tax-18", "24::cal-10"}


def is_known(pre, cid):
    """6.4 known limitation or flake; ids are matched by prefix (cal-10 is cal-10-hard-decidable-think)."""
    return any(f"{pre}::{cid}".startswith(k) for k in KNOWN) or "known-limitation" in cid.lower()


def load(name):
    return {c["id"]: c for c in json.load(open(CASES / name))["cases"]}


def direct(req):
    r = urllib.request.Request(BASE + "/v1/systemone", json.dumps(req).encode(), {"content-type": "application/json"})
    t = time.monotonic()
    body = json.load(urllib.request.urlopen(r, timeout=60))
    return body, (time.monotonic() - t) * 1000


def mcp_args(case):
    req = case["request"]
    (k, q), = list(req["questions"].items())[:1]
    base = {"state": req["state"]}
    if len(req["questions"]) > 1:
        return "ask", {**base, "questions": req["questions"]}
    if q["type"] == "noul":
        c = q.get("criteria", {})
        return "yes_no", {**base, "claim": q["instructions"], **({"true_means": c["true"], "false_means": c["false"]} if c else {})}
    if q["type"] == "choice":
        return "classify", {**base, "question": q["instructions"], "labels": q["criteria"]}
    return "score", {**base, "question": q["instructions"], "levels": q["criteria"]}


def check(case, sc, tool):
    """Compare MCP structured content with the recorded expect for the first question; return mismatches."""
    exp = list(case["expect"]["answers"].values())[0]
    bad = []
    if tool == "yes_no":
        p = sc["p"]
        if "noul_gte" in exp and p < exp["noul_gte"]: bad.append(f"p {p:.4f} < {exp['noul_gte']}")
        if "noul_lte" in exp and p > exp["noul_lte"]: bad.append(f"p {p:.4f} > {exp['noul_lte']}")
    elif tool == "classify":
        if "choice" in exp:
            # the tool abstains (label null, abstained true, top = the escape/winner) as documented: top is the model's choice
            got = sc.get("top") if sc.get("abstained") else sc.get("label")
            if got != exp["choice"]: bad.append(f"label {sc.get('label')} top {sc.get('top')} abstained {sc.get('abstained')} != {exp['choice']}")
    elif tool == "score":
        s = sc.get("score")
        if "score_gte" in exp and s < exp["score_gte"]: bad.append(f"score {s} < {exp['score_gte']}")
    return bad


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, max(0, int(round(q * len(xs) + 0.5)) - 1))], 1) if xs else None


def lat_of(ms):
    return {"n": len(ms), "p50_ms": pct(ms, 0.5), "p95_ms": pct(ms, 0.95), "max_ms": round(max(ms), 1) if ms else None}


async def main(url, rec=None):
    ex = load("00-spec-examples.json")
    out = {"mismatches": [], "calls": 0}
    lat = []
    async with streamable_http_client(url) as (r, w, *_):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print("tools:", tools)
            for cid in ["ex-yes-no", "ex-classify-abstain", "ex-score", "ex-batch-1", "ex-batch-2", "ex-batch-3"]:
                c = ex[cid]
                tool, args = mcp_args(c)
                t = time.monotonic()
                res = await s.call_tool(tool, args)
                ms = (time.monotonic() - t) * 1000
                lat.append(ms); out["calls"] += 1
                sc = res.structured_content
                bad = ["is_error"] if res.is_error else check(c, sc, tool)
                # 4-decimal parity with a direct read of the same recorded request (batch cases have 2 questions: skip)
                if tool == "yes_no" and not res.is_error:
                    d, _ = direct({**c["request"], "samples": c["request"].get("samples", 1)})
                    dp = d["answers"]["q"]["noul"]
                    if round(dp, 4) != round(sc["p"], 4): bad.append(f"parity direct {dp:.4f} vs mcp {sc['p']:.4f}")
                print(("PASS" if not bad else "FAIL"), cid, tool, f"{ms:.0f} ms", json.dumps(sc)[:160] if sc else res.content[0].text[:160], bad)
                if bad: out["mismatches"].append((cid, bad))
            # multi-question ask, ex-ask / ex-filter (as ask)
            for cid in ["ex-ask", "ex-filter"]:
                c = ex[cid]; t = time.monotonic()
                res = await s.call_tool("ask", {"state": c["request"]["state"], "questions": c["request"]["questions"]})
                lat.append((time.monotonic() - t) * 1000); out["calls"] += 1
                sc = res.structured_content; bad = []
                if res.is_error: bad = ["is_error " + res.content[0].text[:200]]
                else:
                    for q, e in c["expect"]["answers"].items():
                        a = sc["answers"][q]
                        p = a.get("noul", a.get("p"))
                        if "noul_gte" in e and (p is None or p < e["noul_gte"]): bad.append(f"{q} {p} < {e['noul_gte']}")
                        if "noul_lte" in e and (p is None or p > e["noul_lte"]): bad.append(f"{q} {p} > {e['noul_lte']}")
                        if "choice" in e and a.get("choice") != e["choice"]: bad.append(f"{q} choice {a.get('choice')} != {e['choice']}")
                        if "score_gte" in e and a.get("score", 0) < e["score_gte"]: bad.append(f"{q} score {a.get('score')} < {e['score_gte']}")
                print(("PASS" if not bad else "FAIL"), cid, "ask", f"{lat[-1]:.0f} ms", json.dumps(sc)[:200], bad)
                if bad: out["mismatches"].append((cid, bad))
            # lint: bad then fixed
            for cid in ["ex-lint-bad-422", "ex-lint-fixed"]:
                c = ex[cid]; req = c["request"]
                res = await s.call_tool("lint", {"request": req})
                lat.append(0.0); out["calls"] += 1
                print("lint", cid, json.dumps(res.structured_content)[:260], "expect", json.dumps(c["expect"])[:120])
            res = await s.call_tool("status", {"probe": True}); out["calls"] += 1
            print("status healthy:", res.structured_content["healthy"], "probe_ms:", res.structured_content.get("latency_probe_ms"))
    lat.sort()
    print(f"mcp calls {out['calls']} mean {sum(lat)/len(lat):.0f} ms p50 {lat[len(lat)//2]:.0f} max {lat[-1]:.0f}; mismatches {len(out['mismatches'])}")
    out["lat"] = lat_of([x for x in lat if x])
    return out


def hook_run(args, payload, env=None):
    t = time.monotonic()
    p = subprocess.run([HOOK, *args], input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                       env={**os.environ, "OPENJEV_BASE_URL": BASE, **(env or {})})
    o = json.loads(p.stdout) if p.stdout.strip() else {}
    return o, (time.monotonic() - t) * 1000, p


def pre_decision(o):
    h = o.get("hookSpecificOutput", {})
    return h.get("permissionDecision", "(none)"), h.get("permissionDecisionReason", "")


def split_state(st):
    task = st.split("\nProposed shell command: ")[0].removeprefix("Task requested by the user: ")
    cmd = st.split("\nProposed shell command: ", 1)[1] if "\nProposed shell command: " in st else None
    return task, cmd


def gate_hook():
    # deviation: 6.7 openjev-hook row: gate-13/14 carry no command, so "14/14" = 12 verdict cases through the hook + gate-13 (bad model env -> 400,
    # fail closed ask / deny --unattended) + gate-14 (raw 400; the MCP-layer refusal is checked separately via the ask tool)
    """The 14 gate cases through openjev-hook pretooluse: 12 verdict cases, gate-13 (unknown model) and gate-14 (empty
    options) as error cases that must fail closed. Returns (passed, rows)."""
    cases = load("03-agent-tool-call-gate.json")
    rows, ms_all = [], []
    for cid, c in cases.items():
        st = c["request"]["state"]
        task, cmd = split_state(st)
        row = {"case": cid}
        if cmd is None:
            row.update(kind="error-case")
            if cid == "gate-13":
                # unknown model -> OpenJev 400 "Unknown model" -> the hook must ask (deny with --unattended)
                payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "rm -rf ./build"}, "cwd": str(ROOT)}
                o, ms, _ = hook_run(["pretooluse", "--task", "clean the build dir"], payload, {"OPENJEV_MCP_MODEL": "jev-1.13.0"})
                o2, _, _ = hook_run(["pretooluse", "--task", "clean the build dir", "--unattended"], payload, {"OPENJEV_MCP_MODEL": "jev-1.13.0"})
                o3, _, _ = hook_run(["pretooluse", "--task", "clean the build dir"], payload, {"OPENJEV_BASE_URL": "http://127.0.0.1:8199"})
                d, d2, d3 = pre_decision(o)[0], pre_decision(o2)[0], pre_decision(o3)[0]
                row.update(got=f"bad-model:{d}/unattended:{d2}/unreachable:{d3}", ok=(d, d2, d3) == ("ask", "deny", "ask"), ms=ms,
                           reason=pre_decision(o)[1][:110])
            else:
                # empty choice options: raw 400 from OpenJev, and the MCP layer must validate locally before calling
                try:
                    urllib.request.urlopen(urllib.request.Request(BASE + "/v1/systemone", json.dumps(c["request"]).encode(), {"content-type": "application/json"}), timeout=30)
                    raw = 200
                except urllib.error.HTTPError as e:
                    raw = e.code
                row.update(got=f"raw {raw}", ok=raw == 400, ms=0.0, reason="MCP local refusal is checked in the mcp section (gate-14 ask)")
        else:
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(ROOT)}
            o, ms, _ = hook_run(["pretooluse", "--task", task], payload)
            dec, why = pre_decision(o)
            v = c["expect"]["answers"]["verdict"]
            ok = dec == v["choice"] if "choice" in v else dec in v["choice_in"]
            row.update(kind="verdict", got=dec, want=v, ok=ok, ms=ms, reason=why[:110])
            ms_all.append(ms)
        rows.append(row)
        print("PASS" if row["ok"] else "FAIL", cid, row["got"], f"{row['ms']:.0f} ms", "|", row["reason"])
    n = sum(r["ok"] for r in rows)
    print(f"hook {n}/{len(rows)} (12 verdict cases + gate-13/14 error cases failing closed)")
    return n, rows, lat_of(ms_all)


# ----------------------------------------------------------------------------- the full run

SP_STATES = None


def section5_states():
    """States of the section 5 usage examples (u01..u23) and the ex-batch tickets; the pool the 200-row CSV is cut from."""
    ex = load("00-spec-examples.json")
    out = [c["request"]["state"] for k, c in ex.items() if (k.startswith("u") or k.startswith("ex-batch")) and c["request"].get("state")]
    return [s.replace("\r", "") for s in out]


def make_csv(path: Path, n=200):
    # deviation: 6.7 batch row: the 200 rows are the ~30 section 5 (u01..u23) and ex-batch states cycled with a unique "[ref NNN]" prefix, not 200 distinct tickets
    pool = section5_states()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "state"])
        for i in range(n):
            # deterministic: base state i % len(pool) with a unique reference line so no two rows are equal
            w.writerow([f"r{i + 1:03d}", f"[ref {i + 1:03d}] {pool[i % len(pool)]}"])
    return len(pool)


QS = {"urgent": {"type": "noul", "instructions": "Does this need a human to look at it today?",
                 "criteria": {"true": "something is broken, blocked, leaking money or data, or has a deadline", "false": "routine, informational, or can wait"}},
      "kind": {"type": "choice", "instructions": "Which kind of item is this?",
               "criteria": {"support": "a customer or user asking for help or reporting a problem", "engineering": "code, logs, builds, agent runs or technical work",
                            "security": "secrets, injection, phishing, destructive commands", "other": "anything else, or too vague to tell"}}}


class Rec:
    def __init__(self):
        self.checks, self.bugs = [], []

    def add(self, name, ok, lat=None, mism=None, **extra):
        c = {"check": name, "pass": bool(ok), **({"latency": lat_of(lat)} if lat else {}), "mismatches": mism or [], **extra}
        self.checks.append(c)
        print(("PASS" if ok else "FAIL"), name, json.dumps(extra)[:220], *(["|", json.dumps(mism)[:300]] if mism else []))
        return c


def mm(case, detail, cls, repro):
    return {"case": case, "detail": detail, "class": cls, "repro": repro}


def spawn_mcp(port, roots, extra_env=None):
    env = {**os.environ, "OPENJEV_BASE_URL": BASE, "OPENJEV_MCP_ROOTS": ":".join(map(str, roots)), **(extra_env or {})}
    log = open(Path(tempfile.gettempdir()) / f"run_live_mcp_{port}.log", "w")
    p = subprocess.Popen([PY, "-m", "openjev_mcp", "--transport", "http", "--port", str(port)], env=env, stdout=log, stderr=log, cwd=str(ROOT))
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return p
        except Exception:
            time.sleep(0.25)
    p.kill()
    raise RuntimeError(f"mcp on {port} did not come up")


async def call(s, tool, args, timeout=300):
    t = time.monotonic()
    res = await s.call_tool(tool, args, read_timeout_seconds=timeout)
    return res, (time.monotonic() - t) * 1000


def sc_of(res):
    return res.structured_content if res.structured_content is not None else {"_text": res.content[0].text if res.content else ""}


def read_rows(path):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    return rows[0], rows[1:]


async def drain(s, args, max_calls=60):
    """Follow next_cursor until null; returns (per-call records, wall seconds)."""
    calls, cursor, t0 = [], None, time.monotonic()
    for _ in range(max_calls):
        res, ms = await call(s, "batch", {**args, **({"cursor": cursor} if cursor else {})})
        sc = sc_of(res)
        if res.is_error or "status" not in sc:
            calls.append({"error": sc, "ms": ms}); break
        st = sc["status"]
        calls.append({"ms": round(ms), "done": st["done"], "ok": st["ok"], "errors": st["errors"], "skipped": st["skipped"], "remaining": st.get("remaining"),
                      "stopped_reason": st["stopped_reason"], "req_per_s": st.get("req_per_s"), "effective_concurrency": st.get("effective_concurrency"),
                      "has_cursor": bool(sc["next_cursor"]), "exports": [e["path"] for e in sc.get("exports", [])]})
        cursor = sc["next_cursor"]
        if not cursor: break
    return calls, time.monotonic() - t0


def dup_gap(path, ids):
    _, rows = read_rows(path)
    last, seen, dups = {}, {}, []
    for r in rows:
        seen[r["id"]] = seen.get(r["id"], 0) + 1
        last[r["id"]] = r
    ok_dups = sorted(i for i, n in seen.items() if sum(1 for r in rows if r["id"] == i and r["status"] == "ok") > 1)
    gaps = [i for i in ids if i not in last or last[i]["status"] != "ok"]
    return {"rows_in_file": len(rows), "unique_ids": len(last), "ids_with_more_than_one_row": sorted(i for i, n in seen.items() if n > 1)[:20],
            "duplicate_ok_ids": ok_dups[:20], "n_duplicate_ok": len(ok_dups), "gaps": gaps[:20], "n_gaps": len(gaps)}


async def full(out_path: Path, quick=False):
    rec = Rec()
    h = urllib.request.urlopen(BASE + "/health", timeout=5).read().decode()
    if '"ok"' not in h:
        sys.exit("OpenJev /health is not ok: " + h)
    res = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "base_url": BASE, "model_health": json.loads(h), "checks": rec.checks}
    work = Path(tempfile.mkdtemp(prefix="oj_live_"))
    procs = []
    try:
        # ---- run_cases over every case file (direct to OpenJev, no MCP), against the 6.3 table
        counts, raw, rawc = {}, {}, {}
        files = sorted(f.name for f in CASES.glob("[0-9]*.json"))
        for fn in (() if quick else files):
            pre = fn.split("-")[0]
            p = subprocess.run([PY, str(ROOT / "docs/mcp-skill-spec/tests/run_cases.py"), "--json", str(CASES / fn)], capture_output=True, text=True, timeout=1800)
            j = json.loads(p.stdout)
            bad = [r for r in j["results"] if not r["ok"]]
            for r in j["results"]: raw[(pre, r["id"])] = r["ok"]
            n63, ok63 = SPEC_63.get(pre, (None, None))
            rawc[pre] = (j["passed"], j["total"])
            counts[fn] = f"{j['passed']}/{j['total']}" + (f" (6.3 rerun {ok63}/{n63})" if n63 else "")
            known = [r for r in bad if is_known(pre, r["id"])]
            rec.add(f"run_cases {fn}", len(bad) == len(known) and (ok63 is None or j["passed"] >= ok63), [r["ms"] for r in j["results"]],
                    [mm(f"{pre}::{r['id']}", "; ".join(r["fails"])[:300] + (" [known limitation 6.4]" if r in known else ""), "model" if r["status"] == 200 else "server",
                        f"run_cases.py {fn} (case {r['id']})") for r in bad], passed=j["passed"], total=j["total"], spec_6_3=f"{ok63}/{n63}" if n63 else "n/a")
        res["case_file_counts"] = {"run_cases": counts}

        # ---- hook over the 14 gate cases
        n, rows, lat = gate_hook()
        rec.add("hook pretooluse 14 gate cases", n == 14, [r["ms"] for r in rows if r.get("kind") == "verdict"],
                [mm(f"03::{r['case']}", f"got {r['got']} want {r.get('want')} {r['reason']}", "model", "openjev-hook pretooluse --task ...") for r in rows if not r["ok"]],
                passed=n, total=len(rows), gate_10=next(r["got"] for r in rows if r["case"] == "gate-10"), rows=[{k: r[k] for k in ("case", "got", "ok")} for r in rows])

        # ---- recipe live files (pytest --live), per case file
        if not quick:
            by_file = await asyncio.to_thread(recipe_files, rec, raw)
            res["case_file_counts"]["recipes_pytest_live"] = {k: f"{v['pass']}/{v['pass'] + v['fail']} ran, {v['skip']} skipped, recipes {','.join(v['recipes'])}" for k, v in sorted(by_file.items())}
            res["pass_table_vs_6_3"] = [{"file": fn, "cases": SPEC_63.get(fn.split("-")[0], (None,))[0], "spec_6_3_rerun": SPEC_63.get(fn.split("-")[0], (None, None))[1],
                                         "run_cases_now": f"{rawc[fn.split('-')[0]][0]}/{rawc[fn.split('-')[0]][1]}" if fn.split("-")[0] in rawc else None,
                                         "recipe_pytest_live": (lambda v: f"{v['pass']}/{v['pass'] + v['fail']} of the {v['pass'] + v['fail'] + v['skip']} cases that map onto recipes" if v else "no recipe")(by_file.get(fn))}
                                        for fn in files]

        # ---- own MCP
        (DATA).mkdir(exist_ok=True)
        csv_path = DATA / "batch_200.csv"
        pool = make_csv(csv_path)
        res["csv"] = {"path": str(csv_path.relative_to(ROOT)), "rows": 200, "pool_states": pool, "sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest()[:16]}
        port = int(os.environ.get("OJ_LIVE_PORT", 8191))
        procs.append(spawn_mcp(port, [DATA, work, ROOT / "tests/data", FIX]))
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            async with streamable_http_client(url) as (r, w, *_):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    await mcp_section(s, rec, res, csv_path, work, url)
        except BaseException as e:  # a harness failure is a failed check, not a lost run
            import traceback
            rec.add("harness error in mcp section", False, None, [mm("harness", "".join(traceback.format_exception_only(type(e), e))[:300] + str(getattr(e, "exceptions", ""))[:300], "test bug", "run_live.py --full")])
        # ---- phase-1 calls through MCP (ex-*)
        t1 = await main(url)
        rec.add("mcp phase-1 ex-* calls", not t1["mismatches"], None, [mm(c, "; ".join(b), "test bug" if "abstain" in c else "model", f"run_live.py main ({c})") for c, b in t1["mismatches"]],
                calls=t1["calls"], latency=t1["lat"])
        # ---- Tasks
        procs.append(spawn_mcp(port + 1, [DATA, work], {"OPENJEV_MCP_TASKS": "on"}))
        for fn, a in ((tasks_section, (rec, f"http://127.0.0.1:{port + 1}/mcp")), (hooks_section, (rec,))):
            try:
                r_ = fn(*a)
                if asyncio.iscoroutine(r_): await r_
            except Exception as e:
                rec.add(f"harness error in {fn.__name__}", False, None, [mm("harness", repr(e)[:300], "test bug", "run_live.py --full")])
    finally:
        for p in procs:
            p.terminate()
            try: p.wait(5)
            except Exception: p.kill()
        shutil.rmtree(work, ignore_errors=True)
        res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        allm = [dict(m, check=c["check"]) for c in rec.checks for m in c["mismatches"]]
        res["mismatches"] = allm
        res["open_bugs"] = [m for m in allm if re.search(r"bug|server|recipe", m["class"]) and "known limitation" not in m["class"]]
        res["summary"] = {"checks": len(rec.checks), "passed": sum(c["pass"] for c in rec.checks), "failed": [c["check"] for c in rec.checks if not c["pass"]]}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(res, indent=1, default=str) + "\n")
        print("wrote", out_path)


def recipe_files(rec, raw=None):
    """pytest --live on the three recipe files; per-recipe case counts grouped by spec case file."""
    sys.path.insert(0, str(ROOT / "mcp/tests"))
    sys.path.insert(0, str(ROOT / "mcp"))
    import recipe_harness as rh
    t = time.monotonic()
    p = subprocess.run([PY, "-m", "pytest", "--live", "-q", "-s", "-p", "no:cacheprovider", "mcp/tests/live/test_live_recipes_p2.py", "mcp/tests/live/test_live_recipes_p3a.py",
                        "mcp/tests/live/test_live_recipes_p3b.py"], capture_output=True, text=True, cwd=str(ROOT), timeout=3600, env={**os.environ, "OPENJEV_BASE_URL": BASE})
    wall = time.monotonic() - t
    per = {}
    from openjev_mcp.config import load_config
    from openjev_mcp.recipes.registry import load_all
    ids = sorted(load_all(load_config({})).ids(), key=len, reverse=True)
    pat = re.compile(r"^[.FsEx]*(" + "|".join(map(re.escape, ids)) + r") ([\w.\-]+): (pass|fail|skip) ?(.*)$")   # -s output follows the progress dots
    for line in p.stdout.splitlines():
        m = pat.match(line)
        if m: per.setdefault(m[1], []).append((m[2], m[3], m[4]))
    by_file, mism = {}, []
    for rid, cases in per.items():
        try: tf = os.path.basename(rh.load(rid).test_file)
        except Exception: tf = "?"
        d = by_file.setdefault(tf, {"recipes": [], "pass": 0, "fail": 0, "skip": 0, "failed_cases": []})
        d["recipes"].append(rid)
        for cid, st, det in cases:
            d[st] += 1
            if st == "fail":
                pre = tf.split("-")[0]
                key = f"{pre}::{cid}"
                d["failed_cases"].append(cid)
                if is_known(pre, cid): cls = "model (known limitation or flake, 6.4)"
                elif raw and raw.get((pre, cid)) is False: cls = "model (the raw case fails in run_cases too)"
                elif raw and raw.get((pre, cid)) is True: cls = "recipe/engine (raw case passes in run_cases, the recipe's own questions fail)"
                else: cls = "model"
                mism.append(mm(key, f"[{rid}] {det[:240]}", cls, f"pytest --live mcp/tests/live -k {rid}; run_cases.py {tf} for the raw case"))
    summary = [l for l in p.stdout.splitlines() if re.search(r"\d+ (passed|failed|skipped)", l)]
    rec.add("pytest --live recipe files (P07 P17 P18)", p.returncode == 0 or all("known limitation" in m["class"] for m in mism), None, mism, exit=p.returncode, wall_s=round(wall), pytest_summary=summary[-1] if summary else p.stdout[-300:],
            by_case_file={k: v for k, v in sorted(by_file.items())})
    return by_file


async def mcp_section(s, rec, res, csv_path, work, url):
    # ---- gate-14 through MCP: empty choice options must be refused before any request
    r, ms = await call(s, "ask", {"state": "x", "questions": {"v": {"type": "choice", "instructions": "pick", "criteria": {}}}})
    sc = sc_of(r)
    rec.add("gate-14 via MCP ask (empty options refused locally)", r.is_error and "OJ_INVALID_INPUT" in json.dumps(sc), [ms], [] if r.is_error else [mm("03::gate-14", "not refused", "MCP bug", "ask choice with criteria {}")], got=json.dumps(sc)[:140])

    # ---- filter ex-filter
    ex = load("00-spec-examples.json")
    fa = ex["ex-filter"]["request"]
    lines = re.findall(r"^(L\d) (.*)$", fa["state"], re.M)
    crit = fa["questions"]["L1"]
    args = {"task": "find log lines that show a real failure an on-call engineer must act on.", "items_label": "LOG LINES", "items": [{"id": i, "text": t} for i, t in lines],
            "criterion": crit["instructions"].replace("L1", "{id}"), "true_means": crit["criteria"]["true"], "false_means": crit["criteria"]["false"]}
    lat, bad = [], []
    for _ in range(3):
        r, ms = await call(s, "filter", args); lat.append(ms); sc = sc_of(r)
        if r.is_error or sc.get("kept") != ["L4"]: bad = [mm("00::ex-filter", f"kept {sc.get('kept')} grey {sc.get('grey')}", "model" if not r.is_error else "MCP bug", "filter with the ex-filter log lines")]
    rec.add("filter ex-filter (x3)", not bad, lat, bad, kept=sc.get("kept"), items=sc.get("items"), requests=sc.get("meta", {}).get("requests"))

    # ---- ask_image ex-image
    r, ms = await call(s, "ask_image", {"images": [{"path": str(ROOT / "tests/data/hotdog.jpg")}], "state": "Look at the photo.", "questions": ex["ex-image"]["request"]["questions"]})
    sc = sc_of(r); a = sc.get("answers", {})
    ok = not r.is_error and a["hotdog"]["p"] >= 0.9 and a["cat"]["p"] <= 0.1
    rec.add("ask_image ex-image", ok, [ms], [] if ok else [mm("00::ex-image", json.dumps(sc)[:200], "model" if not r.is_error else "MCP bug", "ask_image hotdog.jpg")], hotdog=a.get("hotdog", {}).get("p"), cat=a.get("cat", {}).get("p"))

    # ---- calibrate ex-cal-1..7
    exs = [{"id": k, "state": ex[k]["request"]["state"], "label": {"escalate": ex[k]["label"]}} for k in [f"ex-cal-{i}" for i in range(1, 8)]]
    qs = ex["ex-cal-1"]["request"]["questions"]
    lat, last = [], None
    for _ in range(2):  # calibrate twice (6.4 cal-10 rule: repeated reads that decide gates)
        r, ms = await call(s, "calibrate", {"questions": qs, "examples": exs, "options": {"samples": 1}}); lat.append(ms); last = sc_of(r)
    pq = (last.get("per_question") or {}).get("escalate", {})
    ok = pq.get("accuracy_at_0.5") == 1.0 and pq.get("separable") is True and pq.get("most_borderline") == "ex-cal-7"
    rec.add("calibrate ex-cal-1..7 (x2)", ok, lat, [] if ok else [mm("00::ex-cal", json.dumps(pq)[:300], "model", "calibrate with ex-cal-1..7 labelled examples")],
            accuracy=pq.get("accuracy_at_0.5"), separable=pq.get("separable"), gap=pq.get("gap"), t_fit=pq.get("t_fit"), suggested_band=pq.get("suggested_band"), most_borderline=pq.get("most_borderline"))

    # ---- compile ex-compile-*
    lat, bad, got = [], [], {}
    r, ms = await call(s, "compile", {"intent": "tell me if a support email is angry and whether billing, tech or sales should take it"}); lat.append(ms); sc = sc_of(r)
    got["recipe"] = sc.get("recipe", {}).get("id")
    if got["recipe"] != "ticket_triage": bad.append(mm("00::ex-compile-recipe", json.dumps(sc.get("recipe"))[:160], "model", "compile intent ticket_triage"))
    r, ms = await call(s, "compile", {"intent": "write me a release announcement for version 2.0"}); lat.append(ms); sc = sc_of(r)
    got["none"] = sc.get("recipe", {}).get("id")
    if got["none"] != "none": bad.append(mm("00::ex-compile-recipe-none", json.dumps(sc.get("recipe"))[:160], "model", "compile intent generation"))
    subs = [ex[f"ex-compile-qtype-{i}"]["request"]["state"].removeprefix("A human wants this decided about an input: ") for i in range(1, 5)]
    want = [ex[f"ex-compile-qtype-{i}"]["expect"]["answers"]["qtype"]["choice"] for i in range(1, 5)]
    r, ms = await call(s, "compile", {"intent": "tell me if a support email is angry and whether billing, tech or sales should take it", "sub_decisions": subs}); lat.append(ms); sc = sc_of(r)
    qt = [d.get("qtype") or d.get("type") for d in sc.get("sub_decisions", [])]
    got["qtypes"] = qt
    if qt != want: bad.append(mm("00::ex-compile-qtype", f"{qt} != {want}", "model", "compile with the four ex-compile-qtype sub_decisions"))
    rec.add("compile ex-compile-*", not bad, lat, bad, got=got)

    # ---- generate x5 (empty-reply flake) plus direct chat reads for the raw rate
    gen_args = {"messages": [{"role": "user", "content": "What is 2+2? Answer with one number."}], "max_tokens": 16}
    lat, outs = [], []
    for _ in range(5):
        r, ms = await call(s, "generate", gen_args); lat.append(ms); sc = sc_of(r)
        outs.append({"content": sc.get("content"), "retried": sc.get("retried"), "error": r.is_error})
    raw = []
    for _ in range(5):
        rq = urllib.request.Request(BASE + "/v1/chat/completions", json.dumps({"model": "diffusiongemma-26b", "max_tokens": 16, "messages": gen_args["messages"]}).encode(), {"content-type": "application/json"})
        try: raw.append(json.load(urllib.request.urlopen(rq, timeout=60))["choices"][0]["message"]["content"])
        except Exception as e: raw.append(f"ERR {e}")
    good = [o for o in outs if o["content"] and "4" in o["content"]]
    empties_raw = sum(1 for x in raw if not x.strip())
    rec.add("generate ex-generate x5", len(good) == 5, lat, [mm("00::ex-generate", json.dumps(o), "model", "generate What is 2+2 (empty-reply flake)") for o in outs if o not in good],
            n_ok=len(good), retried=sum(bool(o["retried"]) for o in outs), direct_chat_empty_replies=f"{empties_raw}/5", direct_chat=raw)

    # ---- the 200-row CSV batch at concurrency 2, following next_cursor
    outp = work / "b200.jsonl"
    exp = {f: work / f"b200.{e}" for f, e in (("csv", "csv"), ("markdown", "md"), ("ojui-batch", "json"))}
    base = {"items_file": {"path": str(csv_path), "state_field": "state", "id_field": "id"}, "questions": QS, "concurrency": 2, "output_path": str(outp), "max_items_per_call": 50,
            "time_budget_s": 120, "export": [{"format": f, "path": str(p)} for f, p in exp.items()], "audit": {"rate": 0.05, "seed": 7}}
    calls, wall = await drain(s, base)
    ids = [f"r{i + 1:03d}" for i in range(200)]
    dg = dup_gap(outp, ids) if outp.exists() else {"n_gaps": 200, "n_duplicate_ok": 0}
    tot_done = sum(c.get("done", 0) for c in calls)
    rps = round(tot_done / wall, 2)
    ok = tot_done == 200 and dg["n_gaps"] == 0 and dg["n_duplicate_ok"] == 0 and calls and not calls[-1].get("has_cursor", True) and all("error" not in c for c in calls)
    errs = sum(c.get("errors", 0) for c in calls)
    rec.add("batch 200-row CSV concurrency 2 (cursor loop)", ok, [c["ms"] for c in calls if "ms" in c],
            [] if ok else [mm("batch-200", f"done {tot_done} gaps {dg['n_gaps']} dups {dg['n_duplicate_ok']} errors {errs} {json.dumps(calls[-1])[:200]}", "MCP bug", "batch items_file=batch_200.csv concurrency=2 max_items_per_call=50")],
            calls=len(calls), wall_s=round(wall, 1), req_per_s=rps, per_call=calls, rows_ok=tot_done - errs, rows_error=errs, effective_concurrency=calls[0].get("effective_concurrency") if calls else None, **dg)
    res["batch_runs"] = {"run_200": {"calls": len(calls), "wall_s": round(wall, 1), "req_per_s_measured": rps, "concurrency": 2, **dg}}
    res["spec_7_26_req_per_s"] = {"measured_batch_concurrency_2": rps, "note": "wall-clock rows / wall seconds over the whole cursor loop, 2 questions per row, sampling fast (samples 1 plus regrey re-read)"}

    # ---- batch_results review / stats / rows / exports / byte-identity
    full_out = outp
    for view in ("review", "stats", "rows"):
        r, ms = await call(s, "batch_results", {"path": str(full_out), "view": view, "limit": 20}); sc = sc_of(r)
        rec.add(f"batch_results view={view}", not r.is_error, [ms], [] if not r.is_error else [mm("batch_results", json.dumps(sc)[:200], "MCP bug", f"batch_results view={view}")], keys=sorted(sc)[:8])
    bad, ident = [], {}
    for fmt, ext in (("csv", "csv"), ("markdown", "md"), ("ojui-batch", "json"), ("jsonl", "jsonl")):
        p2 = work / f"br.{ext}"
        r, ms = await call(s, "batch_results", {"path": str(full_out), "export": {"format": fmt, "path": str(p2)}})
        if r.is_error: bad.append(mm("batch_results export", f"{fmt}: {json.dumps(sc_of(r))[:160]}", "MCP bug", f"batch_results export {fmt}")); continue
        if fmt in exp:
            a, b = exp[fmt].read_bytes(), p2.read_bytes()
            if fmt == "ojui-batch":
                # exportedAt aside (spec 6.7); title is the file stem of the export path, so it differs by construction
                norm = lambda x: re.sub(rb'"(exportedAt|title)":\s*"[^"]*"', b"", x)
                a, b = norm(a), norm(b)
            ident[fmt] = a == b
            if a != b:
                bad.append(mm("batch_results export", f"{fmt} differs from the batch export of the same file: batch_results rebuilds `questions` from the JSONL header, which keeps only the question hash, so instructions/criteria are missing in the export", "MCP bug", f"batch with export ojui-batch, then batch_results export ojui-batch on the same output_path; diff the two files ({len(a)} vs {len(b)} bytes)"))
    # filtered jsonl export readable by batch and batch_results
    fj = work / "filtered.jsonl"
    r, _ = await call(s, "batch_results", {"path": str(full_out), "filter": {"needs_review": True}, "export": {"format": "jsonl", "filtered": True, "path": str(fj)}})
    nrev = sc_of(r).get("matched") if not r.is_error else None
    r2, _ = await call(s, "batch_results", {"path": str(fj), "view": "stats"})
    r3, _ = await call(s, "batch", {"items_file": {"path": str(fj), "format": "jsonl", "state_field": "state", "id_field": "id"}, "questions": QS, "dry_run": True})
    ok_read = not r.is_error and not r2.is_error and not r3.is_error
    if not ok_read: bad.append(mm("batch_results filtered jsonl", f"{[sc_of(x) for x in (r, r2, r3) if x.is_error][:1]}", "MCP bug", "export filtered jsonl then read with batch_results/batch dry_run"))
    rec.add("batch_results exports byte-identical + filtered jsonl readable", not bad, None, bad, identical=ident, filtered_rows=nrev)

    # ---- interrupted then resumed batch (80 rows of the same csv, same ids and questions), cancel mid-call
    outi = work / "bint.jsonl"
    ib = {"items_file": {"path": str(csv_path), "state_field": "state", "id_field": "id"}, "max_items": 80, "questions": QS, "concurrency": 2, "output_path": str(outi),
          "max_items_per_call": 80, "time_budget_s": 120, "audit": {"rate": 0.05, "seed": 7}}
    t0 = time.monotonic()
    # deviation: 6.7 batch row: the recorded interrupted run cancels by closing a 2026-07-28 POST (the HTTP cancellation spelling); the SDK legacy-era
    # ClientSession cancel (notifications/cancelled) is a no-op on stateless HTTP and is recorded separately as a failed check below
    cancelled = await raw_interrupt(url, ib, 4.0)   # 2026-07-28 wire: closing the POST is the cancellation
    await asyncio.sleep(2)  # in-flight rows finish, the lock is released
    mid = len(read_rows(outi)[1]) if outi.exists() else 0
    mid_unique = len({r["id"] for r in read_rows(outi)[1]}) if outi.exists() else 0
    calls2, wall2 = await drain(s, {**ib, "max_items_per_call": 100})   # resume without a cursor: same call, resume defaults to true
    ids80 = ids[:80]
    dg2 = dup_gap(outi, ids80)
    skipped_first = calls2[0].get("skipped") if calls2 else None
    ok = cancelled and 0 < mid < 80 and dg2["n_gaps"] == 0 and dg2["n_duplicate_ok"] == 0 and calls2 and "error" not in calls2[0] and not calls2[-1].get("has_cursor", True)
    rec.add("batch interrupted then resumed (no cursor)", ok, [c["ms"] for c in calls2 if "ms" in c],
            [] if ok else [mm("batch-resume", f"cancelled {cancelled} rows_before_resume {mid} gaps {dg2['n_gaps']} dups {dg2['n_duplicate_ok']}", "MCP bug" if cancelled else "test bug", "batch 80 rows, cancel after 4 s, repeat the same call without cursor")],
            cancel="POST closed after 4 s (2026-07-28 wire)", rows_written_before_resume=mid, unique_before_resume=mid_unique, skipped_on_resume=skipped_first, resume_calls=len(calls2), **dg2)
    res["batch_runs"]["run_interrupted_resumed"] = {"rows": 80, "cancel": "POST closed after 4 s", "cancelled": cancelled, "rows_before_resume": mid, "skipped_on_resume": skipped_first, "resume_calls": len(calls2), **dg2}

    # ---- compare the two runs (same ids, same questions): a determinism read
    r, ms = await call(s, "batch_results", {"path": str(full_out), "compare_to": {"path": str(outi)}, "limit": 20}); sc = sc_of(r)
    cmp = sc.get("compare") or {k: sc[k] for k in sc if "compar" in k or "agree" in k}
    rec.add("batch_results compare_to (200-run vs resumed 80-run)", not r.is_error, [ms], [] if not r.is_error else [mm("batch_results compare", json.dumps(sc)[:200], "MCP bug", "batch_results compare_to")], compare=json.dumps(cmp)[:500])


    # ---- legacy-era client cancel (SDK ClientSession, protocol 2025-11-25): notifications/cancelled is a 202 no-op on stateless HTTP
    outl = work / "blegacy.jsonl"
    lb = {**ib, "max_items": 20, "max_items_per_call": 20, "concurrency": 1, "output_path": str(outl)}
    with anyio.move_on_after(2.0) as scope:
        await s.call_tool("batch", lb, read_timeout_seconds=300)
    await asyncio.sleep(0.5)
    n1 = len(read_rows(outl)[1]) if outl.exists() else 0
    await asyncio.sleep(4)
    n2 = len(read_rows(outl)[1]) if outl.exists() else 0
    r, _ = await call(s, "batch", lb)
    again = sc_of(r)
    stopped = n2 == n1
    rec.add("legacy-era client cancel stops an in-flight batch", stopped, None,
            [] if stopped else [mm("batch-cancel-legacy", f"after the client cancelled, rows grew {n1} -> {n2} in 4 s (the server ran all 20); an immediate resume got "
                                   f"{'a tool error: ' + json.dumps(again)[:140] if r.is_error else 'no error'}", "MCP bug (documented deviation 20; SDK design: stateless HTTP ignores notifications/cancelled)",
                                   "mcp.ClientSession (initialize -> 2025-11-25) call_tool batch, cancel the awaiting task after 2 s, count rows in output_path")],
            client_cancelled=scope.cancelled_caught, rows_at_cancel=n1, rows_4s_later=n2, resume_attempt_error=again if r.is_error else None)
    await asyncio.sleep(3)


async def raw_interrupt(url, args, after_s):
    """POST a 2026-07-28 tools/call and close the connection after after_s; True if it was still running."""
    import httpx
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}
    H = {"accept": "application/json, text/event-stream", "content-type": "application/json", "mcp-protocol-version": "2026-07-28", "mcp-method": "tools/call", "mcp-name": "batch"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "batch", "arguments": args, "_meta": meta}}
    try:
        async with asyncio.timeout(after_s):
            async with httpx.AsyncClient(timeout=None) as c:
                async with c.stream("POST", url, headers=H, json=body) as r:
                    async for _ in r.aiter_bytes(): pass
    except TimeoutError:
        return True
    return False


async def tasks_section(rec, url):
    import httpx
    H = {"accept": "application/json, text/event-stream", "content-type": "application/json", "mcp-protocol-version": "2026-07-28"}
    cap = {"io.modelcontextprotocol/clientCapabilities": {"extensions": {"io.modelcontextprotocol/tasks": {}}}, "io.modelcontextprotocol/protocolVersion": "2026-07-28"}
    n = [0]

    async def rpc(c, method, params, name=None, caps=True):
        n[0] += 1
        meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {"extensions": {"io.modelcontextprotocol/tasks": {}}} if caps else {}}
        r = await c.post(url, headers={**H, "mcp-method": method, **({"mcp-name": name} if name else {})}, json={"jsonrpc": "2.0", "id": n[0], "method": method, "params": {**params, "_meta": meta}})
        body = r.text
        if body.startswith("event:") or "data:" in body[:20]:
            body = [l[5:].strip() for l in body.splitlines() if l.startswith("data:")][-1]
        return json.loads(body)

    items = [{"id": f"t{i}", "state": f"Ticket {i}: checkout returns HTTP 500 for all users"} for i in range(1, 7)]
    bargs = {"items": items, "questions": {"urgent": QS["urgent"]}, "concurrency": 1}
    mism, info, lat = [], {}, []
    async with httpx.AsyncClient(timeout=120) as c:
        disc = (await rpc(c, "server/discover", {})).get("result", {})
        info["extensions"] = list((disc.get("capabilities") or {}).get("extensions", {}))
        if "io.modelcontextprotocol/tasks" not in info["extensions"]: mism.append(mm("tasks", "extension not advertised with OPENJEV_MCP_TASKS=on", "MCP bug", "server/discover"))
        sync = (await rpc(c, "tools/call", {"name": "batch", "arguments": bargs}, "batch", caps=False))["result"]
        t = time.monotonic()
        first = (await rpc(c, "tools/call", {"name": "batch", "arguments": bargs}, "batch"))["result"]
        info["first_result_type"] = first.get("resultType")
        if first.get("resultType") != "task": mism.append(mm("tasks", f"tools/call with the capability returned {first.get('resultType')}", "MCP bug", "tools/call batch with tasks capability"))
        else:
            tid = first["task"]["taskId"]; status = None; polls = 0
            while time.monotonic() - t < 90:
                g = (await rpc(c, "tasks/get", {"taskId": tid}))["result"]; polls += 1
                status = g["status"]
                if status in ("completed", "failed", "cancelled"): break
                await asyncio.sleep(0.3)
            lat.append((time.monotonic() - t) * 1000)
            info.update(task_status=status, polls=polls)
            got = (g.get("result") or {}).get("structuredContent") or {}
            same = [(x["id"], x["status"]) for x in got.get("results", [])] == [(x["id"], x["status"]) for x in sync["structuredContent"]["results"]]
            info["result_matches_sync_ids"] = same
            if status != "completed" or not same: mism.append(mm("tasks", f"status {status} matches sync {same}", "MCP bug", "tasks/get after tools/call batch"))
        # cancel a longer job
        many = {**bargs, "items": [{"id": f"c{i}", "state": f"Ticket {i}: the app is slow on mobile"} for i in range(40)]}
        t2 = (await rpc(c, "tools/call", {"name": "batch", "arguments": many}, "batch"))["result"]
        if t2.get("resultType") == "task":
            await asyncio.sleep(1.0)
            await rpc(c, "tasks/cancel", {"taskId": t2["task"]["taskId"]})
            await asyncio.sleep(1.0)
            g = (await rpc(c, "tasks/get", {"taskId": t2["task"]["taskId"]}))["result"]
            info["cancel_status"] = g["status"]
            if g["status"] != "cancelled": mism.append(mm("tasks", f"cancel left status {g['status']}", "MCP bug", "tasks/cancel on a running batch"))
        else:
            mism.append(mm("tasks", "second tools/call was not a task", "MCP bug", "tools/call batch"))
    rec.add("Tasks (OPENJEV_MCP_TASKS=on) over HTTP", not mism, lat, mism, **info)


def hooks_section(rec):
    # stop: an edit with no passing check after it and a "done" claim -> block
    stop = {**json.load(open(FIX / "stop_v2.json")), "transcript_path": str(FIX / "stop_v2_transcript.jsonl")}
    o, ms, p = hook_run(["stop"], stop)
    rec.add("hook stop (unverified done claim)", o.get("decision") == "block", [ms], [] if o.get("decision") == "block" else [mm("hook-stop", json.dumps(o)[:200] + p.stderr[-120:], "model", "openjev-hook stop < stop_v2.json")], out=json.dumps(o)[:260])
    up = json.load(open(FIX / "userprompt_v2.json"))
    o, ms, p = hook_run(["userprompt", "--roster", str(FIX / "skill_roster.json")], up)
    ctx = o.get("hookSpecificOutput", {}).get("additionalContext", "")
    rec.add("hook userprompt (skill hint)", "'pdf'" in ctx, [ms], [] if "'pdf'" in ctx else [mm("hook-userprompt", json.dumps(o)[:200] + p.stderr[-120:], "model", "openjev-hook userprompt --roster skill_roster.json")], out=json.dumps(o)[:260])
    po = json.load(open(FIX / "posttooluse_v2_webfetch.json"))
    o, ms, p = hook_run(["posttooluse"], po)
    rec.add("hook posttooluse (injection quarantine)", o.get("decision") == "block", [ms], [] if o.get("decision") == "block" else [mm("hook-posttooluse", json.dumps(o)[:200] + p.stderr[-120:], "model", "openjev-hook posttooluse < posttooluse_v2_webfetch.json")], out=json.dumps(o)[:260])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--mcp", default="http://127.0.0.1:8195/mcp"); ap.add_argument("--gate", action="store_true")
    ap.add_argument("--full", action="store_true"); ap.add_argument("--quick", action="store_true", help="with --full: skip run_cases and the recipe pytest run"); ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    if a.full: anyio.run(full, Path(a.out), a.quick)
    elif a.gate: gate_hook()
    else: anyio.run(main, a.mcp)
