// Manual end-to-end check against the REAL Gemini API (not part of `npm test`: it needs a key and takes a minute).
//   GEMINI_API_KEY=... node tests/integration.manual.js path/to/meeting.wav
// The WAV must be 16 kHz mono 16-bit. It is fed in real time as the other side of a call.

import { readFileSync } from "node:fs";
import { Gemini } from "../core/gemini.js";
import { Session } from "../core/session.js";

const key = process.env.GEMINI_API_KEY;
const wavPath = process.argv[2];
if (!key || !wavPath) { console.error("usage: GEMINI_API_KEY=... node tests/integration.manual.js meeting.wav"); process.exit(2); }

const wav = readFileSync(wavPath);
const pcm = new Int16Array(wav.buffer.slice(wav.byteOffset + 44, wav.byteOffset + wav.byteLength - ((wav.byteLength - 44) % 2)));
const samples = Float32Array.from(pcm, (v) => v / 32768);
console.log(`audio: ${(samples.length / 16000).toFixed(1)} s`);

const t0 = Date.now();
const at = () => `${((Date.now() - t0) / 1000).toFixed(1)}s`;
const gemini = new Gemini(key, { reportModel: "gemini-flash-lite-latest", fallbackModel: "gemini-flash-lite-latest" });
const session = new Session({
  settings: { apiKey: key, name: "Dhanush", targetLanguage: "English", translate: false, nameSpeakers: true },
  gemini,
  hooks: {
    onCaption: (who, text, final) => final && console.log(`  [${at()}] caption  ${who}: ${text.slice(0, 90)}`),
    onNotice: (m) => console.log(`  [${at()}] NOTICE   ${m}`),
    onState: (s) => console.log(`  [${at()}] state    ${s}`),
  },
});
session.start({ platform: "Test", channels: ["Others"] });

// real-time pacing: 100 ms of audio every 100 ms
for (let i = 0; i + 1600 <= samples.length; i += 1600) {
  session.push("Others", samples.subarray(i, i + 1600));
  await new Promise((r) => setTimeout(r, 100));
}
await new Promise((r) => setTimeout(r, 4000));      // let the last words come back
console.log(`[${at()}] stopping: separating voices, analysing...`);
const record = await session.stop();
console.log(`[${at()}] done`);
console.log("participants:", record.participants);
for (const l of record.transcript) console.log(`  ${String(Math.round(l.t)).padStart(3)}s ${l.speaker.padEnd(8)} ${l.text.slice(0, 100)}`);
console.log("talk (s):", Object.fromEntries(Object.entries(record.talk).map(([k, v]) => [k, +Object.values(v).reduce((a, b) => a + b, 0).toFixed(1)])));
console.log("tldr:", record.analysis.tldr);
console.log("actions:", JSON.stringify(record.analysis.action_items));
