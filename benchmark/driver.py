"""
SimDriver — the request loop.

Owns the clock and all money accounting, so every policy is measured on
identical rules. Policies never see dollars; they only make cache decisions.

Per request it serves a hit from whichever tier holds the object
(`entry.tier`), a blocking refresh, or a full miss to the origin. Each epoch it
charges per-tier memory, runs `policy.maintenance()`, then drains the policy's
proactive **refresh** and **prefetch** queues (origin $ charged, no client
latency) and emits an `EpochSnapshot`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from common import CachePolicy, CostConfig, EpochSnapshot, RequestOutcome, L1, L2, L3
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


def _cnt(pol: CachePolicy) -> dict:
    try:
        return pol.counters()
    except Exception:
        return {"evictions": 0, "refreshes": 0, "promotions": 0, "demotions": 0, "prefetches": 0}


class SimDriver:
    def __init__(self, policy: CachePolicy, workload: Workload,
                 cost_cfg: CostConfig | None = None, *, epoch_seconds: float = 10.0):
        self.policy = policy
        self.wl = workload
        self.cfg = cost_cfg or CostConfig()
        self.epoch_seconds = epoch_seconds

    def run(self) -> RunResult:
        catalog = self.wl.catalog
        pol = self.policy
        cfg = self.cfg
        eps = self.epoch_seconds

        c_origin = c_latency = c_memory = c_move = 0.0
        tot_req = tot_hit = tot_stale = 0
        tot = {L1: 0, L2: 0, L3: 0}
        run_lat: list[float] = []
        snapshots: list[EpochSnapshot] = []

        ep = 0
        ep_req = ep_hit = ep_stale = 0
        ep_tier = {L1: 0, L2: 0, L3: 0}
        ep_lat: list[float] = []
        base_cnt = _cnt(pol)
        next_epoch_t = eps
        last_t = 0.0

        def close_epoch(t_now: float) -> None:
            nonlocal ep, ep_req, ep_hit, ep_stale, ep_lat, ep_tier, base_cnt
            nonlocal c_memory, c_origin, c_move

            tu = pol.tier_used()
            for tier in (L1, L2, L3):
                c_memory += cfg.memory_usd(tu.get(tier, 0), eps, tier)

            pol.maintenance(t_now)

            # drain proactive refreshes (regenerate resident stale entries)
            for k in pol.pending_refreshes():
                e = pol.lookup(k, t_now)
                if e is not None:
                    c_origin += e.spec.gen_cost_usd * cfg.refresh_discount
                    pol.on_refresh(e, t_now)
            # drain prefetches (warm predicted-hot non-resident objects)
            for k in pol.pending_prefetches():
                spec = catalog.get(k)
                if spec is None:
                    continue
                c_origin += spec.gen_cost_usd * cfg.refresh_discount
                acc = getattr(pol, "accept_prefetch", None)
                if acc:
                    acc(spec, t_now)

            cur = _cnt(pol)
            mv = getattr(pol, "_move_bytes", 0) - getattr(pol, "_move_bytes_reported", 0)
            if mv > 0:
                c_move += cfg.move_usd(mv)
                pol._move_bytes_reported = getattr(pol, "_move_bytes", 0)

            req = max(ep_req, 1)
            intern = pol.internals()
            tcap = pol.tier_capacity()
            snap = EpochSnapshot(
                policy=pol.name, epoch=ep, t_sim=t_now, requests=ep_req,
                hit_rate=ep_hit / req, stale_rate=ep_stale / req,
                l1_rate=ep_tier[L1] / req, l2_rate=ep_tier[L2] / req, l3_rate=ep_tier[L3] / req,
                avg_latency_ms=float(np.mean(ep_lat)) if ep_lat else 0.0,
                p95_latency_ms=float(np.percentile(ep_lat, 95)) if ep_lat else 0.0,
                cost_total=c_origin + c_latency + c_memory + c_move,
                cost_origin=c_origin, cost_latency=c_latency, cost_memory=c_memory,
                cost_move=c_move,
                capacity_bytes=pol.capacity_bytes, used_bytes=pol.used_bytes, entries=pol.entries,
                evictions=cur["evictions"] - base_cnt["evictions"],
                refreshes=cur["refreshes"] - base_cnt["refreshes"],
                promotions=cur.get("promotions", 0) - base_cnt.get("promotions", 0),
                demotions=cur.get("demotions", 0) - base_cnt.get("demotions", 0),
                prefetches=cur.get("prefetches", 0) - base_cnt.get("prefetches", 0),
                l1_used=tu.get(L1, 0), l2_used=tu.get(L2, 0), l3_used=tu.get(L3, 0),
                l1_cap=tcap.get(L1, 0), l2_cap=tcap.get(L2, 0), l3_cap=tcap.get(L3, 0),
                weights=intern.get("weights"), regime=intern.get("regime"),
                bandit_arm=intern.get("bandit_arm"),
            )
            snapshots.append(snap)
            ep += 1
            ep_req = ep_hit = ep_stale = 0
            ep_tier = {L1: 0, L2: 0, L3: 0}
            ep_lat = []
            base_cnt = cur

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
                hit, tier, stale_served, refreshed = False, 0, False, True
                action = "miss_fill" if admitted else "miss_no_admit"
            elif entry.is_stale(t) and pol.should_refresh(entry, t):
                latency = spec.gen_latency_ms
                cost = spec.gen_cost_usd
                pol.on_refresh(entry, t)
                pol.on_hit(entry, t)
                hit, tier, stale_served, refreshed, action = True, entry.tier, False, True, "refresh"
            else:
                tier = entry.tier
                latency = cfg.tier_latency_ms(tier) + (cfg.decompress_latency_ms if entry.compressed else 0.0)
                cost = 0.0
                stale_served = entry.is_stale(t)
                pol.on_hit(entry, t)
                hit, refreshed = True, False
                action = f"l{tier}_hit" if not stale_served else "stale_hit"

            c_origin += cost
            lat_cost = cfg.latency_usd(latency)
            c_latency += lat_cost
            pol.on_request_end(RequestOutcome(
                key=key, hit=hit, hit_tier=tier, stale_served=stale_served, refreshed=refreshed,
                latency_ms=latency, cost_usd=cost + lat_cost, action=action), t)

            ep_req += 1
            tot_req += 1
            ep_lat.append(latency)
            run_lat.append(latency)
            if hit:
                ep_hit += 1
                tot_hit += 1
                ep_tier[tier] += 1
                tot[tier] += 1
            if stale_served:
                ep_stale += 1
                tot_stale += 1

        close_epoch(max(last_t, next_epoch_t))

        cur = _cnt(pol)
        summary = {
            "requests": tot_req,
            "hit_rate": tot_hit / max(tot_req, 1),
            "stale_rate": tot_stale / max(tot_req, 1),
            "l1_rate": tot[L1] / max(tot_req, 1), "l2_rate": tot[L2] / max(tot_req, 1),
            "l3_rate": tot[L3] / max(tot_req, 1),
            "avg_latency_ms": float(np.mean(run_lat)) if run_lat else 0.0,
            "p95_latency_ms": float(np.percentile(run_lat, 95)) if run_lat else 0.0,
            "p99_latency_ms": float(np.percentile(run_lat, 99)) if run_lat else 0.0,
            "cost_total": c_origin + c_latency + c_memory + c_move,
            "cost_origin": c_origin, "cost_latency": c_latency,
            "cost_memory": c_memory, "cost_move": c_move,
            "evictions": cur["evictions"], "refreshes": cur["refreshes"],
            "promotions": cur.get("promotions", 0), "demotions": cur.get("demotions", 0),
            "prefetches": cur.get("prefetches", 0),
            "final_capacity_bytes": pol.capacity_bytes,
            "peak_used_bytes": max((s.used_bytes for s in snapshots), default=0),
        }
        return RunResult(policy=pol.name, scenario=self.wl.scenario, profile=self.wl.profile,
                         snapshots=snapshots, summary=summary)
