"""
Benchmark result store — SQLite.

Every run of the matrix appends to `results/aacms.db` so results are queryable
history, not one-shot JSON. Two tables:

    runs   — one row per (profile, scenario, policy): the whole-run summary
    epochs — one row per epoch of every run: the time series (drives charts,
             and lets us prove *when* AACMS pulls ahead, not just the total)

Schema is normalised on `run_id`; `epochs.run_id` -> `runs.run_id` (FK, indexed).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "results" / "aacms.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    ts                  TEXT NOT NULL,
    profile             TEXT NOT NULL,
    scenario            TEXT NOT NULL,
    policy              TEXT NOT NULL,
    requests            INTEGER NOT NULL,
    hit_rate            REAL NOT NULL,
    stale_rate          REAL NOT NULL,
    avg_latency_ms      REAL NOT NULL,
    p95_latency_ms      REAL NOT NULL,
    p99_latency_ms      REAL NOT NULL,
    cost_total          REAL NOT NULL,
    cost_origin         REAL NOT NULL,
    cost_latency        REAL NOT NULL,
    cost_memory         REAL NOT NULL,
    evictions           INTEGER NOT NULL,
    refreshes           INTEGER NOT NULL,
    final_capacity_bytes INTEGER NOT NULL,
    peak_used_bytes     INTEGER NOT NULL,
    config_json         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS epochs (
    run_id          TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    epoch           INTEGER NOT NULL,
    t_sim           REAL NOT NULL,
    requests        INTEGER NOT NULL,
    hit_rate        REAL NOT NULL,
    stale_rate      REAL NOT NULL,
    avg_latency_ms  REAL NOT NULL,
    p95_latency_ms  REAL NOT NULL,
    cost_total      REAL NOT NULL,
    cost_origin     REAL NOT NULL,
    cost_latency    REAL NOT NULL,
    cost_memory     REAL NOT NULL,
    capacity_bytes  INTEGER NOT NULL,
    used_bytes      INTEGER NOT NULL,
    entries         INTEGER NOT NULL,
    evictions       INTEGER NOT NULL,
    refreshes       INTEGER NOT NULL,
    regime          TEXT,
    bandit_arm      TEXT,
    w_core REAL, w_rec REAL, w_freq REAL, w_cost REAL, w_size REAL,
    PRIMARY KEY (run_id, epoch)
);
CREATE INDEX IF NOT EXISTS ix_runs_key   ON runs(profile, scenario, policy);
CREATE INDEX IF NOT EXISTS ix_epochs_run ON epochs(run_id);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


def save_run(conn: sqlite3.Connection, result, config: dict) -> str:
    run_id = uuid.uuid4().hex[:12]
    s = result.summary
    conn.execute(
        """INSERT INTO runs VALUES
           (:run_id,:ts,:profile,:scenario,:policy,:requests,:hit_rate,:stale_rate,
            :avg_latency_ms,:p95_latency_ms,:p99_latency_ms,:cost_total,:cost_origin,
            :cost_latency,:cost_memory,:evictions,:refreshes,:final_capacity_bytes,
            :peak_used_bytes,:config_json)""",
        {
            "run_id": run_id, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "profile": result.profile, "scenario": result.scenario, "policy": result.policy,
            "config_json": json.dumps(config),
            **{k: s[k] for k in (
                "requests", "hit_rate", "stale_rate", "avg_latency_ms", "p95_latency_ms",
                "p99_latency_ms", "cost_total", "cost_origin", "cost_latency", "cost_memory",
                "evictions", "refreshes", "final_capacity_bytes", "peak_used_bytes")},
        },
    )
    conn.executemany(
        """INSERT INTO epochs VALUES
           (:run_id,:epoch,:t_sim,:requests,:hit_rate,:stale_rate,:avg_latency_ms,
            :p95_latency_ms,:cost_total,:cost_origin,:cost_latency,:cost_memory,
            :capacity_bytes,:used_bytes,:entries,:evictions,:refreshes,:regime,:bandit_arm,
            :w_core,:w_rec,:w_freq,:w_cost,:w_size)""",
        [
            {
                "run_id": run_id, "epoch": e.epoch, "t_sim": e.t_sim, "requests": e.requests,
                "hit_rate": e.hit_rate, "stale_rate": e.stale_rate,
                "avg_latency_ms": e.avg_latency_ms, "p95_latency_ms": e.p95_latency_ms,
                "cost_total": e.cost_total, "cost_origin": e.cost_origin,
                "cost_latency": e.cost_latency, "cost_memory": e.cost_memory,
                "capacity_bytes": e.capacity_bytes, "used_bytes": e.used_bytes,
                "entries": e.entries, "evictions": e.evictions, "refreshes": e.refreshes,
                "regime": e.regime, "bandit_arm": e.bandit_arm,
                **{f"w_{k}": (e.weights or {}).get(k) for k in ("core", "rec", "freq", "cost", "size")},
            }
            for e in result.snapshots
        ],
    )
    conn.commit()
    return run_id


def savings_matrix(conn: sqlite3.Connection, profile: str, baseline: str = "GDSF") -> list[dict]:
    """Latest cost of every policy per scenario, and % saved vs `baseline`."""
    rows = conn.execute(
        """
        WITH latest AS (
          SELECT scenario, policy, cost_total, hit_rate, p95_latency_ms,
                 ROW_NUMBER() OVER (PARTITION BY scenario, policy ORDER BY ts DESC) rn
          FROM runs WHERE profile = ?
        )
        SELECT l.scenario, l.policy, l.cost_total, l.hit_rate, l.p95_latency_ms,
               b.cost_total AS base_cost
        FROM latest l
        JOIN (SELECT scenario, cost_total FROM latest WHERE policy = ? AND rn = 1) b
          ON b.scenario = l.scenario
        WHERE l.rn = 1
        ORDER BY l.scenario, l.cost_total
        """,
        (profile, baseline),
    ).fetchall()
    out = []
    for scen, pol, cost, hr, p95, base in rows:
        out.append({
            "scenario": scen, "policy": pol, "cost_total": round(cost, 3),
            "hit_rate": round(hr, 3), "p95_latency_ms": round(p95, 1),
            "saving_vs_%s_pct" % baseline: round(100 * (1 - cost / base), 1) if base else 0.0,
        })
    return out


def _cli() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="api")
    ap.add_argument("--baseline", default="GDSF")
    a = ap.parse_args()
    conn = connect()
    n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    print(f"{DB_PATH}  —  {n} runs stored\n")
    for r in savings_matrix(conn, a.profile, a.baseline):
        print(f"  {r['scenario']:18s} {r['policy']:12s} "
              f"hit={r['hit_rate']:.3f}  p95={r['p95_latency_ms']:8.1f}ms  "
              f"${r['cost_total']:8.3f}  vs {a.baseline} {r[f'saving_vs_{a.baseline}_pct']:+.1f}%")


if __name__ == "__main__":
    _cli()
