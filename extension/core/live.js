// Real-time speech -> text (and translation) over the Gemini Live WebSocket. Port of src/inkwake/live.py.
// Audio goes up in 100 ms frames while someone is speaking; words come back as they are recognised.

import { API_HOST, LANG_CODES, LIVE_MODEL } from "./config.js";
import { DEAD_RMS, FRAME, FRAME_SAMPLES, NoiseFloor, floatToInt16, int16ToBase64, rms, speechThreshold } from "./audio.js";

export const HANGOVER = 1.2;          // keep sending this long after the last speech so the model can close the phrase
export const PREROLL = 0.4;           // audio kept from just before speech starts
export const IDLE_FINALIZE = 1.6;     // no new words for this long -> the caption line is complete
export const MAX_LINE = 280;          // characters; split very long run-on speech into several lines
export const DEAD_MIC_SECONDS = 8.0;  // this long of pure silence from the microphone -> warn the user
export const SPEECH_LAG = 1.6;        // seconds between someone speaking and their words coming back
const QUEUE_MAX = 600;                // about 60 s of audio buffered while reconnecting
const SENTENCE_END = /[.?!。।؟]\s*$/;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const decoder = new TextDecoder();

export class LiveStream {
  /**
   * @param {object} o
   * @param {string} o.label "You" or "Others"
   * @param {string} o.apiKey
   * @param {() => {targetLanguage: string, translate: boolean}} o.getSettings read live, so toggles apply mid-meeting
   * @param {(label: string, text: string) => void} o.onPartial caption text so far
   * @param {(line: object) => void} o.onSegment a finished transcript line
   * @param {(label: string, minute: number, secs: number) => void} o.onTalk
   * @param {(msg: string) => void} o.onNotice
   * @param {(pcm: Int16Array) => void} [o.record] receives every frame (used to keep the other side's audio)
   * @param {typeof WebSocket} [o.WebSocketImpl]
   * @param {() => number} [o.now] seconds, monotonic
   */
  constructor(o) {
    this.label = o.label;
    this.apiKey = o.apiKey;
    this.getSettings = o.getSettings;
    this.onPartial = o.onPartial || (() => {});
    this.onSegment = o.onSegment || (() => {});
    this.onTalk = o.onTalk || (() => {});
    this.onNotice = o.onNotice || (() => {});
    this.record = o.record || null;
    this.WS = o.WebSocketImpl || globalThis.WebSocket;
    this.now = o.now || (() => performance.now() / 1000);
    this.url = o.url || `wss://${API_HOST}/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key=${encodeURIComponent(o.apiKey)}`;

    this.offset = 0;                 // seconds of audio received so far
    this.lastSpeech = 0;
    this._buf = new Float32Array(0);
    this._floor = new NoiseFloor();
    this._deadSecs = 0;
    this._micWarned = false;
    this._hang = 0;
    this._pre = [];
    this._queue = [];
    this._src = ""; this._out = "";
    this._lineT = 0; this._lineEnd = 0; this._lastFrag = 0;
    this._failCount = 0;
    this._stopping = false;
    this._ended = false;
    this._ws = null;
    this._wake = null;
    this._done = new Promise((r) => { this._resolveDone = r; });
  }

  // ---- lifecycle -----------------------------------------------------------
  start() {
    this._idleTimer = setInterval(() => this._idle(), 300);
    this._idleTimer.unref?.();           // never keeps a Node process alive (a no-op in browsers)
    this._loop();
  }

  /** Flush what is left, tell the server the audio is over, wait briefly for the last words, then close. */
  async stop() {
    if (this._stopping) return this._done;
    this._stopping = true;
    if (this._buf.length) {          // pad the last partial frame with silence so no audio is lost
      const pad = new Float32Array(FRAME_SAMPLES);
      pad.set(this._buf);
      this._buf = new Float32Array(0);
      this._takeFrame(pad);
    }
    this._queue.push(null);
    this._poke();
    await Promise.race([this._done, sleep(12_000)]);
    this._ended = true;
    this._closeSocket();
    return undefined;
  }

  _poke() { if (this._wake) this._wake(); }

  async _loop() {
    let stopTries = 0;
    try {
      while (!this._ended) {
        try {
          await this._session();
        } catch (e) {
          if (this._stopping && ++stopTries >= 2) break;
          this._failCount += 1;
          if (this._failCount === 3) {   // one or two quick retries are normal; only speak up when it keeps failing
            this.onNotice(`Live captions keep disconnecting (${this.label}): ${String(e.message || e).slice(0, 120)}. Still retrying...`);
          }
          await sleep(Math.min(1000 * this._failCount, 5000));
        }
      }
    } finally {
      clearInterval(this._idleTimer);
      this._finalizeLine();
      this._ended = true;
      this._resolveDone();
    }
  }

  _setup(language) {
    return {
      setup: {
        model: `models/${LIVE_MODEL}`,
        generationConfig: {
          responseModalities: ["AUDIO"],
          translationConfig: { targetLanguageCode: LANG_CODES[language] || "en", echoTargetLanguage: true },
        },
        inputAudioTranscription: {},
        outputAudioTranscription: {},
      },
    };
  }

