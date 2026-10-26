// Audio helpers: pure functions, no browser APIs, so they are unit-tested in Node.

export const SR = 16000;              // Gemini wants 16 kHz mono PCM
export const FRAME = 0.1;             // seconds per analysis frame
export const FRAME_SAMPLES = 1600;
export const MIN_SPEECH_RMS = 0.004;  // quietest level ever treated as speech (laptop mics are often quiet)
export const MAX_SPEECH_RMS = 0.02;   // loudest threshold allowed in a noisy room
export const DEAD_RMS = 0.0003;       // below this a microphone is delivering digital silence

/** Streaming linear resampler: feed it blocks at `inRate`, get 16 kHz blocks back with no seams between them. */
export class Resampler {
  constructor(inRate, outRate = SR) {
    this.ratio = inRate / outRate;
    this.pos = 0;           // read position, relative to `carry`
    this.carry = null;      // last sample of the previous block
  }

  process(input) {
    if (this.ratio === 1) return Float32Array.from(input);
    if (!input.length) return new Float32Array(0);
    if (this.carry === null) this.carry = input[0];
    const n = input.length + 1;
    const at = (i) => (i === 0 ? this.carry : input[i - 1]);
    const out = [];
    let p = this.pos;
    while (Math.floor(p) + 1 < n) {
      const i = Math.floor(p), f = p - i;
      out.push(at(i) * (1 - f) + at(i + 1) * f);
      p += this.ratio;
    }
    this.pos = p - (n - 1);
    this.carry = input[input.length - 1];
    return Float32Array.from(out);
  }
}

export function rms(x) {
  if (!x.length) return 0;
  let s = 0;
  for (let i = 0; i < x.length; i++) s += x[i] * x[i];
  return Math.sqrt(s / x.length);
}

/** Speech must be clearly above the room's own noise: 3x the floor, kept within sensible limits. */
export function speechThreshold(noiseFloor) {
  return Math.min(MAX_SPEECH_RMS, Math.max(MIN_SPEECH_RMS, 3 * noiseFloor));
}

/** The noise floor is the quiet end (10th percentile) of the last 30 s of frame levels. */
export class NoiseFloor {
  constructor(frames = 300) {
    this.max = frames;
    this.hist = [];
    this.value = 0.001;
  }

  add(level) {
    this.hist.push(level);
    if (this.hist.length > this.max) this.hist.shift();
    if (this.hist.length >= 10) {
      const sorted = [...this.hist].sort((a, b) => a - b);
      this.value = sorted[Math.floor((sorted.length - 1) * 0.1)];
    }
    return this.value;
  }
}

export function floatToInt16(f32) {
  const out = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    const v = Math.max(-1, Math.min(1, f32[i]));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return out;
}

export function int16ToBase64(i16) {
  const bytes = new Uint8Array(i16.buffer, i16.byteOffset, i16.byteLength);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/** A WAV file (16 kHz mono 16-bit) from PCM chunks, as a Blob so long meetings are never copied into one big buffer. */
export function wavBlob(chunks, rate = SR) {
  const dataBytes = chunks.reduce((n, c) => n + c.byteLength, 0);
  const header = new DataView(new ArrayBuffer(44));
  const tag = (o, s) => [...s].forEach((ch, i) => header.setUint8(o + i, ch.charCodeAt(0)));
  tag(0, "RIFF"); header.setUint32(4, 36 + dataBytes, true); tag(8, "WAVE"); tag(12, "fmt ");
  header.setUint32(16, 16, true); header.setUint16(20, 1, true); header.setUint16(22, 1, true);
  header.setUint32(24, rate, true); header.setUint32(28, rate * 2, true); header.setUint16(32, 2, true);
  header.setUint16(34, 16, true); tag(36, "data"); header.setUint32(40, dataBytes, true);
  return new Blob([header.buffer, ...chunks], { type: "audio/wav" });
}
