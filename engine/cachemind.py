"""
CACHE MIND — the decision engine that sits above a multi-level cache.

Per request it serves from L1 / L2 / L3 or the origin. Once per epoch it runs
the full loop:

  1  observe cache state          6  choose actions per object:
  2  understand the workload         KEEP / PROMOTE / DEMOTE / COMPRESS / EVICT
  3  predict the future           7  PREFETCH predicted-hot objects
  4  score every object           8  REFRESH hot near-stale objects
  5  compute net value per tier   9  SCALE tiers on a cost-benefit test
                                 10  learn (bandit) · 11 update weights/refs

Only `common` is imported.
"""

from __future__ import annotations

import math
from collections import Counter, deque

import numpy as np

from common import (CacheEntry, CachePolicy, CostConfig, ObjectSpec, RequestOutcome,
                    L1, L2, L3)
from engine.autoscaler import Autoscaler, GhostList
from engine.bandit import LinUCBWeightController
from engine.correlate import CoAccessTracker
from engine.predict import AccessPredictor
from engine.regime import RegimeDetector
from engine.scoring import (ScoreRefs, best_tier, net_value_at_tier, refresh_priority,
                            serve_saving, value)
from common import TieredStore

# six "personalities" spanning the three value families (GDSF · heuristic · ML).
# The bandit picks one per epoch. keys: gdsf | rec | fresh | size | ml
# GDSF term is ~[0,40]; the heuristic/ML terms are ~[0,1] and act as
# refinements. A personality leans on one family without dropping the others.
WEIGHT_ARMS: dict[str, dict[str, float]] = {
    "balanced":   {"gdsf": 1.0, "rec": 2.0, "fresh": 1.5, "size": 1.5, "ml": 3.0},
    "proven":     {"gdsf": 1.0, "rec": 0.6, "fresh": 0.5, "size": 0.8, "ml": 0.8},   # ≈ classical GDSF
    "predictive": {"gdsf": 1.0, "rec": 1.5, "fresh": 1.0, "size": 1.0, "ml": 8.0},   # ML forecast leads
    "recency":    {"gdsf": 1.0, "rec": 7.0, "fresh": 1.0, "size": 1.5, "ml": 2.0},   # recency heuristic leads
    "freshness":  {"gdsf": 1.0, "rec": 1.5, "fresh": 6.0, "size": 1.0, "ml": 2.0},   # protect fresh data
    "lean":       {"gdsf": 1.0, "rec": 1.0, "fresh": 1.0, "size": 6.0, "ml": 1.5},   # size-averse
}

_HORIZON_EPOCHS = 6.0


