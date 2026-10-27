"""
lobster_config_dialog.py
========================
Frameless modal dialog for configuring lobster.lua (Headcrab config recovery).
Reads and writes ~/.config/SLSsteam/lobster.conf.lua.

Strictly NO emojis used anywhere.
"""

import logging
from typing import Optional

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
from ui.dialogs.game_details.depots_tab import MiniSwitchToggle
from utils.settings import get_settings
from utils.color_utils import get_best_foreground_color
from utils.sls_lua_config import read_lobster_conf, write_lobster_conf

logger = logging.getLogger(__name__)


class LobsterConfigDialog(QDialog):
    """Configuration dialog for lobster.conf.lua."""

    def __init__(self, parent: Optional[QWidget] = None, accent_color: str = "#6c5ce7"):
        super().__init__(parent)
        self.settings = get_settings()
        self.accent_color = accent_color or self.settings.value("accent_color", "#6c5ce7")
        self.background_color = self.settings.value("background_color", "#111318")

        self.cfg = read_lobster_conf()
        self._drag_pos: Optional[QPoint] = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(460)

        self._init_ui()

    def _init_ui(self):
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
            QLabel {{
                color: #FFFFFF;
                border: none;
                background: transparent;
            }}
        """)

        container_lay = QVBoxLayout(self.container)
        container_lay.setContentsMargins(0, 0, 0, 16)
        container_lay.setSpacing(14)

        self.title_bar = DialogTitleBar(
            self,
            title="Lobster Configuration",
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
        )
        container_lay.addWidget(self.title_bar)

        body_lay = QVBoxLayout()
        body_lay.setContentsMargins(20, 4, 20, 6)
        body_lay.setSpacing(12)

        desc_lbl = QLabel(
            "Lobster detects when Headcrab updates strip AdditionalDepots or DecryptionKeys "
            "from SLSsteam's config.yaml and automatically restores them."
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.70); line-height: 1.3;")
        body_lay.addWidget(desc_lbl)

        # Settings card
        card = QFrame()
        card.setObjectName("settingsCard")
        card.setStyleSheet("""
            QFrame#settingsCard {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
            QLabel {
                border: none;
                background: transparent;
            }
        """)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(14, 12, 14, 12)
        card_lay.setSpacing(10)

        # Toggle rows
        self.switches = {}

        def _add_switch_row(key: str, title: str, subtitle: str):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)

            text_lay = QVBoxLayout()
            text_lay.setSpacing(2)
            lbl_title = QLabel(title)
            lbl_title.setStyleSheet("font-size: 9pt; font-weight: 600; color: #FFFFFF;")
            lbl_sub = QLabel(subtitle)
            lbl_sub.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.55);")
            lbl_sub.setWordWrap(True)
            text_lay.addWidget(lbl_title)
            text_lay.addWidget(lbl_sub)
            row.addLayout(text_lay, 1)

            sw = MiniSwitchToggle(checked=bool(self.cfg.get(key, False)), read_only=False)
            self.switches[key] = sw
            row.addWidget(sw, 0, Qt.AlignmentFlag.AlignVCenter)

            card_lay.addLayout(row)

        _add_switch_row("enabled", "Enable Lobster Recovery", "Master switch for the config recovery plugin.")
        _add_switch_row("notify", "Desktop Notifications", "Display system notification when missing sections are restored.")
        _add_switch_row("auto_recover", "Automatic Recovery", "Automatically restore sections without interactive prompts.")
        _add_switch_row("run_on_reload", "Run on Config Reload", "Check on every SLSsteam reload. Off: only runs once on Steam boot.")

        body_lay.addWidget(card)

        # Bottom buttons
        btn_lay = QHBoxLayout()
        btn_lay.setSpacing(10)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setFixedHeight(34)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: rgba(255, 255, 255, 0.65);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                font-size: 8.5pt;
                padding: 4px 16px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.06);
                color: #FFFFFF;
            }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_lay.addWidget(btn_cancel)

        c = QColor(self.accent_color)
        if not c.isValid():
            c = QColor("#6c5ce7")
        fg = get_best_foreground_color(c)

        btn_save = QPushButton("Save Settings")
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.setFixedHeight(34)
        btn_save.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: {fg};
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 8.5pt;
                padding: 4px 20px;
            }}
            QPushButton:hover {{
                background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.85);
            }}
            QPushButton:pressed {{
                background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.70);
            }}
        """)
        btn_save.clicked.connect(self._on_save)
        btn_lay.addWidget(btn_save)

        body_lay.addLayout(btn_lay)
        container_lay.addLayout(body_lay)
        root.addWidget(self.container)

    def _on_save(self):
        new_cfg = dict(self.cfg)
        for key, sw in self.switches.items():
            new_cfg[key] = sw.isChecked()

        ok = write_lobster_conf(new_cfg)
        if ok:
            logger.info("[LobsterConfigDialog] Saved lobster.conf.lua successfully")
            self.accept()
        else:
            logger.error("[LobsterConfigDialog] Failed to save lobster.conf.lua")
            self.reject()

    # Drag support
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
