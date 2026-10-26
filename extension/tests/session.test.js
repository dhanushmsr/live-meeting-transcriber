import test from "node:test";
import assert from "node:assert/strict";
import { Session, bookmarkContext } from "../core/session.js";
import { FakeWS, SETTINGS, sleep, tone } from "./helpers.js";

function fakeGemini(over = {}) {
  return {
    calls: [],
    async analyse(text, host, title, marks) {
      this.calls.push(["analyse", text, marks]);
      return over.analyse || { tldr: "We launch Friday.", decisions: ["Ship Friday"], action_items: [{ task: "Send deck", owner: "Anita", due: "Wed" }],
        languages: ["English"], chapters: [{ start: "00:02", title: "Launch", summary: "x" }] };
    },
    async diarize(blob) {
      this.calls.push(["diarize", blob.size]);
      if (over.diarizeError) throw new Error(over.diarizeError);
      return over.turns || [{ start: 2, end: 9, speaker: "Anita", starts: "Hello everyone this is Anita" },
        { start: 10, end: 18, speaker: "Paul", starts: "Sure Anita this is Paul" }];
    },
    async liveNotes(text, prev, host) { this.calls.push(["notes", text]); return { gist: "They are planning a launch.", now: "dates", decisions: [], action_items: [], open_questions: [] }; },
  };
}

function make({ settings = {}, gemini = fakeGemini(), notesEveryMs = 60_000 } = {}) {
  FakeWS.reset();
  const ev = { captions: [], states: [], notices: [], mentions: [], bookmarks: [], notes: [] };
  const s = new Session({
    settings: { ...SETTINGS, ...settings }, gemini, WebSocketImpl: FakeWS, notesEveryMs,
    hooks: { onCaption: (...a) => ev.captions.push(a), onState: (x) => ev.states.push(x), onNotice: (m) => ev.notices.push(m),
      onMention: (...a) => ev.mentions.push(a), onBookmark: (t) => ev.bookmarks.push(t), onNotes: (n) => ev.notes.push(n) },
  });
  return { s, ev, gemini, socket: (i) => FakeWS.instances[i] };
}
const speak = (s, label, secs, level = 0.05) => { for (let i = 0; i < secs * 10; i++) s.push(label, tone(level, 0.1)); };
const finish = (stream, text, translation = "") => { stream._src = text; stream._out = translation; stream._lastFrag = 0; stream._lineT = stream._lineT || 2; stream._finalizeLine(); };

test("a meeting runs from start to a finished report, with the voices named", async () => {
  const { s, ev, gemini } = make();
  s.start({ platform: "Google Meet", title: "Launch sync" });
  speak(s, "Others", 20);
  speak(s, "You", 2);
  s.streams.Others._lineT = 2; s.streams.Others._lineEnd = 18;
  finish(s.streams.Others, "Hello everyone this is Anita. Sure Anita this is Paul speaking we launch Friday.");
  assert.equal(s.state, "recording");
  const record = await s.stop();
  assert.deepEqual(ev.states, ["recording", "processing", "idle"]);
  assert.ok(gemini.calls.some((c) => c[0] === "diarize"), "the recording was sent for voice separation");
  assert.deepEqual(record.participants.sort(), ["Anita", "Paul"]);
  assert.deepEqual(record.transcript.map((l) => l.speaker), ["Anita", "Paul"]);
  assert.equal(record.analysis.tldr, "We launch Friday.");
  assert.equal(record.title, "Launch sync");
  assert.equal(record.platform, "Google Meet");
  assert.ok(record.duration_minutes > 0.3);
  assert.ok(record.talk.Anita && record.talk.Paul && !record.talk.Others, "talk time is per person");
  assert.ok(record.transcript.every((l) => !("channel" in l) && !("diar" in l)), "internal fields never reach the report");
  assert.ok(Date.parse(record.started_at) > 0);
}, { timeout: 30_000 });

test("the analysis sees real names, not 'Others'", async () => {
  const { s, gemini } = make();
  s.start({});
  speak(s, "Others", 20);
  s.streams.Others._lineEnd = 18;
  finish(s.streams.Others, "Hello everyone this is Anita. Sure Anita this is Paul speaking we launch Friday.");
  await s.stop();
  const analyseCall = gemini.calls.find((c) => c[0] === "analyse");
  assert.match(analyseCall[1], /Anita:/);
  assert.doesNotMatch(analyseCall[1], /Others:/);
}, { timeout: 30_000 });

