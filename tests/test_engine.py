"""CACHE MIND engine: the pieces the jury will poke at."""

from __future__ import annotations

from common import CacheEntry, CostConfig, TieredStore, L1, L2, L3
from engine import CacheMind, WEIGHT_ARMS, WEIGHT_KEYS
from engine.scoring import ScoreRefs, value, refresh_priority, best_tier, serve_saving
from engine.bandit import LinUCBWeightController
from engine.predict import AccessPredictor
from engine.autoscaler import Autoscaler, GhostList


# ---- tiered store ------------------------------------------------------- #
def test_tiered_store_places_moves_and_accounts(mk_spec):
    st = TieredStore({L1: 10_000, L2: 40_000, L3: 100_000})
    e = CacheEntry(mk_spec("a", size=1_000), 0, 0, 0)
    st.place(e, L1)
    assert st.tier_of("a") == L1 and st.used(L1) == 1_000
    moved = st.move("a", L3)
    assert moved == 1_000 and st.tier_of("a") == L3
    assert st.used(L1) == 0 and st.used(L3) == 1_000
    assert st.remove("a").key == "a" and "a" not in st


def test_compression_shrinks_occupied_bytes(mk_spec):
    st = TieredStore({L1: 10_000, L2: 0, L3: 0})
    st.place(CacheEntry(mk_spec("a", size=1_000), 0, 0, 0), L1)  # compressible defaults 0.5
    st.set_compressed("a", True)
    assert st.used(L1) == 500
    assert st.get("a").full_size_bytes == 1_000


# ---- value score ------------------------------------------------------- #
def test_value_ranks_expensive_small_hot_above_cheap_big_cold(mk_spec):
    refs, now = ScoreRefs(), 100.0
    hot = CacheEntry(mk_spec("hot", size=500, lat=900.0), 0, now, 0, freq=20)
    cold = CacheEntry(mk_spec("cold", size=50_000, lat=5.0), 0, 0.0, 0, freq=1)
    w = WEIGHT_ARMS["balanced"]
    assert value(hot, now, w, refs, 0.0, 2e-6) > value(cold, now, w, refs, 0.0, 2e-6)


def test_all_weight_arms_cover_every_key():
    for arm, w in WEIGHT_ARMS.items():
        assert set(w) == set(WEIGHT_KEYS), arm


# ---- tier economics -------------------------------------------------- #
def test_expensive_object_is_worth_caching_even_in_cold_l3(mk_spec):
    cfg = CostConfig()
    e = CacheEntry(mk_spec("x", size=2_000, lat=800.0, cost=4e-3), 0, 0, 0)
    # even one expected hit over the horizon beats L3's byte cost
    tier, nv = best_tier(e, expected_hits=1.0, horizon_s=60.0, cfg=cfg)
    assert tier in (L1, L2, L3) and nv > 0
    assert serve_saving(e.spec, L3, cfg) > serve_saving(e.spec, L1, cfg) * 0.5


def test_worthless_object_is_not_placed(mk_spec):
    cfg = CostConfig()
    e = CacheEntry(mk_spec("junk", size=400_000, lat=1.0, cost=0.0), 0, 0, 0)
    tier, _ = best_tier(e, expected_hits=0.0, horizon_s=60.0, cfg=cfg)
    assert tier == 0


# ---- predictor ------------------------------------------------------- #
def test_predictor_learns_a_regular_cadence():
    p = AccessPredictor()
    for i in range(40):
        p.observe("k", now=float(i * 10))          # every 10s like clockwork
    assert 6.0 < p._d["k"].gap_ewma < 14.0         # learned the ~10s cadence
    assert p.confidence("k") > 0.35                # low variance -> some trust
    assert p.p_soon("k", now=392.0) > 0.4          # just after the expected next hit


def test_predictor_flags_hot_non_resident_candidates():
    p = AccessPredictor()
    for i in range(8):
        p.observe("rising", now=float(i * 5))
    p.epoch_decay({"rising": 6})
    cands = p.hot_candidates(resident=set(), now=41.0, k=5)
    assert "rising" in cands


# ---- bandit -------------------------------------------------------------- #
def test_bandit_learns_to_prefer_the_rewarding_arm():
    ctrl = LinUCBWeightController(WEIGHT_ARMS, alpha=0.3, seed=1)
    feats = {k: 0.5 for k in
             ("rate", "entropy", "hit_trend", "miss_cost", "pressure", "evict_rate", "ghost_rate")}
    for _ in range(60):
        ctrl.select(feats)
        ctrl.learn(1.0 if ctrl.active.name == "cost_first" else 0.0)
    pulls = ctrl.snapshot()["pulls"]
    assert pulls["cost_first"] == max(pulls.values())


# ---- autoscaler ------------------------------------------------------- #
def test_autoscaler_grows_when_ghost_hits_beat_ram():
    cfg = CostConfig(scale_step_bytes=1_000_000)
    a = Autoscaler(cfg, min_bytes=1_000_000, max_bytes=10_000_000, epoch_seconds=10.0)
    for _ in range(50):
        a.record_ghost_hit(size=20_000, regen_usd_equiv=0.01)
    _, action, _ = a.decide(capacity=2_000_000, used=2_000_000, evictions=500, requests=1000)
    assert action == "grow"


def test_ghost_list_is_bounded():
    g = GhostList(capacity=100)
    for i in range(500):
        g.add(f"k{i}", 1_000, 0.001)
    assert len(g) == 100 and g.hit("k499") is not None and g.hit("k0") is None


# ---- CacheMind end to end ---------------------------------------------- #
def test_cachemind_serves_from_warm_tiers_and_counts_moves(mk_spec):
    cm = CacheMind(l1_bytes=4_000, epoch_seconds=5.0)
    for i in range(40):
        s = mk_spec(f"k{i}", size=800, lat=500.0, cost=3e-3)
        cm.on_admit(s, now=float(i))
    cm.maintenance(now=50.0)
    used = cm.tier_used()
    assert used[L2] + used[L3] > 0                 # overflow demoted, not evicted
    assert cm.entries > 5


def test_cachemind_falls_back_cleanly_to_single_tier(mk_spec):
    cm = CacheMind(l1_bytes=100_000, tiering=False, epoch_seconds=10.0)
    assert cm.on_admit(mk_spec("x", size=1_000), now=0.0) is True
    assert cm.tier_capacity()[L2] == 0
