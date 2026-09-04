# AACMS — Adaptive, Application-Aware Cache Management System

**VH-26 · KASUKABE DEFENCE FORCE** — VCET Hackathon 2026
Domain: *Application Scaling* · PS: *Adaptive, Application-Aware Cache Management System*

---

LRU and LFU rank cached objects by **access pattern alone** — when, or how
often, something was used. But a rarely-touched object that costs 2 seconds and
a cent to rebuild from an external API is worth far more than a popular one that
recomputes for free. At scale that blind spot forces a bad choice:
over-provision cache "just in case" and burn money, or under-provision and
thrash the backend on every traffic spike.

**AACMS** scores every object by a **multi-factor value** — access pattern,
size, and the real latency-plus-dollar cost to regenerate it — then:

- **adapts that score at runtime** with a contextual bandit (no hardcoded weights),
- decides **retain / evict / refresh** in one framework,
- and **grows or shrinks the cache only when a cost-benefit test says it pays off.**

## Results — `api` profile, cache = 15% of working set

Same workload and cost model for every policy. `AACMS-fixed` = our value model
at fixed capacity (ablation); `AACMS` = full engine with adaptation + autoscaling.

| scenario | metric | LRU | LFU | GDS | GDSF | **AACMS-fixed** | **AACMS** |
|---|---|---|---|---|---|---|---|
| steady | cost $ | 148.1 | 128.4 | 72.6 | 67.3 | **60.4** | **27.9** |
| | vs GDSF | −120% | −91% | −8% | — | **+10%** | **+59%** |
| spike | cost $ | 198.0 | 167.8 | 92.3 | 84.3 | **75.7** | **32.3** |
| | vs GDSF | −135% | −99% | −10% | — | **+10%** | **+62%** |
| popularity shift | cost $ | 152.2 | 218.6 | 72.3 | 76.1 | **~72** | **~30** |
| | vs GDSF | −100% | −187% | +5% | — | **+5%** | **+61%** |

p95 latency: LRU/LFU ~350–440 ms, cost-aware policies ~18–24 ms.
Same engine wins on the compute-heavy `recsys` profile with no retuning
(`results/REPORT_recsys.md`). LFU actually gets **worse than LRU** under a
popularity shift (stale frequency counts) — AACMS stays flat.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

pytest                                   # 30 tests
python -m benchmark.runner --profile api # reproduce the table above
python -m benchmark.report --profile api # charts + markdown into results/

bash scripts/dev.sh                       # live dashboard at http://localhost:5173
```

## Architecture

```
workload/   traffic generator — 2 app profiles, 5 scenarios (spike, shift, diurnal, …)
   │  (timestamp, ObjectSpec) stream
   ▼
baselines/  LRU · LFU · GDS · GDSF          engine/  AACMS
                     │                          ├─ scoring.py     multi-factor value score
                     │                          ├─ bandit.py      LinUCB — adapts weights every epoch
                     ▼                          ├─ regime.py      workload-regime label
benchmark/  SimDriver — one clock, one         ├─ autoscaler.py  cost-benefit + ghost list
            cost model, per-epoch snapshots    └─ aacms.py       admission · eviction · refresh
   │                → results/aacms.db (SQLite)
   ▼
api/  FastAPI + SSE live simulator  ──►  dashboard/  React — hit rate, $ saved, decisions, spike button
```

Every module depends only on `common/interfaces.py` (the shared contract), so
the four workstreams build in parallel. Full write-ups:

- [`docs/architecture.md`](docs/architecture.md) — component map + the decision engine
- [`docs/data-design.md`](docs/data-design.md) — input model, derived signals, output schema
- [`docs/originality.md`](docs/originality.md) — prior art vs. our contributions
- [`docs/demo-script.md`](docs/demo-script.md) — the 5-minute demo

## Team

| Member | GitHub | Owns |
|---|---|---|
| Avadh Mehta | `avadh-lang` | `engine/`, `common/`, integration, docs |
| Priti Kangne | `pritikangne266-dev` | `workload/`, `baselines/` |
| Prathamesh | `Prathamesh-2803` | `benchmark/` |
| Sahil Kadam | `SahilKadam-dev` | `api/`, `dashboard/` |
