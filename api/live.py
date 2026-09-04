"""
LiveSim — drives several cache policies in lockstep over an epoch-by-epoch
request stream so the dashboard can watch them race in real time.

Unlike the offline benchmark it generates traffic one epoch at a time, so the
demo can inject a flash crowd mid-run (`inject_spike`) and everyone sees AACMS
react while LRU/LFU thrash.
"""

from __future__ import annotations

import numpy as np

from common import CostConfig, RequestOutcome
from workload.catalog import build_catalog
from baselines import REGISTRY as BASELINES
from engine import AACMSCache
from api.cost import CostLedger

_ALPHA = 0.92


class _PolicyRunner:
    def __init__(self, name: str, policy, cfg: CostConfig):
        self.name = name
        self.p = policy
        self.cfg = cfg
        self.c_origin = self.c_latency = self.c_memory = 0.0
        self.hits = self.misses = self.reqs = 0
        self.hit_win: list[int] = []
        self.hit_latency_ms = float(getattr(policy, "hit_latency_ms", 0.5))
        self._ep_lat: list[float] = []

    def serve(self, t: float, key: str, spec) -> None:
        p = self.p
        entry = p.lookup(key, t)
        if entry is None:
            latency, cost = spec.gen_latency_ms, spec.gen_cost_usd
            p.on_admit(spec, t)
            hit = False
        elif entry.is_stale(t) and p.should_refresh(entry, t):
            latency, cost = spec.gen_latency_ms, spec.gen_cost_usd
            p.on_refresh(entry, t); p.on_hit(entry, t); hit = True
        elif entry.is_stale(t):
            latency, cost = self.hit_latency_ms, 0.0
            p.on_hit(entry, t); hit = True
        else:
            latency, cost = self.hit_latency_ms, 0.0
            p.on_hit(entry, t); hit = True

        self.c_origin += cost
        lat_cost = self.cfg.latency_usd(latency)
        self.c_latency += lat_cost
        p.on_request_end(RequestOutcome(key, hit, False, entry is None, latency,
                                        cost + lat_cost, "hit" if hit else "miss"), t)
        self.reqs += 1
        self._ep_lat.append(latency)
        self.hit_win.append(1 if hit else 0)
        if hit:
            self.hits += 1
        else:
            self.misses += 1

    def close_epoch(self, t: float, epoch_seconds: float) -> dict:
        self.c_memory += self.cfg.memory_usd(self.p.used_bytes, epoch_seconds)
        self.p.maintenance(t)
        for k in self.p.pending_refreshes():
            e = self.p.lookup(k, t)
            if e:
                self.c_origin += e.spec.gen_cost_usd * self.cfg.refresh_discount
                self.p.on_refresh(e, t)

        win = self.hit_win[-4000:]
        recent_hr = sum(win) / len(win) if win else 0.0
        lat = self._ep_lat
        avg_lat = sum(lat) / len(lat) if lat else 0.0
        p95_lat = sorted(lat)[int(0.95 * (len(lat) - 1))] if lat else 0.0
        self._ep_lat = []
        intern = self.p.internals()
        snap = {
            "policy": self.name,
            "hit_rate": round(recent_hr, 4),
            "hit_rate_cum": round(self.hits / max(self.reqs, 1), 4),
            "avg_latency_ms": round(avg_lat, 2),
            "p95_latency_ms": round(p95_lat, 2),
            "cost_total": round(self.c_origin + self.c_latency + self.c_memory, 5),
            "cost_origin": round(self.c_origin, 5),
            "cost_latency": round(self.c_latency, 5),
            "cost_memory": round(self.c_memory, 5),
            "capacity_mb": round(self.p.capacity_bytes / 1e6, 2),
            "used_mb": round(self.p.used_bytes / 1e6, 2),
            "entries": self.p.entries,
        }
        if intern:
            snap["weights"] = intern.get("weights")
            snap["bandit_arm"] = intern.get("bandit_arm")
            snap["regime"] = intern.get("regime")
            snap["decisions"] = intern.get("decisions", [])[-6:]
        self.hit_win = self.hit_win[-4000:]
        return snap


