"""Builds cases/22-image-and-ui-decisions.json. Run: ../../../.venv/bin/python gen22_cases.py"""
import json, os
D = "docs/mcp-skill-spec/tests/data/"
def img(n): return {"$file": D + n}
M = "openjev-latest"
def noul(i, crit=None):
    q = {"type": "noul", "instructions": i}
    if crit: q["criteria"] = crit
    return q
def choice(i, c): return {"type": "choice", "instructions": i, "criteria": c}
def case(id, desc, request, answers=None, **exp):
    e = {"answers": answers} if answers else {}
    e.update(exp)
    return {"id": id, "description": desc, "request": {"model": M, **request}, "expect": e}

LOGIN_Q = noul("Does this screenshot show a login wall or sign-in prompt that blocks the content the user wants to read?")
cases = []
cases.append(case("ui-01", "Image + noul: login wall present (positive)",
  {"state": "Screenshot of the page the browser agent just loaded.", "images": [img("ui22_login_wall.png")], "questions": {"login_wall": LOGIN_Q}},
  {"login_wall": {"noul_gte": 0.8}}))
cases.append(case("ui-02", "Image + noul: same question on a normal dashboard (negative)",
  {"state": "Screenshot of the page the browser agent just loaded.", "images": [img("ui22_dashboard.png")], "questions": {"login_wall": LOGIN_Q}},
  {"login_wall": {"noul_lte": 0.2}}))
PAGE = choice("What kind of page does this screenshot show?", {
  "login_wall": "a sign-in or paywall page asking for credentials or a subscription",
  "dashboard": "a working application page with data, metrics or lists",
  "error_page": "an HTTP error or crash page such as 404 or 500",
  "consent_modal": "a cookie or privacy consent dialog covering the page"})
cases.append(case("ui-03", "Image + choice taxonomy: HTTP error page classified as error_page, with a done/blocked noul",
  {"state": "Screenshot after submitting the checkout form.", "images": [img("ui22_error_500.png")],
   "questions": {"kind": PAGE, "checkout_completed": noul("Does the screenshot show that the order was successfully placed?")}},
  {"kind": {"choice": "error_page"}, "checkout_completed": {"noul_lte": 0.1}}))
cases.append(case("ui-04", "Image + choice taxonomy: cookie consent modal",
  {"state": "Screenshot of a blog landing page.", "images": [img("ui22_cookie_modal.png")], "questions": {"kind": PAGE}},
  {"kind": {"choice": "consent_modal"}}))
cases.append(case("ui-05", "Image + noul irreversible: delete-confirm dialog is irreversible and goal not done (image, 2 noul)",
  {"state": "Screenshot of the dialog on screen. The agent is about to click the red button.", "images": [img("ui22_delete_confirm.png")],
   "questions": {"irreversible": noul("Would clicking the primary red button in this dialog permanently destroy data that cannot be recovered?"),
                 "done": noul("Does the screenshot show that the user's goal, cleaning up a staging log file, is complete?")}},
  {"irreversible": {"noul_gte": 0.8}, "done": {"noul_lte": 0.2}}))
cases.append(case("ui-06", "Image + choice among visible elements (cookie modal): next click when the task is to read the article without tracking",
  {"state": "Task: read the article without agreeing to tracking. Screenshot shows the current page. Candidates: e1 button 'Accept all', e2 button 'Reject non-essential'.",
   "images": [img("ui22_cookie_modal.png")],
   "questions": {"next": choice("Which element should the agent click next to dismiss the dialog according to the task?", {
       "e1": "the 'Accept all' button, which agrees to all tracking", "e2": "the 'Reject non-essential' button, which declines tracking"})}},
  {"next": {"choice": "e2"}}))

TREE = """Goal: add the blue 'Trail Runner' shoes, size 42, to the cart.
Page: shop.example.com/product/trail-runner (accessibility tree)
e01 link 'Home'
e02 link 'Men'
e03 searchbox 'Search products'
e04 button 'Sign in'
e05 heading 'Trail Runner - Blue'
e06 combobox 'Size' (value: none selected)
e07 button 'Add to cart' (disabled until a size is chosen)
e08 button 'Add to wishlist'
e09 link 'Size guide'
e10 link 'Privacy policy'"""
cases.append(case("ui-07", "Choice over accessibility-tree ids (text only): a size must be chosen before Add to cart",
  {"state": TREE, "questions": {"next": choice("Which element should the agent interact with next to make progress on the goal?", {
      "e03": "search box", "e04": "sign in button", "e06": "size dropdown, where the size is chosen",
      "e07": "add to cart button", "e08": "add to wishlist button", "e10": "privacy policy link"})}},
  {"next": {"choice": "e06"}}))

TREE40 = "Goal: open the billing settings page to download the latest invoice PDF.\nCandidates (ids from the accessibility tree):\n" + "\n".join(
  [f"e{i:02d} link 'Nav item {i}'" for i in range(1, 21)] + ["e21 link 'Billing and invoices'"] + [f"e{i:02d} link 'Nav item {i}'" for i in range(22, 41)])
