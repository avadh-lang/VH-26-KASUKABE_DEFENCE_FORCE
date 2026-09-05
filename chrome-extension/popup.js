const OBJECTS = Array.from({ length: 10 }, (_, i) => i + 1).map((n) => ({
  key: `posts:${n}`,
  label: `#${n}`,
  url: `https://jsonplaceholder.typicode.com/posts/${n}`,
}));

const TIER_COLOR = { L1: "#f28b82", L2: "#6ea8fe", L3: "#5fe0a0" };

const objectGrid = document.getElementById("objectGrid");
const resultEl = document.getElementById("result");
const feedEl = document.getElementById("feed");
const tiersEl = document.getElementById("tiers");

const buttons = new Map();
for (const o of OBJECTS) {
  const b = document.createElement("button");
  b.textContent = o.label;
  b.title = o.url;
  b.onclick = () => fetchObject(o);
  objectGrid.appendChild(b);
  buttons.set(o.key, b);
}

const liveToggle = document.getElementById("liveToggle");
const liveSub = document.getElementById("liveSub");

function paintLive(enabled) {
  liveToggle.checked = enabled;
  liveSub.textContent = enabled
    ? "Watching real GET requests on every tab you browse."
    : "Off — this tab's requests are not being cached.";
}

chrome.runtime.sendMessage({ type: "HOOK_STATUS" }, (res) => paintLive(res?.enabled ?? true));

liveToggle.onchange = () => {
  chrome.runtime.sendMessage({ type: "SET_ENABLED", enabled: liveToggle.checked }, () => {
    paintLive(liveToggle.checked);
  });
};

document.getElementById("resetBtn").onclick = async () => {
  await chrome.runtime.sendMessage({ type: "RESET" });
  for (const b of buttons.values()) b.classList.remove("hit");
  resultEl.textContent = "Cache cleared. Fetch something above.";
  await refreshSnapshot();
};

async function fetchObject(o) {
  resultEl.innerHTML = `fetching <b>${o.label}</b>…`;
  const res = await chrome.runtime.sendMessage({ type: "FETCH", url: o.url, key: o.key });
  if (res.error) {
    resultEl.textContent = `error: ${res.error}`;
    return;
  }
  buttons.get(o.key)?.classList.toggle("hit", res.hit);
  resultEl.innerHTML = res.hit
    ? `<span class="hit">HIT</span> ${o.label} served from <b>${res.tier}</b> — 0 ms, no network call.`
    : `<span class="miss">MISS</span> ${o.label} fetched from the network in <b>${res.latencyMs.toFixed(0)} ms</b>, placed in <b>${res.tier}</b>.`;
  renderFeed(res.log);
  await refreshSnapshot();
}

function renderFeed(log) {
  feedEl.innerHTML = "";
  for (const e of log) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<span class="tag ${e.action}">${e.action}</span><span class="k">${e.key}</span><span class="r">${e.reason}</span>`;
    feedEl.appendChild(row);
  }
  if (!log.length) feedEl.innerHTML = `<div class="row"><span class="r">no decisions yet</span></div>`;
}

async function refreshSnapshot() {
  const snap = await chrome.runtime.sendMessage({ type: "SNAPSHOT" });
  tiersEl.innerHTML = "";
  for (const t of snap.tiers) {
    const row = document.createElement("div");
    row.className = "trow";
    const pct = Math.min(100, (t.used / t.cap) * 100);
    row.innerHTML = `
      <span class="tname">${t.tier}</span>
      <span class="tbar"><span class="tfill" style="width:${pct}%;background:${TIER_COLOR[t.tier]}"></span></span>
      <span class="tnum">${t.used}/${t.cap}</span>`;
    tiersEl.appendChild(row);
  }
  renderFeed(snap.log);
}

refreshSnapshot();
setInterval(refreshSnapshot, 1200); // keep showing real traffic the page makes while this popup stays open
