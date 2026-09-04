"""
Access predictor — "predict the future" step of the CACHE MIND loop.

Per key we keep an EWMA of the inter-access gap and its variance. From that:

    next_access_eta   ≈ last_gap_ewma           (when we expect the next hit)
    p_soon            = exp(-idle / eta)         (prob. it's accessed within ~one horizon)
    trend             = short_ewma / long_ewma   (>1 heating up, <1 cooling)
    confidence        = 1 / (1 + cv)             (low gap-variance ⇒ trust the estimate)

Cheap (a handful of floats per key), online, no training. It feeds three of the
value-score signals and drives PREFETCH.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(slots=True)
class _Track:
    last_t: float
    gap_ewma: float = 30.0
    gap_var: float = 900.0
    short_rate: float = 0.0        # EWMA hits/epoch, fast
    long_rate: float = 0.0        # EWMA hits/epoch, slow
    n: int = 1


class AccessPredictor:
    def __init__(self, a_gap: float = 0.25, a_short: float = 0.4, a_long: float = 0.05,
                 cap: int = 40_000):
        self.a_gap, self.a_short, self.a_long = a_gap, a_short, a_long
        self._cap = cap
        self._d: dict[str, _Track] = {}

    def observe(self, key: str, now: float) -> None:
        t = self._d.get(key)
        if t is None:
            if len(self._d) >= self._cap:
                # drop the coldest ~5%
                for k in sorted(self._d, key=lambda k: self._d[k].last_t)[: self._cap // 20]:
                    del self._d[k]
            self._d[key] = _Track(last_t=now)
            return
        gap = max(now - t.last_t, 1e-3)
        d = gap - t.gap_ewma
        t.gap_ewma += self.a_gap * d
        t.gap_var += self.a_gap * (d * d - t.gap_var)
        t.last_t = now
        t.n += 1

    def epoch_decay(self, epoch_hits: dict[str, int]) -> None:
        """Roll the short/long access-rate EWMAs once per epoch."""
        for key, tr in self._d.items():
            h = epoch_hits.get(key, 0)
            tr.short_rate += self.a_short * (h - tr.short_rate)
            tr.long_rate += self.a_long * (h - tr.long_rate)

    # -- queries ------------------------------------------------------- #
    def p_soon(self, key: str, now: float) -> float:
        t = self._d.get(key)
        if t is None or t.n < 2:
            return 0.0
        idle = max(now - t.last_t, 0.0)
        return math.exp(-idle / max(t.gap_ewma, 1.0))

    def trend(self, key: str) -> float:
        t = self._d.get(key)
        if t is None:
            return 1.0
        return (t.short_rate + 1e-6) / (t.long_rate + 1e-6)

    def confidence(self, key: str) -> float:
        t = self._d.get(key)
        if t is None or t.n < 3:
            return 0.0
        cv = math.sqrt(max(t.gap_var, 0.0)) / max(t.gap_ewma, 1.0)
        return 1.0 / (1.0 + cv)

    def expected_hits(self, key: str, n_epochs: float) -> float:
        """Predicted accesses over the next `n_epochs` epochs."""
        t = self._d.get(key)
        if t is None:
            return 0.0
        base = t.short_rate * n_epochs
        conf = 0.4 + 0.6 * self.confidence(key)
        tr = min(max(self.trend(key), 0.5), 2.5)
        return max(base * conf * tr, 0.0)

    def eta(self, key: str, now: float) -> float:
        t = self._d.get(key)
        if t is None:
            return float("inf")
        return max(t.last_t + t.gap_ewma - now, 0.0)

    def hot_candidates(self, resident: set[str], now: float, k: int,
                       pool: list[str] | None = None) -> list[str]:
        """
        Non-resident keys most likely to be hit very soon — the PREFETCH list.
        `pool` (recently-seen keys) bounds the scan; falls back to all tracks.
        """
        keys = pool if pool is not None else self._d.keys()
        scored = []
        for key in keys:
            t = self._d.get(key)
            if t is None or t.n < 3 or key in resident:
                continue
            ps = self.p_soon(key, now) * self.confidence(key) * min(self.trend(key), 3.0)
            if ps > 0.12:
                scored.append((ps, key))
        scored.sort(reverse=True)
        return [key for _, key in scored[:k]]
