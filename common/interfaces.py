"""
Core contracts shared by every CACHE MIND component.

The design keeps a hard boundary:
  - workload/  produces a stream of (timestamp, ObjectSpec) requests
  - baselines/ and engine/ implement CachePolicy
  - benchmark/ drives a policy with a workload and collects EpochSnapshot rows
  - api/ + dashboard/ stream EpochSnapshot rows to the UI

Nothing here imports from any other package, so all workstreams build in
parallel against these types.

CACHE MIND is a *multi-level* cache: L1 (RAM, fast, dear), L2 (Redis-class,
warm, cheap), L3 (disk/cold-store, slow, near-free). Conventional baselines
are single-level — they only ever use L1. A warm hit in L2/L3 still avoids the
origin's regeneration latency and dollar cost entirely; that is where most of
CACHE MIND's saving comes from.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# tier indices — 0 means "not resident / served from origin"
ORIGIN = 0
L1, L2, L3 = 1, 2, 3


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
    compressible: float = 0.5  # 0..1 — fraction of size recoverable by compression
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
    tier: int = L1                      # which level this entry currently lives in
    compressed: bool = False            # stored at reduced size (adds decompress latency on hit)
    meta: dict = field(default_factory=dict)   # policy-private scratch space

    # -- convenience ------------------------------------------------------- #
    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def size_bytes(self) -> int:
        """Bytes actually occupied — smaller when compressed."""
        if self.compressed:
            return max(1, int(self.spec.size_bytes * (1.0 - self.spec.compressible)))
        return self.spec.size_bytes

    @property
    def full_size_bytes(self) -> int:
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
    hit_tier: int               # L1/L2/L3 the request was served from, or ORIGIN (0) on a miss
    stale_served: bool          # served from cache while stale (no blocking refresh)
    refreshed: bool             # a (re)generation happened on this request
    latency_ms: float           # what the client experienced
    cost_usd: float             # origin money cost charged on this request
    action: str                 # 'l1_hit'|'l2_hit'|'l3_hit'|'miss_fill'|'miss_no_admit'|'refresh'|'stale_hit'
    reason: str = ""            # human-readable, shown in the dashboard decision feed


# --------------------------------------------------------------------------- #
#  Tiered cost model
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class TierSpec:
    name: str
    hit_latency_ms: float
    usd_per_gb_hour: float


# L1 dear+fast, L2 ~4x cheaper + Redis-ish latency, L3 near-free + disk latency.
# Every tier is still an order of magnitude faster/cheaper than regenerating
# from a 120-2000 ms, $0.0005-0.006 origin.
DEFAULT_TIERS: tuple[TierSpec, TierSpec, TierSpec] = (
    TierSpec("L1-RAM", 0.5, 0.12),
    TierSpec("L2-Redis", 4.0, 0.030),
    TierSpec("L3-Cold", 28.0, 0.004),
)


@dataclass(slots=True)
class CostConfig:
    """Infra cost model. One source of truth for every benchmark."""

    tiers: tuple[TierSpec, TierSpec, TierSpec] = DEFAULT_TIERS
    latency_usd_per_ms: float = 2.0e-6      # business cost of user-visible latency, per request-ms
    refresh_discount: float = 1.0           # refresh cost = gen_cost_usd * this (proactive can be cheaper)
    scale_step_bytes: int = 4 * 1024 * 1024  # autoscaler granularity
    move_usd_per_gb: float = 0.010          # data-movement cost of a promote / demote
    decompress_latency_ms: float = 2.0      # extra latency to serve a compressed entry

    # -- backward-compatible single-tier accessor (baselines use this) ----- #
    @property
    def mem_usd_per_gb_hour(self) -> float:
        return self.tiers[0].usd_per_gb_hour

    def memory_usd(self, bytes_held: float, seconds: float, tier: int = L1) -> float:
        gb = bytes_held / (1024 ** 3)
        hours = seconds / 3600.0
        return self.tiers[tier - 1].usd_per_gb_hour * gb * hours

    def tier_latency_ms(self, tier: int) -> float:
        return self.tiers[tier - 1].hit_latency_ms

    def latency_usd(self, latency_ms: float) -> float:
        return self.latency_usd_per_ms * latency_ms

    def move_usd(self, bytes_moved: float) -> float:
        return self.move_usd_per_gb * bytes_moved / (1024 ** 3)


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
    hit_rate: float             # any tier
    stale_rate: float
    l1_rate: float = 0.0        # fraction of requests served from each level
    l2_rate: float = 0.0
    l3_rate: float = 0.0

    # latency (ms, over the epoch)
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0

    # money (USD, cumulative from run start)
    cost_total: float = 0.0
    cost_origin: float = 0.0     # regeneration / API calls
    cost_latency: float = 0.0    # latency business cost
    cost_memory: float = 0.0     # cache tiers
    cost_move: float = 0.0       # promote / demote data movement

    # cache state
    capacity_bytes: int = 0      # total across tiers
    used_bytes: int = 0
    entries: int = 0
    evictions: int = 0
    refreshes: int = 0
    promotions: int = 0
    demotions: int = 0
    prefetches: int = 0
    l1_used: int = 0
    l2_used: int = 0
    l3_used: int = 0
    l1_cap: int = 0
    l2_cap: int = 0
    l3_cap: int = 0

    # engine internals (None for baselines) — powers the "it's adapting" panel
    weights: dict | None = None
    regime: str | None = None
    bandit_arm: str | None = None

    def as_row(self) -> dict:
        d = {
            "policy": self.policy, "epoch": self.epoch, "t_sim": round(self.t_sim, 2),
            "requests": self.requests, "hit_rate": round(self.hit_rate, 4),
            "stale_rate": round(self.stale_rate, 4),
            "l1_rate": round(self.l1_rate, 4), "l2_rate": round(self.l2_rate, 4),
            "l3_rate": round(self.l3_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 3),
            "p95_latency_ms": round(self.p95_latency_ms, 3),
            "cost_total": round(self.cost_total, 6),
            "cost_origin": round(self.cost_origin, 6),
            "cost_latency": round(self.cost_latency, 6),
            "cost_memory": round(self.cost_memory, 6),
            "cost_move": round(self.cost_move, 6),
            "capacity_bytes": self.capacity_bytes, "used_bytes": self.used_bytes,
            "entries": self.entries, "evictions": self.evictions, "refreshes": self.refreshes,
            "promotions": self.promotions, "demotions": self.demotions,
            "prefetches": self.prefetches,
            "l1_used": self.l1_used, "l2_used": self.l2_used, "l3_used": self.l3_used,
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
    Every caching strategy (LRU, LFU, GDS, GDSF, CACHE MIND) implements this.

    The benchmark driver owns the request loop and the clock. Per request:

        entry = policy.lookup(key, now)           # searches all resident tiers
        if entry and not stale:              -> policy.on_hit(entry, now)
        elif entry and stale:                -> policy.should_refresh(entry, now) decides
        else (miss):  fetch from origin, then policy.on_admit(spec, now)

    then always:  policy.on_request_end(outcome, now)

    The tier a hit came from is read from `entry.tier` (baselines leave it at
    L1). Capacity is in bytes; single-tier policies report it all under L1.
    """

    name: str = "base"

    # -- required ---------------------------------------------------------- #
    @abstractmethod
    def lookup(self, key: str, now: float) -> CacheEntry | None:
        """Return the live entry for key from whichever tier holds it, or None."""

    @abstractmethod
    def on_hit(self, entry: CacheEntry, now: float) -> None:
        """Register a fresh cache hit (update recency/frequency/priority)."""

    @abstractmethod
    def on_admit(self, spec: ObjectSpec, now: float) -> bool:
        """
        Called after a miss has been served from origin. The policy decides
        whether to cache `spec` (and at which tier), evicting as needed.
        Return True if admitted anywhere.
        """

    # -- overridable ----------------------------------------------------- #
    def should_refresh(self, entry: CacheEntry, now: float) -> bool:
        return entry.is_stale(now)

    def on_refresh(self, entry: CacheEntry, now: float) -> None:
        entry.refreshed_at = now
        entry.hits_since_refresh = 0

    def on_request_end(self, outcome: RequestOutcome, now: float) -> None:
        """Hook for adaptive policies to learn from the just-finished request."""

    def maintenance(self, now: float) -> None:
        """Called once per epoch. CACHE MIND runs its full decision loop here."""

    def pending_refreshes(self) -> list[str]:
        """Keys the policy wants proactively regenerated (background, no latency)."""
        return []

    def pending_prefetches(self) -> list[str]:
        """
        Keys the policy predicts will be hot soon and wants warmed from origin
        into the cache before they are requested. The driver charges a
        (discounted) origin cost with no client latency.
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

    def tier_used(self) -> dict[int, int]:
        return {L1: self.used_bytes, L2: 0, L3: 0}

    def tier_capacity(self) -> dict[int, int]:
        return {L1: self.capacity_bytes, L2: 0, L3: 0}

    def counters(self) -> dict[str, int]:
        """evictions/refreshes/promotions/demotions/prefetches since run start."""
        return {
            "evictions": int(getattr(self, "_evictions", 0)),
            "refreshes": int(getattr(self, "_refreshes", 0)),
            "promotions": int(getattr(self, "_promotions", 0)),
            "demotions": int(getattr(self, "_demotions", 0)),
            "prefetches": int(getattr(self, "_prefetches", 0)),
        }

    def internals(self) -> dict:
        """Engine-specific state for the dashboard (weights, regime, arm, feed)."""
        return {}
