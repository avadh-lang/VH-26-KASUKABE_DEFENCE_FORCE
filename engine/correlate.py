"""
CoAccessTracker — a light co-access signal: which OTHER objects tend to be
requested in the same short window as this one.

Deliberately simple (a bounded co-occurrence counter over a sliding window
of recent keys, not a graph/embedding model) — this exists to answer "is
object-to-object correlation modeled at all", and to feed one extra PREFETCH
source: when a hot object is served, its strongest known partner gets
warmed too, even before the predictor has independently seen the partner
trend hot on its own.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque


class CoAccessTracker:
    def __init__(self, window: int = 8, cap: int = 20_000):
        self.window = window
        self._recent: deque[str] = deque(maxlen=window)
        self._co: dict[str, Counter] = defaultdict(Counter)
        self._cap = cap

    def observe(self, key: str) -> None:
        for other in self._recent:
            if other != key:
                self._co[key][other] += 1
                self._co[other][key] += 1
        self._recent.append(key)
        if len(self._co) > self._cap:
            for k in list(self._co)[: self._cap // 4]:
                del self._co[k]

    def partners(self, key: str, top: int = 2, min_count: int = 3) -> list[str]:
        """Keys most often seen within `window` requests of `key`, if any."""
        c = self._co.get(key)
        if not c:
            return []
        return [k for k, n in c.most_common(top) if n >= min_count]
