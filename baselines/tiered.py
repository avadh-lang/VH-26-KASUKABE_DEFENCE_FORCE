"""
Tiered baselines — the *fair* comparison for CACHE MIND.

Same three physical levels (L1/L2/L3, same sizes, same cost model), but the
placement logic is dumb: every object enters at L1, and when a tier is full its
victim is chosen by the wrapped rule (LRU or GDSF) and **demoted** one level
down instead of evicted (evicted only from L3).

This isolates CACHE MIND's contribution: CM vs `GDSF` shows the whole-system
win; CM vs `GDSF-tiered` shows the value of *deciding where each object lives*
(prediction, net-value placement, prefetch) on top of the same hardware.
"""

from __future__ import annotations

import numpy as np

from common import CacheEntry, CachePolicy, CostConfig, ObjectSpec, TieredStore, L1, L2, L3



class _TieredBase(CachePolicy):
    name = "tiered"

    def __init__(self, l1_bytes: int, *, l2_mult: float = 4.0, l3_mult: float = 12.0):
        l1 = int(l1_bytes)
        self.store = TieredStore({L1: l1, L2: int(l1 * l2_mult), L3: int(l1 * l3_mult)})
        self._evictions = self._refreshes = 0
        self._promotions = self._demotions = self._prefetches = 0
        self._rng = np.random.default_rng(0)

    # -- introspection ------------------------------------------------- #
    @property
    def capacity_bytes(self) -> int:
        return self.store.total_cap

    @property
    def used_bytes(self) -> int:
        return self.store.total_used

    @property
    def entries(self) -> int:
        return self.store.count

    def tier_used(self) -> dict[int, int]:
        return {t: self.store.used(t) for t in (L1, L2, L3)}

    def tier_capacity(self) -> dict[int, int]:
        return {t: self.store.cap(t) for t in (L1, L2, L3)}

    def counters(self) -> dict[str, int]:
        return {"evictions": self._evictions, "refreshes": self._refreshes,
                "promotions": self._promotions, "demotions": self._demotions,
                "prefetches": self._prefetches}

    # -- ranking (subclass) ------------------------------------------- #
    def _priority(self, e: CacheEntry, now: float) -> float:
        """Lower = demote/evict first."""
        raise NotImplementedError

    # -- policy ------------------------------------------------------- #
    def lookup(self, key: str, now: float) -> CacheEntry | None:
        return self.store.get(key)

    def on_hit(self, entry: CacheEntry, now: float) -> None:
        entry.freq += 1
        entry.hits_since_refresh += 1
        entry.last_access = now
        # promote one level on a hit (classic multi-level behaviour)
        if entry.tier != L1:
            target = entry.tier - 1
            if self._make_room(target, entry.size_bytes, now, exclude=entry.key):
                self.store.move(entry.key, target)
                self._promotions += 1

    def on_refresh(self, entry: CacheEntry, now: float) -> None:
        entry.refreshed_at = now
        entry.hits_since_refresh = 0
        self._refreshes += 1

    def on_admit(self, spec: ObjectSpec, now: float) -> bool:
        if spec.size_bytes > self.store.cap(L1):
            return False
        if not self._make_room(L1, spec.size_bytes, now):
            return False
        self.store.place(CacheEntry(spec, now, now, now, freq=1), L1)
        return True

    def _make_room(self, tier: int, need: int, now: float, exclude: str | None = None) -> bool:
        guard = 0
        while not self.store.fits(tier, need) and guard < 500:
            guard += 1
            es = self.store.entries(tier)
            if not es:
                return False
            if len(es) > 32:
                idx = self._rng.choice(len(es), size=32, replace=False)
                es = [es[i] for i in idx]
            pool = [e for e in es if e.key != exclude] or es
            victim = min(pool, key=lambda e: self._priority(e, now))
            if tier < L3:
                target = tier + 1
                if not self.store.fits(target, victim.size_bytes):
                    self._make_room(target, victim.size_bytes, now)
                if self.store.fits(target, victim.size_bytes):
                    self.store.move(victim.key, target)
                    self._demotions += 1
                else:
                    self.store.remove(victim.key)
                    self._evictions += 1
            else:
                self.store.remove(victim.key)
                self._evictions += 1
        return self.store.fits(tier, need)


class TieredLRU(_TieredBase):
    name = "LRU-tiered"

    def _priority(self, e: CacheEntry, now: float) -> float:
        return e.last_access


class TieredGDSF(_TieredBase):
    name = "GDSF-tiered"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._L = 0.0

    def _priority(self, e: CacheEntry, now: float) -> float:
        h = self._L + e.freq * (e.spec.gen_latency_ms + 1.0) / e.full_size_bytes * 1e6
        return h
