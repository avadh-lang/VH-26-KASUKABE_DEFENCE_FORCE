"""End-to-end: the cost model is sane and AACMS actually wins."""

from __future__ import annotations

import pytest

from common import CostConfig
from workload import generate
from benchmark import SimDriver, build_policy


@pytest.fixture(scope="module")
def workload():
    return generate("spike", "api", duration_s=200, seed=0)


def _run(name, wl):
    cfg = CostConfig()
    cap = max(int(wl.working_set_bytes * 0.15), cfg.scale_step_bytes)
    return SimDriver(build_policy(name, cap, cfg, 10.0), wl, cfg, epoch_seconds=10.0).run()


def test_cost_model_components_are_nonnegative_and_add_up():
    cfg = CostConfig()
    assert cfg.memory_usd(1024 ** 3, 3600) == pytest.approx(cfg.mem_usd_per_gb_hour)
    assert cfg.latency_usd(100) > 0


def test_driver_produces_one_snapshot_per_epoch(workload):
    res = _run("GDSF", workload)
    assert len(res.snapshots) >= int(workload.duration_s / 10) - 1
    assert res.summary["requests"] == len(workload)


def test_hit_rate_and_costs_in_valid_ranges(workload):
    for name in ("LRU", "LFU", "GDS", "GDSF", "AACMS"):
        s = _run(name, workload).summary
        assert 0.0 <= s["hit_rate"] <= 1.0
        assert s["cost_total"] > 0
        assert s["cost_origin"] >= 0 and s["cost_memory"] >= 0


def test_aacms_beats_every_baseline_on_cost(workload):
    aacms = _run("AACMS", workload).summary["cost_total"]
    for base in ("LRU", "LFU", "GDS", "GDSF"):
        assert aacms < _run(base, workload).summary["cost_total"], base


def test_aacms_fixed_capacity_is_at_least_as_good_as_gdsf(workload):
    fixed = _run("AACMS-fixed", workload).summary["cost_total"]
    gdsf = _run("GDSF", workload).summary["cost_total"]
    assert fixed <= gdsf * 1.02          # never materially worse than the best baseline


def test_autoscaler_stays_within_its_ceiling(workload):
    res = _run("AACMS", workload)
    start = res.snapshots[0].capacity_bytes
    assert res.summary["final_capacity_bytes"] <= start * 3 + 1