  /** One WebSocket connection. Resolves on a clean end or a GoAway; rejects on errors so the loop reconnects. */
  _session() {
    return new Promise((resolve, reject) => {
      const language = this.getSettings().targetLanguage;
      const ws = new this.WS(this.url);
      ws.binaryType = "arraybuffer";
      this._ws = ws;
      let ready = false, finished = false, endTimer = null, pump = null;
      const finish = (err) => {
        if (finished) return;
        finished = true;
        clearInterval(pump); clearTimeout(endTimer);
        this._wake = null;
        try { ws.close(); } catch { /* already closed */ }
        err ? reject(err) : resolve();
      };
      const send = (obj) => ws.readyState === 1 && ws.send(JSON.stringify(obj));

      ws.onopen = () => send(this._setup(language));
      ws.onerror = () => finish(new Error("connection error"));
      ws.onclose = (ev) => {
        if (this._ended || this._stopping) return finish();
        finish(ready ? null : new Error(`closed before ready (${ev && ev.code})`));   // after a normal session: just reconnect
      };
      ws.onmessage = async (ev) => {
        let text = ev.data;
        if (typeof text !== "string") text = decoder.decode(text instanceof ArrayBuffer ? text : await text.arrayBuffer());
        let m;
        try { m = JSON.parse(text); } catch { return; }
        if (m.setupComplete) {
          ready = true;
          this._failCount = 0;
          pump = setInterval(() => {
            if (this.getSettings().targetLanguage !== language) return finish();   // caption language changed: reconnect
            while (this._queue.length) {
              const item = this._queue.shift();
              if (item === null) {
                send({ realtimeInput: { audioStreamEnd: true } });
                endTimer = setTimeout(() => { this._ended = true; finish(); }, 3000);   // last words still arrive
                endTimer.unref?.();
                return;
              }
              send({ realtimeInput: { audio: { data: item, mimeType: "audio/pcm;rate=16000" } } });
            }
          }, 50);
          pump.unref?.();
          return;
        }
        if (m.goAway) return finish();     // the server's session time limit is near: reconnect cleanly and silently
        const sc = m.serverContent;
        if (!sc) return;
        if (sc.inputTranscription?.text) this._fragment("src", sc.inputTranscription.text);
        if (sc.outputTranscription?.text) this._fragment("out", sc.outputTranscription.text);
      };
    });
  }

  _closeSocket() {
    try { this._ws && this._ws.close(); } catch { /* ignore */ }
  }

  // ---- audio in ---------------------------------------------------------
  /** Feed 16 kHz mono float samples (any block size). */
  push(samples) {
    if (this._stopping || !samples.length) return;
    const joined = new Float32Array(this._buf.length + samples.length);
    joined.set(this._buf); joined.set(samples, this._buf.length);
    let i = 0;
    for (; i + FRAME_SAMPLES <= joined.length; i += FRAME_SAMPLES) this._takeFrame(joined.subarray(i, i + FRAME_SAMPLES));
    this._buf = joined.slice(i);
  }

  _takeFrame(frame) {
    const level = rms(frame);
    const speech = level > speechThreshold(this._floor.value);
    this._floor.add(level);                       // learned from the quiet end of the last 30 s, so speech can't raise the bar
    if (this.label === "You") this._watchMic(level);
    const minute = Math.floor(this.offset / 60);
    if (speech) { this.lastSpeech = this.now(); this.onTalk(this.label, minute, FRAME); }
    this.offset += FRAME;
    const pcm = floatToInt16(frame);
    if (this.record) this.record(pcm);            // every sample, silence included, so timestamps match the transcript
    const b64 = int16ToBase64(pcm);
    if (speech) {
      this._hang = HANGOVER;
      while (this._pre.length) this._enqueue(this._pre.shift());
      this._enqueue(b64);
    } else if (this._hang > 0) {                  // trailing silence so the model sees the phrase end
      this._hang -= FRAME;
      this._enqueue(b64);
    } else {
      this._pre.push(b64);
      while (this._pre.length * FRAME > PREROLL) this._pre.shift();
    }
    this._poke();
  }

  _enqueue(item) {
    if (this._queue.length >= QUEUE_MAX) this._queue.shift();   // reconnecting for a long time: drop the oldest audio
    this._queue.push(item);
  }

  /** Tell the user when their microphone delivers pure silence (hardware mute key, blocked, wrong device). */
  _watchMic(level) {
    if (this._micWarned) return;
    if (level < DEAD_RMS) {
      this._deadSecs += FRAME;
      if (this._deadSecs >= DEAD_MIC_SECONDS) {
        this._micWarned = true;
        this.onNotice("Ink can't hear your microphone (it is sending silence). Check the laptop's mic-mute key, your browser's " +
          "microphone permission, other apps using the mic, or pick another microphone in Ink's settings.");
      }
    } else this._deadSecs = 0;
  }

  // ---- text out ---------------------------------------------------------
  _fragment(kind, text) {
    if (!(this._src || this._out)) this._lineT = Math.max(0, this.offset - 1.5);
    if (kind === "src") this._src += text; else this._out += text;
    this._lastFrag = this.now();
    this._lineEnd = Math.max(this._lineT, this.offset - SPEECH_LAG);
    const s = this.getSettings();
    const shown = (s.translate ? this._out : this._src).trim();
    if (shown) this.onPartial(this.label, shown);
    if (this._src.length > MAX_LINE && SENTENCE_END.test(this._src)) this._finalizeLine();
  }

  _idle() {
    if ((this._src || this._out) && this.now() - this._lastFrag > IDLE_FINALIZE) this._finalizeLine();
  }

  _finalizeLine() {
    const src = this._src.trim(), out = this._out.trim();
    this._src = ""; this._out = "";
    if (!(src || out)) return;
    this.onSegment({ speaker: this.label, t: this._lineT, t_end: this._lineEnd, text: src, translation: out, channel: this.label });
  }
}
