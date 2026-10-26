// Gemini REST client. Same prompts as src/inkwake/gemini.py. Works in the browser (extension pages with host
// permission) and in Node 20+ (tests), because it only uses fetch.

import { API_HOST, FALLBACK_MODEL, REPORT_MODEL } from "./config.js";

const BASE = `https://${API_HOST}`;
const MAX_TRANSCRIPT_CHARS = 400_000;
const LIVE_TAIL_CHARS = 14_000;

export const stamp = (t) => {
  t = Math.floor(Number(t) || 0);
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function parseJson(text) {
  text = (text || "").trim();
  if (text.startsWith("```")) {
    text = text.replace(/^```(?:json)?/i, "").replace(/```$/, "").trim();
  }
  return JSON.parse(text);
}

/** Voices are identified first and named once; every turn of a voice gets that voice's name (or its voice id). */
export function turnsWithNames(data) {
  if (Array.isArray(data)) return data;
  if (!data || typeof data !== "object") return [];
  const voices = data.voices && typeof data.voices === "object" ? data.voices : {};
  const out = [];
  for (const t of data.turns || []) {
    if (!t || typeof t !== "object") continue;
    const vid = String(t.voice || t.speaker || "").trim();
    const name = String(voices[vid] || "").trim();
    const label = name || (/^voice\s*\d+/i.test(vid) ? vid.replace(/^voice\s*/i, "Speaker ") : vid);
    out.push({ start: t.start, end: t.end, speaker: label, starts: String(t.starts || "") });
  }
  return out;
}

export class Gemini {
  constructor(apiKey, { reportModel = REPORT_MODEL, fallbackModel = FALLBACK_MODEL, fetchImpl } = {}) {
    this.key = apiKey;
    this.reportModel = reportModel;
    this.fallbackModel = fallbackModel;
    this.fetch = fetchImpl || ((...a) => globalThis.fetch(...a));
  }

  headers(extra = {}) {
    return { "x-goog-api-key": this.key, ...extra };
  }

  /** One generateContent call with retries on "busy" errors and a hard time limit. Returns the text. */
  async generate(parts, { model, json = false, timeoutMs = 60_000, attempts = 4 } = {}) {
    const body = {
      contents: [{ role: "user", parts: typeof parts === "string" ? [{ text: parts }] : parts }],
      generationConfig: { temperature: 0.2, ...(json ? { responseMimeType: "application/json" } : {}) },
    };
    let lastErr;
    for (let i = 0; i < attempts; i++) {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), timeoutMs);
      try {
        const r = await this.fetch(`${BASE}/v1beta/models/${model}:generateContent`, {
          method: "POST", headers: this.headers({ "Content-Type": "application/json" }), body: JSON.stringify(body), signal: ctl.signal,
        });
        if (!r.ok) {
          const detail = (await r.text().catch(() => "")).slice(0, 200);
          throw new ApiError(r.status, `Gemini ${r.status}: ${detail}`);
        }
        const data = await r.json();
        return (data.candidates?.[0]?.content?.parts || []).map((p) => p.text || "").join("");
      } catch (e) {
        lastErr = e.name === "AbortError" ? new ApiError(0, "Gemini took too long to answer") : e;
        const retryable = !(lastErr instanceof ApiError) || [0, 429, 500, 503].includes(lastErr.status);
        if (!retryable || i === attempts - 1) throw lastErr;
        await sleep(2 ** i * 1000);
      } finally {
        clearTimeout(timer);
      }
    }
    throw lastErr;
  }

  /** Try the stronger model, fall back to the lighter one when it is rate-limited or down. */
  async withFallback(parts, { json = false, timeoutMs = 60_000 } = {}) {
    let text;
    try {
      text = await this.generate(parts, { model: this.reportModel, json, timeoutMs });
    } catch (e) {
      if (!(e instanceof ApiError)) throw e;
      text = await this.generate(parts, { model: this.fallbackModel, json, timeoutMs });
    }
    return json ? parseJson(text) : text;
  }

  // ---- after the meeting ------------------------------------------------
  async analyse(transcript, host, title, bookmarks = []) {
    const marks = bookmarks.length
      ? `\nThe host bookmarked these moments, so make sure what was said there is covered (never mention the bookmarks themselves): ${bookmarks.join("; ")}.`
      : "";
    const prompt =
      `You write meeting briefs for busy people${host ? ` (the reader is ${host})` : ""}. The transcript may be in several languages and ` +
      "may be messy speech-to-text; write everything in plain English. Be faithful to the transcript and never invent facts, " +
      "names, dates or owners. Write for someone who has 30 seconds: short sentences, no filler, no corporate jargon, and NEVER " +
      "say the same fact in more than one section. If a section has nothing real to say, return an empty list or an empty string for it. " +
      'Return JSON only: {"title": "<what the meeting was about, max 7 words>", ' +
      '"tldr": "<ONE plain sentence, max 25 words: the single most important outcome>", ' +
      '"summary": "<2-3 short sentences of context that the other sections do not already give>", ' +
      '"decisions": [<things that were decided, max 6, one short line each>], ' +
      '"action_items": [{"task": "<something a person must do, starting with a verb>", "owner": "<name or empty>", "due": "<deadline if one was said, else empty>"}], ' +
      '"takeaways": [<max 5 short points worth remembering that are NOT decisions or action items>], ' +
      '"open_questions": [<questions raised but left unanswered>], ' +
      '"chapters": [{"start": "mm:ss", "title": "<max 6 words>", "summary": "<max 12 words>"}], ' +
      '"follow_up_email": {"subject": "string", "body": "<plain-text email from the host to the participants, under 120 words: one line of thanks, ' +
      "the decisions, the action items with owners and dates, next step; sign off with the host's name if known, no placeholders>\"}, " +
      '"languages": ["string"], "sentiment": "<one word>"}. ' +
      "Chapters are the main stretches of the meeting in order (at most 6) and use the transcript timestamps. " +
      `${marks} Meeting: ${title || "unknown"}.\n\nTranscript:\n${transcript.slice(0, MAX_TRANSCRIPT_CHARS)}`;
    const data = await this.withFallback(prompt, { json: true, timeoutMs: 120_000 });
    return data && typeof data === "object" && !Array.isArray(data) ? data : {};
  }

  // ---- during the meeting -----------------------------------------------
  async liveNotes(transcript, previous, host) {
    const prev = previous?.gist ? `\nNotes from earlier in the meeting (keep what is still true): ${JSON.stringify(previous).slice(0, 3000)}` : "";
    const prompt =
      `A meeting is in progress. Write a quick catch-up for a participant${host ? ` (${host})` : ""} who looked away. English only; ` +
      "faithful to the transcript, no inventions. Return JSON only: " +
      '{"gist": "<2-4 sentences: what has been discussed so far>", "now": "<one line: what is being discussed right now>", ' +
      '"decisions": ["string"], "action_items": [{"task": "string", "owner": "string"}], "open_questions": ["string"]}.' +
      `${prev}\n\nLatest transcript:\n${transcript.slice(-LIVE_TAIL_CHARS)}`;
    const text = await this.generate(prompt, { model: this.fallbackModel, json: true, timeoutMs: 30_000, attempts: 2 });
    const data = parseJson(text);
    return data && typeof data === "object" && !Array.isArray(data) ? data : {};
  }

  async ask(question, transcript, analysis) {
    const prompt =
      "You answer questions about one meeting using ONLY its transcript and summary below. If the meeting does not say, answer that " +
      "it was not discussed. Be concise, name who said what, and cite timestamps like [12:30]. Reply in the language of the question.\n\n" +
      `Summary: ${JSON.stringify(analysis || {}).slice(0, 6000)}\n\nTranscript:\n${transcript.slice(0, MAX_TRANSCRIPT_CHARS)}\n\nQuestion: ${question}`;
    return ((await this.withFallback(prompt, { json: false, timeoutMs: 90_000 })) || "").trim();
  }

  // ---- who spoke when (after the meeting) -------------------------------
  /** Upload with Google's resumable protocol; returns the file record {name, uri, mimeType, state}. */
  async uploadFile(blob, mimeType, displayName = "meeting-audio") {
    const start = await this.fetch(`${BASE}/upload/v1beta/files`, {
      method: "POST",
      headers: this.headers({
        "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": String(blob.size), "X-Goog-Upload-Header-Content-Type": mimeType,
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({ file: { display_name: displayName } }),
    });
    if (!start.ok) throw new ApiError(start.status, `Upload could not start (${start.status})`);
    const url = start.headers.get("x-goog-upload-url");
    if (!url) throw new ApiError(0, "Upload address missing in Google's answer");
    const up = await this.fetch(url, {
      method: "POST",
      headers: { "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize" },
      body: blob,
    });
    if (!up.ok) throw new ApiError(up.status, `Upload failed (${up.status})`);
    return (await up.json()).file;
  }

  async deleteFile(name) {
    try {
      await this.fetch(`${BASE}/v1beta/${name}`, { method: "DELETE", headers: this.headers() });
    } catch { /* best effort */ }
  }

  /** Listen to the recording of the OTHER participants and list who speaks when (seconds from its start). */
  async diarize(blob, names = []) {
    const hint = names.length
      ? ` People seen on the meeting screen (a hint only, never assume a particular one is speaking): ${names.slice(0, 12).join(", ")}.` : "";
    const prompt =
      "This is the recording of the OTHER people in an online meeting; the person who recorded it is not in this audio. Work out who " +
      'speaks when. Do it in two steps. STEP 1: tell the different VOICES apart and call them "voice 1", "voice 2", ... giving each ' +
      "voice the same id for the whole recording. STEP 2: for each voice, give a real name ONLY if that voice itself says its name " +
      "('I'm Paul', 'this is Anita') or it is addressed by name and THEN replies (the name belongs to the person who replies, never to " +
      "the person who is speaking to them; 'Paul, can you...' is spoken BY someone who is not Paul). Otherwise leave the name empty. " +
      'Never guess. Return JSON only: {"voices": {"voice 1": "<real name or empty>", "voice 2": "<real name or empty>"}, ' +
      '"turns": [{"start": <seconds>, "end": <seconds>, "voice": "voice 1", "starts": "<the first 4-6 words of that turn, exactly as spoken>"}]}. ' +
      "List every stretch of speech in order, merging one voice's consecutive sentences into a single turn; ignore silence and noise." +
      `${hint} Timestamps are seconds from the start of the recording.`;
    const file = await this.uploadFile(blob, "audio/wav");
    try {
      let f = file;
      for (let i = 0; i < 90 && f.state && f.state !== "ACTIVE"; i++) {
        if (f.state === "FAILED") throw new ApiError(0, "Google could not process the recording");
        await sleep(2000);
        f = await (await this.fetch(`${BASE}/v1beta/${file.name}`, { headers: this.headers() })).json();
      }
      const data = await this.withFallback(
        [{ fileData: { mimeType: "audio/wav", fileUri: f.uri } }, { text: prompt }], { json: true, timeoutMs: 240_000 });
      return turnsWithNames(data);
    } finally {
      await this.deleteFile(file.name);   // the recording does not stay on Google's side
    }
  }
}
