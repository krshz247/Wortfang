// Wortfang service worker — offline app shell + offline dictionary.
// Bump VERSION whenever you change index.html (or other shell files) so phones pick up the update.
const VERSION = "wortfang-v5";
const DICT_CACHE = "wortschatz-dict";
const FONT_CACHE = "wortschatz-fonts";
const SHELL = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/apple-touch-icon.png",
];
const DICT = ["./dict/meta.json", "./dict/nouns.json", "./dict/verbs.json"];

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const shell = await caches.open(VERSION);
    await shell.addAll(SHELL);
    // Pre-cache the dictionary if it's deployed; never fail the install over it.
    const dict = await caches.open(DICT_CACHE);
    await Promise.allSettled(DICT.map(async u => {
      const res = await fetch(u, { cache: "no-cache" });
      if (res.ok) await dict.put(u, res);
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys
        .filter(k => ![VERSION, DICT_CACHE, FONT_CACHE].includes(k))
        .map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // Never touch Claude API calls.
  if (url.hostname === "api.anthropic.com") return;

  // Google Fonts: cache-first.
  if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {
    event.respondWith(
      caches.open(FONT_CACHE).then(async cache => {
        const hit = await cache.match(req);
        if (hit) return hit;
        const res = await fetch(req);
        if (res.ok || res.type === "opaque") cache.put(req, res.clone());
        return res;
      })
    );
    return;
  }

  if (url.origin !== self.location.origin) return;

  // Dictionary: serve from cache instantly, refresh in the background
  // (the browser revalidates with ETags, so unchanged files aren't re-downloaded).
  if (url.pathname.includes("/dict/")) {
    event.respondWith((async () => {
      const cache = await caches.open(DICT_CACHE);
      const key = url.pathname.replace(/^.*\/dict\//, "./dict/");
      const hit = await cache.match(key);
      const refresh = fetch(req).then(res => {
        if (res.ok) cache.put(key, res.clone());
        return res;
      });
      if (hit) {
        event.waitUntil(refresh.catch(() => {}));
        return hit;
      }
      return refresh;
    })());
    return;
  }

  // App shell: network-first (so updates show up right away), cache fallback when
  // offline or when the network takes longer than 3 s.
  const key = req.mode === "navigate" ? "./index.html" : req;
  event.respondWith((async () => {
    const cache = await caches.open(VERSION);
    // Only good responses count; a 404/5xx falls back to the cached copy.
    const network = fetch(req).then(res => {
      if (!res.ok) throw new Error("HTTP " + res.status);
      cache.put(key, res.clone());
      return res;
    });
    const timeout = new Promise(resolve => setTimeout(resolve, 3000, null));
    try {
      const res = await Promise.race([network, timeout]);
      if (res) return res;
    } catch { /* offline — fall through to cache */ }
    const hit = await cache.match(key, { ignoreSearch: true });
    if (hit) return hit;
    try { return await network; } catch { return fetch(req).catch(() => Response.error()); }
  })());
});
