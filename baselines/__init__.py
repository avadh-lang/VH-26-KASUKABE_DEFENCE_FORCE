"""Conventional cache policies AACMS is benchmarked against: LRU, LFU, GDS, GDSF."""

from baselines.lru import LRUCache
from baselines.lfu import LFUCache
from baselines.gds import GDSCache
from baselines.gdsf import GDSFCache

# name -> factory(capacity_bytes) ; used by the benchmark runner and the API
REGISTRY = {
    "LRU": LRUCache,
    "LFU": LFUCache,
    "GDS": GDSCache,
    "GDSF": GDSFCache,
}

__all__ = ["LRUCache", "LFUCache", "GDSCache", "GDSFCache", "REGISTRY"]
