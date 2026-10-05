---
name: openjev-ui-vision
description: "Reads screenshots, photos, UI test failure images and accessibility trees with OpenJev (ask_image, ui_decision) to decide what a page shows or what to do next: login wall, error page, cookie or consent modal, dashboard, which element to click, whether a click would pay, delete or send, and whether a browser task is done or blocked (CAPTCHA, 2FA, permissions). Use when a decision depends on an image or a UI snapshot, or when classifying an uploaded image into fixed categories."
---

# UI and image decisions

Tools live on the `openjev` MCP server as `mcp__openjev__<tool>` (with the openjev-mcp plugin: `mcp__plugin_openjev-mcp_openjev__<tool>`). If they are missing, load skill `openjev-data-prep` (Connect) and do not start or restart OpenJev or its MCP server without the user's go-ahead; give the user the command.

Precedence: DOM or accessibility-tree text (candidate mode) over pixels (image mode) whenever a tree exists: faster, precise, and `think` is allowed. Deterministic signals (confirmation URL or text) beat a `done` read. Never read text out of images (use OCR or the DOM); the model gives no coordinates.

Image paths must be absolute and inside the MCP server's allowed roots (its working directory plus `OPENJEV_MCP_ROOTS`); if refused, see `openjev-data-prep` Connect for the alternatives (a `data_url` or `base64` image, or a server whose roots include the file).

## Checklist

```
- [ ] Tree available? Use candidate mode (recipe ui_decision or ask with a text state)
- [ ] Otherwise image mode: 1 to 8 images, absolute paths in the allowed roots, short text state
- [ ] State the task and the image order when they decide the answer
- [ ] No options.think / options.sequential with images (refused)
- [ ] Act at p of 0.8 or more; confirm irreversible clicks with the human
```

## Candidate mode (accessibility tree)

State: `Goal: ...`, `Page: <url> (accessibility tree)`, then one `e01 link 'Home'` line per element with disabled or precondition notes. Trim to visible interactive nodes (under 60 is comfortable, 255 max). `candidates` are `{id, description}` and the ids are the answer keys. `profile: "strict"` picks the next element.

<!-- openjev-call: recipe -->
```json
{"recipe": "ui_decision", "inputs": {"goal": "add the blue shoes, size 42, to the cart", "tree": "Page: https://shop.example/p/blue-shoes (accessibility tree)\ne03 searchbox 'Search'\ne06 combobox 'Size' (choose a size)\ne07 button 'Add to cart' (disabled until a size is chosen)\ne08 button 'Add to wishlist'\ne10 link 'Privacy policy'", "candidates": [{"id": "e03", "description": "search box"}, {"id": "e06", "description": "size dropdown, where the size is chosen"}, {"id": "e07", "description": "add to cart button"}, {"id": "e08", "description": "add to wishlist button"}, {"id": "e10", "description": "privacy policy link"}], "profile": "strict"}}
```

The `decision` is `click:<id>`, `done`, `confirm`, `blocked` or `refresh` (no decisive state on this read). Act on `click:` at 0.8 or more and re-validate the id against the live DOM at click time (ids go stale after navigation).

## Image mode

Use `mcp__openjev__ask_image`: `images` holds 1 to 8 items (`path`, `data_url`, `base64` with `content_type`, or an https `url` only if the server allows fetching); formats png, jpeg, webp, gif. The server downscales them (`max_side_px`). Keep `state` to a short sentence naming what the images are.

<!-- openjev-call: ask_image -->
```json
{"images": [{"path": "/abs/path/to/screenshot.png"}], "state": "Screenshot of the page the browser agent just loaded.", "questions": {"kind": {"type": "choice", "instructions": "What kind of page does this screenshot show?", "criteria": {"login_wall": "a sign-in or paywall page asking for credentials or a subscription", "dashboard": "a working application page with data, metrics or lists", "error_page": "an HTTP error or crash page such as 404 or 500", "consent_modal": "a cookie or privacy consent dialog covering the page", "other": "none of the above"}}}}
```

For a login-wall check the recipe `ui_decision` with `profile: "login_wall"` and `inputs.caption` plus `inputs.images` as data URLs does the same in one call.

## Safety flags (both modes)

Append `Proposed click: e04 button 'Place order'` to the state (or pass `proposed_click` with `profile: "state"` to the recipe).

<!-- openjev-call: ask -->
```json
{"state": "Goal: buy the blue shoes.\nPage: https://shop.example/checkout (accessibility tree)\ne04 button 'Place order'\nProposed click: e04 button 'Place order'", "questions": {"irreversible": {"type": "noul", "instructions": "Does clicking the proposed element permanently commit an irreversible action, such as submitting payment, deleting data or sending a message?", "criteria": {"true": "the click pays, deletes or sends", "false": "the click only navigates or changes view state"}}, "done": {"type": "noul", "instructions": "Does the page state show that the goal is fully accomplished?", "criteria": {"true": "a confirmation shows the goal is complete", "false": "the goal is not yet complete"}}, "blocked": {"type": "noul", "instructions": "Does the page need human help before the agent can continue, such as a CAPTCHA, a two-factor prompt or a permissions error?", "criteria": {"true": "a CAPTCHA, 2FA or permission wall is shown", "false": "nothing blocks progress"}}}}
```

`irreversible` at 0.5 or more means confirm with the human; auto-click only at 0.15 or less. Page-state questions act at 0.8 or more. Fail closed to "ask the human".

## Worked example

Goal "add the blue shoes, size 42, to the cart"; the tree shows `e07 button 'Add to cart'` disabled until a size is chosen. The `ui_decision` call above returns `click:e06` (the size dropdown), not the tempting e07.
