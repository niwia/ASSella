"""
steamlibrary.py
===============
Dialog for selecting a Steam library folder.
Revamped with ASSella custom title bar, large storage card tabs,
borderless styling, and full-width Cancel/Proceed buttons.
"""

import os
import logging
from typing import List, Optional
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QFrame,
    QWidget,
    QScrollArea,
)
from ui.dialogs.themed_message_box import ThemedMessageBox

from ui.dialog_titlebar import DialogTitleBar
from utils.settings import get_settings
from utils.color_utils import get_best_foreground_color

logger = logging.getLogger(__name__)


def _get_storage_friendly_name(path: str) -> str:
    """Derive a user-friendly name from a Steam library path."""
    p_str = os.path.normpath(str(path))
    home = os.path.expanduser("~")

    # Primary / Default Steam directory
    if p_str.startswith(os.path.join(home, ".local/share/Steam")) or p_str.startswith(os.path.join(home, ".steam")):
        return "Internal Storage (Default)"

    # SD Card / Removable media
    if "/run/media/" in p_str or "/media/" in p_str or "SDCARD" in p_str.upper() or "MMC" in p_str.upper():
        parts = Path(p_str).parts
        drive_name = "SD Card"
        for i, part in enumerate(parts):
            if part in ("media", "run"):
                if i + 2 < len(parts):
                    drive_name = parts[i + 2]
                break
        return f"Removable Storage ({drive_name})"

    # Custom mounted disk or partition
    parts = Path(p_str).parts
    if len(parts) > 1:
        return f"Storage Drive ({parts[-1]})"
    return "Steam Library"


