"""
native_steam_action_dialog.py
=============================
Minimal compact dialog matching user specification:
  - "How to proceed?" header
  - Option 1: "Automatic" (starts download immediately in Steam)
  - Option 2: "Manual Depot/Storage" (adds to Steam only)
  - "Remember my choice" checkbox (unchecked by default)
  - "Cancel" and "Proceed" buttons
"""

from typing import Optional
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QRadioButton,
    QButtonGroup,
    QFrame,
    QWidget,
)

from utils.settings import get_settings

logger = logging.getLogger(__name__)

ACTION_CANCEL = 0
ACTION_DOWNLOAD = 1
ACTION_ADD_ONLY = 2
ACTION_TRACK = 1
ACTION_HANDOFF = 2


class NativeSteamActionDialog(QDialog):
    """
    Minimal dialog prompting how to proceed with Steam installation:
      - Automatic (starts download immediately)
      - Manual Depot/Storage (add to Steam only)
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        app_id: str = "",
        game_name: str = "",
        accent_color: str = "#6c5ce7",
    ):
        super().__init__(parent)
        self.app_id = str(app_id)
        self.game_name = game_name or f"App {app_id}"
        self.accent_color = accent_color
        self._action = ACTION_CANCEL
        self.settings = get_settings()

        self.setWindowTitle("How to proceed?")
        self.setFixedWidth(420)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1c23;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }
            QLabel {
                color: #FFFFFF;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        # Header title
        title_lbl = QLabel("How to proceed?")
        title_lbl.setStyleSheet("font-size: 11pt; font-weight: bold; color: rgba(255, 255, 255, 0.95);")
        layout.addWidget(title_lbl)

        self.button_group = QButtonGroup(self)

        # Option 1: Automatic
        self.auto_frame = QFrame()
        self.auto_frame.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_frame.setFixedHeight(46)
        auto_layout = QHBoxLayout(self.auto_frame)
        auto_layout.setContentsMargins(14, 8, 14, 8)
        auto_layout.setSpacing(10)

        # Left badge indicator
        auto_badge = QLabel("AUTO")
        auto_badge.setStyleSheet(f"""
            background-color: rgba(255, 255, 255, 0.08);
            color: {self.accent_color};
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 4px;
            padding: 2px 6px;
            font-size: 7.5pt;
            font-weight: bold;
        """)
        auto_layout.addWidget(auto_badge)

        auto_lbl = QLabel("Automatic")
        auto_lbl.setStyleSheet("font-size: 9.5pt; color: #FFFFFF; background: transparent; font-weight: 500;")
        auto_layout.addWidget(auto_lbl)
        auto_layout.addStretch()

        self.auto_radio = QRadioButton()
        self.auto_radio.setChecked(True)
        self.auto_radio.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_radio(self.auto_radio)
        self.button_group.addButton(self.auto_radio)
        auto_layout.addWidget(self.auto_radio)

        self.auto_frame.mousePressEvent = lambda e: self.auto_radio.setChecked(True)
        layout.addWidget(self.auto_frame)

        # Option 2: Manual Depot/Storage
        self.manual_frame = QFrame()
        self.manual_frame.setCursor(Qt.CursorShape.PointingHandCursor)
        self.manual_frame.setFixedHeight(46)
        manual_layout = QHBoxLayout(self.manual_frame)
        manual_layout.setContentsMargins(14, 8, 14, 8)
        manual_layout.setSpacing(10)

        # Left badge indicator
        manual_badge = QLabel("MANUAL")
        manual_badge.setStyleSheet("""
            background-color: rgba(255, 255, 255, 0.08);
            color: rgba(255, 255, 255, 0.7);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 4px;
            padding: 2px 6px;
            font-size: 7.5pt;
            font-weight: bold;
        """)
        manual_layout.addWidget(manual_badge)

        manual_lbl = QLabel("Manual Depot/Storage")
        manual_lbl.setStyleSheet("font-size: 9.5pt; color: #FFFFFF; background: transparent; font-weight: 500;")
        manual_layout.addWidget(manual_lbl)
        manual_layout.addStretch()

        self.manual_radio = QRadioButton()
        self.manual_radio.setChecked(False)
        self.manual_radio.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_radio(self.manual_radio)
        self.button_group.addButton(self.manual_radio)
        manual_layout.addWidget(self.manual_radio)

        self.manual_frame.mousePressEvent = lambda e: self.manual_radio.setChecked(True)
        layout.addWidget(self.manual_frame)

        # Synchronize frame selection styles when radios change
        self.auto_radio.toggled.connect(self._update_styles)
        self.manual_radio.toggled.connect(self._update_styles)
        self._update_styles()

        # Remember my choice Checkbox
        self.remember_chk = QCheckBox("Remember my choice")
        self.remember_chk.setChecked(False)
        self.remember_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remember_chk.setStyleSheet("""
            QCheckBox {
                font-size: 8.5pt;
                color: rgba(255, 255, 255, 0.75);
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 3px;
                background: rgba(255, 255, 255, 0.05);
            }
            QCheckBox::indicator:checked {
                background: %s;
                border-color: %s;
            }
        """ % (self.accent_color, self.accent_color))
        layout.addWidget(self.remember_chk)

        # Bottom row: Cancel / Proceed
        bot_layout = QHBoxLayout()
        bot_layout.setContentsMargins(0, 4, 0, 0)
        bot_layout.setSpacing(10)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedSize(85, 30)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                color: rgba(255, 255, 255, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
            }
        """)
        self.cancel_btn.clicked.connect(self._on_cancel)
        bot_layout.addWidget(self.cancel_btn)

        bot_layout.addStretch()

        self.proceed_btn = QPushButton("Proceed")
        self.proceed_btn.setFixedSize(85, 30)
        self.proceed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.proceed_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: #FFFFFF;
                border: 1px solid {self.accent_color};
                border-radius: 6px;
                font-size: 8.5pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
        """)
        self.proceed_btn.clicked.connect(self._on_proceed)
        bot_layout.addWidget(self.proceed_btn)

        layout.addLayout(bot_layout)

    def _style_radio(self, radio: QRadioButton):
        radio.setStyleSheet(f"""
            QRadioButton::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: 1px solid rgba(255, 255, 255, 0.4);
                background-color: rgba(255, 255, 255, 0.05);
            }}
            QRadioButton::indicator:hover {{
                border-color: {self.accent_color};
            }}
            QRadioButton::indicator:checked {{
                background-color: {self.accent_color};
                border: 3px solid #1a1c23;
                outline: 1px solid {self.accent_color};
            }}
        """)

    def _update_styles(self):
        sel_style = f"""
            QFrame {{
                background-color: rgba(255, 255, 255, 0.07);
                border: 1px solid {self.accent_color};
                border-radius: 8px;
            }}
        """
        unsel_style = """
            QFrame {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
            }
            QFrame:hover {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.22);
            }
        """
        self.auto_frame.setStyleSheet(sel_style if self.auto_radio.isChecked() else unsel_style)
        self.manual_frame.setStyleSheet(sel_style if self.manual_radio.isChecked() else unsel_style)

    def _on_proceed(self):
        if self.auto_radio.isChecked():
            self._action = ACTION_DOWNLOAD
        else:
            self._action = ACTION_ADD_ONLY

        if self.should_remember():
            val = "immediate" if self._action == ACTION_DOWNLOAD else "add_only"
            self.settings.setValue("at0m_start_download_action", val)
            self.settings.setValue("vapor_start_download_action", val)
            logger.info(f"[NativeSteamActionDialog] Saved default start download action: {val}")

        self.accept()

    def _on_cancel(self):
        self._action = ACTION_CANCEL
        self.reject()

    def get_action(self) -> int:
        return self._action

    def should_remember(self) -> bool:
        return self.remember_chk.isChecked()