class CacheMind(CachePolicy):
    name = "CACHE MIND"

    def __init__(
        self,
        l1_bytes: int,
        cost_cfg: CostConfig | None = None,
        *,
        l2_mult: float = 4.0,          # L2 is this much larger than L1 (and ~4x cheaper/byte)
        l3_mult: float = 12.0,         # L3 larger still, near-free
        epoch_seconds: float = 10.0,
        autoscale: bool = True,
        adapt_weights: bool = True,
        admission: bool = True,
        refresh: bool = True,
        prefetch: bool = True,
        compress: bool = True,
        tiering: bool = True,
        prefetch_per_epoch: int = 12,
        proactive_per_epoch: int = 8,
        move_budget: int = 60,          # max promote+demote moves per epoch (anti-thrash)
        seed: int = 0,
    ):
        l1 = int(l1_bytes)
        self.cfg = cost_cfg or CostConfig()
        self.epoch_seconds = epoch_seconds
        self.move_budget = move_budget
        self.autoscale = autoscale
        self.adapt_weights = adapt_weights
        self.admission = admission
        self.refresh_enabled = refresh
        self.prefetch_enabled = prefetch
        self.compress_enabled = compress
        self.tiering = tiering
        self.prefetch_per_epoch = prefetch_per_epoch if prefetch else 0
        self.proactive_per_epoch = proactive_per_epoch if refresh else 0
        self._rng = np.random.default_rng(seed)

        if tiering:
            caps = {L1: l1, L2: int(l1 * l2_mult), L3: int(l1 * l3_mult)}
        else:                                   # ablation: single L1 tier
            caps = {L1: l1, L2: 0, L3: 0}
        self.store = TieredStore(caps)
        self.predictor = AccessPredictor()
        self.correlate = CoAccessTracker()

        self._L = 0.0  # retained for signature compat; aging is now inside the gdsf term
        self._evictions = self._refreshes = 0
        self._promotions = self._demotions = self._prefetches = 0
        self._move_bytes = 0

        self.refs = ScoreRefs()
        self.bandit = LinUCBWeightController(WEIGHT_ARMS, seed=seed)
        self.detector = RegimeDetector()
        self.w = dict(WEIGHT_ARMS["balanced"])
        self.regime = "cold_start"

        self.ghost = GhostList()
        self.scaler = Autoscaler(
            self.cfg, min_bytes=max(caps[L1] // 3, self.cfg.scale_step_bytes),
            max_bytes=l1 * 3, epoch_seconds=epoch_seconds,
        )

        self._demand: Counter[str] = Counter()
        self._recent_miss: deque[str] = deque(maxlen=4000)   # prefetch candidate pool
        self._last_seen: dict[str, float] = {}
        self._reuse_gap_ewma = 60.0
        self._interarrival_ewma = 0.05
        self._prev_t = 0.0

        self._epoch = 0
        self._reset_epoch()
        self._prev_hit_rate = 0.0
        self._rate_max, self._latmax, self._costmax = 1.0, 1.0, 1e-9

        self._refresh_q: list[str] = []
        self._prefetch_q: list[str] = []
        self._feed: deque[dict] = deque(maxlen=48)

    def _reset_epoch(self) -> None:
        self._ep_req = self._ep_hit = self._ep_stale = 0
        self._ep_l1 = self._ep_l2 = self._ep_l3 = 0
        self._ep_evict = self._ep_ghost = self._ep_miss = 0
        self._ep_lat = self._ep_miss_cost = 0.0
        self._ep_moves = 0
        self._ep_keys: Counter[str] = Counter()
        self._stale_seen: set[str] = set()      # stale hits to background-refresh next epoch

    # ------------------------------------------------------------------ #
    #  CachePolicy surface
    # ------------------------------------------------------------------ #
    @property
    def capacity_bytes(self) -> int:
        return self.store.total_cap

    @property
    def used_bytes(self) -> int:
        return self.store.total_used

    @property
    def entries(self) -> int:
        return self.store.count

    def tier_used(self) -> dict[int, int]:
        return {t: self.store.used(t) for t in (L1, L2, L3)}

    def tier_capacity(self) -> dict[int, int]:
        return {t: self.store.cap(t) for t in (L1, L2, L3)}

    def counters(self) -> dict[str, int]:
        return {"evictions": self._evictions, "refreshes": self._refreshes,
                "promotions": self._promotions, "demotions": self._demotions,
                "prefetches": self._prefetches}

    def lookup(self, key: str, now: float) -> CacheEntry | None:
        return self.store.get(key)

    def on_hit(self, entry: CacheEntry, now: float) -> None:
        prev = self._last_seen.get(entry.key)
        if prev is not None:
            self._reuse_gap_ewma += 0.02 * ((now - prev) - self._reuse_gap_ewma)
        self._last_seen[entry.key] = now
        self.predictor.observe(entry.key, now)
        self.correlate.observe(entry.key)

        entry.freq += 1
        entry.hits_since_refresh += 1
        entry.last_access = now
        cms = _cost_ms(entry.spec, self.cfg.latency_usd_per_ms)
        self.refs.observe(entry.spec, entry.freq, cms)

        # opportunistic promote: a warm hit on something that clearly belongs hotter
        if (self.tiering and entry.tier != L1 and self._ep_moves < self.move_budget
                and entry.hits_since_refresh >= 2):
            eh = self._expected_hits(entry.key)
            horizon = _HORIZON_EPOCHS * self.epoch_seconds
            bt, bt_nv = best_tier(entry, eh, horizon, self.cfg)
            cur_nv = net_value_at_tier(entry, entry.tier, 0.0, eh, horizon, self.cfg)
            if (bt != 0 and bt < entry.tier and bt_nv > cur_nv * 1.25
                    and self._make_room(bt, entry.size_bytes, now, exclude=entry.key)):
                self._move(entry.key, bt, now, "hot warm-hit — promote")

    def should_refresh(self, entry: CacheEntry, now: float) -> bool:
        if not entry.is_stale(now):
            return False
        if not self.refresh_enabled:
            return True
        rp = refresh_priority(entry, now, self.refs, self.cfg.latency_usd_per_ms)
        if rp >= 0.6:                       # high drift + high value: block & refresh (rare)
            return True
        if rp >= 0.18:                      # worth refreshing, but not worth making the user wait
            self._stale_seen.add(entry.key)  # -> background refresh next epoch
        else:
            self._note("serve_stale", entry.key, f"low drift risk ({rp:.2f}) — serve stale, skip refresh")
        return False

    def on_refresh(self, entry: CacheEntry, now: float) -> None:
        entry.refreshed_at = now
        entry.hits_since_refresh = 0
        self._refreshes += 1

    def on_admit(self, spec: ObjectSpec, now: float) -> bool:
        g = self.ghost.hit(spec.key)
        if g is not None:
            size, regen = g
            self._ep_ghost += 1
            self.scaler.record_ghost_hit(size, regen)

        self.predictor.observe(spec.key, now)
        self.correlate.observe(spec.key)
        self._recent_miss.append(spec.key)
        est_freq = 1 + int(self._demand.get(spec.key, 0))
        hypo = CacheEntry(spec, now, now, now, freq=est_freq)
        eh = max(self._expected_hits(spec.key), 0.4)
        horizon = _HORIZON_EPOCHS * self.epoch_seconds

        bt, nv = best_tier(hypo, eh, horizon, self.cfg)
        if not self.tiering:
            bt = L1
        if bt == 0:
            self._note("evict", spec.key, "negative net value at every tier — don't cache")
            return False

        # admission control (optional): a lukewarm newcomer may not displace
        # a genuinely hotter L1 occupant — it settles for a colder tier instead.
        # Otherwise admit at the best tier and let eviction sort it out (GDSF-style).
        hv = self._val(hypo, now) if self.admission else None
        for t in range(bt, L3 + 1):
            floor = hv if (t == bt and self.admission and self.tiering) else None
            if self._make_room(t, hypo.size_bytes, now, min_value=floor):
                self.store.place(hypo, t)
                self._last_seen[spec.key] = now
                if t != bt:
                    self._note(f"admit L{t}", spec.key, f"L{bt} holds hotter objects")
                return True
        return False

    def on_request_end(self, o: RequestOutcome, now: float) -> None:
        self._ep_req += 1
        self._ep_keys[o.key] += 1
        self._demand[o.key] += 1
        self._ep_lat += o.latency_ms
        if o.hit:
            self._ep_hit += 1
            if o.hit_tier == L1:
                self._ep_l1 += 1
            elif o.hit_tier == L2:
                self._ep_l2 += 1
            elif o.hit_tier == L3:
                self._ep_l3 += 1
        else:
            self._ep_miss += 1
            self._ep_miss_cost += o.cost_usd
        if o.stale_served:
            self._ep_stale += 1
        if now > self._prev_t:
            self._interarrival_ewma += 0.001 * ((now - self._prev_t) - self._interarrival_ewma)
        self._prev_t = now

    # ------------------------------------------------------------------ #
    #  the epoch loop
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
            c = np.array(list(self._ep_keys.values()), dtype=float)
            p = c / c.sum()
            entropy = float(-(p * np.log(p)).sum() / math.log(distinct))

        feats = {
            "rate": self._ep_req / self._rate_max, "rate_abs": float(self._ep_req),
            "entropy": entropy, "hit_trend": hit_rate - self._prev_hit_rate,
            "miss_cost": (origin_cost / max(self._ep_miss, 1)) / max(self._costmax, 1e-9),
            "pressure": self.store.used(L1) / max(self.store.cap(L1), 1),
            "evict_rate": self._ep_evict / req, "ghost_rate": self._ep_ghost / req,
        }

        # 3  predict
        self.predictor.epoch_decay(self._ep_keys)
        # 10-11  learn + choose weights
        if self.adapt_weights:
            if self._epoch > 0:
                r = self.bandit.reward_from_epoch(
                    hit_rate, norm_latency=avg_lat / max(self._latmax, 1e-9),
                    norm_cost=origin_cost / max(self._costmax, 1e-9))
                self.bandit.learn(r)
            self.w = self.bandit.select(feats)
        self.regime = self.detector.update(feats)
        self.refs.adapt_tau(self._interarrival_ewma, self._reuse_gap_ewma)

        # 4-6  rebalance every resident object to its best tier
        if self.tiering:
            self._rebalance(now)
        # 6  compress marginal keepers
        if self.compress_enabled:
            self._compress_pass(now)
        # 7  prefetch predicted-hot non-resident objects
        self._prefetch_q = self._pick_prefetch(now) if self.prefetch_enabled else []
        # 8  proactive refresh
        self._refresh_q = self._pick_refresh(now)
        # 9  scale
        if self.autoscale:
            self._autoscale(now)

        for k in list(self._demand):
            v = self._demand[k] * 0.5
            if v < 1:
                del self._demand[k]
            else:
                self._demand[k] = v

        self._prev_hit_rate = hit_rate
        self._epoch += 1
        self._reset_epoch()

    def pending_refreshes(self) -> list[str]:
        q, self._refresh_q = self._refresh_q, []
        return q

    def pending_prefetches(self) -> list[str]:
        q, self._prefetch_q = self._prefetch_q, []
        return q

    # ------------------------------------------------------------------ #
    #  internal mechanics
    # ------------------------------------------------------------------ #
    def _cost_ms_per_ms(self) -> float:
        return self.cfg.latency_usd_per_ms

    def _expected_hits(self, key: str) -> float:
        return self.predictor.expected_hits(key, _HORIZON_EPOCHS)

    def _val(self, e: CacheEntry, now: float) -> float:
        ml = self.predictor.p_soon(e.key, now) * self.predictor.confidence(e.key)
        return value(e, now, self.w, self.refs, self._L, self.cfg.latency_usd_per_ms, ml)

    def _sample(self, tier: int, k: int) -> list[CacheEntry]:
        es = self.store.entries(tier)
        if len(es) <= k:
            return es
        idx = self._rng.choice(len(es), size=k, replace=False)
        return [es[i] for i in idx]

    def _make_room(self, tier: int, need: int, now: float, exclude: str | None = None,
                   min_value: float | None = None) -> bool:
        """
        Free `need` bytes in `tier` by demoting/evicting its lowest-value entries.
        If `min_value` is given, refuse to disturb anything worth more than it
        (so a lukewarm newcomer doesn't kick a hot object out of L1).
        """
        if need > self.store.cap(tier):
            return False
        guard = 0
        while not self.store.fits(tier, need) and guard < 200:
            guard += 1
            pool = [e for e in self._sample(tier, 32) if e.key != exclude]
            if not pool:
                return False
            victim = min(pool, key=lambda e: self._val(e, now))
            if min_value is not None and self._val(victim, now) >= min_value:
                return False
            # demote instead of evict if a colder tier still profits. A victim
            # with no prediction history still gets a floor "might be wanted"
            # estimate, so overflow lands in L2/L3 rather than being thrown away.
            eh = max(self._expected_hits(victim.key), 0.3 if victim.freq > 1 or
                     victim.idle_s(now) < 4 * self.epoch_seconds else 0.0)
            if self.tiering and victim.tier < L3:
                for ct in range(victim.tier + 1, L3 + 1):
                    if net_value_at_tier(victim, ct, 0.0, eh,
                                         _HORIZON_EPOCHS * self.epoch_seconds, self.cfg) > 0 \
                       and self.store.fits(ct, victim.size_bytes):
                        self._move(victim.key, ct, now, "demote — cooling")
                        break
                else:
                    self._evict(victim.key, now)
            else:
                self._evict(victim.key, now)
        return self.store.fits(tier, need)

    def _move(self, key: str, to_tier: int, now: float, why: str) -> None:
        e = self.store.get(key)
        if e is None:
            return
        frm = e.tier
        moved = self.store.move(key, to_tier)
        self._move_bytes += moved
        self._ep_moves += 1
        if to_tier < frm:
            self._promotions += 1
        else:
            self._demotions += 1
        self._note(f"L{frm}->L{to_tier}", key, why)

    def _evict(self, key: str, now: float) -> None:
        e = self.store.remove(key)
        if e is None:
            return
        self._evictions += 1
        self._ep_evict += 1
        regen = e.spec.gen_cost_usd + self.cfg.latency_usd_per_ms * e.spec.gen_latency_ms
        self.ghost.add(key, e.full_size_bytes, regen)

    def _rebalance(self, now: float) -> None:
        """
        Conservative: evict clearly-dead entries, and promote clear winners with
        hysteresis (new tier must beat the current one by >25%). Demotion under
        pressure is handled in `_make_room`, not here, to avoid thrashing.
        """
        horizon = _HORIZON_EPOCHS * self.epoch_seconds
        promote: list[tuple[float, str, int]] = []
        for e in list(self.store.all_entries()):
            eh = self._expected_hits(e.key)
            cur_nv = net_value_at_tier(e, e.tier, 0.0, eh, horizon, self.cfg)
            bt, bt_nv = best_tier(e, eh, horizon, self.cfg)
            if bt == 0 and e.tier == L3:
                self._evict(e.key, now)                     # dead & already coldest
            elif bt != 0 and bt < e.tier and bt_nv > cur_nv * 1.25 + 1e-9:
                promote.append((bt_nv - cur_nv, e.key, bt))

        promote.sort(reverse=True)
        for _, key, bt in promote[: self.move_budget]:
            e = self.store.get(key)
            if e is None:
                continue
            if self._make_room(bt, e.size_bytes, now, exclude=key):
                self._move(key, bt, now, "promote — value rose")

    def _compress_pass(self, now: float) -> None:
        for t in (L2, L3):
            if self.store.free(t) > self.store.cap(t) * 0.15:
                continue                        # only bother when the tier is tight
            for e in self.store.entries(t):
                if not e.compressed and e.spec.compressible > 0.3:
                    self.store.set_compressed(e.key, True)
                    self._note("compress", e.key, f"tight {['','L1','L2','L3'][t]} — store compressed")

    def _pick_prefetch(self, now: float) -> list[str]:
        resident = {e.key for e in self.store.all_entries()}
        # _recent_miss alone is nearly useless here: admission caches almost
        # every miss immediately, so by the time this runs those keys are
        # already resident. The ghost list (recently *evicted* keys) is the
        # genuinely non-resident, previously-seen pool PREFETCH needs.
        pool = list(dict.fromkeys(self.ghost.keys() + list(self._recent_miss)))
        cands = self.predictor.hot_candidates(resident, now, self.prefetch_per_epoch, pool=pool)
        for k in cands:
            self._note("prefetch", k, "predicted hot & not resident — warm from origin")

        # correlation: a known partner of something currently hot in L1,
        # even before the predictor independently flags the partner itself.
        if len(cands) < self.prefetch_per_epoch:
            hottest = sorted(self.store.entries(L1), key=lambda e: -e.freq)[:5]
            for e in hottest:
                for partner in self.correlate.partners(e.key, top=1):
                    if partner in resident or partner in cands:
                        continue
                    cands.append(partner)
                    self._note("prefetch", partner, f"co-accessed with hot {e.key}")
                    if len(cands) >= self.prefetch_per_epoch:
                        break
                if len(cands) >= self.prefetch_per_epoch:
                    break
        return cands

    def _pick_refresh(self, now: float) -> list[str]:
        if self.proactive_per_epoch <= 0:
            return []
        # first: objects that took a stale hit this epoch and are worth fixing
        out = [k for k in self._stale_seen if self.store.get(k) is not None]
        # then: hot, near-stale, drift-prone objects, refreshed *before* they're asked for
        scored = [(refresh_priority(e, now, self.refs, self.cfg.latency_usd_per_ms), e.key)
                  for e in self.store.all_entries() if e.key not in self._stale_seen]
        scored.sort(reverse=True)
        out += [k for pr, k in scored[: self.proactive_per_epoch] if pr > 0.25]
        for k in out:
            self._note("refresh", k, "background refresh — no client waits")
        return out[: self.proactive_per_epoch * 4]

    def _autoscale(self, now: float) -> None:
        # --- L1: cost-benefit ghost-list ROI test -----------------------
        cold_cut = now - 2.0 * self.epoch_seconds
        cold = sum(e.size_bytes for e in self.store.entries(L1) if e.last_access < cold_cut)
        new_cap, action, reason = self.scaler.decide(
            capacity=self.store.cap(L1), used=self.store.used(L1),
            evictions=self._ep_evict, requests=self._ep_req, cold_bytes=cold)
        if new_cap != self.store.cap(L1):
            self.store.set_cap(L1, new_cap)
            if action == "shrink":
                self._make_room(L1, 0, now)
            self._note(f"scale_{action}", "L1", reason)

        if not self.tiering:
            return

        # --- L2 / L3: demand-driven, bounded to multiples of the live L1 -
        l1 = self.store.cap(L1)
        step = self.cfg.scale_step_bytes
        bounds = {L2: (2 * l1, 10 * l1), L3: (3 * l1, 30 * l1)}
        tier_hits = {L2: self._ep_l2, L3: self._ep_l3}
        for t in (L2, L3):
            cap, used = self.store.cap(t), self.store.used(t)
            lo, hi = bounds[t]
            fill = used / max(cap, 1)
            pulls_weight = tier_hits[t] / max(self._ep_req, 1) > 0.02
            if fill > 0.9 and pulls_weight and cap + step <= hi:
                self.store.set_cap(t, cap + step)
                self._note("scale_grow", f"L{t}",
                           f"{fill:.0%} full, {tier_hits[t]} hits this epoch — +{step//1024}KB")
            elif fill < 0.5 and cap - step >= lo:
                self._streak = getattr(self, "_streak", {})
                self._streak[t] = self._streak.get(t, 0) + 1
                if self._streak[t] >= 3:
                    self.store.set_cap(t, cap - step)
                    self._streak[t] = 0
                    self._note("scale_shrink", f"L{t}", f"only {fill:.0%} used — release {step//1024}KB")
            else:
                getattr(self, "_streak", {}).pop(t, None)

    def _prefetch_landing_tier(self) -> int:
        return L2

    # -- driver hook: a prefetch/refresh fill landed --------------------- #
    def accept_prefetch(self, spec: ObjectSpec, now: float) -> bool:
        if spec.key in self.store:
            return False
        e = CacheEntry(spec, now, now, now, freq=1)
        tier = self._prefetch_landing_tier()
        if self._make_room(tier, e.size_bytes, now):
            self.store.place(e, tier)
            self._prefetches += 1
            self._last_seen[spec.key] = now
            return True
        return False

    # -- dashboard ----------------------------------------------------- #
    def internals(self) -> dict:
        snap = self.bandit.snapshot()
        patterns = {"periodic": 0, "bursty": 0, "random": 0, "new": 0}
        for e in self.store.entries(L1):
            patterns[self.predictor.access_pattern(e.key)] += 1
        return {
            "weights": snap["weights"], "bandit_arm": snap["arm"],
            "arm_pulls": snap["pulls"], "regime": self.regime,
            "tau_s": round(self.refs.tau_s, 1),
            "tiers": {f"L{t}": {"used": self.store.used(t), "cap": self.store.cap(t),
                                "n": len(self.store.entries(t))} for t in (L1, L2, L3)},
            "decisions": list(self._feed)[-14:],
            "l1_access_patterns": patterns,   # periodic/bursty/random mix of what's in L1 right now
        }

    def _note(self, action: str, key: str, reason: str) -> None:
        self._feed.append({"epoch": self._epoch, "action": action, "key": key, "reason": reason})


def _cost_ms(spec: ObjectSpec, latency_usd_per_ms: float) -> float:
    return spec.gen_latency_ms + spec.gen_cost_usd / max(latency_usd_per_ms, 1e-12)
