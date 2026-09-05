/**
 * CACHE MIND — browser tier demo, shared scoring logic.
 *
 * This is a small, honest port of the *idea* in engine/scoring.py — not a
 * line-for-line copy. A browser extension doesn't have RAM/Redis/cold-store;
 * what it does have is three real storage layers with genuinely different
 * speed/capacity trade-offs, so that's what L1/L2/L3 map to here:
 *
 *   L1 — an in-memory Map inside the service worker. Fastest. Gone the
 *        moment the service worker is unloaded (Chrome does this
 *        aggressively) — a real, honest limitation, not hidden.
 *   L2 — chrome.storage.local. Fast, survives service-worker sleep, small
 *        quota (a few MB).
 *   L3 — IndexedDB. Slower than L2, survives sleep, much larger quota.
 *
 * Same GDSF-shaped value score as the main engine (freq * cost / size,
 * aged by recency) — deliberately not the full 3-family hybrid from
 * engine/scoring.py, because reproducing the bandit/predictor here would
 * just be re-implementing the real engine in a second language. The point
 * of this extension is to prove the *placement* idea works on real browser
 * storage, not to duplicate the whole system.
 */

export const TAU_S = 25;                       // recency time-constant, seconds
export const CAP = { L1: 4, L2: 10, L3: 60 };  // small on purpose — makes placement decisions visible fast

/** entry: { key, size, latencyMs, freq, lastAccess, tier } */
export function scoreOf(entry, now) {
  const idleS = Math.max((now - entry.lastAccess) / 1000, 0);
  const recency = Math.exp(-idleS / TAU_S);
  const sizeKb = Math.max(entry.size / 1024, 0.05);
  const gdsf = (entry.freq * entry.latencyMs) / sizeKb;
  return gdsf * recency;
}

export function newEntry(key, size, latencyMs, now) {
  return { key, size, latencyMs, freq: 1, lastAccess: now, tier: null };
}
