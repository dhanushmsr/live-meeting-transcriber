"""A meeting session: capture -> live streams -> captions -> report."""

import asyncio
import threading
import time
from collections import defaultdict
from datetime import datetime

from google import genai
from PySide6.QtCore import QObject, Signal

from . import audio, config, report
from .gemini import Gemini
from .live import LiveStream
from .speakers import SpeakerWatcher


class Session(QObject):
    caption = Signal(str, str, bool)  # speaker, text so far, is_final
    state_changed = Signal(str)       # recording | paused | processing | idle
    finished = Signal(str)            # report path
    notice = Signal(str)
    failed = Signal(str)

    def __init__(self, settings: config.Settings) -> None:
        super().__init__()
        self.settings = settings
        self.state = "idle"
        self.title = ""
        self.platform = ""
        self.started_at = datetime.now()
        self.captures: dict[str, audio.Capture] = {}
        self.streams: list[LiveStream] = []
        self.transcript: list[dict] = []
        self.talk: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))  # speaker -> minute -> sec
        self.languages: set[str] = set()
        self.participants: set[str] = set()

    # ---- control -------------------------------------------------------
    def start(self, platform: str = "", title: str = "") -> None:
        self.platform = platform
        self.title = title  # empty when the meeting name is unknown -> report is named by date/time only
        self.started_at = datetime.now()
        try:
            self.captures["You"] = audio.open_microphone()
        except Exception as e:
            self.failed.emit(f"Microphone unavailable: {e}")
            return
        try:
            self.captures["Others"] = audio.open_system_audio()
        except Exception as e:
            self.notice.emit(f"Only your microphone is captured. {e}")
        for c in self.captures.values():
            c.start()
        self._set_state("recording")
        threading.Thread(target=self._thread_main, daemon=True).start()

    def pause(self) -> None:
        if self.state == "recording":
            for c in self.captures.values():
                c.paused = True
            self._set_state("paused")

    def resume(self) -> None:
        if self.state == "paused":
            for c in self.captures.values():
                c.paused = False
            self._set_state("recording")

    def stop(self) -> None:
        if self.state in ("recording", "paused"):
            self._set_state("processing")
            for st in self.streams:
                st.stop()

    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)

    # ---- streaming (runs on its own thread / event loop) ---------------
    def _thread_main(self) -> None:
        try:
            asyncio.run(self._stream_all())
        except Exception as e:
            self.notice.emit(f"Streaming stopped: {e}")
        for c in self.captures.values():
            try:
                c.stop()
            except Exception:
                pass
        try:
            self._finalize()
        except Exception as e:
            self.failed.emit(f"Could not build the report: {e}")
        self._set_state("idle")

    async def _stream_all(self) -> None:
        client = genai.Client(api_key=self.settings.api_key)
        watcher = None
        if self.settings.read_names and "Others" in self.captures:
            others: list[LiveStream] = []
            watcher = SpeakerWatcher(client, self.settings, lambda: bool(others) and
                                     time.monotonic() - others[0].last_speech < 2.5, self.notice.emit)
        self.streams = []
        for label, cap in self.captures.items():
            fn = watcher.name_at if (watcher and label == "Others") else None
            st = LiveStream(label, cap, client, self.settings, self._partial, self._segment, self._talk,
                            self.notice.emit, speaker_fn=fn)
            if label == "Others" and watcher:
                others.append(st)
            self.streams.append(st)
        jobs = [st.run() for st in self.streams]
        self.watcher = watcher
        watch_task = asyncio.create_task(watcher.run()) if watcher else None
        try:
            await asyncio.gather(*jobs)
        finally:
            if watcher:
                watcher.stop()
                self.participants |= watcher.participants

    # callbacks from the streams
    def _name(self, speaker: str) -> str:
        return (self.settings.name or speaker) if speaker == "You" else speaker

    def _partial(self, speaker: str, text: str) -> None:
        self.caption.emit(self._name(speaker), text, False)

    def _segment(self, speaker: str, t: float, src: str, out: str, mono: float = 0.0) -> None:
        speaker = self._name(speaker)
        self.transcript.append({"t": t, "speaker": speaker, "text": src, "translation": out, "mono": mono})
        self.caption.emit(speaker, (out if self.settings.translate else src) or src or out, True)

    def _talk(self, speaker: str, minute: int, secs: float) -> None:
        self.talk[self._name(speaker)][minute] += secs

    # ---- report --------------------------------------------------------
    def _finalize(self) -> None:
        duration = max((st.offset for st in self.streams), default=0.0)
        if getattr(self, "watcher", None):  # lines spoken before the first screen reading landed: back-fill names
            for x in self.transcript:
                if x["speaker"] == "Others":
                    x["speaker"] = self.watcher.name_near(x["mono"]) or "Others"
        self.transcript.sort(key=lambda x: x["t"])
        text = "\n".join(
            f"[{int(x['t'] // 60):02d}:{int(x['t'] % 60):02d}] {x['speaker']}: {x['text'] or x['translation']}"
            for x in self.transcript
        )
        s = self.settings
        if text:
            try:
                analysis = Gemini(s.api_key, s.report_model, s.fallback_model).analyse(
                    text, s.name, self.title or "Meeting")
                self.languages.update(analysis.get("languages") or [])
            except Exception as e:
                self.notice.emit(f"Analysis failed, report will contain the transcript only: {e}")
                analysis = {}
        else:
            analysis = {"summary": "No speech was captured in this meeting."}
        minutes = duration / 60
        n_past, past_minutes = config.history_totals()
        path = report.build(
            settings=s, title=self.title, platform=self.platform, started=self.started_at,
            minutes=minutes, transcript=self.transcript, talk=self.talk, analysis=analysis,
            languages=sorted(self.languages), participants=sorted(self.participants), meetings_total=n_past + 1, minutes_total=past_minutes + minutes,
        )
        config.add_history({
            "title": self.title, "started": self.started_at.isoformat(timespec="seconds"),
            "minutes": round(minutes, 2), "report": str(path),
        })
        self.finished.emit(str(path))
