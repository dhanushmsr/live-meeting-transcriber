"""Real-time streaming speech -> text (and translation) over the Gemini Live API.

Audio goes up continuously in 100 ms frames; words come back as they are recognised, so captions
appear roughly a second behind the speaker instead of after a whole recording has been uploaded.

One streaming model (gemini-3.5-live-translate-preview) returns the source-language words and the translation
as they are recognised. The caption shows the translation when "translate" is on, otherwise the source words.
"""

import asyncio
import time
import wave
from pathlib import Path
from collections import deque
from typing import Callable

import numpy as np
from google import genai
from google.genai import types

from . import audio
from .config import LANG_CODES

TRANSLATE_MODEL = "gemini-3.5-live-translate-preview"

HANGOVER = 1.2        # keep streaming this long after the last speech so the model can close the phrase
PREROLL = 0.4         # audio kept from just before speech starts
IDLE_FINALIZE = 1.6   # no new words for this long -> the caption line is complete
MAX_LINE = 280        # characters; split very long run-on speech into several lines
DRAIN_EVERY = 0.1
DEAD_MIC_SECONDS = 8.0  # this long of pure silence from the microphone -> warn the user
TALK_SETTLE = 7.0     # wait this long for the screen reading before assigning talk time to a name
SPEECH_LAG = 1.6      # seconds between someone speaking and their words coming back


