import test from "node:test";
import assert from "node:assert/strict";
import { LiveStream, DEAD_MIC_SECONDS, IDLE_FINALIZE } from "../core/live.js";
import { FakeWS, SETTINGS, sleep, tone } from "./helpers.js";

function make(extra = {}) {
  FakeWS.reset();
  const events = { partial: [], segments: [], talk: [], notes: [], recorded: 0 };
  let clock = 100;
  const stream = new LiveStream({
    label: extra.label || "Others", apiKey: "k", getSettings: () => extra.settings || SETTINGS, WebSocketImpl: FakeWS,
    now: () => clock,
    onPartial: (l, t) => events.partial.push([l, t]), onSegment: (s) => events.segments.push(s),
    onTalk: (l, m, s) => events.talk.push([l, m, s]), onNotice: (m) => events.notes.push(m),
    record: extra.record ? () => { events.recorded++; } : null,
  });
  return { stream, events, tick: (s) => { clock += s; }, ws: () => FakeWS.instances.at(-1) };
}
const feed = (stream, level, frames) => { for (let i = 0; i < frames; i++) stream.push(tone(level, 0.1)); };

test("connects, sends the setup message and only then streams audio", async () => {
  const { stream, ws } = make();
  stream.start();
  feed(stream, 0.05, 5);
  await sleep(120);
  const sent = ws().sent;
  assert.ok(sent[0].setup, "setup comes first");
  assert.equal(sent[0].setup.model, "models/gemini-3.5-live-translate-preview");
  assert.equal(sent[0].setup.generationConfig.translationConfig.targetLanguageCode, "en");
  assert.deepEqual(sent[0].setup.inputAudioTranscription, {});
  assert.ok(ws().audioFrames() >= 5, "buffered audio is sent once the session is ready");
  assert.equal(sent.find((m) => m.realtimeInput?.audio).realtimeInput.audio.mimeType, "audio/pcm;rate=16000");
  await stream.stop();
});

test("silence is not sent, speech is, with a little audio from just before it", async () => {
  const { stream, ws } = make();
  stream.start();
  await sleep(30);
  feed(stream, 0.0005, 30);               // 3 s of a quiet room
  await sleep(120);
  assert.equal(ws().audioFrames(), 0);
  feed(stream, 0.05, 3);                  // speech starts
  await sleep(120);
  assert.equal(ws().audioFrames(), 4 + 3, "4 frames of pre-roll + 3 of speech");
  feed(stream, 0.0005, 20);               // then silence: only the hang-over is sent
  await sleep(120);
  assert.ok(ws().audioFrames() >= 7 + 11 && ws().audioFrames() <= 7 + 13, `got ${ws().audioFrames()}`);
  await stream.stop();
});

test("words become a growing caption, then one finished line after a pause", async () => {
  const { stream, events, ws, tick } = make();
  stream.start();
  feed(stream, 0.05, 20);
  await sleep(60);
  ws().reply({ serverContent: { inputTranscription: { text: "Hello" }, outputTranscription: { text: "Hola" } } });
  ws().reply({ serverContent: { inputTranscription: { text: " team." }, outputTranscription: { text: " equipo." } } });
  await sleep(20);
  assert.deepEqual(events.partial.at(-1), ["Others", "Hola equipo."]);       // translation is shown while translate is on
  assert.equal(events.segments.length, 0);
  tick(IDLE_FINALIZE + 0.5);
  stream._idle();
  assert.equal(events.segments.length, 1);
  const s = events.segments[0];
  assert.deepEqual([s.text, s.translation, s.channel, s.speaker], ["Hello team.", "Hola equipo.", "Others", "Others"]);
  assert.ok(s.t_end >= s.t);
  await stream.stop();
});

test("with translation off the caption shows the original words", async () => {
  const { stream, events, ws } = make({ settings: { ...SETTINGS, translate: false } });
  stream.start();
  feed(stream, 0.05, 5);
  await sleep(60);
  ws().reply({ serverContent: { inputTranscription: { text: "Bonjour" }, outputTranscription: { text: "Hello" } } });
  await sleep(10);
  assert.deepEqual(events.partial.at(-1), ["Others", "Bonjour"]);
  await stream.stop();
});

test("a GoAway reconnects quietly and audio keeps flowing on the new connection", async () => {
  const { stream, events, ws } = make();
  stream.start();
  feed(stream, 0.05, 5);
  await sleep(80);
  ws().reply({ goAway: { timeLeft: "50s" } });
  await sleep(1300);                      // the loop pauses ~1 s after a clean end only if it failed; here it is immediate
  assert.ok(FakeWS.instances.length >= 2, "a second connection was opened");
  feed(stream, 0.05, 5);
  await sleep(100);
  assert.ok(ws().audioFrames() >= 5, "audio flows again");
  assert.deepEqual(events.notes, [], "the user never hears about it");
  await stream.stop();
});

test("a failing connection is retried silently twice and only reported on the third failure", async () => {
  const { stream, events } = make();
  FakeWS.failFirst = 3;
  stream.start();
  await sleep(2000);                      // two failures so far (retries after 1 s and 2 s): the user hears nothing
  assert.equal(FakeWS.instances.length, 2);
  assert.deepEqual(events.notes, []);
  await sleep(1600);                      // the third failure happens at about 3 s
  assert.equal(events.notes.length, 1);
  assert.match(events.notes[0], /keep disconnecting/);
  await sleep(3500);                      // the fourth attempt succeeds and the stream carries on, without more warnings
  assert.ok(FakeWS.instances.length >= 4);
  assert.equal(events.notes.length, 1);
  await stream.stop();
});

test("stopping tells the server the audio is over and waits for the last words", async () => {
  const { stream, events, ws } = make();
  stream.start();
  feed(stream, 0.05, 10);
  await sleep(80);
  const stopping = stream.stop();
  await sleep(120);
  assert.ok(ws().endedAudio(), "audioStreamEnd was sent");
  ws().reply({ serverContent: { inputTranscription: { text: "Last words." } } });   // arrives after the end marker
  await stopping;
  assert.equal(events.segments.at(-1).text, "Last words.");
});

test("a dead microphone is reported once, the other side of the call never is", async () => {
  const mic = make({ label: "You" });
  for (let i = 0; i < (DEAD_MIC_SECONDS / 0.1) + 10; i++) mic.stream.push(new Float32Array(1600));
  assert.equal(mic.events.notes.length, 1);
  assert.match(mic.events.notes[0], /can't hear your microphone/);
  const remote = make({ label: "Others" });
  for (let i = 0; i < 150; i++) remote.stream.push(new Float32Array(1600));
  assert.deepEqual(remote.events.notes, []);
  const working = make({ label: "You" });
  feed(working.stream, 0.02, 150);
  assert.deepEqual(working.events.notes, []);
});

test("every frame is recorded for later voice separation, including silence", () => {
  const { stream, events } = make({ record: true });
  feed(stream, 0.05, 10);
  feed(stream, 0.0005, 10);
  assert.equal(events.recorded, 20);
  assert.ok(Math.abs(stream.offset - 2.0) < 1e-9);
});

test("odd block sizes are re-framed into 100 ms frames without losing audio", () => {
  const { stream, events } = make({ record: true });
  for (let i = 0; i < 10; i++) stream.push(new Float32Array(1000));      // 10 000 samples = 6 frames + 400 left over
  assert.equal(events.recorded, 6);
  assert.ok(Math.abs(stream.offset - 0.6) < 1e-9);
});
