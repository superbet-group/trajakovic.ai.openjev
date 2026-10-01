import json
M="openjev-latest"
def sc(instr, levels): return {"type":"score","instructions":instr,"criteria":levels}
def nl(instr,t,f): return {"type":"noul","instructions":instr,"criteria":{"true":t,"false":f}}
PY=sc("Rate the depth of Python expertise demonstrated by concrete evidence in this resume.",
 ["no evidence of Python","basic scripting or coursework only","solid application development in Python with some libraries","deep expertise: internals, performance profiling, C extensions, or core-library/open-source contributions"])
LEAD=sc("Rate the leadership scope demonstrated by concrete evidence in this resume.",
 ["no evidence of leading anyone","informal mentoring or leading small tasks","led a team or a project end to end","managed multiple teams or set org-level technical direction"])
SD=sc("Rate the system design experience demonstrated by concrete evidence in this resume.",
 ["no evidence of system design","worked on components designed by others","designed services or components of moderate scale","architected distributed systems at high scale with explicit scale/latency/availability tradeoffs"])
STRONG="""RESUME
Priya Nair - Staff Engineer, 12 years
- Core contributor to CPython: fixed a GIL-related refcount bug in the asyncio C accelerator (bpo merged), wrote a C extension for fast varint decoding, profiled and cut p99 of a Python ingestion service from 900ms to 120ms with py-spy and Cython.
- Directed 3 teams (22 engineers) building the payments platform; set the org's technical roadmap and ran the architecture review board.
- Architected an event-sourced ledger on Kafka + Postgres handling 40k TPS at 99.99% availability; documented CAP tradeoffs, partitioning and backpressure strategy."""
MID="""RESUME
Marcus Lee - Software Engineer, 4 years
- Built Django REST services and Celery workers for an e-commerce backend; wrote pytest suites and used pandas for reporting.
- Led a 4-person squad for a checkout redesign, planning sprints and delivering it on schedule.
- Designed a caching layer with Redis for the product catalog service (about 500 req/s)."""
WEAK="""RESUME
Sam Ortiz - Recent graduate
- Completed a university course in Python programming; wrote a few scripts to rename files.
- Member of the chess club.
- Summer internship: helped test a web form."""
def case(id,desc,state,qs,ans,extra=None,status=200,req_extra=None):
    r={"model":M,"state":state,"questions":qs}
    if req_extra: r.update(req_extra)
    c={"id":id,"description":desc,"request":r,"expect":{"status":status}}
    if ans: c["expect"]["answers"]=ans
    if extra: c["expect"].update(extra)
    return c
