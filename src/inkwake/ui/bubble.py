"""The floating circular "Ink" bubble with a 360-degree radial menu."""

import math
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QToolTip, QWidget

from .icons import COLORS, draw_icon

LOGO = Path(__file__).parent.parent / "assets" / "ink.png"
COLLAPSED, EXPANDED, CORE, BTN, RADIUS = 80, 280, 64, 46, 96

STATE_COLOR = {"idle": "#64748b", "recording": "#ef4444", "paused": "#f59e0b", "processing": "#38bdf8"}

ACTIONS = {
    "idle": [("play", "Start listening"), ("settings", "Settings"), ("close", "Close Ink")],
    "recording": [("pause", "Pause"), ("stop", "Stop and create report"), ("settings", "Settings"), ("close", "Close Ink")],
    "paused": [("play", "Resume"), ("stop", "Stop and create report"), ("settings", "Settings"), ("close", "Close Ink")],
    "processing": [("settings", "Settings"), ("close", "Close Ink (after the report is saved)")],
}


def logo_pixmap(size: int) -> QPixmap:
    pm = QPixmap(str(LOGO)) if LOGO.exists() else QPixmap()
    if not pm.isNull():
        return pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    # fallback until the real image is added at src/inkwake/assets/ink.png
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#1e3a8a"))
    g.setColorAt(1, QColor("#7c3aed"))
    p.fillRect(pm.rect(), g)
    p.setPen(QColor("white"))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(size * 0.34))
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "ink")
    p.end()
    return pm


