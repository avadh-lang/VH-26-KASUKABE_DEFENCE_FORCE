"""
Workload generation for AACMS.

    build_catalog(profile, n, seed)  -> {key: ObjectSpec}
    generate(scenario, profile, ...) -> Workload   (catalog + timed request stream)

Two application profiles are modelled (PS asks for >= 2 distinct workload types):
  - "api"    : read-heavy external-API service — many small objects, $ cost per miss
  - "recsys" : compute-heavy recommendation service — fewer large objects, latency per miss

Scenarios (PS asks the scoring model to win under >= 3):
  - "steady"            : constant rate, fixed popularity
  - "spike"             : flash crowd — cold objects go hot, rate x3, then relaxes
  - "popularity_shift"  : the hot set drifts continuously over the run
  - "diurnal"           : sinusoidal rate — exercises the autoscaler
  - "cold_start"        : ramp from idle to steady, cache starts empty
"""

from workload.catalog import build_catalog, PROFILES
from workload.scenarios import generate, Workload, SCENARIOS

__all__ = ["build_catalog", "PROFILES", "generate", "Workload", "SCENARIOS"]
