# CACHE MIND — Data Design

Three data surfaces, each with a deliberate schema:

1. **Input** — the object catalog + request stream (`workload/`)
2. **Runtime** — the signals the engine derives and the decisions it emits (`engine/`)
3. **Output** — per-epoch metrics time series, persisted to SQLite (`benchmark/`)

---

## 1. Input data model

### `ObjectSpec` — a cacheable backend object

| field | type | unit | why it exists | synthetic distribution |
|---|---|---|---|---|
| `key` | str | — | identity | `"<profile>:<5-digit>"` |
| `size_bytes` | int | bytes | RAM footprint → the "resource footprint" signal | **log-normal** (heavy tail: most objects small, a few large) — matches CDN/object-store size studies |
| `gen_latency_ms` | float | ms | time to rebuild from origin → the latency half of "retrieval cost" | **bimodal**: a cheap mode (DB read / warm compute) and an expensive mode (cold external API / model inference). Fraction expensive = `expensive_frac` |
| `gen_cost_usd` | float | USD | money to rebuild (metered API price, compute-seconds) → the $ half of "retrieval cost" | correlated with `gen_latency_ms`; expensive objects also cost real money |
| `ttl_s` | float | s | freshness horizon → drives refresh-vs-evict | uniform per profile; `<=0` ⇒ ∞ (never staleness-driven) |
| `volatility` | float | 0–1 | P(underlying data drifted) per unit of staleness → refresh **value** | uniform per profile; low for recsys embeddings, higher for API resources |
| `compressible` | float | 0–1 | fraction of size recoverable by compression → the COMPRESS action | `Beta(2,2)` scaled per profile (api JSON compresses well ~0.75, recsys blobs poorly ~0.35) |
| `tags` | tuple[str] | — | profile + `expensive`/`cheap` class, for slicing results | derived |

Rationale for the shape (not the exact numbers — those are tunable in
`workload/catalog.py::PROFILES`):

- **Log-normal sizes** — real caches see a few big objects dominating bytes while
  most objects are tiny. A policy that ignores size (LRU/LFU) lets a handful of
  large low-value objects evict hundreds of small high-value ones.
- **Bimodal regeneration cost** — the PS's core example ("2 s + $0.01 to
  regenerate vs. instant and free"). If cost were unimodal, cost-awareness would
  barely matter; the bimodality is what separates CACHE MIND from LRU/LFU.
- **Cost/latency correlation** — an object that's slow to rebuild is usually also
  the one that costs money (external API, GPU). Modelled with a shared
  `is_expensive` draw.

### Two profiles (PS: "simulate at least 2 distinct workload types")

| | `api` — read-heavy API service | `recsys` — compute-heavy recommender |
|---|---|---|
| objects | ~6 000 | ~2 500 |
| median size | ~12 KB | ~180 KB |
| expensive fraction | 35 % | 80 % |
| expensive latency | 120–900 ms | 250–2200 ms |
| dominant cost | **$ per API call** | **latency / compute** |
| volatility | 0.02–0.35 | 0.004–0.06 |

Same engine, same weights, no retuning — it must win on both.

### Request stream

Popularity follows a **Zipf** law over *ranks* (`α ≈ 0.92–1.05`); a
rank→object permutation maps ranks to catalog entries. Arrivals are **Poisson**
with a per-scenario rate function `λ(t)`:

| scenario | what changes over the run |
|---|---|
| `steady` | nothing — stationary Zipf, constant λ |
| `spike` | at t≈45 %, ~25 cold objects jump to the top ranks and λ×3 for ~15 % of the run, then relax |
| `popularity_shift` | the rank→object permutation is continuously perturbed (≈`n/400` swaps per simulated second) |
| `diurnal` | λ(t) sinusoidal, 0.35×–1.65×, and the *active* universe contracts at low tide |
| `cold_start` | λ ramps from 10 % to 100 % over the first 15 %; cache starts empty |
| `regime_flip` | alternates every ~18 % of the run: an expensive-stable regime (cost/frequency weighting wins) and a cheap-churny high-rate regime (recency wins) — no fixed weight vector is good in both |

A `Workload` also exposes `working_set_bytes` (Σ size of every distinct key
requested) — the reference for sizing L1 (benchmarks use 12 %; L2 = 4×L1,
L3 = 12×L1).

### Tiered cost model (`common.CostConfig`)

| component | price / latency | basis |
|---|---|---|
| L1 RAM | $0.12 / GB-hr · 0.5 ms | managed in-memory (ElastiCache-class) |
| L2 Redis-class | $0.030 / GB-hr · 4 ms | separate warm tier, ~4× cheaper |
| L3 cold store | $0.004 / GB-hr · 28 ms | object store / NVMe, near-free |
| origin regenerate | `gen_cost_usd` + `gen_latency_ms` | from the catalog |
| user latency | $2 × 10⁻⁶ per request-ms | conversion/abandonment proxy |
| promote / demote | $0.010 per GB moved | inter-tier data movement |

