"""Settings, meeting history and file locations."""

import json
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

HOME = Path.home() / ".inkwake"
CONFIG_FILE = HOME / "config.json"
HISTORY_FILE = HOME / "meetings.json"


def desktop() -> Path:
    for p in (Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop"):
        if p.exists():
            return p
    return Path.home() / "Desktop"


def reports_dir(name: str = "") -> Path:
    """Desktop/<user name>/ (Desktop/inkwake/ when no name is set); reports are saved here automatically."""
    folder = re.sub(r'[<>:"/\|?*]', "", name).strip(" .") or "inkwake"
    return desktop() / folder

LANG_CODES = {
    "English": "en", "Tamil": "ta", "Hindi": "hi", "Telugu": "te", "Malayalam": "ml", "Kannada": "kn",
    "Bengali": "bn", "Urdu": "ur", "Spanish": "es", "French": "fr", "German": "de", "Italian": "it",
    "Portuguese": "pt", "Russian": "ru", "Arabic": "ar", "Chinese": "zh", "Japanese": "ja", "Korean": "ko",
    "Turkish": "tr", "Indonesian": "id", "Dutch": "nl",
}
LANGUAGES = list(LANG_CODES)


@dataclass
class Settings:
    name: str = ""
    email: str = ""
    role: str = ""
    api_key: str = ""
    report_model: str = "gemini-flash-latest"         # final meeting analysis
    fallback_model: str = "gemini-flash-lite-latest"  # used if the report model is rate-limited
    captions: bool = True
    translate: bool = True   # captions always appear in target_language
    version: int = 2
    target_language: str = "English"
    auto_detect: bool = True
    read_names: bool = True  # read participant names from the meeting screen
    bubble_x: int = -1
    bubble_y: int = -1

    @classmethod
    def load(cls) -> "Settings":
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in raw.items() if k in known})
        if raw.get("version", 1) < 2:  # v1 had translation off by default; captions should follow the chosen language
            s.translate, s.version = True, 2
        s.api_key = s.api_key or os.environ.get("GEMINI_API_KEY", "")
        return s

    def save(self) -> None:
        HOME.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def load_history() -> list[dict]:
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def add_history(entry: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    history = load_history()
    history.append(entry)
    HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")


def history_totals() -> tuple[int, float]:
    """(number of past meetings, total minutes)."""
    h = load_history()
    return len(h), sum(float(m.get("minutes", 0)) for m in h)
