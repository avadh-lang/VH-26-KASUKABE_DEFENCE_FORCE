import {
  Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid,
  ReferenceLine,
} from "recharts";
import type { PolicySnap } from "../api";

export const COLORS: Record<string, string> = {
  LRU: "#9aa0a6",
  LFU: "#c58af9",
  GDS: "#8ab4f8",
  GDSF: "#81c995",
  "LRU-tiered": "#5b9bd5",
  "GDSF-tiered": "#3fae6b",
  "CM-notier": "#fbbc04",
  "CACHE MIND": "#f28b82",
};

type Row = Record<string, number>;

export function MultiLine({
  data, keys, pct, domain, markers, unit, log,
}: {
  data: Row[];
  keys: string[];
  pct?: boolean;
  domain?: [number | string, number | string];
  markers?: number[];
  unit?: string;
  log?: boolean;
}) {
  return (
    <ResponsiveContainer width="100%" height={225}>
      <LineChart data={data} margin={{ top: 5, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#2a323d" strokeDasharray="2 4" />
        <XAxis dataKey="epoch" stroke="#8b949e" fontSize={11} />
        <YAxis
          stroke="#8b949e"
          fontSize={11}
          width={46}
          scale={log ? "log" : "auto"}
          domain={domain ?? (log ? [0.4, "auto"] : ["auto", "auto"])}
          allowDataOverflow={!!log}
          tickFormatter={(v) => (pct ? `${Math.round(v * 100)}%` : `${v}`)}
        />
        <Tooltip
          contentStyle={{ background: "#161b22", border: "1px solid #2a323d", borderRadius: 8, fontSize: 12 }}
          formatter={(v: number) => (pct ? `${(v * 100).toFixed(1)}%` : `${v.toFixed(3)}${unit ?? ""}`)}
        />
        {(markers ?? []).map((m, i) => (
          <ReferenceLine key={i} x={m} stroke="#f28b82" strokeDasharray="3 3"
            label={{ value: "⚡", position: "top", fill: "#f28b82" }} />
        ))}
        {keys.map((k) => (
          <Line key={k} type="monotone" dataKey={k} stroke={COLORS[k] ?? "#ccc"}
            strokeWidth={k === "CACHE MIND" ? 2.6 : 1.6} dot={false} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

const TIER_COLOR = ["#f28b82", "#8ab4f8", "#81c995"];

export function TierBars({ policies }: { policies: PolicySnap[] }) {
  const withTiers = policies.filter((p) => p.tiers && p.tiers.length);
  if (!withTiers.length) return <div className="empty" style={{ padding: 30 }}>no tiered policy</div>;
  return (
    <div className="tierwrap">
      {withTiers.map((p) => {
        const total = p.tiers!.reduce((s, t) => s + t.cap_mb, 0) || 1;
        return (
          <div className="tierrow" key={p.policy}>
            <span className="tname">{p.policy}</span>
            <span className="tbar">
              {p.tiers!.map((t, i) => (
                <span key={t.tier} title={`${t.tier}: ${t.used_mb}/${t.cap_mb} MB`}
                  style={{
                    width: `${(t.cap_mb / total) * 100}%`,
                    background: `${TIER_COLOR[i]}22`,
                    borderRight: i < 2 ? "1px solid #2a323d" : "none",
                  }}>
                  <span className="tfill" style={{
                    width: `${Math.min(100, (t.used_mb / (t.cap_mb || 1)) * 100)}%`,
                    background: TIER_COLOR[i],
                  }} />
                </span>
              ))}
            </span>
            <span className="tnums">
              {p.tiers!.map((t) => `${t.used_mb.toFixed(0)}`).join(" / ")} MB
            </span>
          </div>
        );
      })}
      <div className="tierlegend">
        <span><i style={{ background: TIER_COLOR[0] }} />L1 RAM</span>
        <span><i style={{ background: TIER_COLOR[1] }} />L2 Redis</span>
        <span><i style={{ background: TIER_COLOR[2] }} />L3 cold</span>
      </div>
    </div>
  );
}
