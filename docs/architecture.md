# AACMS — Architecture

**Adaptive, Application-Aware Cache Management System**
VH-26 · KASUKABE DEFENCE FORCE

---

## 1. The idea in one line

> Score every cached object by *what it actually costs us to lose it right now*,
> keep the score model **adapting at runtime**, and grow the cache **only when
> the math says it pays for itself**.

LRU/LFU rank objects by *access pattern alone*. AACMS ranks them by a
**multi-factor value** that also knows each object's **size** and the real
**latency + dollar cost** to regenerate it — then a contextual bandit re-tunes
the ranking as the workload changes, and a cost-benefit controller resizes the
cache.

---

## 2. Component map

```mermaid
flowchart LR
  subgraph WL[workload/]
    G[traffic generator\nZipf popularity, 5 scenarios] --> P1[api profile\nread-heavy, $ per miss]
    G --> P2[recsys profile\ncompute-heavy, latency per miss]
  end

  WL -->|"(t, ObjectSpec) stream"| DRV

  subgraph BENCH[benchmark/]
    DRV[SimDriver\none clock + one cost model] --> SNAP[EpochSnapshot rows]
    SNAP --> RPT[report: charts + markdown]
  end

  subgraph POL[policies]
    B1[LRU] & B2[LFU] & B3[GDS] & B4[GDSF]
    ENG[engine/ — AACMS]
  end

  DRV <--> POL

  subgraph ENGINE[engine/ internals]
    SC[scoring.py\nvalue = L + w_core·GDSF · (1 + tilt)]
    BA[bandit.py\nLinUCB → weights w every epoch]
    RG[regime.py\nsteady / spike / shift / cold]
    AS[autoscaler.py\nghost list → grow/shrink]
    AD[aacms.py\nadmission · eviction · refresh]
    BA --> SC --> AD
    RG --> AD
    AS --> AD
  end
  ENG --- ENGINE

  SNAP --> API
  subgraph SERVE[api/ + dashboard/]
    API[FastAPI + SSE\nlive simulator, cost ledger] --> UI[React dashboard\nhit rate · cost · autoscaler · decisions]
  end
```

Every module imports **only** `common/interfaces.py` (the shared contract:
`ObjectSpec`, `CacheEntry`, `CachePolicy`, `CostConfig`, `EpochSnapshot`). That
is why four people can build the four boxes in parallel.

---

## 3. The decision engine (per request)

```mermaid
flowchart TD
  R[request key @ t] --> L{in cache?}
  L -- no --> M[fetch from origin\npay gen_latency + gen_cost]
  M --> ADM{admission:\nvalue(new) ≥ value(weakest victim) · 0.85 ?}
  ADM -- yes --> EV[evict lowest-value entries\nsampled, Redis-style] --> INS[insert]
  ADM -- no --> SKIP[don't cache\nremember demand for next time]
  L -- yes, fresh --> HIT[serve @ ~0.5 ms, cost 0]
  L -- yes, stale --> RF{refresh_priority high?\n drift × reuse ÷ $}
  RF -- yes --> BLK[blocking refresh\npay gen cost, update entry]
  RF -- no --> STALE[serve stale now\nsave the refresh $]
```

**Value score** (`engine/scoring.py`):

```
value(o) = L  +  w_core · CORE(o) · (1 + tilt(o))

CORE(o)  = normalised  freq(o) · retrieval_cost(o) / size(o)     ← the GDSF term
tilt(o)  = w_rec·(recency−0.4) + w_freq·(freq−0.4)
         + w_cost·(cost−0.4) − w_size·(size−0.4)                  ← bounded ±60%
L        = GreedyDual inflation (ages everything down over time)
retrieval_cost = gen_latency_ms + gen_cost_usd / latency_price     ← latency & $ unified
```

At `tilt = 0` this is exactly **GDSF**. The bandit only ever re-ranks
near-ties, so AACMS can match GDSF in the worst case and beat it whenever the
regime rewards a different blend.

---

## 4. Runtime adaptation — the LinUCB bandit (`engine/bandit.py`)

Once per epoch (~10 s of simulated time):

| step | what happens |
|---|---|
| **context** | 8 features: arrival rate, access-entropy, hit-rate trend, miss-cost pressure, cache pressure, eviction rate, ghost-hit rate, bias |
| **choose** | LinUCB picks the weight-preset ("personality") with the best predicted reward + uncertainty bonus |
| **apply** | that weight vector scores every object for the next epoch |
| **learn** | reward = `hit_rate − 0.35·norm_latency − 0.45·norm_cost` updates the chosen arm |

Five personalities: `balanced`, `cost_first`, `recency_first`,
`frequency_first`, `memory_saver`. In the spike scenario the arm visibly flips
to `cost_first`; in a popularity shift it leans `recency_first`.

---

## 5. Cost-benefit autoscaling (`engine/autoscaler.py`)

A **ghost list** keeps the key + size + regeneration-cost of recently evicted
objects (no data). If a ghost key is requested again, that is a miss a bigger
cache would have prevented.

```
benefit(grow +Δ) ≈ capturable_fraction × Σ ghost-hit regeneration $
cost(grow +Δ)    = RAM price of Δ bytes for one epoch
grow   if benefit > cost × 1.4      (and below the 3× ceiling)
shrink if fill < 60% and ~no evictions and no ghost hits for 2 epochs
```

So the cache expands during a genuine flash crowd and releases memory when the
crowd leaves — never "just in case".

---

## 6. Cost model (`common.CostConfig`, one source of truth)

| component | price |
|---|---|
| cache RAM | $0.12 / GB-hour (managed in-memory cache class) |
| origin regeneration | `gen_cost_usd` per miss, from the object catalog |
| user-visible latency | $2 × 10⁻⁶ per request-ms (business cost) |
| proactive refresh | `gen_cost_usd` (no latency charged — it's off the request path) |

Identical rules for every policy, so the benchmark is apples-to-apples.

---

## 7. Results (api profile, cache = 15% of working set)

| scenario | vs LRU | vs GDSF (best baseline) |
|---|---|---|
| steady | **−81%** cost | **−59%** |
| sudden spike | **−84%** | **−62%** |
| popularity shift | **−80%** | **−61%** |

Same engine, no retuning, also wins on the compute-heavy **recsys** profile.
Full numbers + charts: `results/REPORT_api.md`, `results/REPORT_recsys.md`.