cases=[
 case("rubric-01-strong-resume-all-dims","Strong staff resume: all three rubric dimensions in ONE request; composite is computed in code from these three scores",STRONG,
  {"python_depth":PY,"leadership":LEAD,"system_design":SD},
  {"python_depth":{"score_gte":2.4},"leadership":{"score_gte":2.4},"system_design":{"score_gte":2.4}}),
 case("rubric-02-weak-resume-all-dims","Weak graduate resume: all dimensions low",WEAK,
  {"python_depth":PY,"leadership":LEAD,"system_design":SD},
  {"python_depth":{"score_lte":1.3},"leadership":{"score_lte":0.7},"system_design":{"score_lte":0.7}}),
 case("rubric-03-mid-resume-profile","Mid resume: profile shape matters - leadership and design moderate, python not deep",MID,
  {"python_depth":PY,"leadership":LEAD,"system_design":SD},
  {"python_depth":{"score_gte":1.3,"score_lte":2.5},"leadership":{"score_gte":1.4,"score_lte":2.6},"system_design":{"score_gte":1.3,"score_lte":2.6}}),
 case("rubric-04-lead-qualified","Inbound lead with budget, authority, timeline: evidence gates true and route = sales",
  """INBOUND LEAD FORM
Name: Dana Whitfield, VP Engineering at Helio Logistics (1,800 employees)
Message: We are replacing our current vendor before the contract ends on Nov 30. Budget of $250k for this fiscal year is approved by our CFO and I sign off on tooling purchases. We need SSO and audit logs. Can we get a demo this week?""",
  {"has_budget":nl("Does the lead state that a concrete budget is available?","a budget amount or approved funding is stated","no budget is mentioned or budget is explicitly absent"),
   "has_authority":nl("Does the lead say they personally can approve or sign the purchase?","the lead is a decision maker or signs off on purchases","the lead is not a decision maker or authority is unstated"),
   "route":{"type":"choice","instructions":"Where should this inbound lead be routed?","criteria":{"sales":"a qualified buyer with budget, authority and timeline; send to a sales rep now","nurture":"interested but not ready to buy; add to a marketing nurture sequence","support":"an existing customer with a support problem","discard":"spam, a job seeker, or an irrelevant message"}}},
  {"has_budget":{"noul_gte":0.8},"has_authority":{"noul_gte":0.8},"route":{"choice":"sales"}}),
 case("rubric-05-lead-unqualified-student","Student asking for free info: evidence gates false, route not sales",
  """INBOUND LEAD FORM
Name: Tim, undergraduate student
Message: hi, I am writing a class report on logistics software. do you have a free version or some slides I can use? no budget, just for school. thanks""",
  {"has_budget":nl("Does the lead state that a concrete budget is available?","a budget amount or approved funding is stated","no budget is mentioned or budget is explicitly absent"),
   "has_authority":nl("Does the lead say they personally can approve or sign the purchase?","the lead is a decision maker or signs off on purchases","the lead is not a decision maker or authority is unstated"),
   "route":{"type":"choice","instructions":"Where should this inbound lead be routed?","criteria":{"sales":"a qualified buyer with budget, authority and timeline; send to a sales rep now","nurture":"interested but not ready to buy; add to a marketing nurture sequence","support":"an existing customer with a support problem","discard":"spam, a job seeker, or an irrelevant message"}}},
  {"has_budget":{"noul_lte":0.2},"has_authority":{"noul_lte":0.2},"route":{"choice_in":["nurture","discard"]}}),
 case("rubric-06-lead-hard-nurture","Hard case: real company and interest but no budget/authority/timeline yet: decidable as nurture, not sales",
  """INBOUND LEAD FORM
Name: Rosa Alvarez, Operations Analyst at a mid-size retailer
Message: My manager asked me to start collecting information on route-planning tools for next year. We are only in early research and nothing is approved yet. Could you send a general overview?""",
  {"route":{"type":"choice","instructions":"Where should this inbound lead be routed?","criteria":{"sales":"a qualified buyer with budget, authority and timeline; send to a sales rep now","nurture":"interested but not ready to buy; add to a marketing nurture sequence","support":"an existing customer with a support problem","discard":"spam, a job seeker, or an irrelevant message"}},
   "has_budget":nl("Does the lead state that a concrete budget is available?","a budget amount or approved funding is stated","no budget is mentioned or budget is explicitly absent")},
  {"route":{"choice":"nurture"},"has_budget":{"noul_lte":0.2}}),
 case("rubric-07-monotonic-weak-vs-mid-vs-strong","Monotonic low point: one-line Python evidence must score low (pair with rubric-08, same candidate with rich evidence)",
  """RESUME
Alex Kim - Backend Engineer
- Wrote Python scripts.""",
  {"python_depth":PY},{"python_depth":{"score_lte":1.3}}),
 case("rubric-08-monotonic-strongest","Second monotonic point: same candidate with rich Python evidence (internals, profiling, C extension) must score high",
  """RESUME
Alex Kim - Backend Engineer
- Wrote Python scripts.
- Profiled and optimized a hot loop with cProfile and Cython, cutting runtime 8x.
- Wrote a C extension module and contributed a patch to the CPython standard library (functools).
- Maintains an open-source async Python library with 4k GitHub stars.""",
  {"python_depth":PY},{"python_depth":{"score_gte":2.3}}),
 case("rubric-09-score-ignores-state-guard","Validation of score questions that may ignore state: state is a cooking recipe with no resume evidence; leadership must be low and a noul evidence gate must say no evidence. If score stayed high the rubric would be unusable",
  """RECIPE
Tomato soup: dice 6 tomatoes, saute one onion in butter, add stock, simmer 20 minutes, blend and season with salt.""",
  {"leadership":LEAD,"has_evidence":nl("Does the text contain any evidence about a person's work experience or leadership?","the text describes a person's work history or leadership","the text has no information about a person's work history"),},
  {"leadership":{"score_lte":0.8},"has_evidence":{"noul_lte":0.15}}),
 case("rubric-10-doc-quality-rescore","Live document rescoring: 4 dimensions of a design-doc draft in one request; good draft with weak testing section",
  """DRAFT DESIGN DOC
Title: Rate limiter for public API
Problem: Bursty clients cause p99 latency spikes on the orders API (measured 2.4s at peak on Sept 3).
Proposal: Token bucket per API key in Redis, 100 req/s sustained, burst 200. Return 429 with Retry-After. Fallback to local in-memory limiter if Redis is down (fail open, alert on-call).
Alternatives considered: sliding window log (rejected: memory heavy), gateway-level limiting (rejected: no per-key data).
Testing: TBD.
Rollout: TBD.""",
  {"clarity":sc("Rate how clearly the problem and proposal are stated.",["unclear or missing","vague","clear but missing specifics","clear, specific and measurable"]),
   "alternatives":sc("Rate how well alternatives and tradeoffs are covered.",["none","named without reasoning","some reasoning","several alternatives with explicit rejection reasons"]),
   "testing_plan":sc("Rate how complete the testing plan section is.",["absent or TBD","one vague sentence","reasonable outline","detailed plan with unit, load and failure-injection tests"])},
  {"clarity":{"score_gte":2.2},"alternatives":{"score_gte":2.2},"testing_plan":{"score_lte":0.5}}),
 case("rubric-11-many-dims-with-think-samples","Extension: 6 dimensions with samples=3 and think=256 on a mixed resume; assert non-saturated stable ordering (strong signal > absent signal)",
  STRONG,
  {"python_depth":PY,"leadership":LEAD,"system_design":SD,
   "mentoring":sc("Rate evidence of mentoring or growing other engineers.",["no evidence","hinted","clear mentoring","systematic mentoring program"]),
   "frontend":sc("Rate evidence of frontend (browser UI) engineering experience.",["no evidence","minor","solid","deep"]),
   "data_science":sc("Rate evidence of machine learning or data science experience.",["no evidence","minor","solid","deep"])},
  {"python_depth":{"score_gte":2.2},"frontend":{"score_lte":0.7},"data_science":{"score_lte":0.7}},
  req_extra={"samples":3,"think":256}),
 case("rubric-12-blank-state-error","Error case the MCP layer must handle: score criteria given as a dict instead of a list is a 422; tool must send a list",
  STRONG,{"python_depth":{"type":"score","instructions":"Rate Python depth.","criteria":{"0":"none","3":"deep"}}},None,
  extra={"body_contains":"valid list"},status=422),
 case("rubric-13-unknown-model-error","Error case: unknown model must fail clearly rather than silently",
  STRONG,{"python_depth":PY},None,extra={"body_contains":"nknown model"},status=400),
]
cases[-1]["request"]["model"]="no-such-model"
json.dump({"use_case":"18-composite-rubric-scoring","cases":cases},open("18-composite-rubric-scoring.json","w"),indent=1)
