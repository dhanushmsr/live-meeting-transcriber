import test from "node:test";
import assert from "node:assert/strict";
import { ApiError, Gemini } from "../core/gemini.js";

const json = (obj, status = 200, headers = {}) => new Response(JSON.stringify(obj), { status, headers });
const reply = (text) => json({ candidates: [{ content: { parts: [{ text }] } }] });

function fakeFetch(handler) {
  const calls = [];
  const f = async (url, opts = {}) => { calls.push({ url: String(url), opts }); return handler(String(url), opts, calls.length); };
  f.calls = calls;
  return f;
}

test("sends the key as a header, asks for JSON when told to, and reads the text back", async () => {
  const f = fakeFetch(() => reply('{"a": 1}'));
  const g = new Gemini("KEY", { fetchImpl: f });
  const text = await g.generate("hello", { model: "m1", json: true });
  assert.equal(text, '{"a": 1}');
  assert.match(f.calls[0].url, /models\/m1:generateContent$/);
  assert.equal(f.calls[0].opts.headers["x-goog-api-key"], "KEY");
  assert.ok(!f.calls[0].url.includes("KEY"), "the key is never put in the address");
  const body = JSON.parse(f.calls[0].opts.body);
  assert.equal(body.generationConfig.responseMimeType, "application/json");
  assert.equal(body.contents[0].parts[0].text, "hello");
});

test("a busy server (503) is retried and then succeeds", async () => {
  const f = fakeFetch((u, o, n) => (n < 2 ? json({ error: "busy" }, 503) : reply("ok")));
  assert.equal(await new Gemini("k", { fetchImpl: f }).generate("x", { model: "m" }), "ok");
  assert.equal(f.calls.length, 2);
});

test("a bad key (400/403) fails immediately and is not retried", async () => {
  const f = fakeFetch(() => json({ error: "API key not valid" }, 400));
  await assert.rejects(new Gemini("k", { fetchImpl: f }).generate("x", { model: "m" }), (e) => e instanceof ApiError && e.status === 400);
  assert.equal(f.calls.length, 1);
});

test("when the strong model is rate-limited the lighter one answers", async () => {
  const f = fakeFetch((url) => (url.includes("strong") ? json({ error: "quota" }, 429) : reply('{"ok": true}')));
  const g = new Gemini("k", { reportModel: "strong", fallbackModel: "light", fetchImpl: f });
  const out = await g.withFallback("x", { json: true, timeoutMs: 5000 });
  assert.deepEqual(out, { ok: true });
  assert.ok(f.calls.some((c) => c.url.includes("light")));
}, { timeout: 30_000 });

test("JSON wrapped in a code fence is still understood", async () => {
  const f = fakeFetch(() => reply('```json\n{"tldr": "Done."}\n```'));
  const out = await new Gemini("k", { fetchImpl: f }).withFallback("x", { json: true });
  assert.deepEqual(out, { tldr: "Done." });
});

test("a call that takes too long is cut off", async () => {
  const f = fakeFetch((u, o) => new Promise((_, rej) => o.signal.addEventListener("abort", () => rej(Object.assign(new Error("aborted"), { name: "AbortError" })))));
  await assert.rejects(new Gemini("k", { fetchImpl: f }).generate("x", { model: "m", timeoutMs: 30, attempts: 1 }), /too long/);
});

test("the analysis prompt asks for the one-page brief and passes bookmarks without naming them in the output", async () => {
  const f = fakeFetch(() => reply(JSON.stringify({ tldr: "t", decisions: ["d"] })));
  const out = await new Gemini("k", { fetchImpl: f }).analyse("[00:05] Ravi: hi", "Dhanush", "", ["00:17 We launch Friday."]);
  assert.equal(out.tldr, "t");
  const prompt = JSON.parse(f.calls[0].opts.body).contents[0].parts[0].text;
  for (const need of ['"tldr"', '"action_items"', '"due"', '"takeaways"', '"chapters"', '"follow_up_email"', "never mention the bookmarks", "00:17 We launch Friday."]) {
    assert.ok(prompt.includes(need), need);
  }
});

test("uploading a recording follows Google's resumable protocol", async () => {
  const f = fakeFetch((url, opts) => {
    if (url.includes("/upload/v1beta/files")) return new Response("{}", { headers: { "x-goog-upload-url": "https://upload.example/abc" } });
    return json({ file: { name: "files/xyz", uri: "https://files/xyz", state: "ACTIVE" } });
  });
  const file = await new Gemini("k", { fetchImpl: f }).uploadFile(new Blob(["12345"], { type: "audio/wav" }), "audio/wav");
  assert.equal(file.name, "files/xyz");
  const [start, finish] = f.calls;
  assert.equal(start.opts.headers["X-Goog-Upload-Command"], "start");
  assert.equal(start.opts.headers["X-Goog-Upload-Header-Content-Length"], "5");
  assert.equal(finish.url, "https://upload.example/abc");
  assert.equal(finish.opts.headers["X-Goog-Upload-Command"], "upload, finalize");
});

test("voice separation names the voices, and the upload is deleted afterwards even when the analysis fails", async () => {
  let deleted = null, fail = false;
  const f = fakeFetch((url, opts) => {
    if (opts.method === "DELETE") { deleted = url; return json({}); }
    if (url.includes("/upload/")) return new Response("{}", { headers: { "x-goog-upload-url": "https://upload.example/u" } });
    if (url === "https://upload.example/u") return json({ file: { name: "files/a1", uri: "https://files/a1", state: "ACTIVE" } });
    if (fail) return json({ error: "broken" }, 400);
    return reply(JSON.stringify({ voices: { "voice 1": "Anita", "voice 2": "" }, turns: [
      { start: 2, end: 9, voice: "voice 1", starts: "Hello everyone this is" }, { start: 10, end: 18, voice: "voice 2", starts: "Sure Anita" }] }));
  });
  const g = new Gemini("k", { fetchImpl: f });
  const turns = await g.diarize(new Blob(["x"]), ["Paul"]);
  assert.deepEqual(turns.map((t) => t.speaker), ["Anita", "Speaker 2"]);
  assert.match(deleted, /files\/a1$/);
  const gen = f.calls.find((c) => c.url.includes(":generateContent"));
  const parts = JSON.parse(gen.opts.body).contents[0].parts;
  assert.equal(parts[0].fileData.fileUri, "https://files/a1");
  assert.ok(parts[1].text.includes("Paul"), "people seen on screen are passed as a hint");

  deleted = null; fail = true;
  await assert.rejects(g.diarize(new Blob(["x"]), []));
  assert.match(deleted, /files\/a1$/, "the recording does not stay on Google's side");
}, { timeout: 60_000 });
