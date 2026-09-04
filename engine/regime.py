"""
Lightweight workload-regime label.

This does NOT drive the weights (the bandit does). It is a cheap, explainable
tag for the dashboard and the demo narration — "the engine currently thinks
we're in a SPIKE" — derived from the same epoch features the bandit sees.
"""

from __future__ import annotations

from collections import deque


class RegimeDetector:
    LABELS = ("cold_start", "spike", "popularity_shift", "steady")

    def __init__(self) -> None:
        self._rate_hist: deque[float] = deque(maxlen=8)
        self._entropy_hist: deque[float] = deque(maxlen=8)
        self._epochs = 0

    def update(self, feats: dict[str, float]) -> str:
        self._epochs += 1
        rate = feats.get("rate_abs", 0.0)
        entropy = feats.get("entropy", 0.0)
        self._rate_hist.append(rate)
        self._entropy_hist.append(entropy)

        if self._epochs <= 3 or feats.get("pressure", 0.0) < 0.5:
            return "cold_start"

        base = sorted(self._rate_hist)[len(self._rate_hist) // 2] or 1e-9
        if rate > 1.8 * base:
            return "spike"

        if len(self._entropy_hist) >= 5:
            drift = self._entropy_hist[-1] - self._entropy_hist[0]
            if drift > 0.08 and feats.get("hit_trend", 0.0) < -0.01:
                return "popularity_shift"

        return "steady"
