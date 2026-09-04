"""
CACHE MIND value model.

Two questions, two calculations:

1. **How keep-worthy is this object?**  (ranking — who to demote/evict first)

       value(o) = L + w_core · CORE · (1 + tilt)

   `CORE` = freq · retrieval_cost / size  (the GreedyDual-Size-Frequency shape,
   online-normalised, softly capped). At tilt = 0 this is exactly GDSF, so the
   ranking can never be worse than the strongest classical policy. `tilt` is a
   bounded re-rank from six [0,1] modifier signals (recency, frequency,
   retrieval $, size, and a *prediction* term), each weighted by the bandit.

2. **Where should it live?**  (placement — L1 / L2 / L3 / evict)

       net_value(o, tier) = p_access · serve_saving(o, tier) − hold_cost(o, tier)

   A warm hit in *any* tier avoids the origin's regeneration latency and $, so
   `serve_saving` is large for expensive objects even from cold L3. `hold_cost`
   is that tier's byte price over the horizon. CACHE MIND places each object at
   the tier that maximises net_value, and evicts only when every tier loses money.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from common import CacheEntry, CostConfig, ObjectSpec, L1, L2, L3

WEIGHT_KEYS = ("core", "rec", "freq", "cost", "size", "pred")


def _unit(x: float) -> float:
    return x / (1.0 + x) if x > 0.0 else 0.0


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
        core = max(freq, 1) * cost_ms / max(spec.size_bytes / 1024.0, 1e-6)
        self.core_ref += a * (core - self.core_ref)

    def adapt_tau(self, mean_interarrival_s: float, reuse_gap_s: float) -> None:
        target = max(15.0, min(900.0, 2.5 * reuse_gap_s + 5.0 * mean_interarrival_s))
        self.tau_s += 0.1 * (target - self.tau_s)


def signals(entry: CacheEntry, now: float, refs: ScoreRefs, latency_usd_per_ms: float,
            pred: float = 0.0) -> dict[str, float]:
    cms = cost_ms_equiv(entry.spec, latency_usd_per_ms)
    idle = max(now - entry.last_access, 0.0)
    size_kb = max(entry.full_size_bytes / 1024.0, 1e-6)
    core_raw = entry.freq * cms / size_kb
    x = core_raw / max(refs.core_ref, 1e-9)
    return {
        "core": x / (1.0 + x / 25.0),
        "rec": math.exp(-idle / max(refs.tau_s, 1.0)),
        "freq": _unit(math.log1p(entry.freq) / math.log1p(max(refs.freq_ref, 1.5))),
        "cost": _unit(cms / max(refs.cost_ref_ms, 1e-6)),
        "size": _unit(entry.full_size_bytes / max(refs.size_ref_b, 1.0)),
        "pred": max(0.0, min(1.0, pred)),
    }


_TILT_BASE = 0.4
_TILT_GAIN = 0.8


def value(entry: CacheEntry, now: float, weights: dict[str, float], refs: ScoreRefs,
          inflation_L: float, latency_usd_per_ms: float, pred: float = 0.0) -> float:
    s = signals(entry, now, refs, latency_usd_per_ms, pred)
    tilt = (
        weights["rec"] * (s["rec"] - _TILT_BASE)
        + weights["freq"] * (s["freq"] - _TILT_BASE)
        + weights["cost"] * (s["cost"] - _TILT_BASE)
        + weights.get("pred", 0.0) * (s["pred"] - _TILT_BASE)
        - weights["size"] * (s["size"] - _TILT_BASE)
    )
    factor = 1.0 + _TILT_GAIN * tilt
    factor = 0.2 if factor < 0.2 else 2.6 if factor > 2.6 else factor
    return inflation_L + weights["core"] * s["core"] * factor


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
    drift = 1.0 - math.exp(-entry.spec.volatility * (4.0 * st))
    s = signals(entry, now, refs, latency_usd_per_ms)
    reuse = 0.5 * s["freq"] + 0.5 * s["rec"]
    rc = entry.spec.gen_cost_usd + latency_usd_per_ms * entry.spec.gen_latency_ms
    return drift * reuse / (1.0 + 50.0 * rc)
