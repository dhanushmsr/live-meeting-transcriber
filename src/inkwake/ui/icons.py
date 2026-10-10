"""Icons drawn with QPainter, so the app needs no icon files."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton

COLORS = {
    "play": "#22c55e", "pause": "#f59e0b", "stop": "#ef4444", "settings": "#94a3b8",
    "cc": "#38bdf8", "translate": "#a78bfa", "eye": "#2dd4bf", "check": "#22c55e", "close": "#ef4444", "quit": "#f87171", "people": "#fbbf24",
}


def draw_icon(p: QPainter, kind: str, r: QRectF, color: QColor) -> None:
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    s = r.width()
    c = r.center()
    pen = QPen(color, s * 0.09)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if kind == "play":
        path = QPainterPath()
        path.moveTo(c.x() - s * 0.16, c.y() - s * 0.24)
        path.lineTo(c.x() + s * 0.26, c.y())
        path.lineTo(c.x() - s * 0.16, c.y() + s * 0.24)
        path.closeSubpath()
        p.setBrush(color)
        p.drawPath(path)
    elif kind == "pause":
        p.setBrush(color)
        for dx in (-0.14, 0.14):
            p.drawRoundedRect(QRectF(c.x() + s * dx - s * 0.07, c.y() - s * 0.22, s * 0.14, s * 0.44), 2, 2)
    elif kind == "stop":
        p.setBrush(color)
        p.drawRoundedRect(QRectF(c.x() - s * 0.2, c.y() - s * 0.2, s * 0.4, s * 0.4), s * 0.05, s * 0.05)
    elif kind == "settings":
        p.drawEllipse(c, s * 0.2, s * 0.2)
        p.drawEllipse(c, s * 0.07, s * 0.07)
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(c.x() + math.cos(a) * s * 0.26, c.y() + math.sin(a) * s * 0.26),
                       QPointF(c.x() + math.cos(a) * s * 0.35, c.y() + math.sin(a) * s * 0.35))
    elif kind == "cc":
        p.drawRoundedRect(QRectF(c.x() - s * 0.34, c.y() - s * 0.24, s * 0.68, s * 0.48), s * 0.09, s * 0.09)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(max(8, int(s * 0.25)))
        p.setFont(f)
        p.drawText(QRectF(c.x() - s * 0.34, c.y() - s * 0.24, s * 0.68, s * 0.48), Qt.AlignCenter, "CC")
    elif kind == "translate":
        p.drawEllipse(c, s * 0.3, s * 0.3)
        p.drawEllipse(c, s * 0.12, s * 0.3)
        p.drawLine(QPointF(c.x() - s * 0.3, c.y()), QPointF(c.x() + s * 0.3, c.y()))
    elif kind == "eye":
        p.drawEllipse(c, s * 0.34, s * 0.19)
        p.setBrush(color)
        p.drawEllipse(c, s * 0.09, s * 0.09)
    elif kind == "quit":  # power symbol
        p.drawArc(QRectF(c.x() - s * 0.24, c.y() - s * 0.2, s * 0.48, s * 0.48), 120 * 16, -300 * 16)
        p.drawLine(QPointF(c.x(), c.y() - s * 0.3), QPointF(c.x(), c.y() - s * 0.02))
    elif kind == "people":
        p.drawEllipse(QPointF(c.x(), c.y() - s * 0.1), s * 0.1, s * 0.1)
        p.drawArc(QRectF(c.x() - s * 0.2, c.y() + s * 0.04, s * 0.4, s * 0.34), 0, 180 * 16)
    elif kind == "check":
        p.drawPolyline([QPointF(c.x() - s * 0.2, c.y()), QPointF(c.x() - s * 0.05, c.y() + s * 0.16),
                        QPointF(c.x() + s * 0.22, c.y() - s * 0.15)])
    elif kind == "close":
        d = s * 0.17
        p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        p.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))
    p.restore()


class IconButton(QAbstractButton):
    """Round, icon-only button. Checkable ones glow when on."""

    def __init__(self, kind: str, tip: str, checkable: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(44, 44)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked() or not self.isCheckable()
        col = QColor(COLORS.get(self.kind, "#94a3b8"))
        bg = QColor(col) if self.isChecked() else QColor("#334155")
        bg.setAlpha(60 if self.isChecked() else (230 if self.underMouse() else 190))
        p.setPen(QPen(col if self.isChecked() else QColor("#475569"), 1.5))
        p.setBrush(bg)
        p.drawEllipse(self.rect().adjusted(2, 2, -2, -2))
        draw_icon(p, self.kind, QRectF(self.rect()), col if on else QColor("#94a3b8"))
