// Ink's on-page interface: the floating bubble with its radial menu, live captions, the catch-up panel and alerts.
// Injected into the meeting tab when the user starts Ink. Everything lives in a closed shadow root so the page's own
// styles cannot touch it, and all text from the meeting goes through textContent (never innerHTML).
(() => {
  if (window.__inkLoaded) { window.__inkShow?.(); return; }
  window.__inkLoaded = true;
  const ext = globalThis.browser ?? globalThis.chrome;

  const STATE_COLOR = { idle: "#64748b", starting: "#38bdf8", recording: "#ef4444", paused: "#f59e0b", processing: "#38bdf8" };
  const ICONS = {
    pause: '<path d="M8 5h3v14H8zM13 5h3v14h-3z" fill="currentColor"/>',
    play: '<path d="M8 5l11 7-11 7z" fill="currentColor"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/>',
    notes: '<path d="M7 4h10a1 1 0 011 1v14a1 1 0 01-1 1H7a1 1 0 01-1-1V5a1 1 0 011-1zM9 9h6M9 12h6M9 15h4" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round"/>',
    star: '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.8 6.8 19.6l1-5.8L3.5 9.7l5.9-.9z" fill="currentColor"/>',
    hide: '<path d="M7 7l10 10M17 7L7 17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  };
  const COLORS = { pause: "#f59e0b", play: "#22c55e", stop: "#ef4444", notes: "#60a5fa", star: "#fbbf24", hide: "#94a3b8" };
  const TIPS = { pause: "Pause", play: "Resume", stop: "Stop and create the report", notes: "Catch me up", star: "Bookmark this moment", hide: "Hide Ink (it keeps listening)" };
  const MENUS = { recording: ["pause", "stop", "notes", "star", "hide"], paused: ["play", "stop", "notes", "star", "hide"], processing: ["hide"] };

  const host = document.createElement("div");
  host.style.cssText = "all:initial;position:fixed;inset:0;z-index:2147483647;pointer-events:none;";
  const root = host.attachShadow({ mode: "closed" });
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const svg = (name) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("viewBox", "0 0 24 24"); s.innerHTML = ICONS[name]; return s; };

  const style = el("style");
  style.textContent = `
    * { box-sizing: border-box; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    .captions { position: fixed; left: 50%; bottom: 64px; transform: translateX(-50%); width: min(60vw, 900px); pointer-events: none;
      background: rgba(15,23,42,.86); color: #fff; border-radius: 14px; padding: 10px 18px; font-size: 18px; line-height: 1.45; text-align: center; display: none; }
    .captions .who { color: #94a3b8; }
    .bubble { position: fixed; right: 28px; top: 40%; width: 64px; height: 64px; pointer-events: auto; touch-action: none; }
    .core { position: absolute; inset: 0; border-radius: 50%; border: 4px solid var(--ring, #64748b); background: #fff center/cover no-repeat;
      box-shadow: 0 4px 14px rgba(0,0,0,.35); cursor: pointer; z-index: 2; transition: border-color .2s; }
    .core.recording { animation: pulse 1.4s ease-in-out infinite; }
    .core.processing { border-top-color: transparent; animation: spin 1s linear infinite; }
    .core.flash-gold { border-color: #fbbf24 !important; } .core.flash-violet { border-color: #a78bfa !important; }
    @keyframes pulse { 50% { box-shadow: 0 0 0 7px rgba(239,68,68,.25), 0 4px 14px rgba(0,0,0,.35); } }
    @keyframes spin { to { transform: rotate(360deg); } }
    .item { position: absolute; left: 9px; top: 9px; width: 46px; height: 46px; border-radius: 50%; border: 2px solid var(--c); color: var(--c);
      background: #0f172a; display: grid; place-items: center; cursor: pointer; padding: 0; z-index: 1; opacity: 0; pointer-events: none;
      transform: translate(0, 0) rotate(-360deg) scale(.2); transition: transform .56s cubic-bezier(.34,1.56,.64,1), opacity .25s; }
    .item svg { width: 24px; height: 24px; }
    .item:hover { background: #1e293b; }
    .bubble.open .item { opacity: 1; pointer-events: auto; transform: translate(var(--x), var(--y)) rotate(0) scale(1); }
    .tip { position: absolute; left: 50%; top: 72px; transform: translateX(-50%); background: #0f172a; color: #e2e8f0; font-size: 12px; padding: 4px 9px; border-radius: 6px;
      white-space: nowrap; opacity: 0; transition: opacity .15s; pointer-events: none; }
    .toast { position: fixed; right: 28px; bottom: 120px; max-width: 340px; background: #0f172a; color: #e2e8f0; border: 1px solid #334155;
      border-radius: 10px; padding: 10px 14px; font-size: 13px; line-height: 1.4; display: none; pointer-events: auto; }
    .panel { position: fixed; right: 120px; top: 8vh; width: 420px; max-height: 76vh; overflow: auto; background: #0f172a; color: #e2e8f0;
      border: 1px solid #334155; border-radius: 16px; padding: 14px 18px 16px; font-size: 13px; line-height: 1.5; display: none; pointer-events: auto; }
    .panel h1 { font-size: 15px; margin: 0 0 6px; color: #fff; display: flex; justify-content: space-between; gap: 8px; align-items: center; }
    .panel .status { color: #38bdf8; font-size: 12px; font-weight: 400; flex: 1; }
    .panel .x { background: none; border: 0; color: #ef4444; font-size: 20px; cursor: pointer; }
    .panel h2 { font-size: 11px; letter-spacing: .08em; color: #94a3b8; margin: 12px 0 2px; }
    .panel p { margin: 2px 0; } .panel ul { margin: 2px 0; padding-left: 18px; }
    .panel .t { color: #64748b; } .panel .owner { color: #a78bfa; }
  `;

  const captions = el("div", "captions");
  const bubble = el("div", "bubble");
  const core = el("div", "core");
  core.style.backgroundImage = `url("${ext.runtime.getURL("icons/ink.png")}")`;
  const tip = el("div", "tip");
  const toast = el("div", "toast");
  const panel = el("div", "panel");
  bubble.append(tip, core);
  root.append(style, captions, bubble, toast, panel);
  document.documentElement.appendChild(host);

  let state = "starting", open = false, hideCaptionsTimer, toastTimer;
  const send = (type, extra = {}) => ext.runtime.sendMessage({ type, ...extra }).catch(() => {});

  // ---- radial menu ---------------------------------------------------------------------------------------------------
  function buildMenu() {
    bubble.querySelectorAll(".item").forEach((n) => n.remove());
    const names = MENUS[state] || [];
    names.forEach((name, i) => {
      const b = el("button", "item");
      b.style.setProperty("--c", COLORS[name]);
      b.append(svg(name));
      b.addEventListener("mouseenter", () => { tip.textContent = TIPS[name]; tip.style.opacity = 1; });
      b.addEventListener("mouseleave", () => { tip.style.opacity = 0; });
      b.addEventListener("click", (e) => { e.stopPropagation(); act(name); setOpen(false); });
      bubble.appendChild(b);
    });
  }
  /** Fan the icons out around the bubble. Near a screen edge they fan inward (a half circle) so none fall off-screen. */
  function layout() {
    const items = [...bubble.querySelectorAll(".item")];
    const n = items.length;
    if (!n) return;
    const r = bubble.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2, M = 140;
    const dx = cx < M ? 1 : cx > innerWidth - M ? -1 : 0;
    const dy = cy < M ? 1 : cy > innerHeight - M ? -1 : 0;
    const full = !dx && !dy;
    const center = Math.atan2(dy, dx) * 180 / Math.PI;
    const spread = full ? 0 : dx && dy ? 130 : 180;
    const radius = full ? 92 : 100;
    items.forEach((b, i) => {
      const deg = full ? -90 + (i * 360) / n : center - spread / 2 + (n === 1 ? spread / 2 : (i * spread) / (n - 1));
      const a = (deg * Math.PI) / 180;
      b.style.setProperty("--x", `${Math.cos(a) * radius}px`);
      b.style.setProperty("--y", `${Math.sin(a) * radius}px`);
    });
  }
  function setOpen(v) {
    open = v && !!(MENUS[state] || []).length;
    if (open) layout();
    bubble.classList.toggle("open", open);
  }

  function act(name) {
    if (name === "pause") send("ink:control", { action: "pause" });
    else if (name === "play") send("ink:control", { action: "resume" });
    else if (name === "stop") send("ink:control", { action: "stop" });
    else if (name === "star") send("ink:control", { action: "bookmark" });
    else if (name === "notes") { showPanel({ updating: true }); send("ink:control", { action: "catchup" }); }
    else if (name === "hide") { host.style.display = "none"; }
  }

  function setState(s) {
    state = s;
    core.className = `core ${s}`;
    core.style.setProperty("--ring", STATE_COLOR[s] || "#64748b");
    buildMenu();
    if (s === "idle") {
      captions.style.display = "none"; panel.style.display = "none";
      setTimeout(() => { if (state === "idle") { host.remove(); window.__inkLoaded = false; } }, 4000);
    }
  }

  // drag the bubble anywhere; a click (no movement) opens/closes the menu
  let drag = null;
  core.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, bx: bubble.offsetLeft, by: bubble.offsetTop, moved: false }; core.setPointerCapture(e.pointerId); });
  core.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 5) drag.moved = true;
    if (drag.moved) {
      bubble.style.right = "auto";
      bubble.style.left = `${Math.min(Math.max(drag.bx + dx, 0), innerWidth - 64)}px`;
      bubble.style.top = `${Math.min(Math.max(drag.by + dy, 0), innerHeight - 64)}px`;
    }
  });
  core.addEventListener("pointerup", () => { if (drag && !drag.moved) setOpen(!open); drag = null; });

  // ---- captions ------------------------------------------------------------------------------------------------------
  const history = [];
  const live = new Map();
  function renderCaptions() {
    captions.textContent = "";
    const lines = [...history.slice(-1), ...[...live.entries()].map(([who, text]) => [who, text])].slice(-3);
    lines.forEach(([who, text], i) => {
      if (i) captions.appendChild(document.createElement("br"));
      captions.append(el("span", "who", `${who}: `), document.createTextNode(text.length > 170 ? text.slice(-170) : text));
    });
    captions.style.display = lines.length ? "block" : "none";
    clearTimeout(hideCaptionsTimer);
    hideCaptionsTimer = setTimeout(() => { captions.style.display = "none"; }, 6000);
  }
  function caption(who, text, final) {
    if (final) { live.delete(who); history.push([who, text]); if (history.length > 2) history.shift(); }
    else live.set(who, text);
    renderCaptions();
  }

  // ---- catch-up panel --------------------------------------------------------------------------------------------------
  const stampOf = (t) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  function showPanel(d) {
    panel.textContent = "";
    const h1 = el("h1");
    const status = el("span", "status", d.updating ? "updating..." : d.error ? "AI notes unavailable (Gemini busy)" : "");
    const x = el("button", "x", "×"); x.addEventListener("click", () => { panel.style.display = "none"; });
    h1.append(el("span", null, "Catch me up"), status, x);
    panel.appendChild(h1);
    const section = (title, node) => { panel.appendChild(el("h2", null, title)); panel.appendChild(node); };
    const ul = (items, fmt) => { const u = el("ul"); items.forEach((i) => { const li = el("li"); fmt ? fmt(li, i) : (li.textContent = String(i)); u.appendChild(li); }); return u; };
    if (d.now) section("RIGHT NOW", el("p", null, d.now));
    if (d.gist) section("SO FAR", el("p", null, d.gist));
    if (d.decisions?.length) section("DECISIONS", ul(d.decisions));
    if (d.action_items?.length) section("ACTION ITEMS", ul(d.action_items, (li, a) => { li.append(`${a.task} `, el("span", "owner", `(${a.owner || "-"})`)); }));
    if (d.open_questions?.length) section("OPEN QUESTIONS", ul(d.open_questions));
    if (d.recent?.length) {
      panel.appendChild(el("h2", null, "LATEST LINES"));
      d.recent.forEach((r) => { const p = el("p"); p.append(el("span", "t", `${stampOf(r.t)} `), el("b", null, `${r.speaker}: `), document.createTextNode(r.text)); panel.appendChild(p); });
    }
    if (!panel.querySelector("h2")) panel.appendChild(el("p", null, "Nothing has been said yet."));
    panel.style.display = "block";
  }

  // ---- toasts and flashes ----------------------------------------------------------------------------------------------
  function say(message, ms = 7000) {
    toast.textContent = message;
    toast.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.style.display = "none"; }, ms);
  }
  function flash(cls, ms) {
    core.classList.add(cls);
    setTimeout(() => core.classList.remove(cls), ms);
  }

  // ---- events from the extension -----------------------------------------------------------------------------------------
  ext.runtime.onMessage.addListener((msg) => {
    if (msg?.type !== "ink:event") return;
    const e = msg.event;
    switch (e.kind) {
      case "state": setState(e.state); break;
      case "caption": caption(e.speaker, e.text, e.final); break;
      case "notes": showPanel(e.notes); break;
      case "notice": say(e.message); break;
      case "bookmark": flash("flash-gold", 1400); say(`Moment bookmarked at ${stampOf(e.t)}`, 2500); break;
      case "mention": flash("flash-violet", 3000); say(`${e.speaker} mentioned you: ${e.line}`); break;
      default: break;
    }
  });

  window.__inkShow = () => { host.style.display = ""; };
  setState("recording");
  ext.runtime.sendMessage({ type: "ink:status" }).then((r) => r?.ok && r.value?.status && setState(r.value.status === "starting" ? "recording" : r.value.status)).catch(() => {});
})();
