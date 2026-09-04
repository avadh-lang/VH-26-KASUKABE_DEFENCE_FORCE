/**
 * CACHE MIND browser demo — background service worker.
 *
 * Owns the three tiers and the placement decision. See cachemind.js for the
 * scoring shape. This file is the "engine" equivalent of engine/cachemind.py:
 * on a request it looks across L1 -> L2 -> L3 -> network, and on a miss it
 * decides *where the object should live* the same way the real engine does —
 * try the best tier, and if it's full, only displace something the newcomer
 * is genuinely worth more than (cascading the loser down a tier, never
 * straight to nothing, exactly like DEMOTE in the main project).
 */

import { CAP, TAU_S, scoreOf, newEntry } from "./cachemind.js";

const l1 = new Map(); // key -> entry (entry.value lives only in L1's own shadow map)
const l1Values = new Map(); // key -> raw value (kept separate so entry stays small/serializable-shaped)

const DB_NAME = "cachemind_l3";
const STORE = "objects";

function idb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "key" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function idbGet(key) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).get(key);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  });
}

async function idbPut(row) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(row);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function idbDelete(key) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).delete(key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function idbAll() {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

async function l2All() {
  const all = await chrome.storage.local.get(null);
  return Object.entries(all)
    .filter(([k]) => k.startsWith("cm:"))
    .map(([, v]) => v);
}

async function tierEntries(tier) {
  if (tier === "L1") return [...l1.values()];
  if (tier === "L2") return l2All();
  return idbAll();
}

async function tierCount(tier) {
  return (await tierEntries(tier)).length;
}

async function removeFrom(tier, key) {
  if (tier === "L1") { l1.delete(key); l1Values.delete(key); return; }
  if (tier === "L2") return chrome.storage.local.remove(`cm:${key}`);
  return idbDelete(key);
}

async function placeIn(tier, entry, value) {
  entry.tier = tier;
  if (tier === "L1") { l1.set(entry.key, entry); l1Values.set(entry.key, value); return; }
  if (tier === "L2") return chrome.storage.local.set({ [`cm:${entry.key}`]: { ...entry, value } });
  return idbPut({ ...entry, value });
}

/** Find the weakest entry in a tier (lowest score right now). */
async function weakest(tier, now) {
  const entries = await tierEntries(tier);
  if (!entries.length) return null;
  let worst = entries[0];
  let worstScore = scoreOf(worst, now);
  for (const e of entries.slice(1)) {
    const s = scoreOf(e, now);
    if (s < worstScore) { worst = e; worstScore = s; }
  }
  return { entry: worst, score: worstScore };
}

const TIERS = ["L1", "L2", "L3"];
const decisionLog = [];
function note(action, key, reason) {
  decisionLog.unshift({ t: Date.now(), action, key, reason });
  if (decisionLog.length > 40) decisionLog.length = 40;
}

/**
 * Admit `entry` (with its `value`) into the best tier it fits, cascading a
 * weaker occupant down a tier rather than discarding it outright — the
 * same DEMOTE-not-EVICT idea as the main engine, just three tiers deep.
 */
async function admit(entry, value, now, startTier = 0) {
  for (let i = startTier; i < TIERS.length; i++) {
    const tier = TIERS[i];
    const count = await tierCount(tier);
    if (count < CAP[tier]) {
      await placeIn(tier, entry, value);
      note("place", entry.key, `${tier} had room`);
      return;
    }
    const w = await weakest(tier, now);
    const candidateScore = scoreOf(entry, now);
    if (w && candidateScore > w.score) {
      // this object earns the spot — push the weakest occupant one tier down.
      // Grab its value *before* removing it — L1's value lives in a side
      // map that removeFrom() clears, so reading it after would lose data.
      const weakValue = w.entry.value ?? l1Values.get(w.entry.key);
      await removeFrom(tier, w.entry.key);
      await placeIn(tier, entry, value);
      note("promote", entry.key, `beat weakest in ${tier} (score ${candidateScore.toFixed(1)} > ${w.score.toFixed(1)})`);
      if (i < TIERS.length - 1) {
        await admit(w.entry, weakValue, now, i + 1);
        note("demote", w.entry.key, `pushed out of ${tier}, not discarded`);
      } else {
        note("evict", w.entry.key, "pushed out of L3 — nowhere colder to go");
      }
      return;
    }
    // this tier is full and the newcomer isn't worth displacing anything — try the next, colder tier
  }
  note("skip", entry.key, "not worth caching anywhere right now");
}

async function lookup(key) {
  if (l1.has(key)) return { tier: "L1", entry: l1.get(key), value: l1Values.get(key) };
  const l2 = (await chrome.storage.local.get(`cm:${key}`))[`cm:${key}`];
  if (l2) return { tier: "L2", entry: l2, value: l2.value };
  const l3 = await idbGet(key);
  if (l3) return { tier: "L3", entry: l3, value: l3.value };
  return null;
}

async function handleFetch(url, key) {
  const now = Date.now();
  const hit = await lookup(key);
  if (hit) {
    hit.entry.freq += 1;
    hit.entry.lastAccess = now;
    await placeIn(hit.tier, hit.entry, hit.value); // refresh freq/lastAccess in place
    note("hit", key, `served from ${hit.tier}`);
    return { hit: true, tier: hit.tier, latencyMs: 0, value: hit.value, log: decisionLog.slice(0, 12) };
  }

  const t0 = performance.now();
  const res = await fetch(url);
  const text = await res.text();
  const latencyMs = performance.now() - t0;
  note("miss", key, `fetched from network in ${latencyMs.toFixed(0)} ms`);

  const entry = newEntry(key, new Blob([text]).size, latencyMs, now);
  await admit(entry, text, now);
  return { hit: false, tier: entry.tier, latencyMs, value: text, log: decisionLog.slice(0, 12) };
}

async function snapshot() {
  const [l1n, l2n, l3n] = await Promise.all([tierCount("L1"), tierCount("L2"), tierCount("L3")]);
  return {
    tiers: [
      { tier: "L1", used: l1n, cap: CAP.L1 },
      { tier: "L2", used: l2n, cap: CAP.L2 },
      { tier: "L3", used: l3n, cap: CAP.L3 },
    ],
    log: decisionLog.slice(0, 12),
  };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "FETCH") {
    handleFetch(msg.url, msg.key).then(sendResponse).catch((e) => sendResponse({ error: String(e) }));
    return true; // async response
  }
  if (msg.type === "SNAPSHOT") {
    snapshot().then(sendResponse);
    return true;
  }
  if (msg.type === "RESET") {
    l1.clear(); l1Values.clear();
    Promise.all([
      chrome.storage.local.get(null).then((all) => {
        const keys = Object.keys(all).filter((k) => k.startsWith("cm:"));
        return chrome.storage.local.remove(keys);
      }),
      idbAll().then((rows) => Promise.all(rows.map((r) => idbDelete(r.key)))),
    ]).then(() => { decisionLog.length = 0; sendResponse({ ok: true }); });
    return true;
  }
});
