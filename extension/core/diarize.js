// Who said what, worked out AFTER the meeting from the recording of the other participants.
// Port of src/inkwake/diarize.py. Pure logic, no network. Line objects keep the app's snake_case keys on purpose.

export const MIN_RECORDING_SECONDS = 15;
export const MAX_RECORDING_MB = 150;       // about 80 minutes of 16 kHz mono; longer meetings skip this step
const GENERIC = "others";
const MIN_PART_SECONDS = 1.0;              // a stretch shorter than this is not worth its own transcript line
const SAME_VOICE_GAP = 3.0;                // a short pause between two stretches by the same voice stays one line
const SENTENCE_END = [".", "?", "!", "。", "।", "؟"];

export function cleanTurns(raw) {
  const turns = [];
  for (const t of Array.isArray(raw) ? raw : []) {
    if (!t || typeof t !== "object") continue;
    const start = Number(t.start), end = Number(t.end);
    const label = String(t.speaker || "").trim();
    if (label && Number.isFinite(start) && Number.isFinite(end) && end > start && start >= 0) {
      turns.push({ start, end, speaker: label, starts: String(t.starts || "").trim() });
    }
  }
  return turns.sort((a, b) => a.start - b.start);
}

const overlap = (a0, a1, b0, b1) => Math.max(0, Math.min(a1, b1) - Math.max(a0, b0));

/** Start/end of transcript line i. Uses the real end the live stream recorded, else a speaking-rate guess. */
export function lineWindow(lines, i) {
  const t = Number(lines[i].t || 0);
  const end = Number(lines[i].t_end || 0);
  if (end > t) return [t, end];
  const words = String(lines[i].text || lines[i].translation || "").split(/\s+/).filter(Boolean).length;
  const guess = t + Math.max(2.0, words * 0.45);
  const nxt = i + 1 < lines.length ? Number(lines[i + 1].t) : guess;
  return [t, nxt > t ? Math.min(guess, nxt) : guess];
}

/** The window as consecutive [speaker, start, end, firstWords] stretches (same voice with a short gap is merged). */
export function pieces(window, turns) {
  const segs = [];
  for (const tr of turns) {
    const a = Math.max(window[0], tr.start), b = Math.min(window[1], tr.end);
    if (b - a > 0.05) segs.push([tr.speaker, a, b, tr.starts || ""]);
  }
  const merged = [];
  for (const [sp, a, b, st] of segs) {
    const last = merged[merged.length - 1];
    if (last && last[0] === sp && a - last[2] <= SAME_VOICE_GAP) last[2] = b;
    else merged.push([sp, a, b, st]);
  }
  return merged;
}

const normWords = (text) => String(text || "").toLowerCase().replace(/[^\p{L}\p{N}_\s]/gu, " ").split(/\s+/).filter(Boolean);

/** Index in `words` where `phrase` (the first words of a turn) begins, searching near `ideal`; null if not found. */
export function findPhrase(words, phrase, ideal, lo, reach) {
  const ph = normWords(phrase).slice(0, 5);
  if (ph.length < 2) return null;
  const norm = words.map((w) => normWords(w)[0] || "");
  let best = null, bestScore = 0;
  const from = Math.max(lo, ideal - 2 * reach), to = Math.min(words.length, ideal + 2 * reach);
  for (let i = from; i <= to; i++) {
    const window = norm.slice(i, i + ph.length);
    const score = window.reduce((n, w, k) => n + (w === ph[k] ? 1 : 0), 0) / ph.length;
    if (score > bestScore || (score === bestScore && best !== null && Math.abs(i - ideal) < Math.abs(best - ideal))) {
      best = i; bestScore = score;
    }
  }
  return bestScore >= 0.6 ? best : null;
}

/** Cut a word list into one part per speaker: where that speaker's first words appear, else at the sentence end
 *  nearest to where the timing says, else where the timing says. */
export function sliceWords(words, fractions, hints = null) {
  const ends = words.map((w, i) => (SENTENCE_END.some((e) => w.endsWith(e)) ? i + 1 : 0)).filter(Boolean);
  const reach = Math.max(4, Math.floor(0.2 * words.length));
  const cuts = [];
  let acc = 0, prev = 0;
  for (let k = 0; k < fractions.length - 1; k++) {
    acc += fractions[k];
    const ideal = Math.round(words.length * acc);
    const hint = hints ? hints[k + 1] : "";
    let cut = hint ? findPhrase(words, hint, ideal, prev + 1, reach) : null;
    if (cut === null) {
      const near = ends.filter((e) => Math.abs(e - ideal) <= reach && e > prev);
      cut = near.length ? near.reduce((a, b) => (Math.abs(b - ideal) < Math.abs(a - ideal) ? b : a)) : Math.max(ideal, prev);
    }
    cuts.push(cut);
    prev = cut;
  }
  const parts = [];
  let start = 0;
  for (const c of [...cuts, words.length]) {
    parts.push(words.slice(start, c).join(" "));
    start = c;
  }
  return parts;
}

