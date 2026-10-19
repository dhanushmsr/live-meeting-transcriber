"""Keep Ink's own windows out of screen captures.

Ink reads participant names from screenshots of the meeting. Its caption bar sits over the bottom of the video tiles,
exactly where meeting apps print names and speaking indicators, so without this it blinds itself.
"""

import sys

WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 (2004) and later: the window is visible to the user but absent from captures


def hide_from_screen_capture(widget) -> bool:
    """Exclude a Qt window from screenshots. Returns True when Windows accepted it (no-op elsewhere)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False
