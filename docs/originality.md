# CACHE MIND — Originality: known vs. novel

We are explicit about what is prior art and what is our contribution. Every
primitive below is something a judge could look up; the value is in how they are
combined into one decision framework.

---

## Prior art we deliberately build on

| Primitive | Source | How we use it |
|---|---|---|
| **GreedyDual-Size** | Cao & Irani, *USENIX Symp. on Internet Tech.*, 1997 | the `L` inflation term + cost/size ratio |
| **GreedyDual-Size-Frequency** | Cherkasova, HP Labs TR, 1998 | the `freq · cost / size` magnitude — our score's "core" |
| **LinUCB contextual bandit** | Li, Chu, Langford, Schapire, *WWW*, 2010 | picks the weight preset each epoch from workload context |
| **Ghost / shadow lists** | Megiddo & Modha (ARC), *FAST*, 2003; Jiang & Zhang (LIRS), 2002 | recently-evicted keys kept for the autoscaler's payback test |
| **Admission control w/ a sketch** | Einziger, Friedman, Manes (TinyLFU), 2017 | the idea of gating admission; we gate on *value*, not frequency |
| **Sampled ("power of N") eviction** | Redis `maxmemory` policy, ~2015 | O(1) approximate eviction instead of a global scan |
| **Multi-level / demotion caches** | Wong & Wilkes 2002; CDN edge/mid tiers; DRAM→NVMe→disk | the L1/L2/L3 hierarchy and demote-instead-of-evict |
| **Prefetching / prediction** | classic prefetchers; ML admission (LRB, Song et al. 2020) | warm predicted-hot objects ahead of demand |

## What is ours

### 0. Placement economics across tiers — the headline
Multi-level caches exist, but their placement is almost always *positional*:
new objects enter L1, and eviction from level *k* demotes to *k+1*. CACHE MIND
places each object in the tier that **maximises its expected dollar value**:
```
serve_saving(o, tier) = max(gen_latency_ms − tier_latency, 0)·λ$  +  gen_cost_usd
net_value(o, tier)    = E[hits over horizon] · serve_saving(o, tier)
                        − tier_$per_GB_hr · size · horizon
best_tier(o) = argmax_tier net_value   (evict if every tier is negative)
```
Because origin latency dwarfs inter-tier latency, an expensive object is worth
holding even in cold L3 — but a cheap one that just fell out of L1 is *evicted*,
not demoted. Our tiered baselines (`LRU-tiered`, `GDSF-tiered`) implement the
positional approach for a controlled comparison; CACHE MIND beats them by
13–28 % cost and ~⅔ p95 latency on the *same* hardware.

### 1. GDSF core + a *bounded* adaptive tilt — with a provable floor
```
value(o) = L + w_core · GDSF_core(o) · (1 + tilt(o)),   tilt ∈ ±0.8
```
Existing adaptive caches either **switch whole policies** (ARC moves an
LRU/LFU balance point) or **learn a policy from scratch** (RL-cache, LeCaR).
We do neither: GDSF stays the magnitude term, and the bandit only applies a
bounded multiplicative re-ranking. Consequence — **at `tilt → 0`, CACHE MIND *is*
GDSF**, so it cannot be beaten by the strongest classical baseline; the
adaptation is pure upside. We have not seen this "safety-floor" construction
elsewhere.

### 2. One retrieval-cost signal for latency *and* money
`cost_ms_equiv = gen_latency_ms + gen_cost_usd / latency_price`. A dollar cost
is converted to "how many ms of user latency it's worth" using the same
business price the cost model charges, so a single `cost` term ranks a
$0.005 API object and a 900 ms compute object on one axis.

### 3. Admission by projected value + demand recycling
On a miss we build a hypothetical entry (frequency estimated from a decayed
demand sketch) and admit only if its value clears the weakest victim it would
displace. Rejected keys keep accumulating demand, so a genuinely popular
object is admitted on a later request — a one-hit scan never evicts the
working set. TinyLFU gates on frequency; we gate on the full value (which
includes cost and size).

### 4. Refresh as a first-class decision, priced
Most caches: staleness is binary (TTL expired → refetch on next request).
CACHE MIND computes
```
refresh_priority(o) = drift_risk(o) · reuse(o) / (1 + 50 · refresh_cost(o))
drift_risk = 1 - exp(-volatility · 4 · staleness)
```
and then, every epoch, **proactively background-refreshes** the top-k hot,
near-stale, drift-prone entries (charged origin $, no client latency), while a
stale hit on a *low* refresh_priority entry is **served stale on purpose** to
save the regeneration cost. Retain / evict / refresh become one value
decision, which is exactly what the PS asks for.

### 5. Autoscaler as an explicit ROI test
Ghost lists are normally used to *tune a policy*. We use them to *size the
cache*:
```
benefit(+Δ) ≈ min(1, Δ / ghost_bytes) · Σ (ghost-hit regeneration $)
cost(+Δ)    = RAM_price · Δ · epoch_hours
grow  iff benefit > cost · 1.4
shrink iff fill < 60% ∧ evict_rate < 0.2% ∧ no ghost hits for 2 epochs
```
A literal payback test on capacity, symmetric, bounded by a 3× ceiling. This
is the PS's "scale only when the cost-benefit tradeoff justifies it".

### 6. A cheap online access predictor feeding placement *and* prefetch
Per key: EWMA of the inter-access gap + variance, and fast/slow rate EWMAs →
`p_soon`, `trend`, `confidence`, `expected_hits(n)`. It is a handful of floats
per key, no training, and it feeds three things at once: the `pred` signal in
the value score, the `E[hits]` term in the tier economics, and the PREFETCH
list (predicted-hot non-resident keys, warmed into L2 before they're asked
for). LRB and similar use a trained GBM for admission only; ours is untrained
and drives placement, prefetch and refresh together.

### 7. Fully online, zero training
No offline dataset, no pre-trained model. `ScoreRefs` EWMA-normalises every
signal from the live stream; the bandit starts uninformed and explores. It
runs correctly from a cold cache — which is what makes the live demo real.

---

## Implementation originality

- **All eight policies** — LRU, LFU, GDS, GDSF, LRU-tiered, GDSF-tiered,
  CACHE MIND, plus seven ablation variants — are implemented from scratch
  against a single `CachePolicy` interface. No `cachetools`, no
  `functools.lru_cache`, no RL library. `TieredStore` is ~120 lines, shared by
  the engine and the tiered baselines.
- The **workload generator, tiered cost model, SimDriver and SQLite result
  store** are ours — the benchmark is not a wrapper around an existing harness.
- **The "simulate every decision" idea, made tractable**: the live simulator
  runs the baseline policies *in parallel* on the same stream, so the dashboard
  shows real counterfactuals ("here's what LRU would have cost this epoch")
  instead of an intractable per-object lookahead.
- **Ablations are built in**: `CM-notier`, `CM-noprefetch`, `CM-nobandit`,
  `CM-noautoscale`, `CM-norefresh`, `CM-nocompress`, `CM-fixed` each isolate one
  capability's contribution.

## What we would cite as related work in a report

LeCaR (Vietri et al., HotStorage 2018) and CACHEUS (Rodriguez et al., FAST
2021) also use online learning (regret matching / bandits) for cache
replacement. Difference: they learn a blend of **LRU and LFU experts**; CACHE MIND
learns a blend over a **cost/size/frequency value model** and additionally
owns refresh and capacity — cost-awareness and autoscaling are out of scope
for those systems.
