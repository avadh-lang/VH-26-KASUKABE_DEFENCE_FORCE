"""Workload generation: deterministic and scenario-faithful."""

from __future__ import annotations

import numpy as np

from workload import generate, build_catalog, SCENARIOS, PROFILES


def test_same_seed_same_stream():
    a = generate("steady", "api", duration_s=120, seed=7)
    b = generate("steady", "api", duration_s=120, seed=7)
    assert a.requests == b.requests
    assert a.meta["total_requests"] == b.meta["total_requests"]


def test_different_seed_different_stream():
    a = generate("steady", "api", duration_s=120, seed=1)
    b = generate("steady", "api", duration_s=120, seed=2)
    assert a.requests != b.requests


def test_every_scenario_and_profile_generates_traffic():
    for scen in SCENARIOS:
        for prof in PROFILES:
            wl = generate(scen, prof, duration_s=120, seed=0)
            assert len(wl) > 0
            assert all(k in wl.catalog for _, k in wl.requests)


def test_requests_are_time_sorted():
    wl = generate("spike", "api", duration_s=200, seed=0)
    ts = [t for t, _ in wl.requests]
    assert ts == sorted(ts)


def test_spike_scenario_has_a_rate_surge():
    wl = generate("spike", "api", duration_s=400, seed=0)
    dur = wl.duration_s
    per_sec = np.zeros(int(dur) + 1)
    for t, _ in wl.requests:
        per_sec[int(t)] += 1
    quiet = per_sec[: int(0.3 * dur)].mean()
    surge = per_sec[int(0.48 * dur): int(0.60 * dur)].mean()
    assert surge > 1.8 * quiet


def test_catalog_has_expensive_and_cheap_objects():
    cat = build_catalog("api", n=2000, seed=0)
    exp = [s for s in cat.values() if "expensive" in s.tags]
    cheap = [s for s in cat.values() if "cheap" in s.tags]
    assert len(exp) > 100 and len(cheap) > 100
    assert np.mean([s.gen_latency_ms for s in exp]) > np.mean([s.gen_latency_ms for s in cheap])


def test_profiles_are_actually_distinct():
    api = build_catalog("api", n=1500, seed=0)
    rec = build_catalog("recsys", n=1500, seed=0)
    api_sz = np.median([s.size_bytes for s in api.values()])
    rec_sz = np.median([s.size_bytes for s in rec.values()])
    assert rec_sz > 3 * api_sz            # recsys objects are much larger
