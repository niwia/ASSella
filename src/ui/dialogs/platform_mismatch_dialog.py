"""
platform_mismatch_dialog.py
===========================
Dialog displayed when a game has native Linux depots in Steam PICS,
but only Windows depots and keys are available in the package source.

Gives the user three clear choices:
  1. Force Proton & Install via Steam: writes Proton tool to config.vdf,
     restarts Steam, and installs the Windows depot through Steam client.
  2. Download via ASSella: switches to ASSella's built-in DepotDownloader
     to download Windows depots directly without modifying Steam.
  3. Cancel: abort the operation.

Strictly NO emojis used anywhere.
"""

import logging
from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QWidget,
)

from ui.dialog_titlebar import DialogTitleBar
from utils.settings import get_settings
from utils.color_utils import get_best_foreground_color

logger = logging.getLogger(__name__)

ACTION_CANCEL = 0
ACTION_FORCE_PROTON = 1
ACTION_ASSELLA_DOWNLOAD = 2


class PlatformMismatchDialog(QDialog):
    """
    Theme-aware, frameless dialog warning the user of a platform mismatch
    before Steam client handoff.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        details: Optional[Dict[str, Any]] = None,
        accent_color: str = "#6c5ce7",
    ):
        super().__init__(parent)
        self.details = details or {}
        self.app_id = str(self.details.get("appid", ""))
        self.game_name = self.details.get("game_name", f"App {self.app_id}")
        self.linux_depots = self.details.get("linux_depots", [])
        self.windows_depots = self.details.get("windows_depots", [])
        self.preferred_tool = self.details.get("preferred_tool", "proton_experimental")

        self.settings = get_settings()
        self.accent_color = accent_color or self.settings.value("accent_color", "#6c5ce7")
        self.background_color = self.settings.value("background_color", "#111318")

        self._action = ACTION_CANCEL
        self._drag_pos: Optional[QPoint] = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(500)

        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        # Outer Container
        self.container = QFrame(self)
        self.container.setObjectName("dialogContainer")
        self.container.setStyleSheet(f"""
            QFrame#dialogContainer {{
                background-color: {self.background_color};
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }}
            QLabel {{
                color: #FFFFFF;
                border: none;
                background: transparent;
            }}
        """)

        container_lay = QVBoxLayout(self.container)
        container_lay.setContentsMargins(0, 0, 0, 16)
        container_lay.setSpacing(14)

        # Custom Themed Title Bar
        self.title_bar = DialogTitleBar(
            self,
            title="Platform Mismatch Detected",
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
        )
        container_lay.addWidget(self.title_bar)

        # Body area
        body_lay = QVBoxLayout()
        body_lay.setContentsMargins(20, 4, 20, 6)
        body_lay.setSpacing(12)

        # Main Title
        title_lbl = QLabel("Platform Compatibility Mismatch")
        title_lbl.setStyleSheet(
            "font-size: 11pt; font-weight: bold; color: rgba(255, 255, 255, 0.95);"
        )
        body_lay.addWidget(title_lbl)

        # Description
        desc_lbl = QLabel(
            "Steam on Linux will attempt to download the native Linux version of this game. "
            "However, only Windows depots and decryption keys are available in the source repository.\n\n"
            "If handed off to Steam without Proton, Steam will download 0 bytes."
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(
            "font-size: 9pt; color: rgba(255, 255, 255, 0.75); line-height: 140%;"
        )
        body_lay.addWidget(desc_lbl)

        # Detail Box
        info_frame = QFrame()
        info_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
        """)
        info_lay = QVBoxLayout(info_frame)
        info_lay.setContentsMargins(14, 12, 14, 12)
        info_lay.setSpacing(8)

        linux_depots_str = ", ".join(self.linux_depots) if self.linux_depots else "None"
        win_depots_str = ", ".join(self.windows_depots) if self.windows_depots else "None"

        def _row(label: str, value: str):
            h = QHBoxLayout()
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            lbl_key = QLabel(label)
            lbl_key.setStyleSheet(
                "font-size: 8.5pt; font-weight: bold; color: rgba(255, 255, 255, 0.55);"
            )
            lbl_val = QLabel(value)
            lbl_val.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.90);")
            lbl_val.setWordWrap(True)
            h.addWidget(lbl_key)
            h.addWidget(lbl_val, 1)
            return h

        info_lay.addLayout(_row("Game:", f"{self.game_name} ({self.app_id})"))
        info_lay.addLayout(_row("Native Steam Depots (Linux):", linux_depots_str))
        info_lay.addLayout(_row("Available Depots (Windows):", win_depots_str))
        info_lay.addLayout(_row("Auto-Detected Proton:", self.preferred_tool))

        body_lay.addWidget(info_frame)

        # Buttons layout
        btn_lay = QVBoxLayout()
        btn_lay.setSpacing(8)

        # Accent color calculations
        c = QColor(self.accent_color)
        if not c.isValid():
            c = QColor("#6c5ce7")
        fg = get_best_foreground_color(c)

        # Option A: Force Proton & Install via Steam
        self.btn_force_proton = QPushButton("Force Proton & Install via Steam")
        self.btn_force_proton.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_force_proton.setFixedHeight(38)
        self.btn_force_proton.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: {fg};
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 9pt;
                padding: 4px 16px;
            }}
            QPushButton:hover {{
                background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.85);
            }}
            QPushButton:pressed {{
                background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.70);
            }}
        """)
        self.btn_force_proton.clicked.connect(self._on_force_proton)
        btn_lay.addWidget(self.btn_force_proton)

        # Option B: Download via ASSella
        self.btn_assella = QPushButton("Download via ASSella")
        self.btn_assella.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_assella.setFixedHeight(36)
        self.btn_assella.setStyleSheet("""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.08);
                color: rgba(255, 255, 255, 0.95);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                font-weight: 500;
                font-size: 9pt;
                padding: 4px 16px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.14);
                border-color: rgba(255, 255, 255, 0.20);
            }}
            QPushButton:pressed {{
                background-color: rgba(255, 255, 255, 0.06);
            }}
        """)
        self.btn_assella.clicked.connect(self._on_assella)
        btn_lay.addWidget(self.btn_assella)

        # Option C: Cancel
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setFixedHeight(32)
        self.btn_cancel.setStyleSheet("""
            QPushButton {{
                background-color: transparent;
                color: rgba(255, 255, 255, 0.60);
                border: none;
                border-radius: 6px;
                font-size: 8.5pt;
                padding: 4px 16px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.85);
            }}
            QPushButton:pressed {{
                background-color: rgba(255, 255, 255, 0.02);
            }}
        """)
        self.btn_cancel.clicked.connect(self.reject)
        btn_lay.addWidget(self.btn_cancel)

        body_lay.addLayout(btn_lay)
        container_lay.addLayout(body_lay)
        root.addWidget(self.container)

    def _on_force_proton(self):
        self._action = ACTION_FORCE_PROTON
        self.accept()

    def _on_assella(self):
        self._action = ACTION_ASSELLA_DOWNLOAD
        self.accept()

    def get_action(self) -> int:
        return self._action

    # Dragging support
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()
