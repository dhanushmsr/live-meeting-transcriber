// A meeting session: audio in -> live streams -> captions -> report. Port of src/inkwake/session.py. Live names for the
// other side come from the meeting page (Google Meet's tiles, see content/names.js); after the meeting the voice
// separation confirms them.

import { LiveStream } from "./live.js";
import { wavBlob } from "./audio.js";
import { applyTurns, looksLikeRealName, MAX_RECORDING_MB, MIN_RECORDING_SECONDS } from "./diarize.js";
import { stamp } from "./gemini.js";
import { ScreenNames } from "./screennames.js";

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Give each bookmark the words nearest to it, so the report says WHY the moment mattered. */
export function bookmarkContext(bookmarks, transcript, within = 30) {
  return (bookmarks || []).map((b) => {
    b = { ...b };
    if (!(b.text || "").trim() && transcript.length) {
      const near = transcript.reduce((p, c) => (Math.abs((c.t || 0) - b.t) < Math.abs((p.t || 0) - b.t) ? c : p));
      if (Math.abs((near.t || 0) - b.t) <= within) b.text = String(near.translation || near.text || "").trim().slice(0, 160);
    }
    return b;
  });
}

export class Session {
  /**
   * @param {object} o
   * @param {object} o.settings live settings object (apiKey, name, targetLanguage, translate, nameSpeakers, ...)
   * @param {import('./gemini.js').Gemini} o.gemini
   * @param {object} [o.hooks] onCaption, onState, onNotice, onMention, onBookmark, onNotes
   */
  constructor({ settings, gemini, hooks = {}, WebSocketImpl, now, notesEveryMs = 90_000 }) {
    this.settings = settings;
    this.gemini = gemini;
    this.hooks = hooks;
    this.WS = WebSocketImpl;
    this.now = now;
    this.notesEveryMs = notesEveryMs;
    this.state = "idle";
    this.title = "";
    this.platform = "";
    this.startedAt = new Date();
    this.streams = {};
    this.transcript = [];
    this.talk = {};
    this.bookmarks = [];
    this.notes = {};
    this._notesLines = 0;
    this._notesBusy = false;
    this._chunks = [];            // the other side's audio, kept only until the report is written
    this._chunkBytes = 0;
    this._recording = true;
    this.screen = new ScreenNames(() => [this.settings.name]);
  }

  // ---- control --------------------------------------------------------------
  start({ platform = "", title = "", channels = ["You", "Others"] } = {}) {
    this.platform = platform;
    this.title = title;
    this.startedAt = new Date();
    for (const label of channels) {
      this.streams[label] = new LiveStream({
        label, apiKey: this.settings.apiKey, getSettings: () => this.settings, WebSocketImpl: this.WS, now: this.now,
        onPartial: (l, text) => this.hooks.onCaption?.(this._liveName(l), text, false),
        onSegment: (line) => this._segment(line),
        onTalk: (l, minute, secs) => this._talk(l, minute, secs),
        onNotice: (m) => this.hooks.onNotice?.(m),
        record: label === "Others" && this.settings.nameSpeakers ? (pcm) => this._keep(pcm) : null,
      });
      this.streams[label].start();
    }
    this._setState("recording");
    this._notesTimer = setInterval(() => {
      if (this.state === "recording" && this.transcript.length - this._notesLines >= 4) this.refreshNotes();
    }, this.notesEveryMs);
    this._notesTimer.unref?.();
  }

  /** Audio from the capture glue: 16 kHz mono floats for "You" (microphone) or "Others" (the tab). */
  push(label, samples) {
    if (this.state !== "recording") return;        // paused: audio is dropped, the clock does not advance
    this.streams[label]?.push(samples);
  }

  pause() { if (this.state === "recording") this._setState("paused"); }
  resume() { if (this.state === "paused") this._setState("recording"); }

  _setState(s) { this.state = s; this.hooks.onState?.(s); }

