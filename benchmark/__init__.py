"""
Benchmark harness: drive a CachePolicy with a Workload, collect per-epoch
snapshots + a run summary, and compare policies head-to-head.

    from benchmark import SimDriver, run_matrix
    result = SimDriver(policy, workload, cost_cfg).run()
"""

from benchmark.driver import SimDriver, RunResult
from benchmark.runner import run_matrix, build_policy, POLICY_NAMES

__all__ = ["SimDriver", "RunResult", "run_matrix", "build_policy", "POLICY_NAMES"]
