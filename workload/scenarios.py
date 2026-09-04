"""
Request-stream generation.

A Workload is a catalog plus a time-sorted list of (t_seconds, key) requests.
Popularity follows a Zipf law over *ranks*; each scenario controls how the
rank->object mapping and the arrival rate evolve over the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from common import ObjectSpec
from workload.catalog import build_catalog


@dataclass
class Workload:
    scenario: str
    profile: str
    catalog: dict[str, ObjectSpec]
    requests: list[tuple[float, str]]          # sorted by t
    duration_s: float
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.requests)

    @property
    def working_set_bytes(self) -> int:
        """Total size of every distinct key requested — a natural cache-sizing reference."""
        seen = {k for _, k in self.requests}
        return sum(self.catalog[k].size_bytes for k in seen)


SCENARIOS = ("steady", "spike", "popularity_shift", "diurnal", "cold_start")

# tuned so a run is a few hundred k requests — enough signal, fast to simulate
_DEFAULTS = dict(duration_s=1200.0, base_rate=350.0, zipf_alpha=1.05, seed=0)


def _zipf_weights(n: int, alpha: float) -> np.ndarray:
    w = 1.0 / np.power(np.arange(1, n + 1), alpha)
    return w / w.sum()


def _rate(scenario: str, t: float, dur: float, base: float) -> float:
    if scenario == "spike":
        s0, s1 = 0.45 * dur, 0.62 * dur
        return base * (3.0 if s0 <= t < s1 else 1.0)
    if scenario == "diurnal":
        # 1.5 periods over the run, swinging 0.35x .. 1.65x
        return base * (1.0 + 0.65 * np.sin(2 * np.pi * 1.5 * t / dur))
    if scenario == "cold_start":
        ramp = 0.15 * dur
        return base * min(1.0, 0.1 + 0.9 * t / ramp) if t < ramp else base
    return base  # steady, popularity_shift


def generate(
    scenario: str = "steady",
    profile: str = "api",
    *,
    n_objects: int | None = None,
    duration_s: float | None = None,
    base_rate: float | None = None,
    zipf_alpha: float | None = None,
    seed: int | None = None,
) -> Workload:
    if scenario not in SCENARIOS:
        raise KeyError(f"unknown scenario {scenario!r}; have {SCENARIOS}")

    dur = float(duration_s or _DEFAULTS["duration_s"])
    base = float(base_rate or _DEFAULTS["base_rate"])
    alpha = float(zipf_alpha or _DEFAULTS["zipf_alpha"])
    seed = int(_DEFAULTS["seed"] if seed is None else seed)

    catalog = build_catalog(profile, n_objects, seed=seed)
    keys = list(catalog)
    n = len(keys)
    rng = np.random.default_rng(seed + 1)

    weights = _zipf_weights(n, alpha)
    order = rng.permutation(n)          # rank -> catalog index
    ranks = np.arange(n)

    # spike: pick cold objects (bottom 30% by rank) to promote to the very top
    spike_targets = None
    if scenario == "spike":
        cold = order[int(0.7 * n):]
        spike_targets = rng.choice(cold, size=min(25, len(cold)), replace=False)

    requests: list[tuple[float, str]] = []
    shift_per_bin = max(1, n // 400)   # popularity_shift: swaps applied each 1s bin

    for bin_start in range(int(dur)):
        t_mid = bin_start + 0.5
        lam = _rate(scenario, t_mid, dur, base)
        k = rng.poisson(lam)
        if k == 0:
            continue

        cur_order = order
        if scenario == "spike" and 0.45 * dur <= t_mid < 0.62 * dur:
            cur_order = order.copy()
            cur_order[:len(spike_targets)] = spike_targets

        idx_by_rank = cur_order
        chosen_ranks = rng.choice(ranks, size=k, p=weights)
        chosen_idx = idx_by_rank[chosen_ranks]
        ts = bin_start + np.sort(rng.random(k))
        for tt, ci in zip(ts, chosen_idx):
            requests.append((float(tt), keys[int(ci)]))

        if scenario == "popularity_shift":
            for _ in range(shift_per_bin):
                a, b = rng.integers(0, n, size=2)
                order[a], order[b] = order[b], order[a]

    requests.sort(key=lambda r: r[0])
    return Workload(
        scenario=scenario, profile=profile, catalog=catalog, requests=requests,
        duration_s=dur,
        meta=dict(
            n_objects=n, base_rate=base, zipf_alpha=alpha, seed=seed,
            total_requests=len(requests),
            distinct_keys=len({k for _, k in requests}),
        ),
    )