  _name(speaker) { return speaker === "You" ? (this.settings.name || "You") : speaker; }

  /** The name to show right now: the profile name for "You", whoever the meeting page highlights for "Others". */
  _liveName(label) {
    return label === "Others" ? (this.screen.current(this.meetingTime()) || "Others") : this._name(label);
  }

  /** A reading of the meeting page (see content/names.js): who is highlighted, who is there, the user's own name. */
  observeScreen(reading) { this.screen.observe(this.meetingTime(), reading || {}); }

  _keep(pcm) {
    if (!this._recording) return;
    this._chunks.push(pcm.slice());
    this._chunkBytes += pcm.byteLength;
    if (this._chunkBytes > MAX_RECORDING_MB * 1e6) {   // too long to separate voices afterwards: free the memory
      this._recording = false;
      this._chunks = [];
    }
  }

  // ---- callbacks from the streams -------------------------------------------
  _segment(line) {
    const speaker = line.channel === "Others"
      ? (this.screen.nameFor(line.t, line.t_end || line.t) || "Others") : this._name(line.speaker);
    const out = { ...line, speaker, mention: false };
    const shown = (this.settings.translate ? line.translation : line.text) || line.text || line.translation;
    if (this.mentionsMe(speaker, `${line.text} ${line.translation}`)) {
      out.mention = true;
      this.hooks.onMention?.(speaker, shown);
    }
    this.transcript.push(out);
    this.hooks.onCaption?.(speaker, shown, true);
  }

  _talk(label, minute, secs) {
    const who = this._liveName(label);
    this.talk[who] ??= {};
    this.talk[who][minute] = (this.talk[who][minute] || 0) + secs;
  }

  /** True when someone else says the user's name (first name or full name). */
  mentionsMe(speaker, text) {
    const name = (this.settings.name || "").trim();
    if (!name || speaker === this._name("You")) return false;
    const first = name.split(/\s+/)[0];
    const words = new Set([name, ...(first.length >= 3 ? [first] : [])]);
    return [...words].some((w) => new RegExp(`(?<![\\p{L}\\p{N}_])${escapeRe(w)}(?![\\p{L}\\p{N}_])`, "iu").test(text));
  }

  // ---- live helpers: catch-up notes and bookmarks ---------------------------
  meetingTime() { return Math.max(0, ...Object.values(this.streams).map((s) => s.offset)); }

  transcriptText() {
    return [...this.transcript].sort((a, b) => a.t - b.t)
      .map((x) => `[${stamp(x.t)}] ${x.speaker}: ${x.text || x.translation}`).join("\n");
  }

  recentLines(n = 8) {
    return [...this.transcript].sort((a, b) => a.t - b.t).slice(-n).map((x) => ({ t: x.t, speaker: x.speaker, text: x.text || x.translation }));
  }

  bookmark() {
    const t = this.meetingTime();
    const last = this.transcript.length ? this.transcript[this.transcript.length - 1].text : "";
    this.bookmarks.push({ t, text: String(last || "").slice(0, 200) });
    this.hooks.onBookmark?.(t);
  }

  /** Show the latest lines at once (no AI needed), then refresh the AI notes in the background. */
  requestCatchUp() {
    this.hooks.onNotes?.({ ...this.notes, recent: this.recentLines(), updating: true });
    return this.refreshNotes(true);
  }

  async refreshNotes(force = false) {
    if (this._notesBusy) return;
    const n = this.transcript.length;
    if (n === 0) { this.hooks.onNotes?.({ gist: "Nothing has been said yet.", recent: [], updating: false }); return; }
    if (n === this._notesLines && this.notes.gist && !force) return;
    this._notesBusy = true;
    try {
      this.notes = await this.gemini.liveNotes(this.transcriptText(), this.notes, this.settings.name);
      this._notesLines = n;
      this.hooks.onNotes?.({ ...this.notes, recent: this.recentLines(), updating: false });
    } catch (e) {
      this.hooks.onNotice?.(`Catch-up notes failed: ${String(e.message || e).slice(0, 120)}`);
      this.hooks.onNotes?.({ ...this.notes, recent: this.recentLines(), updating: false, error: true });
    } finally {
      this._notesBusy = false;
    }
  }

