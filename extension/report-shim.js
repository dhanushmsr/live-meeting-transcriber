// Connects the shared report page (src/inkwake/assets/viewer.html) to the extension: reports come from the browser's
// own storage, questions go straight to Gemini with the user's key, and "Share link" uploads one report on request.
// Loaded as a classic script BEFORE the page's own script, which reads window.INK.
(() => {
  const ext = globalThis.browser ?? globalThis.chrome;
  // Outside an extension (developing the page in a normal tab) fall back to localStorage with the same shape.
  const store = ext?.storage?.local || {
    async get(keys) {
      const list = typeof keys === "string" ? [keys] : keys;
      return Object.fromEntries(list.filter((k) => localStorage.getItem(k) !== null).map((k) => [k, JSON.parse(localStorage.getItem(k))]));
    },
    async set(obj) { for (const [k, v] of Object.entries(obj)) localStorage.setItem(k, JSON.stringify(v)); },
  };
  const settings = async () => (await store.get("settings")).settings || {};
  const params = new URLSearchParams(location.search);

  const lines = (rec) => (rec.transcript || []).map((x) => {
    const t = Math.floor(x.t || 0);
    return `[${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}] ${x.speaker}: ${x.text || x.translation || ""}`;
  }).join("\n");

  window.INK = {
    reportId: () => params.get("id") || "",

    async load(id) {
      const rec = (await store.get("report:" + id))["report:" + id];
      return rec && Date.parse(rec.expires_at) > Date.now() ? rec : null;     // expired reports behave as deleted
    },

    async canAsk() { return !!(await settings()).apiKey; },

    async ask(id, question) {
      const [{ Gemini }, rec, s] = await Promise.all([import("./core/gemini.js"), this.load(id), settings()]);
      if (!rec) throw new Error("This report has expired.");
      return new Gemini(s.apiKey).ask(question, lines(rec), rec.analysis);
    },

    async share(rec) {
      const [{ uploadToCloud }, s] = await Promise.all([import("./core/reports.js"), settings()]);
      if (!confirm("Create a public link to this report?\n\nAnyone who has the link can read the full transcript for 72 hours, then it is deleted.")) {
        throw new Error("Sharing cancelled.");
      }
      return uploadToCloud(s.cloudUrl || "https://inkwake-reports.inkwake.workers.dev", rec);
    },
  };
})();
