"""
accent_progress_bar.py - Drop-in animated QProgressBar for ASSella (PyQt6).

Replace `QProgressBar` with `AccentProgressBar` and existing code keeps working
(setRange / setValue / setFormat / valueChanged / reset all behave as before).

What it adds over a QSS-styled QProgressBar:
  * Pill-shaped track and fill, drawn with QPainter (QSS cannot do this).
  * Smooth eased fill when the value jumps (snaps instantly when the value goes
    backwards, so a new download does not slide back from 100%).
  * Soft shimmer sweeping over the fill while work is in progress.
  * Indeterminate mode when the range is (0, 0), like QProgressBar's busy state.
  * Paused state (desaturated, no shimmer) via set_paused().
  * Colours follow the app palette automatically: the accent is the palette's
    Highlight role, which theme.apply_palette() already sets to the accent, so
    a theme change repaints it with no extra wiring.

Animations only run while the widget is visible and there is something to
animate, so an idle or hidden bar costs nothing.

Notes:
  * QProgressBar rules in the app QSS are ignored for this class; it paints
    itself. Use set_accent() to override the palette accent for one bar.
  * invertedAppearance() and setAlignment() are not implemented.
"""

from typing import Optional, Union

from PyQt6.QtCore import QEasingCurve, QEvent, QRectF, QSize, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPalette
from PyQt6.QtWidgets import QProgressBar, QSizePolicy

_SHIMMER_MS = 1500
_FILL_MS = 280


def _grayed(color: QColor, keep: float = 0.15) -> QColor:
    """Return `color` with most of its saturation removed (used for paused)."""
    h, s, l, a = color.getHsl()
    return QColor.fromHsl(max(h, 0), int(s * keep), l, a)


