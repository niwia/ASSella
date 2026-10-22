"""
themed_input_dialog.py - ASSella's own one-question dialog.

QInputDialog draws the desktop's dialog, so asking for one line of text or one
choice shows a window that does not look like the rest of the app. This module
is the themed replacement, framed by the same DialogTitleBar as every other
ASSella window.

    ThemedInputDialog.getText(parent, title, label, text="")   -> (text, ok)
    ThemedInputDialog.getItem(parent, title, label, items, current=0)
"""

import logging
from typing import List, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.dialog_titlebar import DialogTitleBar
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class ThemedInputDialog(QDialog):
    """Frameless dialog carrying one text field or one combo box."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        title: str = "",
        label: str = "",
        text: str = "",
        items: Optional[Sequence[str]] = None,
        editable: bool = False,
    ):
        super().__init__(parent)
        self._combo_mode = items is not None

        settings = get_settings()
        self.accent_color = settings.value("accent_color", "#a1c9fd")
        self.background_color = settings.value("background_color", "#111318")

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(420)

        self._build_ui(title, label, text, items, editable)

    def _build_ui(self, title, label, text, items, editable):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        self.container = QFrame(self)
        self.container.setObjectName("dialogContainer")
        self.container.setStyleSheet(f"""
            QFrame#dialogContainer {{
                background-color: {self.background_color};
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }}
            QLabel#inputLabel {{
                color: #e5e7eb;
                font-size: 9.5pt;
            }}
            QLineEdit, QComboBox {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                color: #e5e7eb;
                padding: 5px 8px;
                min-height: 24px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {self.background_color};
                color: #e5e7eb;
                selection-background-color: {self.accent_color};
                selection-color: #0b0d12;
            }}
        """)

        lay = QVBoxLayout(self.container)
        lay.setContentsMargins(0, 0, 0, 12)
        lay.setSpacing(10)

        self.title_bar = DialogTitleBar(
            self,
            title=title,
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
        )
        lay.addWidget(self.title_bar)

        body = QVBoxLayout()
        body.setContentsMargins(16, 4, 16, 4)
        body.setSpacing(8)

        self.label = QLabel(label, self)
        self.label.setObjectName("inputLabel")
        self.label.setWordWrap(True)
        body.addWidget(self.label)

        if self._combo_mode:
            self.field = QComboBox(self)
            self.field.addItems(list(items or []))
            self.field.setEditable(editable)
            if text:
                self.field.setCurrentText(text)
        else:
            self.field = QLineEdit(self)
            self.field.setText(text)
            self.field.selectAll()
        body.addWidget(self.field)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button = QPushButton("OK", self)
        self.ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_button.clicked.connect(self.accept)
        self.ok_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: #0b0d12;
                border: none;
                border-radius: 5px;
                font-size: 8.5pt;
                padding: 5px 16px;
            }}
            QPushButton:hover {{ background-color: #ffffff; }}
        """)
        self.cancel_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                color: #e5e7eb;
                font-size: 8.5pt;
                padding: 5px 16px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.12); color: #ffffff; }
        """)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.ok_button)
        body.addLayout(buttons)

        lay.addLayout(body)
        root.addWidget(self.container)
        self.field.setFocus()

    # ─────────────────────────────────────────────────────────────────────────
    # QInputDialog compatible interface
    # ─────────────────────────────────────────────────────────────────────────

    def textValue(self) -> str:
        if self._combo_mode:
            return self.field.currentText()
        return self.field.text()

    def setTextValue(self, text: str):
        if self._combo_mode:
            self.field.setCurrentText(text)
        else:
            self.field.setText(text)

    def setLabelText(self, text: str):
        self.label.setText(text)

    def setComboBoxItems(self, items: Sequence[str]):
        self.field.clear()
        self.field.addItems(list(items))

    def setComboBoxEditable(self, editable: bool):
        self.field.setEditable(editable)

    def setWindowTitle(self, title: str):
        super().setWindowTitle(title)
        if title and self.title_bar and hasattr(self.title_bar, "title_label"):
            self.title_bar.title_label.setText(title)

    # ─────────────────────────────────────────────────────────────────────────
    # Statics
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def getText(
        cls,
        parent=None,
        title: str = "",
        label: str = "",
        text: str = "",
    ) -> Tuple[str, bool]:
        dialog = cls(parent, title=title, label=label, text=text)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.textValue(), True
        return "", False

    @classmethod
    def getItem(
        cls,
        parent=None,
        title: str = "",
        label: str = "",
        items: Sequence[str] = (),
        current: int = 0,
    ) -> Tuple[str, bool]:
        start = list(items)[current] if items else ""
        dialog = cls(parent, title=title, label=label, text=start, items=items)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.textValue(), True
        return "", False
