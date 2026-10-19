"""Who said what, worked out AFTER the meeting from the recording of the other participants.

During the meeting Ink can only name a speaker if the meeting screen shows it clearly. Afterwards Gemini listens to the
whole recording of the other side and says when each voice speaks. This module turns that list of turns into names on the
transcript lines and real talk-time per person. Pure logic, no network: easy to test.
"""

import re
from collections import defaultdict

MIN_RECORDING_SECONDS = 15
MAX_RECORDING_MB = 150  # about 80 minutes of 16 kHz mono; longer meetings keep the screen-based names
GENERIC = "others"


def clean_turns(raw) -> list[dict]:
    """Keep only well-formed turns, sorted by start time."""
    turns = []
    for t in raw or []:
        try:
            start, end = float(t["start"]), float(t["end"])
        except (KeyError, TypeError, ValueError):
            continue
        label = str(t.get("speaker") or "").strip()
        if label and end > start >= 0:
            turns.append({"start": start, "end": end, "speaker": label, "starts": str(t.get("starts") or "").strip()})
    return sorted(turns, key=lambda t: t["start"])


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def line_window(lines: list[dict], i: int) -> tuple[float, float]:
    """Start/end (seconds) of transcript line i. Uses the real end the live stream recorded; otherwise it ends when the
    next line starts or after a speaking-rate guess."""
    t = float(lines[i].get("t", 0))
    end = float(lines[i].get("t_end") or 0)
    if end > t:
        return t, end
    words = len((lines[i].get("text") or lines[i].get("translation") or "").split())
    guess = t + max(2.0, words * 0.45)
    nxt = float(lines[i + 1]["t"]) if i + 1 < len(lines) else guess
    return t, min(guess, nxt) if nxt > t else guess


MIN_PART_SECONDS = 1.0   # a stretch shorter than this is not worth its own transcript line
SAME_VOICE_GAP = 3.0     # a short pause between two stretches by the same voice stays one line


def pieces(window: tuple[float, float], turns: list[dict]) -> list[list]:
    """The window as consecutive [speaker, start, end, first_words] stretches (same voice with a short gap is merged)."""
    segs = []
    for tr in turns:
        a, b = max(window[0], tr["start"]), min(window[1], tr["end"])
        if b - a > 0.05:
            segs.append([tr["speaker"], a, b, tr.get("starts", "")])
    merged: list[list] = []
    for sp, a, b, st in segs:
        if merged and merged[-1][0] == sp and a - merged[-1][2] <= SAME_VOICE_GAP:
            merged[-1][2] = b
        else:
            merged.append([sp, a, b, st])
    return merged


def _tokens(text: str) -> list[str]:
    return _norm_words(text)


def _norm_words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", (text or "").lower(), flags=re.UNICODE).split()


def find_phrase(words: list[str], phrase: str, ideal: int, lo: int, reach: int) -> int | None:
    """Index in `words` where `phrase` (the first words of a turn) begins, searching near `ideal`; None if not found."""
    ph = _norm_words(phrase)[:5]
    if len(ph) < 2:
        return None
    norm = [_norm_words(w)[0] if _norm_words(w) else "" for w in words]
    best, best_score = None, 0.0
    for i in range(max(lo, ideal - 2 * reach), min(len(words), ideal + 2 * reach) + 1):
        window = norm[i:i + len(ph)]
        score = sum(1 for a, b in zip(window, ph) if a == b) / len(ph)
        if score > best_score or (score == best_score and best is not None and abs(i - ideal) < abs(best - ideal)):
            best, best_score = i, score
    return best if best_score >= 0.6 else None


SENTENCE_END = (".", "?", "!", "。", "।", "؟")  # . ? ! and the full stops of Chinese/Japanese, Hindi, Arabic


def _slice(words: list[str], fractions: list[float], hints: list[str] | None = None) -> list[str]:
    """Cut a word list into one part per speaker. Each cut goes (1) where that speaker's first words appear, else
    (2) at the sentence end nearest to where the timing says, else (3) where the timing says."""
    ends = [i + 1 for i, w in enumerate(words) if w.endswith(SENTENCE_END)]
    reach = max(4, int(0.2 * len(words)))
    cuts, acc, prev = [], 0.0, 0
    for k, f in enumerate(fractions[:-1]):
        acc += f
        ideal = round(len(words) * acc)
        hint = (hints or [""] * len(fractions))[k + 1]
        cut = find_phrase(words, hint, ideal, prev + 1, reach) if hint else None
        if cut is None:
            near = [e for e in ends if abs(e - ideal) <= reach and e > prev]
            cut = min(near, key=lambda e: abs(e - ideal)) if near else max(ideal, prev)
        cuts.append(cut)
        prev = cut
    parts, start = [], 0
    for c in cuts + [len(words)]:
        parts.append(" ".join(words[start:c]))
        start = c
    return parts


