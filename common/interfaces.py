"""
Core contracts shared by every AACMS component.

The design keeps a hard boundary:
  - workload/  produces a stream of (timestamp, ObjectSpec) requests
  - baselines/ and engine/ implement CachePolicy
  - benchmark/ drives a policy with a workload and collects EpochSnapshot rows
  - api/ + dashboard/ stream EpochSnapshot rows to the UI

Nothing here imports from any other AACMS package, so all four workstreams
can build in parallel against these types.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
#  What the backend can hand us
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ObjectSpec:
    """
    A backend object that *could* be cached.

    The workload generator fills these in; the cache never invents them.
    All "cost to produce this object" information lives here so that a policy
    can reason about retrieval cost, not just access patterns.
    """

    key: str
    size_bytes: int
    gen_latency_ms: float      # wall-clock cost to (re)generate from origin
    gen_cost_usd: float        # money cost to (re)generate (external API price, compute $)
    ttl_s: float               # entry is "fresh" for this long after (re)generation
    volatility: float = 0.0    # 0..1 — chance the underlying data drifted; drives refresh value
    tags: tuple[str, ...] = ()  # free-form, e.g. ("api",) or ("recsys",)

    def __post_init__(self) -> None:
        if self.size_bytes <= 0:
            raise ValueError(f"{self.key}: size_bytes must be > 0")
        if self.ttl_s <= 0:
            self.ttl_s = float("inf")


# --------------------------------------------------------------------------- #
#  What sits inside a cache
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class CacheEntry:
    """A cached object plus the bookkeeping every policy needs."""

    spec: ObjectSpec
    inserted_at: float
    last_access: float
    refreshed_at: float
    freq: int = 1                       # total hits + the fill that created it
    hits_since_refresh: int = 0
    meta: dict = field(default_factory=dict)   # policy-private scratch space

    # -- convenience ------------------------------------------------------- #
    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def size_bytes(self) -> int:
        return self.spec.size_bytes

    def age_s(self, now: float) -> float:
        return now - self.refreshed_at

    def idle_s(self, now: float) -> float:
        return now - self.last_access

    def is_stale(self, now: float) -> bool:
        return (now - self.refreshed_at) >= self.spec.ttl_s

    def staleness(self, now: float) -> float:
        """0.0 fresh -> 1.0 exactly at TTL -> >1.0 past TTL."""
        if not math.isfinite(self.spec.ttl_s):
            return 0.0
        return (now - self.refreshed_at) / self.spec.ttl_s


# --------------------------------------------------------------------------- #
#  What a single simulated request produced
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class RequestOutcome:
    """Result of serving one request through a policy — the unit the driver logs."""

    key: str
    hit: bool
    stale_served: bool          # served from cache while stale (no blocking refresh)
    refreshed: bool             # a (re)generation happened on this request
    latency_ms: float           # what the client experienced
    cost_usd: float             # origin money cost charged on this request
    action: str                 # 'hit' | 'miss_fill' | 'miss_no_admit' | 'refresh' | 'stale_hit'
    reason: str = ""            # human-readable, shown in the dashboard decision feed


# --------------------------------------------------------------------------- #
#  Cost model config (shared so baseline + engine + api agree on the numbers)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class CostConfig:
    """Infra cost model. One source of truth for every benchmark."""

    mem_usd_per_gb_hour: float = 0.12       # managed in-memory cache RAM price (ElastiCache-class)
    latency_usd_per_ms: float = 2.0e-6      # business cost of user-visible latency, per request-ms
    refresh_discount: float = 1.0           # refresh cost = gen_cost_usd * this (proactive can be cheaper)
    scale_step_bytes: int = 8 * 1024 * 1024  # autoscaler granularity (8 MiB)

    def memory_usd(self, bytes_held: float, seconds: float) -> float:
        gb = bytes_held / (1024 ** 3)
        hours = seconds / 3600.0
        return self.mem_usd_per_gb_hour * gb * hours

    def latency_usd(self, latency_ms: float) -> float:
        return self.latency_usd_per_ms * latency_ms


# --------------------------------------------------------------------------- #
#  Per-epoch snapshot — the row streamed to the dashboard and written to CSV
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class EpochSnapshot:
    policy: str
    epoch: int
    t_sim: float                # simulated seconds elapsed

    # traffic
    requests: int
    hit_rate: float
    stale_rate: float

    # latency (ms, over the epoch)
    avg_latency_ms: float
    p95_latency_ms: float

    # money (USD, cumulative from run start)
    cost_total: float
    cost_origin: float          # regeneration / API calls
    cost_latency: float         # latency business cost
    cost_memory: float          # cache RAM

    # cache state
    capacity_bytes: int
    used_bytes: int
    entries: int
    evictions: int
    refreshes: int

    # engine internals (None for baselines) — powers the "it's adapting" panel
    weights: dict | None = None
    regime: str | None = None
    bandit_arm: str | None = None

    def as_row(self) -> dict:
        d = {
            "policy": self.policy, "epoch": self.epoch, "t_sim": round(self.t_sim, 2),
            "requests": self.requests, "hit_rate": round(self.hit_rate, 4),
            "stale_rate": round(self.stale_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 3),
            "p95_latency_ms": round(self.p95_latency_ms, 3),
            "cost_total": round(self.cost_total, 6),
            "cost_origin": round(self.cost_origin, 6),
            "cost_latency": round(self.cost_latency, 6),
            "cost_memory": round(self.cost_memory, 6),
            "capacity_bytes": self.capacity_bytes, "used_bytes": self.used_bytes,
            "entries": self.entries, "evictions": self.evictions, "refreshes": self.refreshes,
            "regime": self.regime or "", "bandit_arm": self.bandit_arm or "",
        }
        if self.weights:
            for k, v in self.weights.items():
                d[f"w_{k}"] = round(v, 4)
        return d


# --------------------------------------------------------------------------- #
#  The policy contract
# --------------------------------------------------------------------------- #
class CachePolicy(ABC):
    """
    Every caching strategy (LRU, LFU, GDS, GDSF, AACMS) implements this.

    The benchmark driver owns the request loop and the clock. It calls, per request:

        entry = policy.lookup(key, now)
        if entry and not stale:              -> policy.on_hit(entry, now)
        elif entry and stale:                -> policy.should_refresh(entry, now) decides
        else (miss):  fetch from origin, then policy.on_admit(spec, now)

    then always:  policy.on_request_end(outcome, now)

    Capacity is in bytes. Policies may change their own capacity between requests
    (only AACMS does; baselines keep it fixed) — the driver reads `capacity_bytes`
    each epoch for the cost model.
    """

    name: str = "base"

    # -- required ---------------------------------------------------------- #
    @abstractmethod
    def lookup(self, key: str, now: float) -> CacheEntry | None:
        """Return the live entry for key, or None. Must not mutate recency here."""

    @abstractmethod
    def on_hit(self, entry: CacheEntry, now: float) -> None:
        """Register a fresh cache hit (update recency/frequency/priority)."""

    @abstractmethod
    def on_admit(self, spec: ObjectSpec, now: float) -> bool:
        """
        Called after a miss has been served from origin. The policy decides
        whether to cache `spec`, evicting as needed. Return True if admitted.
        """

    # -- overridable ----------------------------------------------------- #
    def should_refresh(self, entry: CacheEntry, now: float) -> bool:
        """
        Called when a lookup hit a stale entry. Default: refresh iff stale
        (blocking). AACMS overrides to weigh refresh cost vs. serve-stale risk.
        """
        return entry.is_stale(now)

    def on_refresh(self, entry: CacheEntry, now: float) -> None:
        """Entry was just regenerated from origin — reset freshness bookkeeping."""
        entry.refreshed_at = now
        entry.hits_since_refresh = 0

    def on_request_end(self, outcome: RequestOutcome, now: float) -> None:
        """Hook for adaptive policies to learn from the just-finished request."""

    def maintenance(self, now: float) -> None:
        """Called once per epoch. AACMS runs its bandit + autoscaler here."""

    def pending_refreshes(self) -> list[str]:
        """
        Keys the policy wants proactively regenerated in the background.
        The driver drains this after `maintenance`, charges a (discounted)
        origin cost with no client latency, and calls `on_refresh`.
        Baselines return []; AACMS refreshes hot near-stale entries here.
        """
        return []

    # -- introspection --------------------------------------------------- #
    @property
    @abstractmethod
    def capacity_bytes(self) -> int: ...

    @property
    @abstractmethod
    def used_bytes(self) -> int: ...

    @property
    @abstractmethod
    def entries(self) -> int: ...

    def internals(self) -> dict:
        """Engine-specific state for the dashboard (weights, regime, arm). Empty for baselines."""
        return {}
