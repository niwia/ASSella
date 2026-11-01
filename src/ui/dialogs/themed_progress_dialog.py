"""
themed_progress_dialog.py - ASSella's shared progress dialog.

A small floating card in the same style as the library's "Discovering DLCs"
overlay (spinner, status, elapsed time, optional Cancel, thin bar when the
work has a known length). Drop-in replacement for QProgressDialog.
"""

import logging
from typing import Optional
import time

from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
    QFrame,
)

from utils.settings import get_settings

logger = logging.getLogger(__name__)


class ThemedProgressDialog(QDialog):
    """
    Frameless, theme-aware progress dialog matching ASSella's visual language.
    Drop-in replacement for QProgressDialog.
    """

    canceled = pyqtSignal()

    def __init__(
        self,
        labelText: str = "",
        cancelButtonText: Optional[str] = None,
        minimum: int = 0,
        maximum: int = 0,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._was_canceled = False
        self._drag_pos: Optional[QPoint] = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(420)

        settings = get_settings()
        self.accent_color = settings.value("accent_color", "#a1c9fd")
        self.background_color = settings.value("background_color", "#111318")

        self._build_ui(labelText, cancelButtonText, minimum, maximum)

    def _build_ui(self, label_text: str, cancel_text: Optional[str], minimum: int, maximum: int):
        """Minimal floating card, same look as the "Discovering DLCs" overlay:

            ( spinner )  Status text ...            12.4s   Cancel
                         ━━━━━━━━━━━━━━━━━━━━━━━━  (determinate only)
        """
        from ui.widgets.accent_progress_bar import AccentProgressBar
        from ui.widgets.pixel_loader_overlay import SpinnerRingWidget

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        self.container = QFrame(self)
        self.container.setObjectName("progressCard")
        self.container.setStyleSheet("""
            QFrame#progressCard {
                background-color: rgba(18, 19, 24, 0.96);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 10px;
            }
        """)
        card = QVBoxLayout(self.container)
        card.setContentsMargins(14, 11, 12, 11)
        card.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.spinner = SpinnerRingWidget(self.container, size=22, accent_color=self.accent_color)
        row.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)

        self.label = QLabel(label_text, self.container)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(440)
        self.label.setStyleSheet(
            "color: #FFFFFF; font-size: 9pt; font-weight: 500; border: none; background: transparent;"
        )
        row.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)

        self.timer_lbl = QLabel("0.0s", self.container)
        self.timer_lbl.setStyleSheet(
            "color: rgba(255, 255, 255, 0.5); font-family: monospace; font-size: 8.5pt;"
            " border: none; background: transparent;"
        )
        row.addWidget(self.timer_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.cancel_btn = QPushButton(cancel_text or "Cancel", self.container)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setFixedHeight(24)
        self.cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 5px;
                color: rgba(255, 255, 255, 0.75);
                font-size: 8pt;
                padding: 0 10px;
            }}
            QPushButton:hover {{ color: #ffffff; border-color: {self.accent_color}; }}
        """)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.cancel_btn.setVisible(bool(cancel_text))
        row.addWidget(self.cancel_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        card.addLayout(row)

        # Thin bar for determinate work; indeterminate work is the spinner alone.
        self.progress_bar = AccentProgressBar(self.container, accent=self.accent_color)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setRange(minimum, maximum)
        card.addWidget(self.progress_bar)
        self._sync_bar()

        root.addWidget(self.container)
        self.title_bar = None  # kept for callers that probe it

        self._started = time.monotonic()
        self._clock = QTimer(self)
        self._clock.setInterval(100)
        self._clock.timeout.connect(self._tick)

    def _sync_bar(self):
        self.progress_bar.setVisible(self.progress_bar.maximum() > self.progress_bar.minimum())

    def _tick(self):
        e = time.monotonic() - self._started
        self.timer_lbl.setText(f"{e:.1f}s" if e < 60 else f"{int(e // 60)}m {e % 60:04.1f}s")

    def showEvent(self, event):
        super().showEvent(event)
        self._started = time.monotonic()
        self._clock.start()
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            # Float over the middle of the window that started the work.
            center = parent.window().frameGeometry().center()
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._clock.stop()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ─────────────────────────────────────────────────────────────────────────
    # QProgressDialog Compatible Interface
    # ─────────────────────────────────────────────────────────────────────────

    @pyqtSlot(str)
    def setLabelText(self, text: str):
        self.label.setText(text)
        self.adjustSize()

    def setCancelButtonText(self, text: str):
        if text:
            self.cancel_btn.setText(text)
            self.cancel_btn.setVisible(True)
        else:
            self.cancel_btn.setVisible(False)

    def setCancelButton(self, btn):
        if btn is None:
            self.cancel_btn.setVisible(False)

    def setRange(self, minimum: int, maximum: int):
        self.progress_bar.setRange(minimum, maximum)
        self._sync_bar()

    @pyqtSlot(int)
    def setValue(self, value: int):
        self.progress_bar.setValue(value)

    @pyqtSlot()
    def finish(self):
        """Close the dialog through a queued call from a worker thread."""
        self.accept()

    def setMaximum(self, maximum: int):
        self.progress_bar.setMaximum(maximum)
        self._sync_bar()

    def setMinimum(self, minimum: int):
        self.progress_bar.setMinimum(minimum)

    def setMinimumDuration(self, ms: int):
        pass  # Kept for QProgressDialog compatibility

    def setWindowTitle(self, title: str):
        """Kept for the taskbar / accessibility; the card itself shows only the status."""
        super().setWindowTitle(title)

    def wasCanceled(self) -> bool:
        return self._was_canceled

    def cancel(self):
        self._was_canceled = True
        self.canceled.emit()
        self.reject()

    def _on_cancel_clicked(self):
        self.cancel()
