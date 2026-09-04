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

| policy | hit rate | p95 latency | cost | vs GDSF | vs GDSF-tiered |
|---|---|---|---|---|---|
| GDSF (single-tier, best classical) | 0.75 | 24 ms | — | — | |
| **GDSF-tiered** (same L1/L2/L3, dumb placement) | 0.95 | ~20 ms | | **−45 %** | — |
| **CACHE MIND** (smart placement + prefetch + adapt) | 0.95 | **~6 ms** | | **−60 %** | **−18 %** |

Two separable wins:
- **Tiering** turns evictions into warm L2/L3 hits instead of origin misses —
  −45 % cost vs single-tier GDSF.
- **Smart refresh** — serve-stale-on-purpose for low-drift low-value entries +
  proactive background refresh of hot ones, instead of a blocking refetch on
  every stale hit — a further −13 to −28 % vs `GDSF-tiered` on the *same*
  hardware.
- **Value-aware placement** keeps the genuinely hot objects in fast L1, so p95
  latency is a flat **6 ms** vs 9–22 ms for dumb tiering.

The bandit, autoscaler, prefetch and compression are within a few percent on
these Zipf workloads — kept for runtime adaptivity and robustness (full
per-feature ablation in `results/ABLATION_api.md`).

Beats every baseline on **four** scenarios: steady, sudden spike, gradual
popularity shift, and an adversarial regime-flip. Full numbers +
per-feature ablation: `results/REPORT_api.md`, `results/ABLATION_api.md`.

## Algorithm — hybrid

- **Heuristic core**: GreedyDual-Size-Frequency value magnitude (`freq · cost /
  size`), online-normalised. At zero tilt the ranking *is* GDSF — a provable
  safety floor.
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
