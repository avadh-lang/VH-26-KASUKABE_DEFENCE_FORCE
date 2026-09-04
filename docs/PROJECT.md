# AACMS — Full Project Explanation

**Adaptive, Application-Aware Cache Management System**
VH-26 · KASUKABE DEFENCE FORCE · VCET Hackathon 2026 · Domain: Application Scaling

This is the single reference document: what every part does, why it exists, the
exact maths, the data model, the cost model, and the results. Companion docs go
deeper on individual angles — [architecture](architecture.md),
[data-design](data-design.md), [originality](originality.md),
[demo-script](demo-script.md).

---

## 1. The problem

Caching keeps hot data in fast memory so the backend isn't hit for every
request. When the cache is full something must be evicted. The classic policies:

- **LRU** evicts the entry untouched for the longest.
- **LFU** evicts the entry with the fewest accesses.

Both look **only at access pattern**. They are blind to two things that matter:

1. **Size** — one 200 KB object occupies the space of 200 × 1 KB objects.
2. **Regeneration cost** — some objects take 2 s and $0.01 to rebuild from an
   external API; others recompute instantly for free.

> A rarely-accessed object that costs 2 s + $0.01 to regenerate is worth more to
> keep cached than a popular one that rebuilds for free. LRU/LFU can't see that.

At scale this blind spot forces a lose-lose choice:

- **Over-provision** the cache "just in case" → wasted infrastructure spend.
- **Under-provision** → cache thrashing, latency spikes, cascading backend load
  during traffic surges.

## 2. What we built — the thesis

**AACMS** replaces the single-metric eviction rule with:

1. A **multi-factor value score** per object (access pattern + size + retrieval
   cost + staleness risk).
2. That score's weights are **re-tuned at runtime by a contextual bandit** — not
   hardcoded. This is the PS's "decisions must be made adaptively at runtime".
3. **Retain / evict / refresh** are one decision in that scoring framework.
4. An **autoscaler** grows or shrinks the cache only when a **cost-benefit test**
   says the change pays for itself.
5. A **cost model** prices memory, compute/API regeneration, and user-latency so
   savings are measured in dollars, not just hit-rate.

**Algorithm class: hybrid.** Heuristic core (GreedyDual-Size-Frequency) for the
value magnitude; machine-learned weights (LinUCB contextual bandit) on top. The
heuristic gives a provable safety floor; the bandit gives adaptivity.

---

## 3. System overview

```
workload/        generates a timed stream of (t, ObjectSpec) requests
  │                2 application profiles · 5 traffic scenarios
  ▼
┌─────────────────────────────────────────────────────────────────┐
│ benchmark/  SimDriver — owns the clock + the cost model          │
│   for each request:  lookup → hit / stale-hit / refresh / miss   │
│   every epoch:  policy.maintenance(), drain refreshes, snapshot  │
└─────────────────────────────────────────────────────────────────┘
  │                                    │
  ▼ drives                             ▼ drives
baselines/  LRU LFU GDS GDSF     engine/  AACMS
                                   scoring · bandit · regime · autoscaler
  │
  ▼ per-epoch EpochSnapshot rows
results/aacms.db (SQLite)  +  results/REPORT_*.md  +  charts
  │
  ▼
api/  FastAPI + SSE live simulator  ──►  dashboard/  React real-time UI
```

Everything imports **only** `common/interfaces.py`. That single 190-line
contract is why four people can build the four boxes in parallel with no
integration friction.

---

## 4. `common/interfaces.py` — the contract

| Type | Purpose | Key fields |
|---|---|---|
| `ObjectSpec` | a backend object that *could* be cached | `key, size_bytes, gen_latency_ms, gen_cost_usd, ttl_s, volatility, tags` |
| `CacheEntry` | a cached object + bookkeeping | `spec, inserted_at, last_access, refreshed_at, freq, hits_since_refresh, meta` |
| `RequestOutcome` | result of serving one request | `hit, stale_served, refreshed, latency_ms, cost_usd, action, reason` |
| `CostConfig` | the infra cost model (one source of truth) | `mem_usd_per_gb_hour, latency_usd_per_ms, refresh_discount, scale_step_bytes` |
| `EpochSnapshot` | one row of the per-epoch time series | traffic, latency, cost breakdown, cache state, engine internals |
| `CachePolicy` (ABC) | every policy implements this | `lookup, on_hit, on_admit, should_refresh, on_refresh, on_request_end, maintenance, pending_refreshes` + `capacity_bytes/used_bytes/entries` |