opts = {f"e{i:02d}": (f"navigation link 'Nav item {i}'" if i != 21 else "link 'Billing and invoices'") for i in range(1, 41)}
cases.append(case("ui-08", "40 candidate ids: exact id selected among many options",
  {"state": TREE40, "questions": {"next": choice("Which single candidate should the agent click next to reach the goal?", opts)}},
  {"next": {"choice": "e21"}}))

CHECKOUT = """Goal: buy the item in the cart.
Page: shop.example.com/checkout/review (accessibility tree)
e01 heading 'Review your order'
e02 text 'Total: $189.00, charged to card ending 4242'
e03 button 'Edit cart'
e04 button 'Place order' (submits payment immediately)
e05 link 'Return to shop'"""
IRR = noul("Does clicking this element commit an action that cannot be undone, such as submitting payment, deleting data or sending a message?")
cases.append(case("ui-09", "Noul irreversible on a Place order candidate (text only)",
  {"state": CHECKOUT + "\nProposed click: e04 button 'Place order'", "questions": {"irreversible": IRR}},
  {"irreversible": {"noul_gte": 0.8}}))
cases.append(case("ui-10", "Noul irreversible negative: proposed click is 'Edit cart'",
  {"state": CHECKOUT + "\nProposed click: e03 button 'Edit cart'", "questions": {"irreversible": IRR}},
  {"irreversible": {"noul_lte": 0.15}}))

DONE_Q = noul("Does the page state show that the goal is fully accomplished?")
BLOCK_Q = noul("Is the agent blocked by something it cannot get past without human help, such as a CAPTCHA, a two-factor prompt or a permissions error?")
cases.append(case("ui-11", "Done + blocked in one request: order confirmation page is done and not blocked",
  {"state": "Goal: buy the item in the cart.\nPage: shop.example.com/checkout/confirmation\ne01 heading 'Thank you! Order #88213 confirmed'\ne02 text 'A receipt was emailed to you'\ne03 link 'Continue shopping'",
   "questions": {"done": DONE_Q, "blocked": BLOCK_Q}},
  {"done": {"noul_gte": 0.85}, "blocked": {"noul_lte": 0.1}}))
cases.append(case("ui-12", "Blocked: CAPTCHA wall while goal not done",
  {"state": "Goal: download the latest invoice.\nPage: billing.example.com/login (accessibility tree)\ne01 text 'Verify you are human'\ne02 checkbox 'I am not a robot' (reCAPTCHA iframe)\ne03 button 'Continue' (disabled)\ne04 link 'Help'",
   "questions": {"done": DONE_Q, "blocked": BLOCK_Q}},
  {"done": {"noul_lte": 0.1}, "blocked": {"noul_gte": 0.8}}))

cases.append(case("ui-13", "Two images in one request (login wall then dashboard): question targets the second image",
  {"state": "Two screenshots in order: first before signing in, second after signing in. Answer about the second.",
   "images": [img("ui22_login_wall.png"), img("ui22_dashboard.png")],
   "questions": {"signed_in": noul("Does the second screenshot show the user successfully signed in and looking at their account data?")}},
  {"signed_in": {"noul_gte": 0.7}}))
cases.append(case("ui-14", "Photo classification against a taxonomy using the shared hotdog fixture",
  {"state": "Uploaded photo to classify.", "images": [{"$file": "tests/data/hotdog.jpg"}],
   "questions": {"category": choice("Which category best describes the main subject of the photo?", {
       "food": "prepared food or a meal", "animal": "a pet or wild animal", "vehicle": "a car, bike or other vehicle", "document": "a document, receipt or screenshot of text"})}},
  {"category": {"choice": "food"}}))
cases.append(case("ui-15", "Error: 9 images exceeds the cap of 8",
  {"state": "x", "images": [img("ui22_dashboard.png")] * 9, "questions": {"q": noul("Is this a dashboard?")}},
  status=400, body_contains="at most 8 images per request"))
cases.append(case("ui-16", "Error: think with an image is rejected (must send text-only state to think)",
  {"state": "Check this screenshot.", "images": [img("ui22_dashboard.png")], "think": 128, "questions": {"q": noul("Is this a dashboard?")}},
  status=400, body_contains="think needs a text state"))
cases.append(case("ui-17", "Error: an https URL is not an accepted image; MCP layer must fetch and encode as data URL first",
  {"state": "x", "images": ["https://example.com/shot.png"], "questions": {"q": noul("Is this a dashboard?")}},
  status=400, body_contains="data:image"))

json.dump({"use_case": "22-image-and-ui-decisions", "cases": cases}, open(os.path.join(os.path.dirname(__file__), "cases/22-image-and-ui-decisions.json"), "w"), indent=1)
