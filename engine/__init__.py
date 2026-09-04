"""
AACMS engine — the adaptive, application-aware cache.

    AACMSCache(capacity_bytes, cost_cfg, ...)  implements common.CachePolicy

Pieces:
  scoring.py     multi-factor value score for retain vs. evict
  bandit.py      LinUCB contextual bandit that adapts the score weights at runtime
  regime.py      lightweight workload-regime label (steady / spike / shift / cold)
  autoscaler.py  cost-benefit cache-capacity controller with a ghost list
  aacms.py       ties it together
"""

from engine.aacms import AACMSCache, WEIGHT_ARMS
from engine.scoring import WEIGHT_KEYS

__all__ = ["AACMSCache", "WEIGHT_ARMS", "WEIGHT_KEYS"]
