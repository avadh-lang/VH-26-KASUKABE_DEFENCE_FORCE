"""
Cost-benefit cache autoscaler.

The PS is explicit: scale "when additional cache capacity is actually
justified (cost-benefit tradeoff), rather than scaling reactively/blindly."

Mechanism — a **ghost list** (a.k.a. shadow list, as in ARC/LIRS): when an
entry is evicted we keep its key + size + the $-equiv cost it would take to
regenerate, but not its data. If that key is requested again soon, that is a
miss we *would* have avoided with a bigger cache.

Each epoch:
    benefit(grow by Δ) ≈ capturable_fraction * Σ ghost-hit regeneration cost
    cost(grow by Δ)    = memory price of Δ bytes for one epoch
    grow  if benefit  > cost * grow_margin
    shrink if the cache is loose (low fill, ~no evictions, ~no ghost hits)
"""

from __future__ import annotations

from collections import OrderedDict

from common import CostConfig


class GhostList:
    def __init__(self, capacity: int = 20_000):
        self._cap = capacity
        self._d: OrderedDict[str, tuple[int, float]] = OrderedDict()  # key -> (size, regen_usd_equiv)

    def add(self, key: str, size: int, regen_usd_equiv: float) -> None:
        self._d[key] = (size, regen_usd_equiv)
        self._d.move_to_end(key)
        if len(self._d) > self._cap:
            self._d.popitem(last=False)

    def hit(self, key: str) -> tuple[int, float] | None:
        v = self._d.pop(key, None)
        return v

    def __len__(self) -> int:
        return len(self._d)


class Autoscaler:
    def __init__(
        self,
        cost_cfg: CostConfig,
        *,
        min_bytes: int,
        max_bytes: int,
        epoch_seconds: float,
        grow_margin: float = 1.4,
        cooldown_epochs: int = 2,
    ):
        self.cfg = cost_cfg
        self.min_bytes = int(min_bytes)
        self.max_bytes = int(max_bytes)
        self.epoch_seconds = epoch_seconds
        self.grow_margin = grow_margin
        self.cooldown = cooldown_epochs
        self._cool = 0
        self._loose_streak = 0
        # rolling epoch accumulators
        self._ghost_hits = 0
        self._ghost_value = 0.0
        self._ghost_bytes = 0
        self.last_action = "hold"
        self.last_reason = ""

    def record_ghost_hit(self, size: int, regen_usd_equiv: float) -> None:
        self._ghost_hits += 1
        self._ghost_value += regen_usd_equiv
        self._ghost_bytes += size

    def decide(
        self, *, capacity: int, used: int, evictions: int, requests: int, cold_bytes: int = 0,
    ) -> tuple[int, str, str]:
        """
        cold_bytes — bytes of resident entries not touched in the last couple of
        epochs. If we're holding more than a scale step of cold data and nothing
        is under pressure, that RAM is being wasted and we can release it.
        """
        step = self.cfg.scale_step_bytes
        action, reason = "hold", ""
        new_cap = capacity

        if self._cool > 0:
            self._cool -= 1
        else:
            grow_cost = self.cfg.memory_usd(step, self.epoch_seconds)
            capturable = min(1.0, step / self._ghost_bytes) if self._ghost_bytes else 0.0
            benefit = capturable * self._ghost_value
            evict_rate = evictions / max(requests, 1)

            if benefit > grow_cost * self.grow_margin and capacity + step <= self.max_bytes:
                new_cap = capacity + step
                action = "grow"
                reason = (f"ghost hits={self._ghost_hits} worth ${self._ghost_value:.4f}; "
                          f"+{step // 1024}KB pays back {benefit / max(grow_cost,1e-12):.1f}x its ${grow_cost:.5f} cost")
                self._cool = self.cooldown
                self._loose_streak = 0
            elif evict_rate < 0.003 and self._ghost_hits == 0 and cold_bytes >= step:
                self._loose_streak += 1
                if self._loose_streak >= self.cooldown and capacity - step >= self.min_bytes:
                    new_cap = capacity - step
                    action = "shrink"
                    reason = (f"{cold_bytes // 1024}KB cold, evict-rate={evict_rate:.3%}, "
                              f"no ghost hits for {self._loose_streak} epochs; release "
                              f"{step // 1024}KB, save ${grow_cost:.5f}/epoch")
                    self._cool = self.cooldown
                    self._loose_streak = 0
            else:
                self._loose_streak = 0

        self._ghost_hits = 0
        self._ghost_value = 0.0
        self._ghost_bytes = 0
        self.last_action, self.last_reason = action, reason
        return new_cap, action, reason