def split_line(x: dict, window: tuple[float, float], turns: list[dict]) -> list[dict] | None:
    """If two or more voices spoke inside one transcript line, cut it into one line per voice (words shared out by time)."""
    parts = [p for p in pieces(window, turns) if p[2] - p[1] >= MIN_PART_SECONDS]
    if len({p[0] for p in parts}) < 2:
        return None
    words = (x.get("text") or "").split()
    tr_words = (x.get("translation") or "").split()
    if len(words or tr_words) < len(parts):
        return None
    total = sum(p[2] - p[1] for p in parts)
    fractions = [(p[2] - p[1]) / total for p in parts]
    texts = _slice(words, fractions, [p[3] for p in parts])
    trans = _slice(tr_words, fractions) if tr_words else [""] * len(parts)
    out = []
    for (speaker, start, end, _first), text, tr in zip(parts, texts, trans):
        if not (text or tr):
            continue
        out.append({**x, "t": start, "t_end": end, "text": text, "translation": tr, "diar": speaker,
                    "screen": None, "mention": False})
    return out or None


def label_for(window: tuple[float, float], turns: list[dict], slack: float = 3.0) -> str | None:
    """Speaker with the most overlap, or the nearest turn within `slack` seconds."""
    best, best_ov = None, 0.0
    for tr in turns:
        ov = _overlap(window[0], window[1], tr["start"], tr["end"])
        if ov > best_ov:
            best, best_ov = tr["speaker"], ov
    if best:
        return best
    mid = (window[0] + window[1]) / 2
    near = min(turns, key=lambda tr: 0 if tr["start"] <= mid <= tr["end"] else min(abs(mid - tr["start"]), abs(mid - tr["end"])), default=None)
    if near and (near["start"] - slack <= mid <= near["end"] + slack):
        return near["speaker"]
    return None


def name_map(lines: list[dict], min_share: float = 0.6, min_lines: int = 2) -> dict[str, str]:
    """Turn 'Speaker 2' into a real name when the meeting screen agreed on that name for most of that voice's lines."""
    votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for x in lines:
        screen = x.get("screen")
        if x.get("diar") and screen and screen.lower() != GENERIC:
            votes[x["diar"]][screen] += 1
    out = {}
    for label, names in votes.items():
        total = sum(names.values())
        name, n = max(names.items(), key=lambda kv: kv[1])
        if n >= min_lines and n / total >= min_share:
            out[label] = name
    return out


def apply_turns(transcript: list[dict], turns: list[dict], talk: dict, own_names: set[str], mention_fn=None) -> dict:
    """Name the 'Others' lines and rebuild talk time from the turns.

    Lines from the other side carry channel == "Others"; the host's own lines are never touched. A line that holds two
    people's words is split in two. `transcript` is updated in place; the new talk dict is returned (the host's own
    entries are kept as they were).
    """
    turns = clean_turns(turns)
    others = sorted((x for x in transcript if x.get("channel") == "Others"), key=lambda x: x.get("t", 0))
    if not turns or not others:
        return talk

    rebuilt: list[dict] = []
    for i, x in enumerate(others):
        window = line_window(others, i)
        parts = split_line(x, window, turns)
        if parts:
            rebuilt += parts
        else:
            x["screen"] = x.get("speaker")  # what the meeting screen said for this single-voice line
            x["diar"] = label_for(window, turns)
            rebuilt.append(x)

    names = name_map(rebuilt)
    for x in rebuilt:
        if x.get("diar"):
            x["speaker"] = names.get(x["diar"], x["diar"])
        if mention_fn and x.get("diar"):
            x["mention"] = bool(mention_fn(x["speaker"], f"{x.get('text', '')} {x.get('translation', '')}"))
    transcript[:] = [x for x in transcript if x.get("channel") != "Others"] + rebuilt

    own = {n.lower() for n in own_names if n}
    new_talk: dict[str, dict[int, float]] = {k: dict(v) for k, v in talk.items() if k.lower() in own}
    for tr in turns:
        who = names.get(tr["speaker"], tr["speaker"])
        t = tr["start"]
        while t < tr["end"] - 1e-6:
            minute = int(t // 60)
            step = min(tr["end"], (minute + 1) * 60) - t
            bucket = new_talk.setdefault(who, {})
            bucket[minute] = bucket.get(minute, 0.0) + step
            t += step
    return new_talk


def speakers_found(turns: list[dict]) -> list[str]:
    seen = []
    for t in clean_turns(turns):
        if t["speaker"] not in seen:
            seen.append(t["speaker"])
    return seen


def looks_like_real_name(label: str) -> bool:
    return not re.fullmatch(r"(speaker|voice|person)\s*\d+", label.strip(), flags=re.I)
