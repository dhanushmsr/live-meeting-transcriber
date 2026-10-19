"""Settings: simple profile, Gemini key, captions and translation. Icon buttons only."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLineEdit, QMessageBox, QVBoxLayout

from .. import audio
from ..config import DEFAULT_CLOUD_URL, LANGUAGES, Settings
from .icons import IconButton

STYLE = """
QDialog { background: #0f172a; }
QLineEdit, QComboBox { background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
                       border-radius: 8px; padding: 8px 10px; font-size: 14px; }
QComboBox QAbstractItemView { background: #1e293b; color: #e2e8f0; }
QToolTip { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; }
"""


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("Ink")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        self.name = QLineEdit(settings.name)
        self.name.setPlaceholderText("Your name")
        self.email = QLineEdit(settings.email)
        self.email.setPlaceholderText("Email (optional)")
        self.role = QLineEdit(settings.role)
        self.role.setPlaceholderText("Role / team (optional)")
        self.key = QLineEdit(settings.api_key)
        self.key.setPlaceholderText("Gemini API key")
        self.key.setEchoMode(QLineEdit.Password)
        self.lang = QComboBox()
        self.lang.addItems(LANGUAGES)
        self.lang.setCurrentText(settings.target_language)
        self.lang.setToolTip("Translate into this language")

        self.mic = QComboBox()
        self.mic.addItem("System default microphone", "")
        for name in audio.list_microphones():
            self.mic.addItem(name, name)
        i = self.mic.findData(settings.mic_device)
        self.mic.setCurrentIndex(max(i, 0))
        self.mic.setToolTip("Which microphone Ink listens to")

        self.cloud_url = QLineEdit(settings.cloud_report_url)
        self.cloud_url.setPlaceholderText("Cloud Report Server URL (optional, e.g. Cloudflare Worker)")
        self.cloud_url.setToolTip("URL of your 72h Cloudflare Worker report server")

        self.cc = IconButton("cc", "Live captions", checkable=True)
        self.cc.setChecked(settings.captions)
        self.tr = IconButton("translate", "Show captions in the selected language (translate)", checkable=True)
        self.tr.setChecked(settings.translate)
        self.names = IconButton("people", "Name who is speaking (reads names from the meeting screen, and separates voices after the meeting)", checkable=True)
        self.names.setChecked(settings.read_names)
        self.share = IconButton("share", "Share reports online (uploads each finished report to Ink's report server, link works for 72 hours)", checkable=True)
        self.share.setChecked(settings.share_online)
        self.auto = IconButton("eye", "Detect meetings automatically", checkable=True)
        self.auto.setChecked(settings.auto_detect)
        ok = IconButton("check", "Save")
        ok.clicked.connect(self._save)
        no = IconButton("close", "Cancel")
        no.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        fields = [self.name, self.email, self.role]
        if not settings.api_key:  # key/model are only shown until a key is saved
            fields.append(self.key)
        if not DEFAULT_CLOUD_URL:  # with a built-in server there is nothing for the user to configure
            fields.append(self.cloud_url)
        for w in fields:
            lay.addWidget(w)
        lay.addWidget(self.mic)
        toggles = QHBoxLayout()
        toggles.addWidget(self.cc)
        toggles.addWidget(self.tr)
        toggles.addWidget(self.auto)
        toggles.addWidget(self.names)
        toggles.addWidget(self.share)
        toggles.addWidget(self.lang, 1)
        lay.addLayout(toggles)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(no)
        row.addWidget(ok)
        lay.addLayout(row)

    def _confirm_share(self) -> bool:
        """Turning sharing on sends the transcript and summary to a server, so ask once, plainly."""
        box = QMessageBox(self)
        box.setWindowTitle("Share reports online?")
        box.setIcon(QMessageBox.Question)
        box.setText("Each finished report (summary, transcript, names) will be uploaded to Ink's report server so you "
                    "get a link you can send to others. Anyone with the link can read it for 72 hours, then it is "
                    "deleted.\n\nOnly turn this on if everyone in your meetings is fine with that.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        return box.exec() == QMessageBox.Yes

    def _save(self) -> None:
        s = self.s
        s.name, s.email, s.role = self.name.text().strip(), self.email.text().strip(), self.role.text().strip()
        if not s.api_key:
            s.api_key = self.key.text().strip()
        if not DEFAULT_CLOUD_URL:
            s.cloud_report_url = self.cloud_url.text().strip()
        s.captions, s.translate, s.auto_detect = self.cc.isChecked(), self.tr.isChecked(), self.auto.isChecked()
        s.read_names = self.names.isChecked()
        s.mic_device = self.mic.currentData() or ""
        if self.share.isChecked() and not s.share_online and not self._confirm_share():
            self.share.setChecked(False)
        s.share_online = self.share.isChecked()
        s.target_language = self.lang.currentText()
        s.save()
        self.accept()
