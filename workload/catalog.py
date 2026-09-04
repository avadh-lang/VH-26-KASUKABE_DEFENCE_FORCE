"""
Object catalogs for the two application profiles.

The point of having two very different profiles is to prove AACMS is
application-agnostic: the same engine, no re-tuning, must win on both a
"$-per-miss" workload and a "latency-per-miss" workload.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from common import ObjectSpec


@dataclass(frozen=True)
class ProfileSpec:
    key_prefix: str
    n_default: int
    # size (bytes) — lognormal
    size_mu: float
    size_sigma: float
    # fraction of objects that are "expensive to regenerate"
    expensive_frac: float
    # expensive objects: latency (ms) uniform range, and $ per regeneration
    exp_latency: tuple[float, float]
    exp_cost_usd: tuple[float, float]
    # cheap objects
    cheap_latency: tuple[float, float]
    cheap_cost_usd: tuple[float, float]
    # freshness
    ttl_s: tuple[float, float]
    volatility: tuple[float, float]
    tag: str


PROFILES: dict[str, ProfileSpec] = {
    # read-heavy API service: lots of small JSON objects, some backed by paid
    # third-party APIs ($ per call), some cheap DB reads.
    "api": ProfileSpec(
        key_prefix="api",
        n_default=6000,
        size_mu=float(np.log(12_000)), size_sigma=0.9,
        expensive_frac=0.35,
        exp_latency=(120.0, 900.0), exp_cost_usd=(4e-4, 6e-3),
        cheap_latency=(3.0, 25.0), cheap_cost_usd=(0.0, 2e-6),
        ttl_s=(20.0, 600.0), volatility=(0.02, 0.35),
        tag="api",
    ),
    # compute-heavy recommendation service: fewer, larger objects (candidate
    # lists / embeddings), each very costly to recompute (model inference).
    "recsys": ProfileSpec(
        key_prefix="rec",
        n_default=2500,
        size_mu=float(np.log(180_000)), size_sigma=0.7,
        expensive_frac=0.8,
        exp_latency=(250.0, 2200.0), exp_cost_usd=(6e-4, 4e-3),
        cheap_latency=(40.0, 120.0), cheap_cost_usd=(1e-5, 8e-5),
        ttl_s=(60.0, 1800.0), volatility=(0.004, 0.06),
        tag="recsys",
    ),
}


def build_catalog(profile: str, n: int | None = None, seed: int = 0) -> dict[str, ObjectSpec]:
    if profile not in PROFILES:
        raise KeyError(f"unknown profile {profile!r}; have {list(PROFILES)}")
    p = PROFILES[profile]
    n = n or p.n_default
    rng = np.random.default_rng(seed)

    sizes = np.clip(rng.lognormal(p.size_mu, p.size_sigma, n), 512, None).astype(int)
    is_exp = rng.random(n) < p.expensive_frac

    lat = np.where(
        is_exp,
        rng.uniform(*p.exp_latency, n),
        rng.uniform(*p.cheap_latency, n),
    )
    cost = np.where(
        is_exp,
        rng.uniform(*p.exp_cost_usd, n),
        rng.uniform(*p.cheap_cost_usd, n),
    )
    ttl = rng.uniform(*p.ttl_s, n)
    vol = rng.uniform(*p.volatility, n)
    # text/JSON objects compress well; already-encoded blobs (images, embeddings) do not
    compressible = np.clip(rng.beta(2.0, 2.0, n) * (0.75 if profile == "api" else 0.35), 0.0, 0.9)

    catalog: dict[str, ObjectSpec] = {}
    for i in range(n):
        key = f"{p.key_prefix}:{i:05d}"
        catalog[key] = ObjectSpec(
            key=key,
            size_bytes=int(sizes[i]),
            gen_latency_ms=float(lat[i]),
            gen_cost_usd=float(cost[i]),
            ttl_s=float(ttl[i]),
            volatility=float(vol[i]),
            compressible=float(compressible[i]),
            tags=(p.tag, "expensive" if is_exp[i] else "cheap"),
        )
    return catalog
