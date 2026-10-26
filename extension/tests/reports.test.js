import test from "node:test";
import assert from "node:assert/strict";
import { cleanupExpired, loadReport, newReportId, saveReport, toCloudPayload, uploadToCloud } from "../core/reports.js";
import { platformFromUrl, titleFromTab } from "../core/meeting.js";

function fakeStorage() {
  const data = {};
  return {
    data,
    async get(keys) {
      const list = typeof keys === "string" ? [keys] : keys;
      return Object.fromEntries(list.filter((k) => k in data).map((k) => [k, data[k]]));
    },
    async set(obj) { Object.assign(data, obj); },
    async remove(keys) { for (const k of [].concat(keys)) delete data[k]; },
  };
}
const HOUR = 3600 * 1000;

test("report ids are 24 random hex characters", () => {
  const ids = new Set(Array.from({ length: 50 }, newReportId));
  assert.equal(ids.size, 50);
  assert.ok([...ids].every((i) => /^[a-f0-9]{24}$/.test(i)));
});

test("a saved report can be read back and knows when it expires", async () => {
  const s = fakeStorage();
  const saved = await saveReport(s, { title: "Sync", transcript: [] }, Date.UTC(2026, 9, 19, 8, 0));
  assert.equal(saved.created_at, "2026-10-19T08:00:00.000Z");
  assert.equal(saved.expires_at, "2026-10-22T08:00:00.000Z");        // exactly 72 hours
  assert.equal((await loadReport(s, saved.id)).title, "Sync");
  assert.equal(await loadReport(s, "nope"), null);
});

test("reports older than 72 hours are deleted, younger ones stay", async () => {
  const s = fakeStorage();
  const t0 = Date.UTC(2026, 9, 19);
  const old = await saveReport(s, { title: "old" }, t0);
  const fresh = await saveReport(s, { title: "fresh" }, t0 + 60 * HOUR);
  assert.equal(await cleanupExpired(s, t0 + 71 * HOUR), 0);          // nothing is due yet
  assert.equal(await cleanupExpired(s, t0 + 73 * HOUR), 1);          // the first one is past its 72 hours
  assert.equal(await loadReport(s, old.id), null);
  assert.equal((await loadReport(s, fresh.id)).title, "fresh");
  assert.deepEqual(s.data.reportIds, [fresh.id]);
  assert.equal(await cleanupExpired(s, t0 + 200 * HOUR), 1);
  assert.deepEqual(s.data.reportIds, []);
});

test("only report fields go to the server, and the upload returns the public link", async () => {
  const record = { title: "", started_at: "x", duration_minutes: 1, host_name: "D", analysis: { tldr: "t" }, bookmarks: [], participants: ["A"], languages: [],
    transcript: [{ t: 1, speaker: "A", text: "hi", translation: "", mention: false, channel: "Others", diar: "Speaker 1", t_end: 4 }], talk: { A: { 0: 5 } },
    id: "secret-local-id", expires_at: "later" };
  const payload = toCloudPayload(record);
  assert.deepEqual(Object.keys(payload.transcript[0]).sort(), ["mention", "speaker", "t", "text", "translation"]);
  assert.equal(payload.title, "Meeting Report");
  assert.ok(!("id" in payload) && !("expires_at" in payload));
  const calls = [];
  const url = await uploadToCloud("https://reports.example/", record, {
    token: "tok",
    fetchImpl: async (u, o) => { calls.push([u, o]); return new Response(JSON.stringify({ url: "https://reports.example/r/abc" })); },
  });
  assert.equal(url, "https://reports.example/r/abc");
  assert.equal(calls[0][0], "https://reports.example/api/report");
  assert.equal(calls[0][1].headers.Authorization, "Bearer tok");
  await assert.rejects(uploadToCloud("https://r.example", record, { fetchImpl: async () => new Response("{}", { status: 500 }) }), /500/);
});

test("the meeting platform comes from the address", () => {
  const cases = { "https://meet.google.com/abc-defg-hij": "Google Meet", "https://us02web.zoom.us/wc/join/123": "Zoom",
    "https://teams.microsoft.com/v2/": "Microsoft Teams", "https://teams.live.com/meet/9": "Microsoft Teams", "https://web.whatsapp.com/": "WhatsApp",
    "https://meet.jit.si/Room": "Jitsi", "https://example.com/": "", "not a url": "" };
  for (const [url, want] of Object.entries(cases)) assert.equal(platformFromUrl(url), want, url);
});

test("the meeting name comes from the tab title when it really is a name", () => {
  assert.equal(titleFromTab("Weekly Sync - Google Meet"), "Weekly Sync");
  assert.equal(titleFromTab("Weekly Sync | Microsoft Teams"), "Weekly Sync");
  assert.equal(titleFromTab("Meet - abc-defg-hij"), "");
  assert.equal(titleFromTab("Zoom Meeting"), "");
  assert.equal(titleFromTab("Meet – Design review"), "Design review");
  assert.equal(titleFromTab(""), "");
});