  // ---- the end: separate voices, analyse, build the report ------------------
  async stop() {
    if (!["recording", "paused"].includes(this.state)) return null;
    clearInterval(this._notesTimer);
    this._setState("processing");
    await Promise.all(Object.values(this.streams).map((s) => s.stop()));
    let record = null;
    try {
      record = await this._finalize();
    } finally {
      this._chunks = [];                 // never keep other people's voices longer than needed
      this._setState("idle");
    }
    return record;
  }

  async _separateSpeakers() {
    const others = this.streams.Others;
    if (!others || !this.settings.nameSpeakers || !this._recording || !this._chunks.length) return;
    if (others.offset < MIN_RECORDING_SECONDS || !this.transcript.some((x) => x.channel === "Others")) return;
    this.hooks.onNotice?.("Working out who said what...");
    try {
      const turns = await this.gemini.diarize(wavBlob(this._chunks), [...this.screen.participants]);
      const own = new Set(["You", this._name("You")]);
      const before = new Set(this.transcript.filter((x) => x.channel === "Others").map((x) => x.speaker));
      this.talk = applyTurns(this.transcript, turns, this.talk, own, (spk, text) => this.mentionsMe(spk, text));
      const after = new Set(this.transcript.filter((x) => x.channel === "Others").map((x) => x.speaker));
      if (after.size === before.size && [...after].every((a) => before.has(a))) {
        this.hooks.onNotice?.("Could not tell the voices apart in this recording; labels were left as they were.");
      }
    } catch (e) {
      this.hooks.onNotice?.(`Could not separate speakers (${String(e.message || e).slice(0, 100)}).`);
    }
  }

  async _finalize() {
    const duration = Math.max(0, ...Object.values(this.streams).map((s) => s.offset));
    for (const x of this.transcript) {      // lines spoken just before the page's highlight was first read
      if (x.channel === "Others" && x.speaker === "Others") x.speaker = this.screen.nameFor(x.t, x.t_end || x.t, { after: true }) || "Others";
    }
    await this._separateSpeakers();
    this.transcript.sort((a, b) => a.t - b.t);
    this.bookmarks = bookmarkContext(this.bookmarks, this.transcript);
    const text = this.transcriptText();
    let analysis;
    if (text) {
      try {
        const marks = this.bookmarks.filter((b) => b.text).map((b) => `${stamp(b.t)} ${b.text.slice(0, 100)}`);
        analysis = await this.gemini.analyse(text, this.settings.name, this.title || "Meeting", marks);
      } catch (e) {
        this.hooks.onNotice?.(`Analysis failed, the report will contain the transcript only: ${String(e.message || e).slice(0, 120)}`);
        analysis = {};
      }
    } else {
      analysis = { summary: "No speech was captured in this meeting." };
    }
    const participants = [...new Set([...this.screen.participants, ...this.transcript.filter((x) => x.channel === "Others").map((x) => x.speaker)])]
      .filter((n) => n.toLowerCase() !== "others" && looksLikeRealName(n));
    return {
      title: this.title || "Meeting Report",
      started_at: this.startedAt.toISOString(),
      duration_minutes: duration / 60,
      platform: this.platform,
      host_name: this.settings.name || "",
      analysis,
      participants,
      languages: analysis.languages || [],
      bookmarks: this.bookmarks,
      transcript: this.transcript.map((x) => ({ t: x.t, speaker: x.speaker, text: x.text, translation: x.translation, mention: !!x.mention })),
      talk: this.talk,
    };
  }
}
