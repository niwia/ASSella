"""
native_steam_action_dialog.py
=============================
Dialog offering the user a choice between:
  1. Download & Track in ASSella (monitors download progress in ASSella UI)
  2. Add to Steam & Hand Off (instant registration, hands off to Steam client)
"""

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QFrame,
    QWidget,
)

ACTION_CANCEL = 0
ACTION_TRACK = 1
ACTION_HANDOFF = 2


class NativeSteamActionDialog(QDialog):
    """
    Presents the user with a choice between monitored Steam download
    and instant handoff to the Steam client.
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

        self.setWindowTitle("Steam Client Download")
        self.setFixedSize(520, 370)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #1a1c23;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 12px;
            }}
            QLabel {{
                color: #FFFFFF;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # Title
        title_lbl = QLabel("Steam Client Download")
        title_lbl.setStyleSheet(f"font-size: 15pt; font-weight: bold; color: {self.accent_color};")
        layout.addWidget(title_lbl)

        # Game info
        info_lbl = QLabel(f"Target: <b style='color: #FFFFFF;'>{self.game_name}</b> (AppID: {self.app_id})")
        info_lbl.setStyleSheet("font-size: 10pt; color: rgba(255, 255, 255, 0.85);")
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        desc_lbl = QLabel("Select how you would like ASSella to proceed with this Steam download:")
        desc_lbl.setStyleSheet("font-size: 9pt; color: rgba(255, 255, 255, 0.65);")
        layout.addWidget(desc_lbl)

        # Option A: Download & Track in ASSella
        self.track_btn = QPushButton()
        self.track_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.track_btn.setFixedHeight(68)
        self.track_btn.clicked.connect(self._on_track_clicked)
        track_layout = QVBoxLayout(self.track_btn)
        track_layout.setContentsMargins(14, 8, 14, 8)
        track_layout.setSpacing(2)

        t_title = QLabel("Download and Track in ASSella")
        t_title.setStyleSheet("font-size: 10pt; font-weight: bold; color: #FFFFFF; background: transparent;")
        t_sub = QLabel("Steam downloads files while ASSella monitors progress, speeds, and completion.")
        t_sub.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.65); background: transparent;")
        t_sub.setWordWrap(True)
        track_layout.addWidget(t_title)
        track_layout.addWidget(t_sub)
        self._style_option_btn(self.track_btn, primary=True)
        layout.addWidget(self.track_btn)

        # Option B: Add to Steam & Hand Off
        self.handoff_btn = QPushButton()
        self.handoff_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.handoff_btn.setFixedHeight(68)
        self.handoff_btn.clicked.connect(self._on_handoff_clicked)
        handoff_layout = QVBoxLayout(self.handoff_btn)
        handoff_layout.setContentsMargins(14, 8, 14, 8)
        handoff_layout.setSpacing(2)

        h_title = QLabel("Add to Steam and Hand Off")
        h_title.setStyleSheet("font-size: 10pt; font-weight: bold; color: #FFFFFF; background: transparent;")
        h_sub = QLabel("Registers game and depot keys in Steam library. Install manually in Steam whenever you want.")
        h_sub.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.65); background: transparent;")
        h_sub.setWordWrap(True)
        handoff_layout.addWidget(h_title)
        handoff_layout.addWidget(h_sub)
        self._style_option_btn(self.handoff_btn, primary=False)
        layout.addWidget(self.handoff_btn)

        # Bottom row: Remember checkbox + Cancel button
        bot_layout = QHBoxLayout()
        bot_layout.setContentsMargins(0, 8, 0, 0)

        self.remember_chk = QCheckBox("Remember my choice")
        self.remember_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remember_chk.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.75);")
        bot_layout.addWidget(self.remember_chk)

        bot_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(80, 28)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.07);
                color: rgba(255, 255, 255, 0.8);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.14);
                color: #FFFFFF;
            }
        """)
        cancel_btn.clicked.connect(self._on_cancel_clicked)
        bot_layout.addWidget(cancel_btn)

        layout.addLayout(bot_layout)

    def _style_option_btn(self, btn: QPushButton, primary: bool):
        border_color = self.accent_color if primary else "rgba(255, 255, 255, 0.18)"
        bg_color = "rgba(255, 255, 255, 0.05)"
        hover_bg = "rgba(255, 255, 255, 0.10)"
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
                border-color: {self.accent_color};
            }}
        """)

    def _on_track_clicked(self):
        self._action = ACTION_TRACK
        self.accept()

    def _on_handoff_clicked(self):
        self._action = ACTION_HANDOFF
        self.accept()

    def _on_cancel_clicked(self):
        self._action = ACTION_CANCEL
        self.reject()

    def get_action(self) -> int:
        return self._action

    def should_remember(self) -> bool:
        return self.remember_chk.isChecked()
