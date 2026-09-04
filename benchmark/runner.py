"""
Run a matrix of {scenario} x {policy} and persist the results.

    python -m benchmark.runner --profile api --scenarios steady spike popularity_shift
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from common import CostConfig
from workload import generate
from benchmark.driver import SimDriver, RunResult

from baselines import REGISTRY as BASELINE_REGISTRY
from engine import AACMSCache

POLICY_NAMES = ("LRU", "LFU", "GDS", "GDSF", "AACMS-fixed", "AACMS")
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


def build_policy(name: str, capacity_bytes: int, cost_cfg: CostConfig, epoch_seconds: float):
    if name in BASELINE_REGISTRY:
        return BASELINE_REGISTRY[name](capacity_bytes)
    if name == "AACMS":
        return AACMSCache(capacity_bytes, cost_cfg, epoch_seconds=epoch_seconds, autoscale=True)
    if name == "AACMS-fixed":       # ablation: engine scoring, no bandit adaptation, no autoscale
        p = AACMSCache(capacity_bytes, cost_cfg, epoch_seconds=epoch_seconds, autoscale=False)
        p.maintenance = lambda now: None  # freeze weights at "balanced"
        p.name = "AACMS-fixed"
        return p
    raise KeyError(f"unknown policy {name!r}")


def run_matrix(
    scenarios: list[str],
    policies: list[str] = list(POLICY_NAMES),
    *,
    profile: str = "api",
    capacity_frac: float = 0.15,
    epoch_seconds: float = 10.0,
    duration_s: float | None = None,
    seed: int = 0,
    save: bool = True,
) -> dict[tuple[str, str], RunResult]:
    cost_cfg = CostConfig()
    out: dict[tuple[str, str], RunResult] = {}

    for scenario in scenarios:
        wl = generate(scenario, profile, duration_s=duration_s, seed=seed)
        capacity = max(int(wl.working_set_bytes * capacity_frac), cost_cfg.scale_step_bytes)
        print(f"\n=== {scenario} / {profile} : {len(wl):,} requests, "
              f"{wl.meta['distinct_keys']:,} keys, cache {capacity/1e6:.1f} MB ===")

        for name in policies:
            t0 = time.perf_counter()
            pol = build_policy(name, capacity, cost_cfg, epoch_seconds)
            res = SimDriver(pol, wl, cost_cfg, epoch_seconds=epoch_seconds).run()
            out[(scenario, name)] = res
            s = res.summary
            print(f"  {name:12s} hit={s['hit_rate']:.3f}  p95={s['p95_latency_ms']:7.1f}ms  "
                  f"cost=${s['cost_total']:.4f}  evict={s['evictions']:,}  "
                  f"({time.perf_counter()-t0:.1f}s)")

    if save:
        _persist(out, profile)
        _persist_db(out, profile, dict(
            capacity_frac=capacity_frac, epoch_seconds=epoch_seconds,
            duration_s=duration_s, seed=seed,
        ))
    return out


def _persist_db(results: dict[tuple[str, str], RunResult], profile: str, config: dict) -> None:
    from benchmark.store import connect, save_run
    conn = connect()
    for res in results.values():
        save_run(conn, res, config)
    conn.close()


def _persist(results: dict[tuple[str, str], RunResult], profile: str) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    blob = {
        "profile": profile,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "runs": [
            {
                "scenario": sc, "policy": po,
                "summary": res.summary,
                "epochs": [s.as_row() for s in res.snapshots],
            }
            for (sc, po), res in results.items()
        ],
    }
    path = RESULTS_DIR / f"bench_{profile}.json"
    path.write_text(json.dumps(blob, indent=2))
    print(f"\nwrote {path}")


def _cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="api", choices=["api", "recsys"])
    ap.add_argument("--scenarios", nargs="+",
                    default=["steady", "spike", "popularity_shift"])
    ap.add_argument("--policies", nargs="+", default=list(POLICY_NAMES))
    ap.add_argument("--capacity-frac", type=float, default=0.15)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--epoch", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    run_matrix(
        a.scenarios, a.policies, profile=a.profile,
        capacity_frac=a.capacity_frac, epoch_seconds=a.epoch,
        duration_s=a.duration, seed=a.seed,
    )


if __name__ == "__main__":
    _cli()
