"""
themed_progress_dialog.py - ASSella custom-styled frameless progress dialog.
Replaces native OS QProgressDialog with ASSella dark theme and DialogTitleBar.
"""

import logging
from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QPoint
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QWidget,
    QFrame,
)

from ui.dialog_titlebar import DialogTitleBar
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
        self.setMinimumWidth(380)

        settings = get_settings()
        self.accent_color = settings.value("accent_color", "#a1c9fd")
        self.background_color = settings.value("background_color", "#111318")

        self._build_ui(labelText, cancelButtonText, minimum, maximum)

    def _build_ui(self, label_text: str, cancel_text: Optional[str], minimum: int, maximum: int):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        # Outer Container with border and shadow effect
        self.container = QFrame(self)
        self.container.setObjectName("dialogContainer")
        self.container.setStyleSheet(f"""
            QFrame#dialogContainer {{
                background-color: {self.background_color};
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }}
        """)

        container_lay = QVBoxLayout(self.container)
        container_lay.setContentsMargins(0, 0, 0, 12)
        container_lay.setSpacing(12)

        # In-App Title Bar (No native decorations)
        self.title_bar = DialogTitleBar(
            self,
            title="Progress",
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
        )
        container_lay.addWidget(self.title_bar)

        # Body area
        body_lay = QVBoxLayout()
        body_lay.setContentsMargins(16, 4, 16, 4)
        body_lay.setSpacing(10)

        self.label = QLabel(label_text, self)
        self.label.setWordWrap(True)
        self.label.setStyleSheet("color: rgba(255, 255, 255, 0.90); font-size: 9pt;")
        body_lay.addWidget(self.label)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(minimum, maximum)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255, 255, 255, 0.08);
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background-color: {self.accent_color};
                border-radius: 4px;
            }}
        """)
        body_lay.addWidget(self.progress_bar)

        # Button layout
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.cancel_btn = QPushButton(cancel_text or "Cancel", self)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setFixedHeight(28)
        self.cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                color: #e5e7eb;
                font-size: 8.5pt;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.12);
                color: #ffffff;
                border-color: rgba(255, 255, 255, 0.25);
            }}
        """)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self.cancel_btn)

        if not cancel_text:
            self.cancel_btn.setVisible(False)

        body_lay.addLayout(btn_row)
        container_lay.addLayout(body_lay)
        root.addWidget(self.container)

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
        if self.title_bar and hasattr(self.title_bar, "title_label"):
            short_title = text.split("...")[0].strip()
            if len(short_title) > 28:
                short_title = short_title[:25] + "..."
            self.title_bar.title_label.setText(short_title)

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

    @pyqtSlot(int)
    def setValue(self, value: int):
        self.progress_bar.setValue(value)

    @pyqtSlot()
    def finish(self):
        """Close the dialog through a queued call from a worker thread."""
        self.accept()

    def setMaximum(self, maximum: int):
        self.progress_bar.setMaximum(maximum)

    def setMinimum(self, minimum: int):
        self.progress_bar.setMinimum(minimum)

    def setMinimumDuration(self, ms: int):
        pass  # Kept for QProgressDialog compatibility

    def setWindowTitle(self, title: str):
        """Show the caller's title in the in-app bar, since there are no decorations."""
        super().setWindowTitle(title)
        if title and self.title_bar and hasattr(self.title_bar, "title_label"):
            self.title_bar.title_label.setText(title)

    def wasCanceled(self) -> bool:
        return self._was_canceled

    def cancel(self):
        self._was_canceled = True
        self.canceled.emit()
        self.reject()

    def _on_cancel_clicked(self):
        self.cancel()
