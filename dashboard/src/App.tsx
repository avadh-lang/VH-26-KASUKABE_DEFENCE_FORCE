import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Decision, Frame, Meta, PolicySnap, RealPing, getMeta, injectSpike, pingReal, setScenario, setSurge, startSim, stopSim, streamSim,
} from "./api";
import { COLORS, MultiLine, TierBars } from "./components/Charts";
import { AnimatedNumber } from "./components/AnimatedNumber";
import { CacheGrid } from "./components/CacheGrid";
import { DecisionExplain } from "./components/DecisionExplain";
import { FlowDiagram } from "./components/FlowDiagram";
import { SurgeFader } from "./components/SurgeFader";

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
  const [ping, setPing] = useState<RealPing | "loading" | null>(null);
  const [surge, setSurgeState] = useState(1);
  const [lastExplain, setLastExplain] = useState<Decision | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const wasSpiking = useRef(false);
  const surgeSendRef = useRef<{ t: number; timer: ReturnType<typeof setTimeout> | null }>({ t: 0, timer: null });

  useEffect(() => {
    getMeta().then(setMeta).catch(() => {});
    return () => esRef.current?.close();
  }, []);

  const latest = frames.at(-1) ?? null;
  const running = !!runId;

  async function start() {
    stop();
    setFrames([]); setSpikeMarks([]); wasSpiking.current = false; setLastExplain(null);
    const { run_id } = await startSim({ scenario, profile, policies: DEFAULT_POLICIES, speed });
    setRunId(run_id);
    esRef.current = streamSim(run_id, (f) => {
      if (f.spike_active && !wasSpiking.current) setSpikeMarks((m) => [...m, f.epoch]);
      wasSpiking.current = f.spike_active;
      setFrames((prev) => (prev.length > 240 ? [...prev.slice(-240), f] : [...prev, f]));
      // refresh/prefetch notes are so frequent they'd otherwise crowd a rarer
      // eviction or tier-move out of the small per-frame decisions window
      // before anyone saw it — so remember the latest explainable one here,
      // independent of what's currently in that window.
      const cm = f.policies.find((p) => p.policy === ME);
      const withExplain = [...(cm?.decisions ?? [])].reverse().find((d) => d.explain);
      if (withExplain) setLastExplain(withExplain);
    });
  }
  function stop() {
    esRef.current?.close(); esRef.current = null;
    if (runId) stopSim(runId);
    setRunId(null);
    setSurgeState(1);
  }
  const onSurge = useCallback((v: number) => {
    setSurgeState(v);
    if (!runId) return;
    // throttle the network call to ~10/s during a fast drag, but always
    // fire the trailing value so the backend ends up exactly where the
    // handle was dropped
    const s = surgeSendRef.current;
    const now = performance.now();
    if (s.timer) clearTimeout(s.timer);
    if (now - s.t > 100) {
      s.t = now;
      setSurge(runId, v);
    } else {
      s.timer = setTimeout(() => { s.t = performance.now(); setSurge(runId, v); }, 100);
    }
  }, [runId]);
  function onScenario(s: string) {
    setScen(s);
    if (runId) setScenario(runId, s);
  }
  async function doPing() {
    setPing("loading");
    try {
      setPing(await pingReal(profile === "real" ? "posts" : "posts"));
    } catch {
      setPing(null);
    }
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
  const lru = latest?.policies.find((p) => p.policy === "LRU");
  const saving = latest?.cost_report.rows.find((r) => r.policy === ME);
  const warm = me ? (me.l2_rate + me.l3_rate) : 0;
  const patterns = me?.l1_access_patterns;

  return (
    <div className="app">
      <header>
        <div className="brand">
          <div className="brand-mark"><i /><i /><i /></div>
          <div>
            <h1>
              <span className="brand-name">CACHE MIND</span>
              <span className="tagline">an AI brain above your cache</span>
            </h1>
            <div className="sub">KASUKABE DEFENCE FORCE — live: CACHE MIND vs LRU / LFU / GDS / GDSF</div>
          </div>
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
          <SurgeFader value={surge} onChange={onSurge} disabled={!running} />
          <button className="spike" disabled={!running} onClick={() => runId && injectSpike(runId)}>
            Inject spike
          </button>
          <button className="ping" onClick={doPing}>Ping real API</button>
        </div>
      </header>

      {ping && (
        <div className="ping-result">
          {ping === "loading" ? "pinging jsonplaceholder.typicode.com …" : (
            <>
              live GET <span className="url">{ping.url}</span> → <b>{ping.latency_ms} ms</b>, {ping.bytes} bytes,
              measured just now — not simulated.
            </>
          )}
        </div>
      )}

      {!latest ? (
        <div className={`empty ${running ? "" : "idle"}`}>
          <div className="ring" />
          {running ? (
            <>
              <div><b>Warming up the first epoch…</b></div>
              <div className="hint">First few rounds of simulated traffic are loading.</div>
            </>
          ) : (
            <>
              <div>Press <b>Start</b> to launch a live simulation.</div>
              <div className="hint">
                Five caching policies race on identical traffic in real time — watch CACHE MIND
                place objects across three storage tiers while the classical policies only ever
                have one.
              </div>
            </>
          )}
        </div>
      ) : (
        <>
          <div className="grid">
            <div className="card hero">
              <div className="k">CACHE MIND cost saving vs LRU</div>
              <div className="v"><AnimatedNumber text={`${saving?.saving_pct ?? 0}%`} /></div>
              <div className="foot">${saving?.saving_vs_baseline?.toFixed(4) ?? 0} saved so far</div>
            </div>
            <div className="card">
              <div className="k">avg latency</div>
              <div className="v"><AnimatedNumber text={`${(me?.avg_latency_ms ?? 0).toFixed(1)} ms`} /></div>
              <div className="foot">GDSF {(gdsf?.avg_latency_ms ?? 0).toFixed(0)} ms · LRU {(lru?.avg_latency_ms ?? 0).toFixed(0)} ms</div>
            </div>
            <div className="card">
              <div className="k">served from warm tiers</div>
              <div className="v"><AnimatedNumber text={`${(warm * 100).toFixed(0)}%`} /></div>
              <div className="foot">L1 {((me?.l1_rate ?? 0) * 100).toFixed(0)}% · single-tier caches miss these to origin</div>
            </div>
            <div className="card">
              <div className="k">detected regime</div>
              <div className={`v ${latest.spike_active ? "bad" : ""}`} style={latest.spike_active ? undefined : { color: "var(--text)" }}>
                <AnimatedNumber text={me?.regime ?? "—"} />
              </div>
              <div className="foot">bandit arm: {me?.bandit_arm ?? "—"}</div>
            </div>
          </div>

          <div className="chart-card flow-card">
            <div className="chart-title">
              Live traffic flow — CACHE MIND routing decisions in motion
              <span className="hint">particle speed/density = real hit rate per tier</span>
            </div>
            <FlowDiagram
              l1={me?.l1_rate ?? 0}
              l2={me?.l2_rate ?? 0}
              l3={me?.l3_rate ?? 0}
              miss={Math.max(0, 1 - (me?.hit_rate ?? 0))}
              running={running}
            />
          </div>

          <div className="legend">
            {names.map((p) => (
              <span key={p}><span className="dot" style={{ background: COLORS[p] ?? "#ccc", color: COLORS[p] ?? "#ccc" }} />{p}</span>
            ))}
            <span className="meta-strip">
              <span className={`badge ${!running ? "stopped" : latest.spike_active ? "spike" : "live"}`}>
                <span className="pulse" />{!running ? "STOPPED — showing last state" : latest.spike_active ? "SPIKE ACTIVE" : "LIVE"}
              </span>
              epoch {latest.epoch} · {latest.rate}/s · {latest.scenario}
            </span>
          </div>

          <div className="panels-2">
            <div className="chart-card">
              <div className="chart-title">Hit rate<span className="hint">higher is better</span></div>
              <MultiLine data={hitData} keys={names} pct domain={[0, 1]} markers={spikeMarks} />
            </div>
            <div className="chart-card">
              <div className="chart-title">Cumulative cost ($)<span className="hint">lower is better</span></div>
              <MultiLine data={costData} keys={names} markers={spikeMarks} />
            </div>
            <div className="chart-card">
              <div className="chart-title">Latency p95 (ms, log)<span className="hint">tail-end worst case</span></div>
              <MultiLine data={latData} keys={names} unit="ms" markers={spikeMarks} log />
            </div>
            <div className="chart-card">
              <div className="chart-title">Cache tiers — used / capacity</div>
              <TierBars policies={latest.policies} />
            </div>
          </div>

          <div className="chart-card" style={{ marginTop: 14 }}>
            <div className="chart-title">
              Live cache contents — objects entering / leaving each tier right now
              <span className="hint">colour = access pattern</span>
            </div>
            <CacheGrid sample={me?.sample} />
          </div>

          <div className="chart-card" style={{ marginTop: 14 }}>
            <div className="chart-title">
              Why — the reasoning behind the latest decision
              <span className="hint">real signals, not a placeholder</span>
            </div>
            <DecisionExplain decision={lastExplain} />
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
                {!me?.decisions?.length && <div className="empty-row">warming up…</div>}
              </div>
            </div>
            <div className="chart-card">
              <div className="chart-title">Value-score weights (bandit)</div>
              <div className="wbars">
                {Object.entries((me?.weights ?? {}) as Record<string, number>).map(([k, v]) => (
                  <div className="wbar" key={k}>
                    <span className="name">{k}</span>
                    <span className="track"><span className="fill" style={{ width: `${Math.min(100, (v / 8) * 100)}%` }} /></span>
                    <span className="num">{v.toFixed(2)}</span>
                  </div>
                ))}
              </div>
              {patterns && (
                <div className="patterns">
                  <span className="pchip periodic"><i />periodic <b>{patterns.periodic}</b></span>
                  <span className="pchip bursty"><i />bursty <b>{patterns.bursty}</b></span>
                  <span className="pchip random"><i />random <b>{patterns.random}</b></span>
                  <span className="pchip new"><i />new <b>{patterns.new}</b></span>
                </div>
              )}
              <div className="arm">active personality: <b>{me?.bandit_arm ?? "—"}</b></div>
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
