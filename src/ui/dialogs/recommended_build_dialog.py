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

        self.setWindowTitle(f"Recommended Version — {self.game_name}")
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
        main_layout.setContentsMargins(22, 20, 22, 20)
        main_layout.setSpacing(16)

        # ── Header ──
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)

        title_lbl = QLabel("Tested Version Available")
        title_lbl.setStyleSheet("font-size: 13pt; font-weight: bold; color: #FFFFFF;")
        header_layout.addWidget(title_lbl)

        sub_text = self.game_name
        if self.app_id and self.app_id != "0":
            sub_text += f" ({self.app_id})"
        subtitle_lbl = QLabel(sub_text)
        subtitle_lbl.setStyleSheet("font-size: 9.5pt; color: rgba(255, 255, 255, 0.65);")
        header_layout.addWidget(subtitle_lbl)

        main_layout.addLayout(header_layout)

        # ── Card Container ──
        card = QFrame()
        card_bg = get_dark_container_color(self.accent_color) or "rgba(255, 255, 255, 0.04)"
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {card_bg};
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 10px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        # Description / Reason
        desc_lbl = QLabel(self.reason)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 9.5pt; line-height: 1.4;")
        card_layout.addWidget(desc_lbl)

        # Build comparison details
        details_layout = QVBoxLayout()
        details_layout.setSpacing(6)

        rec_html = (
            f'<span style="color: rgba(255, 255, 255, 0.6); font-size: 9pt;">Recommended Build:</span> '
            f'<span style="color: {self.accent_color}; font-weight: bold; font-size: 10pt;">'
            f'{self.recommended_build_id}</span>'
            f' <span style="background-color: rgba(76, 141, 245, 0.2); color: {self.accent_color}; '
            f'border-radius: 3px; padding: 1px 6px; font-size: 8pt; font-weight: bold;">TESTED</span>'
        )
        rec_lbl = QLabel(rec_html)
        details_layout.addWidget(rec_lbl)

        current_desc = self.current_build_id if self.current_build_id else "Latest Steam Manifest"
        curr_html = (
            f'<span style="color: rgba(255, 255, 255, 0.45); font-size: 9pt;">Current / Latest:</span> '
            f'<span style="color: rgba(255, 255, 255, 0.7); font-size: 9pt;">{current_desc}</span>'
        )
        curr_lbl = QLabel(curr_html)
        details_layout.addWidget(curr_lbl)

        card_layout.addLayout(details_layout)
        main_layout.addWidget(card)

        # ── Action Buttons ──
        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(8)

        # 1. Recommended Button (Primary Accent)
        fg_col = get_best_foreground_color(self.accent_color)
        btn_rec_text = f"Download Recommended (Build {self.recommended_build_id})"
        self.btn_rec = QPushButton(btn_rec_text)
        self.btn_rec.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rec.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: {fg_col};
                font-weight: bold;
                font-size: 9.5pt;
                border-radius: 8px;
                padding: 9px 16px;
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

        # 2. Latest Version Button
        self.btn_latest = QPushButton("Download Latest Version")
        self.btn_latest.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_latest.setStyleSheet("""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                font-size: 9pt;
                border-radius: 8px;
                padding: 8px 16px;
                border: 1px solid rgba(255, 255, 255, 0.15);
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.14);
                border-color: rgba(255, 255, 255, 0.25);
            }}
        """)
        self.btn_latest.clicked.connect(self._on_latest)
        actions_layout.addWidget(self.btn_latest)

        # 3. Bottom Row: Browse Builds + Cancel
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        self.btn_browse = QPushButton("Choose Another Build...")
        self.btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse.setStyleSheet("""
            QPushButton {{
                background-color: transparent;
                color: rgba(255, 255, 255, 0.7);
                font-size: 8.5pt;
                border-radius: 6px;
                padding: 6px 12px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.06);
                color: #FFFFFF;
            }}
        """)
        self.btn_browse.clicked.connect(self._on_browse)
        bottom_row.addWidget(self.btn_browse)

        bottom_row.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setStyleSheet("""
            QPushButton {{
                background-color: transparent;
                color: rgba(255, 255, 255, 0.5);
                font-size: 8.5pt;
                border-radius: 6px;
                padding: 6px 12px;
                border: none;
            }}
            QPushButton:hover {{
                color: #FFFFFF;
            }}
        """)
        self.btn_cancel.clicked.connect(self.reject)
        bottom_row.addWidget(self.btn_cancel)

        actions_layout.addLayout(bottom_row)
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