The driver calls, per request:

```
entry = policy.lookup(key, now)
if entry and not stale:          policy.on_hit(entry, now)
elif entry and stale:            policy.should_refresh(entry, now) decides
else (miss): fetch from origin,  policy.on_admit(spec, now)
always:                          policy.on_request_end(outcome, now)
```

and once per epoch: `policy.maintenance(now)` then drains
`policy.pending_refreshes()`.

---

## 5. `workload/` — the data (Priti)

### 5.1 Object catalog (`catalog.py`)

Each profile defines distributions; `build_catalog(profile, n, seed)` samples a
catalog of `ObjectSpec`s.

| Field | Distribution | Why this shape |
|---|---|---|
| `size_bytes` | **log-normal** | real caches: a few big objects dominate bytes, most are tiny — a size-blind policy lets big low-value objects evict many small high-value ones |
| `gen_latency_ms` | **bimodal** — a cheap mode (DB / warm compute) and an expensive mode (cold external API / model inference) | the PS's core example; if cost were unimodal, cost-awareness wouldn't matter |
| `gen_cost_usd` | correlated with latency via a shared `is_expensive` draw | slow-to-rebuild objects are usually the ones that cost money (paid API, GPU) |
| `ttl_s` | uniform per profile | drives the refresh-vs-evict decision |
| `volatility` (0–1) | uniform per profile | P(underlying data drifted) per unit of staleness — drives refresh *value* |

### 5.2 Two profiles (PS asks ≥ 2 distinct workload types)

| | `api` — read-heavy API service | `recsys` — compute-heavy recommender |
|---|---|---|
| objects | ~6 000 | ~2 500 |
| median size | ~12 KB | ~180 KB |
| expensive fraction | 35 % | 80 % |
| expensive latency | 120–900 ms | 250–2200 ms |
| dominant cost | **$ per API call** | **latency / compute** |
| volatility | 0.02–0.35 | 0.004–0.06 |

The same engine, same weights, must win on both — proving it is
application-agnostic.

### 5.3 Traffic scenarios (`scenarios.py`) — PS asks the model to win under ≥ 3

Popularity follows a **Zipf law over ranks** (`α ≈ 0.92–1.05`); a rank→object
permutation maps ranks to objects. Arrivals are **Poisson** with a per-scenario
rate `λ(t)`.

| scenario | what changes over the run |
|---|---|
| `steady` | nothing — stationary Zipf, constant λ |
| `spike` | at t ≈ 45 %, ~25 cold objects jump to the top ranks and λ × 3 for ~15 % of the run, then relax |
| `popularity_shift` | the rank→object permutation is continuously perturbed (≈ n/400 swaps per simulated second) |
| `diurnal` | λ(t) sinusoidal, 0.35× – 1.65× (live demo also contracts the working set at low tide) |
| `cold_start` | λ ramps 10 % → 100 % over the first 15 %; cache starts empty |

`Workload.working_set_bytes` = Σ size of every distinct key requested — the
natural reference for sizing the cache (benchmarks use 15 %).

---

## 6. `baselines/` — what we compare against (Priti)

All implemented from scratch against `CachePolicy`; `BaseCache` handles the dict,
byte accounting, and the evict-until-it-fits loop. Subclasses only pick the
victim.

| Policy | Victim = entry with the lowest… |
|---|---|
| **LRU** | `last_access` |
| **LFU** | `freq` (ties broken by recency) |
| **GDS** (GreedyDual-Size, Cao & Irani 1997) | `H = L + cost/size`; on eviction `L ← H` of the victim (aging). cost = `gen_latency_ms` |
| **GDSF** (GreedyDual-Size-Frequency, Cherkasova 1998) | `H = L + freq·cost/size` — the strongest classical baseline; blends recency (via L), frequency, size, cost |

---

