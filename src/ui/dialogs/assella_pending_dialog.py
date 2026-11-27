"""Small dialog for an ASSella game that is registered in AdditionalApps but not downloaded."""

from __future__ import annotations

import logging
from typing import Any, Dict

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ui.dialogs.themed_dialog import install_themed_frame

logger = logging.getLogger(__name__)

MUTED = "rgba(255, 255, 255, 0.55)"


class AssellaPendingDialog(QDialog):
    """Shows status of an uninstalled ASSella game and lets the user download or remove it."""

    def __init__(self, game: Dict[str, Any], parent=None, accent_color: str = "#a1c9fd"):
        super().__init__(parent)
        self.game = game
        self.removed = False
        self.download_requested = False
        self.accent_color = accent_color
        self.setWindowTitle(f"{game.get('game_name', 'Game')} — ASSella")
        self.setMinimumWidth(460)

        body = install_themed_frame(self, margins=(18, 14, 18, 16), spacing=12)

        # ── Header ──
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        title = QLabel(game.get("game_name", "Game"))
        title.setWordWrap(True)
        title.setStyleSheet("color: #FFFFFF; font-size: 13pt; font-weight: 700; background: transparent;")
        title_col.addWidget(title)

        appid_str = str(game.get("appid", ""))
        sub = QLabel(f"AppID: {appid_str} • Mode: ASSella")
        sub.setStyleSheet(f"color: {MUTED}; font-size: 8.5pt; background: transparent;")
        title_col.addWidget(sub)
        header_row.addLayout(title_col, 1)

        # Status badge pill
        state_badge = QLabel("  Not downloaded  ")
        state_badge.setStyleSheet(f"""
            QLabel {{
                color: {accent_color};
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid {accent_color};
                border-radius: 12px;
                padding: 4px 8px;
                font-size: 8.5pt;
                font-weight: 600;
            }}
        """)
        header_row.addWidget(state_badge, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        body.addLayout(header_row)

        # ── Info Card ──
        info_card = QFrame()
        info_card.setObjectName("pendingInfoCard")
        info_card.setStyleSheet("""
            QFrame#pendingInfoCard {
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
            }
        """)
        info_lay = QVBoxLayout(info_card)
        info_lay.setContentsMargins(12, 10, 12, 10)
        info_lay.setSpacing(6)

        status_lbl = QLabel(
            "• <b>Status:</b> Registered with SLSsteam in <code>AdditionalApps</code>, but no game files exist on disk."
        )
        status_lbl.setWordWrap(True)
        status_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 9pt; background: transparent;")
        info_lay.addWidget(status_lbl)

        desc_lbl = QLabel(
            "• <b>Action:</b> You can start a fresh installation to download game files, or cleanly remove it from AdditionalApps."
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 9pt; background: transparent;")
        info_lay.addWidget(desc_lbl)

        body.addWidget(info_card)

        # ── Action Buttons ──
        btns = QHBoxLayout()
        btns.setContentsMargins(0, 4, 0, 0)
        btns.setSpacing(8)

        remove_btn = QPushButton("Remove from AdditionalApps")
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.setFixedHeight(32)
        remove_btn.setToolTip(
            "Purges the game from SLSsteam config.yaml and sends uninstall command.\n"
            "Nothing is downloaded yet, so no files will be lost."
        )
        remove_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #EF5350;
                border: 1px solid rgba(239, 83, 80, 0.45);
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background: rgba(239, 83, 80, 0.15);
                border-color: #EF5350;
                color: #FF8A80;
            }
        """)
        remove_btn.clicked.connect(self._remove)
        btns.addWidget(remove_btn)

        btns.addStretch(1)

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedHeight(32)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: rgba(255, 255, 255, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 6px;
                padding: 4px 14px;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.15);
                color: #FFFFFF;
            }
        """)
        close_btn.clicked.connect(self.reject)
        btns.addWidget(close_btn)

        download_btn = QPushButton("Download Game")
        download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        download_btn.setFixedHeight(32)
        download_btn.setStyleSheet(f"""
            QPushButton {{
                background: {accent_color};
                color: #000000;
                font-weight: 700;
                border: none;
                border-radius: 6px;
                padding: 4px 16px;
                font-size: 8.5pt;
            }}
            QPushButton:hover {{
                background: #FFFFFF;
                color: #000000;
            }}
        """)
        download_btn.clicked.connect(self._download)
        btns.addWidget(download_btn)

        body.addLayout(btns)
        self.adjustSize()

    def _download(self) -> None:
        self.download_requested = True
        self.accept()

    def _remove(self) -> None:
        from utils.assella_pending import remove_pending_assella_game

        ok, msg = remove_pending_assella_game(self.game)
        if not ok:
            from ui.dialogs.themed_message_box import ThemedMessageBox
            ThemedMessageBox.warning(self, "Remove from AdditionalApps", msg)
            return
        self.removed = True
        self.accept()
