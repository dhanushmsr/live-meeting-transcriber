const ext = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);
const status = $("status");

const LABELS = { idle: "Ready. Open your meeting tab, then start.", starting: "Starting...", recording: "Listening and captioning this tab.",
  paused: "Paused.", processing: "Writing your report. This takes up to a minute." };

const call = async (type, extra = {}) => {
  const r = await ext.runtime.sendMessage({ type, ...extra });
  if (!r?.ok) throw new Error(r?.error || "No answer from Ink");
  return r.value;
};

function show(state) {
  status.className = "";
  status.textContent = LABELS[state] || state;
  $("start").hidden = state !== "idle";
  $("pause").hidden = state !== "recording";
  $("resume").hidden = state !== "paused";
  $("bookmark").hidden = !["recording", "paused"].includes(state);
  $("stop").hidden = !["recording", "paused"].includes(state);
}
const fail = (e) => { status.className = "err"; status.textContent = String(e.message || e); };

async function init() {
  const { settings } = await ext.storage.local.get("settings");
  if (!settings?.apiKey) {
    status.className = "err";
    status.textContent = "First, add your Gemini API key in Settings.";
    $("hint").textContent = "Ink uses your own key; it is stored only in this browser.";
    return;
  }
  show((await call("ink:status")).status);
  $("hint").textContent = "Tip: tell the other people you are transcribing.";
}

$("start").onclick = async () => {
  try {
    const [tab] = await ext.tabs.query({ active: true, currentWindow: true });
    show("starting");
    await call("ink:start", { tabId: tab.id });
    window.close();                                  // the bubble on the page takes over from here
  } catch (e) { show("idle"); fail(e); }
};
for (const [id, action] of [["pause", "pause"], ["resume", "resume"], ["bookmark", "bookmark"], ["stop", "stop"]]) {
  $(id).onclick = async () => {
    try { if (action === "stop") show("processing"); await call("ink:control", { action }); if (action === "stop") window.close(); }
    catch (e) { fail(e); }
  };
}
$("settings").onclick = () => ext.runtime.openOptionsPage();
$("last").onclick = async () => {
  const { reportIds = [] } = await ext.storage.local.get("reportIds");
  if (!reportIds.length) return fail(new Error("No report yet."));
  ext.tabs.create({ url: ext.runtime.getURL(`report.html?id=${reportIds[reportIds.length - 1]}`) });
};
ext.runtime.onMessage.addListener((msg) => { if (msg?.type === "ink:event" && msg.event.kind === "state") show(msg.event.state); });
init();
