"""
LinUCB contextual bandit — the adaptive weighting brain.

Each *arm* is a named weight preset (a caching "personality"). Once per epoch
the engine builds a context vector describing the current workload, the bandit
picks the arm whose predicted reward + uncertainty bonus is highest, and the
cache scores objects with that arm's weights for the next epoch. At the end of
the epoch the realised reward (hit rate, penalised by $ and latency) updates
that arm's model.

This is a standard, defensible algorithm (Li et al., WWW 2010). It gives us:
  * genuine runtime adaptation (weights follow the workload, nothing hardcoded)
  * an explore/exploit story for the jury
  * something concrete to visualise (active arm + weights over time)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

CONTEXT_KEYS = (
    "rate",          # arrivals this epoch / running max
    "entropy",       # access-distribution entropy / log(distinct)  (spread of demand)
    "hit_trend",     # hit_rate(t) - hit_rate(t-1)
    "miss_cost",     # mean origin $-equiv per miss / running max
    "pressure",      # used / capacity
    "evict_rate",    # evictions / requests
    "ghost_rate",    # ghost-list hits / requests   (undersize signal)
    "bias",          # constant 1.0
)
D = len(CONTEXT_KEYS)


@dataclass
class _Arm:
    name: str
    weights: dict[str, float]
    A: np.ndarray = field(default_factory=lambda: np.eye(D))
    b: np.ndarray = field(default_factory=lambda: np.zeros(D))
    pulls: int = 0

    def theta(self) -> np.ndarray:
        return np.linalg.solve(self.A, self.b)

    def ucb(self, x: np.ndarray, alpha: float) -> float:
        A_inv_x = np.linalg.solve(self.A, x)
        mean = float(self.theta() @ x)
        bonus = alpha * float(np.sqrt(max(x @ A_inv_x, 0.0)))
        return mean + bonus

    def update(self, x: np.ndarray, reward: float) -> None:
        self.A += np.outer(x, x)
        self.b += reward * x
        self.pulls += 1


class LinUCBWeightController:
    def __init__(self, arms: dict[str, dict[str, float]], *, alpha: float = 0.6,
                 lat_penalty: float = 0.35, cost_penalty: float = 0.45, seed: int = 0):
        self._arms = [_Arm(name=n, weights=w) for n, w in arms.items()]
        self.alpha = alpha
        self.lat_penalty = lat_penalty
        self.cost_penalty = cost_penalty
        self._rng = np.random.default_rng(seed)
        self._last_x: np.ndarray | None = None
        self._last_arm: _Arm | None = None
        self.active: _Arm = self._arms[0]

    # -- context -------------------------------------------------------- #
    @staticmethod
    def context_vector(feats: dict[str, float]) -> np.ndarray:
        return np.array([feats.get(k, 0.0) if k != "bias" else 1.0 for k in CONTEXT_KEYS], dtype=float)

    # -- per-epoch cycle ---------------------------------------------- #
    def select(self, feats: dict[str, float]) -> dict[str, float]:
        x = self.context_vector(feats)
        scores = [a.ucb(x, self.alpha) for a in self._arms]
        best = int(np.argmax(scores))
        self.active = self._arms[best]
        self._last_x, self._last_arm = x, self.active
        return dict(self.active.weights)

    def reward_from_epoch(self, hit_rate: float, norm_latency: float, norm_cost: float) -> float:
        return float(hit_rate - self.lat_penalty * norm_latency - self.cost_penalty * norm_cost)

    def learn(self, reward: float) -> None:
        if self._last_arm is not None and self._last_x is not None:
            self._last_arm.update(self._last_x, reward)

    # -- introspection ---------------------------------------------- #
    def snapshot(self) -> dict:
        return {
            "arm": self.active.name,
            "weights": dict(self.active.weights),
            "pulls": {a.name: a.pulls for a in self._arms},
        }
