# CACHE MIND — Full Project Explanation

**An AI brain that sits above a multi-level cache.**
VH-26 · KASUKABE DEFENCE FORCE · VCET Hackathon 2026 · Domain: Application Scaling

The single reference: what every part does, why, the exact maths, the data
model, the cost model, the results. See also [architecture](architecture.md),
[originality](originality.md), [data-design](data-design.md),
[demo-script](demo-script.md).

---

## 1. The problem

Caching keeps hot data in fast memory so the backend isn't hit for every
request. When the cache is full, something must go. LRU evicts the
least-recently-used; LFU the least-frequently-used. **Both look only at access
pattern.** They are blind to:

- **size** — one 200 KB object costs the space of 200 × 1 KB objects;
- **regeneration cost** — some objects take 2 s and $0.01 to rebuild from an
  external API, others recompute instantly for free.

> A rarely-used object that costs 2 s + $0.01 to regenerate is worth more to
> keep than a popular one that rebuilds for free.

At scale this forces a lose-lose choice: over-provision expensive RAM "just in
case", or under-provision and thrash the backend during a surge.

## 2. What we built — the thesis

**CACHE MIND** turns the cache from a passive store into an autonomous
decision-maker. Two ideas:

**(a) Multi-level cache.** L1 (RAM: ~0.5 ms, $0.12/GB-hr), L2 (Redis-class:
~4 ms, $0.03/GB-hr), L3 (cold store: ~28 ms, $0.004/GB-hr). Every tier is an
order of magnitude faster and cheaper than regenerating from a
120–2000 ms / $0.0005–0.006 origin. An object that falls out of L1 is
**demoted**, not evicted — the next access is a warm hit, not an origin miss.

**(b) An economic decision engine.** Per object, per epoch, CACHE MIND chooses
among **KEEP · PROMOTE · DEMOTE · PREFETCH · REFRESH · COMPRESS · EVICT · SCALE**
by computing where each object earns the most money:

```
net_value(o, tier) = E[hits over horizon] · serve_saving(o, tier) − hold_cost(o, tier)
```

**Algorithm class: hybrid.** Heuristic GDSF core for the value magnitude;
LinUCB bandit for the weights; a per-object access predictor; a cost-benefit
autoscaler. The heuristic gives a provable safety floor; the ML gives runtime
adaptivity and prediction.

## 3. System overview

```
workload/     (t, ObjectSpec) stream — 2 profiles, 6 scenarios
   │
   ▼
benchmark/ SimDriver — owns the clock + all cost accounting
   │  per request:  L1/L2/L3 hit  /  blocking refresh  /  origin miss
   │  per epoch:    policy.maintenance() → drain refresh + prefetch → snapshot
   ▼
baselines/ LRU LFU GDS GDSF        engine/ CACHE MIND
           LRU-tiered GDSF-tiered  (tiers · predict · scoring · bandit · autoscaler)
   │
   ▼  EpochSnapshot rows → results/cachemind.db (SQLite) + REPORT_*.md + charts
   ▼
api/ FastAPI + SSE live simulator  ──►  dashboard/ React real-time view
```

Every module imports only `common/interfaces.py`. `TieredStore` sits in
`common/tierstore.py` so baselines and engine share the exact same tier
mechanics.

---

## 4. `common/interfaces.py` — the contract

| Type | Purpose |
|---|---|
| `ObjectSpec` | a backend object: `key, size_bytes, gen_latency_ms, gen_cost_usd, ttl_s, volatility, compressible, tags` |
| `CacheEntry` | a cached object + `tier`, `compressed`, `freq`, `last_access`, `refreshed_at` |
| `RequestOutcome` | one served request: `hit, hit_tier, latency_ms, cost_usd, action` |
| `CostConfig` | tier price/latency table + latency $, move $, decompress latency |
| `EpochSnapshot` | the per-epoch time-series row (hit rates by tier, cost breakdown, tier occupancy, promotions/demotions/prefetches, weights, regime) |
| `CachePolicy` (ABC) | `lookup, on_hit, on_admit, should_refresh, maintenance, pending_refreshes, pending_prefetches` + `tier_used/tier_capacity/counters` |
| `TieredStore` | L1/L2/L3 dicts, byte accounting, `place / move / remove / set_compressed` |

## 5. `workload/` — the data (Priti)

**Catalog** (`catalog.py`): per profile, sample `size_bytes` log-normal,
`gen_latency_ms` **bimodal** (cheap DB reads vs. expensive cold-API / model
inference), `gen_cost_usd` correlated, `ttl_s`, `volatility`, `compressible`.

**Two profiles**: `api` (read-heavy, ~6000 small objects, $ per miss) and
`recsys` (compute-heavy, ~2500 large objects, latency per miss). Same engine,
no retuning, must win on both.

