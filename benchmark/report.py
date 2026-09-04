"""
Turn a run matrix into the deliverable: markdown tables + charts.

    python -m benchmark.report --run --profile api
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
    "LRU": "#9aa0a6", "LFU": "#c58af9", "GDS": "#8ab4f8", "GDSF": "#81c995",
    "LRU-tiered": "#5b9bd5", "GDSF-tiered": "#3fae6b",
    "CM-fixed": "#fbbc04", "CACHE MIND": "#f28b82",
}
ORDER = ["LRU", "LFU", "GDS", "GDSF", "LRU-tiered", "GDSF-tiered", "CM-fixed", "CACHE MIND"]


def _load(profile: str) -> dict:
    path = RESULTS / f"bench_{profile}.json"
    if not path.exists():
        raise SystemExit(f"{path} not found — run with --run first")
    return json.loads(path.read_text())


def _by_scenario(blob: dict) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for run in blob["runs"]:
        out.setdefault(run["scenario"], {})[run["policy"]] = run
    return out




def make_charts(blob: dict, profile: str) -> list[Path]:
    FIGS.mkdir(parents=True, exist_ok=True)
    scen_map = _by_scenario(blob)
    written: list[Path] = []

    for scenario, policies in scen_map.items():
        fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))
        for pname, run in policies.items():
            c = PALETTE.get(pname, "#666")
            xs = [e["t_sim"] for e in run["epochs"]]
            ax[0].plot(xs, [e["hit_rate"] for e in run["epochs"]], label=pname, color=c, lw=2)
            ax[1].plot(xs, [e["cost_total"] for e in run["epochs"]], label=pname, color=c, lw=2)
            ax[2].plot(xs, [e["p95_latency_ms"] for e in run["epochs"]], label=pname, color=c, lw=2)
        ax[0].set_title(f"{scenario} — hit rate"); ax[0].set_ylim(0, 1)
        ax[1].set_title(f"{scenario} — cumulative cost $")
        ax[2].set_title(f"{scenario} — p95 latency ms"); ax[2].set_yscale("log")
        for a in ax:
            a.set_xlabel("sim seconds"); a.legend(fontsize=7); a.grid(alpha=0.25)
        fig.tight_layout()
        p = FIGS / f"{profile}_{scenario}.png"
        fig.savefig(p, dpi=110); plt.close(fig); written.append(p)

    # CACHE MIND tier occupancy + weights on the spike scenario
    spike = scen_map.get("spike") or next(iter(scen_map.values()))
    cm = spike.get("CACHE MIND")
    if cm:
        fig, ax = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
        xs = [e["t_sim"] for e in cm["epochs"]]
        for key, col in (("l1_used", "#f28b82"), ("l2_used", "#8ab4f8"), ("l3_used", "#81c995")):
            ax[0].plot(xs, [e.get(key, 0) / 1e6 for e in cm["epochs"]], label=key[:2].upper(), color=col, lw=2)
        ax[0].set_title("CACHE MIND — bytes held per tier (MB)"); ax[0].legend(fontsize=8); ax[0].grid(alpha=0.25)
        wkeys = [k for k in cm["epochs"][0] if k.startswith("w_")]
        for wk in wkeys:
            ax[1].plot(xs, [e.get(wk, 0) for e in cm["epochs"]], label=wk[2:], lw=1.8)
        ax[1].set_title("CACHE MIND — value-score weights (bandit)"); ax[1].set_xlabel("sim seconds")
        ax[1].legend(fontsize=8); ax[1].grid(alpha=0.25)
        fig.tight_layout()
        p = FIGS / f"{profile}_cachemind_internals.png"
        fig.savefig(p, dpi=110); plt.close(fig); written.append(p)
    return written


def make_report(blob: dict, profile: str, figs: list[Path]) -> Path:
    scen_map = _by_scenario(blob)
    lines = [f"# CACHE MIND benchmark — `{profile}` profile", "",
             f"_generated {blob['generated_at']}_", "",
             "Same workload and cost model for every policy. Single-tier policies "
             "use one RAM tier; `*-tiered` and `CACHE MIND` get the same L1/L2/L3 "
             "hardware (L1 = 12 % of the working set, L2 = 4×L1, L3 = 12×L1).", ""]

    for scenario, policies in scen_map.items():
        lines += [f"## {scenario}", "",
                  "| policy | hit rate | p95 ms | cost $ | vs GDSF | vs GDSF-tiered |",
                  "|---|---|---|---|---|---|"]
        gdsf = policies.get("GDSF", {}).get("summary", {}).get("cost_total")
        gt = policies.get("GDSF-tiered", {}).get("summary", {}).get("cost_total")
        for pname in ORDER:
            run = policies.get(pname)
            if not run:
                continue
            s = run["summary"]
            vg = f"{100*(1-s['cost_total']/gdsf):+.0f}%" if gdsf else "—"
            vt = f"{100*(1-s['cost_total']/gt):+.0f}%" if gt else "—"
            lines.append(f"| {pname} | {s['hit_rate']:.3f} | {s['p95_latency_ms']:.0f} | "
                         f"{s['cost_total']:.2f} | {vg} | {vt} |")
        lines.append("")

    lines += ["## Charts", ""]
    for p in figs:
        lines += [f"![{p.stem}](figs/{p.name})", ""]

    out = RESULTS / f"REPORT_{profile}.md"
    out.write_text("\n".join(lines))
    return out


def _cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="api", choices=["api", "recsys"])
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--scenarios", nargs="+",
                    default=["steady", "spike", "popularity_shift", "regime_flip"])
    a = ap.parse_args()
    if a.run:
        from benchmark.runner import run_matrix
        run_matrix(a.scenarios, profile=a.profile)
    blob = _load(a.profile)
    figs = make_charts(blob, a.profile)
    report = make_report(blob, a.profile, figs)
    print(f"wrote {report} and {len(figs)} charts")


if __name__ == "__main__":
    _cli()
