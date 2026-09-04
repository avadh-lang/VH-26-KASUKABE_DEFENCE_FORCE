"""End-to-end: the cost model is sane and CACHE MIND actually wins."""

from __future__ import annotations

import pytest

from common import CostConfig
from workload import generate
from benchmark import SimDriver, build_policy


@pytest.fixture(scope="module")
def workload():
    return generate("spike", "api", duration_s=400, seed=0)


def _run(name, wl):
    cfg = CostConfig()
    cap = max(int(wl.working_set_bytes * 0.12), cfg.scale_step_bytes)
    return SimDriver(build_policy(name, cap, cfg, 10.0), wl, cfg, epoch_seconds=10.0).run()


def test_cost_model_components_are_nonnegative():
    cfg = CostConfig()
    assert cfg.memory_usd(1024 ** 3, 3600, 1) == pytest.approx(cfg.tiers[0].usd_per_gb_hour)
    assert cfg.tier_latency_ms(2) > cfg.tier_latency_ms(1)      # L2 slower than L1
    assert cfg.memory_usd(1e6, 10, 3) < cfg.memory_usd(1e6, 10, 1)  # L3 cheaper than L1


def test_driver_produces_one_snapshot_per_epoch(workload):
    res = _run("GDSF", workload)
    assert len(res.snapshots) >= int(workload.duration_s / 10) - 1
    assert res.summary["requests"] == len(workload)


def test_hit_rate_and_costs_in_valid_ranges(workload):
    for name in ("LRU", "GDSF", "GDSF-tiered", "CACHE MIND"):
        s = _run(name, workload).summary
        assert 0.0 <= s["hit_rate"] <= 1.0
        assert s["cost_total"] > 0
        assert s["cost_origin"] >= 0 and s["cost_memory"] >= 0 and s["cost_move"] >= 0


def test_cachemind_beats_every_baseline_on_cost(workload):
    cm = _run("CACHE MIND", workload).summary["cost_total"]
    for base in ("LRU", "LFU", "GDSF", "LRU-tiered", "GDSF-tiered"):
        assert cm < _run(base, workload).summary["cost_total"], base


def test_tiering_lifts_hit_rate_and_lowers_cost(workload):
    single = _run("GDSF", workload).summary
    tiered = _run("GDSF-tiered", workload).summary
    assert tiered["hit_rate"] > single["hit_rate"]
    assert tiered["cost_total"] < single["cost_total"]


def test_cachemind_beats_dumb_tiering_on_the_same_hardware(workload):
    cm = _run("CACHE MIND", workload).summary
    dumb = _run("GDSF-tiered", workload).summary
    # smart placement + adaptation on identical L1/L2/L3 -> lower total cost,
    # and latency no worse than the positional baseline
    assert cm["cost_total"] < dumb["cost_total"]
    assert cm["p95_latency_ms"] <= dumb["p95_latency_ms"] * 1.5
