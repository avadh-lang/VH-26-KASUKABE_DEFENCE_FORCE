import {
  Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid, Area, AreaChart,
  ReferenceLine,
} from "recharts";

export const COLORS: Record<string, string> = {
  LRU: "#9aa0a6",
  LFU: "#c58af9",
  GDS: "#8ab4f8",
  GDSF: "#81c995",
  "AACMS-fixed": "#fbbc04",
  AACMS: "#f28b82",
};

type Row = Record<string, number>;

export function MultiLine({
  data, keys, pct, domain, markers, unit,
}: {
  data: Row[];
  keys: string[];
  pct?: boolean;
  domain?: [number | string, number | string];
  markers?: number[];
  unit?: string;
}) {
  return (
    <ResponsiveContainer width="100%" height={230}>
      <LineChart data={data} margin={{ top: 5, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#2a323d" strokeDasharray="2 4" />
        <XAxis dataKey="epoch" stroke="#8b949e" fontSize={11} />
        <YAxis
          stroke="#8b949e"
          fontSize={11}
          width={46}
          domain={domain ?? ["auto", "auto"]}
          tickFormatter={(v) => (pct ? `${Math.round(v * 100)}%` : `${v}`)}
        />
        <Tooltip
          contentStyle={{ background: "#161b22", border: "1px solid #2a323d", borderRadius: 8, fontSize: 12 }}
          formatter={(v: number) => (pct ? `${(v * 100).toFixed(1)}%` : `${v.toFixed(3)}${unit ?? ""}`)}
        />
        {(markers ?? []).map((m, i) => (
          <ReferenceLine key={i} x={m} stroke="#f28b82" strokeDasharray="3 3" label={{ value: "⚡", position: "top", fill: "#f28b82" }} />
        ))}
        {keys.map((k) => (
          <Line
            key={k}
            type="monotone"
            dataKey={k}
            stroke={COLORS[k] ?? "#ccc"}
            strokeWidth={k === "AACMS" ? 2.6 : 1.6}
            dot={false}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function CapacityChart({ data }: { data: Row[] }) {
  return (
    <ResponsiveContainer width="100%" height={230}>
      <AreaChart data={data} margin={{ top: 5, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#2a323d" strokeDasharray="2 4" />
        <XAxis dataKey="epoch" stroke="#8b949e" fontSize={11} />
        <YAxis stroke="#8b949e" fontSize={11} width={46} tickFormatter={(v) => `${v}MB`} />
        <Tooltip
          contentStyle={{ background: "#161b22", border: "1px solid #2a323d", borderRadius: 8, fontSize: 12 }}
          formatter={(v: number) => `${v.toFixed(1)} MB`}
        />
        <Area type="monotone" dataKey="capacity" stroke="#f28b82" fill="#f28b8233" strokeWidth={2} isAnimationActive={false} />
        <Area type="monotone" dataKey="used" stroke="#8ab4f8" fill="#8ab4f822" strokeWidth={1.5} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
