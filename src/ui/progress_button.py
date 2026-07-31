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
        self._is_loading = loading
        self._progress = 0.0
        if loading:
            if not self._loading_timer:
                self._loading_timer = QTimer(self)
                self._loading_timer.timeout.connect(self._animate_loading)
                self._loading_timer.start(30)
            # 20-second safety timeout fallback so button never gets stuck permanently
            QTimer.singleShot(20000, self._safety_timeout_reset)
        else:
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
        if self._is_loading or (0.0 < self._progress < 1.0):
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
            width = 60
            x = self._loading_offset
            rect = QRect(x - width, 0, width, self.height())
            color = QColor(accent.red(), accent.green(), accent.blue(), 45)
            painter.setClipRect(self.rect())
            painter.fillRect(rect, color)
            
            # Wrap around support
            if x > self.width():
                rect2 = QRect(x - self.width() - width, 0, width, self.height())
                painter.fillRect(rect2, color)

