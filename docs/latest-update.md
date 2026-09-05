# CACHE MIND — latest update

Everything built since the last full docs/results pass, in one place. Read
this before the next jury round — it's the delta, not a re-explanation of
the whole system (that's [`PROJECT.md`](PROJECT.md) / [`architecture.md`](architecture.md)).

---

## 1. Real data

New **`real`** workload profile ([workload/real_catalog.py](../workload/real_catalog.py)):
instead of statistically-sampled objects, it makes one genuine HTTP GET per
object (to `jsonplaceholder.typicode.com`, ~650 endpoints) at catalog-build
time and uses the *actually measured* latency and payload size — not a
distribution. Scoped to the live dashboard only; the offline benchmark keeps
the synthetic catalogs because ablation needs deterministic, repeatable
traffic across many seeds.

Also added **`GET /api/real/ping`** — fires one live HTTP request right now
and returns the measured latency, for an on-demand "this is really the
internet" proof mid-demo.

## 2. A real integration gap, found and fixed

While wiring in real data, discovered `feat/avadh` (the branch behind the PR)
had **stale `api/`, `dashboard/`, `baselines/`, `workload/`, `benchmark/`** —
pre-CACHE-MIND-pivot code, left over from before the big rename. The working
dashboard that had been tested all session only ever existed in the local
working folder, never committed. Fixed by reconciling everything into one
consistent state (commit `integrate`).

**Also caught a real bug** during the reconciliation test run: `serve_saving()`
in `engine/scoring.py` memoized by `id(spec)` — Python reuses that id once an
object is garbage-collected, so a long-running session could silently serve a
stale value computed for a *different* object. Fixed to key on the actual
cost fields instead.

## 3. Prefetch — fixed the logic, honest about its limits

`_pick_prefetch` used to scan `_recent_miss` — but admission caches almost
every miss immediately, so by the time prefetch ran, those keys were already
resident and got filtered out. Replaced with two real sources:

- the **ghost list** (recently-evicted, genuinely non-resident keys)
- a new **co-access correlation tracker** ([engine/correlate.py](../engine/correlate.py)) — when a hot object is served, a known partner gets a chance to warm too

**Honest finding, not spin:** even fixed, prefetch still fires near-zero in
the documented benchmark configs — traced it to L1+L2+L3 combined capacity
(~17× L1 by default) comfortably exceeding the catalog size, so almost
nothing ever leaves the cache entirely. That's a capacity-sizing property of
the demo, not a remaining bug — written into the demo script as a prepared
answer rather than hidden.

## 4. Two new signals (from the "factors" audit)

- **Access pattern** (`predictor.access_pattern`) — classifies every
  resident object as `periodic` / `bursty` / `random` / `new`, from stats
  already being tracked (no new cost). Surfaced on the dashboard as a chip
  row with live counts. Not yet folded into `value()` itself — kept separate
  on purpose, to avoid moving the already-tuned, documented benchmark numbers.
- **Correlation** (`engine/correlate.py`) — feeds PREFETCH, see §3.

## 5. Deliverable #3 was actually broken — now fixed

The PS asks for benchmark results vs **LRU/LFU and GDS**. `benchmark/runner.py`'s
policy list never included plain GDS — only GDSF. The literal submitted
`results/REPORT_api.md` had zero GDS rows. Fixed and regenerated: GDS now
appears in every scenario table in both `REPORT_api.md` and `REPORT_recsys.md`
with its own real measured numbers (worse than GDSF everywhere — GDSF's extra
size-awareness earns its keep). Also removed stale `*_aacms_internals.png`
chart files left over from before the CACHE MIND rename.

## 6. Full dashboard visual redesign

Dark "mission control" theme — gradient glass header, a glowing hero stat
card for the headline cost-saving number, monospace tabular numbers,
animated live/spike badges, a gradient fill under the CACHE MIND line on
every chart so it visually stands out against the baselines. Verified live
for 640+ epochs with zero console errors.

**Emoji removed everywhere** — dashboard labels/buttons, the chart spike
marker, and the two docs that had them (`PROJECT.md`, `demo-script.md`).
Confirmed with a full-repo scan.

**Fixed:** `dashboard/vite.config.ts` was binding to IPv6 only (`::1`), so
`http://127.0.0.1:5173` got connection-refused even though the server was up.
Added `host: true` — both `localhost` and `127.0.0.1` work now.

## 7. Two new live, interactive panels

**Surge fader** — a drag control (1x–6x) next to the other header controls,
not a fixed preset. Dragging it sends a continuous multiplier to
`POST /api/sim/{id}/surge`, which genuinely raises the simulated request rate
and, past 2x, promotes a batch of cold objects — verified live: rate jumped
from ~520/s to 2594/s on drag, cost-saving card jumped to 76%, SPIKE ACTIVE
badge fired automatically through the existing marker system.

**Live cache contents grid** — three lanes (L1/L2/L3) showing the objects
actually resident right now as small chips, colour-coded by access pattern,
animating in on admit/promote and out on demote/evict. Backed by a new
`CacheMind.sample()` method. Verified genuine churn over a 12-second window
(one object left L1, two new ones entered) — not scripted.

## 8. "Why" — decision explanation panel

New panel showing the real signals behind the *latest* eviction, promotion,
or demotion — future-access probability, regeneration cost, memory
footprint, freshness, access trend, and the final weighted score, plus the
plain-English reason. All real numbers from the engine (`CacheMind._explain_entry`),
not placeholders.

**Fixed along the way:** `refresh` decisions fire so often they were crowding
the rarer evict/promote/demote decisions out of the small live-decisions
window before anyone could see them. The dashboard now remembers the last
decision with a full breakdown across epochs instead of re-deriving it from
just the current frame.

## 9. Bonus: a Chrome extension

New [`chrome-extension/`](../chrome-extension/) — the same 3-tier,
value-based placement idea applied to real browser storage: in-memory `Map`
(L1), `chrome.storage.local` (L2), `IndexedDB` (L3). Deliberately simplified
to the GDSF shape only — the point is proving placement works on real browser
APIs, not re-implementing the whole engine a second time.

Verified with a Node harness mocking `chrome.storage`/`IndexedDB`/`fetch`,
driving the real unmodified message handler — including a forced cross-tier
demotion confirming the displaced object's value survives the move intact
(this test caught and led to fixing one real read-after-remove bug before
shipping).

## Where things stand

- **32/32 tests pass**, TypeScript compiles clean, throughout every change above.
- Everything is pushed to `feat/avadh` — see **[PR #6](https://github.com/avadh-lang/VH-26-KASUKABE_DEFENCE_FORCE/pull/6)** for the full diff.
- `main` has everything through PR #5 (the CACHE MIND pivot); PR #6 has all nine items above.
