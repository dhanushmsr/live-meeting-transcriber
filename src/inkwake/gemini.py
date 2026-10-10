"""Gemini API: transcription, translation and meeting analysis."""

import json
import time

from google import genai
from google.genai import errors, types

MAX_TRANSCRIPT_CHARS = 400_000


def _json(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1:] if text.lower().startswith("json") else text
    return json.loads(text)


class Gemini:
    def __init__(self, api_key: str, report_model: str, fallback_model: str) -> None:
        self.client = genai.Client(api_key=api_key)
        self.report_model = report_model
        self.fallback_model = fallback_model

    def _ask(self, contents, model: str) -> object:
        for attempt in range(5):
            try:
                r = self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
                )
                return _json(r.text)
            except errors.APIError as e:  # busy / rate limited: back off and retry
                if e.code not in (429, 500, 503) or attempt == 4:
                    raise
                time.sleep(2 ** attempt)

    def analyse(self, transcript: str, host: str, title: str) -> dict:
        prompt = (
            "You are an executive assistant analysing a meeting for the person who started it"
            f"{' (' + host + ')' if host else ''}. The transcript may be in several languages; write your "
            "analysis in English. Be specific and faithful to the transcript; do not invent facts. "
            'Return JSON only: {"summary": "<one concise paragraph>", '
            '"topics": [{"title": str, "detail": str}], "key_points": [str], "decisions": [str], '
            '"action_items": [{"task": str, "owner": str}], "languages": [str], "sentiment": str, '
            '"follow_ups": [str]}. '
            f"Meeting: {title}.\n\nTranscript:\n{transcript[:MAX_TRANSCRIPT_CHARS]}"
        )
        try:
            data = self._ask(prompt, self.report_model)
        except errors.APIError:  # quota/outage on the stronger model: fall back to the live model
            data = self._ask(prompt, self.fallback_model)
        return data if isinstance(data, dict) else {}
