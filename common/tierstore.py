"""
TieredStore — the three physical levels CACHE MIND places objects in.

    L1  RAM        fast, dear     — the working set
    L2  Redis-ish  warm, cheap    — recently-demoted / prefetched
    L3  cold store  slow, ~free   — long-tail worth keeping but not paying RAM for

A hit in *any* tier avoids the origin's regeneration latency and $ entirely —
that is where most of the saving comes from. The store only does mechanics
(placement, byte accounting, moves); CACHE MIND decides *what goes where*.
"""

from __future__ import annotations

from common.interfaces import CacheEntry, L1, L2, L3


class TieredStore:
    def __init__(self, caps: dict[int, int]):
        self._t: dict[int, dict[str, CacheEntry]] = {L1: {}, L2: {}, L3: {}}
        self._used: dict[int, int] = {L1: 0, L2: 0, L3: 0}
        self._cap: dict[int, int] = dict(caps)
        self._index: dict[str, int] = {}          # key -> tier, for O(1) lookup

    # -- lookup --------------------------------------------------------- #
    def get(self, key: str) -> CacheEntry | None:
        tier = self._index.get(key)
        return self._t[tier][key] if tier else None

    def tier_of(self, key: str) -> int:
        return self._index.get(key, 0)

    def __contains__(self, key: str) -> bool:
        return key in self._index

    # -- placement --------------------------------------------------- #
    def place(self, entry: CacheEntry, tier: int) -> None:
        if entry.key in self._index:
            self.remove(entry.key)
        entry.tier = tier
        self._t[tier][entry.key] = entry
        self._used[tier] += entry.size_bytes
        self._index[entry.key] = tier

    def remove(self, key: str) -> CacheEntry | None:
        tier = self._index.pop(key, None)
        if tier is None:
            return None
        e = self._t[tier].pop(key)
        self._used[tier] -= e.size_bytes
        return e

    def move(self, key: str, to_tier: int) -> int:
        """Promote/demote an entry. Returns bytes moved (for the move-cost model)."""
        e = self.get(key)
        if e is None or e.tier == to_tier:
            return 0
        moved = e.size_bytes
        self.remove(key)
        self.place(e, to_tier)
        return moved

    def set_compressed(self, key: str, compressed: bool) -> None:
        e = self.get(key)
        if e is None or e.compressed == compressed:
            return
        tier = e.tier
        self._used[tier] -= e.size_bytes
        e.compressed = compressed
        self._used[tier] += e.size_bytes

    # -- capacity -------------------------------------------------------- #
    def cap(self, tier: int) -> int:
        return self._cap[tier]

    def set_cap(self, tier: int, nbytes: int) -> None:
        self._cap[tier] = int(nbytes)

    def used(self, tier: int) -> int:
        return self._used[tier]

    def free(self, tier: int) -> int:
        return self._cap[tier] - self._used[tier]

    def fits(self, tier: int, nbytes: int) -> bool:
        return self._used[tier] + nbytes <= self._cap[tier]

    # -- iteration ------------------------------------------------------- #
    def entries(self, tier: int) -> list[CacheEntry]:
        return list(self._t[tier].values())

    def all_entries(self):
        for tier in (L1, L2, L3):
            yield from self._t[tier].values()

    @property
    def total_used(self) -> int:
        return sum(self._used.values())

    @property
    def total_cap(self) -> int:
        return sum(self._cap.values())

    @property
    def count(self) -> int:
        return len(self._index)
