#!/usr/bin/env python3
"""Small OpenJev benchmark: 100 labelled questions against a running server.

Runs the questions one request each, sequentially, after a short warm-up, and
reports latency (mean/p50/p95/p99), throughput and accuracy per question type.
Then repeats the run with a few parallel clients to show how the server scales.

    ./benchmark.py [--url http://127.0.0.1:8080] [--parallel 4] [--warmup 3]
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REFUND = {"type": "noul", "instructions": "Is the customer asking for a refund?"}
TOPIC = {
    "type": "choice",
    "instructions": "Which department should handle this message?",
    "criteria": {
        "billing": "charges, invoices, refunds",
        "outage": "service down or errors",
        "feature": "feature requests, how-to questions",
        "security": "account safety, suspicious activity, passwords",
    },
}
MOOD = {
    "type": "score",
    "instructions": "How does the customer feel?",
    "criteria": ["negative", "neutral", "positive"],
}

# (state, question, expected). noul: True/False. choice: key. score: level index.
YES = [
    "I want my money back for last month.",
    "Please refund the duplicate charge.",
    "Can I get a refund? The product never arrived.",
    "I'm cancelling and would like a full refund.",
    "You charged me twice. Refund one of them.",
    "This isn't what I ordered, I need my money returned.",
    "Reimburse me for the broken unit.",
    "I request a refund under your 30-day policy.",
    "Give me my payment back, the service was useless.",
    "The subscription renewed by mistake. Refund it please.",
    "I'd like a refund for the annual plan I never used.",
    "Return my deposit, the booking was cancelled.",
    "Refund me, the app crashed every time I opened it.",
    "Please send the money for the late delivery back to my card.",
    "I was overcharged and want the difference refunded.",
    "Can you reverse the payment and return the funds?",
    "I want to be refunded for the missing item.",
    "Money back please, I was not satisfied.",
    "Process a refund for order 4471.",
    "The trial ended early and I got billed. Refund me.",
]
NO = [
    "How do I change my profile picture?",
    "The dashboard looks great after the update.",
    "What time does support open tomorrow?",
    "Thanks for the quick reply yesterday.",
    "Can you add a dark mode to the app?",
    "I forgot my password and need help logging in.",
    "Is there an API for exporting reports?",
    "Your team was very helpful, thank you.",
    "Where can I find the user manual?",
    "How many users can I add to the team plan?",
    "The website is loading very slowly today.",
    "Please update my shipping address.",
    "Do you integrate with Slack?",
    "I would like to upgrade to the premium plan.",
    "What are your opening hours on holidays?",
    "How do I export my data to CSV?",
    "I saw a login from a country I have never visited.",
    "Can I change the language of the interface?",
    "The mobile app keeps logging me out.",
    "Just wanted to say the new design is lovely.",
]
CHOICE = [
    ("billing", [
        "My invoice shows a charge I don't recognise.",
        "Why was my card billed twice this month?",
        "I need a copy of last quarter's invoice.",
        "The price on my bill is higher than the quote.",
        "Please update the credit card on file.",
        "I was charged after cancelling my plan.",
        "Can you send a receipt for my last payment?",
        "The tax on my invoice looks wrong.",
        "My refund still hasn't arrived after two weeks.",
        "I was billed for seats we removed.",
    ]),
    ("outage", [
        "The whole site is down for everyone in our office.",
        "I get a 500 error every time I open the dashboard.",
        "The API has been timing out for the last hour.",
        "Nothing loads, the service seems to be offline.",
        "All our uploads are failing with a server error.",
        "The app crashes at startup since this morning.",
        "Your servers are unreachable from our network.",
        "Login page returns 503 for all our staff.",
        "Webhooks have stopped firing completely.",
        "Pages load a blank screen, the platform is broken.",
    ]),
    ("feature", [
        "Could you add support for exporting to PDF?",
        "How do I set up a recurring report?",
        "It would be great to have keyboard shortcuts.",
        "Is there a way to schedule messages for later?",
        "I'd love a calendar view of my tasks.",
        "How can I invite a colleague to my workspace?",
        "Please add a bulk edit option to the table.",
        "Where do I change the notification settings?",
        "Do you plan to support single sign-on?",
        "Can the widget be customised with our colours?",
    ]),
    ("security", [
        "Someone logged into my account from another country.",
        "I got a phishing email pretending to be you.",
        "How do I enable two-factor authentication?",
        "My password was leaked in a data breach.",
        "There are unknown devices in my session list.",
        "I think my account has been hacked.",
        "Please lock my account, I lost my phone.",
        "I received a password reset I didn't request.",
        "How do I revoke an API key that leaked?",
        "Suspicious transactions appeared under my profile.",
    ]),
]
SCORE = [
    (0, [
        "This is the worst service I have ever used.",
        "I'm furious, nothing works and nobody answers.",
        "Absolutely terrible, I want to leave.",
        "So disappointed, this has ruined my week.",
        "Unacceptable. Third outage this month.",
        "I hate this app, it loses my work every time.",
        "Awful support, they ignored me for days.",
    ]),
    (1, [
        "I'd like to know how the export works.",
        "The report was generated at 9am.",
        "Please send me the pricing table.",
        "I updated my address as requested.",
        "The meeting is scheduled for Tuesday.",
        "Received your email, will review it.",
    ]),
    (2, [
        "Fantastic, this solved everything, thank you!",
        "Love the new update, works perfectly.",
        "Your support team was wonderful and fast.",
        "Brilliant product, I recommend it to everyone.",
        "Super happy with how easy this was.",
        "Great job, exactly what we needed!",
        "Thrilled with the results, thanks so much.",
    ]),
]


def build_cases():
    cases = []
    cases += [(s, "noul", REFUND, True) for s in YES]
    cases += [(s, "noul", REFUND, False) for s in NO]
    for label, texts in CHOICE:
        cases += [(s, "choice", TOPIC, label) for s in texts]
    for level, texts in SCORE:
        cases += [(s, "score", MOOD, level) for s in texts]
    assert len(cases) == 100, len(cases)
    return cases


def ask(url, model, state, question, timeout=120):
    body = json.dumps(
        {"model": model, "state": state, "questions": {"q": question}}
    ).encode()
    req = urllib.request.Request(
        url + "/v1/systemone", body, {"Content-Type": "application/json"}
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
        timing = resp.headers.get("server-timing", "")
    wall = time.perf_counter() - t0
    model_ms = 0.0
    for part in timing.split(","):
        if part.strip().startswith("model;dur="):
            model_ms = float(part.split("=")[1])
    return data["answers"]["q"], wall * 1000, model_ms, data["usage"]["input_tokens"]


def correct(qtype, answer, expected):
    if qtype == "noul":
        return (answer["noul"] >= 0.5) == expected
    if qtype == "choice":
        return answer["choice"] == expected
    return round(answer["score"]) == expected


def pct(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(round(p / 100 * (len(sorted_vals) - 1))))]


def run(url, model, cases, workers):
    def one(case):
        state, qtype, question, expected = case
        ans, wall_ms, model_ms, tokens = ask(url, model, state, question)
        return qtype, correct(qtype, ans, expected), wall_ms, model_ms, tokens

    t0 = time.perf_counter()
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(one, cases))
    return results, time.perf_counter() - t0


def report(title, results, elapsed):
    lat = sorted(r[2] for r in results)
    model = statistics.mean(r[3] for r in results)
    tokens = sum(r[4] for r in results)
    print(f"\n== {title} ==")
    print(f"requests     {len(results)} in {elapsed:.2f}s  ->  {len(results) / elapsed:.2f} req/s")
    print(
        f"latency ms   mean {statistics.mean(lat):.0f}  p50 {pct(lat, 50):.0f}  "
        f"p95 {pct(lat, 95):.0f}  p99 {pct(lat, 99):.0f}  min {lat[0]:.0f}  max {lat[-1]:.0f}"
    )
    print(f"model ms     mean {model:.0f}   input tokens {tokens}")
    for qtype in ("noul", "choice", "score"):
        rows = [r for r in results if r[0] == qtype]
        ok = sum(r[1] for r in rows)
        print(f"accuracy     {qtype:<6} {ok}/{len(rows)}  ({100 * ok / len(rows):.0f}%)")
    ok = sum(r[1] for r in results)
    print(f"accuracy     total  {ok}/{len(results)}  ({100 * ok / len(results):.0f}%)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--model", default="openjev-latest")
    ap.add_argument("--warmup", type=int, default=3, help="untimed requests first")
    ap.add_argument("--parallel", type=int, default=4, help="clients for the second run (0 skips it)")
    args = ap.parse_args()

    try:
        urllib.request.urlopen(args.url + "/v1/models", timeout=3).read()
    except Exception as e:
        sys.exit(f"Server not reachable at {args.url} ({e}). Run: mise run start")

    cases = build_cases()
    print(f"Warming up ({args.warmup} requests)...")
    for case in cases[: args.warmup]:
        ask(args.url, args.model, case[0], case[2])

    report("sequential, 1 client", *run(args.url, args.model, cases, 1))
    if args.parallel > 1:
        report(f"parallel, {args.parallel} clients", *run(args.url, args.model, cases, args.parallel))


if __name__ == "__main__":
    main()
