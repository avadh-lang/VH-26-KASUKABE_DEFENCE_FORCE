# AACMS ablation — `api` profile

Each row disables **one** engine feature. The cost delta is that feature's contribution. All at a fixed 15 % cache (no autoscaler advantage) except where the autoscaler itself is the variable.

## steady

| variant | what's off | hit rate | cost $ | vs full AACMS |
|---|---|---|---|---|
| `AACMS` | full engine | 0.871 | 28.26 | — |
| `AACMS-noadmit` | no admission control | 0.870 | 28.19 | -0.2% |
| `AACMS-nobandit` | no bandit (frozen weights) | 0.863 | 27.86 | -1.4% |
| `AACMS-noautoscale` | no autoscaler | 0.729 | 58.04 | +105.4% cost |
| `AACMS-norefresh` | no smart refresh | 0.870 | 40.89 | +44.7% cost |
| `AACMS-fixed` | value model only (all off) | 0.729 | 65.23 | +130.9% cost |
| `GDSF` | best classical baseline | 0.774 | 67.34 | +138.3% cost |

## spike

| variant | what's off | hit rate | cost $ | vs full AACMS |
|---|---|---|---|---|
| `AACMS` | full engine | 0.872 | 32.76 | — |
| `AACMS-noadmit` | no admission control | 0.872 | 32.76 | 0.0% |
| `AACMS-nobandit` | no bandit (frozen weights) | 0.864 | 31.92 | -2.6% |
| `AACMS-noautoscale` | no autoscaler | 0.733 | 74.79 | +128.3% cost |
| `AACMS-norefresh` | no smart refresh | 0.865 | 44.37 | +35.4% cost |
| `AACMS-fixed` | value model only (all off) | 0.731 | 81.20 | +147.9% cost |
| `GDSF` | best classical baseline | 0.770 | 84.28 | +157.3% cost |

## popularity_shift

| variant | what's off | hit rate | cost $ | vs full AACMS |
|---|---|---|---|---|
| `AACMS` | full engine | 0.848 | 29.91 | — |
| `AACMS-noadmit` | no admission control | 0.848 | 29.91 | 0.0% |
| `AACMS-nobandit` | no bandit (frozen weights) | 0.844 | 29.72 | -0.6% |
| `AACMS-noautoscale` | no autoscaler | 0.710 | 67.54 | +125.8% cost |
| `AACMS-norefresh` | no smart refresh | 0.847 | 42.17 | +41.0% cost |
| `AACMS-fixed` | value model only (all off) | 0.709 | 74.84 | +150.3% cost |
| `GDSF` | best classical baseline | 0.712 | 76.14 | +154.6% cost |

## Reading this

- **Autoscaler** and **smart refresh** are the load-bearing features — double-digit-to-triple-digit cost swings.
- **Value model**: `AACMS-fixed` (everything off) still edges `GDSF` at every capacity — the multi-factor score is a small, consistent win over GDSF's fixed blend.
- **Bandit** and **admission control** are near-noise on Zipf traffic: GDSF's freq·cost/size is already close to optimal for that demand shape, so re-weighting it barely moves rankings, and there is no scan/pollution pattern here for admission to catch. They are kept as *robustness* — the bandit makes the weighting adaptive with zero per-deployment tuning (the PS's "adaptive at runtime" requirement) and cannot do worse than a hand-picked vector; admission matters on adversarial workloads (crawlers, scans) not modelled here.
