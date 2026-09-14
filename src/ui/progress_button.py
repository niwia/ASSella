import logging
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtGui import QPainter, QColor, QPalette
from PyQt6.QtCore import QRect, QTimer, Qt

logger = logging.getLogger(__name__)


class ProgressButton(QPushButton):
    """A QPushButton that supports rendering progress overlay or indeterminate
    loading animations inside the button itself, keeping text fully visible.
    """
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._progress = 0.0  # float 0.0 to 1.0
        self._is_loading = False
        self._loading_offset = 0
        self._loading_timer = None

    def set_progress(self, progress: float):
        """Set progress between 0.0 and 1.0. Automatically stops loading animation."""
        self._progress = max(0.0, min(1.0, progress))
        self._is_loading = False
        if self._loading_timer:
            self._loading_timer.stop()
            self._loading_timer = None
        self.update()

    def set_loading(self, loading: bool):
        """Start or stop indeterminate loading/pulser animation."""
        from PyQt6.QtGui import QIcon
        self._is_loading = loading
        self._progress = 0.0

        if loading:
            if not hasattr(self, "_saved_icon"):
                self._saved_icon = None
            if self._saved_icon is None and not self.icon().isNull():
                self._saved_icon = self.icon()
                self.setIcon(QIcon())
            self.setEnabled(False)
            if not self._loading_timer:
                self._loading_timer = QTimer(self)
                self._loading_timer.timeout.connect(self._animate_loading)
                self._loading_timer.start(30)
            # 60-second safety timeout fallback so button never gets stuck permanently
            QTimer.singleShot(60000, self._safety_timeout_reset)
        else:
            if getattr(self, "_saved_icon", None) is not None:
                if not self._saved_icon.isNull():
                    self.setIcon(self._saved_icon)
                self._saved_icon = None
            self.setEnabled(True)
            if self._loading_timer:
                self._loading_timer.stop()
                self._loading_timer = None
        self.update()

    def _safety_timeout_reset(self):
        if self._is_loading:
            logger.warning("ProgressButton safety fallback timeout triggered; resetting loading state.")
            self.set_loading(False)


    def _animate_loading(self):
        w = max(1, self.width())
        self._loading_offset = (self._loading_offset + 3) % w
        self.update()

    def mousePressEvent(self, event):
        if self._is_loading or (0.0 < self._progress < 1.0) or not self.isEnabled():
            # Accept event to block it from triggering clicked signal or parent interactions
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        # Paint the standard button style and contents
        super().paintEvent(event)
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        
        if self._progress > 0.0:
            width = int(self.width() * self._progress)
            rect = QRect(0, 0, width, self.height())
            color = QColor(accent.red(), accent.green(), accent.blue(), 60)
            painter.fillRect(rect, color)
            
        elif self._is_loading:
            # Draw a beautiful Material circular progress spinner
            from PyQt6.QtCore import QRectF
            from PyQt6.QtGui import QPen

            is_compact = (not self.text()) or (self.width() <= 48)
            spinner_size = min(16, min(self.width(), self.height()) - 10) if is_compact else 14
            
            if is_compact:
                x = (self.width() - spinner_size) / 2.0
            else:
                x = 12.0
            y = (self.height() - spinner_size) / 2.0
            
            # Use self._loading_offset to animate the rotation angle
            angle = (self._loading_offset * 4) % 360
            
            # Indeterminate breathing arc span: breathes between 90 and 270 degrees
            cycle = (self._loading_offset // 2) % 180
            span = 90 + abs(cycle - 90) * 2
            
            painter.save()
            pen_color = getattr(self, "_spinner_color", None)
            if not pen_color or not pen_color.isValid():
                pen_color = QColor("#FFFFFF")
            pen = QPen(pen_color)
            pen.setWidth(2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            
            rect = QRectF(x, y, spinner_size, spinner_size)
            painter.drawArc(rect, int(angle * 16), int(span * 16))
            painter.restore()

