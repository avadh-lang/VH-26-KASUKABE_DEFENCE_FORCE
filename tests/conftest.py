"""Shared fixtures for the AACMS test suite."""

from __future__ import annotations

import pytest

from common import CostConfig, ObjectSpec


@pytest.fixture
def cost_cfg() -> CostConfig:
    return CostConfig()


def spec(key: str, *, size=1_000, lat=100.0, cost=1e-3, ttl=1e9, vol=0.0) -> ObjectSpec:
    return ObjectSpec(key=key, size_bytes=size, gen_latency_ms=lat,
                      gen_cost_usd=cost, ttl_s=ttl, volatility=vol)


@pytest.fixture
def mk_spec():
    return spec
