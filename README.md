# AACMS — Adaptive, Application-Aware Cache Management System

**VH-26 · KASUKABE DEFENCE FORCE** — VCET Hackathon 2026
Domain: Application Scaling · PS: Adaptive, Application-Aware Cache Management System

Traditional caches (LRU/LFU) only look at *when* / *how often* an object was
used. AACMS scores every object by a **multi-factor value model** — access
pattern, size, and the real cost (latency + $) to regenerate it — and adapts
that model **at runtime** with a contextual bandit. It also decides, on a
cost-benefit basis, **when growing the cache is actually worth it**.

## Results (vs. the strongest conventional baseline, GDSF)

| Scenario | AACMS (fixed capacity) | AACMS (+ autoscaler) |
|---|---|---|
| steady load        | **-12% cost** | **-52% cost** |
| sudden spike        | **-11% cost** | **-56% cost** |
| popularity shift    | **-9% cost**  | **-53% cost** |

Same engine, no re-tuning, wins on both the read-heavy **api** profile and the
compute-heavy **recsys** profile. Full numbers: `python -m benchmark.runner`.

## Architecture

```
workload/   traffic generator — 2 app profiles, 5 scenarios (spike, shift, diurnal, …)
   │  (timestamp, ObjectSpec) stream
   ▼
baselines/  LRU · LFU · GDS · GDSF          engine/  AACMS
                     │                          ├─ scoring.py     multi-factor value score
                     │                          ├─ bandit.py      LinUCB — adapts weights / epoch
                     ▼                          ├─ regime.py      workload-regime label
benchmark/  SimDriver — one clock, one        ├─ autoscaler.py  cost-benefit + ghost list
            cost model, per-epoch snapshots   └─ aacms.py       admission · eviction · refresh
   │
   ▼
api/  FastAPI + SSE  ──►  dashboard/  React live view (hit rate, $ saved, decisions)
```

Every module depends only on `common/interfaces.py` (the shared contract), so
the four workstreams build in parallel.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m benchmark.runner --profile api --scenarios steady spike popularity_shift
```

## Team

| Member | GitHub | Branch | Owns |
|---|---|---|---|
| Avadh Mehta | `avadh-lang` | `feat/avadh` | `engine/`, `common/`, integration |
| Priti Kangne | `pritikangne266-dev` | `feat/priti` | `workload/`, `baselines/` |
| Prathamesh | `Prathamesh-2803` | `feat/prathamesh` | `benchmark/` |
| Sahil Kadam | `SahilKadam-dev` | `feat/sahil` | `api/`, `dashboard/` |

Each member commits only inside their own folder(s) on their own branch; `main`
is integrated hourly by PR and always stays demo-ready.
