"""Shared contracts for CACHE MIND. Every module depends on this and nothing else."""

from common.interfaces import (
    ORIGIN, L1, L2, L3,
    ObjectSpec,
    CacheEntry,
    RequestOutcome,
    CachePolicy,
    EpochSnapshot,
    CostConfig,
    TierSpec,
    DEFAULT_TIERS,
)
from common.tierstore import TieredStore

__all__ = [
    "TieredStore",
    "ORIGIN", "L1", "L2", "L3",
    "ObjectSpec",
    "CacheEntry",
    "RequestOutcome",
    "CachePolicy",
    "EpochSnapshot",
    "CostConfig",
    "TierSpec",
    "DEFAULT_TIERS",
]
