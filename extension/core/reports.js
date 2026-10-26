// Saving reports in the browser's storage, deleting them after 72 hours, and the optional upload to the report server.

import { REPORT_TTL_HOURS } from "./config.js";

const PREFIX = "report:";
const HOUR = 3600 * 1000;

export function newReportId() {
  const bytes = crypto.getRandomValues(new Uint8Array(12));            // 96 random bits: not guessable
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** @param {{get: Function, set: Function, remove: Function}} storage chrome.storage.local (or a fake in tests) */
export async function saveReport(storage, record, now = Date.now()) {
  const id = newReportId();
  const saved = {
    ...record, id,
    created_at: new Date(now).toISOString(),
    expires_at: new Date(now + REPORT_TTL_HOURS * HOUR).toISOString(),
  };
  const index = (await storage.get("reportIds")).reportIds || [];
  await storage.set({ [PREFIX + id]: saved, reportIds: [...index, id] });
  return saved;
}

export async function loadReport(storage, id) {
  return (await storage.get(PREFIX + id))[PREFIX + id] || null;
}

/** Delete every report older than its 72 hours. Returns how many were removed. */
export async function cleanupExpired(storage, now = Date.now()) {
  const ids = (await storage.get("reportIds")).reportIds || [];
  if (!ids.length) return 0;
  const records = await storage.get(ids.map((i) => PREFIX + i));
  const keep = [], drop = [];
  for (const id of ids) {
    const r = records[PREFIX + id];
    (r && Date.parse(r.expires_at) > now ? keep : drop).push(id);
  }
  if (drop.length) {
    await storage.remove(drop.map((i) => PREFIX + i));
    await storage.set({ reportIds: keep });
  }
  return drop.length;
}

/** Only report-worthy fields leave the browser; the server also re-validates everything. */
export function toCloudPayload(record) {
  const { title, started_at, duration_minutes, platform, host_name, analysis, participants, languages, transcript, talk, bookmarks } = record;
  return {
    title: title || "Meeting Report", started_at, duration_minutes, platform, host_name, analysis, participants, languages, bookmarks: bookmarks || [],
    transcript: (transcript || []).map(({ t, speaker, text, translation, mention }) => ({ t, speaker, text, translation, mention })),
    talk,
  };
}

/** Uploads one report to the report server and returns its public link (valid for 72 hours there). */
export async function uploadToCloud(cloudUrl, record, { fetchImpl = (...a) => fetch(...a), token = "" } = {}) {
  const headers = { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  const r = await fetchImpl(`${cloudUrl.replace(/\/+$/, "")}/api/report`, { method: "POST", headers, body: JSON.stringify(toCloudPayload(record)) });
  if (!r.ok) throw new Error(`The report server answered ${r.status}`);
  const data = await r.json();
  if (!data.url) throw new Error("The report server did not return a link");
  return data.url;
}
