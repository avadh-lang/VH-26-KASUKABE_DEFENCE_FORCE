export type PolicySnap = {
  policy: string;
  hit_rate: number;
  hit_rate_cum: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  cost_total: number;
  cost_origin: number;
  cost_latency: number;
  cost_memory: number;
  capacity_mb: number;
  used_mb: number;
  entries: number;
  weights?: Record<string, number>;
  bandit_arm?: string;
  regime?: string;
  decisions?: { epoch: number; action: string; key: string; reason: string }[];
};

export type Frame = {
  epoch: number;
  t: number;
  rate: number;
  spike_active: boolean;
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

const base = "";

export async function getMeta(): Promise<Meta> {
  return (await fetch(`${base}/api/meta`)).json();
}

export async function startSim(body: {
  scenario: string;
  profile: string;
  policies: string[];
  speed: number;
}): Promise<{ run_id: string; start_capacity_mb: number }> {
  const r = await fetch(`${base}/api/sim/start`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function injectSpike(runId: string) {
  await fetch(`${base}/api/sim/${runId}/spike`, { method: "POST" });
}

export async function setScenario(runId: string, scenario: string) {
  await fetch(`${base}/api/sim/${runId}/scenario`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ scenario }),
  });
}

export async function stopSim(runId: string) {
  await fetch(`${base}/api/sim/${runId}`, { method: "DELETE" });
}

export function streamSim(runId: string, onFrame: (f: Frame) => void): EventSource {
  const es = new EventSource(`${base}/api/sim/${runId}/stream`);
  es.addEventListener("epoch", (e) => onFrame(JSON.parse((e as MessageEvent).data)));
  es.addEventListener("done", () => es.close());
  return es;
}