class LiveStream:
    """One audio source ("You" or "Others") streamed to its own Live session."""

    def __init__(self, label: str, capture: audio.Capture, client: genai.Client, settings,
                 on_partial: Callable, on_segment: Callable, on_talk: Callable, on_notice: Callable,
                 speaker_fn: Callable[[float], str | None] | None = None, record_to: Path | None = None) -> None:
        self.label, self.cap, self.client, self.settings = label, capture, client, settings
        self.on_partial, self.on_segment, self.on_talk, self.on_notice = on_partial, on_segment, on_talk, on_notice
        self.speaker_fn = speaker_fn           # monotonic time -> remote speaker name (None if unknown)
        self.last_speech = 0.0                 # monotonic time of the last audible speech
        self._speaker = label                  # speaker of the line being built (sticky)
        self._line_mono = 0.0
        self._line_end = 0.0                   # stream time (s) of the last words in the line being built
        self._floor = 0.002                    # running estimate of the room noise level (RMS)
        self._dead_secs = 0.0                  # how long the microphone has delivered digital silence
        self._mic_warned = False
        self._talk_pending: deque = deque()    # (monotonic time, minute) waiting for a name
        self.offset = 0.0                      # seconds of (unpaused) audio captured so far
        self._q: asyncio.Queue = asyncio.Queue(maxsize=600)
        self._stopping = False
        self._src, self._out = "", ""
        self._line_t = 0.0
        self._last_frag = 0.0
        self._hang = 0.0
        self._pre: deque = deque()
        self._fail_count = 0                   # consecutive connection failures (a few are normal and invisible)
        self._goaway: asyncio.Event | None = None
        # Optional: keep every sample on disk (16 kHz mono WAV) so the speakers can be told apart after the meeting.
        self.record_path = record_to
        self._rec = None
        if record_to:
            record_to.parent.mkdir(parents=True, exist_ok=True)
            self._rec = wave.open(str(record_to), "wb")
            self._rec.setnchannels(1)
            self._rec.setsampwidth(2)
            self._rec.setframerate(audio.SR)

    # ---- lifecycle -----------------------------------------------------
    def stop(self) -> None:
        self._stopping = True

    async def run(self) -> None:
        tasks = [asyncio.create_task(self._drain()), asyncio.create_task(self._idle())]
        self._goaway = asyncio.Event()
        self._ended = False
        stop_tries = 0
        try:
            while not self._ended:
                try:
                    await self._connection()
                except Exception as e:  # network blip, model hiccup: reconnect
                    if self._stopping:  # meeting already over: don't loop forever
                        stop_tries += 1
                        if stop_tries >= 2:
                            break
                    self._fail_count += 1
                    if self._fail_count == 3:  # one or two quick retries are normal; only speak up when it keeps failing
                        self.on_notice(f"Live captions keep disconnecting ({self.label}): {str(e)[:120]}. Still retrying...")
                    await asyncio.sleep(min(1.0 * self._fail_count, 5.0))
        finally:
            tasks[1].cancel()
            self._finalize_line()
            if self._rec:
                self._rec.close()
                self._rec = None

    # ---- settings -> session config -----------------------------------
    def _mode(self) -> str:
        return self.settings.target_language

    def _config(self, language: str):
        return TRANSLATE_MODEL, types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            translation_config=types.TranslationConfig(
                target_language_code=LANG_CODES.get(language, "en"), echo_target_language=True),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

    async def _connection(self) -> None:
        mode = self._mode()
        model, cfg = self._config(mode)
        self._goaway.clear()
        async with self.client.aio.live.connect(model=model, config=cfg) as sess:
            self._fail_count = 0
            send = asyncio.create_task(self._send(sess, mode))
            recv = asyncio.create_task(self._recv(sess))
            done, pending = await asyncio.wait({send, recv}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                t.result()

    # ---- audio in ------------------------------------------------------
    async def _drain(self) -> None:
        """Read the capture every 100 ms, account talk time, and queue audio while someone is speaking."""
        while not self._stopping:
            await asyncio.sleep(DRAIN_EVERY)
            self._take(self.cap.drain())
            self._settle_talk()
        self._take(self.cap.drain())
        self._settle_talk(force=True)
        await self._put(None)

    def _settle_talk(self, force: bool = False) -> None:
        while self._talk_pending and (force or time.monotonic() - self._talk_pending[0][0] > TALK_SETTLE):
            t, minute = self._talk_pending.popleft()
            self.on_talk((self.speaker_fn(t) if self.speaker_fn else None) or self.label, minute, audio.FRAME)

    def _take(self, data: np.ndarray) -> None:
        if len(data) == 0:
            return
        secs = len(data) / audio.SR
        rms = audio.frame_rms(data)
        # Learn how noisy the room is (the quiet 20% of frames) and require speech to stand clearly above it.
        if len(rms):
            self._floor = 0.97 * self._floor + 0.03 * float(np.percentile(rms, 20))
        act = rms > audio.speech_threshold(self._floor)
        self._watch_microphone(rms, secs)
        if act.any():
            self.last_speech = time.monotonic()
        now = time.monotonic()
        for i in act.nonzero()[0]:
            minute = int((self.offset + i * audio.FRAME) // 60)
            if self.speaker_fn:  # name readings arrive a few seconds late: settle talk time afterwards
                self._talk_pending.append((now, minute))
            else:
                self.on_talk(self.label, minute, audio.FRAME)
        self.offset += secs
        pcm = (np.clip(data, -1, 1) * 32767).astype("<i2").tobytes()
        if self._rec:  # every sample, silence included, so timestamps match the transcript
            self._rec.writeframes(pcm)
        if act.any():
            self._hang = HANGOVER
            while self._pre:
                self._put_nowait(self._pre.popleft())
            self._put_nowait(pcm)
        elif self._hang > 0:  # trailing silence so the model sees the phrase end
            self._hang -= secs
            self._put_nowait(pcm)
        else:
            self._pre.append(pcm)
            while len(self._pre) * DRAIN_EVERY > PREROLL:
                self._pre.popleft()

    def _watch_microphone(self, rms: np.ndarray, secs: float) -> None:
        """Tell the user when their microphone delivers pure silence (hardware mute key, blocked, wrong device)."""
        if self.label != "You" or self._mic_warned or not len(rms):
            return
        if float(rms.max()) < audio.DEAD_RMS:
            self._dead_secs += secs
            if self._dead_secs >= DEAD_MIC_SECONDS:
                self._mic_warned = True
                self.on_notice(
                    "Ink can't hear your microphone (it is sending silence). Check the laptop's mic-mute key, "
                    "Windows Settings > Privacy > Microphone, other apps using the mic, or pick another microphone "
                    "in Ink's Settings.")
        else:
            self._dead_secs = 0.0

    def _put_nowait(self, item) -> None:
        if self._q.full():  # reconnecting for a long time: drop the oldest audio
            self._q.get_nowait()
        self._q.put_nowait(item)

    async def _put(self, item) -> None:
        self._put_nowait(item)

    async def _send(self, sess, mode) -> None:
        while True:
            if self._mode() != mode:  # user changed the caption language: reconnect with the new config
                return
            try:
                item = await asyncio.wait_for(self._q.get(), 0.5)
            except asyncio.TimeoutError:
                continue
            if item is None:  # meeting stopped: flush and wait briefly for the last words
                await sess.send_realtime_input(audio_stream_end=True)
                await asyncio.sleep(3.0)
                self._ended = True
                return
            await sess.send_realtime_input(audio=types.Blob(data=item, mime_type="audio/pcm;rate=16000"))

    # ---- text out ------------------------------------------------------
    async def _recv(self, sess) -> None:
        while True:
            async for m in sess.receive():
                if getattr(m, "go_away", None):  # the server's session time limit is near: reconnect cleanly, silently
                    self._goaway.set()
                    return
                sc = m.server_content
                if not sc:
                    continue
                if sc.input_transcription and sc.input_transcription.text:
                    self._fragment("src", sc.input_transcription.text)
                if sc.output_transcription and sc.output_transcription.text:
                    self._fragment("out", sc.output_transcription.text)

    def _fragment(self, kind: str, text: str) -> None:
        if self.speaker_fn:  # words arrive ~1.5 s after they were spoken: look up who spoke then
            who = self.speaker_fn(time.monotonic() - SPEECH_LAG)
            if who and who != self._speaker:
                self._finalize_line()  # a different person started talking: close the previous line
                self._speaker = who
        if not (self._src or self._out):
            self._line_t = max(0.0, self.offset - 1.5)
            self._line_mono = time.monotonic() - SPEECH_LAG
        if kind == "src":
            self._src += text
        else:
            self._out += text
        self._last_frag = time.monotonic()
        self._line_end = max(self._line_t, self.offset - SPEECH_LAG)
        shown = (self._out if self.settings.translate else self._src).strip()
        if shown:
            self.on_partial(self._speaker, shown)
        if len(self._src) > MAX_LINE and self._src.rstrip().endswith((".", "?", "!", "。")):
            self._finalize_line()

    async def _idle(self) -> None:
        while True:
            await asyncio.sleep(0.3)
            if (self._src or self._out) and time.monotonic() - self._last_frag > IDLE_FINALIZE:
                self._finalize_line()

    def _finalize_line(self) -> None:
        src, out = self._src.strip(), self._out.strip()
        self._src = self._out = ""
        if not (src or out):
            return
        self.on_segment(self._speaker, self._line_t, src, out, self._line_mono, self.label, self._line_end)
