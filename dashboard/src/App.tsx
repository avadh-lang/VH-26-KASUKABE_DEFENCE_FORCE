import { useEffect, useMemo, useRef, useState } from "react";
import {
  Frame, Meta, PolicySnap, getMeta, injectSpike, setScenario, startSim, stopSim, streamSim,
} from "./api";
import { COLORS, MultiLine, TierBars } from "./components/Charts";

const DEFAULT_POLICIES = ["LRU", "LFU", "GDS", "GDSF", "CACHE MIND"];
const ME = "CACHE MIND";

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [scenario, setScen] = useState("steady");
  const [profile, setProfile] = useState("api");
  const [speed, setSpeed] = useState(8);
  const [runId, setRunId] = useState<string | null>(null);
  const [frames, setFrames] = useState<Frame[]>([]);
  const [spikeMarks, setSpikeMarks] = useState<number[]>([]);
  const esRef = useRef<EventSource | null>(null);
  const wasSpiking = useRef(false);

  useEffect(() => {
    getMeta().then(setMeta).catch(() => {});
    return () => esRef.current?.close();
  }, []);

  const latest = frames.at(-1) ?? null;
  const running = !!runId;

  async function start() {
    stop();
    setFrames([]); setSpikeMarks([]); wasSpiking.current = false;
    const { run_id } = await startSim({ scenario, profile, policies: DEFAULT_POLICIES, speed });
    setRunId(run_id);
    esRef.current = streamSim(run_id, (f) => {
      if (f.spike_active && !wasSpiking.current) setSpikeMarks((m) => [...m, f.epoch]);
      wasSpiking.current = f.spike_active;
      setFrames((prev) => (prev.length > 240 ? [...prev.slice(-240), f] : [...prev, f]));
    });
  }
  function stop() {
    esRef.current?.close(); esRef.current = null;
    if (runId) stopSim(runId);
    setRunId(null);
  }
  function onScenario(s: string) {
    setScen(s);
    if (runId) setScenario(runId, s);
  }

  const names = useMemo(
    () => (latest ? latest.policies.map((p) => p.policy) : DEFAULT_POLICIES),
    [latest]
  );
  const hitData = useMemo(() => frames.map((f) => ({ epoch: f.epoch, ...row(f, "hit_rate") })), [frames]);
  const costData = useMemo(() => frames.map((f) => ({ epoch: f.epoch, ...row(f, "cost_total") })), [frames]);
  const latData = useMemo(() => frames.map((f) => ({ epoch: f.epoch, ...row(f, "p95_latency_ms") })), [frames]);

  const me = latest?.policies.find((p) => p.policy === ME);
  const gdsf = latest?.policies.find((p) => p.policy === "GDSF");
  const saving = latest?.cost_report.rows.find((r) => r.policy === ME);
  const warm = me ? (me.l2_rate + me.l3_rate) : 0;

  return (
    <div className="app">
      <header>
        <div>
          <h1><span>CACHE MIND</span> · an AI brain above your cache</h1>
          <div className="sub">KASUKABE DEFENCE FORCE — live: CACHE MIND vs LRU / LFU / GDS / GDSF</div>
        </div>
        <div className="controls">
          <select value={scenario} onChange={(e) => onScenario(e.target.value)}>
            {(meta?.scenarios ?? [scenario]).map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={profile} onChange={(e) => setProfile(e.target.value)} disabled={running}>
            {(meta?.profiles ?? [profile]).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <select value={speed} onChange={(e) => setSpeed(+e.target.value)}>
            {[4, 8, 16, 24].map((s) => <option key={s} value={s}>{s}x</option>)}
          </select>
          {running ? <button onClick={stop}>Stop</button>
                   : <button className="primary" onClick={start}>Start</button>}
          <button className="spike" disabled={!running} onClick={() => runId && injectSpike(runId)}>
            ⚡ Inject traffic spike
          </button>
        </div>
      </header>

      {!latest ? (
        <div className="empty">Press <b>Start</b> to launch a live simulation.</div>
      ) : (
        <>
          <div className="grid">
            <Card k="CACHE MIND cost saving vs LRU" cls="good"
              v={`${saving?.saving_pct ?? 0}%`}
              foot={`$${saving?.saving_vs_baseline?.toFixed(4) ?? 0} saved so far`} />
            <Card k="avg latency"
              v={`${(me?.avg_latency_ms ?? 0).toFixed(1)} ms`}
              foot={`avg — GDSF ${(gdsf?.avg_latency_ms ?? 0).toFixed(0)} ms · LRU ${(latest.policies.find(p=>p.policy==="LRU")?.avg_latency_ms ?? 0).toFixed(0)} ms`} />
            <Card k="served from warm tiers (L2+L3)"
              v={`${(warm * 100).toFixed(0)}%`}
              foot={`L1 ${((me?.l1_rate ?? 0) * 100).toFixed(0)}% · single-tier caches would miss these to origin`} />
            <Card k="detected regime"
              v={me?.regime ?? "—"} cls={latest.spike_active ? "bad" : undefined}
              foot={`bandit arm: ${me?.bandit_arm ?? "—"}`} />
          </div>

          <div className="legend">
            {names.map((p) => (
              <span key={p}><span className="dot" style={{ background: COLORS[p] ?? "#ccc" }} />{p}</span>
            ))}
            <span style={{ marginLeft: "auto" }}>
              <span className={`badge ${latest.spike_active ? "spike" : "live"}`}>
                {latest.spike_active ? "⚡ SPIKE ACTIVE" : "● LIVE"}
              </span>{" "}epoch {latest.epoch} · {latest.rate}/s · {latest.scenario}
            </span>
          </div>

          <div className="panels-2">
            <div className="chart-card">
              <div className="chart-title">Hit rate</div>
              <MultiLine data={hitData} keys={names} pct domain={[0, 1]} markers={spikeMarks} />
            </div>
            <div className="chart-card">
              <div className="chart-title">Cumulative cost ($) — lower is better</div>
              <MultiLine data={costData} keys={names} markers={spikeMarks} />
            </div>
            <div className="chart-card">
              <div className="chart-title">latency p95 (ms, log)</div>
              <MultiLine data={latData} keys={names} unit="ms" markers={spikeMarks} log />
            </div>
            <div className="chart-card">
              <div className="chart-title">Cache tiers — used / capacity</div>
              <TierBars policies={latest.policies} />
            </div>
          </div>

          <div className="panels">
            <div className="chart-card">
              <div className="chart-title">CACHE MIND decision feed</div>
              <div className="feed">
                {(me?.decisions ?? []).slice().reverse().map((d, i) => (
                  <div className="row" key={i}>
                    <span className={`tag ${d.action.replace(/[^a-z0-9_]/gi, "_")}`}>{d.action}</span>
                    <span className="key">{d.key}</span>
                    <span className="reason">{d.reason}</span>
                  </div>
                ))}
                {!me?.decisions?.length && <div className="row"><span className="reason">warming up…</span></div>}
              </div>
            </div>
            <div className="chart-card">
              <div className="chart-title">Value-score weights (bandit)</div>
              <div className="wbars">
                {Object.entries((me?.weights ?? {}) as Record<string, number>).map(([k, v]) => (
                  <div className="wbar" key={k}>
                    <span className="name">{k}</span>
                    <span className="track"><span className="fill" style={{ width: `${Math.min(100, (v / 8) * 100)}%` }} /></span>
                    <span>{v.toFixed(2)}</span>
                  </div>
                ))}
              </div>
              <div className="arm" style={{ padding: "8px" }}>
                active personality: <b>{me?.bandit_arm ?? "—"}</b>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function row(f: Frame, field: keyof PolicySnap): Record<string, number> {
  const o: Record<string, number> = {};
  for (const p of f.policies) o[p.policy] = p[field] as number;
  return o;
}

function Card({ k, v, foot, cls }: { k: string; v: string; foot?: string; cls?: string }) {
  return (
    <div className="card">
      <div className="k">{k}</div>
      <div className={`v ${cls ?? ""}`}>{v}</div>
      {foot && <div className="foot">{foot}</div>}
    </div>
  );
}
