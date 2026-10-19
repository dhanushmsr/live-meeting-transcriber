"""'Catch me up' panel: what has been said so far, decisions, action items, open questions."""

from html import escape

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from .capture import hide_from_screen_capture
from .icons import IconButton

STYLE = """
#card { background: #0f172a; border: 1px solid #334155; border-radius: 16px; }
QLabel { color: #e2e8f0; font-size: 13px; }
#title { font-size: 15px; font-weight: 700; color: white; }
#status { color: #38bdf8; font-size: 12px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
#body { background: transparent; }
"""

H = "color:#94a3b8;font-size:11px;font-weight:700;letter-spacing:1px"  # section heading


def _stamp(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def build_html(d: dict) -> str:
    parts: list[str] = []
    if d.get("now"):
        parts.append(f"<p style='{H}'>RIGHT NOW</p><p>{escape(d['now'])}</p>")
    if d.get("gist"):
        parts.append(f"<p style='{H}'>SO FAR</p><p>{escape(d['gist'])}</p>")
    if d.get("decisions"):
        items = "".join(f"<li>{escape(str(x))}</li>" for x in d["decisions"])
        parts.append(f"<p style='{H}'>DECISIONS</p><ul>{items}</ul>")
    if d.get("action_items"):
        items = "".join(
            f"<li>{escape(str(a.get('task', '')))} <span style='color:#a78bfa'>({escape(str(a.get('owner') or '-'))})</span></li>"
            for a in d["action_items"])
        parts.append(f"<p style='{H}'>ACTION ITEMS</p><ul>{items}</ul>")
    if d.get("open_questions"):
        items = "".join(f"<li>{escape(str(x))}</li>" for x in d["open_questions"])
        parts.append(f"<p style='{H}'>OPEN QUESTIONS</p><ul>{items}</ul>")
    recent = d.get("recent") or []
    if recent:
        rows = "".join(
            f"<p style='margin:2px 0'><span style='color:#64748b'>{_stamp(r['t'])}</span> "
            f"<b>{escape(r['speaker'])}:</b> {escape(r['text'])}</p>" for r in recent)
        parts.append(f"<p style='{H}'>LATEST LINES</p>{rows}")
    return "".join(parts) or "<p>Nothing has been said yet.</p>"


class NotesPanel(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self.setFixedSize(420, 520)

        card = QWidget(self)
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)

        head = QHBoxLayout()
        title = QLabel("Catch me up")
        title.setObjectName("title")
        self.status = QLabel("")
        self.status.setObjectName("status")
        close = IconButton("close", "Close")
        close.setFixedSize(34, 34)
        close.clicked.connect(self.hide)
        head.addWidget(title)
        head.addWidget(self.status, 1)
        head.addWidget(close)
        lay.addLayout(head)

        self.body = QLabel()
        self.body.setObjectName("body")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        self.body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(self.body)
        lay.addWidget(scroll)
        self._drag: QPoint | None = None

    def showEvent(self, event) -> None:
        hide_from_screen_capture(self)
        super().showEvent(event)

    def set_notes(self, d: dict) -> None:
        if d.get("updating"):
            self.status.setText("updating...")
        elif d.get("error"):
            self.status.setText("AI notes unavailable (Gemini busy)")
        else:
            self.status.setText("")
        self.body.setText(build_html(d))

    def show_near(self, anchor: QPoint) -> None:
        """Open next to the bubble, kept fully on screen."""
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        x = anchor.x() - self.width() - 30
        if x < g.left():
            x = anchor.x() + 90
        y = min(max(anchor.y() - self.height() // 2, g.top() + 10), g.bottom() - self.height() - 10)
        self.move(max(g.left() + 10, min(x, g.right() - self.width() - 10)), y)
        self.show()
        self.raise_()

    def mousePressEvent(self, e) -> None:  # drag anywhere
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _) -> None:
        self._drag = None
