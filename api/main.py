"""
CACHE MIND API — starts live simulations and streams per-epoch metrics (SSE) to the
React dashboard. Also serves the built dashboard as static files in production.

    uvicorn api.main:app --reload
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from workload import SCENARIOS
from workload.catalog import PROFILES
from api.live import LiveSim

app = FastAPI(title="CACHE MIND", version="1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_SIMS: dict[str, LiveSim] = {}
POLICIES = ["LRU", "LFU", "GDS", "GDSF", "GDSF-tiered", "CM-notier", "CACHE MIND"]


class StartReq(BaseModel):
    scenario: str = "steady"
    profile: str = "api"
    policies: list[str] = ["LRU", "LFU", "GDS", "GDSF", "CACHE MIND"]
    speed: float = 8.0            # epochs per real second


class ScenarioReq(BaseModel):
    scenario: str


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "sims": len(_SIMS)}


@app.get("/api/meta")
def meta() -> dict:
    return {"scenarios": list(SCENARIOS), "profiles": list(PROFILES), "policies": POLICIES}


@app.post("/api/sim/start")
def start(req: StartReq) -> dict:
    if req.scenario not in SCENARIOS:
        raise HTTPException(400, f"bad scenario; pick from {list(SCENARIOS)}")
    if req.profile not in PROFILES:
        raise HTTPException(400, f"bad profile; pick from {list(PROFILES)}")
    run_id = uuid.uuid4().hex[:8]
    _SIMS[run_id] = LiveSim(
        req.scenario, req.profile,
        policies=[p for p in req.policies if p in POLICIES] or None,
        epoch_seconds=10.0,
    )
    _SIMS[run_id]._speed = max(0.5, min(req.speed, 40.0))  # type: ignore[attr-defined]
    return {"run_id": run_id, "start_capacity_mb": round(_SIMS[run_id].start_capacity / 1e6, 2)}


def _sim(run_id: str) -> LiveSim:
    s = _SIMS.get(run_id)
    if not s:
        raise HTTPException(404, "unknown run_id (start a sim first)")
    return s


@app.post("/api/sim/{run_id}/spike")
def spike(run_id: str) -> dict:
    _sim(run_id).inject_spike()
    return {"ok": True, "msg": "flash crowd injected for the next few epochs"}


@app.post("/api/sim/{run_id}/scenario")
def scenario(run_id: str, req: ScenarioReq) -> dict:
    if req.scenario not in SCENARIOS:
        raise HTTPException(400, "bad scenario")
    _sim(run_id).set_scenario(req.scenario)
    return {"ok": True, "scenario": req.scenario}


@app.get("/api/sim/{run_id}/cost")
def cost(run_id: str) -> dict:
    return _sim(run_id).ledger.report()


@app.delete("/api/sim/{run_id}")
def stop(run_id: str) -> dict:
    _SIMS.pop(run_id, None)
    return {"ok": True}


@app.get("/api/sim/{run_id}/stream")
async def stream(run_id: str):
    sim = _sim(run_id)
    speed = getattr(sim, "_speed", 8.0)

    async def gen():
        while run_id in _SIMS:
            frame = await asyncio.to_thread(sim.step)
            yield {"event": "epoch", "data": json.dumps(frame)}
            if frame["t"] >= sim.duration_s:
                yield {"event": "done", "data": "{}"}
                return
            await asyncio.sleep(1.0 / speed)

    return EventSourceResponse(gen())


# -- serve the built dashboard (optional; only if dashboard/dist exists) --- #
_DIST = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="dashboard")
