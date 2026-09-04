"""
Real-data catalog — objects backed by genuine public HTTP endpoints
(jsonplaceholder.typicode.com, a free REST API built for exactly this kind
of testing/dev traffic — no key, no auth, no rate-limit surprises).

Every object's size and latency are measured from one real HTTP GET made
right now, at catalog-build time — not sampled from a distribution like the
"api"/"recsys" profiles. The live simulation that runs afterwards is fast
and controlled (no network call per simulated request), but every number
in this catalog came from an actual round trip over the internet.

This is intentionally scoped to the live dashboard only. The offline
benchmark (benchmark/) needs deterministic, repeatable traffic across many
seeds and scenarios to run a fair ablation study — hitting a real API
hundreds of times per run would make that slow and non-reproducible, so it
keeps using the synthetic catalogs in workload/catalog.py. This module is
the "look, this is really the internet" counterpart for the live demo.
"""

from __future__ import annotations

import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from common import ObjectSpec

BASE_URL = "https://jsonplaceholder.typicode.com"

# resource -> how many of that resource to pull into the catalog
_RESOURCES: dict[str, int] = {
    "posts": 100,
    "comments": 150,
    "albums": 100,
    "photos": 150,
    "todos": 150,
    "users": 10,
}

# real, public list prices — not invented numbers
_BANDWIDTH_USD_PER_GB = 0.09      # AWS standard data-transfer-out (first 10 TB)
_REQUEST_USD = 0.0000035          # ~ AWS API Gateway, $ per request

_TTL_BY_RESOURCE = {
    "posts": 300.0, "comments": 180.0, "albums": 600.0,
    "photos": 600.0, "todos": 60.0, "users": 900.0,
}
_VOLATILITY_BY_RESOURCE = {
    "posts": 0.05, "comments": 0.15, "albums": 0.03,
    "photos": 0.03, "todos": 0.25, "users": 0.02,
}


def real_key(resource: str, obj_id: int) -> str:
    return f"real:{resource}:{obj_id}"


def probe_one(resource: str = "posts", obj_id: int | None = None,
              timeout: float = 6.0) -> dict:
    """One live GET, right now. Used for the on-demand '/api/real/ping' proof."""
    import random
    if obj_id is None:
        obj_id = random.randint(1, _RESOURCES.get(resource, 100))
    url = f"{BASE_URL}/{resource}/{obj_id}"
    t0 = time.perf_counter()
    with urllib.request.urlopen(url, timeout=timeout) as r:
        body = r.read()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "url": url, "bytes": len(body), "latency_ms": round(elapsed_ms, 1),
        "sample": body[:160].decode("utf-8", "replace"),
    }


def _probe(resource: str, obj_id: int) -> tuple[str, int, float] | None:
    url = f"{BASE_URL}/{resource}/{obj_id}"
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            body = r.read()
    except Exception:
        return None
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return real_key(resource, obj_id), len(body), elapsed_ms


def build_real_catalog(max_workers: int = 40,
                        on_progress: Callable[[int, int], None] | None = None
                        ) -> dict[str, ObjectSpec]:
    """
    Fetch every configured endpoint once, live, concurrently, and build a
    catalog from the measured size + latency. Network-bound — a few seconds
    for ~650 endpoints on a normal connection.
    """
    jobs = [(res, i) for res, n in _RESOURCES.items() for i in range(1, n + 1)]
    catalog: dict[str, ObjectSpec] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_probe, res, i): (res, i) for res, i in jobs}
        for fut in as_completed(futures):
            res, _i = futures[fut]
            result = fut.result()
            done += 1
            if on_progress:
                on_progress(done, len(jobs))
            if result is None:
                continue
            key, size_bytes, latency_ms = result
            cost = size_bytes / 1e9 * _BANDWIDTH_USD_PER_GB + _REQUEST_USD
            catalog[key] = ObjectSpec(
                key=key,
                size_bytes=max(size_bytes, 64),
                gen_latency_ms=max(latency_ms, 1.0),
                gen_cost_usd=cost,
                ttl_s=_TTL_BY_RESOURCE.get(res, 300.0),
                volatility=_VOLATILITY_BY_RESOURCE.get(res, 0.05),
                compressible=0.7,
                tags=("real", res),
            )
    if not catalog:
        raise RuntimeError("real_catalog: no endpoint responded — check internet access")
    return catalog
