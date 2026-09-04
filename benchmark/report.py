"""
Turn a run matrix into the deliverable: a markdown report + charts.

    python -m benchmark.report --profile api        # uses results/bench_api.json
    python -m benchmark.report --run                 # run the matrix first, then report
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
FIGS = RESULTS / "figs"

PALETTE = {
    "LRU": "#9aa0a6", "LFU": "#c58af9", "GDS": "#8ab4f8",
    "GDSF": "#81c995", "AACMS": "#f28b82", "AACMS-fixed": "#fbbc04",
}


def _load(profile: str) -> dict:
    path = RESULTS / f"bench_{profile}.json"
    if not path.exists():
        raise SystemExit(f"{path} not found — run `python -m benchmark.runner --profile {profile}` first")
    return json.loads(path.read_text())


def _by_scenario(blob: dict) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for run in blob["runs"]:
        out.setdefault(run["scenario"], {})[run["policy"]] = run
    return out


def _line(ax, run: dict, field: str, label: str, color: str) -> None:
    xs = [e["t_sim"] for e in run["epochs"]]
    ys = [e[field] for e in run["epochs"]]
    ax.plot(xs, ys, label=label, color=color, lw=2)


def make_charts(blob: dict, profile: str) -> list[Path]:
    FIGS.mkdir(parents=True, exist_ok=True)
    scen_map = _by_scenario(blob)
    written: list[Path] = []

    for scenario, policies in scen_map.items():
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
        for pname, run in policies.items():
            c = PALETTE.get(pname, "#5f6368")
            _line(axes[0], run, "hit_rate", pname, c)
            _line(axes[1], run, "cost_total", pname, c)
            _line(axes[2], run, "p95_latency_ms", pname, c)
        axes[0].set_title(f"{scenario} — hit rate"); axes[0].set_ylim(0, 1)
        axes[1].set_title(f"{scenario} — cumulative cost ($)")
        axes[2].set_title(f"{scenario} — p95 latency (ms)")
        for ax in axes:
            ax.set_xlabel("sim seconds"); ax.legend(fontsize=8); ax.grid(alpha=0.25)
        fig.tight_layout()
        p = FIGS / f"{profile}_{scenario}.png"
        fig.savefig(p, dpi=110); plt.close(fig); written.append(p)

    # AACMS internals on the spike scenario (weights + capacity over time)
    spike = scen_map.get("spike") or next(iter(scen_map.values()))
    aacms = spike.get("AACMS")
    if aacms:
        wkeys = [k for k in aacms["epochs"][0] if k.startswith("w_")]
        fig, ax = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
        xs = [e["t_sim"] for e in aacms["epochs"]]
        for wk in wkeys:
            ax[0].plot(xs, [e[wk] for e in aacms["epochs"]], label=wk[2:], lw=2)
        ax[0].set_title("AACMS — value-score weights chosen by the bandit"); ax[0].legend(fontsize=8)
        ax[0].grid(alpha=0.25)
        ax[1].plot(xs, [e["capacity_bytes"] / 1e6 for e in aacms["epochs"]], color="#f28b82", lw=2, label="capacity")
        ax[1].plot(xs, [e["used_bytes"] / 1e6 for e in aacms["epochs"]], color="#8ab4f8", lw=1.5, label="used")
        ax[1].set_title("AACMS — cache capacity (autoscaler)"); ax[1].set_xlabel("sim seconds")
        ax[1].set_ylabel("MB"); ax[1].legend(fontsize=8); ax[1].grid(alpha=0.25)
        fig.tight_layout()
        p = FIGS / f"{profile}_aacms_internals.png"
        fig.savefig(p, dpi=110); plt.close(fig); written.append(p)

    return written


def make_report(blob: dict, profile: str, figs: list[Path]) -> Path:
    scen_map = _by_scenario(blob)
    lines: list[str] = [
        f"# AACMS benchmark — `{profile}` profile",
        "",
        f"_generated {blob['generated_at']}_",
        "",
        "Cost model: managed-cache RAM @ $0.12/GB-hr, origin $ per regeneration from "
        "the object catalog, latency business-cost @ $2e-6/ms. Identical workload and "
        "cost rules for every policy.",
        "",
    ]

    for scenario, policies in scen_map.items():
        lines += [f"## {scenario}", "", "| policy | hit rate | stale rate | p95 latency (ms) | total cost ($) | vs GDSF | vs LRU |",
                  "|---|---|---|---|---|---|---|"]
        gdsf = policies.get("GDSF", {}).get("summary", {}).get("cost_total")
        lru = policies.get("LRU", {}).get("summary", {}).get("cost_total")
        order = ["LRU", "LFU", "GDS", "GDSF", "AACMS-fixed", "AACMS"]
        for pname in order:
            run = policies.get(pname)
            if not run:
                continue
            s = run["summary"]
            vg = f"{100 * (1 - s['cost_total'] / gdsf):+.1f}%" if gdsf else "—"
            vl = f"{100 * (1 - s['cost_total'] / lru):+.1f}%" if lru else "—"
            lines.append(
                f"| {pname} | {s['hit_rate']:.3f} | {s['stale_rate']:.3f} | "
                f"{s['p95_latency_ms']:.1f} | {s['cost_total']:.3f} | {vg} | {vl} |"
            )
        cap = policies.get("AACMS", {}).get("summary", {}).get("final_capacity_bytes")
        if cap:
            lines += ["", f"AACMS autoscaler settled at **{cap/1e6:.0f} MB** "
                      f"(started at {policies['AACMS']['epochs'][0]['capacity_bytes']/1e6:.0f} MB).", ""]

    lines += ["## Charts", ""]
    for p in figs:
        lines.append(f"![{p.stem}](figs/{p.name})")
        lines.append("")

    out = RESULTS / f"REPORT_{profile}.md"
    out.write_text("\n".join(lines))
    return out


def _cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="api", choices=["api", "recsys"])
    ap.add_argument("--run", action="store_true", help="run the benchmark matrix first")
    ap.add_argument("--scenarios", nargs="+", default=["steady", "spike", "popularity_shift"])
    a = ap.parse_args()

    if a.run:
        from benchmark.runner import run_matrix
        run_matrix(a.scenarios, profile=a.profile)

    blob = _load(a.profile)
    figs = make_charts(blob, a.profile)
    report = make_report(blob, a.profile, figs)
    print(f"wrote {report} and {len(figs)} charts under {FIGS}")


if __name__ == "__main__":
    _cli()
