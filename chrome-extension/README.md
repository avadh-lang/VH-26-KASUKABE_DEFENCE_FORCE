# CACHE MIND — browser tier demo

A small, honest proof-of-concept: the same 3-tier, value-based placement idea
from the main project, applied to real browser storage instead of a
simulated RAM/Redis/cold-store.

This is a **bonus artifact**, not a replacement for the main engine
(`engine/`). It exists to show the placement idea is portable — same shape
of decision, a completely different runtime.

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
2. Click the extension icon. Click any of the 10 demo objects (real GETs to
   `jsonplaceholder.typicode.com`, on purpose — same real-API idea as the
   main dashboard's "real" profile).
3. Fetch a few — watch L1 fill up (capacity 4, deliberately small so
   placement decisions are visible fast), then keep fetching new ones and
   watch the decision feed show `place` → `promote`/`demote` as objects
   compete for room.
4. Refetch something already cached — instant hit, 0 ms, no network call.

## Verified how

There's no way to click through a real Chrome extension from this repo's
test suite, so the placement/demotion logic (`background.js`) was verified
with a Node harness that mocks `chrome.storage.local`, `IndexedDB`, and
`fetch`, and drives the real (unmodified) message handler end to end —
including a forced-demotion scenario confirming a displaced object's value
survives the move down a tier intact. That harness isn't part of this
folder (it's throwaway, not shipped code) — the logic it exercises is.