class AccentProgressBar(QProgressBar):
    def __init__(self, parent=None, accent: Optional[Union[str, QColor]] = None):
        super().__init__(parent)
        self._accent_override: Optional[QColor] = QColor(accent) if accent else None
        self._paused = False
        self._animated = True
        self._shown = 0.0  # fraction currently drawn (0..1)
        self._phase = 0.0  # shimmer / busy-segment position (0..1)

        self.setTextVisible(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._fill_anim = QVariantAnimation(self)
        self._fill_anim.setDuration(_FILL_MS)
        self._fill_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fill_anim.valueChanged.connect(self._on_fill_step)

        self._sweep_anim = QVariantAnimation(self)
        self._sweep_anim.setStartValue(0.0)
        self._sweep_anim.setEndValue(1.0)
        self._sweep_anim.setDuration(_SHIMMER_MS)
        self._sweep_anim.setLoopCount(-1)
        self._sweep_anim.valueChanged.connect(self._on_sweep_step)

        self.valueChanged.connect(self._on_value_changed)

    # ------------------------------------------------------------------ API

    def set_accent(self, color: Optional[Union[str, QColor]]) -> None:
        """Override the palette accent for this bar. Pass None to follow the theme."""
        self._accent_override = QColor(color) if color else None
        self.update()

    def accent_color(self) -> QColor:
        if self._accent_override is not None and self._accent_override.isValid():
            return QColor(self._accent_override)
        return self.palette().color(QPalette.ColorRole.Highlight)

    def set_paused(self, paused: bool) -> None:
        if paused != self._paused:
            self._paused = paused
            self._sync_sweep()
            self.update()

    def set_animated(self, animated: bool) -> None:
        """Turn all motion off (for a 'reduce motion' setting)."""
        self._animated = animated
        if not animated:
            self._fill_anim.stop()
            self._shown = self._target()
        self._sync_sweep()
        self.update()

    # Keep the drawn state consistent when the range changes or the bar resets.
    def setRange(self, minimum: int, maximum: int) -> None:
        super().setRange(minimum, maximum)
        self._snap()

    def setMinimum(self, minimum: int) -> None:
        super().setMinimum(minimum)
        self._snap()

    def setMaximum(self, maximum: int) -> None:
        super().setMaximum(maximum)
        self._snap()

    def reset(self) -> None:
        super().reset()
        self._snap()

    # ------------------------------------------------------------- internals

    def _is_busy(self) -> bool:
        return self.maximum() <= self.minimum()

    def _target(self) -> float:
        lo, hi = self.minimum(), self.maximum()
        if hi <= lo:
            return 0.0
        return min(1.0, max(0.0, (self.value() - lo) / (hi - lo)))

    def _snap(self) -> None:
        self._fill_anim.stop()
        self._shown = self._target()
        self._sync_sweep()
        self.update()

    def _on_value_changed(self, _value: int) -> None:
        target = self._target()
        self._fill_anim.stop()
        if not self._animated or not self.isVisible() or target < self._shown:
            self._shown = target
        else:
            self._fill_anim.setStartValue(self._shown)
            self._fill_anim.setEndValue(target)
            self._fill_anim.start()
        self._sync_sweep()
        self.update()

    def _on_fill_step(self, value) -> None:
        self._shown = float(value)
        self.update()

    def _on_sweep_step(self, value) -> None:
        self._phase = float(value)
        self.update()

    def _wants_sweep(self) -> bool:
        if not (self._animated and self.isVisible() and self.isEnabled()) or self._paused:
            return False
        if self._is_busy():
            return True
        return 0.0 < self._target() < 1.0

    def _sync_sweep(self) -> None:
        running = self._sweep_anim.state() == QVariantAnimation.State.Running
        if self._wants_sweep():
            if not running:
                self._sweep_anim.start()
        elif running:
            self._sweep_anim.stop()
            self._phase = 0.0

    # --------------------------------------------------------------- events

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_sweep()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._sweep_anim.stop()
        self._fill_anim.stop()
        self._shown = self._target()
        self._phase = 0.0

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.EnabledChange):
            self._sync_sweep()
            self.update()

    def sizeHint(self) -> QSize:
        if self.isTextVisible():
            return QSize(160, self.fontMetrics().height() + 8)
        return QSize(160, 8)

    def minimumSizeHint(self) -> QSize:
        return QSize(32, 6)

    # --------------------------------------------------------------- paint

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect())
        h = rect.height()
        radius = h / 2.0

        pal = self.palette()
        light_ui = pal.color(QPalette.ColorRole.Window).lightness() > 128
        overlay = QColor(0, 0, 0) if light_ui else QColor(255, 255, 255)

        accent = self.accent_color()
        if not self.isEnabled():
            accent = _grayed(accent, 0.0)
            accent.setAlphaF(0.45)
        elif self._paused:
            accent = _grayed(accent)

        # Track
        track = QColor(overlay)
        track.setAlpha(26 if not light_ui else 22)
        track_path = QPainterPath()
        track_path.addRoundedRect(rect, radius, radius)
        p.fillPath(track_path, track)

        fill_rect = QRectF()
        if self._is_busy():
            self._paint_busy(p, rect, track_path, accent)
        else:
            frac = self._shown
            if frac > 0.0:
                w = max(rect.width() * frac, h)  # keep a full pill at the start
                fill_rect = QRectF(rect.left(), rect.top(), min(w, rect.width()), h)
                self._paint_fill(p, fill_rect, accent)

        if self.isTextVisible() and not self._is_busy():
            self._paint_text(p, rect, fill_rect, accent, overlay)

    def _paint_fill(self, p: QPainter, fill_rect: QRectF, accent: QColor) -> None:
        radius = fill_rect.height() / 2.0
        path = QPainterPath()
        path.addRoundedRect(fill_rect, radius, radius)

        grad = QLinearGradient(fill_rect.left(), 0, fill_rect.right(), 0)
        grad.setColorAt(0.0, accent)
        end = accent.lighter(118)
        end.setAlpha(accent.alpha())
        grad.setColorAt(1.0, end)
        p.fillPath(path, grad)

        if self._sweep_anim.state() == QVariantAnimation.State.Running:
            band = max(40.0, fill_rect.width() * 0.35)
            x = fill_rect.left() - band + (fill_rect.width() + band) * self._phase
            shimmer = QLinearGradient(x, 0, x + band, 0)
            shimmer.setColorAt(0.0, QColor(255, 255, 255, 0))
            shimmer.setColorAt(0.5, QColor(255, 255, 255, 70))
            shimmer.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.save()
            p.setClipPath(path)
            p.fillRect(QRectF(x, fill_rect.top(), band, fill_rect.height()), shimmer)
            p.restore()

    def _paint_busy(self, p: QPainter, rect: QRectF, track_path: QPainterPath, accent: QColor) -> None:
        seg = max(rect.width() * 0.3, rect.height() * 3)
        running = self._sweep_anim.state() == QVariantAnimation.State.Running
        phase = self._phase if running else 0.5  # parked in the middle when paused/disabled
        eased = QEasingCurve(QEasingCurve.Type.InOutSine).valueForProgress(phase)
        x = rect.left() - seg + (rect.width() + seg) * eased
        seg_rect = QRectF(x, rect.top(), seg, rect.height())
        radius = rect.height() / 2.0
        seg_path = QPainterPath()
        seg_path.addRoundedRect(seg_rect, radius, radius)
        p.save()
        p.setClipPath(track_path)
        p.fillPath(seg_path, accent)
        p.restore()

    def _paint_text(self, p: QPainter, rect: QRectF, fill_rect: QRectF, accent: QColor, overlay: QColor) -> None:
        text = self.text()
        if not text:
            return
        p.setFont(self.font())
        ink_on_fill = QColor(18, 18, 20) if accent.lightnessF() > 0.6 else QColor(255, 255, 255)
        ink_on_track = QColor(overlay)
        ink_on_track.setAlpha(190)

        # Same text drawn twice with different clips so it stays readable on both halves.
        p.save()
        p.setClipRect(rect)
        p.setPen(ink_on_track)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()

        if fill_rect.width() > 0:
            p.save()
            p.setClipRect(fill_rect)
            p.setPen(ink_on_fill)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
            p.restore()