**Six scenarios** (`scenarios.py`) — Zipf popularity over ranks, Poisson
arrivals with a per-scenario rate:
`steady` · `spike` (flash crowd, ×3 rate) · `popularity_shift` (drifting hot
set) · `diurnal` (sinusoidal rate + contracting working set) · `cold_start` ·
`regime_flip` (alternates an expensive-stable regime with a cheap-churny one —
no fixed weight vector is good in both).

## 6. `baselines/` — the comparison (Priti)

Single-tier: **LRU / LFU / GDS / GDSF**, from scratch against `CachePolicy`.
Tiered: **LRU-tiered / GDSF-tiered** — same L1/L2/L3 hardware and cost model,
but placement is dumb (everything enters L1; the wrapped rule picks the victim;
victims are demoted, not evicted).

This makes the comparison fair: **CACHE MIND vs GDSF** shows the whole-system
win; **CACHE MIND vs GDSF-tiered** isolates the value of *deciding where each
object lives*.

## 7. `engine/` — CACHE MIND (Avadh)

### 7.1 Access predictor (`predict.py`)
Per key: EWMA of the inter-access gap + its variance, and fast/slow hit-rate
EWMAs. Yields `p_soon` (prob. accessed within a horizon), `trend`
(heating up / cooling), `confidence` (1 / (1 + coeff-of-variation)),
`expected_hits(n_epochs)`. Cheap, online, no training. Feeds the `ml` value
signal and the PREFETCH list.

### 7.2 Value score (`scoring.py`) — an explicit 3-family hybrid
```
value(o) = L
         + w_gdsf  · GDSF(o)      ← proven cost-aware heuristic (Cherkasova '98)
         + w_rec   · RECENCY(o)   ┐
         + w_fresh · FRESH(o)     ├ hand-designed heuristics GDSF is blind to
         − w_size  · SIZE(o)      ┘
         + w_ml    · ML(o)        ← learned: predicted future access value

GDSF(o)  = freq · retrieval_cost / size_kb  / core_ref   · aging(idle)
RECENCY  = exp(−idle / τ)                    FRESH = 1 − staleness(o)
SIZE     = full_size / size_ref  (unit)      ML    = p_soon · confidence   ∈ [0,1]
```
The GDSF term keeps its full magnitude (~[0, 40]); the four heuristic/ML terms
are `[0, 1]` refinements on top. **No family is structurally dominant** — the six
`w_*` are re-chosen every epoch by the bandit (§7.4). Pick the `proven`
personality and `value ≈ classical GDSF` — a safety floor you can always fall
back to. `L` is GreedyDual inflation; all `*_ref` normalisers are EWMAs of the
live stream, so one weight set works on both profiles.

### 7.3 Tier placement — the economics (`scoring.py`)
```
serve_saving(o, tier) = max(gen_latency_ms − tier_latency, 0)·λ$ + gen_cost_usd
hold_cost(o, tier)    = tier_$per_GB_hr · size · horizon
net_value(o, tier)    = E[hits] · serve_saving − hold_cost
best_tier(o)          = argmax_tier net_value   (or evict if all < 0)
```
Because origin latency (hundreds of ms) dwarfs tier latency (0.5–28 ms), an
expensive object is worth caching **even in cold L3**. `best_tier` still prefers
L1; admission only displaces a *less valuable* L1 occupant (`min_value` guard),
otherwise the newcomer settles for L2.

### 7.4 The bandit (`bandit.py`)
LinUCB over 6 weight "personalities" — each is a full `{gdsf, rec, fresh, size,
ml}` vector, not an on/off switch:

| arm | leans on | use |
|---|---|---|
| `balanced` | everything | default |
| `proven` | GDSF only (others ≈ off) | `value ≈ classical GDSF` — the floor |
| `predictive` | `ml` ×8 | forecast leads (churny / shifting hot set) |
| `recency` | `rec` ×7 | recency heuristic leads |
| `freshness` | `fresh` ×6 | protect near-TTL data |
| `lean` | `size` ×6 | RAM-tight, size-averse |

8-feature context per epoch; reward `= hit_rate − 0.35·norm_latency −
0.45·norm_cost`. Standard, explainable, no training phase.

### 7.5 Autoscaler (`autoscaler.py` + `cachemind._autoscale`)
**All three tiers are dynamic.** L1 uses the ghost-list ROI test: keep a
recently-evicted key + size + regen $, each epoch `grow` if `Σ ghost-hit regen $
> RAM-step-rent × 1.4`, `shrink` if a step has been idle ≥ 2 epochs and the
forgone ghost benefit is below half the rent (bounded 3× ceiling, symmetric).
L2/L3 scale on fill + payoff: grow when `fill > 0.9` and that tier still earns
its hits, shrink after 3 consecutive epochs below 50 % fill — within bounds
`L2 ∈ [2×, 10×] L1`, `L3 ∈ [3×, 30×] L1`.

### 7.6 The epoch loop (`cachemind.py`) — 11 steps
observe → understand → predict → score → net-value per tier → promote/evict
(hysteresis, move budget) → **prefetch** predicted-hot → **refresh** hot
near-stale (serve stale now, background-refresh next epoch) → **compress**
marginal keepers in a tight tier → **scale** L1/L2/L3 → **learn** (bandit) →
update weights + τ + normalisers.

