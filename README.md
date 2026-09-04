# CACHE MIND — an AI brain that sits above your cache

**VH-26 · KASUKABE DEFENCE FORCE** — VCET Hackathon 2026
Domain: *Application Scaling* · PS: *Adaptive, Application-Aware Cache Management System*

LRU and LFU rank cached objects by **access pattern alone**. They are blind to
**size** and to the real **latency + $ cost** to regenerate an object from the
origin. At scale that forces a lose-lose choice: over-provision expensive RAM,
or under-provision and thrash the backend on every traffic surge.

**CACHE MIND** turns the cache from a passive store into an autonomous
decision-maker. Every epoch it **observes → predicts → scores → decides →
executes → learns**, choosing per object between:

> **KEEP · PROMOTE · DEMOTE · PREFETCH · REFRESH · COMPRESS · EVICT · SCALE**

across a **multi-level cache** — L1 (RAM, fast, dear), L2 (Redis-class, warm,
cheap), L3 (cold store, slow, near-free). An object that falls out of L1
isn't evicted into a 900 ms / $0.005 origin miss — it's **demoted** to a 4 ms
warm hit.

## Results — `api` profile, L1 = 12 % of working set, identical cost model

| policy | hit rate | p95 latency | cost $ | vs GDSF |
|---|---|---|---|---|
| LRU | 0.77 | 426 ms | 148.1 | −120 % |
| LFU | 0.81 | 340 ms | 128.4 | −91 % |
| GDSF (best single-tier classical) | 0.77 | 23 ms | 67.3 | — |
| **GDSF-tiered** (same L1/L2/L3, dumb placement) | 0.99 | 14 ms | 39.4 | **+42 %** |
| **CACHE MIND** | 0.99 | **6 ms** | **19.8** | **+71 %** |

(spike / popularity-shift / regime-flip: **−74 / −73 / −82 %** vs GDSF.)

Two capabilities carry the win — the ablation (`results/ABLATION_api.md`) shows
each roughly **doubles total cost when removed**:

1. **Tiering** — overflow is demoted to a warm L2/L3 hit instead of evicted into
   a 900 ms origin miss.
2. **Smart refresh** — a stale hit is served immediately and the object is
   **refreshed in the background** next epoch, instead of a blocking refetch.

On the *same* L1/L2/L3 hardware, CACHE MIND still beats `GDSF-tiered` by **~50 %
cost** and keeps p95 latency at a flat **6 ms** (vs ~14–22 ms) by placing the
genuinely hot objects in fast L1. Cheapest at **every** L1 size (5–40 %).

The bandit, autoscaler, prefetch and compression are a few percent each on these
Zipf workloads — kept for runtime adaptivity and robustness under surges.

Beats every baseline on **four** scenarios: steady, sudden spike, gradual
popularity shift, and an adversarial regime-flip. Full numbers +
per-feature ablation: `results/REPORT_api.md`, `results/ABLATION_api.md`.

## Algorithm — hybrid

- **3-family value model**: `value = w_gdsf·GDSF + w_rec·REC + w_fresh·FRESH −
  w_size·SIZE + w_ml·ML`. GDSF (`freq · cost / size`, online-normalised) carries
  the magnitude; recency/freshness/size are heuristic refinements; ML is the
  learned access forecast. The bandit's `proven` arm ≈ classical GDSF — a
  provable safety floor.
- **Economic tier placement**: `net_value(o, tier) = expected_hits ·
  serve_saving(o, tier) − hold_cost(o, tier)`. Place each object where it earns
  the most; evict only when every tier loses money.
- **ML**: a LinUCB contextual bandit re-weights the score each epoch; a
  per-object access predictor (`p_soon`, `trend`, `confidence`) drives prefetch.
- **Cost-benefit autoscaler** with a ghost list: grow/shrink L1 only when the
  marginal miss saving beats the marginal RAM.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

pytest                                         # 32 tests
python -m benchmark.report --run --profile api # tables + charts → results/
python -m benchmark.studies ablation           # per-feature contribution
bash scripts/dev.sh                             # live dashboard → http://localhost:5173
```

## Architecture

```
workload/        (t, ObjectSpec) stream — 2 profiles, 6 scenarios
   │
   ▼
benchmark/  SimDriver — one clock, one cost model, per-epoch snapshots → SQLite
   │                              │
   ▼ drives                       ▼ drives
baselines/  LRU LFU GDS GDSF      engine/  CACHE MIND
            LRU-tiered            ├─ tiers.py     L1/L2/L3 store  (in common/tierstore.py)
            GDSF-tiered           ├─ predict.py   per-object access forecaster
                                  ├─ scoring.py   keep-worthiness + net-value-per-tier
                                  ├─ bandit.py    LinUCB weight adaptation
                                  ├─ autoscaler.py ghost-list cost-benefit
                                  └─ cachemind.py the 11-step epoch loop
   │
   ▼
api/  FastAPI + SSE live simulator  ──►  dashboard/  React — tiers, cost, latency, decisions
```

Every module imports only `common/interfaces.py`. Deep dives:
[`docs/PROJECT.md`](docs/PROJECT.md) · [`docs/architecture.md`](docs/architecture.md) ·
[`docs/originality.md`](docs/originality.md) · [`docs/data-design.md`](docs/data-design.md) ·
[`docs/demo-script.md`](docs/demo-script.md)

## Team

| Member | GitHub | Owns |
|---|---|---|
| Avadh Mehta | `avadh-lang` | `engine/`, `common/`, `tests/`, docs, integration |
| Priti Kangne | `pritikangne266-dev` | `workload/`, `baselines/` |
| Prathamesh | `Prathamesh-2803` | `benchmark/` |
| Sahil Kadam | `SahilKadam-dev` | `api/`, `dashboard/` |
