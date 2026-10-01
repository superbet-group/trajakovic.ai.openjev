import json
M="openjev-latest"
def noul(i,c=None):
    d={"type":"noul","instructions":i}
    if c: d["criteria"]=c
    return d
SWALLOW="Does this function silently swallow an exception, meaning it catches an error and neither logs it, re-raises it, nor reports it to the caller?"
F_SWALLOW='''def load_config(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        pass
'''
F_RERAISE='''def load_config(path):
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.exception("bad config %s", path)
        raise ConfigError(path) from e
'''
F_DEFAULT='''def get_price(sku):
    try:
        return catalog.lookup(sku).price
    except (KeyError, TimeoutError):
        return 0
'''
D_MATCH='''diff --git a/api/users.py b/api/users.py
--- a/api/users.py
+++ b/api/users.py
@@ -41,7 +41,12 @@ def list_users(request):
-    users = User.objects.all()
+    page = int(request.GET.get("page", 1))
+    size = min(int(request.GET.get("size", 50)), 200)
+    users = User.objects.all()[(page-1)*size:page*size]
     return JsonResponse([u.to_dict() for u in users], safe=False)
'''
D_RENAME='''diff --git a/billing/invoice.py b/billing/invoice.py
--- a/billing/invoice.py
+++ b/billing/invoice.py
@@ -12,8 +12,8 @@ def total(items):
-    t = 0
+    total_cents = 0
     for i in items:
-        t += i.cents
-    return t
+        total_cents += i.cents
+    return total_cents
'''
D_SECRET='''diff --git a/deploy/upload.py b/deploy/upload.py
+import boto3
+s3 = boto3.client(
+    "s3",
+    aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
+    aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
+)
'''
D_ENV='''diff --git a/deploy/upload.py b/deploy/upload.py
+import os, boto3
+s3 = boto3.client(
+    "s3",
+    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
+    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
+)
'''
SLOP="In today's fast-paced digital landscape, our groundbreaking toolkit empowers teams to seamlessly unlock the full potential of their data. Whether you're a seasoned professional or just getting started, this game-changing solution will elevate your workflow to new heights. Let's dive in and delve into the rich tapestry of features!"
PLAIN="Run `mytool init` to create a config file in the current directory. The file lists your data sources, one per line. Then run `mytool sync`, which copies each source into the local cache and prints a count of rows written."
def cm(msg,diff): return f"Commit message:\n{msg}\n\nStaged diff:\n{diff}"
cases=[]
def add(id,desc,req,ans,extra=None):
    c={"id":id,"description":desc,"request":{"model":M,**req},"expect":{"answers":ans}}
    if extra: c.update(extra)
    cases.append(c)
add("swallow-pos","Bare except/pass returning None implicitly is a silent swallow",
 {"state":F_SWALLOW,"questions":{"swallows":noul(SWALLOW)}},{"swallows":{"noul_gte":0.8}})
add("swallow-neg-reraise","Logs and re-raises a wrapped error: not a swallow",
 {"state":F_RERAISE,"questions":{"swallows":noul(SWALLOW)}},{"swallows":{"noul_lte":0.2}})
add("swallow-multi-question","Several independent claims about one function, each with its own label",
 {"state":F_DEFAULT,"questions":{
  "swallows":noul(SWALLOW),
  "catches_broad":noul("Does the except clause catch the base Exception class or use a bare except?"),
  "returns_default":noul("Does the function return a fallback value when the lookup fails?"),
  "has_docstring":noul("Does the function have a docstring?")}},
 {"swallows":{"noul_gte":0.6},"catches_broad":{"noul_lte":0.25},"returns_default":{"noul_gte":0.8},"has_docstring":{"noul_lte":0.15}})
add("commit-matches-diff","Message describes pagination and diff adds pagination",
 {"state":cm("Add page/size pagination to list_users",D_MATCH),
  "questions":{"mismatch":noul("Does the commit message describe a change that is NOT present in the diff?")}},
 {"mismatch":{"noul_lte":0.2}})
add("commit-claims-not-in-diff","Message claims a race condition fix; diff is only a variable rename",
 {"state":cm("Fix race condition in invoice total calculation",D_RENAME),
  "questions":{"mismatch":noul("Does the commit message describe a change that is NOT present in the diff?")}},
 {"mismatch":{"noul_gte":0.8}})
add("secrets-pos-and-neg-pair","Hardcoded AWS-style key pair: positive secret claim plus a negated-contrast claim (reads env) that must be low",
 {"state":D_SECRET,"questions":{
  "secret":noul("Does the added code contain a hardcoded credential, such as an API key or secret key written as a string literal?"),
  "reads_env":noul("Does the added code read the credentials from environment variables?")}},
 {"secret":{"noul_gte":0.85},"reads_env":{"noul_lte":0.15}})
