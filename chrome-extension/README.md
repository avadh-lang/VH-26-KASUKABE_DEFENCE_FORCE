# CACHE MIND — live browser cache

A small, honest proof-of-concept: the same 3-tier, value-based placement idea
from the main project, applied to **real browser traffic** — not a fixed
demo dataset, not a simulation. It caches actual GET requests the page
you're on makes, for real, while you browse.

This is a **bonus artifact**, not a replacement for the main engine
(`engine/`). It exists to show the placement idea is portable — same shape
of decision, a completely different runtime.

## How it actually sees real traffic

`hook.js` runs in the page's own JS realm (`"world": "MAIN"` in
`manifest.json`) and overrides `window.fetch` — an ordinary isolated-world
content script only ever sees its own private copy of `fetch`, never the
page's. `bridge.js` is the isolated-world content script that relays
messages between that page-context hook and the extension's background
service worker (`chrome.runtime` isn't available in the MAIN world).

**Scope is deliberately narrow — this is a caching layer, not a replay
surface for anything personal:**
- GET requests only. Nothing that mutates state is touched.
- Nothing credentialed: `credentials:"include"` is skipped outright, and so
  is a same-origin call left at fetch's default (`"same-origin"`) —
  because that default *still* attaches the page's cookies. Only
  cross-origin calls (which never carry this origin's cookies under
  `"same-origin"` mode) or explicit `credentials:"omit"` qualify.
- Nothing with an `Authorization` header, and nothing under a path that
  looks account/cart/checkout/session/payment-shaped.
- Responses: only cached if `content-type` isn't `text/html` (never replay
  a whole personalized page) and `Cache-Control` doesn't say
  `no-store`/`private`.

Anything that fails any check just falls straight through to the real
network, untouched — a miss on our side never means a broken page, only a
skipped optimization.

## What it maps to

| Main project | This extension | Real tradeoff |
|---|---|---|
| L1 — RAM | an in-memory `Map` in the service worker | fastest — but Chrome kills idle service workers, so L1 is best-effort only (an honest limitation, not hidden) |
| L2 — Redis | `chrome.storage.local` | fast, survives sleep, small quota |
| L3 — cold store | `IndexedDB` | slower, survives sleep, much bigger quota |

## What's simplified vs. the main engine, and why

`cachemind.js` uses the GDSF shape only (`freq · latency / size`, aged by
recency) — not the full 3-family hybrid (GDSF + heuristics + ML) from
`engine/scoring.py`, and there's no bandit or access predictor here.
Re-implementing the whole engine a second time in JavaScript would just be
duplicating it, not proving anything new. What this *does* prove for real:
placement decisions — including **demoting a weaker occupant down a tier
instead of discarding it**, the same DEMOTE-not-EVICT idea as the main
project — genuinely work against real browser storage APIs, not just in
simulation.

## Try it

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   select this `chrome-extension/` folder.
2. Browse a site that makes real cross-origin GET calls (a public GitHub
   repo's file browser hitting `api.github.com` is a reliable one). Open
   the extension popup and confirm the **Live caching** toggle is on.
3. Watch the tier bars fill and the decision feed show real
   `hostname/path` keys — not demo data — as `place` → `promote`/`demote`
   plays out (capacity is deliberately tiny: L1=4, L2=10, L3=60, so
   placement decisions are visible fast).
4. Revisit the same page/file — the feed shows `hit … network call
   skipped` for that exact URL, served straight from cache.
5. A **manual test** panel (10 jsonplaceholder buttons) is still there
   below the live feed — it always works, independent of whatever site
   you're on, useful if a page you're testing makes no eligible calls.

## Verified how

There's no way to click through a real Chrome extension from this repo's
test suite, so the placement/demotion logic (`background.js`, including the
`HOOK_LOOKUP`/`HOOK_STORE` handlers `hook.js` calls into) was verified with
a Node harness that mocks `chrome.storage.local`, `IndexedDB`, and
`chrome.runtime`, and drives the real (unmodified) message handlers end to
end — a stored real-URL body round-trips byte-for-byte on lookup, and a
forced-demotion scenario confirms a displaced object's value survives the
move down a tier intact. That harness isn't part of this folder (it's
throwaway, not shipped code) — the logic it exercises is.
