"""
GreedyDual-Size-Frequency (Cherkasova, 1998).

GDS plus an access-frequency multiplier:

    H = L + freq * cost / size

This is the strongest conventional baseline for our setting — it already
blends recency (via L aging), frequency, size and retrieval cost. AACMS uses
the same shape as its static core, then makes the blend *adaptive*.
"""

from __future__ import annotations

from common import CacheEntry, ObjectSpec
from baselines.base import BaseCache


class GDSFCache(BaseCache):
    name = "GDSF"

    def __init__(self, capacity_bytes: int, **kw):
        super().__init__(capacity_bytes, **kw)
        self._L = 0.0

    def _cost_term(self, spec: ObjectSpec) -> float:
        return spec.gen_latency_ms + 1.0

    def _H(self, entry: CacheEntry) -> float:
        return self._L + entry.freq * self._cost_term(entry.spec) / entry.size_bytes * 1_000_000.0

    def _score(self, entry: CacheEntry, now: float) -> float:
        return entry.meta.get("H", 0.0)

    def _on_insert(self, entry: CacheEntry, now: float) -> None:
        entry.meta["H"] = self._H(entry)

    def _touch(self, entry: CacheEntry, now: float) -> None:
        entry.meta["H"] = self._H(entry)

    def _on_evict(self, entry: CacheEntry, now: float) -> None:
        self._L = entry.meta.get("H", self._L)
