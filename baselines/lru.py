"""Least Recently Used — evict the entry untouched for the longest."""

from __future__ import annotations

from common import CacheEntry
from baselines.base import BaseCache


class LRUCache(BaseCache):
    name = "LRU"

    def _score(self, entry: CacheEntry, now: float) -> float:
        # smaller last_access = older = evict first
        return entry.last_access
