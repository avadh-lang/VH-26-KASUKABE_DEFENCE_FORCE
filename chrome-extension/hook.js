/**
 * CACHE MIND — page-context network hook.
 *
 * Runs in the page's own JS realm ("world": "MAIN" in manifest.json), not
 * the extension's isolated content-script world — an isolated-world script
 * only ever sees its own private copy of `window.fetch`, never the page's.
 * To actually see (and serve) the page's real fetch() calls, the override
 * has to live where the page's own code runs.
 *
 * Scope is deliberately narrow — this is a caching layer, not a replay
 * surface for anything personal:
 *   - GET requests only. Nothing that mutates state is ever touched.
 *   - Nothing credentialed. `credentials:"include"` is skipped outright;
 *     same-origin calls left at the fetch default ("same-origin") are ALSO
 *     skipped, because that default still attaches the page's cookies —
 *     only cross-origin calls (which never carry this origin's cookies
 *     under "same-origin" mode) or explicit credentials:"omit" qualify.
 *   - Nothing with an Authorization header, and nothing under a path that
 *     looks account/cart/checkout/session/payment-shaped.
 *   - Responses: only cached if content-type isn't text/html (never replay
 *     a whole personalized page) and Cache-Control doesn't say
 *     no-store/private.
 * Anything that fails any of these checks just falls straight through to
 * the real network, untouched — a miss on our side never means a broken
 * page, only a skipped optimization.
 */
(() => {
  if (window.__cacheMindHooked) return;
  window.__cacheMindHooked = true;

  const BLOCK_PATH = /(cart|checkout|account|login|signin|logout|auth|session|payment|order|billing|oauth)/i;

  function isSameOrigin(url) {
    try { return new URL(url, location.href).origin === location.origin; } catch { return false; }
  }

  function credentialed(req) {
    if (req.credentials === "include") return true;
    if (req.credentials !== "omit" && isSameOrigin(req.url)) return true;
    return false;
  }

  function cacheable(req) {
    if (req.method !== "GET") return false;
    if (credentialed(req)) return false;
    try {
      const u = new URL(req.url);
      if (!/^https?:$/.test(u.protocol)) return false;
      if (BLOCK_PATH.test(u.pathname)) return false;
    } catch { return false; }
    if (req.headers && req.headers.get && req.headers.get("authorization")) return false;
    return true;
  }

  let seq = 0;
  const pending = new Map();
  window.addEventListener("message", (ev) => {
    if (ev.source !== window || !ev.data || ev.data.__cm !== "resp") return;
    const p = pending.get(ev.data.id);
    if (p) { pending.delete(ev.data.id); p(ev.data); }
  });

  function ask(type, payload) {
    return new Promise((resolve) => {
      const id = `${Date.now()}_${seq++}`;
      pending.set(id, resolve);
      window.postMessage({ __cm: "req", id, type, payload }, "*");
      setTimeout(() => {
        if (pending.has(id)) { pending.delete(id); resolve(null); }
      }, 4000);
    });
  }

  const origFetch = window.fetch.bind(window);

  window.fetch = async function (input, init) {
    let req;
    try { req = new Request(input, init); } catch { return origFetch(input, init); }

    if (!cacheable(req)) return origFetch(input, init);

    const status = await ask("STATUS", {});
    if (!status || !status.enabled) return origFetch(input, init);

    const cached = await ask("LOOKUP", { url: req.url });
    if (cached && cached.hit) {
      return new Response(cached.body, {
        status: 200,
        statusText: "OK (CACHE MIND)",
        headers: { "content-type": cached.contentType || "text/plain" },
      });
    }

    const t0 = performance.now();
    const res = await origFetch(input, init);
    try {
      const cc = res.headers.get("cache-control") || "";
      const ct = res.headers.get("content-type") || "";
      const okToStore = res.ok && !/no-store|private/i.test(cc) && !/text\/html/i.test(ct);
      if (okToStore) {
        const latencyMs = performance.now() - t0;
        res.clone().text().then((body) => {
          if (body && body.length < 2_000_000) {
            ask("STORE", { url: req.url, body, contentType: ct, latencyMs });
          }
        }).catch(() => {});
      }
    } catch { /* never let bookkeeping break the real response */ }
    return res;
  };
})();
