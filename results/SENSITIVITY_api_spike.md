# AACMS capacity sensitivity — `api` / `spike`

Working set ≈ 107 MB. Each column is a fixed cache size (the `AACMS` row still autoscales from that starting point).

| cache | 5% | 10% | 15% | 25% | 40% |
|---|---|---|---|---|---|
| LRU cost $ | 259.7 | 236.6 | 198.0 | 148.1 | 102.0 |
| LFU cost $ | 212.9 | 195.7 | 167.8 | 129.4 | 97.0 |
| GDSF cost $ | 130.5 | 113.5 | 84.3 | 58.6 | 44.8 |
| AACMS-fixed cost $ | 124.6 | 106.6 | 81.2 | 56.0 | 43.9 |
| AACMS cost $ | 49.2 | 45.2 | 32.8 | 28.8 | 29.1 |

AACMS (and AACMS-fixed) is cheapest at **every** capacity — the advantage is structural, not a tuned operating point.
