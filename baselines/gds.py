"""
GreedyDual-Size (Cao & Irani, 1997).

The first classic policy to factor *retrieval cost* and *size* into eviction,
which is exactly what this PS asks us to beat. Each entry carries a priority

    H = L + cost / size

where `L` is a running "inflation" value set to the H of the last evicted
entry, so long-idle entries naturally sink. We use retrieval latency as the
cost term (the cost-aware GDS variant).
"""

from __future__ import annotations

from common import CacheEntry, ObjectSpec
from baselines.base import BaseCache


class GDSCache(BaseCache):
    name = "GDS"

    def __init__(self, capacity_bytes: int, **kw):
        super().__init__(capacity_bytes, **kw)
        self._L = 0.0

    def _cost_term(self, spec: ObjectSpec) -> float:
        # latency to regenerate, in ms; +1 so nothing is ever exactly zero-value
        return spec.gen_latency_ms + 1.0

    def _H(self, entry: CacheEntry) -> float:
        return self._L + self._cost_term(entry.spec) / entry.size_bytes * 1_000_000.0

    def _score(self, entry: CacheEntry, now: float) -> float:
        return entry.meta.get("H", 0.0)

    def _on_insert(self, entry: CacheEntry, now: float) -> None:
        entry.meta["H"] = self._H(entry)

    def _touch(self, entry: CacheEntry, now: float) -> None:
        entry.meta["H"] = self._H(entry)

    def _on_evict(self, entry: CacheEntry, now: float) -> None:
        self._L = entry.meta.get("H", self._L)
