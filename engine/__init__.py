"""
CACHE MIND engine — the decision layer above a multi-level cache.

    CacheMind(capacity_bytes, cost_cfg, ...)   implements common.CachePolicy

Pieces:
  predict.py     per-object access predictor (p_soon, trend, confidence, ETA)
  scoring.py     keep-worthiness value + net-value-per-tier economics
  bandit.py      LinUCB contextual bandit — adapts the score weights at runtime
  regime.py      lightweight workload-regime label
  autoscaler.py  cost-benefit tier-capacity controller with a ghost list
  cachemind.py   the 11-step epoch loop that ties it together
"""

from engine.cachemind import CacheMind, WEIGHT_ARMS
from engine.scoring import WEIGHT_KEYS

# backwards-compat alias (older imports / tests)
AACMSCache = CacheMind

__all__ = ["CacheMind", "AACMSCache", "WEIGHT_ARMS", "WEIGHT_KEYS"]
