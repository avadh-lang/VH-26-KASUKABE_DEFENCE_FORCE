// In dev, Vite proxies "/api" to the local backend (vite.config.ts), so this
// stays empty and every call below is same-origin. In production the built
// dashboard is typically deployed separately from the backend (e.g. Vercel +
// Render), so VITE_API_BASE points straight at the backend's real URL —
// set at build time, baked into the static bundle.
const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export type Tier = { tier: string; used_mb: number; cap_mb: number };

export type Explain = {
  p_soon: number; confidence: number; regen_ms: number; regen_usd: number;
  size_kb: number; freshness: number; trend: number; score: number;
};
export type Decision = { epoch: number; action: string; key: string; reason: string; explain?: Explain };

export type PolicySnap = {
  policy: string;
  hit_rate: number;
  hit_rate_cum: number;
  l1_rate: number;
  l2_rate: number;
  l3_rate: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  cost_total: number;
  cost_origin: number;
  cost_latency: number;
  cost_memory: number;
  cost_move: number;
  used_mb: number;
  entries: number;
  tiers?: Tier[];
  weights?: Record<string, number>;
  bandit_arm?: string;
  regime?: string;
  decisions?: Decision[];
  l1_access_patterns?: { periodic: number; bursty: number; random: number; new: number };
  sample?: { key: string; tier: string; size_kb: number; freq: number; pattern: string; value: number }[];
};

export type Frame = {
  epoch: number;
  t: number;
  rate: number;
  spike_active: boolean;
  surge_mult?: number;
  scenario: string;
  policies: PolicySnap[];
  cost_report: {
    baseline: string;
    rows: {
      policy: string;
      cost_total: number;
      hit_rate: number;
      saving_vs_baseline: number;
      saving_pct: number;
    }[];
  };
};

export type Meta = { scenarios: string[]; profiles: string[]; policies: string[] };

export async function getMeta(): Promise<Meta> {
  return (await fetch(`${API_BASE}/api/meta`)).json();
}

export async function startSim(body: {
  scenario: string;
  profile: string;
  policies: string[];
  speed: number;
}): Promise<{ run_id: string; start_capacity_mb: number }> {
  const r = await fetch(`${API_BASE}/api/sim/start`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function injectSpike(runId: string) {
  await fetch(`${API_BASE}/api/sim/${runId}/spike`, { method: "POST" });
}

export async function setScenario(runId: string, scenario: string) {
  await fetch(`${API_BASE}/api/sim/${runId}/scenario`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ scenario }),
  });
}

export async function setSurge(runId: string, mult: number) {
  await fetch(`${API_BASE}/api/sim/${runId}/surge`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ mult }),
  });
}

export async function stopSim(runId: string) {
  await fetch(`${API_BASE}/api/sim/${runId}`, { method: "DELETE" });
}

export type RealPing = { url: string; bytes: number; latency_ms: number; sample: string };

export async function pingReal(resource = "posts"): Promise<RealPing> {
  const r = await fetch(`${API_BASE}/api/real/ping?resource=${encodeURIComponent(resource)}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function streamSim(runId: string, onFrame: (f: Frame) => void, onError?: () => void): EventSource {
  const es = new EventSource(`${API_BASE}/api/sim/${runId}/stream`);
  es.addEventListener("epoch", (e) => onFrame(JSON.parse((e as MessageEvent).data)));
  es.addEventListener("done", () => es.close());
  if (onError) es.onerror = onError;
  return es;
}
