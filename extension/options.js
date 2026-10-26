import { DEFAULT_SETTINGS, FALLBACK_MODEL, LANG_CODES } from "./core/config.js";
import { Gemini } from "./core/gemini.js";

const ext = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);
const TEXT = ["apiKey", "name", "targetLanguage", "micDeviceId"];
const FLAGS = ["captions", "translate", "nameSpeakers", "shareOnline"];
const say = (text, ok = true) => { $("msg").className = ok ? "ok" : "bad"; $("msg").textContent = text; };

for (const lang of Object.keys(LANG_CODES)) $("targetLanguage").add(new Option(lang, lang));

async function loadMics(selected) {
  const select = $("micDeviceId");
  select.length = 1;
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    devices.filter((d) => d.kind === "audioinput" && d.deviceId && d.deviceId !== "default" && d.deviceId !== "communications")
      .forEach((d) => select.add(new Option(d.label || "Microphone", d.deviceId)));
  } catch { /* no permission yet: only the default is listed */ }
  select.value = [...select.options].some((o) => o.value === selected) ? selected : "";
}

async function init() {
  const s = { ...DEFAULT_SETTINGS, ...((await ext.storage.local.get("settings")).settings || {}) };
  for (const k of TEXT) if (k !== "micDeviceId") $(k).value = s[k] ?? "";
  for (const k of FLAGS) $(k).checked = !!s[k];
  await loadMics(s.micDeviceId);
}

$("allowMic").onclick = async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });   // the one-time permission prompt
    stream.getTracks().forEach((t) => t.stop());
    $("micNote").textContent = "Microphone allowed.";
    await loadMics($("micDeviceId").value);
  } catch (e) {
    $("micNote").textContent = "The browser blocked the microphone. Click the lock icon in the address bar and allow it.";
  }
};

$("test").onclick = async () => {
  const key = $("apiKey").value.trim();
  if (!key) return say("Paste your key first.", false);
  say("Testing...");
  try {
    await new Gemini(key).generate("Reply with the single word OK.", { model: FALLBACK_MODEL, timeoutMs: 20_000, attempts: 1 });
    say("The key works.");
  } catch (e) { say(`The key was not accepted: ${String(e.message).slice(0, 120)}`, false); }
};

$("save").onclick = async () => {
  const settings = { ...DEFAULT_SETTINGS, ...((await ext.storage.local.get("settings")).settings || {}) };
  for (const k of TEXT) settings[k] = $(k).value.trim();
  for (const k of FLAGS) settings[k] = $(k).checked;
  if (settings.shareOnline && !confirm("Every finished report will be uploaded to Ink's report server and get a public link " +
    "that anyone who has it can read for 72 hours.\n\nTurn this on only if everyone in your meetings is fine with that.")) {
    $("shareOnline").checked = false;
    settings.shareOnline = false;
  }
  await ext.storage.local.set({ settings });
  say("Saved.");
};

init();
