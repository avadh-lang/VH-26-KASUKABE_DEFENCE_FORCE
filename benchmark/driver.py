"""
SimDriver — the request loop.

It owns the clock and the cost accounting so every policy is measured on
exactly the same rules. Policies never see money; they only make cache
decisions. The driver:

  * serves each request (hit / stale-hit / blocking-refresh / miss)
  * charges origin $, latency $ and memory $ via the shared CostConfig
  * every `epoch_seconds` of sim time: runs policy.maintenance(), drains
    proactive refreshes, and emits an EpochSnapshot
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from common import CachePolicy, CostConfig, EpochSnapshot, RequestOutcome
from workload import Workload


@dataclass
class RunResult:
    policy: str
    scenario: str
    profile: str
    snapshots: list[EpochSnapshot]
    summary: dict = field(default_factory=dict)

    def series(self, field_name: str) -> list[float]:
        return [getattr(s, field_name) for s in self.snapshots]


class SimDriver:
    def __init__(
        self,
        policy: CachePolicy,
        workload: Workload,
        cost_cfg: CostConfig | None = None,
        *,
        epoch_seconds: float = 10.0,
    ):
        self.policy = policy
        self.wl = workload
        self.cfg = cost_cfg or CostConfig()
        self.epoch_seconds = epoch_seconds
        self.hit_latency_ms = float(getattr(policy, "hit_latency_ms", 0.5))

    def run(self) -> RunResult:
        catalog = self.wl.catalog
        pol = self.policy
        cfg = self.cfg
        eps = self.epoch_seconds

        # cumulative money
        c_origin = c_latency = c_memory = 0.0
        # whole-run tallies
        tot_req = tot_hit = tot_stale = tot_ref = tot_evict = 0
        run_lat: list[float] = []

        # epoch-local
        ep = 0
        ep_req = ep_hit = ep_stale = 0
        ep_lat: list[float] = []
        ep_start_ref = (c_origin, c_latency, c_memory)
        ev0 = _evictions(pol)
        rf0 = _refreshes(pol)
        snapshots: list[EpochSnapshot] = []

        next_epoch_t = eps
        last_t = 0.0

        def close_epoch(t_now: float) -> None:
            nonlocal ep, ep_req, ep_hit, ep_stale, ep_lat, ep_start_ref, ev0, rf0
            nonlocal c_memory
            # memory cost for the epoch (on current residency)
            c_memory_add = cfg.memory_usd(pol.used_bytes, eps)
            c_memory += c_memory_add

            pol.maintenance(t_now)
            _drain_proactive(pol, cfg, t_now, add_origin=lambda x: _bump(x))

            req = max(ep_req, 1)
            p95 = float(np.percentile(ep_lat, 95)) if ep_lat else 0.0
            avg = float(np.mean(ep_lat)) if ep_lat else 0.0
            intern = pol.internals()
            snap = EpochSnapshot(
                policy=pol.name, epoch=ep, t_sim=t_now,
                requests=ep_req,
                hit_rate=ep_hit / req,
                stale_rate=ep_stale / req,
                avg_latency_ms=avg, p95_latency_ms=p95,
                cost_total=c_origin + c_latency + c_memory,
                cost_origin=c_origin, cost_latency=c_latency, cost_memory=c_memory,
                capacity_bytes=pol.capacity_bytes, used_bytes=pol.used_bytes,
                entries=pol.entries,
                evictions=_evictions(pol) - ev0,
                refreshes=_refreshes(pol) - rf0,
                weights=intern.get("weights"),
                regime=intern.get("regime"),
                bandit_arm=intern.get("bandit_arm"),
            )
            snapshots.append(snap)
            ep += 1
            ep_req = ep_hit = ep_stale = 0
            ep_lat = []
            ev0 = _evictions(pol)
            rf0 = _refreshes(pol)

        def _bump(x: float) -> None:
            nonlocal c_origin
            c_origin += x

        for t, key in self.wl.requests:
            while t >= next_epoch_t:
                close_epoch(next_epoch_t)
                next_epoch_t += eps
            last_t = t
            spec = catalog[key]
            entry = pol.lookup(key, t)

            if entry is None:
                latency = spec.gen_latency_ms
                cost = spec.gen_cost_usd
                admitted = pol.on_admit(spec, t)
                hit = False
                stale_served = refreshed = True
                action = "miss_fill" if admitted else "miss_no_admit"
            elif entry.is_stale(t) and pol.should_refresh(entry, t):
                latency = spec.gen_latency_ms
                cost = spec.gen_cost_usd
                pol.on_refresh(entry, t)
                pol.on_hit(entry, t)
                hit, stale_served, refreshed, action = True, False, True, "refresh"
            elif entry.is_stale(t):
                latency = self.hit_latency_ms
                cost = 0.0
                pol.on_hit(entry, t)
                hit, stale_served, refreshed, action = True, True, False, "stale_hit"
            else:
                latency = self.hit_latency_ms
                cost = 0.0
                pol.on_hit(entry, t)
                hit, stale_served, refreshed, action = True, False, False, "hit"

            c_origin += cost
            lat_cost = cfg.latency_usd(latency)
            c_latency += lat_cost

            outcome = RequestOutcome(
                key=key, hit=hit, stale_served=stale_served, refreshed=refreshed,
                latency_ms=latency, cost_usd=cost + lat_cost, action=action,
            )
            pol.on_request_end(outcome, t)

            ep_req += 1
            tot_req += 1
            ep_lat.append(latency)
            run_lat.append(latency)
            if hit:
                ep_hit += 1
                tot_hit += 1
            if stale_served:
                ep_stale += 1
                tot_stale += 1
            if refreshed:
                tot_ref += 1

        close_epoch(max(last_t, next_epoch_t))

        tot_evict = _evictions(pol)
        summary = {
            "requests": tot_req,
            "hit_rate": tot_hit / max(tot_req, 1),
            "stale_rate": tot_stale / max(tot_req, 1),
            "avg_latency_ms": float(np.mean(run_lat)) if run_lat else 0.0,
            "p95_latency_ms": float(np.percentile(run_lat, 95)) if run_lat else 0.0,
            "p99_latency_ms": float(np.percentile(run_lat, 99)) if run_lat else 0.0,
            "cost_total": c_origin + c_latency + c_memory,
            "cost_origin": c_origin,
            "cost_latency": c_latency,
            "cost_memory": c_memory,
            "evictions": tot_evict,
            "refreshes": _refreshes(pol),
            "final_capacity_bytes": pol.capacity_bytes,
            "peak_used_bytes": max((s.used_bytes for s in snapshots), default=0),
        }
        return RunResult(
            policy=pol.name, scenario=self.wl.scenario, profile=self.wl.profile,
            snapshots=snapshots, summary=summary,
        )


# -- small helpers so baselines and AACMS expose the same numbers --------- #
def _evictions(pol: CachePolicy) -> int:
    return int(getattr(pol, "evictions", 0) or getattr(pol, "_evictions", 0))


def _refreshes(pol: CachePolicy) -> int:
    return int(getattr(pol, "refreshes", 0) or getattr(pol, "_refreshes", 0))


def _drain_proactive(pol: CachePolicy, cfg: CostConfig, now: float, add_origin) -> None:
    keys = pol.pending_refreshes()
    for k in keys:
        e = pol.lookup(k, now)
        if e is None:
            continue
        add_origin(e.spec.gen_cost_usd * cfg.refresh_discount)
        pol.on_refresh(e, now)