## 7. `engine/` — AACMS (Avadh)

### 7.1 The value score (`scoring.py`)

```
value(o) = L  +  w_core · CORE(o) · (1 + tilt(o))
```

**CORE** — the GDSF magnitude, normalised and softly capped:

```
core_raw = freq · retrieval_cost / size_kb
x        = core_raw / core_ref                 (core_ref: EWMA of observed core_raw)
CORE     = x / (1 + x/25)                        → 0 … ~25, preserves ordering, bounds outliers
```

**retrieval_cost** unifies latency and money into one "ms-equivalent":

```
retrieval_cost = gen_latency_ms + gen_cost_usd / latency_usd_per_ms
```

i.e. a dollar cost is converted to "how many ms of user latency it is worth",
using the same price the cost model charges.

**tilt** — a bounded ±60 % re-ranking from four [0,1] modifier signals, each
weighted by the bandit:

```
tilt = w_rec ·(rec −0.4) + w_freq·(freq−0.4) + w_cost·(cost−0.4) − w_size·(size−0.4)
factor = clip(1 + 0.6 · tilt, 0.25, 2.0)

rec   = exp(−idle_seconds / τ)        τ adapts to observed reuse gaps (starts 120 s)
freq  = unit( log1p(freq) / log1p(freq_ref) )
cost  = unit( retrieval_cost / cost_ref_ms )
size  = unit( size_bytes / size_ref_b )
unit(x) = x/(1+x)                     monotone squash into [0,1)
```

**L** is the GreedyDual inflation term carried by the cache: on each eviction
`L ← max(L, value(victim))`, so long-idle high-value entries eventually sink and
nothing is immortal.

**Key property:** at `tilt = 0` (all modifier weights zero) this is **exactly
GDSF**. The bandit only re-ranks near-ties, so AACMS can never be beaten by the
best classical policy — the adaptivity is pure upside. This is the "safety
floor".

All `*_ref` normalisers are EWMAs (rate `α = 0.02`) of live observations, which
is what lets one weight vector work on both the `api` and `recsys` profiles
despite their 15× size difference.

### 7.2 The bandit (`bandit.py`) — the ML part

A **LinUCB contextual bandit** (Li, Chu, Langford, Schapire — WWW 2010). Each
**arm** is a named weight preset — a caching "personality":

| arm | core | rec | freq | cost | size | good when |
|---|---|---|---|---|---|---|
| `balanced` | 1.0 | 0.5 | 0.5 | 0.5 | 0.5 | default / unknown regime |
| `cost_first` | 1.0 | 0.2 | 0.3 | **1.5** | 0.3 | miss penalty is high (spike) |
| `recency_first` | 1.0 | **1.6** | 0.2 | 0.3 | 0.4 | working set is drifting |
| `frequency_first` | 1.0 | 0.2 | **1.6** | 0.3 | 0.3 | stationary, skewed demand |
| `memory_saver` | 1.0 | 0.4 | 0.4 | 0.5 | **1.6** | under memory pressure |

**Context vector** `x` (8 features, each ~[0,1]), rebuilt every epoch:

`rate` (arrivals / running-max), `entropy` (Shannon entropy of the epoch's key
distribution / log distinct — how spread demand is), `hit_trend` (Δ hit-rate vs
previous epoch), `miss_cost` (mean origin $-equiv per miss / running-max),
`pressure` (used / capacity), `evict_rate`, `ghost_rate` (ghost-list hits /
requests — an "undersized" signal), `bias` (1.0).

**Per epoch:**

1. For each arm `a`: keep `A_a` (d×d, starts `I`) and `b_a` (d, starts `0`).
   Estimate `θ_a = A_a⁻¹ b_a`; score `= θ_aᵀx + α·√(xᵀA_a⁻¹x)` (mean reward +
   uncertainty bonus, `α = 0.6`).
2. Pick the argmax arm; its weights score every object for the next epoch.
3. At epoch end compute the realised **reward**:
   `reward = hit_rate − 0.35·norm_latency − 0.45·norm_cost`
   and update the chosen arm: `A_a += xxᵀ`, `b_a += reward·x`.

