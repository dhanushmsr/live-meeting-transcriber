"""Heuristic detection of a meeting in progress. Returns a platform name or None.

Detection is best-effort (process names and window titles); the user can always
start Ink manually from the bubble.
"""

import sys

import psutil

# (window-title substring, lowercase) -> platform
TITLE_RULES = [
    ("zoom meeting", "Zoom"),
    ("meet.google.com", "Google Meet"),
    ("meet - ", "Google Meet"),
    ("| microsoft teams", "Microsoft Teams"),
    ("whatsapp", "WhatsApp"),
]
# a title match only counts when it also looks like a live call
CALL_WORDS = ("meeting", "meet", "call", "huddle")


def _titles_windows() -> list[str]:
    import ctypes

    user32 = ctypes.windll.user32
    titles: list[str] = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                titles.append(buf.value)
        return True

    user32.EnumWindows(proc(cb), 0)
    return titles


GENERIC = {"", "meet", "google meet", "zoom meeting", "zoom", "whatsapp", "microsoft teams", "teams",
           "google chrome", "microsoft edge", "firefox", "meeting"}
SUFFIXES = (" - google chrome", " - microsoft edge", " - mozilla firefox", " - brave", " | microsoft teams",
            " - microsoft teams", " - zoom", " - meet", " - google meet")


def _title_from(window: str) -> str:
    """Best-effort meeting name from a window title, e.g. 'Weekly sync - Google Chrome' -> 'Weekly sync'."""
    t = window.strip()
    for _ in range(3):
        low = t.lower()
        for suf in SUFFIXES:
            if low.endswith(suf):
                t = t[: -len(suf)].strip()
                break
    t = t.removeprefix("Meet - ").removeprefix("Meet – ").strip()
    return "" if t.lower() in GENERIC else t


def detect() -> tuple[str, str] | None:
    """Returns (platform, meeting title or "") for a meeting in progress, else None."""
    try:
        names = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
    except Exception:
        names = set()
    zoom = bool(names & {"cpthost.exe", "cpthost"})  # Zoom runs this process only during a meeting
    if sys.platform != "win32":
        return ("Zoom", "") if zoom else None
    try:
        windows = _titles_windows()
    except Exception:
        return ("Zoom", "") if zoom else None
    for w in windows:
        t = w.lower()
        for needle, platform in TITLE_RULES:
            if needle in t and (needle in ("zoom meeting", "meet.google.com") or any(k in t for k in CALL_WORDS)):
                return platform, _title_from(w)
    return ("Zoom", "") if zoom else None
