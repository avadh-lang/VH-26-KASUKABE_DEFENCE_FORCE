"""Baseline policies behave exactly as their textbook definitions say."""

from __future__ import annotations

from baselines import LRUCache, LFUCache, GDSCache, GDSFCache


def test_lru_evicts_least_recently_used(mk_spec):
    c = LRUCache(capacity_bytes=3_000)          # room for 3 x 1000B
    for k in ("a", "b", "c"):
        c.on_admit(mk_spec(k), now=0.0)
    c.on_hit(c.lookup("a", 1.0), now=1.0)        # touch a -> b is now oldest
    c.on_admit(mk_spec("d"), now=2.0)           # must evict one

    assert c.lookup("b", 2.0) is None           # b was least recently used
    assert c.lookup("a", 2.0) is not None
    assert c.lookup("d", 2.0) is not None


def test_lfu_evicts_least_frequently_used(mk_spec):
    c = LFUCache(capacity_bytes=3_000)
    for k in ("a", "b", "c"):
        c.on_admit(mk_spec(k), now=0.0)
    for _ in range(5):
        c.on_hit(c.lookup("a", 1.0), now=1.0)
    for _ in range(2):
        c.on_hit(c.lookup("b", 1.0), now=1.0)
    c.on_admit(mk_spec("d"), now=2.0)

    assert c.lookup("c", 2.0) is None           # freq 1 -> evicted
    assert c.lookup("a", 2.0) is not None


def test_size_accounting_never_exceeds_capacity(mk_spec):
    c = LRUCache(capacity_bytes=10_000)
    for i in range(100):
        c.on_admit(mk_spec(f"k{i}", size=1_500), now=float(i))
        assert c.used_bytes <= c.capacity_bytes


def test_oversized_object_is_rejected(mk_spec):
    c = LRUCache(capacity_bytes=1_000)
    assert c.on_admit(mk_spec("big", size=5_000), now=0.0) is False
    assert c.entries == 0


def test_gdsf_prefers_expensive_small_objects(mk_spec):
    c = GDSFCache(capacity_bytes=2_000)
    c.on_admit(mk_spec("cheap_big", size=1_000, lat=5.0), now=0.0)
    c.on_admit(mk_spec("pricey_small", size=1_000, lat=900.0), now=0.0)
    c.on_admit(mk_spec("newcomer", size=1_000, lat=50.0), now=1.0)   # evicts one

    assert c.lookup("pricey_small", 1.0) is not None       # kept: high cost/size
    assert c.lookup("cheap_big", 1.0) is None


def test_gds_inflation_is_monotone(mk_spec):
    c = GDSCache(capacity_bytes=2_000)
    c.on_admit(mk_spec("a", lat=800.0), now=0.0)
    c.on_admit(mk_spec("b", lat=5.0), now=0.0)
    c.on_admit(mk_spec("c", lat=5.0), now=1.0)   # evict b (low H)
    l1 = c._L
    c.on_admit(mk_spec("d", lat=5.0), now=2.0)
    assert c._L >= l1
