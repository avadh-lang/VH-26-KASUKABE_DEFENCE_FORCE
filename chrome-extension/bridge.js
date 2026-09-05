/**
 * CACHE MIND — isolated-world bridge.
 *
 * hook.js runs in the page's own JS realm and has no access to chrome.*
 * APIs (that world isn't the extension). This ordinary content script runs
 * alongside it in the extension's isolated world instead, where
 * chrome.runtime IS available, and just relays messages both ways over
 * window.postMessage <-> chrome.runtime.sendMessage.
 */
window.addEventListener("message", (ev) => {
  if (ev.source !== window || !ev.data || ev.data.__cm !== "req") return;
  const { id, type, payload } = ev.data;
  chrome.runtime.sendMessage({ type: `HOOK_${type}`, ...payload }, (res) => {
    if (chrome.runtime.lastError) {
      window.postMessage({ __cm: "resp", id, hit: false }, "*");
      return;
    }
    window.postMessage({ __cm: "resp", id, ...(res || {}) }, "*");
  });
});
