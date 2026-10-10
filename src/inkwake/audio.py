"""Audio capture: the user's microphone ("You") and the system output ("Others").

System output is what the meeting app plays, so this works with any platform
(Meet, Zoom, Teams, WhatsApp, ...) without integrating with it.
  - Windows: WASAPI loopback, no setup needed.
  - macOS: needs a virtual device such as BlackHole (system audio capture is not
    exposed to ordinary apps).
"""

import io
import sys
import threading
import wave

import numpy as np

SR = 16000
FRAME = 0.1  # seconds, used for speech-activity detection
SPEECH_RMS = 0.012


def to_16k_mono(data: np.ndarray, rate: int) -> np.ndarray:
    if len(data) == 0 or rate == SR:
        return data.astype(np.float32)
    n_out = int(len(data) * SR / rate)
    return np.interp(np.linspace(0, len(data) - 1, n_out), np.arange(len(data)), data).astype(np.float32)


def activity(audio: np.ndarray) -> np.ndarray:
    """Boolean array, one entry per FRAME seconds: True where someone is speaking."""
    n = int(SR * FRAME)
    frames = len(audio) // n
    if frames == 0:
        return np.zeros(0, dtype=bool)
    x = audio[: frames * n].reshape(frames, n)
    return np.sqrt((x ** 2).mean(axis=1)) > SPEECH_RMS


def to_wav(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


class Capture:
    def __init__(self) -> None:
        self.paused = False
        self._rate = SR
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()

    def _push(self, mono: np.ndarray) -> None:
        if not self.paused:
            with self._lock:
                self._chunks.append(mono)

    def drain(self) -> np.ndarray:
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return to_16k_mono(np.concatenate(chunks), self._rate)

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class SoundDeviceCapture(Capture):
    def __init__(self, device=None) -> None:
        super().__init__()
        import sounddevice as sd

        info = sd.query_devices(device if device is not None else sd.default.device[0], "input")
        self._rate = int(info["default_samplerate"])
        self._sd = sd
        self._device = device
        self._stream = None

    def start(self) -> None:
        def cb(indata, frames, time_info, status):
            self._push(indata.mean(axis=1).astype(np.float32))

        self._stream = self._sd.InputStream(
            device=self._device, samplerate=self._rate, channels=1, dtype="float32", callback=cb
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()


class WasapiLoopbackCapture(Capture):
    def __init__(self) -> None:
        super().__init__()
        import pyaudiowpatch as pa

        self._pa = pa
        self._p = pa.PyAudio()
        self._dev = self._p.get_default_wasapi_loopback()
        self._rate = int(self._dev["defaultSampleRate"])
        self._stream = None

    def start(self) -> None:
        ch = int(self._dev["maxInputChannels"])
        pa = self._pa

        def cb(in_data, frame_count, time_info, status):
            arr = np.frombuffer(in_data, dtype=np.int16).reshape(-1, ch).mean(axis=1) / 32768.0
            self._push(arr.astype(np.float32))
            return (None, pa.paContinue)

        self._stream = self._p.open(
            format=pa.paInt16, channels=ch, rate=self._rate, input=True,
            input_device_index=self._dev["index"], frames_per_buffer=1024, stream_callback=cb,
        )

    def stop(self) -> None:
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
        self._p.terminate()


def open_system_audio() -> Capture:
    if sys.platform == "win32":
        return WasapiLoopbackCapture()
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and any(k in d["name"].lower() for k in ("blackhole", "loopback", "soundflower")):
            return SoundDeviceCapture(i)
    raise RuntimeError(
        "No system-audio device found. On macOS install BlackHole (existential.audio/blackhole) "
        "and route output through a Multi-Output Device."
    )


def open_microphone() -> Capture:
    return SoundDeviceCapture()
