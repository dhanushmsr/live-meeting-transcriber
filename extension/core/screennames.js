// Who is speaking, according to the meeting page itself. Port of the timeline in src/inkwake/speakers.py, but fed by
// the page (content/names.js reads Google Meet's participant tiles) instead of screenshots, so it costs no API calls.
// Times are meeting seconds (the session clock), so they line up with the transcript.

export const VALID_FOR = 8.0;   // an observation still counts this long (a speaker rarely changes faster)
export const NEAR = 6.0;        // after the meeting, a line may borrow a name seen this close before or after it
const LEAD = 0.5;               // the page's speaking highlight trails the voice slightly

const clean = (n) => String(n || "").replace(/\s+/g, " ").trim();

export class ScreenNames {
  /** @param {() => string[]} ownNames the user's own names (profile name, Meet's self name), never a remote speaker */
  constructor(ownNames = () => []) {
    this.ownNames = ownNames;
    this.timeline = [];           // [meeting seconds, name or null], ordered by time
    this.participants = new Set();
    this.selfName = "";
  }

  isOwn(name) {
    const n = clean(name).toLowerCase();
    if (!n) return true;
    const own = new Set(["you", "me", ...[...this.ownNames(), this.selfName].map((x) => clean(x).toLowerCase())]);
    own.delete("");
    return own.has(n) || [...own].some((m) => m.length > 3 && n.includes(m));
  }

  /** One reading of the page: who is highlighted right now (null = nobody), everyone on screen, and the user's own name. */
  observe(t, { speaker = null, participants = [], self = "" } = {}) {
    if (clean(self)) this.selfName = clean(self);
    for (const p of participants) if (!this.isOwn(p)) this.participants.add(clean(p));
    const name = speaker && !this.isOwn(speaker) ? clean(speaker) : null;
    if (name) this.participants.add(name);
    const last = this.timeline[this.timeline.length - 1];
    if (last && last[1] === name) return;
    if (last && t < last[0]) {           // never out of order
      this.timeline.push([t, name]);
      this.timeline.sort((a, b) => a[0] - b[0]);
    } else {
      this.timeline.push([t, name]);
    }
    if (this.timeline.length > 5000) this.timeline.splice(0, this.timeline.length - 5000);
  }

  /** If the page shows exactly one other person, everything the other side says is theirs. */
  soleRemote() {
    const others = [...this.participants].filter((n) => !this.isOwn(n));
    return others.length === 1 ? others[0] : null;
  }

  /** Name for speech between t0 and t1 (null if unknown). `after` lets a line borrow a name seen just after it,
   *  which is only possible once the meeting is over. */
  nameFor(t0, t1 = t0, { after = false } = {}) {
    const from = t0 - LEAD;
    const share = {};
    this.timeline.forEach(([t, name], i) => {
      if (!name) return;
      const end = i + 1 < this.timeline.length ? this.timeline[i + 1][0] : Math.max(t1, t);
      const ov = Math.min(end, t1) - Math.max(t, from);
      if (ov > 0 || (t >= from && t <= t1)) share[name] = (share[name] || 0) + Math.max(ov, 0.01);
    });
    const top = Object.entries(share).sort((a, b) => b[1] - a[1])[0];
    if (top) return top[0];
    let before = null;
    for (const [t, name] of this.timeline) { if (t > t0) break; if (name) before = [t, name]; }
    if (before && t0 - before[0] <= VALID_FOR) return before[1];
    if (after) {
      const next = this.timeline.find(([t, name]) => name && t >= t1 && t - t1 <= NEAR);
      if (next) return next[1];
    }
    return this.soleRemote();
  }

  /** Who is speaking at meeting time t, for live captions and talk time. */
  current(t) { return this.nameFor(t, t); }
}
