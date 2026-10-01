import json, sys, time, urllib.request, urllib.error
sys.path.insert(0, "/Users/trajakovic/Projects/Models/openjev/docs/mcp-skill-spec/tests")
import run_cases as rc
suite = json.load(open("/Users/trajakovic/Projects/Models/openjev/docs/mcp-skill-spec/tests/cases/00-spec-examples.json"))
out = {}
for c in suite["cases"]:
    ep = c.get("endpoint", "/v1/systemone"); m = c.get("method", "POST")
    data = None if m == "GET" else json.dumps(rc.resolve_images(c.get("request", {}))).encode()
    r = urllib.request.Request(rc.BASE + ep, data=data, method=m); r.add_header("Content-Type", "application/json")
    t = time.monotonic()
    try:
        with urllib.request.urlopen(r, timeout=600) as resp:
            st, body, hd = resp.status, resp.read().decode(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        st, body, hd = e.code, e.read().decode(), dict(e.headers)
    ms = (time.monotonic() - t) * 1000
    fails, _ = rc.check(c, st, body)
    try: js = json.loads(body)
    except ValueError: js = body
    out[c["id"]] = {"status": st, "ms": round(ms), "server_timing": hd.get("server-timing"), "request_id": hd.get("x-request-id"), "body": js, "fails": fails}
    print(("PASS" if not fails else "FAIL"), c["id"], round(ms), fails, flush=True)
json.dump(out, open("captured.json", "w"), indent=1, ensure_ascii=False)
print(sum(1 for v in out.values() if not v["fails"]), "/", len(out))
