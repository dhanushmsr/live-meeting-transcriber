import test from "node:test";
import assert from "node:assert/strict";
import {
  DEAD_RMS, MAX_SPEECH_RMS, MIN_SPEECH_RMS, NoiseFloor, Resampler, SR, floatToInt16, int16ToBase64, rms, speechThreshold, wavBlob,
} from "../core/audio.js";
import { tone } from "./helpers.js";

test("48 kHz audio becomes 16 kHz with the right length and no seams between blocks", () => {
  const r = new Resampler(48000);
  const whole = tone(0.1, 1.0, 48000);
  const blocks = [];
  for (let i = 0; i < whole.length; i += 480) blocks.push(r.process(whole.subarray(i, i + 480)));   // 10 ms blocks
  const out = new Float32Array(blocks.reduce((n, b) => n + b.length, 0));
  let o = 0;
  for (const b of blocks) { out.set(b, o); o += b.length; }
  assert.ok(Math.abs(out.length - SR) <= 2, `got ${out.length} samples`);
  assert.ok(Math.abs(rms(out) - rms(tone(0.1, 1.0, 16000))) < 0.005);          // the tone survives
  let worstJump = 0;
  for (let i = 1; i < out.length; i++) worstJump = Math.max(worstJump, Math.abs(out[i] - out[i - 1]));
  assert.ok(worstJump < 0.12, `a seam would show as a jump, worst step was ${worstJump}`);
});

test("44.1 kHz works too and 16 kHz passes straight through", () => {
  const out = new Resampler(44100).process(tone(0.1, 1.0, 44100));
  assert.ok(Math.abs(out.length - SR) <= 2);
  const same = tone(0.1, 0.2, 16000);
  assert.deepEqual(new Resampler(16000).process(same), same);
});

test("threshold is 3x the floor, kept within limits", () => {
  assert.equal(speechThreshold(0), MIN_SPEECH_RMS);
  assert.ok(Math.abs(speechThreshold(0.005) - 0.015) < 1e-9);
  assert.equal(speechThreshold(0.5), MAX_SPEECH_RMS);
});

test("a quiet speaker talking for 90 seconds is still heard at the end", () => {
  const nf = new NoiseFloor();
  let heard = 0, total = 0;
  for (let cycle = 0; cycle < 90; cycle++) {
    for (let i = 0; i < 10; i++) {               // 0.7 s of words, 0.3 s of pause
      const level = i < 7 ? 0.006 : 0.0008;
      if (cycle >= 60 && i < 7) { total++; heard += level > speechThreshold(nf.value); }
      nf.add(level);
    }
  }
  assert.ok(heard / total >= 0.95);
  assert.ok(speechThreshold(nf.value) <= 0.006 * 0.9);
});

test("the noise floor follows a fan up and back down", () => {
  const nf = new NoiseFloor();
  for (let i = 0; i < 400; i++) nf.add(0.0005);
  const quiet = nf.value;
  for (let i = 0; i < 400; i++) nf.add(0.01);
  const noisy = nf.value;
  for (let i = 0; i < 400; i++) nf.add(0.0005);
  assert.ok(quiet < 0.001 && noisy > 0.005 && nf.value < 0.001);
  assert.ok(DEAD_RMS < MIN_SPEECH_RMS);
});

test("PCM conversion clips and base64 round-trips", () => {
  const pcm = floatToInt16(Float32Array.from([0, 1, -1, 2, -2, 0.5]));
  assert.deepEqual([...pcm], [0, 32767, -32768, 32767, -32768, 16383]);
  const back = new Int16Array(Uint8Array.from(Buffer.from(int16ToBase64(pcm), "base64")).buffer);
  assert.deepEqual([...back], [...pcm]);
});

test("the WAV file has a valid header and the right length", async () => {
  const chunks = [new Int16Array(16000), new Int16Array(8000)];
  const blob = wavBlob(chunks);
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const text = (a, b) => String.fromCharCode(...bytes.subarray(a, b));
  assert.equal(text(0, 4), "RIFF"); assert.equal(text(8, 12), "WAVE"); assert.equal(text(36, 40), "data");
  const view = new DataView(bytes.buffer);
  assert.equal(view.getUint32(24, true), 16000);              // sample rate
  assert.equal(view.getUint32(40, true), 24000 * 2);          // data bytes
  assert.equal(bytes.length, 44 + 24000 * 2);
  assert.equal(blob.type, "audio/wav");
});
