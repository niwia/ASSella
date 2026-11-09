"""
Lightweight, high-performance 60 FPS snow particle simulation for ASSella.
Provides atmospheric falling snow with depth layering, subtle horizontal sway,
and zero-overhead automatic sleep when widgets are hidden or minimized.
"""

from __future__ import annotations

import math
import random
import time
from typing import List, Optional

from PyQt6.QtCore import QObject, QTimer, Qt
from PyQt6.QtGui import QColor, QPainter, QBrush


class SnowFlake:
    __slots__ = ("x", "y", "radius", "speed_y", "drift_amp", "drift_freq", "drift_phase", "alpha")

    def __init__(self, w: float, h: float, layer: int):
        self.x = random.uniform(0, max(w, 100))
        self.y = random.uniform(0, max(h, 100))
        self.drift_phase = random.uniform(0, math.tau)
        self.drift_freq = random.uniform(0.8, 1.8)

        if layer == 0:
            # Background layer: distant, fine flakes falling steadily
            self.radius = random.uniform(1.0, 1.6)
            self.speed_y = random.uniform(28.0, 48.0)
            self.drift_amp = random.uniform(8.0, 16.0)
            self.alpha = random.randint(90, 140)
        elif layer == 1:
            # Midground layer: soft flakes drifting with noticeable sway
            self.radius = random.uniform(1.8, 2.4)
            self.speed_y = random.uniform(42.0, 68.0)
            self.drift_amp = random.uniform(18.0, 32.0)
            self.alpha = random.randint(150, 205)
        else:
            # Foreground layer: prominent soft snow puffs
            self.radius = random.uniform(2.8, 3.6)
            self.speed_y = random.uniform(60.0, 95.0)
            self.drift_amp = random.uniform(25.0, 45.0)
            self.alpha = random.randint(210, 255)


class SnowEngine(QObject):
    """
    Renders continuous silky-smooth snowfall over any QWidget's paintEvent.
    """

    def __init__(self, target_widget, flake_count: int = 75, parent: Optional[QObject] = None):
        super().__init__(parent or target_widget)
        self._target = target_widget
        self._flake_count = max(20, min(flake_count, 150))
        self._flakes: List[SnowFlake] = []
        self._last_w = 0.0
        self._last_h = 0.0
        self._last_time = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60 FPS
        self._timer.timeout.connect(self._on_tick)
        self._running = False

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._last_time = time.perf_counter()
            self._timer.start()
            if self._target:
                self._target.update()

    def stop(self) -> None:
        if self._running:
            self._running = False
            self._timer.stop()
            if self._target:
                self._target.update()

    def is_running(self) -> bool:
        return self._running

    def _ensure_flakes(self, w: float, h: float) -> None:
        if len(self._flakes) == self._flake_count and abs(w - self._last_w) < 40 and abs(h - self._last_h) < 40:
            return

        self._last_w = w
        self._last_h = h
        self._flakes.clear()

        # Distribute across 3 depth planes: 40% background, 45% midground, 15% foreground
        bg_count = int(self._flake_count * 0.40)
        mid_count = int(self._flake_count * 0.45)
        fg_count = self._flake_count - bg_count - mid_count

        for _ in range(bg_count):
            self._flakes.append(SnowFlake(w, h, layer=0))
        for _ in range(mid_count):
            self._flakes.append(SnowFlake(w, h, layer=1))
        for _ in range(fg_count):
            self._flakes.append(SnowFlake(w, h, layer=2))

    def _on_tick(self) -> None:
        if not self._running or not self._target:
            return
        if not self._target.isVisible() or self._target.isMinimized():
            return
        self._target.update()

    def paint(self, painter: QPainter, w: float, h: float) -> None:
        """Draw and advance snowfall inside widget paintEvent."""
        if w <= 0 or h <= 0:
            return

        self._ensure_flakes(w, h)

        now = time.perf_counter()
        dt = (now - self._last_time) if self._last_time > 0 else 0.016
        self._last_time = now

        # Clamp dt to prevent jumping during sudden wakeups or stalls
        dt = min(0.08, max(0.001, dt))

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        for flake in self._flakes:
            # Update position
            flake.y += flake.speed_y * dt
            flake.drift_phase = (flake.drift_phase + flake.drift_freq * dt) % math.tau
            sway_x = flake.x + math.sin(flake.drift_phase) * flake.drift_amp

            # Wrap around boundaries
            if flake.y > h + flake.radius:
                flake.y = -flake.radius * 2
                flake.x = random.uniform(0, w)
            if sway_x < -flake.radius:
                sway_x = w + flake.radius
                flake.x = w
            elif sway_x > w + flake.radius:
                sway_x = -flake.radius
                flake.x = 0

            painter.setBrush(QBrush(QColor(255, 255, 255, flake.alpha)))
            painter.drawEllipse(
                int(sway_x - flake.radius),
                int(flake.y - flake.radius),
                int(flake.radius * 2),
                int(flake.radius * 2),
            )

        painter.restore()
