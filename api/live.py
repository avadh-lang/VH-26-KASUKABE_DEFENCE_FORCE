"""
LiveSim — drives several cache policies in lockstep over an epoch-by-epoch
request stream so the dashboard can watch them race in real time.

Traffic is generated one epoch at a time, so the demo can inject a flash crowd
mid-run (`inject_spike`) and everyone sees CACHE MIND route objects across its
L1/L2/L3 tiers while LRU/LFU thrash and hit the origin.
"""

from __future__ import annotations

import numpy as np

from common import CostConfig, RequestOutcome, L1, L2, L3
from workload.catalog import build_catalog
from baselines import REGISTRY as BASELINES
from engine import CacheMind
from api.cost import CostLedger

_ALPHA = 0.92


class _PolicyRunner:
    def __init__(self, name: str, policy, cfg: CostConfig):
        self.name = name
        self.p = policy
        self.cfg = cfg
        self.c_origin = self.c_latency = self.c_memory = self.c_move = 0.0
        self.hits = self.misses = self.reqs = 0
        self.tier_hits = {L1: 0, L2: 0, L3: 0}
        self.hit_win: list[int] = []
        self._ep_lat: list[float] = []
        self._mv_reported = 0

    def serve(self, t: float, key: str, spec) -> None:
        p, cfg = self.p, self.cfg
        entry = p.lookup(key, t)
        if entry is None:
            latency, cost, tier = spec.gen_latency_ms, spec.gen_cost_usd, 0
            p.on_admit(spec, t)
            hit = False
        elif entry.is_stale(t) and p.should_refresh(entry, t):
            latency, cost, tier = spec.gen_latency_ms, spec.gen_cost_usd, entry.tier
            p.on_refresh(entry, t); p.on_hit(entry, t); hit = True
        else:
            tier = entry.tier
            latency = cfg.tier_latency_ms(tier) + (cfg.decompress_latency_ms if entry.compressed else 0.0)
            cost = 0.0
            p.on_hit(entry, t); hit = True

        self.c_origin += cost
        lat_cost = cfg.latency_usd(latency)
        self.c_latency += lat_cost
        p.on_request_end(RequestOutcome(key, hit, tier, False, entry is None, latency,
                                        cost + lat_cost, f"l{tier}" if hit else "miss"), t)
        self.reqs += 1
        self._ep_lat.append(latency)
        self.hit_win.append(1 if hit else 0)
        if hit:
            self.hits += 1
            self.tier_hits[tier] = self.tier_hits.get(tier, 0) + 1
        else:
            self.misses += 1

    def close_epoch(self, t: float, epoch_seconds: float) -> dict:
        cfg = self.cfg
        tu = self.p.tier_used()
        for tier in (L1, L2, L3):
            self.c_memory += cfg.memory_usd(tu.get(tier, 0), epoch_seconds, tier)

        self.p.maintenance(t)
        for k in self.p.pending_refreshes():
            e = self.p.lookup(k, t)
            if e:
                self.c_origin += e.spec.gen_cost_usd * cfg.refresh_discount
                self.p.on_refresh(e, t)
        acc = getattr(self.p, "accept_prefetch", None)
        for k in self.p.pending_prefetches():
            spec = _CATALOG.get(k) if _CATALOG else None
            if spec is None:
                continue
            self.c_origin += spec.gen_cost_usd * cfg.refresh_discount
            if acc:
                acc(spec, t)
        mv = getattr(self.p, "_move_bytes", 0) - self._mv_reported
        if mv > 0:
            self.c_move += cfg.move_usd(mv)
            self._mv_reported = getattr(self.p, "_move_bytes", 0)

        win = self.hit_win[-4000:]
        recent_hr = sum(win) / len(win) if win else 0.0
        self._lat_win = (getattr(self, "_lat_win", []) + self._ep_lat)[-6000:]
        lw = self._lat_win
        avg_lat = sum(lw) / len(lw) if lw else 0.0
        p95_lat = sorted(lw)[int(0.95 * (len(lw) - 1))] if lw else 0.0
        self._ep_lat = []
        self.hit_win = win
        intern = self.p.internals()
        tcap = self.p.tier_capacity()
        r = max(self.reqs, 1)
        snap = {
            "policy": self.name,
            "hit_rate": round(recent_hr, 4),
            "hit_rate_cum": round(self.hits / r, 4),
            "l1_rate": round(self.tier_hits[L1] / r, 4),
            "l2_rate": round(self.tier_hits[L2] / r, 4),
            "l3_rate": round(self.tier_hits[L3] / r, 4),
            "avg_latency_ms": round(avg_lat, 2),
            "p95_latency_ms": round(p95_lat, 2),
            "cost_total": round(self.c_origin + self.c_latency + self.c_memory + self.c_move, 5),
            "cost_origin": round(self.c_origin, 5),
            "cost_latency": round(self.c_latency, 5),
            "cost_memory": round(self.c_memory, 5),
            "cost_move": round(self.c_move, 5),
            "used_mb": round(self.p.used_bytes / 1e6, 2),
            "entries": self.p.entries,
            "tiers": [
                {"tier": f"L{tier}", "used_mb": round(tu.get(tier, 0) / 1e6, 2),
                 "cap_mb": round(tcap.get(tier, 0) / 1e6, 2)}
                for tier in (L1, L2, L3)
            ],
        }
        if intern:
            snap["weights"] = intern.get("weights")
            snap["bandit_arm"] = intern.get("bandit_arm")
            snap["regime"] = intern.get("regime")
            snap["decisions"] = intern.get("decisions", [])[-7:]
        return snap


