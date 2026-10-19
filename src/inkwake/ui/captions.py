"""Live caption strip shown near the bottom of the screen."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .capture import hide_from_screen_capture


class CaptionBar(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet(
            "background: rgba(15,23,42,215); color: white; border-radius: 14px; padding: 10px 18px;"
            "font-size: 18px;"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.label)
        self.history: list[str] = []          # finished lines
        self.live: dict[str, str] = {}        # speaker -> line still being spoken
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def showEvent(self, event) -> None:
        hide_from_screen_capture(self)
        super().showEvent(event)

    @staticmethod
    def _fmt(speaker: str, text: str) -> str:
        text = text[-170:] if len(text) > 170 else text
        return f"<span style='color:#94a3b8'>{speaker}:</span> {text}"

    def show_caption(self, speaker: str, text: str, final: bool) -> None:
        if final:
            self.live.pop(speaker, None)
            self.history = (self.history + [self._fmt(speaker, text)])[-2:]
        else:
            self.live[speaker] = text
        lines = self.history[-1:] + [self._fmt(sp, t) for sp, t in self.live.items()]
        self.label.setText("<br>".join(lines[-3:]))
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.setFixedWidth(int(screen.width() * 0.6))
        self.adjustSize()
        self.move(screen.center().x() - self.width() // 2, screen.bottom() - self.height() - 60)
        self.show()
        self.hide_timer.start(6000)

    def clear(self) -> None:
        self.history, self.live = [], {}
        self.hide()
