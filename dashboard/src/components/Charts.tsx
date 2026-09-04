import {
  Line, ComposedChart, Area, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid,
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
  const hero = "CACHE MIND";
  const heroFirst = keys.includes(hero) ? [hero, ...keys.filter((k) => k !== hero)] : keys;
  return (
    <ResponsiveContainer width="100%" height={230}>
      <ComposedChart data={data} margin={{ top: 5, right: 12, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="heroFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={COLORS[hero]} stopOpacity={0.28} />
            <stop offset="100%" stopColor={COLORS[hero]} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#1c232d" strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="epoch" stroke="#5d6674" fontSize={10.5} tickLine={false} axisLine={{ stroke: "#262e3a" }} />
        <YAxis
          stroke="#5d6674"
          fontSize={10.5}
          width={46}
          tickLine={false}
          axisLine={{ stroke: "#262e3a" }}
          scale={log ? "log" : "auto"}
          domain={domain ?? (log ? [0.4, "auto"] : ["auto", "auto"])}
          allowDataOverflow={!!log}
          tickFormatter={(v) => (pct ? `${Math.round(v * 100)}%` : `${v}`)}
        />
        <Tooltip
          contentStyle={{
            background: "rgba(18,22,29,0.96)", border: "1px solid #262e3a", borderRadius: 10,
            fontSize: 12, fontFamily: "ui-monospace, monospace", boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
          }}
          labelStyle={{ color: "#8b96a5", marginBottom: 4 }}
          formatter={(v: number, name: string) => [
            pct ? `${(v * 100).toFixed(1)}%` : `${v.toFixed(3)}${unit ?? ""}`,
            name,
          ]}
        />
        {(markers ?? []).map((m, i) => (
          <ReferenceLine key={i} x={m} stroke="#f28b82" strokeDasharray="3 3" strokeOpacity={0.7}
            label={{ value: "⚡", position: "top", fill: "#f28b82" }} />
        ))}
        {keys.includes(hero) && (
          <Area type="monotone" dataKey={hero} stroke="none" fill="url(#heroFill)"
            isAnimationActive={false} activeDot={false} legendType="none" />
        )}
        {heroFirst.map((k) => (
          <Line key={k} type="monotone" dataKey={k} stroke={COLORS[k] ?? "#ccc"}
            strokeWidth={k === hero ? 2.75 : 1.5}
            strokeOpacity={k === hero ? 1 : 0.75}
            dot={false}
            activeDot={{ r: 3.5, strokeWidth: 0 }}
            isAnimationActive={false} />
        ))}
      </ComposedChart>
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
                    background: `${TIER_COLOR[i]}18`,
                    borderRight: i < 2 ? "1px solid #262e3a" : "none",
                  }}>
                  <span className="tfill" style={{
                    width: `${Math.min(100, (t.used_mb / (t.cap_mb || 1)) * 100)}%`,
                    background: TIER_COLOR[i],
                    boxShadow: `0 0 8px ${TIER_COLOR[i]}66`,
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
