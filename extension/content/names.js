// Google Meet: who is speaking right now? Injected into the Meet tab next to ink.js while Ink is running.
//
// Meet's class names are scrambled and change often, so this relies on as little as possible:
//  - every participant tile carries a `data-participant-id` attribute (stable for years);
//  - the name is the tile's visible label (icons are font ligatures such as "mic_off", which are skipped);
//  - while someone talks, Meet animates their tile's sound indicator by changing classes and styles many times a
//    second. The tile with that steady burst of changes is the speaker; a single change (hover, layout) is not enough.
// Only names are read and sent to Ink's own engine. Nothing is changed on the page.
(() => {
  if (window.__inkNames) { window.__inkNames.restart(); return; }
  const ext = globalThis.browser ?? globalThis.chrome;

  const TILE = "[data-participant-id]";
  const WINDOW_MS = 1200;      // look at this much recent activity
  const MIN_CHANGES = 4;       // a speaking indicator changes far more often than this in WINDOW_MS
  const TICK_MS = 400;
  const SKIP = 'i, button, [role="button"], [role="tooltip"], [aria-hidden="true"], style, script, [class*="material-icons"], [class*="material-symbols"]';
  const ICON = /^[a-z0-9_]+$/;  // icon ligatures: mic_off, more_vert, keep, ...

  const activity = new Map();  // tile id -> timestamps of recent changes inside that tile
  let last = "";
  let observer = null, timer = null;

  const idOf = (tile) => tile.getAttribute("data-participant-id");
  const outer = (el) => { let t = el.closest(TILE); while (t?.parentElement?.closest(TILE)) t = t.parentElement.closest(TILE); return t; };

  function tileName(tile) {
    const walker = document.createTreeWalker(tile, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const p = n.parentElement;
      if (!p || p.closest(SKIP)) continue;
      const text = n.textContent.replace(/\s+/g, " ").trim().replace(/\s*\([^)]*\)$/, "");   // "Ana (Presentation)" -> "Ana"
      if (text.length < 2 || text.length > 60 || ICON.test(text)) continue;
      return text;
    }
    return "";
  }

  function selfInfo() {
    const el = document.querySelector("[data-self-name]");
    return { name: (el?.getAttribute("data-self-name") || "").trim(), tile: el ? outer(el) : null };
  }

  function read() {
    const now = performance.now();
    const self = selfInfo();
    const tiles = new Map();                   // id -> outermost tile element
    for (const t of document.querySelectorAll(TILE)) { const o = outer(t); if (o) tiles.set(idOf(o), o); }
    const names = new Map();
    for (const [id, t] of tiles) { const n = tileName(t); if (n) names.set(id, n); }
    const selfId = self.tile ? idOf(self.tile) : null;

    let best = null, bestN = 0;
    for (const [id, stamps] of activity) {
      while (stamps.length && now - stamps[0] > WINDOW_MS) stamps.shift();
      if (!stamps.length) { activity.delete(id); continue; }
      if (id === selfId || !names.has(id)) continue;         // your own voice comes from your microphone
      if (stamps.length > bestN) { best = id; bestN = stamps.length; }
    }
    const speaker = bestN >= MIN_CHANGES ? names.get(best) : null;
    const participants = [...new Set([...names].filter(([id]) => id !== selfId).map(([, n]) => n))].sort();
    return { speaker, participants, self: self.name || (selfId ? names.get(selfId) || "" : "") };
  }

  function send() {
    const reading = read();
    const key = JSON.stringify(reading);
    if (key === last) return;                  // only changes are sent
    last = key;
    ext.runtime.sendMessage({ type: "ink:names", names: reading }).catch?.(() => {});
  }

  function start() {
    stop();
    observer = new MutationObserver((muts) => {
      const now = performance.now();
      for (const m of muts) {
        const el = m.target.nodeType === 1 ? m.target : m.target.parentElement;
        const tile = el && outer(el);
        if (!tile) continue;
        const id = idOf(tile);
        if (!activity.has(id)) activity.set(id, []);
        activity.get(id).push(now);
      }
    });
    observer.observe(document.body, { subtree: true, attributes: true, attributeFilter: ["class", "style"] });
    timer = setInterval(send, TICK_MS);
  }

  function stop() {
    observer?.disconnect(); observer = null;
    clearInterval(timer); timer = null;
    activity.clear(); last = "";
  }

  ext.runtime.onMessage.addListener((msg) => {
    if (msg?.type === "ink:event" && msg.event?.kind === "state" && msg.event.state === "idle") stop();
  });

  window.__inkNames = { restart: start, read };
  start();
})();