class LiveSim:
    def __init__(
        self,
        scenario: str = "steady",
        profile: str = "api",
        *,
        policies: list[str] | None = None,
        epoch_seconds: float = 10.0,
        base_rate: float = 450.0,
        duration_s: float = 100_000.0,
        seed: int = 0,
    ):
        self.scenario = scenario
        self.profile = profile
        self.epoch_seconds = epoch_seconds
        self.base_rate = base_rate
        self.duration_s = duration_s
        # Demo cost model: price cache capacity as a *managed, replicated HA tier*
        # provisioned in node-units, not spot RAM — so over/under-provisioning is a
        # real, visible tradeoff for the autoscaler on screen.
        self.cfg = CostConfig(mem_usd_per_gb_hour=2.5, scale_step_bytes=4 * 1024 * 1024)

        self.catalog = build_catalog(profile, seed=seed)
        self.keys = list(self.catalog)
        self.n = len(self.keys)
        self._rng = np.random.default_rng(seed + 7)
        w = 1.0 / np.power(np.arange(1, self.n + 1), _ALPHA)
        self.weights = w / w.sum()
        self.order = self._rng.permutation(self.n)

        names = policies or ["LRU", "LFU", "GDSF", "AACMS"]
        # small enough that cache pressure (and policy divergence) shows within ~15 epochs
        working = sum(self.catalog[k].size_bytes for k in self.keys)
        cap = max(int(working * 0.05), 4 * 1024 * 1024)
        self.start_capacity = cap
        self.runners: list[_PolicyRunner] = []
        for nm in names:
            if nm in BASELINES:
                pol = BASELINES[nm](cap)
            elif nm == "AACMS":
                pol = AACMSCache(cap, self.cfg, epoch_seconds=epoch_seconds, autoscale=True)
            elif nm == "AACMS-fixed":
                pol = AACMSCache(cap, self.cfg, epoch_seconds=epoch_seconds, autoscale=False)
            else:
                continue
            self.runners.append(_PolicyRunner(nm, pol, self.cfg))

        self.ledger = CostLedger(baseline="LRU" if "LRU" in names else names[0])
        self.epoch = 0
        self.t = 0.0
        self._spike_epochs = 0
        self._spike_targets: np.ndarray | None = None
        self._shift = 0

    # -- demo controls ------------------------------------------------- #
    def inject_spike(self, epochs: int = 6, hot: int = 25) -> None:
        cold = self.order[int(0.75 * self.n):]
        self._spike_targets = self._rng.choice(cold, size=min(hot, len(cold)), replace=False)
        self._spike_epochs = epochs

    def set_scenario(self, scenario: str) -> None:
        self.scenario = scenario

    # -- stepping ---------------------------------------------------- #
    def _epoch_rate(self) -> float:
        r = self.base_rate
        if self._spike_epochs > 0 or self.scenario == "spike":
            r *= 3.0 if self._spike_epochs > 0 else 1.0
        if self.scenario == "diurnal":
            r *= 1.0 + 0.6 * np.sin(2 * np.pi * self.epoch / 40.0)
        return max(r, 5.0)

    def step(self) -> dict:
        """Advance one epoch; return {epoch, t, policies:[snap...], cost_report}."""
        rate = self._epoch_rate()
        k = int(self._rng.poisson(rate))      # ~`rate` requests per epoch
        cur = self.order
        if self._spike_epochs > 0 and self._spike_targets is not None:
            cur = self.order.copy()
            cur[: len(self._spike_targets)] = self._spike_targets

        ranks = self._rng.choice(self.n, size=k, p=self.weights)
        idxs = cur[ranks]
        for j, ci in enumerate(idxs):
            t = self.t + (j / max(k, 1)) * self.epoch_seconds
            key = self.keys[int(ci)]
            spec = self.catalog[key]
            for r in self.runners:
                r.serve(t, key, spec)

        self.t += self.epoch_seconds
        snaps = [r.close_epoch(self.t, self.epoch_seconds) for r in self.runners]
        for r, s in zip(self.runners, snaps):
            self.ledger.update(r.name, origin=r.c_origin, latency=r.c_latency,
                               memory=r.c_memory, hits=r.hits, misses=r.misses)

        if self.scenario == "popularity_shift":
            for _ in range(max(1, self.n // 300)):
                a, b = self._rng.integers(0, self.n, size=2)
                self.order[a], self.order[b] = self.order[b], self.order[a]
        if self._spike_epochs > 0:
            self._spike_epochs -= 1

        out = {
            "epoch": self.epoch,
            "t": round(self.t, 1),
            "rate": round(rate, 1),
            "spike_active": self._spike_epochs > 0 or self.scenario == "spike",
            "scenario": self.scenario,
            "policies": snaps,
            "cost_report": self.ledger.report(),
        }
        self.epoch += 1
        return out
