"""Wires the bubble, captions, detector and session together."""

import os
import signal
import subprocess
import sys
import webbrowser

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout

from . import detector, localserver
from .gemini import Gemini, stamp
from .config import Settings, cleanup_tmp, reports_dir
from .session import Session
from .ui.bubble import Bubble, logo_pixmap
from .ui.captions import CaptionBar
from .ui.icons import IconButton
from .ui.notes import NotesPanel
from .ui.settings import SettingsDialog


def _open(path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class InkApp:
    def __init__(self, detect: bool = True) -> None:
        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setApplicationName("Ink")
        self.settings = Settings.load()
        cleanup_tmp()
        self.session: Session | None = None
        self._prompted: str | None = None
        self._last_report: str | None = None

        self.bubble = Bubble()
        self.bubble.action.connect(self.on_action)
        self.bubble.moved.connect(self._remember_position)
        self.captions = CaptionBar()
        self.notes_panel = NotesPanel()

        self.tray = QSystemTrayIcon(QIcon(logo_pixmap(64)))
        menu = QMenu()
        for label, fn in (("Settings", self.open_settings), ("Open reports folder", self.open_reports),
                          ("Quit Ink", self.quit)):
            act = QAction(label, menu)
            act.triggered.connect(fn)
            menu.addAction(act)
        self.tray.setContextMenu(menu)
        self.tray.messageClicked.connect(self._open_last_report)
        self.tray.show()

        try:
            localserver.set_asker(self._answer)
            localserver.ensure_started()  # keeps earlier report links working while Ink runs
        except Exception as e:
            print(f"[ink] local report server not started: {e}", file=sys.stderr)
        self._place_bubble()
        self.bubble.show()
        if detect:
            self.poll = QTimer()
            self.poll.timeout.connect(self._poll)
            self.poll.start(5000)
        if not self.settings.api_key:
            QTimer.singleShot(400, self.open_settings)

    # ---- helpers -------------------------------------------------------
    def _place_bubble(self) -> None:
        s = self.settings
        if s.bubble_x >= 0:
            self.bubble.move(s.bubble_x, s.bubble_y)
        else:
            g = QGuiApplication.primaryScreen().availableGeometry()
            self.bubble.move(g.right() - 120, g.center().y())

    def _remember_position(self, x: int, y: int) -> None:
        self.settings.bubble_x, self.settings.bubble_y = x, y
        self.settings.save()

    def _notify(self, msg: str) -> None:
        print(f"[ink] {msg}", file=sys.stderr)
        self.tray.showMessage("Ink", msg, QSystemTrayIcon.Information, 6000)

    def _busy(self) -> bool:
        return self.session is not None and self.session.state != "idle"

    # ---- actions -------------------------------------------------------
    def on_action(self, key: str) -> None:
        if key == "settings":
            self.open_settings()
        elif key == "play":
            if self.session and self.session.state == "paused":
                self.session.resume()
            elif not self._busy():
                self.start_meeting("")
        elif key == "pause" and self.session:
            self.session.pause()
        elif key == "stop" and self.session:
            self.session.stop()
        elif key == "notes" and self._busy() and self.session:
            self.notes_panel.show_near(self.bubble.geometry().center())
            self.session.request_catch_up()
        elif key == "star" and self.session:
            self.session.bookmark()
        elif key == "close":
            self.quit()

    def quit(self) -> None:
        if self.session and self.session.state in ("recording", "paused"):
            self.session.stop()  # builds the report, then we exit
            self.session.state_changed.connect(lambda st: st == "idle" and self.qt.quit())
        elif self._busy():  # report still being written: wait for it
            self.session.state_changed.connect(lambda st: st == "idle" and self.qt.quit())
        else:
            self.qt.quit()

    def open_settings(self) -> None:
        SettingsDialog(self.settings).exec()

    def open_reports(self) -> None:
        folder = reports_dir(self.settings.name)
        folder.mkdir(parents=True, exist_ok=True)
        _open(folder)

    def _open_last_report(self) -> None:
        if self._last_report:
            _open(self._last_report)

    def start_meeting(self, platform: str, title: str = "") -> None:
        self.settings = Settings.load()
        if not self.settings.api_key:
            self.open_settings()
            if not self.settings.api_key:
                return
        self.session = Session(self.settings)
        self.session.state_changed.connect(self._on_state)
        self.session.caption.connect(self._on_caption)
        self.session.notice.connect(self._notify)
        self.session.failed.connect(self._on_failed)
        self.session.finished.connect(self._on_finished)
        self.session.notes_ready.connect(self.notes_panel.set_notes)
        self.session.bookmarked.connect(self._on_bookmark)
        self.session.mention.connect(self._on_mention)
        self.session.start(platform, title)

    # ---- session callbacks ---------------------------------------------
    def _on_state(self, state: str) -> None:
        self.bubble.set_state(state)
        if state != "recording":
            self.captions.clear()
        if state == "idle":
            self.notes_panel.hide()

    def _on_caption(self, speaker: str, text: str, final: bool) -> None:
        if self.settings.captions:
            self.captions.show_caption(speaker, text, final)

    def _on_failed(self, msg: str) -> None:
        self._notify(msg)
        self.bubble.set_state("idle")

    def _on_finished(self, path: str, link: str) -> None:
        """The meeting is over and the report is written: open it in the browser. The folder is never opened."""
        self._last_report = path
        folder = f"Desktop/{os.path.basename(os.path.dirname(path))}"
        if link:
            webbrowser.open(link)
            clipboard = QGuiApplication.clipboard()
            if clipboard:
                clipboard.setText(link)
            self._notify(f"Your report is open in the browser and the link is copied. A .docx copy is in {folder}.")
        else:  # could not make an online report: say so, open nothing, let the notification open the file on click
            self._notify(f"Report saved: {folder}/{os.path.basename(path)} (click to open). The online report could not be made.")

    def _answer(self, record: dict, question: str) -> str:
        """Answer a question about one saved meeting (called by the local report server)."""
        s = Settings.load()
        text = "\n".join(
            f"[{stamp(float(x.get('t', 0)))}] {x.get('speaker', '')}: {x.get('text') or x.get('translation', '')}"
            for x in record.get("transcript", []))
        return Gemini(s.api_key, s.report_model, s.fallback_model).ask(question, text, record.get("analysis"))

    def _on_bookmark(self, t: float) -> None:
        self.bubble.flash("#fbbf24")
        self._notify(f"Moment bookmarked at {int(t // 60):02d}:{int(t % 60):02d}")

    def _on_mention(self, speaker: str, line: str) -> None:
        """Someone said the user's name: make it impossible to miss, and remember it in the report."""
        self.qt.beep()
        self.bubble.flash("#a78bfa", 3.0)
        self._notify(f"{speaker} mentioned you: {line[:120]}")

    # ---- meeting detection & consent -----------------------------------
    def _poll(self) -> None:
        if self._busy() or not Settings.load().auto_detect:
            return
        found = detector.detect()
        if found is None:
            self._prompted = None
        elif found[0] != self._prompted:
            platform, title = found
            self._prompted = platform
            if self._ask_permission(platform):
                self.start_meeting(platform, title)

    def _ask_permission(self, platform: str) -> bool:
        d = QDialog()
        d.setWindowTitle("Ink")
        d.setStyleSheet("QDialog{background:#0f172a;} QLabel{color:#e2e8f0;font-size:14px;}")
        d.setWindowFlag(d.windowFlags().WindowStaysOnTopHint, True)
        lay = QVBoxLayout(d)
        lay.addWidget(QLabel(f"<b>{platform}</b> meeting detected.<br>Let Ink listen, caption and write the report?"))
        note = QLabel("Ink also reads participant names from your screen (turn off in settings)."+chr(10)+"Please let the other participants know you are transcribing.")
        note.setStyleSheet("color:#94a3b8;font-size:12px;")
        lay.addWidget(note)
        row = QHBoxLayout()
        row.addStretch()
        no, yes = IconButton("close", "No"), IconButton("check", "Yes, start")
        no.clicked.connect(d.reject)
        yes.clicked.connect(d.accept)
        row.addWidget(no)
        row.addWidget(yes)
        lay.addLayout(row)
        return d.exec() == QDialog.Accepted

    def run(self) -> int:
        # Ctrl+C in the terminal quits cleanly (Qt needs a periodic tick for Python to see the signal)
        signal.signal(signal.SIGINT, lambda *_: self.qt.quit())
        self._sig_timer = QTimer()
        self._sig_timer.timeout.connect(lambda: None)
        self._sig_timer.start(200)
        return self.qt.exec()


def run(detect: bool = True) -> int:
    return InkApp(detect).run()