/** If two or more voices spoke inside one transcript line, cut it into one line per voice. */
export function splitLine(x, window, turns) {
  const parts = pieces(window, turns).filter((p) => p[2] - p[1] >= MIN_PART_SECONDS);
  if (new Set(parts.map((p) => p[0])).size < 2) return null;
  const words = String(x.text || "").split(/\s+/).filter(Boolean);
  const trWords = String(x.translation || "").split(/\s+/).filter(Boolean);
  if ((words.length ? words : trWords).length < parts.length) return null;
  const total = parts.reduce((n, p) => n + (p[2] - p[1]), 0);
  const fractions = parts.map((p) => (p[2] - p[1]) / total);
  const texts = sliceWords(words, fractions, parts.map((p) => p[3]));
  const trans = trWords.length ? sliceWords(trWords, fractions) : parts.map(() => "");
  const out = [];
  parts.forEach(([speaker, start, end], i) => {
    if (!(texts[i] || trans[i])) return;
    out.push({ ...x, t: start, t_end: end, text: texts[i], translation: trans[i], diar: speaker, screen: null, mention: false });
  });
  return out.length ? out : null;
}

/** Speaker with the most overlap, or the nearest turn within `slack` seconds. */
export function labelFor(window, turns, slack = 3.0) {
  let best = null, bestOv = 0;
  for (const tr of turns) {
    const ov = overlap(window[0], window[1], tr.start, tr.end);
    if (ov > bestOv) { best = tr.speaker; bestOv = ov; }
  }
  if (best) return best;
  const mid = (window[0] + window[1]) / 2;
  const dist = (tr) => (tr.start <= mid && mid <= tr.end ? 0 : Math.min(Math.abs(mid - tr.start), Math.abs(mid - tr.end)));
  let near = null;
  for (const tr of turns) if (near === null || dist(tr) < dist(near)) near = tr;
  return near && near.start - slack <= mid && mid <= near.end + slack ? near.speaker : null;
}

/** Turn 'Speaker 2' into a real name when the screen agreed on that name for most of that voice's lines. */
export function nameMap(lines, minShare = 0.6, minLines = 2) {
  const votes = {};
  for (const x of lines) {
    if (x.diar && x.screen && x.screen.toLowerCase() !== GENERIC) {
      votes[x.diar] ??= {};
      votes[x.diar][x.screen] = (votes[x.diar][x.screen] || 0) + 1;
    }
  }
  const out = {};
  for (const [label, names] of Object.entries(votes)) {
    const total = Object.values(names).reduce((a, b) => a + b, 0);
    const [name, n] = Object.entries(names).reduce((a, b) => (b[1] > a[1] ? b : a));
    if (n >= minLines && n / total >= minShare) out[label] = name;
  }
  return out;
}

/** Name the 'Others' lines and rebuild talk time from the turns. `transcript` is updated in place; the new talk object
 *  ({speaker: {minute: seconds}}) is returned, with the host's own entries kept as they were. */
export function applyTurns(transcript, rawTurns, talk, ownNames, mentionFn = null) {
  const turns = cleanTurns(rawTurns);
  const others = transcript.filter((x) => x.channel === "Others").sort((a, b) => (a.t || 0) - (b.t || 0));
  if (!turns.length || !others.length) return talk;

  const rebuilt = [];
  others.forEach((x, i) => {
    const window = lineWindow(others, i);
    const parts = splitLine(x, window, turns);
    if (parts) rebuilt.push(...parts);
    else { x.screen = x.speaker; x.diar = labelFor(window, turns); rebuilt.push(x); }
  });

  const names = nameMap(rebuilt);
  for (const x of rebuilt) {
    if (x.diar) x.speaker = names[x.diar] || x.diar;
    if (mentionFn && x.diar) x.mention = !!mentionFn(x.speaker, `${x.text || ""} ${x.translation || ""}`);
  }
  const rest = transcript.filter((x) => x.channel !== "Others");
  transcript.length = 0;
  transcript.push(...rest, ...rebuilt);

  const own = new Set([...ownNames].filter(Boolean).map((n) => n.toLowerCase()));
  const newTalk = {};
  for (const [k, v] of Object.entries(talk || {})) if (own.has(k.toLowerCase())) newTalk[k] = { ...v };
  for (const tr of turns) {
    const who = names[tr.speaker] || tr.speaker;
    let t = tr.start;
    while (t < tr.end - 1e-6) {
      const minute = Math.floor(t / 60);
      const step = Math.min(tr.end, (minute + 1) * 60) - t;
      newTalk[who] ??= {};
      newTalk[who][minute] = (newTalk[who][minute] || 0) + step;
      t += step;
    }
  }
  return newTalk;
}

export const looksLikeRealName = (label) => !/^(speaker|voice|person)\s*\d+$/i.test(String(label).trim());
