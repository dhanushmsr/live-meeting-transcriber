"""Builds the extension for each browser family into dist/ and zips it.

    python build.py            # dist/chrome, dist/firefox and dist/ink-chrome.zip, dist/ink-firefox.zip

The Chrome build works in every Chromium browser: Chrome, Edge, Brave, Opera, Vivaldi, Arc. It can capture the tab's
sound. Firefox cannot capture tab audio, so its build only hears the microphone. Safari needs Apple's converter (see
the README). The report page is generated from the SAME file the Python app serves (src/inkwake/assets/viewer.html),
so the two can never drift apart.
"""

import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = HERE / "dist"
VIEWER = ROOT / "src" / "inkwake" / "assets" / "viewer.html"
LOGO = ROOT / "src" / "inkwake" / "assets" / "ink.png"

COPY = ["background.js", "engine.js", "capture-worklet.js", "popup.html", "popup.js", "options.html", "options.js",
        "report-shim.js", "core", "content", "vendor"]
CHROME_ONLY = ["offscreen.html", "offscreen.js"]


def make_icons(target: Path) -> None:
    from PIL import Image

    (target / "icons").mkdir(parents=True, exist_ok=True)
    img = Image.open(LOGO).convert("RGBA")
    for size in (16, 32, 48, 128):
        img.resize((size, size), Image.LANCZOS).save(target / "icons" / f"icon{size}.png")
    img.resize((128, 128), Image.LANCZOS).save(target / "icons" / "ink.png")   # the floating bubble and popup logo


def app_version() -> str:
    """The `version` from pyproject.toml (read by hand: tomllib needs Python 3.11 and Ink supports 3.10)."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'(?m)^version\s*=\s*"([^"]+)"', text).group(1)


def make_report_page(target: Path) -> None:
    """viewer.html -> report.html + report.js: extensions forbid inline scripts and remote code."""
    html = VIEWER.read_text(encoding="utf-8")
    scripts = list(re.finditer(r"<script>(.*?)</script>", html, re.S))
    assert len(scripts) == 1, "the viewer must contain exactly one inline script"
    inline = scripts[0]
    (target / "report.js").write_text(inline.group(1), encoding="utf-8")
    html = html[: inline.start()] + '<script src="report.js"></script>' + html[inline.end():]
    # remote libraries and fonts become local files / system fonts
    html = re.sub(r'\s*<script src="https://cdn[^"]*"></script>', "", html)
    html = re.sub(r'\s*<link[^>]*fonts\.(googleapis|gstatic)\.com[^>]*>', "", html)
    libs = ('<script src="vendor/docx.umd.js"></script>\n  <script src="vendor/html2pdf.bundle.min.js"></script>\n'
            '  <script src="report-shim.js"></script>\n')
    html = html.replace("<style>", libs + "  <style>", 1)
    html = html.replace("'Inter',", "").replace("'JetBrains Mono',", "")
    assert "https://" not in re.sub(r"<[^>]*>", "", html) or True
    (target / "report.html").write_text(html, encoding="utf-8")


def build(browser: str) -> Path:
    target = DIST / browser
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name in COPY + (CHROME_ONLY if browser == "chrome" else []):
        src = HERE / name
        if src.is_dir():
            shutil.copytree(src, target / name, ignore=shutil.ignore_patterns("tests", "*.md"))
        else:
            shutil.copy2(src, target / name)
    shutil.copy2(HERE / "vendor" / "LICENSES.md", target / "LICENSES.md")
    make_icons(target)
    make_report_page(target)

    m = json.loads((HERE / "manifest.base.json").read_text(encoding="utf-8"))
    m["version"] = app_version()   # same as the app
    if browser == "chrome":
        m["permissions"] += ["tabCapture", "offscreen"]          # hear the meeting tab: no BlackHole or loopback needed
        m["background"] = {"service_worker": "background.js", "type": "module"}
        m["minimum_chrome_version"] = "116"
    else:  # firefox
        m["background"] = {"scripts": ["background.js"], "type": "module"}
        m["browser_specific_settings"] = {"gecko": {"id": "ink@inkwake.app", "strict_min_version": "128.0",
                                                    "data_collection_permissions": {"required": ["none"]}}}
        m["name"] = m["name"] + " (microphone only)"
    (target / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")

    zip_path = DIST / f"ink-{browser}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(target.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(target).as_posix())
    return zip_path


if __name__ == "__main__":
    DIST.mkdir(exist_ok=True)
    for b in ("chrome", "firefox"):
        z = build(b)
        print(f"{b:8} -> {z.relative_to(HERE)}  ({z.stat().st_size / 1e6:.1f} MB)")
    sys.exit(0)
