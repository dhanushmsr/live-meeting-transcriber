// The audio engine: captures the microphone ("You") and, in Chromium browsers, the meeting tab's sound ("Others"),
// and feeds a Session. It runs in Chrome's offscreen document, or in Firefox's background page.
// This file needs real browser audio APIs, so it is the one part that cannot be unit-tested in Node.

import { Resampler, SR } from "./core/audio.js";
import { Gemini } from "./core/gemini.js";
import { Session } from "./core/session.js";

export class Engine {
  /**
   * @param {object} o
   * @param {() => Promise<object>} o.loadSettings
   * @param {(event: object) => void} o.emit sends events (caption, state, notice, mention, bookmark, notes, finished) to the UI
   */
  constructor({ loadSettings, emit }) {
    this.loadSettings = loadSettings;
    this.emit = emit;
    this.session = null;
    this.ctx = null;
    this.streams = [];
  }

  get active() { return !!this.session && this.session.state !== "idle"; }

  /** @param {{tabStreamId?: string, platform?: string, title?: string}} opts tabStreamId is only available in Chromium */
  async start({ tabStreamId = null, platform = "", title = "" } = {}) {
    if (this.active) throw new Error("Ink is already running.");
    const settings = await this.loadSettings();
    if (!settings.apiKey) throw new Error("Add your Gemini API key in Ink's settings first.");

    this.session = new Session({
      settings,
      gemini: new Gemini(settings.apiKey),
      hooks: {
        onCaption: (speaker, text, final) => this.emit({ kind: "caption", speaker, text, final }),
        onState: (state) => this.emit({ kind: "state", state }),
        onNotice: (message) => this.emit({ kind: "notice", message }),
        onMention: (speaker, line) => this.emit({ kind: "mention", speaker, line }),
        onBookmark: (t) => this.emit({ kind: "bookmark", t }),
        onNotes: (notes) => this.emit({ kind: "notes", notes }),
      },
    });

    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule(new URL("./capture-worklet.js", import.meta.url));
    await this.ctx.resume();

    const channels = [];
    try {
      const mic = await navigator.mediaDevices.getUserMedia({
        audio: { deviceId: settings.micDeviceId ? { exact: settings.micDeviceId } : undefined, echoCancellation: true, noiseSuppression: false, autoGainControl: true },
      });
      this._wire("You", mic, false);
      channels.push("You");
    } catch (e) {
      this.emit({ kind: "notice", message: "Ink could not open your microphone. Open Ink's settings and click \"Allow microphone\"." });
    }
    if (tabStreamId) {
      const tab = await navigator.mediaDevices.getUserMedia({
        audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: tabStreamId } }, video: false,
      });
      this._wire("Others", tab, true);          // capturing a tab silences it, so it is played back to the user as well
      channels.push("Others");
    }
    if (!channels.length) { await this._teardown(); throw new Error("No audio source could be opened."); }
    this.session.start({ platform, title, channels });
  }

  _wire(label, stream, playback) {
    this.streams.push(stream);
    const source = this.ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(this.ctx, "ink-capture");
    const resampler = new Resampler(this.ctx.sampleRate, SR);
    node.port.onmessage = (e) => this.session?.push(label, resampler.process(e.data));
    const mute = this.ctx.createGain();         // keeps the node pulled by the audio graph without making any sound
    mute.gain.value = 0;
    source.connect(node);
    node.connect(mute).connect(this.ctx.destination);
    if (playback) source.connect(this.ctx.destination);
  }

  /** Settings changed while a meeting is running (caption language, translation on/off): the session reads them live. */
  updateSettings(s) { if (this.session) Object.assign(this.session.settings, s); }

  pause() { this.session?.pause(); }
  resume() { this.session?.resume(); }
  bookmark() { this.session?.bookmark(); }
  catchUp() { return this.session?.requestCatchUp(); }

  /** Ends the meeting: separate voices, analyse, build the report. Returns the report record. */
  async stop() {
    if (!this.session) return null;
    const session = this.session;
    this.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));   // release the microphone straight away
    let record = null;
    try {
      record = await session.stop();
    } finally {
      await this._teardown();
    }
    return record;
  }

  async _teardown() {
    this.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
    this.streams = [];
    try { await this.ctx?.close(); } catch { /* already closed */ }
    this.ctx = null;
    this.session = null;
  }
}
