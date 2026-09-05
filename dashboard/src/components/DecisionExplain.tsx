import type { Decision } from "../api";

const ACTION_COLOR: Record<string, string> = {
  evict: "var(--bad)",
  "admit L2": "var(--blue)", "admit L3": "var(--blue)",
};

/**
 * The real signals behind one decision, not just the action that was taken.
 * `decision` is the most recent one CACHE MIND has made that carries a full
 * breakdown (evictions, admission rejections, and tier moves all do —
 * refresh/prefetch notes don't, their reason line already says everything).
 * The caller is responsible for remembering it across epochs — refresh
 * decisions are so frequent they'd otherwise crowd a rarer eviction out of
 * a short recent-decisions window before anyone saw it.
 */
export function DecisionExplain({ decision }: { decision: Decision | null }) {
  const e = decision?.explain;
  if (!decision || !e) {
    return (
      <div className="explain empty-row">waiting for a decision with a full breakdown…</div>
    );
  }
  const d = decision;
  const color = ACTION_COLOR[d.action] ?? (d.action.includes("->") ? "var(--amber)" : "var(--muted)");
  const rows: [string, string][] = [
    ["Future access probability", `${(e.p_soon * 100).toFixed(0)}%`],
    ["Prediction confidence", `${(e.confidence * 100).toFixed(0)}%`],
    ["Regeneration cost", `${e.regen_ms} ms · $${e.regen_usd}`],
    ["Memory footprint", `${e.size_kb} KB`],
    ["Freshness", `${(e.freshness * 100).toFixed(0)}%`],
    ["Access trend", e.trend >= 1 ? `${e.trend.toFixed(2)}x heating up` : `${e.trend.toFixed(2)}x cooling`],
  ];
  return (
    <div className="explain">
      <div className="explain-obj">object <b>{d.key}</b></div>
      <div className="explain-rows">
        {rows.map(([label, val]) => (
          <div className="explain-row" key={label}>
            <span className="explain-label">{label}</span>
            <span className="explain-val">{val}</span>
          </div>
        ))}
      </div>
      <div className="explain-score">
        <span>final score</span>
        <b>{e.score}</b>
      </div>
      <div className="explain-verdict">
        <span className="explain-dot" style={{ background: color, boxShadow: `0 0 8px ${color}` }} />
        <span className="explain-action" style={{ color }}>{d.action}</span>
      </div>
      <div className="explain-reason">{d.reason}</div>
    </div>
  );
}
