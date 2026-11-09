"""Small dialog for an AT0-M game that is registered but not downloaded yet."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.themed_dialog import install_themed_frame

logger = logging.getLogger(__name__)

MUTED = "rgba(255, 255, 255, 0.55)"


class AtomPendingDialog(QDialog):
    """Shows where an added AT0-M game stands and lets the user start or remove it."""

    def __init__(self, game: Dict[str, Any], parent=None, accent_color: str = "#a1c9fd"):
        super().__init__(parent)
        self.game = game
        self.removed = False
        self.accent_color = accent_color
        self.setWindowTitle(f"{game.get('game_name', 'Game')} — AT0-M")
        self.setMinimumWidth(460)

        from utils.atom_pending import pending_label

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
        ts = int(game.get("pending_updated_at") or 0)
        date_str = f" • Added {datetime.fromtimestamp(ts).strftime('%d/%m/%y %H:%M')}" if ts else ""
        sub = QLabel(f"AppID: {appid_str}{date_str}")
        sub.setStyleSheet(f"color: {MUTED}; font-size: 8.5pt; background: transparent;")
        title_col.addWidget(sub)
        header_row.addLayout(title_col, 1)

        # Status badge pill
        text, color = pending_label(game)
        state_badge = QLabel(f"  {text}  ")
        state_badge.setStyleSheet(f"""
            QLabel {{
                color: {color};
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid {color};
                border-radius: 12px;
                padding: 4px 8px;
                font-size: 8.5pt;
                font-weight: 600;
            }}
        """)
        header_row.addWidget(state_badge, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        body.addLayout(header_row)

        # ── Info Card ──
        # Styled by object name: a bare "QFrame { }" rule also hits every QLabel
        # inside (QLabel is a QFrame) and draws a box around each line.
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

        is_queued = game.get("pending_state") == "queued"
        status_msg = (
            "Steam has this game queued for download. Start or resume it from Steam's Downloads page."
            if is_queued
            else "Registered with SLSsteam and AT0-M. Steam has not started downloading it yet."
        )
        status_lbl = QLabel(f"• <b>Status:</b> {status_msg}")
        status_lbl.setWordWrap(True)
        status_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 9pt; background: transparent;")
        info_lay.addWidget(status_lbl)

        if game.get("is_dlc_only"):
            dlc_lbl = QLabel("• <b>DLC-Only Mode:</b> Base game is your genuine Steam copy; AT0-M unlocks extra DLC depots.")
            dlc_lbl.setWordWrap(True)
            dlc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 9pt; background: transparent;")
            info_lay.addWidget(dlc_lbl)

        body.addWidget(info_card)

        # ── Issue / Repair Card (if any issues exist) ──
        issues = game.get("pending_issues") or []
        if issues:
            issue_card = QFrame()
            issue_card.setObjectName("pendingIssueCard")
            issue_card.setStyleSheet("""
                QFrame#pendingIssueCard {
                    background: rgba(255, 171, 0, 0.10);
                    border: 1px solid rgba(255, 171, 0, 0.35);
                    border-radius: 8px;
                }
            """)
            issue_lay = QVBoxLayout(issue_card)
            issue_lay.setContentsMargins(12, 10, 12, 12)
            issue_lay.setSpacing(6)

            issue_hdr = QLabel("⚠ Configuration Needs Attention")
            issue_hdr.setStyleSheet("color: #FFD54F; font-size: 9.5pt; font-weight: bold; background: transparent;")
            issue_lay.addWidget(issue_hdr)

            for issue in issues:
                lbl = QLabel(f"• {issue[0].upper() + issue[1:]}")
                lbl.setWordWrap(True)
                lbl.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 8.5pt; background: transparent;")
                issue_lay.addWidget(lbl)

            repair_btn = QPushButton("Repair Configuration")
            repair_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            repair_btn.setFixedHeight(30)
            repair_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255, 171, 0, 0.25);
                    color: #FFE082;
                    border: 1px solid rgba(255, 171, 0, 0.55);
                    border-radius: 5px;
                    font-weight: 600;
                    font-size: 8.5pt;
                }
                QPushButton:hover {
                    background: rgba(255, 171, 0, 0.40);
                    color: #FFFFFF;
                }
            """)
            repair_btn.clicked.connect(self._repair)
            issue_lay.addWidget(repair_btn)
            body.addWidget(issue_card)

        # ── Action Buttons ──
        btns = QHBoxLayout()
        btns.setContentsMargins(0, 4, 0, 0)
        btns.setSpacing(8)

        remove_btn = QPushButton("Remove from AT0-M")
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.setFixedHeight(32)
        remove_btn.setToolTip(
            "Removes the game from plugin_library.json and SLSsteam's config.yaml.\n"
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

        steam_btn = QPushButton("Open Downloads" if is_queued else "Install in Steam")
        steam_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        steam_btn.setFixedHeight(32)
        steam_btn.setStyleSheet(f"""
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
        steam_btn.clicked.connect(self._open_steam)
        btns.addWidget(steam_btn)

        body.addLayout(btns)
        self.adjustSize()

    def _open_steam(self) -> None:
        appid = self.game.get("appid")
        url = "steam://nav/downloads" if self.game.get("pending_state") == "queued" else f"steam://install/{appid}"
        QDesktopServices.openUrl(QUrl(url))
        self.accept()

    def _repair(self) -> None:
        from ui.dialogs.themed_message_box import ThemedMessageBox
        from utils.atom_pending import repair_pending_atom_game

        ok, msg = repair_pending_atom_game(self.game)
        if not ok:
            ThemedMessageBox.warning(self, "Repair", msg)
            return
        self.removed = True  # list needs a refresh
        self.accept()

    def _remove(self) -> None:
        from ui.dialogs.themed_message_box import ThemedMessageBox
        from utils.atom_pending import remove_pending_atom_game

        name = self.game.get("game_name", "this game")
        reply = ThemedMessageBox.question(
            self,
            "Remove from AT0-M",
            f"Remove {name} from AT0-M?\n\nIt is taken out of plugin_library.json and SLSsteam's config.yaml. "
            "Entries other games still use are kept. You can add it again any time.",
            ThemedMessageBox.StandardButton.Ok | ThemedMessageBox.StandardButton.Cancel,
            ThemedMessageBox.StandardButton.Cancel,
        )
        if reply != ThemedMessageBox.StandardButton.Ok:
            return
        ok, msg = remove_pending_atom_game(self.game)
        if not ok:
            ThemedMessageBox.warning(self, "Remove from AT0-M", msg)
            return
        self.removed = True
        self.accept()
