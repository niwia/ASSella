"""
themed_color_dialog.py - ASSella's own colour picker.

QColorDialog draws a desktop window, so picking the accent colour popped a
window that belonged to the desktop rather than to ASSella. This module embeds
the picker's own colour wheel/swatch widget inside a normal themed frame, which
also gives us the native dialog's behaviour for free (the wheel, the presets,
the HTML/hex field).

    ThemedColorDialog.getColor()                 -> QColor (invalid if cancelled)
    ThemedColorDialog.getColor(current, parent, title)
"""

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QDialog,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.dialog_titlebar import DialogTitleBar
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class ThemedColorDialog(QDialog):
    """Frameless wrapper around the standard colour picker's widget."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        title: str = "Choose a Colour",
        initial: Optional[QColor] = None,
    ):
        super().__init__(parent)

        settings = get_settings()
        self.accent_color = settings.value("accent_color", "#a1c9fd")
        self.background_color = settings.value("background_color", "#111318")

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        container = QFrame(self)
        container.setObjectName("dialogContainer")
        container.setStyleSheet(f"""
            QFrame#dialogContainer {{
                background-color: {self.background_color};
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }}
            QLabel {{ color: #e5e7eb; }}
            QPushButton#plainButton {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                color: #e5e7eb;
                font-size: 8.5pt;
                padding: 5px 16px;
            }}
            QPushButton#plainButton:hover {{
                background-color: rgba(255, 255, 255, 0.12);
                color: #ffffff;
            }}
            QPushButton#accentButton {{
                background-color: {self.accent_color};
                color: #0b0d12;
                border: none;
                border-radius: 5px;
                font-size: 8.5pt;
                padding: 5px 16px;
            }}
            QPushButton#accentButton:hover {{ background-color: #ffffff; }}
        """)

        lay = QVBoxLayout(container)
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

        # The picker itself, as a widget: native dialog, no buttons of its own.
        self.picker = QColorDialog(self)
        self.picker.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        self.picker.setOption(QColorDialog.ColorDialogOption.NoButtons, True)
        self.picker.setWindowFlags(Qt.WindowType.Widget)
        if initial is not None and initial.isValid():
            self.picker.setCurrentColor(initial)
        lay.addWidget(self.picker)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(16, 0, 16, 0)
        buttons.addStretch()
        cancel_button = QPushButton("Cancel", self)
        cancel_button.setObjectName("plainButton")
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_button.clicked.connect(self.reject)
        ok_button = QPushButton("OK", self)
        ok_button.setObjectName("accentButton")
        ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_button.clicked.connect(self.accept)
        buttons.addWidget(cancel_button)
        buttons.addWidget(ok_button)
        lay.addLayout(buttons)

        root.addWidget(container)

    def selected_color(self) -> QColor:
        return self.picker.currentColor()

    @classmethod
    def getColor(
        cls,
        initial: Optional[QColor] = None,
        parent: Optional[QWidget] = None,
        title: str = "Choose a Colour",
    ) -> QColor:
        """Return the chosen colour, or an invalid QColor when cancelled."""
        dialog = cls(parent, title=title, initial=initial)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            color = dialog.selected_color()
            logger.debug("Themed colour dialog picked %s", color.name())
            return color
        return QColor()
