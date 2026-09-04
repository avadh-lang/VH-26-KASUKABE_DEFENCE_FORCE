# AACMS benchmark — `api` profile

_generated 2026-09-04 11:13:28_

Cost model: managed-cache RAM @ $0.12/GB-hr, origin $ per regeneration from the object catalog, latency business-cost @ $2e-6/ms. Identical workload and cost rules for every policy.

## steady

| policy | hit rate | stale rate | p95 latency (ms) | total cost ($) | vs GDSF | vs LRU |
|---|---|---|---|---|---|---|
| LRU | 0.765 | 0.235 | 425.7 | 148.055 | -119.8% | +0.0% |
| LFU | 0.810 | 0.190 | 339.6 | 128.377 | -90.6% | +13.3% |
| GDS | 0.727 | 0.273 | 24.0 | 72.555 | -7.7% | +51.0% |
| GDSF | 0.774 | 0.226 | 23.3 | 67.344 | +0.0% | +54.5% |
| AACMS-fixed | 0.748 | 0.286 | 23.3 | 60.409 | +10.3% | +59.2% |
| AACMS | 0.865 | 0.195 | 17.8 | 27.863 | +58.6% | +81.2% |

AACMS autoscaler settled at **41 MB** (started at 24 MB).

## spike

| policy | hit rate | stale rate | p95 latency (ms) | total cost ($) | vs GDSF | vs LRU |
|---|---|---|---|---|---|---|
| LRU | 0.765 | 0.235 | 426.1 | 198.005 | -134.9% | +0.0% |
| LFU | 0.807 | 0.193 | 330.6 | 167.781 | -99.1% | +15.3% |
| GDS | 0.726 | 0.274 | 23.8 | 92.284 | -9.5% | +53.4% |
| GDSF | 0.770 | 0.230 | 23.0 | 84.284 | +0.0% | +57.4% |
| AACMS-fixed | 0.746 | 0.278 | 23.0 | 75.685 | +10.2% | +61.8% |
| AACMS | 0.866 | 0.183 | 17.6 | 32.259 | +61.7% | +83.7% |

AACMS autoscaler settled at **41 MB** (started at 25 MB).

## popularity_shift

| policy | hit rate | stale rate | p95 latency (ms) | total cost ($) | vs GDSF | vs LRU |
|---|---|---|---|---|---|---|
| LRU | 0.758 | 0.242 | 441.9 | 152.187 | -99.9% | +0.0% |
| LFU | 0.652 | 0.348 | 558.2 | 218.604 | -187.1% | -43.6% |
| GDS | 0.708 | 0.292 | 23.9 | 72.327 | +5.0% | +52.5% |
| GDSF | 0.712 | 0.288 | 24.0 | 76.137 | +0.0% | +50.0% |
| AACMS-fixed | 0.704 | 0.329 | 24.1 | 72.036 | +5.4% | +52.7% |
| AACMS | 0.843 | 0.215 | 19.1 | 29.981 | +60.6% | +80.3% |

AACMS autoscaler settled at **41 MB** (started at 16 MB).

## Charts

![api_steady](figs/api_steady.png)

![api_spike](figs/api_spike.png)

![api_popularity_shift](figs/api_popularity_shift.png)

![api_aacms_internals](figs/api_aacms_internals.png)
