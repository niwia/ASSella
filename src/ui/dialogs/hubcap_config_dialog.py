"""
hubcap_config_dialog.py
=======================
Frameless modal dialog for configuring hubcap.lua (Hubcap key resolver companion)
within ~/.config/SLSsteam/spacebunny.conf.lua.

Strictly NO emojis used anywhere.
"""

import logging
import time
from typing import Optional

from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFrame,
    QWidget,
)

from ui.dialog_titlebar import DialogTitleBar
from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.dialogs.game_details.depots_tab import MiniSwitchToggle
from utils.settings import get_settings
from utils.color_utils import get_best_foreground_color
from utils.sls_lua_config import (
    read_spacebunny_conf,
    write_spacebunny_conf,
    is_valid_hubcap_key,
    generate_hubcap_timestamp,
)

logger = logging.getLogger(__name__)


class HubcapConfigDialog(QDialog):
    """Configuration dialog for Hubcap resolver in spacebunny.conf.lua."""

    def __init__(self, parent: Optional[QWidget] = None, accent_color: str = "#6c5ce7"):
        super().__init__(parent)
        self.settings = get_settings()
        self.accent_color = accent_color or self.settings.value("accent_color", "#6c5ce7")
        self.background_color = self.settings.value("background_color", "#111318")

        self.sb_cfg = read_spacebunny_conf()
        self.hub_cfg = dict(self.sb_cfg.get("hubcap", {}))
        self._drag_pos: Optional[QPoint] = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(490)

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
            title="Hubcap Configuration",
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
        )
        container_lay.addWidget(self.title_bar)

        body_lay = QVBoxLayout()
        body_lay.setContentsMargins(20, 4, 20, 6)
        body_lay.setSpacing(12)

        desc_lbl = QLabel(
            "Hubcap automatically resolves missing depot AES decryption keys in SLSsteam "
            "when Steam requests them, using spacebunny.conf.lua."
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
        card_lay.setSpacing(12)

        # 1. Base URL (Read-only)
        url_row = QVBoxLayout()
        url_row.setSpacing(2)
        lbl_url_title = QLabel("Hubcap Base URL:")
        lbl_url_title.setStyleSheet("font-size: 8.5pt; font-weight: 600; color: rgba(255, 255, 255, 0.85);")
        lbl_url_val = QLabel(self.hub_cfg.get("base_url", "https://hubcapmanifest.com/api/v1"))
        lbl_url_val.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.50); font-family: monospace;")
        url_row.addWidget(lbl_url_title)
        url_row.addWidget(lbl_url_val)
        card_lay.addLayout(url_row)

        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(255,255,255,0.06); border: none; background: rgba(255,255,255,0.06); max-height: 1px;")
        card_lay.addWidget(sep)

        # 2. Master Enable Toggle
        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 0, 0, 0)
        toggle_row.setSpacing(10)

        t_text = QVBoxLayout()
        t_text.setSpacing(2)
        lbl_t_title = QLabel("Enable Hubcap Auto-Fetch")
        lbl_t_title.setStyleSheet("font-size: 9pt; font-weight: 600; color: #FFFFFF;")
        lbl_t_sub = QLabel("Query Hubcap API for missing AES keys during Steam downloads.")
        lbl_t_sub.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.55);")
        t_text.addWidget(lbl_t_title)
        t_text.addWidget(lbl_t_sub)
        toggle_row.addLayout(t_text, 1)

        self.sw_enabled = MiniSwitchToggle(
            checked=bool(self.hub_cfg.get("enabled", True)),
            read_only=False,
        )
        toggle_row.addWidget(self.sw_enabled, 0, Qt.AlignmentFlag.AlignVCenter)
        card_lay.addLayout(toggle_row)

        # Separator line
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: rgba(255,255,255,0.06); border: none; background: rgba(255,255,255,0.06); max-height: 1px;")
        card_lay.addWidget(sep2)

        # 3. API Key field & Actions
        key_box = QVBoxLayout()
        key_box.setSpacing(6)

        lbl_key = QLabel("Hubcap API Key (in Lua):")
        lbl_key.setStyleSheet("font-size: 8.5pt; font-weight: 600; color: rgba(255, 255, 255, 0.85);")
        key_box.addWidget(lbl_key)

        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Enter or paste Hubcap API key...")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setText(str(self.hub_cfg.get("api_key", "")).strip())
        self.key_input.setStyleSheet("""
            QLineEdit {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                color: #FFFFFF;
                font-size: 8.5pt;
                padding: 5px 8px;
            }
            QLineEdit:focus {
                border-color: rgba(255, 255, 255, 0.35);
            }
        """)
        input_row.addWidget(self.key_input, 1)

        btn_show = QPushButton("Show")
        btn_show.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_show.setFixedHeight(28)
        btn_show.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: rgba(255, 255, 255, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                font-size: 8pt;
                padding: 2px 10px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.14);
            }
        """)

        def _toggle_visibility():
            if self.key_input.echoMode() == QLineEdit.EchoMode.Password:
                self.key_input.setEchoMode(QLineEdit.EchoMode.Normal)
                btn_show.setText("Hide")
            else:
                self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
                btn_show.setText("Show")

        btn_show.clicked.connect(_toggle_visibility)
        input_row.addWidget(btn_show)

        key_box.addLayout(input_row)

        # Paste & Sync Action Buttons
        act_row = QHBoxLayout()
        act_row.setSpacing(8)

        btn_paste = QPushButton("Paste")
        btn_paste.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_paste.setFixedHeight(26)
        btn_paste.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                color: rgba(255, 255, 255, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 5px;
                font-size: 8pt;
                padding: 2px 12px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
            }
        """)

        def _on_paste():
            clip = QApplication.clipboard().text()
            if clip:
                self.key_input.setText(clip.strip())

        btn_paste.clicked.connect(_on_paste)
        act_row.addWidget(btn_paste)

        # ASSella key status & sync button
        assella_key = str(self.settings.value("morrenus_api_key", "", type=str) or "").strip()
        if assella_key:
            masked_assella = f"{assella_key[:4]}...{assella_key[-4:]}" if len(assella_key) > 10 else "***"
            lbl_assella = QLabel(f"ASSella key: {masked_assella}")
            lbl_assella.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.50);")
            act_row.addWidget(lbl_assella)

            btn_sync = QPushButton("Sync from ASSella")
            btn_sync.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_sync.setFixedHeight(26)
            btn_sync.setToolTip("Copy ASSella's Hubcap API key into this field and save to Lua.")
            btn_sync.setStyleSheet("""
                QPushButton {
                    background-color: rgba(108, 92, 231, 0.20);
                    color: #A29BFE;
                    border: 1px solid rgba(108, 92, 231, 0.40);
                    border-radius: 5px;
                    font-size: 8pt;
                    font-weight: 600;
                    padding: 2px 12px;
                }
                QPushButton:hover {
                    background-color: rgba(108, 92, 231, 0.35);
                    color: #FFFFFF;
                }
            """)

            def _on_sync_assella():
                self.key_input.setText(assella_key)

            btn_sync.clicked.connect(_on_sync_assella)
            act_row.addWidget(btn_sync)

        act_row.addStretch()
        key_box.addLayout(act_row)
        card_lay.addLayout(key_box)

        # Inactive / Extensible Placeholders (dead code structure for future toggles per user request)
        self._future_toggles = {
            "write_config": bool(self.hub_cfg.get("write_config", True)),
            "notify": bool(self.hub_cfg.get("notify", True)),
            "depot_index": bool(self.hub_cfg.get("depot_index", False)),
            "write_depot_comments": bool(self.hub_cfg.get("write_depot_comments", False)),
        }

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
        new_hub = dict(self.hub_cfg)
        new_hub["enabled"] = self.sw_enabled.isChecked()

        new_key = self.key_input.text().strip()
        old_key = str(self.hub_cfg.get("api_key", "")).strip()

        if new_key != old_key:
            if not new_key:
                new_hub["api_key"] = ""
                new_ts = generate_hubcap_timestamp()
                new_hub["api_key_timestamp"] = new_ts
                try:
                    get_settings().setValue("morrenus_api_key", "")
                    get_settings().setValue("morrenus_api_key_timestamp", str(new_ts))
                except Exception as exc:
                    logger.debug(f"[HubcapConfigDialog] Could not clear settings key: {exc}")
            elif is_valid_hubcap_key(new_key):
                new_hub["api_key"] = new_key
                new_ts = generate_hubcap_timestamp()
                new_hub["api_key_timestamp"] = new_ts
                try:
                    get_settings().setValue("morrenus_api_key", new_key)
                    get_settings().setValue("morrenus_api_key_timestamp", str(new_ts))
                except Exception as exc:
                    logger.debug(f"[HubcapConfigDialog] Could not update settings key: {exc}")
            else:
                ThemedMessageBox.warning(
                    self,
                    "Invalid API Key",
                    "The entered Hubcap API key is invalid.\n\n"
                    "Keys must be at least 8 characters long and contain only letters, numbers, hyphens, and underscores."
                )
                return

        # Preserve future option placeholders
        for k, v in self._future_toggles.items():
            if k not in new_hub:
                new_hub[k] = v

        new_sb = dict(self.sb_cfg)
        new_sb["hubcap"] = new_hub

        ok = write_spacebunny_conf(new_sb)
        if ok:
            logger.info("[HubcapConfigDialog] Saved spacebunny.conf.lua successfully")
            self.accept()
        else:
            logger.error("[HubcapConfigDialog] Failed to save spacebunny.conf.lua")
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
