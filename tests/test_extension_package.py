"""The browser extension is built from extension/. These checks catch the mistakes that make a browser reject or break it,
without needing a browser: a bad manifest, a missing file, a script that does not parse, inline scripts or remote code
(extensions forbid both), and imports that do not resolve."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXT = Path(__file__).resolve().parents[1] / "extension"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("ink_ext_build", EXT / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DIST = tmp_path_factory.mktemp("dist")
    for b in ("chrome", "firefox"):
        mod.build(b)
    return mod.DIST


def manifest(built, browser):
    return json.loads((built / browser / "manifest.json").read_text(encoding="utf-8"))


def test_both_zips_exist_and_are_small(built):
    for b in ("chrome", "firefox"):
        z = built / f"ink-{b}.zip"
        assert z.exists() and z.stat().st_size < 5_000_000


def test_chrome_manifest_can_capture_the_tab_and_uses_a_service_worker(built):
    m = manifest(built, "chrome")
    assert m["manifest_version"] == 3
    assert {"tabCapture", "offscreen", "activeTab", "scripting", "storage"} <= set(m["permissions"])
    assert m["background"]["service_worker"] == "background.js" and m["background"]["type"] == "module"
    assert "scripts" not in m["background"]


def test_firefox_manifest_has_no_chromium_only_parts(built):
    m = manifest(built, "firefox")
    assert m["manifest_version"] == 3
    assert not ({"tabCapture", "offscreen"} & set(m["permissions"]))
    assert m["background"]["scripts"] == ["background.js"] and "service_worker" not in m["background"]
    assert m["browser_specific_settings"]["gecko"]["id"].endswith("@inkwake.app")
    assert not (built / "firefox" / "offscreen.html").exists()
    assert "microphone only" in m["name"]


def test_permissions_are_the_minimum_and_never_all_websites(built):
    for b in ("chrome", "firefox"):
        m = manifest(built, b)
        assert "<all_urls>" not in m["permissions"] + m["host_permissions"]
        assert not any("*://*/*" in h for h in m["host_permissions"])
        assert all(h.startswith("https://") for h in m["host_permissions"])
        assert "tabs" not in m["permissions"], "activeTab is enough: Ink must not be able to read every tab"


def files_in_manifest(m):
    out = set(m["icons"].values()) | set(m["action"]["default_icon"].values())
    out |= {m["action"]["default_popup"], m["options_ui"]["page"]}
    out |= {m["background"].get("service_worker")} | set(m["background"].get("scripts", []))
    for r in m["web_accessible_resources"]:
        out |= set(r["resources"])
    return {f for f in out if f}


@pytest.mark.parametrize("browser", ["chrome", "firefox"])
def test_every_file_the_manifest_names_exists(built, browser):
    for f in files_in_manifest(manifest(built, browser)):
        assert (built / browser / f).is_file(), f"{browser}: manifest points at missing file {f}"


@pytest.mark.parametrize("browser", ["chrome", "firefox"])
def test_icons_are_square_and_the_right_size(built, browser):
    from PIL import Image
    for size in (16, 32, 48, 128):
        im = Image.open(built / browser / "icons" / f"icon{size}.png")
        assert im.size == (size, size)


@pytest.mark.parametrize("browser", ["chrome", "firefox"])
def test_pages_have_no_inline_scripts_handlers_or_remote_code(built, browser):
    root = built / browser
    pages = [p for p in root.glob("*.html")]
    assert {p.name for p in pages} >= {"popup.html", "options.html", "report.html"}
    for page in pages:
        html = page.read_text(encoding="utf-8")
        for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S):
            attrs, body = m.groups()
            src = re.search(r'src="([^"]+)"', attrs)
            assert src, f"{page.name}: inline script (extensions forbid it)"
            assert not body.strip()
            assert not src.group(1).startswith(("http:", "https:", "//")), f"{page.name}: remote script {src.group(1)}"
            assert (root / src.group(1)).is_file(), f"{page.name}: script {src.group(1)} is missing"
        assert not re.search(r"\son\w+\s*=", html), f"{page.name}: inline event handler"
        for link in re.findall(r"<link\b[^>]*>", html):
            assert not re.search(r'href="https?:', link), f"{page.name}: remote stylesheet/font {link}"


@pytest.mark.parametrize("browser", ["chrome", "firefox"])
def test_report_page_loads_the_shim_and_libraries_before_its_own_script(built, browser):
    html = (built / browser / "report.html").read_text(encoding="utf-8")
    order = [html.index(s) for s in ("vendor/docx.umd.js", "vendor/html2pdf.bundle.min.js", "report-shim.js", 'src="report.js"')]
    assert order == sorted(order)
    assert "window.INK" in (built / browser / "report-shim.js").read_text(encoding="utf-8")
    assert "const INK = window.INK" in (built / browser / "report.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("browser", ["chrome", "firefox"])
def test_every_import_resolves(built, browser):
    root = built / browser
    for js in list(root.glob("*.js")) + list((root / "core").glob("*.js")) + list((root / "content").glob("*.js")):
        text = js.read_text(encoding="utf-8")
        for rel in re.findall(r'''(?:from\s+|import\(\s*|import\s+)["'](\.{1,2}/[^"']+)["']''', text):
            assert (js.parent / rel).resolve().is_file(), f"{js.relative_to(root)} imports missing {rel}"
        for rel in re.findall(r'new URL\(\s*["\'](\./[^"\']+)["\']\s*,\s*import\.meta\.url', text):
            assert (js.parent / rel).is_file(), f"{js.relative_to(root)} refers to missing {rel}"


@pytest.mark.skipif(not NODE, reason="node is not installed")
@pytest.mark.parametrize("browser", ["chrome", "firefox"])
def test_every_script_parses(built, browser, tmp_path):
    root = built / browser
    modules = [root / n for n in ("background.js", "engine.js", "popup.js", "options.js", "offscreen.js") if (root / n).exists()]
    modules += list((root / "core").glob("*.js"))
    classics = [root / "content" / "ink.js", root / "report.js", root / "report-shim.js", root / "capture-worklet.js"]
    for i, f in enumerate(modules):
        copy = tmp_path / f"m{i}.mjs"
        copy.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        r = subprocess.run([NODE, "--check", str(copy)], capture_output=True, text=True)
        assert r.returncode == 0, f"{f.name}: {r.stderr[:400]}"
    for i, f in enumerate(classics):
        copy = tmp_path / f"c{i}.cjs"
        copy.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        r = subprocess.run([NODE, "--check", str(copy)], capture_output=True, text=True)
        assert r.returncode == 0, f"{f.name}: {r.stderr[:400]}"


def test_the_chrome_extension_never_calls_a_domain_other_than_the_two_it_declares(built):
    allowed = {"generativelanguage.googleapis.com", "inkwake-reports.inkwake.workers.dev", "aistudio.google.com", "wa.me", "www.w3.org",
               "inkwake.workers.dev", "github.com", "cdn.jsdelivr.net"}
    root = built / "chrome"
    for js in [*root.glob("*.js"), *(root / "core").glob("*.js"), *(root / "content").glob("*.js")]:
        for host in re.findall(r"https?://([a-z0-9.-]+)", js.read_text(encoding="utf-8")):
            assert host in allowed or host.endswith("example.com"), f"{js.name} contacts {host}"
