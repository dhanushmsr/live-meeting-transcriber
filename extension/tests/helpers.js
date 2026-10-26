// Test helpers: a fake Gemini Live server and audio generators.

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function tone(level, secs = 0.1, rate = 16000) {
  const n = Math.round(rate * secs);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = level * Math.SQRT2 * Math.sin((2 * Math.PI * 220 * i) / rate);   // RMS == level
  return out;
}

/** A WebSocket stand-in that behaves like Gemini Live: it answers the setup message with setupComplete. */
export class FakeWS {
  static instances = [];
  static failFirst = 0;           // make the first N connections fail before they are ready
  static reset() { FakeWS.instances = []; FakeWS.failFirst = 0; }

  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    this.binaryType = "blob";
    FakeWS.instances.push(this);
    const failing = FakeWS.instances.length <= FakeWS.failFirst;
    setTimeout(() => {
      if (failing) { this.readyState = 3; this.onerror?.({}); this.onclose?.({ code: 1006 }); return; }
      this.readyState = 1;
      this.onopen?.();
    }, 1);
  }

  send(text) {
    const msg = JSON.parse(text);
    this.sent.push(msg);
    if (msg.setup) setTimeout(() => this.reply({ setupComplete: {} }), 1);
  }

  /** Deliver a server message the way the real server does: JSON inside a binary frame. */
  reply(obj) {
    const buf = new TextEncoder().encode(JSON.stringify(obj));
    this.onmessage?.({ data: buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) });
  }

  close() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.onclose?.({ code: 1000 });
  }

  audioFrames() { return this.sent.filter((m) => m.realtimeInput?.audio).length; }
  endedAudio() { return this.sent.some((m) => m.realtimeInput?.audioStreamEnd); }
}

export const SETTINGS = { targetLanguage: "English", translate: true, name: "Dhanush M", nameSpeakers: true, apiKey: "test-key" };
