"""Gemini API: meeting analysis, live catch-up notes and question answering."""

import json
import time

from google import genai
from google.genai import errors, types

MAX_TRANSCRIPT_CHARS = 400_000
LIVE_TAIL_CHARS = 14_000  # how much recent transcript the catch-up panel looks at


def _json(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1:] if text.lower().startswith("json") else text
    return json.loads(text)


def stamp(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


class Gemini:
    def __init__(self, api_key: str, report_model: str, fallback_model: str) -> None:
        self.client = genai.Client(api_key=api_key)
        self.report_model = report_model
        self.fallback_model = fallback_model

    def _call(self, contents, model: str, as_json: bool, timeout_ms: int | None = None, attempts: int = 5) -> str:
        extra = {"response_mime_type": "application/json"} if as_json else {}
        if timeout_ms:  # interactive calls must fail fast instead of hanging a panel
            extra["http_options"] = types.HttpOptions(timeout=timeout_ms)
        cfg = types.GenerateContentConfig(temperature=0.2, **extra)
        for attempt in range(attempts):
            try:
                return self.client.models.generate_content(model=model, contents=contents, config=cfg).text or ""
            except errors.APIError as e:  # busy / rate limited: back off and retry
                if e.code not in (429, 500, 503) or attempt == attempts - 1:
                    raise
                time.sleep(2 ** attempt)
        return ""

    def _with_fallback(self, contents, as_json: bool, timeout_ms: int | None = None):
        """Try the stronger model; if it is rate-limited or down, use the lighter one."""
        try:
            text = self._call(contents, self.report_model, as_json, timeout_ms=timeout_ms)
        except errors.APIError:
            text = self._call(contents, self.fallback_model, as_json, timeout_ms=timeout_ms)
        return _json(text) if as_json else text

    # ---- after the meeting ---------------------------------------------
    def analyse(self, transcript: str, host: str, title: str, bookmarks: list[str] | None = None) -> dict:
        marks = ""
        if bookmarks:
            marks = ("\nThe host bookmarked these moments, so make sure what was said there is covered (never mention "
                     "the bookmarks themselves): " + "; ".join(bookmarks) + ".")
        prompt = (
            "You write meeting briefs for busy people"
            f"{' (the reader is ' + host + ')' if host else ''}. The transcript may be in several languages and "
            "may be messy speech-to-text; write everything in plain English. Be faithful to the transcript and never "
            "invent facts, names, dates or owners. Write for someone who has 30 seconds: short sentences, no filler, "
            "no corporate jargon, and NEVER say the same fact in more than one section. If a section has nothing "
            "real to say, return an empty list or an empty string for it. "
            'Return JSON only: {"title": "<what the meeting was about, max 7 words>", '
            '"tldr": "<ONE plain sentence, max 25 words: the single most important outcome>", '
            '"summary": "<2-3 short sentences of context that the other sections do not already give>", '
            '"decisions": [<things that were decided, max 6, one short line each>], '
            '"action_items": [{"task": "<something a person must do, starting with a verb>", "owner": "<name or empty>", '
            '"due": "<deadline if one was said, else empty>"}], '
            '"takeaways": [<max 5 short points worth remembering that are NOT decisions or action items>], '
            '"open_questions": [<questions raised but left unanswered>], '
            '"chapters": [{"start": "mm:ss", "title": "<max 6 words>", "summary": "<max 12 words>"}], '
            '"follow_up_email": {"subject": str, "body": "<plain-text email from the host to the participants, under '
            "120 words: one line of thanks, the decisions, the action items with owners and dates, next step; sign off "
            'with the host\'s name if known, no placeholders>"}, '
            '"languages": [str], "sentiment": "<one word>"}. '
            "Chapters are the main stretches of the meeting in order (at most 6) and use the transcript timestamps. "
            f"{marks} Meeting: {title or 'unknown'}.\n\nTranscript:\n{transcript[:MAX_TRANSCRIPT_CHARS]}"
        )
        data = self._with_fallback(prompt, True, timeout_ms=120_000)
        return data if isinstance(data, dict) else {}

    # ---- during the meeting --------------------------------------------
    def live_notes(self, transcript: str, previous: dict | None, host: str) -> dict:
        """Quick catch-up for someone who looked away: runs on the fast model, a few seconds."""
        prev = ""
        if previous and previous.get("gist"):
            prev = f"\nNotes from earlier in the meeting (keep what is still true): {json.dumps(previous)[:3000]}"
        prompt = (
            "A meeting is in progress. Write a quick catch-up for a participant"
            f"{' (' + host + ')' if host else ''} who looked away. English only; faithful to the transcript, no "
            'inventions. Return JSON only: {"gist": "<2-4 sentences: what has been discussed so far>", '
            '"now": "<one line: what is being discussed right now>", "decisions": [str], '
            '"action_items": [{"task": str, "owner": str}], "open_questions": [str]}.'
            f"{prev}\n\nLatest transcript:\n{transcript[-LIVE_TAIL_CHARS:]}"
        )
        data = _json(self._call(prompt, self.fallback_model, True, timeout_ms=30_000, attempts=2))
        return data if isinstance(data, dict) else {}

    # ---- question answering --------------------------------------------
    def ask(self, question: str, transcript: str, analysis: dict | None) -> str:
        prompt = (
            "You answer questions about one meeting using ONLY its transcript and summary below. If the meeting "
            "does not say, answer that it was not discussed. Be concise, name who said what, and cite timestamps "
            "like [12:30]. Reply in the language of the question.\n\n"
            f"Summary: {json.dumps(analysis or {})[:6000]}\n\nTranscript:\n{transcript[:MAX_TRANSCRIPT_CHARS]}\n\n"
            f"Question: {question}"
        )
        return (self._with_fallback(prompt, False, timeout_ms=90_000) or "").strip()
