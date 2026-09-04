"""
AACMS — the adaptive, application-aware cache policy.

What makes it more than GDSF:
  1. multi-factor value score (scoring.py) with online-normalised signals
  2. weights rewritten every epoch by a LinUCB contextual bandit (bandit.py)
  3. value-aware ADMISSION control (a scan / one-hit spike can't evict the
     working set) using a decayed demand sketch
  4. proactive background REFRESH of hot, near-stale, drift-prone entries
  5. cost-benefit AUTOSCALING via a ghost list (autoscaler.py)
  6. sampled ("Redis-style") approximate eviction — O(1) per eviction

Only `common` is imported; the engine has no dependency on baselines/.
"""

from __future__ import annotations

import math
from collections import Counter, deque

import numpy as np

from common import CacheEntry, CachePolicy, CostConfig, ObjectSpec, RequestOutcome
from engine.autoscaler import Autoscaler, GhostList
from engine.bandit import LinUCBWeightController
from engine.regime import RegimeDetector
from engine.scoring import ScoreRefs, refresh_priority, value

# caching "personalities" the bandit chooses between.
# key "core" is the GDSF magnitude term; the rest are [0,1] shape modifiers.
WEIGHT_ARMS: dict[str, dict[str, float]] = {
    "balanced":        {"core": 1.0, "rec": 0.5, "freq": 0.5, "cost": 0.5, "size": 0.5},
    "cost_first":      {"core": 1.0, "rec": 0.2, "freq": 0.3, "cost": 1.5, "size": 0.3},
    "recency_first":   {"core": 1.0, "rec": 1.6, "freq": 0.2, "cost": 0.3, "size": 0.4},
    "frequency_first": {"core": 1.0, "rec": 0.2, "freq": 1.6, "cost": 0.3, "size": 0.3},
    "memory_saver":    {"core": 1.0, "rec": 0.4, "freq": 0.4, "cost": 0.5, "size": 1.6},
}


