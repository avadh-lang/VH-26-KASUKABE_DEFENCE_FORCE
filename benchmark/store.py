"""
Benchmark result store — SQLite.

Every run of the matrix appends to `results/cachemind.db` so results are
queryable history, not one-shot JSON. Two tables:

    runs   — one row per (profile, scenario, policy): the whole-run summary
    epochs — one row per epoch of every run: the time series, kept as a JSON
             blob plus a few indexed columns (schema-stable as EpochSnapshot grows)

Schema is normalised on `run_id`; `epochs.run_id` -> `runs.run_id` (FK, indexed).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "results" / "cachemind.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, ts TEXT NOT NULL,
    profile TEXT NOT NULL, scenario TEXT NOT NULL, policy TEXT NOT NULL,
    requests INTEGER, hit_rate REAL, stale_rate REAL,
    l1_rate REAL, l2_rate REAL, l3_rate REAL,
    avg_latency_ms REAL, p95_latency_ms REAL, p99_latency_ms REAL,
    cost_total REAL, cost_origin REAL, cost_latency REAL, cost_memory REAL, cost_move REAL,
    evictions INTEGER, refreshes INTEGER, promotions INTEGER, demotions INTEGER, prefetches INTEGER,
    final_capacity_bytes INTEGER, peak_used_bytes INTEGER, config_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS epochs (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    epoch INTEGER NOT NULL, t_sim REAL,
    hit_rate REAL, p95_latency_ms REAL, cost_total REAL,
    l1_used INTEGER, l2_used INTEGER, l3_used INTEGER,
    regime TEXT, bandit_arm TEXT, row_json TEXT NOT NULL,
    PRIMARY KEY (run_id, epoch)
);
CREATE INDEX IF NOT EXISTS ix_runs_key   ON runs(profile, scenario, policy);
CREATE INDEX IF NOT EXISTS ix_epochs_run ON epochs(run_id);
"""

_RUN_COLS = ("requests", "hit_rate", "stale_rate", "l1_rate", "l2_rate", "l3_rate",
             "avg_latency_ms", "p95_latency_ms", "p99_latency_ms",
             "cost_total", "cost_origin", "cost_latency", "cost_memory", "cost_move",
             "evictions", "refreshes", "promotions", "demotions", "prefetches",
             "final_capacity_bytes", "peak_used_bytes")


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
        f"INSERT INTO runs (run_id, ts, profile, scenario, policy, {', '.join(_RUN_COLS)}, config_json) "
        f"VALUES ({','.join('?' * (5 + len(_RUN_COLS) + 1))})",
        (run_id, time.strftime("%Y-%m-%dT%H:%M:%S"), result.profile, result.scenario, result.policy,
         *[s.get(c, 0) for c in _RUN_COLS], json.dumps(config)),
    )
    conn.executemany(
        "INSERT INTO epochs (run_id, epoch, t_sim, hit_rate, p95_latency_ms, cost_total, "
        "l1_used, l2_used, l3_used, regime, bandit_arm, row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(run_id, e.epoch, e.t_sim, e.hit_rate, e.p95_latency_ms, e.cost_total,
          e.l1_used, e.l2_used, e.l3_used, e.regime, e.bandit_arm, json.dumps(e.as_row()))
         for e in result.snapshots],
    )
    conn.commit()
    return run_id


def savings_matrix(conn: sqlite3.Connection, profile: str, baseline: str = "GDSF-tiered") -> list[dict]:
    rows = conn.execute(
        """
        WITH latest AS (
          SELECT scenario, policy, cost_total, hit_rate, p95_latency_ms,
                 ROW_NUMBER() OVER (PARTITION BY scenario, policy ORDER BY ts DESC) rn
          FROM runs WHERE profile = ?)
        SELECT l.scenario, l.policy, l.cost_total, l.hit_rate, l.p95_latency_ms, b.cost_total AS base
        FROM latest l
        JOIN (SELECT scenario, cost_total FROM latest WHERE policy = ? AND rn = 1) b
          ON b.scenario = l.scenario
        WHERE l.rn = 1 ORDER BY l.scenario, l.cost_total
        """, (profile, baseline)).fetchall()
    out = []
    for scen, pol, cost, hr, p95, base in rows:
        out.append({"scenario": scen, "policy": pol, "cost_total": round(cost, 3),
                    "hit_rate": round(hr, 3), "p95_latency_ms": round(p95, 1),
                    "saving_pct": round(100 * (1 - cost / base), 1) if base else 0.0})
    return out


def _cli() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="api")
    ap.add_argument("--baseline", default="GDSF-tiered")
    a = ap.parse_args()
    conn = connect()
    n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    print(f"{DB_PATH}  —  {n} runs stored\n")
    for r in savings_matrix(conn, a.profile, a.baseline):
        print(f"  {r['scenario']:18s} {r['policy']:13s} hit={r['hit_rate']:.3f}  "
              f"p95={r['p95_latency_ms']:7.1f}ms  ${r['cost_total']:8.3f}  "
              f"vs {a.baseline} {r['saving_pct']:+.1f}%")


if __name__ == "__main__":

    _cli()