add("secrets-neg-env","Env var lookups are not hardcoded secrets; block-on-high-precision check must not fire",
 {"state":D_ENV,"questions":{
  "secret":noul("Does the added code contain a hardcoded credential, such as an API key or secret key written as a string literal?")}},
 {"secret":{"noul_lte":0.1}})
add("prose-slop-pos","Marketing filler README paragraph",
 {"state":SLOP,"questions":{"slop":noul("Is this paragraph written in generic AI-sounding marketing filler, using stock phrases such as 'delve', 'tapestry', 'unlock the full potential' or 'in today's fast-paced world'?")}},
 {"slop":{"noul_gte":0.85}})
add("prose-slop-neg","Concrete plain README paragraph",
 {"state":PLAIN,"questions":{"slop":noul("Is this paragraph written in generic AI-sounding marketing filler, using stock phrases such as 'delve', 'tapestry', 'unlock the full potential' or 'in today's fast-paced world'?")}},
 {"slop":{"noul_lte":0.15}})
add("readme-ten-checks","Ten one-claim style checks in one call on the plain paragraph; mixed expected labels",
 {"state":PLAIN,"questions":{
  "imperative":noul("Does the paragraph tell the reader to run at least one command?"),
  "code_formatted":noul("Are command names written in inline code formatting (backticks)?"),
  "exclaims":noul("Does the paragraph contain an exclamation mark?"),
  "first_person":noul("Does the paragraph use the words 'I' or 'we'?"),
  "hype":noul("Does the paragraph use hype adjectives such as 'powerful', 'blazing', 'revolutionary' or 'game-changing'?"),
  "mentions_sync":noul("Does the paragraph mention a sync command?"),
  "mentions_docker":noul("Does the paragraph mention Docker?"),
  "sequence":noul("Does the paragraph describe steps in a specific order?"),
  "states_output":noul("Does the paragraph say what the sync command prints?"),
  "mentions_pricing":noul("Does the paragraph mention pricing or licensing?")}},
 {"imperative":{"noul_gte":0.8},"code_formatted":{"noul_gte":0.8},"exclaims":{"noul_lte":0.15},"first_person":{"noul_lte":0.2},
  "hype":{"noul_lte":0.15},"mentions_sync":{"noul_gte":0.85},"mentions_docker":{"noul_lte":0.1},"sequence":{"noul_gte":0.7},
  "states_output":{"noul_gte":0.7},"mentions_pricing":{"noul_lte":0.1}})
add("negation-not-assumed","Ask claim and its negation separately, assert each on its own; both must land on the right side, never derive one from the other",
 {"state":F_RERAISE,"questions":{
  "logs":noul("Does the except block log the error?"),
  "does_not_log":noul("Does the except block fail to log the error?")}},
 {"logs":{"noul_gte":0.8},"does_not_log":{"noul_lte":0.2}})
add("literal-reading-edge","Literal reading: the function catches and logs at debug, then returns None; 'silently' is arguable, but the literal claim 'no raise' is decidable",
 {"state":'''def fetch_avatar(url):
    try:
        return requests.get(url, timeout=2).content
    except requests.RequestException:
        logger.debug("avatar fetch failed")
        return None
''',"questions":{
  "raises":noul("Can this function raise a requests.RequestException to its caller?"),
  "logs":noul("Does the function write a log message when the request fails?")}},
 {"raises":{"noul_lte":0.25},"logs":{"noul_gte":0.75}})
add("choice-commit-type","Choice over conventional-commit type with criteria descriptions",
 {"state":cm("Add page/size pagination to list_users",D_MATCH),
  "questions":{"type":{"type":"choice","instructions":"Which conventional-commit type best fits this change?",
   "criteria":{"feat":"adds new user-visible behavior","fix":"corrects a bug","refactor":"restructures code with no behavior change","docs":"documentation only","chore":"tooling, deps, config"}}}},
 {"type":{"choice":"feat"}})
add("choice-commit-type-refactor","Rename-only diff should be refactor",
 {"state":cm("Rename t to total_cents",D_RENAME),
  "questions":{"type":{"type":"choice","instructions":"Which conventional-commit type best fits this change?",
   "criteria":{"feat":"adds new user-visible behavior","fix":"corrects a bug","refactor":"restructures code with no behavior change","docs":"documentation only","chore":"tooling, deps, config"}}}},
 {"type":{"choice":"refactor"}})
add("score-style-adherence","Score: naming quality of a variable-name-poor function (levels 0-3). Score questions can ignore state, so this one is a probe",
 {"state":"def f(a, b, c):\n    x = a * b\n    y = x + c\n    z = y * 0.2\n    return y + z\n",
  "questions":{"naming":{"type":"score","instructions":"How descriptive are the function and variable names in this code?",
   "criteria":["completely opaque single letters","mostly unclear","mostly descriptive","fully descriptive"]}}},
 {"naming":{"score_lte":1.0}})
