"""
CACHE MIND value model — an explicit three-family hybrid.

1. **How keep-worthy is this object?**  (ranking — who to demote/evict first)

       value(o) = L
                + w_gdsf  · GDSF(o)     ← proven cost-aware heuristic (Cherkasova '98)
                + w_rec   · RECENCY(o)  ┐
                + w_fresh · FRESH(o)    ├ hand-designed heuristics GDSF ignores
                − w_size  · SIZE(o)     ┘
                + w_ml    · ML(o)       ← learned: predicted future access value

   No single family is structurally dominant — the six weights `w_*` are chosen
   every epoch by a LinUCB contextual bandit. Pick the "proven" personality and
   `value ≈ GDSF` (a safety floor you can fall back to); pick "predictive" and
   the forecast leads. GDSF is our *foundation and our benchmark*, not the whole
   model.

2. **Where should it live?**  (placement — L1 / L2 / L3 / evict)

       net_value(o, tier) = E[hits] · serve_saving(o, tier) − hold_cost(o, tier)

   `E[hits]` is the ML forecast; `serve_saving` is the cost heuristic (latency +
   $ avoided by a warm hit); `hold_cost` is that tier's byte price. Same hybrid,
   applied to placement instead of ranking.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from common import CacheEntry, CostConfig, ObjectSpec, L1, L2, L3

WEIGHT_KEYS = ("gdsf", "rec", "fresh", "size", "ml")
FAMILY = {"gdsf": "GDSF heuristic", "rec": "heuristic", "fresh": "heuristic",
          "size": "heuristic", "ml": "machine-learned"}


def _unit(x: float) -> float:
    return x / (1.0 + x) if x > 0.0 else 0.0


def _softcap(x: float, k: float) -> float:
    """Monotone, ~linear near 0, saturates toward k for large x."""
    return x / (1.0 + x / k) if x > 0.0 else 0.0


def cost_ms_equiv(spec: ObjectSpec, latency_usd_per_ms: float) -> float:
    """Retrieval latency + money cost as one 'ms-equivalent' number."""
    return spec.gen_latency_ms + spec.gen_cost_usd / max(latency_usd_per_ms, 1e-12)


@dataclass(slots=True)
class ScoreRefs:
    freq_ref: float = 4.0
    cost_ref_ms: float = 300.0
    size_ref_b: float = 50_000.0
    core_ref: float = 0.05
    tau_s: float = 120.0
    _alpha: float = 0.02

    def observe(self, spec: ObjectSpec, freq: int, cost_ms: float) -> None:
        a = self._alpha
        self.freq_ref += a * (max(freq, 1) - self.freq_ref)
        self.cost_ref_ms += a * (cost_ms - self.cost_ref_ms)
        self.size_ref_b += a * (spec.size_bytes - self.size_ref_b)
        # cost-per-byte only (no frequency) — so `gdsf_raw = freq · cost/size /
        # core_ref` keeps its full magnitude spread; a freq-weighted normaliser
        # would collapse it toward 1.
        core = cost_ms / max(spec.size_bytes / 1024.0, 1e-6)
        self.core_ref += a * (core - self.core_ref)

    def adapt_tau(self, mean_interarrival_s: float, reuse_gap_s: float) -> None:
        target = max(15.0, min(900.0, 2.5 * reuse_gap_s + 5.0 * mean_interarrival_s))
        self.tau_s += 0.1 * (target - self.tau_s)


def signals(entry: CacheEntry, now: float, refs: ScoreRefs, latency_usd_per_ms: float,
            ml: float = 0.0) -> dict[str, float]:
    """Every term normalised to roughly [0, 3] (GDSF) or [0, 1] (the rest)."""
    cms = cost_ms_equiv(entry.spec, latency_usd_per_ms)
    idle = max(now - entry.last_access, 0.0)
    size_kb = max(entry.full_size_bytes / 1024.0, 1e-6)

    recency = math.exp(-idle / max(refs.tau_s, 1.0))

    # --- GDSF family: freq · retrieval_cost / size, normalised by its running
    # mean and gently aged by idleness (GDS uses a rising inflation term for the
    # same effect — an object nobody touches must eventually sink).
    gdsf_raw = entry.freq * cms / size_kb / max(refs.core_ref, 1e-9)
    aging = 0.25 + 0.75 * math.exp(-idle / (3.0 * max(refs.tau_s, 1.0)))

    # --- heuristic family: things GDSF is blind to
    freshness = 1.0 - min(entry.staleness(now), 1.0)          # near TTL ⇒ worth less as-is
    size_pen = _unit(entry.full_size_bytes / max(refs.size_ref_b, 1.0))

    return {
        # left uncapped so it keeps the strong magnitude ordering that makes
        # GDSF good; the [0,1] heuristic/ML terms are refinements on top.
        "gdsf": gdsf_raw * aging,
        "rec": recency,
        "fresh": freshness,
        "size": size_pen,
        "ml": max(0.0, min(1.0, ml)),
    }


def value(entry: CacheEntry, now: float, weights: dict[str, float], refs: ScoreRefs,
          inflation_L: float, latency_usd_per_ms: float, ml: float = 0.0) -> float:
    """L + weighted sum of the GDSF term, the heuristic terms, and the ML term."""
    s = signals(entry, now, refs, latency_usd_per_ms, ml)
    return (
        inflation_L
        + weights["gdsf"] * s["gdsf"]
        + weights["rec"] * s["rec"]
        + weights["fresh"] * s["fresh"]
        + weights["ml"] * s["ml"]
        - weights["size"] * s["size"]
    )


# --------------------------------------------------------------------------- #
#  Tier placement — the economic calculation
# --------------------------------------------------------------------------- #
_SAVE_CACHE: dict[int, tuple[float, float, float]] = {}


def serve_saving(spec: ObjectSpec, tier: int, cfg: CostConfig) -> float:
    """$ avoided per hit by serving from `tier` instead of regenerating."""
    key = id(spec)
    trip = _SAVE_CACHE.get(key)
    if trip is None:
        trip = tuple(
            max(spec.gen_latency_ms - cfg.tiers[i].hit_latency_ms, 0.0) * cfg.latency_usd_per_ms
            + spec.gen_cost_usd
            for i in range(3)
        )
        if len(_SAVE_CACHE) > 200_000:
            _SAVE_CACHE.clear()
        _SAVE_CACHE[key] = trip
    return trip[tier - 1]


def hold_cost(entry: CacheEntry, tier: int, horizon_s: float, cfg: CostConfig) -> float:
    return cfg.memory_usd(entry.size_bytes, horizon_s, tier)


def net_value_at_tier(entry: CacheEntry, tier: int, p_access_epoch: float,
                      expected_hits: float, horizon_s: float, cfg: CostConfig) -> float:
    """
    Expected $ gain over `horizon_s` from holding `entry` at `tier`.
    expected_hits = predicted accesses over the horizon.
    """
    save = serve_saving(entry.spec, tier, cfg)
    return expected_hits * save - hold_cost(entry, tier, horizon_s, cfg)


def best_tier(entry: CacheEntry, expected_hits: float, horizon_s: float,
              cfg: CostConfig) -> tuple[int, float]:
    """Return (tier, net_value) for the most profitable tier, or (0, nv) to evict."""
    best_t, best_nv = 0, 0.0
    for t in (L1, L2, L3):
        nv = net_value_at_tier(entry, t, 0.0, expected_hits, horizon_s, cfg)
        if nv > best_nv:
            best_t, best_nv = t, nv
    return best_t, best_nv


def refresh_priority(entry: CacheEntry, now: float, refs: ScoreRefs,
                     latency_usd_per_ms: float) -> float:
    st = entry.staleness(now)
    if st < 0.55:
        return 0.0
    drift = 1.0 - math.exp(-entry.spec.volatility * (4.0 * min(st, 2.0)))
    s = signals(entry, now, refs, latency_usd_per_ms)
    reuse = 0.5 * min(s["gdsf"], 1.0) + 0.5 * s["rec"]
    rc = entry.spec.gen_cost_usd + latency_usd_per_ms * entry.spec.gen_latency_ms
    return drift * reuse / (1.0 + 50.0 * rc)