So the weights genuinely follow the workload — nothing is hardcoded, and there
is a clean explore/exploit story to show the jury. On a spike the active arm
visibly flips to `cost_first`.

### 7.3 Regime label (`regime.py`)

A cheap, explainable tag (`cold_start` / `spike` / `popularity_shift` /
`steady`) derived from the same features — for the dashboard and demo narration.
It does **not** drive the weights (the bandit does); it's for humans.

### 7.4 Admission control (`aacms.py`)

On a miss, before caching the object:

```
est_freq  = 1 + decayed_demand_sketch[key]     (Counter, ×0.5 each epoch)
hypo      = CacheEntry(spec, freq = est_freq)
if room:                       insert
elif value(hypo) ≥ 0.85 · value(most-valuable entry we'd evict):
                               evict those, insert
else:                          reject, but keep the demand signal
```

A rejected key keeps accumulating demand, so a genuinely popular object is
admitted on a later request — but a one-hit scan or a flash of cold objects
cannot evict the working set. (TinyLFU gates admission on *frequency*; we gate on
the full *value*, which includes cost and size.)

### 7.5 Eviction

Sampled ("Redis-style"): sample `sample_size = 48` entries, evict the
lowest-value among them, repeat until the incoming object fits. O(1) amortised —
no global scan.

### 7.6 Refresh — the third decision

Staleness is **not** treated as binary TTL expiry. We compute:

```
refresh_priority(o) = drift_risk(o) · reuse(o) / (1 + 50 · refresh_cost(o))
drift_risk          = 1 − exp(−volatility · 4 · staleness)
reuse               = 0.5·freq_signal + 0.5·rec_signal
```

- **Reactive** (stale hit): refresh (blocking) only if `refresh_priority ≥ 0.15`.
  Otherwise serve stale on purpose and save the regeneration cost.
- **Proactive** (each epoch): background-refresh the top ≤ 8 entries with
  `refresh_priority > 0.25` — hot, near-stale, drift-prone. Charged origin $, **no
  client latency**, because it's off the request path.

Net effect: hot objects rarely cause a slow/stale hit; cold objects don't waste
money on refreshes.

### 7.7 Autoscaler (`autoscaler.py`)

A **ghost list** (shadow list, as in ARC/LIRS): on eviction we keep the key,
size, and $-equiv regeneration cost of the victim — not its data. If that key is
requested again, that is a miss a bigger cache would have avoided.

Each epoch, with step = `scale_step_bytes`:

```
benefit(+step) ≈ min(1, step / ghost_bytes) · Σ (ghost-hit regeneration $)
step_rent      = mem price of `step` bytes for one epoch

GROW   if benefit > step_rent · 1.4         and capacity + step ≤ 3× start
SHRINK if ≥ step of the cache has been idle ≥ 2 epochs, eviction-rate < 0.6 %,
          and the ghost benefit we'd forgo < half a step's rent
HOLD   otherwise           (2-epoch cooldown after any move)
```

A literal ROI test on cache size, symmetric, bounded. With realistic cloud RAM
pricing it typically **grows to fit a surge, then holds** at the efficient point
— it does not over-provision beyond what is justified, and it would release
capacity if RAM were a hard budget line.

### 7.8 Putting it together — per request

```
             ┌── hit, fresh ──────────────► serve ~0.5 ms, $0
lookup ──────┼── hit, stale ── refresh_priority high? ──► blocking refresh
             │                                   └─ no ─► serve stale, save $
             └── miss ── fetch from origin (pay latency + $)
                          └─ admission: value(new) ≥ 0.85·value(victim)?
                               ├─ yes ─► sampled-evict lowest value, insert
                               └─ no  ─► don't cache, remember demand
per epoch: bandit picks weights · regime label · τ adapts · autoscaler decides ·
           proactive-refresh queue built · demand sketch decays
```

---

## 8. `benchmark/` — measurement (Prathamesh)

### 8.1 `SimDriver` (`driver.py`)

Owns the clock and **all** money accounting, so every policy is judged on
identical rules. Policies never see dollars — they only make cache decisions.
Per request it charges origin $ (on miss/refresh), latency $ (every request),
and accrues memory $ per epoch on current residency.