One source of truth; identical for every policy, so the benchmark is
apples-to-apples even though `CACHE MIND` and `*-tiered` use three tiers.

---

## 2. Runtime data — derived signals

The engine never stores raw history per object beyond `CacheEntry`
(`freq`, `last_access`, `refreshed_at`, `inserted_at`, `hits_since_refresh`).
Everything else is computed on demand and **normalised online** by `ScoreRefs`,
an EWMA (α = 0.02) of observed magnitudes:

| signal | formula | normaliser |
|---|---|---|
| `core` | `freq · (gen_latency_ms + gen_cost_usd/lat_price) / size_kb` | `core_ref` (EWMA) then soft-cap |
| `rec` | `exp(-idle_s / τ)` | `τ` adapts to observed reuse gaps |
| `freq` | `log1p(freq) / log1p(freq_ref)` | `freq_ref` (EWMA) |
| `cost` | `cost_ms_equiv / cost_ref_ms` | `cost_ref_ms` (EWMA) |
| `size` | `size_bytes / size_ref_b` | `size_ref_b` (EWMA) |
| `pred` | `p_soon · confidence` from the access predictor | — |

**Access predictor** (`engine/predict.py`) — per key: EWMA of the inter-access
gap + variance, and fast/slow hit-rate EWMAs. Yields `p_soon`, `trend`,
`confidence`, `expected_hits(n_epochs)`. Drives the `pred` signal, the `E[hits]`
term in the tier economics, and the PREFETCH list. A few floats per key,
bounded to 40 k tracked keys.

Online normalisation is what makes one weight vector portable across the `api`
and `recsys` profiles despite their 15× size difference.

**Bandit context** (8 features per epoch, all normalised to ~[0,1]): arrival
rate, access-distribution entropy, hit-rate trend, per-miss cost pressure, cache
pressure, eviction rate, ghost-hit rate, bias.

**Decision feed** — a bounded ring buffer (40) of
`{epoch, action, key, reason}` records (`admit_reject`, `proactive_refresh`,
`autoscale_grow/shrink`, `serve_stale`) surfaced to the dashboard. Not
persisted — it's an explainability stream, not state.

---

## 3. Output data — `results/cachemind.db` (SQLite)

Normalised on `run_id`:

```
runs (run_id PK, ts, profile, scenario, policy,
      requests, hit_rate, stale_rate, avg/p95/p99_latency_ms,
      cost_total, cost_origin, cost_latency, cost_memory,
      evictions, refreshes, final_capacity_bytes, peak_used_bytes,
      config_json)

epochs (run_id FK→runs, epoch, t_sim,
        requests, hit_rate, stale_rate, avg/p95_latency_ms,
        cost_total, cost_origin, cost_latency, cost_memory,
        capacity_bytes, used_bytes, entries, evictions, refreshes,
        regime, bandit_arm, w_core, w_rec, w_freq, w_cost, w_size,
        PRIMARY KEY (run_id, epoch))

ix_runs_key   (profile, scenario, policy)
ix_epochs_run (run_id)
```

Every matrix run **appends** — results are queryable history, not a
last-write-wins JSON blob. Example (window function over run history):

```sql
-- latest cost of each policy per scenario, % saved vs GDSF
WITH latest AS (
  SELECT scenario, policy, cost_total,
         ROW_NUMBER() OVER (PARTITION BY scenario, policy ORDER BY ts DESC) rn
  FROM runs WHERE profile = 'api')
SELECT l.scenario, l.policy, l.cost_total,
       round(100*(1 - l.cost_total / b.cost_total), 1) AS saved_pct
FROM latest l
JOIN (SELECT scenario, cost_total FROM latest WHERE policy='GDSF' AND rn=1) b
  ON b.scenario = l.scenario
WHERE l.rn = 1 ORDER BY l.scenario, l.cost_total;
```

`python -m benchmark.store --profile api` prints exactly this.

The `epochs` table is the important one for judging: it lets us show *when*
CACHE MIND pulls ahead (the gap widens during the spike), which weight the bandit
picked at each moment, and the autoscaler's capacity trajectory — not just a
single end-of-run number.

### Live stream schema (`api/` → dashboard)

SSE `event: epoch`, one JSON frame per simulated epoch:
`{epoch, t, rate, spike_active, scenario, policies:[{policy, hit_rate,
cost_total, cost_origin/latency/memory, capacity_mb, used_mb, entries,
weights?, bandit_arm?, regime?, decisions?}], cost_report}`.
Same field names as `EpochSnapshot` so the offline and live paths agree.
