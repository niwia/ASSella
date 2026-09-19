"""
Recommended Build Prompt Dialog
===============================
Presents a dialog when downloading or verifying a game that has a curated
recommended build ID ("Voices"), allowing the user to select the recommended
pinned build, download the latest version, or browse SteamDB build history.
"""

from typing import Optional, Dict, Any
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
)

from utils.color_utils import get_dark_container_color, get_best_foreground_color


ACTION_RECOMMENDED = "recommended"
ACTION_LATEST = "latest"
ACTION_BROWSE = "browse"
ACTION_CANCEL = "cancel"


class RecommendedBuildPromptDialog(QDialog):
    """Modal dialog prompting user to choose between recommended build, latest, or custom."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        app_id: str = "",
        game_name: str = "",
        recommended_build_id: str = "",
        current_build_id: str = "",
        reason: str = "",
        accent_color: str = "#4c8df5",
    ):
        super().__init__(parent)
        self.app_id = str(app_id).strip()
        self.game_name = game_name or (f"App {self.app_id}" if self.app_id else "Game")
        self.recommended_build_id = str(recommended_build_id).strip()
        self.current_build_id = str(current_build_id).strip()
        self.reason = reason or "A specific build is recommended for optimal compatibility."
        self.accent_color = accent_color or "#4c8df5"

        self.selected_action = ACTION_CANCEL

        self.setWindowTitle(f"Voices38 Version — {self.game_name}")
        self.setMinimumWidth(480)
        self.setStyleSheet("""
            QDialog {
                background-color: #12131a;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 12px;
            }
        """)

        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 22, 24, 22)
        main_layout.setSpacing(18)

        # ── Header ──
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_lbl = QLabel("Voices38 version available.")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setStyleSheet("font-size: 13.5pt; font-weight: bold; color: #FFFFFF; border: none; background: transparent;")
        header_layout.addWidget(title_lbl)

        sub_text = self.game_name
        if self.app_id and self.app_id != "0":
            sub_text += f" ({self.app_id})"
        subtitle_lbl = QLabel(sub_text)
        subtitle_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_lbl.setStyleSheet("font-size: 10pt; color: rgba(255, 255, 255, 0.65); border: none; background: transparent;")
        header_layout.addWidget(subtitle_lbl)

        main_layout.addLayout(header_layout)

        # ── Description ──
        desc_text = (
            f'For playing with voices38 crack : '
            f'<span style="color: {self.accent_color}; font-weight: bold;">{self.recommended_build_id}</span><br>'
            f'<span style="color: rgba(255, 255, 255, 0.55); font-size: 8.5pt;">Note: You will need to find and apply the crack yourself!</span>'
        )
        desc_lbl = QLabel(desc_text)
        desc_lbl.setWordWrap(True)
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_lbl.setTextFormat(Qt.TextFormat.RichText)
        desc_lbl.setStyleSheet(
            "color: rgba(255, 255, 255, 0.85); font-size: 9.5pt; line-height: 1.5; border: none; background: transparent; padding: 2px 8px;"
        )
        main_layout.addWidget(desc_lbl)

        # ── Action Buttons ──
        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(9)

        # 1. Recommended Button (Primary Accent)
        fg_col = get_best_foreground_color(self.accent_color)
        btn_rec_text = f"Download Recommended (Build {self.recommended_build_id})"
        self.btn_rec = QPushButton(btn_rec_text)
        self.btn_rec.setFixedHeight(36)
        self.btn_rec.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rec.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: {fg_col};
                font-weight: 600;
                font-size: 9pt;
                border-radius: 8px;
                padding: 0 16px;
                border: none;
            }}
            QPushButton:hover {{
                background-color: {self.accent_color}DD;
            }}
            QPushButton:pressed {{
                background-color: {self.accent_color}AA;
            }}
        """)
        self.btn_rec.clicked.connect(self._on_recommended)
        actions_layout.addWidget(self.btn_rec)

        # 2. Latest Version Button (showing latest build id)
        if self.current_build_id:
            btn_latest_text = f"Download Latest Version (Build {self.current_build_id})"
        else:
            btn_latest_text = "Download Latest Version"
        self.btn_latest = QPushButton(btn_latest_text)
        self.btn_latest.setFixedHeight(36)
        self.btn_latest.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_latest.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                font-size: 9pt;
                font-weight: 600;
                border-radius: 8px;
                padding: 0 16px;
                border: 1px solid rgba(255, 255, 255, 0.15);
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.14);
                border-color: rgba(255, 255, 255, 0.25);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.18);
            }
        """)
        self.btn_latest.clicked.connect(self._on_latest)
        actions_layout.addWidget(self.btn_latest)

        # 3. Choose Another Build...
        self.btn_browse = QPushButton("Choose Another Build...")
        self.btn_browse.setFixedHeight(36)
        self.btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: rgba(255, 255, 255, 0.75);
                font-size: 9pt;
                font-weight: 600;
                border-radius: 8px;
                padding: 0 16px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.06);
                color: #FFFFFF;
                border-color: rgba(255, 255, 255, 0.22);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.10);
            }
        """)
        self.btn_browse.clicked.connect(self._on_browse)
        actions_layout.addWidget(self.btn_browse)

        main_layout.addLayout(actions_layout)

    def _on_recommended(self):
        self.selected_action = ACTION_RECOMMENDED
        self.accept()

    def _on_latest(self):
        self.selected_action = ACTION_LATEST
        self.accept()

    def _on_browse(self):
        self.selected_action = ACTION_BROWSE
        self.accept()

    def get_action(self) -> str:
        return self.selected_action