class SteamLibraryDialog(QDialog):
    """
    Dialog for selecting a Steam library folder.
    Revamped with large card tabs, zero borders, and full-width bottom buttons.
    """

    def __init__(
        self,
        library_paths: Optional[List[str]] = None,
        parent: Optional[QWidget] = None,
        accent_color: Optional[str] = None,
    ):
        super().__init__(parent)
        self.library_paths = list(library_paths) if library_paths else []
        self.selected_path: Optional[str] = None
        self.settings = get_settings()

        if not accent_color:
            self.accent_color = self.settings.value("accent_color", "#6c5ce7", type=str)
        else:
            self.accent_color = accent_color

        c = QColor(self.accent_color)
        if not c.isValid():
            c = QColor("#6c5ce7")
        self._r, self._g, self._b = c.red(), c.green(), c.blue()

        self.setWindowTitle("Select Steam Library")
        self.setFixedWidth(460)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.card_widgets = []
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        # Outer Container Frame
        self.container = QFrame(self)
        self.container.setObjectName("dialogContainer")
        self.container.setStyleSheet("""
            QFrame#dialogContainer {
                background-color: #1a1c23;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }
            * {
                outline: none;
            }
            QLabel {
                color: #FFFFFF;
                border: none;
                background: transparent;
            }
        """)
        root.addWidget(self.container)

        container_lay = QVBoxLayout(self.container)
        container_lay.setContentsMargins(0, 0, 0, 16)
        container_lay.setSpacing(12)

        # Custom Themed Title Bar
        self.title_bar = DialogTitleBar(
            self,
            title="Select Steam Library",
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
        )
        container_lay.addWidget(self.title_bar)

        # Body area
        body_lay = QVBoxLayout()
        body_lay.setContentsMargins(20, 4, 20, 4)
        body_lay.setSpacing(14)
        container_lay.addLayout(body_lay)

        # Header Title
        title_lbl = QLabel("Choose Destination Library")
        title_lbl.setStyleSheet(
            "font-size: 11pt; font-weight: bold; color: rgba(255, 255, 255, 0.95); border: none; background: transparent;"
        )
        body_lay.addWidget(title_lbl)

        # Scroll Area for storage tabs if list is large
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
                outline: none;
            }
            QScrollBar:vertical {
                width: 6px;
                background: transparent;
                margin: 0px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.2);
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.35);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        list_container = QWidget()
        list_container.setStyleSheet("background: transparent; border: none;")
        list_lay = QVBoxLayout(list_container)
        list_lay.setContentsMargins(0, 0, 0, 0)
        list_lay.setSpacing(10)

        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)

        sorted_paths = sorted(self.library_paths)
        for idx, path in enumerate(sorted_paths):
            card = self._create_storage_card(path, idx == 0)
            list_lay.addWidget(card)

        list_lay.addStretch()
        scroll.setWidget(list_container)

        # Restrict scroll height cleanly
        num_items = len(sorted_paths)
        if num_items <= 3:
            scroll.setFixedHeight(num_items * 64 + 10)
        else:
            scroll.setFixedHeight(220)

        body_lay.addWidget(scroll)

        # Bottom row: Cancel / Proceed (big buttons filling bottom width)
        bot_layout = QHBoxLayout()
        bot_layout.setContentsMargins(0, 8, 0, 0)
        bot_layout.setSpacing(10)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancel_btn")
        self.cancel_btn.setFixedHeight(38)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton#cancel_btn {
                background-color: rgba(255, 255, 255, 0.06);
                color: rgba(255, 255, 255, 0.85);
                border: none;
                outline: none;
                border-radius: 6px;
                font-size: 9pt;
                font-weight: 500;
            }
            QPushButton#cancel_btn:hover {
                background-color: rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
            }
            QPushButton#cancel_btn:focus {
                outline: none;
                border: none;
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        bot_layout.addWidget(self.cancel_btn, 1)

        # Foreground color chosen for maximum contrast against accent
        btn_fg = get_best_foreground_color(self.accent_color, dark_color="#121214", light_color="#FFFFFF")

        self.proceed_btn = QPushButton("Proceed")
        self.proceed_btn.setObjectName("proceed_btn")
        self.proceed_btn.setFixedHeight(38)
        self.proceed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.proceed_btn.setStyleSheet(f"""
            QPushButton#proceed_btn {{
                background-color: {self.accent_color};
                color: {btn_fg};
                border: none;
                outline: none;
                border-radius: 6px;
                font-size: 9pt;
                font-weight: bold;
            }}
            QPushButton#proceed_btn:hover {{
                opacity: 0.9;
            }}
            QPushButton#proceed_btn:focus {{
                outline: none;
                border: none;
            }}
        """)
        self.proceed_btn.clicked.connect(self.accept)
        bot_layout.addWidget(self.proceed_btn, 1)

        body_lay.addLayout(bot_layout)
        self._update_all_card_styles()

    def _create_storage_card(self, path: str, is_default: bool) -> QFrame:
        """Create a large borderless card tab for a storage location."""
        card = QFrame()
        card.setFixedHeight(56)
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(card)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(12)

        # Text VBox (Friendly Title + Full Path)
        text_vbox = QVBoxLayout()
        text_vbox.setContentsMargins(0, 0, 0, 0)
        text_vbox.setSpacing(2)

        friendly_title = _get_storage_friendly_name(path)
        title_lbl = QLabel(friendly_title)
        title_lbl.setStyleSheet("font-size: 9.5pt; font-weight: bold; color: #FFFFFF; border: none; background: transparent;")
        text_vbox.addWidget(title_lbl)

        path_lbl = QLabel(path)
        path_lbl.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.60); border: none; background: transparent;")
        text_vbox.addWidget(path_lbl)

        lay.addLayout(text_vbox, stretch=1)

        # Radio indicator on the right
        radio = QRadioButton()
        radio.setChecked(is_default)
        radio.setCursor(Qt.CursorShape.PointingHandCursor)
        radio.setStyleSheet(f"""
            QRadioButton {{
                background: transparent;
                border: none;
                outline: none;
            }}
            QRadioButton:focus {{
                border: none;
                outline: none;
            }}
            QRadioButton::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: none;
                outline: none;
                background-color: rgba(255, 255, 255, 0.12);
            }}
            QRadioButton::indicator:hover {{
                background-color: rgba(255, 255, 255, 0.22);
            }}
            QRadioButton::indicator:checked {{
                background-color: {self.accent_color};
                border: none;
            }}
        """)
        self.button_group.addButton(radio)
        lay.addWidget(radio)

        card_info = {
            "frame": card,
            "radio": radio,
            "path": path,
        }
        self.card_widgets.append(card_info)

        def _on_click(event):
            radio.setChecked(True)

        def _on_double_click(event):
            radio.setChecked(True)
            self.accept()

        card.mousePressEvent = _on_click
        card.mouseDoubleClickEvent = _on_double_click
        radio.toggled.connect(lambda checked: self._update_all_card_styles())

        if is_default:
            self.selected_path = path

        return card

    def _update_all_card_styles(self):
        """Update background highlight on selected vs unselected cards without any borders."""
        sel_style = f"""
            QFrame {{
                background-color: rgba({self._r}, {self._g}, {self._b}, 0.20);
                border: none;
                outline: none;
                border-radius: 8px;
            }}
            QFrame:hover {{
                background-color: rgba({self._r}, {self._g}, {self._b}, 0.26);
            }}
            QLabel {{
                border: none;
                background: transparent;
            }}
        """
        unsel_style = """
            QFrame {
                background-color: rgba(255, 255, 255, 0.04);
                border: none;
                outline: none;
                border-radius: 8px;
            }
            QFrame:hover {
                background-color: rgba(255, 255, 255, 0.08);
            }
            QLabel {
                border: none;
                background: transparent;
            }
        """
        for item in self.card_widgets:
            if item["radio"].isChecked():
                item["frame"].setStyleSheet(sel_style)
                self.selected_path = item["path"]
            else:
                item["frame"].setStyleSheet(unsel_style)

    def accept(self) -> None:
        """Handle Proceed button click."""
        for item in self.card_widgets:
            if item["radio"].isChecked():
                self.selected_path = item["path"]
                break

        if not self.selected_path:
            ThemedMessageBox.warning(self, "No Selection", "Please select a Steam library folder.")
            return

        logger.info(f"User selected Steam library: {self.selected_path}")
        super().accept()

    def get_selected_path(self) -> Optional[str]:
        """Return the selected library path."""
        return self.selected_path