_CATALOG = None      # set per LiveSim so _PolicyRunner can resolve prefetch keys


class LiveSim:
    def __init__(self, scenario: str = "steady", profile: str = "api", *,
                 policies: list[str] | None = None, epoch_seconds: float = 10.0,
                 base_rate: float = 520.0, duration_s: float = 100_000.0, seed: int = 0):
        global _CATALOG
        self.scenario = scenario
        self.profile = profile
        self.epoch_seconds = epoch_seconds
        self.base_rate = base_rate
        self.duration_s = duration_s
        self.cfg = CostConfig(scale_step_bytes=4 * 1024 * 1024)

        self.catalog = build_catalog(profile, seed=seed)
        _CATALOG = self.catalog
        self.keys = list(self.catalog)
        self.n = len(self.keys)
        self._rng = np.random.default_rng(seed + 7)
        w = 1.0 / np.power(np.arange(1, self.n + 1), _ALPHA)
        self.weights = w / w.sum()
        self.order = self._rng.permutation(self.n)

        names = policies or ["LRU", "LFU", "GDS", "GDSF", "CACHE MIND"]
        working = sum(self.catalog[k].size_bytes for k in self.keys)
        l1 = max(int(working * 0.12), 3 * 1024 * 1024)
        self.start_capacity = l1
        self.runners: list[_PolicyRunner] = []
        for nm in names:
            if nm in BASELINES:
                pol = BASELINES[nm](l1)
            elif nm in ("CACHE MIND", "CM"):
                pol = CacheMind(l1, self.cfg, epoch_seconds=epoch_seconds)
            elif nm == "CM-notier":
                pol = CacheMind(l1, self.cfg, epoch_seconds=epoch_seconds, tiering=False)
            else:
                continue
            self.runners.append(_PolicyRunner(nm, pol, self.cfg))

        self.ledger = CostLedger(baseline="LRU" if "LRU" in names else names[0])
        self.epoch = 0
        self.t = 0.0
        self._spike_epochs = 0
        self._spike_targets: np.ndarray | None = None

    def inject_spike(self, epochs: int = 6, hot: int = 25) -> None:
        cold = self.order[int(0.75 * self.n):]
        self._spike_targets = self._rng.choice(cold, size=min(hot, len(cold)), replace=False)
        self._spike_epochs = epochs

    def set_scenario(self, scenario: str) -> None:
        self.scenario = scenario

    def _epoch_rate(self) -> float:
        r = self.base_rate
        if self._spike_epochs > 0 or self.scenario == "spike":
            r *= 3.0 if self._spike_epochs > 0 else 1.0
        if self.scenario == "diurnal":
            r *= 1.0 + 0.6 * np.sin(2 * np.pi * self.epoch / 40.0)
        return max(r, 5.0)

    def step(self) -> dict:
        rate = self._epoch_rate()
        k = int(self._rng.poisson(rate))
        cur = self.order
        if self._spike_epochs > 0 and self._spike_targets is not None:
            cur = self.order.copy()
            cur[: len(self._spike_targets)] = self._spike_targets

        active_n = self.n
        if self.scenario == "diurnal":
            frac = 0.15 + 0.85 * min(1.0, rate / (self.base_rate * 1.5))
            active_n = max(200, int(self.n * frac))
        w = self.weights[:active_n]
        ranks = self._rng.choice(active_n, size=k, p=w / w.sum())
        for j, ci in enumerate(cur[ranks]):
            t = self.t + (j / max(k, 1)) * self.epoch_seconds
            key = self.keys[int(ci)]
            spec = self.catalog[key]
            for run in self.runners:
                run.serve(t, key, spec)

        self.t += self.epoch_seconds
        snaps = [run.close_epoch(self.t, self.epoch_seconds) for run in self.runners]
        for run in self.runners:
            self.ledger.update(run.name, origin=run.c_origin, latency=run.c_latency,
                               memory=run.c_memory + run.c_move, hits=run.hits, misses=run.misses)

        if self.scenario == "popularity_shift":
            for _ in range(max(1, self.n // 300)):
                a, b = self._rng.integers(0, self.n, size=2)
                self.order[a], self.order[b] = self.order[b], self.order[a]
        if self._spike_epochs > 0:
            self._spike_epochs -= 1

        out = {
            "epoch": self.epoch, "t": round(self.t, 1), "rate": round(rate, 1),
            "spike_active": self._spike_epochs > 0 or self.scenario == "spike",
            "scenario": self.scenario, "policies": snaps,
            "cost_report": self.ledger.report(),
        }
        self.epoch += 1
        return out