class AACMSCache(CachePolicy):
    name = "AACMS"

    def __init__(
        self,
        capacity_bytes: int,
        cost_cfg: CostConfig | None = None,
        *,
        min_bytes: int | None = None,
        max_bytes: int | None = None,
        epoch_seconds: float = 10.0,
        hit_latency_ms: float = 0.5,
        sample_size: int = 48,
        proactive_per_epoch: int = 8,
        autoscale: bool = True,
        seed: int = 0,
    ):
        self._capacity = int(capacity_bytes)
        self.cfg = cost_cfg or CostConfig()
        self.epoch_seconds = epoch_seconds
        self.hit_latency_ms = hit_latency_ms
        self.sample_size = sample_size
        self.proactive_per_epoch = proactive_per_epoch
        self.autoscale = autoscale
        self._rng = np.random.default_rng(seed)

        self._entries: dict[str, CacheEntry] = {}
        self._used = 0
        self._evictions = 0
        self._refreshes = 0
        self._L = 0.0                       # GreedyDual inflation

        self.refs = ScoreRefs()
        self.bandit = LinUCBWeightController(WEIGHT_ARMS, seed=seed)
        self.detector = RegimeDetector()
        self.w: dict[str, float] = dict(WEIGHT_ARMS["balanced"])
        self._admit_margin = 0.85
        self.regime = "cold_start"

        self.ghost = GhostList()
        self.scaler = Autoscaler(
            self.cfg,
            min_bytes=min_bytes or max(self._capacity // 4, self.cfg.scale_step_bytes),
            max_bytes=max_bytes or self._capacity * 3,
            epoch_seconds=epoch_seconds,
        )

        # demand sketch for admission control (decayed each epoch)
        self._demand: Counter[str] = Counter()
        # reuse-gap tracking for tau adaptation
        self._last_seen: dict[str, float] = {}
        self._reuse_gap_ewma = 60.0
        self._interarrival_ewma = 0.05
        self._prev_t = 0.0

        # epoch accumulators
        self._epoch = 0
        self._ep_req = 0
        self._ep_hit = 0
        self._ep_stale = 0
        self._ep_evict = 0
        self._ep_ghost = 0
        self._ep_lat = 0.0
        self._ep_cost = 0.0
        self._ep_miss_cost = 0.0
        self._ep_miss = 0
        self._ep_keys: Counter[str] = Counter()
        self._prev_hit_rate = 0.0
        self._rate_max = 1.0
        self._latmax = 1.0
        self._costmax = 1e-9

        self._refresh_q: list[str] = []
        self._feed: deque[dict] = deque(maxlen=40)

    # ------------------------------------------------------------------ #
    #  CachePolicy surface
    # ------------------------------------------------------------------ #
    @property
    def capacity_bytes(self) -> int:
        return self._capacity

    @property
    def used_bytes(self) -> int:
        return self._used

    @property
    def entries(self) -> int:
        return len(self._entries)

    @property
    def evictions(self) -> int:
        return self._evictions

    @property
    def refreshes(self) -> int:
        return self._refreshes

    def lookup(self, key: str, now: float) -> CacheEntry | None:
        return self._entries.get(key)

    def on_hit(self, entry: CacheEntry, now: float) -> None:
        # reuse gap (for recency-horizon adaptation)
        prev = self._last_seen.get(entry.key)
        if prev is not None:
            gap = now - prev
            self._reuse_gap_ewma += 0.02 * (gap - self._reuse_gap_ewma)
        self._last_seen[entry.key] = now

        entry.freq += 1
        entry.hits_since_refresh += 1
        entry.last_access = now
        cms = _cost_ms(entry.spec, self.cfg.latency_usd_per_ms)
        self.refs.observe(entry.spec, entry.freq, cms)

    def should_refresh(self, entry: CacheEntry, now: float) -> bool:
        if not entry.is_stale(now):
            return False
        rp = refresh_priority(entry, now, self.refs, self.cfg.latency_usd_per_ms)
        if rp >= 0.15:                       # valuable + drift-prone -> block & refresh
            return True
        self._note("serve_stale", entry.key, f"stale but low refresh value ({rp:.2f}) — serve stale, save $")
        return False

    def on_refresh(self, entry: CacheEntry, now: float) -> None:
        entry.refreshed_at = now
        entry.hits_since_refresh = 0
        self._refreshes += 1

    def on_admit(self, spec: ObjectSpec, now: float) -> bool:
        # ghost-list accounting: this miss might have been avoidable
        g = self.ghost.hit(spec.key)
        if g is not None:
            size, regen = g
            self._ep_ghost += 1
            self.scaler.record_ghost_hit(size, regen)

        if spec.size_bytes > self._capacity:
            return False

        est_freq = 1 + int(self._demand.get(spec.key, 0))
        hypo = CacheEntry(spec, now, now, now, freq=est_freq)
        hypo_val = value(hypo, now, self.w, self.refs, self._L, self.cfg.latency_usd_per_ms)

        if self._used + spec.size_bytes <= self._capacity:
            self._insert(hypo, now)
            return True

        victims, freed, worst_val = self._plan_eviction(spec.size_bytes, now)
        if freed >= spec.size_bytes and hypo_val >= worst_val * self._admit_margin:
            for vk in victims:
                self._evict(vk, now)
            self._insert(hypo, now)
            return True

        # rejected — keep the demand signal so a genuinely popular key gets a
        # second chance on its next request
        self._note("admit_reject", spec.key,
                   f"value {hypo_val:.2f} < best victim {worst_val:.2f} — don't pollute cache")
        return False

    def on_request_end(self, outcome: RequestOutcome, now: float) -> None:
        self._ep_req += 1
        self._ep_keys[outcome.key] += 1
        self._demand[outcome.key] += 1
        self._ep_lat += outcome.latency_ms
        self._ep_cost += outcome.cost_usd
        if outcome.hit:
            self._ep_hit += 1
        else:
            self._ep_miss += 1
            self._ep_miss_cost += outcome.cost_usd
        if outcome.stale_served:
            self._ep_stale += 1

        if now > self._prev_t:
            self._interarrival_ewma += 0.001 * ((now - self._prev_t) - self._interarrival_ewma)
        self._prev_t = now

    # ------------------------------------------------------------------ #
    #  Per-epoch brain: bandit + autoscaler + proactive refresh
    # ------------------------------------------------------------------ #
    def maintenance(self, now: float) -> None:
        req = max(self._ep_req, 1)
        hit_rate = self._ep_hit / req
        avg_lat = self._ep_lat / req
        origin_cost = self._ep_miss_cost
        self._rate_max = max(self._rate_max, self._ep_req)
        self._latmax = max(self._latmax, avg_lat)
        self._costmax = max(self._costmax, origin_cost)

        distinct = len(self._ep_keys)
        entropy = 0.0
        if distinct > 1:
            counts = np.array(list(self._ep_keys.values()), dtype=float)
            p = counts / counts.sum()
            entropy = float(-(p * np.log(p)).sum() / math.log(distinct))

        feats = {
            "rate": self._ep_req / self._rate_max,
            "rate_abs": float(self._ep_req),
            "entropy": entropy,
            "hit_trend": hit_rate - self._prev_hit_rate,
            "miss_cost": (origin_cost / max(self._ep_miss, 1)) / max(self._costmax, 1e-9),
            "pressure": self._used / max(self._capacity, 1),
            "evict_rate": self._ep_evict / req,
            "ghost_rate": self._ep_ghost / req,
        }

        # learn from the epoch that just ended, then pick the next arm
        if self._epoch > 0:
            reward = self.bandit.reward_from_epoch(
                hit_rate,
                norm_latency=avg_lat / max(self._latmax, 1e-9),
                norm_cost=origin_cost / max(self._costmax, 1e-9),
            )
            self.bandit.learn(reward)
        self.w = self.bandit.select(feats)
        self.regime = self.detector.update(feats)

        # recency horizon follows real reuse gaps
        self.refs.adapt_tau(self._interarrival_ewma, self._reuse_gap_ewma)

        # cost-benefit autoscaling
        if self.autoscale:
            cold_cut = now - 2.0 * self.epoch_seconds
            cold_bytes = sum(e.size_bytes for e in self._entries.values() if e.last_access < cold_cut)
            new_cap, action, reason = self.scaler.decide(
                capacity=self._capacity, used=self._used,
                evictions=self._ep_evict, requests=self._ep_req,
                cold_bytes=cold_bytes,
            )
            if new_cap != self._capacity:
                if new_cap < self._capacity:
                    self._shrink_to(new_cap, now)
                self._capacity = new_cap
                self._note(f"autoscale_{action}", "-", reason)

        # proactive refresh queue: hottest near-stale entries
        self._refresh_q = self._pick_proactive_refresh(now)

        # decay demand sketch, roll epoch
        for k in list(self._demand):
            v = self._demand[k] * 0.5
            if v < 1:
                del self._demand[k]
            else:
                self._demand[k] = v

        self._prev_hit_rate = hit_rate
        self._epoch += 1
        self._ep_req = self._ep_hit = self._ep_stale = 0
        self._ep_evict = self._ep_ghost = self._ep_miss = 0
        self._ep_lat = self._ep_cost = self._ep_miss_cost = 0.0
        self._ep_keys = Counter()

    def pending_refreshes(self) -> list[str]:
        q, self._refresh_q = self._refresh_q, []
        return q

    # ------------------------------------------------------------------ #
    #  internals for the dashboard / demo narration
    # ------------------------------------------------------------------ #
    def internals(self) -> dict:
        snap = self.bandit.snapshot()
        return {
            "weights": snap["weights"],
            "bandit_arm": snap["arm"],
            "arm_pulls": snap["pulls"],
            "regime": self.regime,
            "tau_s": round(self.refs.tau_s, 1),
            "L": round(self._L, 3),
            "ghost_size": len(self.ghost),
            "capacity_bytes": self._capacity,
            "decisions": list(self._feed)[-12:],
        }

    # ------------------------------------------------------------------ #
    #  cache mechanics
    # ------------------------------------------------------------------ #
    def _insert(self, entry: CacheEntry, now: float) -> None:
        entry.inserted_at = entry.last_access = entry.refreshed_at = now
        self._entries[entry.key] = entry
        self._used += entry.size_bytes
        self._last_seen[entry.key] = now

    def _evict(self, key: str, now: float) -> None:
        e = self._entries.pop(key)
        self._used -= e.size_bytes
        self._evictions += 1
        self._ep_evict += 1
        v = value(e, now, self.w, self.refs, self._L, self.cfg.latency_usd_per_ms)
        self._L = max(self._L, v)                    # GreedyDual aging
        regen = e.spec.gen_cost_usd + self.cfg.latency_usd_per_ms * e.spec.gen_latency_ms
        self.ghost.add(key, e.size_bytes, regen)

    def _val(self, e: CacheEntry, now: float) -> float:
        return value(e, now, self.w, self.refs, self._L, self.cfg.latency_usd_per_ms)

    def _sample(self, k: int) -> list[CacheEntry]:
        """Uniform sample of distinct entries (Redis-style approximate eviction)."""
        vals = list(self._entries.values())
        if len(vals) <= k:
            return vals
        idx = self._rng.choice(len(vals), size=k, replace=False)
        return [vals[i] for i in idx]

    def _plan_eviction(self, need: int, now: float) -> tuple[list[str], int, float]:
        """Lowest-value entries from a sample that free `need` bytes; `worst` = priciest victim."""
        pool = self._sample(self.sample_size)
        pool.sort(key=lambda e: self._val(e, now))          # ascending value
        picked: list[str] = []
        freed = 0
        worst = 0.0
        for e in pool:
            picked.append(e.key)
            freed += e.size_bytes
            worst = self._val(e, now)
            if freed >= need:
                break
        return picked, freed, worst

    def _shrink_to(self, target: int, now: float) -> None:
        while self._used > target and self._entries:
            pool = self._sample(self.sample_size)
            victim = min(pool, key=lambda e: self._val(e, now))
            self._evict(victim.key, now)

    def _pick_proactive_refresh(self, now: float) -> list[str]:
        if not self._entries or self.proactive_per_epoch <= 0:
            return []
        scored = [
            (refresh_priority(e, now, self.refs, self.cfg.latency_usd_per_ms), e.key)
            for e in self._entries.values()
        ]
        scored.sort(reverse=True)
        out = [k for pr, k in scored[: self.proactive_per_epoch] if pr > 0.25]
        for k in out:
            self._note("proactive_refresh", k, "hot + near-stale + drift-prone — refresh before it's asked for")
        return out

    def _note(self, action: str, key: str, reason: str) -> None:
        self._feed.append({"epoch": self._epoch, "action": action, "key": key, "reason": reason})


def _cost_ms(spec: ObjectSpec, latency_usd_per_ms: float) -> float:
    return spec.gen_latency_ms + spec.gen_cost_usd / max(latency_usd_per_ms, 1e-12)