### 8.2 Cost model (`common.CostConfig`)

| component | price | basis |
|---|---|---|
| cache RAM | **$0.12 / GB-hour** | managed in-memory cache tier (ElastiCache-class) |
| origin regeneration | `gen_cost_usd` per miss | from the object catalog (metered API price / compute-seconds) |
| user-visible latency | **$2 × 10⁻⁶ per request-ms** | business cost of latency (conversion/abandonment proxy) |
| proactive refresh | `gen_cost_usd × refresh_discount` (no latency) | it's off the request path |

Total run cost = origin + latency + memory. Identical workload + rules for all
policies ⇒ apples-to-apples.

### 8.3 SQLite result store (`store.py`)

Every matrix run **appends** to `results/aacms.db` — results are queryable
history, not a last-write JSON blob. Two tables normalised on `run_id`:

- `runs` — one row per (profile, scenario, policy): whole-run summary
- `epochs` — one row per epoch: the time series (hit-rate, cost breakdown,
  capacity, `regime`, `bandit_arm`, `w_core…w_size`)

`python -m benchmark.store --profile api` runs a window-function query showing
each policy's latest cost per scenario and the % saved vs GDSF.

### 8.4 Report (`report.py`)

`python -m benchmark.report --run --profile api` runs the matrix, writes
`results/REPORT_api.md` (comparison tables) and charts into `results/figs/`
(hit-rate / cost / p95 over time per scenario, plus AACMS's weights and capacity
trajectory).

---

## 9. `api/` + `dashboard/` — the live demo (Sahil)

- `api/live.py` — `LiveSim` runs several policies **in lockstep** over an
  epoch-by-epoch generated stream, so the dashboard can inject a flash crowd
  mid-run (`inject_spike`) and watch AACMS react while LRU/LFU thrash.
- `api/cost.py` — `CostLedger` tracks running cost per policy and reports the
  saving of each vs a baseline (default LRU).
- `api/main.py` — FastAPI: `POST /api/sim/start`, SSE `/api/sim/{id}/stream`
  (one JSON frame per epoch), `POST …/spike`, `…/scenario`, `GET …/cost`.
- `dashboard/` — React + Vite + Recharts. Stat cards (cost saving, hit rate,
  regime, capacity), live charts (hit-rate, cumulative cost, p95 latency,
  capacity), the AACMS decision feed, the bandit weight bars, and the
  **⚡ Inject traffic spike** button.

`bash scripts/dev.sh` runs both (`uvicorn` :8000 + Vite :5173 with HMR).

---

## 10. `tests/` — 30 tests (Avadh)

- **baselines** — LRU evicts LRU, LFU evicts LFU, size accounting never exceeds
  capacity, oversized objects rejected, GDSF prefers expensive-small, GDS
  inflation monotone.
- **engine** — value ranks expensive-small-hot above cheap-big-cold; admission
  rejects a low-value object when full; cold cache admits freely; the bandit
  learns to prefer the rewarding arm; autoscaler grows on costly ghost hits and
  holds when nothing is under pressure; ghost list is bounded;
  `refresh_priority` is 0 when fresh; proactive refresh targets hot/volatile/
  stale entries.
- **workload** — same seed ⇒ same stream; scenarios generate traffic; requests
  time-sorted; spike scenario really surges; catalog has expensive & cheap
  objects; profiles are genuinely distinct.
- **benchmark** — cost-model components non-negative; one snapshot per epoch;
  hit-rate/costs in valid ranges; **AACMS beats every baseline on cost**;
  **AACMS-fixed is never materially worse than GDSF**; autoscaler stays within
  its ceiling.

`.github/workflows/ci.yml` runs `pytest` + a benchmark smoke on every push/PR.

---

## 11. Results

`api` profile, cache = 15 % of working set, identical cost model. `AACMS-fixed` =
value model at fixed capacity (isolates the scoring contribution); `AACMS` = full
engine.