add("score-style-good","Same rubric on descriptive code should land high",
 {"state":"def price_with_tax(unit_price, quantity, shipping):\n    subtotal = unit_price * quantity\n    pre_tax_total = subtotal + shipping\n    tax = pre_tax_total * TAX_RATE\n    return pre_tax_total + tax\n",
  "questions":{"naming":{"type":"score","instructions":"How descriptive are the function and variable names in this code?",
   "criteria":["completely opaque single letters","mostly unclear","mostly descriptive","fully descriptive"]}}},
 {"naming":{"score_gte":2.0}})
add("ext-samples-think","Extension: samples averaging on a borderline cleanup loop: bare 'except Exception: pass' around close(), a common accepted idiom but still a literal swallow",
 {"samples":4,"state":'''def close_all(conns):
    for c in conns:
        try:
            c.close()
        except Exception:
            pass
''',"questions":{"swallows":noul(SWALLOW)}},
 {"swallows":{"noul_gte":0.6}})

REAL_DEF="Does the added code contain a real, working-looking credential written as a string literal? A placeholder such as YOUR_API_KEY_HERE, <token>, xxx or changeme is not a real credential."
add("secrets-placeholder-not-flagged","Placeholder value must not be flagged (block-on-high-precision). Before: 'hardcoded credential such as an API key written as a string literal' scored 0.874 on YOUR_API_KEY_HERE; after: definition excludes placeholders",
 {"state":'+API_KEY = "YOUR_API_KEY_HERE"\n',"questions":{"secret":noul(REAL_DEF)}},{"secret":{"noul_lte":0.1}})
add("secrets-real-token-flagged","GitHub-token-shaped literal is flagged under the same placeholder-aware definition",
 {"state":'+token = "ghp_REDACTED_EXAMPLE_TOKEN"\n',"questions":{"secret":noul(REAL_DEF),"placeholder":noul("Is the string assigned to token an obvious placeholder rather than a real secret?")}},
 {"secret":{"noul_gte":0.85},"placeholder":{"noul_lte":0.15}})
add("swallow-flag-only","Swallow variant: exception only flips a boolean, no logging, no info kept; three literal claims",
 {"state":'''def sync_all(items):
    ok = True
    for it in items:
        try:
            push(it)
        except Exception:
            ok = False
    return ok
''',"questions":{
  "exc_stored":noul("Is the exception object stored, logged, or returned anywhere in this function?"),
  "records_failed_item":noul("Does the function record which item failed?"),
  "bool_only":noul("Does the function return only a boolean, without identifying which item failed?")}},
 {"exc_stored":{"noul_lte":0.15},"records_failed_item":{"noul_lte":0.15},"bool_only":{"noul_gte":0.85}})
add("comment-vs-code","Docstring says inclusive; code returns plain difference (exclusive). Question spells out the literal difference",
 {"state":"# Returns the number of days between a and b, inclusive\ndef days_between(a, b):\n    return (b - a).days\n",
  "questions":{"contradicts":noul("Does the comment describe behavior that differs from what the code does? The code returns the plain difference, which excludes one endpoint.")}},
 {"contradicts":{"noul_gte":0.8}})
add("KNOWN-LIMITATION-coasked-interference","KNOWN LIMITATION: the same claim scored 0.996 alone but 0.25 when co-asked with a broader swallow question in one request. Answers are not independent of sibling questions; the MCP layer should not batch a claim with a semantically overlapping sibling if the claim gates a block.",
 {"state":'''def sync_all(items):
    ok = True
    for it in items:
        try:
            push(it)
        except Exception:
            ok = False
    return ok
''',"questions":{
  "swallows":noul(SWALLOW),
  "info_lost":noul("Does the caller lose all information about which item failed and why?")}},
 {"info_lost":{"noul_gte":0.7}})
add("error-unknown-model","MCP layer must surface a 400 for an unpinned/unknown model name",
 {"model":"jev-1.13.0","state":F_SWALLOW,"questions":{"swallows":noul(SWALLOW)}},{},
 {"expect":{"status":400,"body_contains":"Unknown model"}})
add("error-bad-question-shape","MCP layer must validate: score criteria given as an object gives 422",
 {"state":F_SWALLOW,"questions":{"s":{"type":"score","instructions":"x","criteria":{"1":"bad","5":"good"}}}},{},
 {"expect":{"status":422}})
cases[-1]["expect"].pop("answers",None); cases[-2]["expect"].pop("answers",None)
# fix: request.model override
for c in cases:
    if c["id"]=="error-unknown-model": c["request"]["model"]="jev-1.13.0"
DROP={"negation-not-assumed","error-unknown-model","error-bad-question-shape"}  # off-topic per audit
cases=[c for c in cases if c["id"] not in DROP]
json.dump({"use_case":"06-semantic-code-lint","cases":cases},open("/Users/trajakovic/Projects/Models/openjev/docs/mcp-skill-spec/tests/cases/06-semantic-code-lint.json","w"),indent=1)
