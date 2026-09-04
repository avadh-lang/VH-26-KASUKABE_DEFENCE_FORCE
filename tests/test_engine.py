"""AACMS engine: the pieces the jury will poke at."""

from __future__ import annotations

import numpy as np

from common import CostConfig
from engine import AACMSCache, WEIGHT_ARMS, WEIGHT_KEYS
from engine.scoring import ScoreRefs, value, refresh_priority
from engine.bandit import LinUCBWeightController
from engine.autoscaler import Autoscaler, GhostList


# ---- value score ---------------------------------------------------------- #
def test_value_ranks_expensive_small_hot_above_cheap_big_cold(mk_spec):
    refs = ScoreRefs()
    now = 100.0
    from common import CacheEntry
    hot = CacheEntry(mk_spec("hot", size=500, lat=900.0), 0, now, 0, freq=20)
    cold = CacheEntry(mk_spec("cold", size=50_000, lat=5.0), 0, 0.0, 0, freq=1)
    w = WEIGHT_ARMS["balanced"]
    assert value(hot, now, w, refs, 0.0, 2e-6) > value(cold, now, w, refs, 0.0, 2e-6)


def test_all_weight_arms_cover_every_key():
    for arm, w in WEIGHT_ARMS.items():
        assert set(w) == set(WEIGHT_KEYS), arm


# ---- admission control -------------------------------------------------- #
def test_admission_rejects_low_value_object_when_full(mk_spec):
    c = AACMSCache(capacity_bytes=5_000, autoscale=False)
    # fill with valuable small expensive objects
    for i in range(5):
        s = mk_spec(f"good{i}", size=1_000, lat=800.0, cost=5e-3)
        c.on_admit(s, now=0.0)
        for _ in range(10):
            e = c.lookup(f"good{i}", 1.0)
            if e:
                c.on_hit(e, 1.0)
    # a big cheap one-hit-wonder should not evict the working set
    admitted = c.on_admit(mk_spec("junk", size=1_000, lat=2.0, cost=0.0), now=2.0)
    assert admitted is False
    assert c.lookup("junk", 2.0) is None


def test_cold_cache_admits_freely(mk_spec):
    c = AACMSCache(capacity_bytes=100_000, autoscale=False)
    assert c.on_admit(mk_spec("x", size=1_000), now=0.0) is True
    assert c.entries == 1


# ---- bandit -------------------------------------------------------------- #
def test_bandit_learns_to_prefer_the_rewarding_arm():
    ctrl = LinUCBWeightController(WEIGHT_ARMS, alpha=0.3, seed=1)
    feats = {k: 0.5 for k in
             ("rate", "entropy", "hit_trend", "miss_cost", "pressure", "evict_rate", "ghost_rate")}
    target = "cost_first"
    for _ in range(60):
        w = ctrl.select(feats)
        reward = 1.0 if ctrl.active.name == target else 0.0
        ctrl.learn(reward)
    pulls = ctrl.snapshot()["pulls"]
    assert pulls[target] == max(pulls.values())


def test_bandit_weights_are_a_valid_vector():
    ctrl = LinUCBWeightController(WEIGHT_ARMS, seed=0)
    w = ctrl.select({k: 0.4 for k in ("rate", "entropy", "hit_trend", "miss_cost",
                                      "pressure", "evict_rate", "ghost_rate")})
    assert set(w) == set(WEIGHT_KEYS)
    assert all(v >= 0 for v in w.values())


# ---- autoscaler ------------------------------------------------------- #
def test_autoscaler_grows_when_ghost_hits_are_worth_more_than_ram():
    cfg = CostConfig(mem_usd_per_gb_hour=0.1, scale_step_bytes=1_000_000)
    a = Autoscaler(cfg, min_bytes=1_000_000, max_bytes=10_000_000, epoch_seconds=10.0)
    for _ in range(50):
        a.record_ghost_hit(size=20_000, regen_usd_equiv=0.01)   # lots of costly ghost hits
    new_cap, action, _ = a.decide(capacity=2_000_000, used=2_000_000, evictions=500, requests=1000)
    assert action == "grow"
    assert new_cap > 2_000_000


def test_autoscaler_holds_when_nothing_is_under_pressure():
    cfg = CostConfig(scale_step_bytes=1_000_000)
    a = Autoscaler(cfg, min_bytes=1_000_000, max_bytes=10_000_000, epoch_seconds=10.0)
    new_cap, action, _ = a.decide(capacity=4_000_000, used=3_900_000,
                                  evictions=0, requests=1000, cold_bytes=0)
    assert action == "hold"
    assert new_cap == 4_000_000


def test_ghost_list_is_bounded():
    g = GhostList(capacity=100)
    for i in range(500):
        g.add(f"k{i}", 1_000, 0.001)
    assert len(g) == 100
    assert g.hit("k499") is not None            # most recent kept
    assert g.hit("k0") is None                  # oldest dropped


# ---- refresh ---------------------------------------------------------- #
def test_refresh_priority_zero_when_fresh(mk_spec):
    from common import CacheEntry
    refs = ScoreRefs()
    e = CacheEntry(mk_spec("x", ttl=1000.0, vol=0.5), 0.0, 0.0, 0.0, freq=5)
    assert refresh_priority(e, now=10.0, refs=refs, latency_usd_per_ms=2e-6) == 0.0


def test_proactive_refresh_targets_hot_volatile_stale_entries(mk_spec):
    c = AACMSCache(capacity_bytes=100_000, epoch_seconds=10.0, autoscale=False)
    c.on_admit(mk_spec("hot", ttl=5.0, vol=0.5, lat=50.0), now=0.0)
    for _ in range(30):
        c.on_hit(c.lookup("hot", 1.0), now=1.0)
    c.maintenance(now=20.0)                     # entry is now well past its 5s TTL
    assert "hot" in c.pending_refreshes()
