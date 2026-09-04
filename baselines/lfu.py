"""Least Frequently Used — evict the entry with the fewest accesses.

Ties (common early in a run) are broken by recency, which is the usual
practical LFU implementation and avoids pathological behaviour on cold starts.
"""

from __future__ import annotations

from common import CacheEntry
from baselines.base import BaseCache


class LFUCache(BaseCache):
    name = "LFU"

    def _score(self, entry: CacheEntry, now: float) -> float:
        # primary: frequency, secondary: recency — both "smaller = evict first"
        return entry.freq + 1e-9 * entry.last_access
