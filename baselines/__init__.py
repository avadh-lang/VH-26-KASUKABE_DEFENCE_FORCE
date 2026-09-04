"""
Conventional cache policies CACHE MIND is benchmarked against.

  single-tier:  LRU · LFU · GDS · GDSF
  tiered:       LRU-tiered · GDSF-tiered  (same L1/L2/L3 hardware, dumb placement)
"""

from baselines.lru import LRUCache
from baselines.lfu import LFUCache
from baselines.gds import GDSCache
from baselines.gdsf import GDSFCache
from baselines.tiered import TieredLRU, TieredGDSF

# name -> factory(capacity_bytes) ; used by the benchmark runner and the API
REGISTRY = {
    "LRU": LRUCache,
    "LFU": LFUCache,
    "GDS": GDSCache,
    "GDSF": GDSFCache,
    "LRU-tiered": TieredLRU,
    "GDSF-tiered": TieredGDSF,
}

__all__ = ["LRUCache", "LFUCache", "GDSCache", "GDSFCache",
           "TieredLRU", "TieredGDSF", "REGISTRY"]
