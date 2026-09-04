"""
BaseCache — shared machinery for the size-aware baselines.

Handles the dict, byte accounting, and the evict-until-it-fits loop.
Subclasses only decide *who* to evict by implementing `_score(entry, now)`
(lowest score is evicted first) or overriding `_victim_key`.
"""

from __future__ import annotations

from common import CacheEntry, CachePolicy, ObjectSpec


class BaseCache(CachePolicy):
    name = "base"

    def __init__(self, capacity_bytes: int, *, hit_latency_ms: float = 0.5):
        self._capacity = int(capacity_bytes)
        self.hit_latency_ms = hit_latency_ms
        self._entries: dict[str, CacheEntry] = {}
        self._used = 0
        self._evictions = 0
        self._refreshes = 0

    # -- CachePolicy introspection -------------------------------------- #
    @property
    def capacity_bytes(self) -> int:
        return self._capacity

    @property
    def used_bytes(self) -> int:
        return self._used

    @property
    def entries(self) -> int:
        return len(self._entries)

    @property
    def evictions(self) -> int:
        return self._evictions

    @property
    def refreshes(self) -> int:
        return self._refreshes

    # -- lookup / hit -------------------------------------------------- #
    def lookup(self, key: str, now: float) -> CacheEntry | None:
        return self._entries.get(key)

    def on_hit(self, entry: CacheEntry, now: float) -> None:
        entry.freq += 1
        entry.hits_since_refresh += 1
        entry.last_access = now
        self._touch(entry, now)

    def on_refresh(self, entry: CacheEntry, now: float) -> None:
        super().on_refresh(entry, now)
        self._refreshes += 1

    # -- admission / eviction ---------------------------------------- #
    def on_admit(self, spec: ObjectSpec, now: float) -> bool:
        if spec.size_bytes > self._capacity:
            return False  # can never fit
        while self._used + spec.size_bytes > self._capacity:
            if not self._evict_one(now):
                return False
        entry = CacheEntry(
            spec=spec, inserted_at=now, last_access=now, refreshed_at=now, freq=1,
        )
        self._entries[spec.key] = entry
        self._used += spec.size_bytes
        self._on_insert(entry, now)
        return True

    def _evict_one(self, now: float) -> bool:
        victim = self._victim_key(now)
        if victim is None:
            return False
        entry = self._entries.pop(victim)
        self._used -= entry.size_bytes
        self._evictions += 1
        self._on_evict(entry, now)
        return True

    def _victim_key(self, now: float) -> str | None:
        if not self._entries:
            return None
        return min(self._entries.values(), key=lambda e: self._score(e, now)).key

    # -- subclass hooks -------------------------------------------------- #
    def _score(self, entry: CacheEntry, now: float) -> float:
        """Lower = evicted first. Subclasses override."""
        raise NotImplementedError

    def _touch(self, entry: CacheEntry, now: float) -> None:
        """Called on every hit — update subclass bookkeeping."""

    def _on_insert(self, entry: CacheEntry, now: float) -> None:
        ...

    def _on_evict(self, entry: CacheEntry, now: float) -> None:
        ...
