// Coordinator: starts/stops a meeting, relays events to the page and popup, saves the finished report and opens it.
// Runs as a service worker in Chromium browsers and as a background page in Firefox.

import { DEFAULT_SETTINGS } from "./core/config.js";
import { cleanupExpired, saveReport, uploadToCloud } from "./core/reports.js";
import { platformFromUrl, titleFromTab } from "./core/meeting.js";

const ext = globalThis.browser ?? globalThis.chrome;
const HAS_OFFSCREEN = !!(globalThis.chrome && chrome.offscreen && chrome.tabCapture);   // Chromium: full audio capture
let firefoxEngine = null;                                                                // Firefox: engine lives right here

// A service worker can be shut down at any time, so the little state we need is kept in session storage.
const session = ext.storage.session || ext.storage.local;
const getState = async () => ({ tabId: null, status: "idle", ...((await session.get("ink")).ink || {}) });
const setState = async (patch) => { await session.set({ ink: { ...(await getState()), ...patch } }); };

const loadSettings = async () => ({ ...DEFAULT_SETTINGS, ...((await ext.storage.local.get("settings")).settings || {}) });

function notify(title, message) {
  try { ext.notifications.create({ type: "basic", iconUrl: ext.runtime.getURL("icons/icon128.png"), title, message }); } catch { /* optional */ }
}

async function badge(status) {
  const text = status === "recording" ? "REC" : status === "paused" ? "II" : status === "processing" ? "..." : "";
  await ext.action.setBadgeText({ text });
  await ext.action.setBadgeBackgroundColor({ color: status === "paused" ? "#f59e0b" : "#ef4444" });
}

// ---- talking to the audio engine ---------------------------------------------------------------------------------
async function ensureOffscreen() {
  const open = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  if (open.length) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html", reasons: ["USER_MEDIA", "AUDIO_PLAYBACK"], justification: "Capture and process the meeting audio for live captions",
  });
}

async function engineCall(type, extra = {}) {
  if (!HAS_OFFSCREEN) {
    const e = firefoxEngine;
    switch (type) {
      case "start": return e.start(extra);
      case "settings": return e.updateSettings(extra.settings);
      case "stop": return e.stop();
      case "pause": return e.pause();
      case "resume": return e.resume();
      case "bookmark": return e.bookmark();
      case "catchup": return e.catchUp();
      default: return undefined;
    }
  }
  const r = await chrome.runtime.sendMessage({ target: "offscreen", type, ...extra });
  if (!r?.ok) throw new Error(r?.error || "The audio engine did not answer");
  return r.value;
}

async function injectUi(tabId) {
  await ext.scripting.executeScript({ target: { tabId }, files: ["content/ink.js"] });
}

async function toTab(event) {
  const { tabId } = await getState();
  if (tabId != null) ext.tabs.sendMessage(tabId, { type: "ink:event", event }).catch(() => {});
}

// ---- the meeting ---------------------------------------------------------------------------------------------------
async function startSession(tabId) {
  if ((await getState()).status !== "idle") throw new Error("Ink is already running.");
  const tab = await ext.tabs.get(tabId);
  const platform = platformFromUrl(tab.url);
  const title = titleFromTab(tab.title);
  await setState({ tabId, status: "starting" });
  try {
    await injectUi(tabId);
    if (HAS_OFFSCREEN) {
      await ensureOffscreen();
      const tabStreamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
      await engineCall("start", { tabStreamId, platform, title, settings: await loadSettings() });
    } else {
      const { Engine } = await import("./engine.js");
      firefoxEngine = new Engine({ loadSettings, emit: handleEvent });
      await engineCall("start", { platform, title });
    }
  } catch (e) {
    await setState({ tabId: null, status: "idle" });
    await badge("idle");
    throw e;
  }
}

async function stopSession() {
  const st = await getState();
  if (!["recording", "paused"].includes(st.status)) return;
  const record = await engineCall("stop");
  if (HAS_OFFSCREEN) chrome.offscreen.closeDocument().catch(() => {});
  firefoxEngine = null;
  await setState({ tabId: null, status: "idle" });
  await badge("idle");
  if (st.tabId != null) ext.tabs.sendMessage(st.tabId, { type: "ink:event", event: { kind: "state", state: "idle" } }).catch(() => {});
  if (record) await showReport(record);
  else notify("Ink", "Nothing was recorded, so there is no report.");
}

async function showReport(record) {
  const settings = await loadSettings();
  const saved = await saveReport(ext.storage.local, record);
  let url = ext.runtime.getURL(`report.html?id=${saved.id}`);
  if (settings.shareOnline) {                    // opt-in: the report also goes to the report server and the public link opens
    try { url = await uploadToCloud(settings.cloudUrl, saved); }
    catch (e) { notify("Ink", `Could not create the online link (${String(e.message).slice(0, 80)}). Opening the local report.`); }
  }
  await ext.tabs.create({ url });
  notify("Your meeting report is ready", "It opened in a new tab. Reports are kept for 72 hours.");
}

async function control(action) {
  switch (action) {
    case "stop": return stopSession();
    case "pause": case "resume": case "bookmark": case "catchup": return engineCall(action);
    default: throw new Error(`Unknown action ${action}`);
  }
}

function handleEvent(event) {
  if (event.kind === "state") { setState({ status: event.state }); badge(event.state); }
  if (event.kind === "mention") notify(`${event.speaker} mentioned you`, String(event.line || "").slice(0, 140));
  toTab(event);
}

// ---- wiring ----------------------------------------------------------------------------------------------------------
// Changing a setting (for example the caption language) while a meeting runs takes effect immediately.
ext.storage.onChanged.addListener(async (changes, area) => {
  if (area !== "local" || !changes.settings) return;
  if (["recording", "paused"].includes((await getState()).status)) engineCall("settings", { settings: await loadSettings() }).catch(() => {});
});

const answer = (promise, reply) => { promise.then((value) => reply({ ok: true, value }), (e) => reply({ ok: false, error: String(e.message || e) })); return true; };

ext.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg || msg.target === "offscreen") return false;
  switch (msg.type) {
    case "ink:event": handleEvent(msg.event); return false;
    case "ink:start": return answer(startSession(msg.tabId), reply);
    case "ink:control": return answer(control(msg.action), reply);
    case "ink:status": return answer(getState(), reply);
    default: return false;
  }
});

// The bubble disappears when the page reloads; bring it back. Closing the tab ends the meeting.
ext.tabs.onUpdated.addListener(async (tabId, change) => {
  const st = await getState();
  if (st.tabId === tabId && change.status === "complete" && ["recording", "paused"].includes(st.status)) injectUi(tabId).catch(() => {});
});
ext.tabs.onRemoved.addListener(async (tabId) => {
  const st = await getState();
  if (st.tabId === tabId && ["recording", "paused"].includes(st.status)) stopSession().catch(() => {});
});

ext.alarms.create("ink-cleanup", { periodInMinutes: 60 });
ext.alarms.onAlarm.addListener((a) => { if (a.name === "ink-cleanup") cleanupExpired(ext.storage.local); });
ext.runtime.onStartup.addListener(() => cleanupExpired(ext.storage.local));
ext.runtime.onInstalled.addListener((d) => {
  cleanupExpired(ext.storage.local);
  if (d.reason === "install") ext.runtime.openOptionsPage();
});
