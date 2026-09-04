"""
Multi-factor value score.

    value(entry) = L
                 + w_core * core        (GreedyDual-Size-Frequency term, log-scaled)
                 + w_rec  * recency      modifier   in [0,1]
                 + w_freq * frequency    modifier   in [0,1]
                 + w_cost * retrieval $  modifier   in [0,1]
                 - w_size * size penalty            in [0,1]

`core` carries the *magnitude* of "how much do we lose by dropping this"
(freq * retrieval-cost / size, exactly the GDSF shape) so at w = {core:1}
AACMS reduces to GDSF and can only improve from there. The [0,1] modifiers
let the bandit re-shape the ranking for the current regime (favour recency
during a popularity shift, favour size under memory pressure, ...) without
ever losing the cost/size backbone.

`L` is the GreedyDual inflation term carried by the cache (ages everything
down over time). All reference magnitudes adapt online via `ScoreRefs`, so
one weight vector works on both the "api" and "recsys" profiles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from common import CacheEntry, ObjectSpec

WEIGHT_KEYS = ("core", "rec", "freq", "cost", "size")


def _unit(x: float) -> float:
    """Monotonic squash into [0, 1)."""
    return x / (1.0 + x) if x > 0.0 else 0.0


def cost_ms_equiv(spec: ObjectSpec, latency_usd_per_ms: float) -> float:
    """Retrieval latency + money cost, expressed as one 'ms-equivalent' number."""
    money_as_ms = spec.gen_cost_usd / max(latency_usd_per_ms, 1e-12)
    return spec.gen_latency_ms + money_as_ms


@dataclass(slots=True)
class ScoreRefs:
    freq_ref: float = 4.0
    cost_ref_ms: float = 300.0
    size_ref_b: float = 50_000.0
    core_ref: float = 0.05          # typical freq*cms/size_kb
    tau_s: float = 120.0            # recency decay horizon (seconds)
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


def signals(entry: CacheEntry, now: float, refs: ScoreRefs, latency_usd_per_ms: float) -> dict[str, float]:
    cms = cost_ms_equiv(entry.spec, latency_usd_per_ms)
    idle = max(now - entry.last_access, 0.0)
    size_kb = max(entry.size_bytes / 1024.0, 1e-6)
    core_raw = entry.freq * cms / size_kb
    x = core_raw / max(refs.core_ref, 1e-9)
    return {
        # GDSF magnitude term, normalised, gently soft-capped (~saturates near 25).
        # keeps the full strength of the cost/size/frequency preference.
        "core": x / (1.0 + x / 25.0),
        # bounded [0,1] modifiers that *tilt* the core value for the current regime
        "rec": math.exp(-idle / max(refs.tau_s, 1.0)),
        "freq": _unit(math.log1p(entry.freq) / math.log1p(max(refs.freq_ref, 1.5))),
        "cost": _unit(cms / max(refs.cost_ref_ms, 1e-6)),
        "size": _unit(entry.size_bytes / max(refs.size_ref_b, 1.0)),
    }


_TILT_BASE = 0.4       # modifier value treated as "neutral"
_TILT_GAIN = 0.6       # max +-60% swing on the GDSF value


def value(
    entry: CacheEntry,
    now: float,
    weights: dict[str, float],
    refs: ScoreRefs,
    inflation_L: float,
    latency_usd_per_ms: float,
) -> float:
    """
    L + w_core * GDSF_core * (1 + tilt)

    where `tilt` is a bounded, bandit-weighted nudge from the [0,1] modifiers.
    At tilt = 0 this is exactly GDSF; the bandit only re-ranks near-ties.
    """
    s = signals(entry, now, refs, latency_usd_per_ms)
    tilt = (
        weights["rec"] * (s["rec"] - _TILT_BASE)
        + weights["freq"] * (s["freq"] - _TILT_BASE)
        + weights["cost"] * (s["cost"] - _TILT_BASE)
        - weights["size"] * (s["size"] - _TILT_BASE)
    )
    factor = 1.0 + _TILT_GAIN * tilt
    factor = 0.25 if factor < 0.25 else 2.0 if factor > 2.0 else factor
    return inflation_L + weights["core"] * s["core"] * factor


def refresh_priority(
    entry: CacheEntry, now: float, refs: ScoreRefs, latency_usd_per_ms: float
) -> float:
    """
    Worth of a *proactive* background refresh right now.

    high when:  near / past TTL  *  data likely drifted  *  likely read again soon
    divided by the $ cost of regenerating it.
    """
    st = entry.staleness(now)
    if st < 0.55:
        return 0.0
    drift = 1.0 - math.exp(-entry.spec.volatility * (4.0 * st))
    s = signals(entry, now, refs, latency_usd_per_ms)
    reuse = 0.5 * s["freq"] + 0.5 * s["rec"]
    refresh_cost = entry.spec.gen_cost_usd + latency_usd_per_ms * entry.spec.gen_latency_ms
    return drift * reuse / (1.0 + 50.0 * refresh_cost)