test("if voice separation fails the report is still produced, with a clear notice", async () => {
  const { s, ev } = make({ gemini: fakeGemini({ diarizeError: "quota" }) });
  s.start({});
  speak(s, "Others", 20);
  finish(s.streams.Others, "Hello everyone.");
  const record = await s.stop();
  assert.equal(record.analysis.tldr, "We launch Friday.");
  assert.ok(ev.notices.some((n) => /Could not separate speakers/.test(n)));
  assert.equal(record.transcript[0].speaker, "Others");
}, { timeout: 30_000 });

test("if the analysis fails the transcript is still delivered", async () => {
  const g = fakeGemini(); g.analyse = async () => { throw new Error("busy"); };
  const { s, ev } = make({ gemini: g, settings: { nameSpeakers: false } });
  s.start({});
  speak(s, "You", 3);
  finish(s.streams.You, "Notes to self.");
  const record = await s.stop();
  assert.deepEqual(record.analysis, {});
  assert.equal(record.transcript[0].text, "Notes to self.");
  assert.ok(ev.notices.some((n) => /Analysis failed/.test(n)));
}, { timeout: 30_000 });

test("the other side is not recorded when speaker naming is switched off", async () => {
  const { s, gemini } = make({ settings: { nameSpeakers: false } });
  s.start({});
  speak(s, "Others", 20);
  finish(s.streams.Others, "Hello.");
  await s.stop();
  assert.ok(!gemini.calls.some((c) => c[0] === "diarize"));
  assert.equal(s._chunks.length, 0);
}, { timeout: 30_000 });

test("someone saying your name raises an alert; your own words never do", () => {
  const { s, ev } = make();
  s.start({});
  s._segment({ speaker: "Others", t: 5, text: "Dhanush, can you take this?", translation: "", channel: "Others" });
  s._segment({ speaker: "You", t: 9, text: "I am Dhanush and I will.", translation: "", channel: "You" });
  s._segment({ speaker: "Others", t: 12, text: "Dhanushka joined", translation: "", channel: "Others" });
  assert.equal(ev.mentions.length, 1);
  assert.equal(ev.mentions[0][0], "Others");
  assert.deepEqual(s.transcript.map((l) => l.mention), [true, false, false]);
  assert.equal(s.transcript[1].speaker, "Dhanush M", "your lines carry your profile name");
});

test("names in other scripts are matched too, and no name means no alerts", () => {
  const { s } = make();
  assert.equal(s.mentionsMe("Ravi", "நாளைக்கு Dhanush வருவார்"), true);
  const anon = make({ settings: { name: "" } }).s;
  assert.equal(anon.mentionsMe("Ravi", "Dhanush?"), false);
});

test("bookmarks get the words spoken near them, but never invent context", () => {
  const tr = [{ t: 60, text: "We must ship Friday.", translation: "" }, { t: 400, text: "later", translation: "" }];
  const out = bookmarkContext([{ t: 70, text: "" }, { t: 200, text: "" }, { t: 5, text: "mine" }], tr);
  assert.equal(out[0].text, "We must ship Friday.");
  assert.equal(out[1].text, "");
  assert.equal(out[2].text, "mine");
});

test("pausing drops audio and resuming brings it back; bookmarks and catch-up work live", async () => {
  const { s, ev } = make();
  s.start({});
  speak(s, "You", 1);
  const before = s.streams.You.offset;
  s.pause();
  speak(s, "You", 3);
  assert.equal(s.streams.You.offset, before, "the clock does not advance while paused");
  s.resume();
  speak(s, "You", 1);
  assert.ok(s.streams.You.offset > before);
  s._segment({ speaker: "Others", t: 3, text: "Budget is fixed.", translation: "", channel: "Others" });
  s.bookmark();
  assert.equal(ev.bookmarks.length, 1);
  assert.equal(s.bookmarks[0].text, "Budget is fixed.");
  await s.requestCatchUp();
  assert.equal(ev.notes[0].updating, true);                       // the latest lines appear first, instantly
  assert.equal(ev.notes[0].recent[0].text, "Budget is fixed.");
  assert.equal(ev.notes.at(-1).gist, "They are planning a launch.");
  await s.stop();
}, { timeout: 30_000 });

test("catch-up with nothing said yet says so, and a failing model still shows the latest lines", async () => {
  const { s, ev } = make();
  s.start({});
  await s.requestCatchUp();
  assert.equal(ev.notes.at(-1).gist, "Nothing has been said yet.");
  s._segment({ speaker: "Others", t: 3, text: "Hello there.", translation: "", channel: "Others" });
  s.gemini.liveNotes = async () => { throw new Error("overloaded"); };
  await s.requestCatchUp();
  const last = ev.notes.at(-1);
  assert.equal(last.error, true);
  assert.equal(last.recent[0].text, "Hello there.");
  await s.stop();
}, { timeout: 30_000 });
