"""
Batch AT0-M Migration Confirmation Dialog.

Displays a detailed breakdown of games selected for migration to AT0-M (Steam Native)
mode, clearly categorizing eligible games and skipped games (with reasons like
DLC-Only Mode, Pinned Manifest/Build, Already AT0-M, etc.).
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.themed_dialog import ThemedDialog
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class BatchAtomMigrationConfirmDialog(ThemedDialog):
    """Themed confirmation dialog showing eligible vs skipped games before AT0-M batch migration."""

    def __init__(
        self,
        eligible_games: List[Dict[str, Any]],
        skipped_games: List[Tuple[Dict[str, Any], str]],
        accent_color: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(
            parent,
            title="Move Games to AT0-M",
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
            margins=(20, 14, 20, 18),
            spacing=12,
        )
        self.eligible_games = eligible_games
        self.skipped_games = skipped_games
        self.accent_color = accent_color or "#a1c9fd"

        self.setMinimumWidth(540)
        self.setMaximumWidth(680)
        self.resize(580, 560)

        self._build_content()

    def _build_content(self) -> None:
        # 1. Header explanation text
        desc_lbl = QLabel(
            "Move selected games from ASSella-managed to <b>AT0-M (Steam Native)</b> mode.<br>"
            "Local marker files (<code>.DepotDownloader</code>) will be removed, and required "
            "AppIDs, Depots, and DecryptionKeys will be registered into SLSsteam config."
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.75); font-size: 9pt; line-height: 1.4;")
        self.body.addWidget(desc_lbl)

        # 2. Changes applied to every migrated game. The migration touches
        #    user-visible settings, so they are listed instead of being
        #    applied silently.
        changes_lbl = QLabel(
            "<b>Also applied to every game:</b> excluded from Update&nbsp;All "
            "(AT0-M games are updated by Steam), and the existing branch selection is kept."
        )
        changes_lbl.setWordWrap(True)
        changes_lbl.setStyleSheet(
            "color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; line-height: 1.4; margin-top: 2px;"
        )
        self.body.addWidget(changes_lbl)

        # 3. Eligible Games Section
        eligible_count = len(self.eligible_games)
        lbl_eligible_hdr = QLabel(f"Eligible for Migration ({eligible_count})")
        lbl_eligible_hdr.setStyleSheet(
            f"font-size: 10pt; font-weight: bold; color: {self.accent_color}; margin-top: 4px;"
        )
        self.body.addWidget(lbl_eligible_hdr)

        scroll_eligible = QScrollArea()
        scroll_eligible.setWidgetResizable(True)
        scroll_eligible.setFrameShape(QFrame.Shape.NoFrame)
        scroll_eligible.setMaximumHeight(180)
        scroll_eligible.setStyleSheet("background: transparent;")

        eligible_container = QWidget()
        eligible_lay = QVBoxLayout(eligible_container)
        eligible_lay.setContentsMargins(0, 0, 0, 0)
        eligible_lay.setSpacing(6)

        if eligible_count == 0:
            no_elig = QLabel("No eligible games selected.")
            no_elig.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-style: italic; padding: 8px 4px;")
            eligible_lay.addWidget(no_elig)
        else:
            for g in self.eligible_games:
                if g.get("acf_pending"):
                    status_text = "Re-run: adopt Steam manifest"
                    status_color = "#f59e0b"  # amber
                    bg_badge = "rgba(245, 158, 11, 0.15)"
                else:
                    status_text = "Ready to Migrate"
                    status_color = "#10b981"  # emerald green
                    bg_badge = "rgba(16, 185, 129, 0.15)"
                row = self._create_game_row(
                    title=g.get("game_name") or f"App {g.get('appid')}",
                    appid=str(g.get("appid", "")),
                    status_text=status_text,
                    status_color=status_color,
                    bg_badge=bg_badge,
                )
                eligible_lay.addWidget(row)

        scroll_eligible.setWidget(eligible_container)
        self.body.addWidget(scroll_eligible)

        # 4. Skipped / Excluded Games Section (if any)
        skipped_count = len(self.skipped_games)
        if skipped_count > 0:
            lbl_skipped_hdr = QLabel(f"Excluded Games ({skipped_count})")
            lbl_skipped_hdr.setStyleSheet(
                "font-size: 10pt; font-weight: bold; color: rgba(255, 255, 255, 0.85); margin-top: 6px;"
            )
            self.body.addWidget(lbl_skipped_hdr)

            scroll_skipped = QScrollArea()
            scroll_skipped.setWidgetResizable(True)
            scroll_skipped.setFrameShape(QFrame.Shape.NoFrame)
            scroll_skipped.setMaximumHeight(160)
            scroll_skipped.setStyleSheet("background: transparent;")

            skipped_container = QWidget()
            skipped_lay = QVBoxLayout(skipped_container)
            skipped_lay.setContentsMargins(0, 0, 0, 0)
            skipped_lay.setSpacing(6)

            for g, reason in self.skipped_games:
                color, bg = self._get_badge_colors(reason)
                row = self._create_game_row(
                    title=g.get("game_name") or f"App {g.get('appid')}",
                    appid=str(g.get("appid", "")),
                    status_text=reason,
                    status_color=color,
                    bg_badge=bg,
                )
                skipped_lay.addWidget(row)

            scroll_skipped.setWidget(skipped_container)
            self.body.addWidget(scroll_skipped)

        self.body.addStretch()

        # 5. Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        btn_cancel = QPushButton("Cancel" if eligible_count > 0 else "Close")
        btn_cancel.setFixedHeight(34)
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 0 16px;
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.15);
            }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        if eligible_count > 0:
            btn_proceed = QPushButton(f"Move {eligible_count} Game{'s' if eligible_count > 1 else ''} to AT0-M")
            btn_proceed.setFixedHeight(34)
            btn_proceed.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_proceed.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.accent_color};
                    color: #000000;
                    border: none;
                    border-radius: 6px;
                    padding: 0 20px;
                    font-size: 9pt;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: #FFFFFF;
                    color: #000000;
                }}
            """)
            btn_proceed.clicked.connect(self.accept)
            btn_layout.addWidget(btn_proceed)

        self.body.addLayout(btn_layout)

    def _create_game_row(
        self,
        title: str,
        appid: str,
        status_text: str,
        status_color: str,
        bg_badge: str,
    ) -> QFrame:
        """Create a stylized list item frame for a game."""
        card = QFrame()
        card.setFixedHeight(38)
        card.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.035);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 6px;
            }
        """)
        hlay = QHBoxLayout(card)
        hlay.setContentsMargins(12, 0, 12, 0)
        hlay.setSpacing(10)

        # Title
        lbl_title = QLabel(title)
        lbl_title.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
        hlay.addWidget(lbl_title, 1)

        # AppID
        lbl_aid = QLabel(f"AppID: {appid}")
        lbl_aid.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 8pt;")
        hlay.addWidget(lbl_aid)

        # Status badge pill
        lbl_badge = QLabel(f" {status_text} ")
        lbl_badge.setStyleSheet(f"""
            QLabel {{
                background-color: {bg_badge};
                color: {status_color};
                border: 1px solid {status_color}55;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 7.5pt;
                font-weight: bold;
            }}
        """)
        hlay.addWidget(lbl_badge)

        return card

    def _get_badge_colors(self, reason: str) -> Tuple[str, str]:
        """Return (text_color, bg_color) based on exclusion reason."""
        r = reason.lower()
        if "dlc" in r:
            # Amber / warning
            return "#f59e0b", "rgba(245, 158, 11, 0.15)"
        elif "pinned" in r:
            # Cyan / info
            return "#06b6d4", "rgba(6, 182, 212, 0.15)"
        elif "already" in r:
            # Purple / neutral
            return "#a855f7", "rgba(168, 85, 247, 0.15)"
        elif "installed" in r:
            # Rose / error
            return "#f43f5e", "rgba(244, 63, 94, 0.15)"
        else:
            return "#9ca3af", "rgba(156, 163, 175, 0.15)"


from PyQt6.QtCore import QThread, pyqtSignal


class BatchAtomMigrationWorker(QThread):
    """Background worker for batch migrating games to AT0-M without blocking the UI."""

    progress = pyqtSignal(int, int, str, str)  # current, total, game_name, status_message
    finished = pyqtSignal(dict)  # results dict: {"migrated": [...], "failed": [...]}

    def __init__(self, games: List[Dict[str, Any]], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.games = games
        self._is_canceled = False

    def cancel(self) -> None:
        """Signal the worker to cancel processing remaining games."""
        self._is_canceled = True

    def run(self) -> None:
        """Execute the batch migration in background thread."""
        from utils.plugin_games import batch_convert_games_to_atom

        def _progress_cb(cur: int, tot: int, name: str, msg: str) -> None:
            if not self._is_canceled:
                self.progress.emit(cur, tot, name, msg)

        try:
            results = batch_convert_games_to_atom(
                self.games,
                progress_callback=_progress_cb,
                cancel_check=lambda: self._is_canceled,
            )
        except Exception as e:
            # A raise here would leave the progress dialog spinning forever.
            logger.exception("[BatchAtomMigration] Batch migration crashed")
            results = {
                "migrated": [],
                "failed": [
                    {
                        "appid": "",
                        "name": "Batch migration",
                        "reason": f"Unexpected error: {e}",
                    }
                ],
            }
        self.finished.emit(results)