class Bubble(QWidget):
    action = Signal(str)  # play | pause | stop | settings
    moved = Signal(int, int)

    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.state = "idle"
        self.expanded = False
        self.p = 0.0
        self.phase = 0.0
        self.hover = -1
        self._btns: list[tuple[str, str, QPointF]] = []
        self._press = None
        self._dragged = False
        self._logo = logo_pixmap(CORE * 2)
        self.resize(COLLAPSED, COLLAPSED)

        self.anim = QVariantAnimation(self)
        self.anim.setDuration(560)
        self.anim.valueChanged.connect(self._on_anim)
        self.anim.finished.connect(self._on_anim_done)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # ---- state ---------------------------------------------------------
    def set_state(self, state: str) -> None:
        self.state = state
        if self.expanded:
            self._set_expanded(False)
        self.update()

    def _actions(self):
        return ACTIONS.get(self.state, ACTIONS["idle"])

    def _tick(self) -> None:
        if self.state in ("recording", "processing"):
            self.phase = (self.phase + 0.12) % (2 * math.pi)
            self.update()

    # ---- radial open / close ------------------------------------------
    def _set_expanded(self, flag: bool) -> None:
        if flag == self.expanded:
            return
        c = self.geometry().center()
        if flag:
            self.resize(EXPANDED, EXPANDED)
            self.move(c.x() - EXPANDED // 2, c.y() - EXPANDED // 2)
        self.expanded = flag
        self.anim.stop()
        self.anim.setEasingCurve(QEasingCurve.OutBack if flag else QEasingCurve.InCubic)
        self.anim.setStartValue(self.p)
        self.anim.setEndValue(1.0 if flag else 0.0)
        self.anim.start()

    def _on_anim(self, v) -> None:
        self.p = float(v)
        self.update()

    def _on_anim_done(self) -> None:
        if not self.expanded:
            c = self.geometry().center()
            self.resize(COLLAPSED, COLLAPSED)
            self.move(c.x() - COLLAPSED // 2, c.y() - COLLAPSED // 2)

    # ---- painting ------------------------------------------------------
    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        acts = self._actions()
        n = len(acts)
        self._btns = []
        if self.p > 0.001:
            for i, (key, tip) in enumerate(acts):
                # each icon travels a full 360 degrees while flying out from the core
                ang = math.radians(-90 + i * 360 / n + (1 - self.p) * 360)
                pos = QPointF(c.x() + math.cos(ang) * RADIUS * self.p, c.y() + math.sin(ang) * RADIUS * self.p)
                self._btns.append((key, tip, pos))
                p.setOpacity(max(0.0, min(1.0, self.p * 1.5)))
                hot = i == self.hover
                r = BTN / 2 + (3 if hot else 0)
                p.setPen(QPen(QColor(COLORS[key]), 2))
                p.setBrush(QColor("#0f172a" if not hot else "#1e293b"))
                p.drawEllipse(pos, r, r)
                draw_icon(p, key, QRectF(pos.x() - BTN / 2, pos.y() - BTN / 2, BTN, BTN), QColor(COLORS[key]))
            p.setOpacity(1.0)

        # soft shadow + core
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(c + QPointF(0, 2), CORE / 2 + 3, CORE / 2 + 3)
        clip = QPainterPath()
        clip.addEllipse(c, CORE / 2, CORE / 2)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(c.x() - CORE / 2, c.y() - CORE / 2, CORE, CORE), QColor("white"))
        pad = CORE * 0.04
        p.drawPixmap(QRectF(c.x() - CORE / 2 + pad, c.y() - CORE / 2 + pad, CORE - 2 * pad, CORE - 2 * pad),
                     self._logo, QRectF(self._logo.rect()))
        p.restore()

        ring = QColor(STATE_COLOR.get(self.state, "#64748b"))
        if self.state == "recording":
            ring.setAlphaF(0.55 + 0.45 * (0.5 + 0.5 * math.sin(self.phase)))
        pen = QPen(ring, 4)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if self.state == "processing":  # spinning arc
            p.drawArc(QRectF(c.x() - CORE / 2 - 2, c.y() - CORE / 2 - 2, CORE + 4, CORE + 4),
                      int(-self.phase * 180 / math.pi * 16), 100 * 16)
        else:
            p.drawEllipse(c, CORE / 2 + 1, CORE / 2 + 1)

    # ---- mouse ---------------------------------------------------------
    def _hit_button(self, pos: QPointF) -> int:
        if self.p < 0.6:
            return -1
        for i, (_, _, bp) in enumerate(self._btns):
            if math.hypot(pos.x() - bp.x(), pos.y() - bp.y()) <= BTN / 2 + 3:
                return i
        return -1

    def _on_core(self, pos: QPointF) -> bool:
        return math.hypot(pos.x() - self.width() / 2, pos.y() - self.height() / 2) <= CORE / 2 + 4

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_global = e.globalPosition().toPoint()
            self._dragged = False

    def mouseMoveEvent(self, e) -> None:
        if self._press is not None and e.buttons() & Qt.LeftButton:
            g = e.globalPosition().toPoint()
            if (g - self._press_global).manhattanLength() > 5 and self._on_core_at(self._press_global):
                self._dragged = True
            if self._dragged:
                self.move(g - self._press)
                return
        i = self._hit_button(e.position())
        if i != self.hover:
            self.hover = i
            self.update()
            if i >= 0:
                QToolTip.showText(e.globalPosition().toPoint(), self._btns[i][1], self)
            else:
                QToolTip.hideText()
        self.setCursor(Qt.PointingHandCursor if i >= 0 or self._on_core(e.position()) else Qt.ArrowCursor)

    def _on_core_at(self, global_pos) -> bool:
        return self._on_core(QPointF(self.mapFromGlobal(global_pos)))

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.LeftButton or self._press is None:
            return
        self._press = None
        if self._dragged:
            self.moved.emit(self.x(), self.y())
            return
        i = self._hit_button(e.position())
        if i >= 0:
            key = self._btns[i][0]
            self._set_expanded(False)
            self.action.emit(key)
        elif self._on_core(e.position()):
            self._set_expanded(not self.expanded)

    def leaveEvent(self, _) -> None:
        self.hover = -1
        self.update()
