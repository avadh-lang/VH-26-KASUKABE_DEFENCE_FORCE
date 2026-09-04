# CACHE MIND — 5-minute demo script

Setup before you present:
```bash
. .venv/bin/activate
uvicorn api.main:app --port 8000      # then open http://localhost:8000
```
Dashboard scenario selector on **steady**, speed **8x**, policies LRU / LFU / GDSF / CACHE MIND.

---

### 0:00 — The problem (30s)

> "LRU and LFU treat every cached object the same — they only look at *when* or
> *how often* it was used. But a rarely-used object that takes 2 seconds and a
> cent to rebuild from an external API is worth far more than a popular one that
> recomputes for free. At scale that blind spot means you either over-provision
> cache and burn money, or under-provision and thrash on every traffic spike."

### 0:30 — What we built (30s)

> "CACHE MIND scores every object by a **multi-factor value** — access pattern, size,
> and the real latency-plus-dollar cost to regenerate it. A contextual bandit
> re-tunes that score **at runtime**, and a cost-benefit controller grows the
> cache only when it pays for itself."

### 1:00 — Live: steady state (60s)

Press **Start**. Let ~15 epochs accumulate.

- Point at **Cumulative cost** chart: "Same workload, same cache size, same cost
  model for all four. CACHE MIND — the red line — is already the cheapest."
- Point at the top-left card: "**~55% cheaper than LRU**, and it's beating GDSF,
  the strongest classic cost-aware policy, by ~12% at the *same* capacity."
- Point at **Detected regime = steady** and the **bandit weights** panel: "The
  engine is adapting these weights every epoch, not running a fixed formula."

### 2:00 — Live: the traffic spike (90s)

Press **⚡ Inject traffic spike**.

- "A flash crowd just hit — cold objects suddenly went hot, 3× the request rate."
- Watch the **regime** card flip to `spike` and the **bandit arm** switch to
  `cost_first`. "It noticed and re-weighted toward protecting expensive objects."
- **Hit rate** chart: "LRU and LFU dip — they're evicting the working set to make
  room for one-hit-wonders. CACHE MIND's admission control refuses to cache low-value
  objects, so its hit rate holds."
- **Cost** chart: "The gap *widens* during the spike — that's the whole point,
  CACHE MIND is most valuable exactly when things go wrong."
- **CACHE MIND cache capacity** chart: "And the autoscaler added capacity here —
  because the ghost list showed those misses were worth more than the RAM."

### 3:30 — The benchmark (60s)

Open `results/REPORT_api.md` (and `results/figs/api_spike.png`).

> "Three scenarios — steady, spike, gradual popularity shift — against LRU, LFU,
> GDS and GDSF. CACHE MIND cuts cost 59–62% versus the best baseline. Note LFU
> actually gets *worse* than LRU under a popularity shift — stale frequency
> counts — while CACHE MIND stays flat. And this is the same engine, no retuning, on
> both a read-heavy API workload and a compute-heavy recommendation workload."

### 4:30 — Close (30s)

> "So: a value model instead of a single metric, adaptation instead of a fixed
> policy, and scaling driven by a cost-benefit test instead of a guess.
> Everything you saw is live — the dashboard is driving the real engine, not a
> recording."

---

## Likely jury questions

**"Isn't the autoscaler win just from a bigger cache?"**
No — the fixed-capacity number (`CM-fixed` row) still beats every baseline at
*identical* size. The autoscaler is a separate, additional gain, and it's
bounded (3× ceiling) and reversible (it shrinks on the diurnal scenario).

**"Why a bandit and not just a heuristic / full ML?"**
A heuristic can't respond to regime change; full ML needs training data we don't
have at cold start and is hard to justify live. LinUCB is a standard, explainable
online algorithm — you can watch it explore and converge on the dashboard.

**"How is retrieval cost known in production?"**
Measured. The cache already sees every origin fetch — it records actual latency
and (from billing metadata / config) the dollar cost, and feeds an EWMA back
into the score normalizers.

**"Overhead?"**
Eviction is sampled (Redis-style), O(1) amortised. The bandit is a 8×8 linear
solve once per epoch. The value score is ~10 float ops per candidate.

**"What if the workload never changes?"**
Then CACHE MIND ≈ GDSF by construction (the bandit tilt goes to ~0). You lose nothing;
you gain the adaptivity for free.