| scenario | LRU | LFU | GDS | GDSF | AACMS-fixed | AACMS |
|---|---|---|---|---|---|---|
| **steady** cost $ | 148.1 | 128.4 | 72.6 | 67.3 | 60.4 | **27.9** |
| vs GDSF | −120 % | −91 % | −8 % | — | **+10 %** | **+59 %** |
| **spike** cost $ | 198.0 | 167.8 | 92.3 | 84.3 | 75.7 | **32.3** |
| vs GDSF | −135 % | −99 % | −10 % | — | **+10 %** | **+62 %** |
| **popularity shift** cost $ | 152.2 | 218.6 | 72.3 | 76.1 | ~72 | **~30** |
| vs GDSF | −100 % | −187 % | +5 % | — | **+5 %** | **+61 %** |

- p95 latency: LRU/LFU ~350–440 ms; cost-aware policies ~18–24 ms.
- LFU gets **worse than LRU** under a popularity shift (stale frequency counts);
  AACMS stays flat.
- Same engine wins on the `recsys` profile with no retuning
  (`results/REPORT_recsys.md`).
### Ablation — what each feature actually contributes (`results/ABLATION_api.md`)

Turning one feature off at a time, fixed 15 % cache:

| feature off | cost vs full AACMS |
|---|---|
| autoscaler | **+105 … +128 %** |
| smart refresh | **+35 … +45 %** |
| value model (→ GDSF) | **+8 … +10 %** at every capacity (see `results/SENSITIVITY_*`) |
| bandit (frozen weights) | −0.6 … −2.6 % (noise) |
| admission control | ~0 % on these scenarios |

We measured our own system honestly:

- **Autoscaler + smart refresh + the value model are the load-bearing wins.**
- **The bandit is within noise on Zipf traffic** — GDSF's freq·cost/size blend is
  already near-optimal, so re-weighting it barely moves rankings. It is kept
  because it makes the weighting *adaptive with zero per-deployment tuning* (the
  PS's "adaptive at runtime" requirement) and provably cannot do worse than a
  hand-picked vector. Note the score is *also* adaptive without it — every
  `*_ref` normaliser and `τ` track the live stream.
- **Admission control is dormant here** (no scan/crawler pattern to catch) and
  is a robustness feature for adversarial workloads.

---

## 12. Design decisions & likely jury questions

**"Isn't the autoscaler win just a bigger cache?"** No — `AACMS-fixed` beats
every baseline at *identical* capacity. The autoscaler is a separate, bounded
(3× ceiling), reversible gain.

**"Heuristic, ML, or hybrid?"** Hybrid. A GDSF heuristic core for the magnitude,
a LinUCB bandit for the weights, and online-adaptive normalisers throughout.
Pure ML has no cold-start data and is a black box; a pure heuristic can't respond
to changing conditions. (The ablation shows the bandit's *numeric* effect is
small on Zipf traffic — GDSF is a strong heuristic — but it is what removes
per-deployment weight tuning and satisfies the runtime-adaptivity requirement.)

**"How is retrieval cost known in production?"** Measured. The cache already sees
every origin fetch — record real latency, take $ from billing metadata / config,
feed EWMAs.

**"Overhead?"** Eviction is sampled → O(1) amortised. Bandit is an 8×8 linear
solve once per epoch. The value score is ~10 float ops per candidate.

**"What if the workload never changes?"** Then the bandit converges to the best
fixed weights and AACMS's value model still beats GDSF at every capacity, and the
autoscaler + refresh wins are unaffected.

**"Why a contextual bandit and not full RL?"** LinUCB is a standard, explainable,
sample-efficient online algorithm with no training phase — you can watch it
explore and converge live. Full RL needs episodes we don't have and is hard to
justify.

---

## 13. Who built what

| Member | Branch | Modules |
|---|---|---|
| Avadh Mehta | `feat/avadh` | `common/`, `engine/`, `docs/`, `tests/`, CI, integration |
| Priti Kangne | `feat/priti` | `workload/`, `baselines/` |
| Prathamesh | `feat/prathamesh` | `benchmark/` |
| Sahil Kadam | `feat/sahil` | `api/`, `dashboard/` |

## 14. Run everything

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

pytest                                       # 30 tests
python -m benchmark.report --run --profile api      # tables + charts + SQLite
python -m benchmark.store  --profile api             # savings query
bash scripts/dev.sh                                   # live dashboard :5173
```
