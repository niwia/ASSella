"""
Custom animated switch toggle widget for ASSella.
Provides a modern sideways toggle button with smooth animation and high contrast.
"""

from PyQt6.QtCore import Qt, pyqtProperty, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen
from PyQt6.QtWidgets import QWidget


class SwitchToggle(QWidget):
    """
    Modern sideways animated switch toggle widget.
    Smoothly slides knob between off and on states with cubic easing.
    """
    toggled = pyqtSignal(bool)

    def __init__(
        self,
        checked: bool = False,
        read_only: bool = False,
        active_color: str = "#4CAF50",
        inactive_color: str = "#374151",
        width: int = 42,
        height: int = 22,
        parent=None,
    ):
        super().__init__(parent)
        self._checked = bool(checked)
        self._read_only = bool(read_only)
        self._active_color = QColor(active_color)
        self._inactive_color = QColor(inactive_color)
        self._hovered = False

        self._w = width
        self._h = height
        self.setFixedSize(self._w, self._h)
        self.setCursor(Qt.CursorShape.PointingHandCursor if not self._read_only else Qt.CursorShape.ArrowCursor)

        self._knob_diameter = float(self._h - 6)
        self._min_x = 3.0
        self._max_x = float(self._w - self._knob_diameter - 3.0)

        self._circle_pos = self._max_x if self._checked else self._min_x
        self._anim = QPropertyAnimation(self, b"circle_pos", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    @pyqtProperty(float)
    def circle_pos(self) -> float:
        return self._circle_pos

    @circle_pos.setter
    def circle_pos(self, pos: float) -> None:
        self._circle_pos = pos
        self.update()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, animate: bool = True) -> None:
        checked = bool(checked)
        if self._checked != checked:
            self._checked = checked
            target = self._max_x if checked else self._min_x
            if animate:
                self._anim.stop()
                self._anim.setStartValue(self._circle_pos)
                self._anim.setEndValue(target)
                self._anim.start()
            else:
                self._circle_pos = target
                self.update()
            self.toggled.emit(self._checked)

    def setReadOnly(self, read_only: bool) -> None:
        self._read_only = bool(read_only)
        self.setCursor(Qt.CursorShape.PointingHandCursor if not self._read_only else Qt.CursorShape.ArrowCursor)
        self.update()

    def isReadOnly(self) -> bool:
        return self._read_only

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._read_only:
            self.setChecked(not self._checked)
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if not self._read_only and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            self.setChecked(not self._checked)
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        radius = h / 2.0

        # Background track color
        track_color = self._active_color if self._checked else self._inactive_color
        if self._read_only:
            track_color = QColor(track_color)
            track_color.setAlpha(170)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(track_color))
        p.drawRoundedRect(0, 0, w, h, radius, radius)

        # Hover outline indicator
        if self._hovered and not self._read_only:
            p.setPen(QPen(QColor(255, 255, 255, 140), 1.5))
            p.drawRoundedRect(1, 1, w - 2, h - 2, radius - 1, radius - 1)
        elif not self._checked:
            # Subtle border around unselected track for crisp definition
            p.setPen(QPen(QColor(255, 255, 255, 35), 1.0))
            p.drawRoundedRect(0, 0, w, h, radius, radius)

        # White knob circle with subtle elevation drop
        knob_y = (h - self._knob_diameter) / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.drawEllipse(int(self._circle_pos), int(knob_y), int(self._knob_diameter), int(self._knob_diameter))
        p.end()
