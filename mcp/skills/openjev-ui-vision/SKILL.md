---
name: openjev-ui-vision
description: Use when you have a screenshot, photo, UI test failure image or an accessibility tree/DOM snapshot and must decide what it shows or what to do next: is this a login wall, error page, cookie/consent modal or dashboard; which element should be clicked next; would clicking this submit payment, delete data or send a message; is the browser task done or blocked (CAPTCHA, 2FA, permissions); classifying an uploaded image against fixed categories.
---

# UI and image decisions

Precedence: DOM/accessibility-tree text (candidate mode) over pixels (image mode) whenever a tree
exists: faster, precise, and `think` is allowed. Deterministic signals (confirmation URL/text)
beat a `done` read. Never read text out of images (use OCR/DOM); the model gives no coordinates.

## Candidate mode (22b, 5/5)
State: `Goal: ...`, `Page: <url> (accessibility tree)`, then `e01 link 'Home'`, ... with disabled /
precondition notes. Choice keys are exactly the candidate ids, each described by role and name:
```json
{"next": {"type": "choice", "instructions": "Which element should the agent interact with next to make progress on the goal?", "criteria": {"e03": "search box", "e04": "sign in button", "e06": "size dropdown, where the size is chosen", "e07": "add to cart button", "e08": "add to wishlist button", "e10": "privacy policy link"}}}
```
Act at `p_top` >= 0.8; re-validate the id against the live DOM at click time (ids go stale).
Trim the tree to visible interactive nodes (< 60 is comfortable, 255 max).

## Image mode (22, 11/11)
`ask_image` arguments:
```json
{"images": [{"path": "screenshot.png"}], "state": "Screenshot of the page the browser agent just loaded.",
 "questions": {"kind": {"type": "choice", "instructions": "What kind of page does this screenshot show?",
  "criteria": {"login_wall": "a sign-in or paywall page asking for credentials or a subscription", "dashboard": "a working application page with data, metrics or lists",
               "error_page": "an HTTP error or crash page such as 404 or 500", "consent_modal": "a cookie or privacy consent dialog covering the page"}}}}
```
State the task when it decides the answer, and image order for several images.

## Safety flags (both modes)
```json
{"irreversible": {"type": "noul", "instructions": "Does clicking this element commit an action that cannot be undone, such as submitting payment, deleting data or sending a message?"},
 "done": {"type": "noul", "instructions": "Does the page state show that the goal is fully accomplished?"},
 "blocked": {"type": "noul", "instructions": "Is the agent blocked by something it cannot get past without human help, such as a CAPTCHA, a two-factor prompt or a permissions error?"}}
```
Append `Proposed click: e04 button 'Place order'` to the state. irreversible >= 0.5 -> confirm with
the human; auto-click only <= 0.15. Page-state nouls act at >= 0.8. Fail closed to "ask the human".

## Worked example
Goal "add the blue shoes, size 42, to the cart"; tree shows `e07 button 'Add to cart' (disabled until
a size is chosen)`. `next` -> `e06` (size dropdown) at 0.9996, not the tempting e07.
