"""
pixel_loader_overlay.py - ASSella modern pixel-grid loading overlay for long-running tasks.
Directly ports the React design from loading-state.json to PyQt6:
- 3x3 pixel grid with animated chevron wavefront (Drive variant, 650ms cycle)
- Shimmering/pulsing status text
- Monospace tabular elapsed timer (tenths of a second)
- Sleek translucent rounded card overlay
"""

import time
from typing import Optional
from PyQt6.QtCore import Qt, QTimer, QRectF, QPoint
from PyQt6.QtGui import QColor, QPainter, QFont, QPen
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QHBoxLayout,
    QVBoxLayout,
    QFrame,
    QApplication,
)

# Chevron wavefront delays (ms) for a 3x3 grid (90ms step per wavefront diagonal)
# row r (0..2), col c (0..2) -> (c + abs(r - 1)) * 90
CHEVRON_DELAYS = [
    (c + abs(r - 1)) * 90
    for r in range(3)
    for c in range(3)
]
CYCLE_DURATION_MS = 650


class PixelGridWidget(QWidget):
    """
    3x3 pixel grid with continuous wavefront animation matching loading-state.json.
    """

    def __init__(self, parent=None, accent_color: str = "#a1c9fd"):
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self.accent_color = QColor(accent_color)
        self.start_time = time.time()

        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 FPS
        self._timer.timeout.connect(self.update)
        self._timer.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        elapsed_ms = int((time.time() - self.start_time) * 1000)

        cell_size = 5.0
        gap = 2.5
        corner_radius = 1.2

        for i, delay in enumerate(CHEVRON_DELAYS):
            r = i // 3
            c = i % 3

            # Calculate phase in the 650ms cycle
            t = (elapsed_ms - delay) % CYCLE_DURATION_MS
            progress = t / CYCLE_DURATION_MS  # 0.0 to 1.0

            # Sine pulse for smooth wavefront glow
            import math
            pulse = math.sin(progress * math.pi)
            opacity = 0.15 + (0.85 * max(0.0, pulse))

            col = QColor(self.accent_color)
            col.setAlphaF(opacity)

            x = c * (cell_size + gap) + 2
            y = r * (cell_size + gap) + 2

            painter.setBrush(col)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(x, y, cell_size, cell_size), corner_radius, corner_radius)


class SpinnerRingWidget(QWidget):
    """Sleek circular spinner ring with a subtle track and smooth animated arc."""

    def __init__(self, parent=None, size: int = 22, accent_color: str = "#a1c9fd", thickness: float = 2.5):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.accent_color = QColor(accent_color)
        self.thickness = thickness
        self.angle = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60 FPS
        self._timer.timeout.connect(self._rotate)
        self._timer.start()

    def set_color(self, hex_color: str):
        self.accent_color = QColor(hex_color)
        self.update()

    def _rotate(self):
        self.angle = (self.angle + 4.5) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect_size = self.width() - self.thickness - 2
        rect = QRectF(
            self.thickness / 2.0 + 1,
            self.thickness / 2.0 + 1,
            rect_size,
            rect_size,
        )

        # 1. Subtle background track ring
        track_color = QColor(255, 255, 255, 30)
        track_pen = QPen(track_color, self.thickness)
        painter.setPen(track_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        # 2. Breathing animated arc
        cycle = (int(self.angle) // 2) % 180
        span = 80 + abs(cycle - 90) * 1.8

        arc_pen = QPen(self.accent_color, self.thickness)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        painter.drawArc(rect, int(self.angle * 16), int(span * 16))


class PixelLoadingOverlay(QWidget):
    """
    Floating modern card overlay showing the circular spinner ring, status message,
    and live elapsed timer in monospace tabular numbers.
    """

    def __init__(self, parent=None, label: str = "Discovering DLCs...", accent_color: str = "#a1c9fd"):
        super().__init__(parent)
        self.accent_color = accent_color
        self._start_time = time.time()

        self.setObjectName("PixelLoadingOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.SubWindow)

        # Root layout
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Card Frame
        self.card = QFrame(self)
        self.card.setStyleSheet("""
            QFrame {
                background-color: rgba(18, 19, 24, 0.94);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 10px;
            }
        """)

        card_lay = QHBoxLayout(self.card)
        card_lay.setContentsMargins(14, 10, 16, 10)
        card_lay.setSpacing(10)

        # 1. Circular Spinner Ring Widget
        self.spinner_widget = SpinnerRingWidget(self.card, size=22, accent_color=self.accent_color)
        self.grid_widget = self.spinner_widget  # backwards-compatible attribute
        card_lay.addWidget(self.spinner_widget)

        # 2. Status Label (shimmering text)
        self.status_lbl = QLabel(label, self.card)
        self.status_lbl.setStyleSheet("""
            color: #FFFFFF;
            font-size: 9pt;
            font-weight: 500;
            border: none;
            background: transparent;
        """)
        card_lay.addWidget(self.status_lbl)

        # 3. Monospace Elapsed Timer
        self.timer_lbl = QLabel("0.0s", self.card)
        self.timer_lbl.setStyleSheet("""
            color: rgba(255, 255, 255, 0.5);
            font-family: monospace;
            font-size: 8.5pt;
            border: none;
            background: transparent;
        """)
        card_lay.addWidget(self.timer_lbl)

        root_lay.addWidget(self.card)

        # Timer to tick elapsed time
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(100)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()

    def set_label(self, text: str):
        self.status_lbl.setText(text)
        self.adjustSize()
        self._center_on_parent()

    def _update_clock(self):
        elapsed = time.time() - self._start_time
        if elapsed < 60:
            self.timer_lbl.setText(f"{elapsed:.1f}s")
        else:
            m = int(elapsed // 60)
            s = elapsed % 60
            self.timer_lbl.setText(f"{m}m {s:.1f}s")

    def show_centered(self):
        self.adjustSize()
        self._center_on_parent()
        self.show()
        self.raise_()
        QApplication.processEvents()

    def _center_on_parent(self):
        parent = self.parentWidget()
        if parent:
            pw = parent.width()
            ph = parent.height()
            w = self.width()
            h = self.height()
            self.move(int((pw - w) / 2), int((ph - h) / 2))