## 8. `benchmark/` (Prathamesh)

- **`SimDriver`** — owns the clock and all money. Per-request: charges tier
  latency $ + origin $; per-epoch: per-tier memory $, `move $` for
  promotes/demotes, drains `pending_refreshes()` and `pending_prefetches()`
  (origin $, no client latency).
- **`store.py`** — appends every run to `results/cachemind.db`; `runs` +
  `epochs` tables, window-function savings query.
- **`report.py`** — `--run` runs the matrix, writes `REPORT_*.md` + charts
  (hit rate / cost / p95 per scenario; CACHE MIND tier occupancy + bandit
  weights over time).
- **`studies.py`** — `ablation` (disable one capability at a time) and
  `sensitivity` (L1 = 5–40 % of working set).

## 9. `api/` + `dashboard/` (Sahil)

`LiveSim` runs several policies in lockstep over an epoch-by-epoch stream;
`inject_spike` triggers a flash crowd mid-demo. SSE streams one JSON frame per
epoch: per-policy hit rates **by tier**, cost breakdown, tier occupancy, the
decision feed and bandit weights. The React dashboard shows all of it live plus
an **Inject traffic spike** button.

## 10. `tests/` — 32 tests (Avadh)
Tiered store mechanics · compression accounting · value ordering · tier
economics · predictor cadence + candidate selection · bandit convergence ·
autoscaler grow/hold · ghost-list bound · CACHE MIND end-to-end (overflow
demotes not evicts; single-tier fallback) · the headline claims
(`test_cachemind_beats_every_baseline_on_cost`,
`test_tiering_lifts_hit_rate_and_lowers_cost`,
`test_cachemind_serves_faster_and_cheaper_than_dumb_tiering`).
`.github/workflows/ci.yml` runs pytest + a benchmark smoke on every push.

## 11. Results & ablation (`api`, L1 = 12 % of working set)

| policy | hit | p95 | cost $ | vs GDSF |
|---|---|---|---|---|
| LRU / LFU | 0.77 / 0.81 | 426 / 340 ms | 148 / 128 | −120 % / −91 % |
| GDSF (best single-tier) | 0.77 | 23 ms | 67 | — |
| GDSF-tiered (same L1/L2/L3, dumb) | 0.99 | 14 ms | 39 | +42 % |
| **CACHE MIND** | 0.99 | **6 ms** | **20** | **+71 %** |

spike / popularity-shift / regime-flip vs GDSF: **−74 / −73 / −82 %**; vs
GDSF-tiered: **−48 to −50 %**. Cheapest at every L1 size (5–40 %).

**Ablation** (`results/ABLATION_api.md`) — two capabilities each roughly *double*
total cost when removed:

1. **Tiering** (`CM-notier`): +165–175 %. Overflow demoted to a warm hit instead
   of evicted; hit rate 0.88 → 0.99, p95 19 → 6 ms.
2. **Smart refresh** (`CM-norefresh`): +93–97 %. Serve-stale-now +
   background-refresh-next-epoch instead of a blocking refetch on every stale
   hit. `CM-fixed` (value model + tiers, everything else off) ≈ `GDSF-tiered`
   ($40); refresh + placement take it to $20.

Autoscaler +4–5 %; **bandit / prefetch / compression within ±3 %** on these
stationary Zipf workloads — kept for runtime adaptivity (the PS's requirement)
and robustness under surges. Wins on all 6 scenarios and both profiles.

## 12. Likely jury questions

**"Isn't CACHE MIND just a bigger cache?"** The tiered baselines have the exact
same L1/L2/L3 sizes and cost model. CACHE MIND still beats them by ~18 % cost
and ~⅔ latency — that gap is pure placement intelligence.

**"Heuristic, ML or hybrid?"** Hybrid — GDSF core (heuristic), LinUCB bandit +
access predictor (ML), online-adaptive normalisers.

**"How is retrieval cost known in production?"** Measured. The cache sees every
origin fetch — record latency, take $ from billing metadata, feed EWMAs.

**"Overhead?"** Eviction is sampled (O(1) amortised). Bandit is an 8×8 solve
per epoch. `serve_saving` is memoised per object. The rebalance is O(n) once
per epoch with a bounded move budget.

**"The full 'simulate every decision' from the vision?"** We run **shadow
baseline policies in parallel** in the live sim — real counterfactuals ("here's
what LRU would have cost") — rather than an intractable per-object lookahead
search.

## 13. Who built what

| Member | Branch | Modules |
|---|---|---|
| Avadh Mehta | `feat/avadh` | `common/`, `engine/`, `tests/`, docs, CI |
| Priti Kangne | `feat/priti` | `workload/`, `baselines/` |
| Prathamesh | `feat/prathamesh` | `benchmark/` |
| Sahil Kadam | `feat/sahil` | `api/`, `dashboard/` |
