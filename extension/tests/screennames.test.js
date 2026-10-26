import test from "node:test";
import assert from "node:assert/strict";
import { ScreenNames } from "../core/screennames.js";
import { Session } from "../core/session.js";
import { FakeWS, SETTINGS } from "./helpers.js";

const make = (own = ["Dhanush M"]) => new ScreenNames(() => own);

test("a line gets the name highlighted on the page while it was spoken", () => {
  const s = make();
  s.observe(1, { speaker: "Anita", participants: ["Anita", "Paul"] });
  s.observe(6, { speaker: null });
  s.observe(7, { speaker: "Paul" });
  s.observe(12, { speaker: null });
  assert.equal(s.nameFor(1.5, 5.5), "Anita");
  assert.equal(s.nameFor(7.2, 11), "Paul");
  assert.equal(s.nameFor(5, 9), "Paul", "the person highlighted for most of the line wins");
  assert.equal(s.current(13), "Paul", "a short pause keeps the last speaker");
  assert.equal(s.current(40), null, "an old reading does not count forever when several people are there");
});

test("you are never a remote speaker, even when your own tile lights up", () => {
  const s = make();
  s.observe(1, { speaker: "Dhanush M", participants: ["Dhanush M", "Anita", "Paul"], self: "Dhanush M" });
  assert.equal(s.current(2), null);
  assert.deepEqual([...s.participants].sort(), ["Anita", "Paul"]);
  s.observe(3, { speaker: "You", self: "" });
  assert.equal(s.current(3.5), null);
});

test("with exactly one other person on the call, everything they say is theirs", () => {
  const s = make();
  s.observe(0, { speaker: null, participants: ["Anita"], self: "Dhanush M" });
  assert.equal(s.nameFor(30, 35), "Anita");
  s.observe(40, { speaker: null, participants: ["Anita", "Paul"] });
  assert.equal(s.nameFor(60, 65), null, "two others and no highlight: unknown, not a guess");
});

test("after the meeting, an early line may borrow the first name seen just after it", () => {
  const s = make();
  s.observe(5, { speaker: "Paul", participants: ["Anita", "Paul"] });
  assert.equal(s.nameFor(0.5, 2), null);
  assert.equal(s.nameFor(0.5, 2, { after: true }), "Paul");
  assert.equal(s.nameFor(-20, -15, { after: true }), null, "but not from far away");
});

test("the session names live captions, transcript lines and talk time from the page, and hints the voice separation", async () => {
  FakeWS.reset();
  const captions = [];
  const calls = [];
  const gemini = {
    async diarize(blob, names) { calls.push(names); return []; },
    async analyse() { return { tldr: "ok" }; },
    async liveNotes() { return {}; },
  };
  const s = new Session({ settings: { ...SETTINGS }, gemini, WebSocketImpl: FakeWS, hooks: { onCaption: (...a) => captions.push(a) } });
  s.start({ platform: "Google Meet" });
  s.observeScreen({ speaker: "Anita", participants: ["Anita", "Paul", "Dhanush M"], self: "Dhanush M" });
  s._segment({ speaker: "Others", t: 0, t_end: 3, text: "Hello, this is Anita.", translation: "", channel: "Others" });
  s._segment({ speaker: "You", t: 4, text: "Hi Anita.", translation: "", channel: "You" });
  s._talk("Others", 0, 3);
  assert.deepEqual(captions.map((c) => c[0]), ["Anita", "Dhanush M"]);
  assert.deepEqual(s.transcript.map((l) => l.speaker), ["Anita", "Dhanush M"]);
  assert.ok(s.talk.Anita && !s.talk.Others, "talk time goes to the person, not 'Others'");
  s.streams.Others.offset = 30; s._chunks = [new Int16Array(16000)];   // pretend enough was recorded to separate voices
  const record = await s.stop();
  assert.deepEqual(calls[0].sort(), ["Anita", "Paul"], "the names on screen are given to the voice separation as a hint");
  assert.deepEqual(record.participants.sort(), ["Anita", "Paul"]);
}, { timeout: 30_000 });
