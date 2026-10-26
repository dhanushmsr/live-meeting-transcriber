// Manual end-to-end test in a REAL Chrome (not part of `npm test`): it loads the built extension into an isolated, temporary
// browser profile, plays a WAV file as the microphone, and checks the whole chain: service worker, offscreen document,
// audio worklet, live captions, analysis and the report page.
//
//   GEMINI_API_KEY=... node tests/chrome.manual.mjs path/to/meeting.wav [path/to/chrome.exe]
//
// It talks to Chrome over the debugging pipe (Extensions.loadUnpacked), which is how Chrome 137+ still lets tools load
// unpacked extensions. Nothing touches your normal Chrome profile.

import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const key = process.env.GEMINI_API_KEY;
const wav = process.argv[2] && resolve(process.argv[2]);
const chromePath = process.argv[3] || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const extDir = resolve(new URL("../dist/chrome", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
if (!key || !wav) { console.error("usage: GEMINI_API_KEY=... node tests/chrome.manual.mjs meeting.wav [chrome.exe]"); process.exit(2); }

const profile = mkdtempSync(join(tmpdir(), "ink-chrome-"));
const t0 = Date.now();
const at = () => `${((Date.now() - t0) / 1000).toFixed(1)}s`;
const log = (...a) => console.log(`[${at()}]`, ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const chrome = spawn(chromePath, [
  "--headless=new", "--remote-debugging-pipe", "--enable-unsafe-extension-debugging", `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--autoplay-policy=no-user-gesture-required",
  "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", `--use-file-for-fake-audio-capture=${wav}%noloop`,
  "about:blank",
], { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"] });

let n = 0, buf = "";
const waiting = new Map(), listeners = [];
chrome.stdio[4].on("data", (d) => {
  buf += d.toString();
  let i;
  while ((i = buf.indexOf("\0")) >= 0) {
    const msg = JSON.parse(buf.slice(0, i)); buf = buf.slice(i + 1);
    if (msg.id && waiting.has(msg.id)) { const { ok, bad } = waiting.get(msg.id); waiting.delete(msg.id); msg.error ? bad(new Error(JSON.stringify(msg.error))) : ok(msg.result); }
    else listeners.forEach((f) => f(msg));
  }
});
const send = (method, params = {}, sessionId) => new Promise((ok, bad) => {
  const id = ++n; waiting.set(id, { ok, bad });
  chrome.stdio[3].write(JSON.stringify({ id, method, params, sessionId }) + "\0");
});
const waitFor = (pred, ms = 15000) => new Promise((ok, bad) => {
  const timer = setTimeout(() => bad(new Error("timed out waiting for a browser event")), ms);
  const f = (m) => { const v = pred(m); if (v) { clearTimeout(timer); listeners.splice(listeners.indexOf(f), 1); ok(v); } };
  listeners.push(f);
});
async function attach(targetId) { return (await send("Target.attachToTarget", { targetId, flatten: true })).sessionId; }
async function evaluate(sessionId, expression) {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}

const results = [];
const check = (name, ok, detail = "") => { results.push(ok); log(ok ? "PASS" : "FAIL", name, detail); };

try {
  await send("Target.setDiscoverTargets", { discover: true });
  const { id } = await send("Extensions.loadUnpacked", { path: extDir });
  check("Chrome accepts the extension (manifest, permissions, icons)", !!id, id);
  if (process.env.DEBUG_TARGETS) {   // debugging aid: list every target Chrome knows about
    await sleep(1500);
    const t = await send("Target.getTargets");
    for (const x of t.targetInfos) console.log(`${x.type} ${x.url.slice(0, 90)}`);
  }

  // find the extension's service worker (it may already be running) and wait until its extension APIs exist
  let sw = null;
  for (let i = 0; i < 50 && !sw; i++) {
    const { targetInfos } = await send("Target.getTargets");
    sw = targetInfos.find((t) => t.type === "service_worker" && t.url.startsWith(`chrome-extension://${id}/background.js`));
    if (!sw) await sleep(300);
  }
  if (!sw) throw new Error("the extension's service worker never started");
  const swSession = await attach(sw.targetId);
  await send("Runtime.enable", {}, swSession);
  for (let i = 0; i < 40; i++) {
    try { if (await evaluate(swSession, "typeof chrome !== 'undefined' && !!chrome.runtime && !!chrome.storage")) break; } catch { /* not ready yet */ }
    await sleep(250);
  }
  const manifest = await evaluate(swSession, "(() => { const m = chrome.runtime.getManifest(); return { v: m.version, perms: m.permissions }; })()");
  check("the service worker starts (all its imports load)", manifest.v === process.env.INK_VERSION || /^\d+\.\d+\.\d+$/.test(manifest.v), JSON.stringify(manifest.perms.slice(-3)));

  await evaluate(swSession, `chrome.storage.local.set({ settings: { apiKey: ${JSON.stringify(key)}, name: "Dhanush", targetLanguage: "English", translate: false, captions: true, nameSpeakers: false, shareOnline: false, cloudUrl: "https://inkwake-reports.inkwake.workers.dev", micDeviceId: "" } })`);
  await evaluate(swSession, "globalThis.__events = []; chrome.runtime.onMessage.addListener((m) => { if (m && m.type === 'ink:event') __events.push(m.event); }); true");

  // the popup and the options page, as real extension pages (strict security policy enforced by Chrome itself)
  for (const page of ["popup.html", "options.html"]) {
    const { targetId } = await send("Target.createTarget", { url: `chrome-extension://${id}/${page}` });
    const s = await attach(targetId);
    await sleep(800);
    const text = await evaluate(s, "document.body.innerText.replace(/\\s+/g, ' ').slice(0, 90)");
    check(`${page} renders as a real extension page`, text.length > 20, JSON.stringify(text));
  }

  // the audio engine in Chrome's offscreen document: fake microphone = the WAV file
  await evaluate(swSession, "chrome.offscreen.createDocument({ url: 'offscreen.html', reasons: ['USER_MEDIA', 'AUDIO_PLAYBACK'], justification: 'test' }).then(() => true)");
  const started = await evaluate(swSession, "chrome.storage.local.get('settings').then((r) => chrome.runtime.sendMessage({ target: 'offscreen', type: 'start', platform: 'Test', title: '', settings: r.settings }))");
  check("the engine starts: AudioContext, AudioWorklet, microphone and the live connection", started?.ok === true, JSON.stringify(started));
  if (started?.ok) {
    log("playing the recording as the microphone for ~40 s ...");
    await sleep(40000);
    const events = await evaluate(swSession, "__events.filter((e) => e.kind === 'caption' && e.final).map((e) => e.speaker + ': ' + e.text)");
    check("live captions came back through the real browser audio path", events.length > 0, JSON.stringify(events.slice(0, 2)));
    log("stopping: analysing ...");
    const stopped = await evaluate(swSession, "chrome.runtime.sendMessage({ target: 'offscreen', type: 'stop' })");
    const record = stopped?.value;
    check("stopping returns a finished report record", !!record && Array.isArray(record.transcript) && record.transcript.length > 0,
      record ? `${record.transcript.length} lines; tldr: ${String(record.analysis?.tldr).slice(0, 80)}` : JSON.stringify(stopped));
    if (record) {
      // save it the way the extension does, then open the report page in the real extension
      const rid = "0123456789abcdef01234567";
      await evaluate(swSession, `chrome.storage.local.set({ ['report:${rid}']: ${JSON.stringify({ ...record, id: rid, expires_at: new Date(Date.now() + 71 * 3600e3).toISOString() })}, reportIds: ['${rid}'] })`);
      const { targetId } = await send("Target.createTarget", { url: `chrome-extension://${id}/report.html?id=${rid}` });
      const s = await attach(targetId);
      await sleep(1500);
      const page = await evaluate(s, "({ title: document.title, sections: [...document.querySelectorAll('#reportContent h2')].map((e) => e.textContent), brief: document.querySelector('.brief p')?.textContent || '', libs: [typeof docx, typeof html2pdf] })");
      check("the report page renders from the extension's own storage", page.brief.length > 5 && page.libs.every((l) => l !== "undefined"), JSON.stringify(page).slice(0, 220));
      const exp = await evaluate(s, "(async () => { const got = {}; window.download = (b, n) => { got[n] = b.size; }; await exportDocx(); exportMarkdown(); return got; })()");
      check("exports work under Chrome's real extension security policy", Object.keys(exp).length === 2, JSON.stringify(exp));
    }
  }
} catch (e) {
  results.push(false);
  log("ERROR", e.message);
} finally {
  chrome.kill();
  await sleep(500);
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* the profile is temporary */ }
  const failed = results.filter((r) => !r).length;
  console.log(failed ? `\n${failed} check(s) FAILED` : `\nall ${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
}
