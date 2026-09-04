# CACHE MIND — 5-minute demo script

Setup:
```bash
. .venv/bin/activate
bash scripts/dev.sh          # then open http://localhost:5173
```
Scenario **steady**, speed **8x**, policies LRU / GDSF / GDSF-tiered / CACHE MIND.

---

### 0:00 — The problem (30s)

> "LRU and LFU only look at *when* or *how often* an object was used. They can't
> see that a rarely-used object costing 2 seconds and a cent to rebuild from an
> API is worth far more than a popular one that's free to recompute. At scale
> you either over-provision expensive RAM, or under-provision and hammer the
> backend on every spike."

### 0:30 — What CACHE MIND is (40s)

> "CACHE MIND turns the cache into an autonomous decision-maker over a
> **multi-level cache** — L1 RAM, L2 Redis, L3 cold store. Every epoch it
> observes, predicts, scores, and decides per object: keep, promote, demote,
> prefetch, refresh, compress, evict, or scale. When something falls out of
> L1 it's **demoted**, not evicted — so the next hit is 4 milliseconds, not a
> 900-millisecond origin miss."

### 1:10 — Live: steady state (70s)

Press **Start**, let ~15 epochs build.

- **Cost chart**: "Same workload, same cost model. GDSF — single-tier — is the
  green line up top. The moment you add tiers, cost drops 40%. CACHE MIND, the
  red line, is lower still."
- Top-left card: "**~60% cheaper than LRU.**"
- **p95 latency card**: "6 milliseconds — versus 14 for dumb tiering and 23 for
  GDSF. CACHE MIND keeps the genuinely hot objects in L1, so even the 95th
  percentile request is a fast hit."
- **Cache tiers panel**: "You can watch objects distributed across L1/L2/L3.
  GDSF-tiered just dumps everything into L1 first; CACHE MIND places each object
  where its *net value* is highest."
- **Weights panel**: "These weights are being re-chosen every epoch by a
  contextual bandit — nothing here is hardcoded."

### 2:20 — Live: the traffic spike (90s)

Press **⚡ Inject traffic spike**.

- "A flash crowd — cold objects suddenly hot, 3× the request rate."
- **Regime card** flips to `spike`; **bandit arm** shifts (often to
  `cost_first` or `predict_first`).
- **Decision feed**: point at `prefetch` and `L2->L1` lines — "it's warming
  predicted-hot objects and promoting them ahead of demand."
- **Cost chart**: "The gap *widens* during the spike. LRU and GDSF are back to
  hitting the origin; CACHE MIND absorbs it in the warm tiers."

### 3:50 — The benchmark (50s)

Open `results/REPORT_api.md` and `results/ABLATION_api.md`.

> "Four scenarios — steady, spike, popularity shift, and an adversarial
> regime-flip — against LRU, LFU, GDS, GDSF and the tiered baselines. CACHE MIND
> is 58–71% cheaper than GDSF, and — critically — **13–28% cheaper than
> GDSF-tiered, which has the exact same L1/L2/L3 hardware.** That gap is pure
> placement intelligence. The ablation shows tiering is the biggest lever, then
> smart placement and prefetch, then the autoscaler and refresh."

### 4:40 — Close (20s)

> "A value model instead of a single metric. Placement economics instead of
> keep-or-drop. Prediction and prefetch instead of reacting. And it adapts at
> runtime — the dashboard is driving the real engine, live."

---

## Likely jury questions

**"Isn't this just a bigger cache?"** The `GDSF-tiered` baseline has identical
L1/L2/L3 sizes and the same cost model. CACHE MIND beats it by 13–28% cost and
~⅔ latency — that's placement, prefetch and adaptation, not capacity.

**"Heuristic, ML, or hybrid?"** Hybrid. GDSF heuristic core (provable floor),
LinUCB bandit + access predictor (ML), online-adaptive normalisers.

**"The 'simulate every decision' idea from our brief?"** We run shadow baseline
policies in parallel in the live sim — real counterfactuals — rather than an
intractable per-object lookahead search.

**"How would L2/L3 latency and price be known?"** They're deployment constants
(your Redis tier's p99, your object-store pricing). The engine reads them from
`CostConfig`.

**"Overhead?"** Sampled eviction (O(1) amortised), an 8×8 bandit solve per
epoch, memoised `serve_saving`, a bounded per-epoch move budget.
