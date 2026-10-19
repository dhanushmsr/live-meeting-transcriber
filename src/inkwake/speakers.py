"""Who is speaking? Reads the active-speaker name from the meeting screen (Meet, Zoom, Teams, ...).

Audio alone cannot tell remote people apart or know their names, but every meeting app shows each
person's name on their tile and highlights whoever is talking. While remote audio is active we grab
a small screenshot every ~2 s and ask Gemini vision which name is highlighted. Each answer is stamped
with the time the screenshot was taken, so it lines up with the words even though the reply is slow.
"""

import asyncio
import io
import json
import time
from collections import deque
from typing import Callable

from google import genai
from google.genai import types

REQUEST_TIMEOUT_MS = 20_000  # never let one slow call block the others
INTERVAL = 2.0     # seconds between screenshots while remote audio is active
MAX_INFLIGHT = 3   # vision calls can take 3-5 s; overlap them
VALID_FOR = 8.0    # an observation still counts this long (a speaker rarely changes faster)
WIDTH = 1280

PROMPT = (
    "This is a screenshot of a video meeting (Google Meet, Zoom, Microsoft Teams, WhatsApp, ...). "
    "Find the participant who is speaking right now: the tile with the active-speaker highlight "
    "(a coloured or glowing border, an animated sound-wave / three-dot microphone icon on the tile, or the "
    "spotlighted / enlarged speaker). Other participants' tiles are plain or show a muted-microphone icon. "
    "Ignore any captions text drawn over the video. "
    'Return JSON only: {"speaker": "<name exactly as written on that tile, or empty>", '
    '"me": <true if that tile is the local user (labelled You / self view)>, '
    '"participants": [<every participant name visible>]}. '
    'If there is no meeting on screen or you cannot tell, speaker is "". Do not guess.'
)


def grab_jpeg() -> bytes:
    import mss
    from PIL import Image

    with (getattr(mss, "MSS", None) or mss.mss)() as sct:  # mss 10 renamed mss.mss to mss.MSS
        shot = sct.grab(sct.monitors[1])  # primary monitor
        img = Image.frombytes("RGB", shot.size, shot.rgb)
    if img.width > WIDTH:
        img = img.resize((WIDTH, int(img.height * WIDTH / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return buf.getvalue()


def ask(client: genai.Client, model: str, jpeg: bytes) -> dict:
    r = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), PROMPT],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0,
                                           http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS)),
    )
    data = json.loads(r.text)
    return data if isinstance(data, dict) else {}


class SpeakerWatcher:
    def __init__(self, client: genai.Client, settings, is_active: Callable[[], bool], on_notice: Callable) -> None:
        self.client, self.settings, self.is_active, self.on_notice = client, settings, is_active, on_notice
        self.timeline: deque = deque(maxlen=600)   # (monotonic time of screenshot, name or None)
        self.participants: set[str] = set()
        self._stop = False
        self._inflight = 0
        self._warned = False

    def stop(self) -> None:
        self._stop = True

    def name_at(self, t: float) -> str | None:
        """Remote speaker name at monotonic time t (None if unknown)."""
        best = None
        for ts, name in self.timeline:
            if ts <= t:
                best = (ts, name)
            else:
                break
        if best and t - best[0] <= VALID_FOR:
            return best[1]
        return self.sole_remote()

    def sole_remote(self) -> str | None:
        """If the screen shows exactly one other person, everything said by 'Others' is theirs even when the speaking
        highlight can't be read."""
        me = {"you", "me", (getattr(self.settings, "name", "") or "").strip().lower()} - {""}
        others = [n for n in self.participants if n.lower() not in me and not any(m in n.lower() for m in me if len(m) > 3)]
        return others[0] if len(others) == 1 else None

    def name_near(self, t: float, window: float = 6.0) -> str | None:
        """Closest named observation within `window` seconds of t, before or after (used to back-fill early lines)."""
        best = min(((abs(ts - t), n) for ts, n in self.timeline if n), default=None)
        return best[1] if best and best[0] <= window else None

    async def run(self) -> None:
        while not self._stop:
            await asyncio.sleep(INTERVAL)
            if not getattr(self.settings, "read_names", True) or self._inflight >= MAX_INFLIGHT:
                continue
            if not self.is_active():
                self._append(time.monotonic(), None)  # nobody speaking: clear the current speaker
                continue
            asyncio.create_task(self._observe())

    def _append(self, ts: float, name: str | None) -> None:
        # keep the timeline ordered even when slow replies arrive out of order
        items = list(self.timeline) + [(ts, name)]
        items.sort(key=lambda x: x[0])
        self.timeline = deque(items[-600:], maxlen=600)

    async def _observe(self) -> None:
        self._inflight += 1
        ts = time.monotonic()
        try:
            jpeg = await asyncio.to_thread(grab_jpeg)
            res = await asyncio.to_thread(ask, self.client, self.settings.fallback_model, jpeg)
            name = (res.get("speaker") or "").strip()
            self.participants.update(n for n in (str(x).strip() for x in res.get("participants") or [])
                                    if n and n.lower() not in ("you", "me"))
            # An empty answer means "could not tell" (the model is sometimes unsure on a frame): keep the last
            # known speaker instead of clearing it. The local user is covered by the microphone stream.
            if name and not res.get("me"):
                self._append(ts, name)
        except Exception as e:
            if not self._warned:
                self._warned = True
                self.on_notice(f"Could not read speaker names from the screen: {str(e)[:120]}")
        finally:
            self._inflight -= 1
