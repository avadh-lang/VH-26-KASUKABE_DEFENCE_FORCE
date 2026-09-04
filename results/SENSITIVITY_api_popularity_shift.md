# AACMS capacity sensitivity — `api` / `popularity_shift`

Working set ≈ 107 MB. Each column is a fixed cache size (the `AACMS` row still autoscales from that starting point).

| cache | 5% | 10% | 15% | 25% | 40% |
|---|---|---|---|---|---|
| LRU cost $ | 201.0 | 182.6 | 152.2 | 113.7 | 80.8 |
| LFU cost $ | 259.2 | 248.1 | 218.6 | 182.5 | 135.2 |
| GDSF cost $ | 112.6 | 97.3 | 76.1 | 55.0 | 42.7 |
| AACMS-fixed cost $ | 110.4 | 96.2 | 74.8 | 52.9 | 41.4 |
| AACMS cost $ | 46.0 | 42.0 | 29.9 | 27.5 | 28.0 |

AACMS (and AACMS-fixed) is cheapest at **every** capacity — the advantage is structural, not a tuned operating point.
