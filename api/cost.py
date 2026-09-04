"""
Cost-awareness layer.

Tracks running infra cost for every policy in a live run and reports the
saving of each policy against a chosen baseline (default LRU). This is the
component the PS calls "a cost-awareness layer that models infrastructure
cost (memory, compute, API calls) and demonstrates measurable savings".
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PolicyCost:
    origin: float = 0.0        # regeneration / external API $
    latency: float = 0.0       # latency business $
    memory: float = 0.0        # cache RAM $
    hits: int = 0
    misses: int = 0

    @property
    def total(self) -> float:
        return self.origin + self.latency + self.memory


@dataclass
class CostLedger:
    baseline: str = "LRU"
    _p: dict[str, PolicyCost] = field(default_factory=dict)

    def update(self, policy: str, *, origin: float, latency: float, memory: float,
               hits: int, misses: int) -> None:
        c = self._p.setdefault(policy, PolicyCost())
        c.origin, c.latency, c.memory = origin, latency, memory
        c.hits, c.misses = hits, misses

    def report(self) -> dict:
        base = self._p.get(self.baseline)
        base_total = base.total if base else 0.0
        rows = []
        for name, c in self._p.items():
            saving = (base_total - c.total)
            rows.append({
                "policy": name,
                "cost_total": round(c.total, 5),
                "cost_origin": round(c.origin, 5),
                "cost_latency": round(c.latency, 5),
                "cost_memory": round(c.memory, 5),
                "hit_rate": round(c.hits / max(c.hits + c.misses, 1), 4),
                "saving_vs_baseline": round(saving, 5),
                "saving_pct": round(100 * saving / base_total, 1) if base_total else 0.0,
            })
        rows.sort(key=lambda r: r["cost_total"])
        return {"baseline": self.baseline, "rows": rows}
