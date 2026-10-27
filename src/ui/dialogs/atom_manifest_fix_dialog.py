"""
atom_manifest_fix_dialog.py - Modal dialog for fixing Steam manifest on AT0-M games.

Offers two choices vertically:
1. Delete Manifest (.acf) [Recommended] - Removes stale ACF so Steam cleanly rediscovers files.
2. Mark Update Only (StateFlags=6) - Sets StateFlags to 6 so Steam flags an update required.
"""

from typing import Optional
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
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
from utils.settings import get_settings
from utils.color_utils import get_best_foreground_color

ACTION_CANCEL = 0
ACTION_DELETE_ACF = 1
ACTION_MARK_UPDATE = 2


class AtomManifestFixDialog(QDialog):
    """
    Dialog asking the user how to fix an AT0-M game's Steam manifest.
    """

    def __init__(
        self,
        parent: Optional[QWidget],
        game_name: str,
        appid: str,
        acf_path: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Fix Manifest")
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setMaximumWidth(520)

        self.game_name = game_name or f"App {appid}"
        self.appid = str(appid)
        self.acf_path = acf_path
        self.choice = ACTION_CANCEL

        settings = get_settings()
        self.accent_color = (
            getattr(parent, "accent_color", None)
            or (settings.value("accent_color", "#a1c9fd") if settings else "#a1c9fd")
        )

        self._init_ui()

    def _init_ui(self) -> None:
        layout = install_themed_frame(self, "Fix Manifest", use_power_close=True)
        layout.setSpacing(14)

        # Header description
        desc_lbl = QLabel(
            f"What would you like to do with the Steam manifest for <b>{self.game_name}</b> ({self.appid})?"
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setTextFormat(Qt.TextFormat.RichText)
        desc_lbl.setStyleSheet("font-size: 9.5pt; color: #E8E8EC; background: transparent;")
        layout.addWidget(desc_lbl)

        # Options Container
        options_layout = QVBoxLayout()
        options_layout.setSpacing(10)

        # -------------------------------------------------------------
        # Option 1 (Top, Recommended): Delete ACF
        # -------------------------------------------------------------
        c = QColor(self.accent_color)
        if not c.isValid():
            c = QColor("#a1c9fd")
        fg = get_best_foreground_color(c)

        opt1_frame = QFrame()
        opt1_frame.setObjectName("opt1Frame")
        opt1_frame.setStyleSheet(f"""
            QFrame#opt1Frame {{
                background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.08);
                border: 1px solid rgba({c.red()}, {c.green()}, {c.blue()}, 0.35);
                border-radius: 8px;
            }}
        """)
        opt1_lay = QVBoxLayout(opt1_frame)
        opt1_lay.setContentsMargins(14, 12, 14, 12)
        opt1_lay.setSpacing(6)

        opt1_hdr = QHBoxLayout()
        opt1_title = QLabel("Delete Manifest (.acf)")
        opt1_title.setStyleSheet("font-size: 10pt; font-weight: bold; color: #FFFFFF; background: transparent;")
        opt1_hdr.addWidget(opt1_title)

        opt1_badge = QLabel("Recommended")
        opt1_badge.setStyleSheet(f"""
            background-color: {self.accent_color};
            color: {fg};
            font-size: 8pt;
            font-weight: bold;
            border-radius: 4px;
            padding: 2px 6px;
        """)
        opt1_hdr.addWidget(opt1_badge)
        opt1_hdr.addStretch()
        opt1_lay.addLayout(opt1_hdr)

        opt1_desc = QLabel(
            "Deletes the manifest file so Steam natively rediscovers and adopts installed files "
            "cleanly on next startup without carrying over corrupted state flags."
        )
        opt1_desc.setWordWrap(True)
        opt1_desc.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.75); background: transparent;")
        opt1_lay.addWidget(opt1_desc)

        btn_delete = QPushButton("Delete Manifest (.acf)")
        btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_delete.setFixedHeight(34)
        btn_delete.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: {fg};
                border: none;
                border-radius: 5px;
                font-weight: bold;
                font-size: 9pt;
            }}
            QPushButton:hover {{
                background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.85);
            }}
        """)
        btn_delete.clicked.connect(self._on_delete_acf)
        opt1_lay.addWidget(btn_delete)
        options_layout.addWidget(opt1_frame)

        # -------------------------------------------------------------
        # Option 2 (Bottom): Mark Update Only
        # -------------------------------------------------------------
        opt2_frame = QFrame()
        opt2_frame.setObjectName("opt2Frame")
        opt2_frame.setStyleSheet("""
            QFrame#opt2Frame {{
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
            }}
        """)
        opt2_lay = QVBoxLayout(opt2_frame)
        opt2_lay.setContentsMargins(14, 12, 14, 12)
        opt2_lay.setSpacing(6)

        opt2_hdr = QHBoxLayout()
        opt2_title = QLabel("Mark Update Only")
        opt2_title.setStyleSheet("font-size: 10pt; font-weight: bold; color: #FFFFFF; background: transparent;")
        opt2_hdr.addWidget(opt2_title)

        opt2_badge = QLabel("StateFlags = 6")
        opt2_badge.setStyleSheet("""
            background-color: rgba(255, 255, 255, 0.12);
            color: rgba(255, 255, 255, 0.85);
            font-size: 8pt;
            font-weight: bold;
            border-radius: 4px;
            padding: 2px 6px;
        """)
        opt2_hdr.addWidget(opt2_badge)
        opt2_hdr.addStretch()
        opt2_lay.addLayout(opt2_hdr)

        opt2_desc = QLabel(
            "Modifies the manifest's StateFlags to 6 so Steam flags an update required to "
            "verify and download missing files."
        )
        opt2_desc.setWordWrap(True)
        opt2_desc.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.75); background: transparent;")
        opt2_lay.addWidget(opt2_desc)

        btn_update = QPushButton("Mark Update Only")
        btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_update.setFixedHeight(34)
        btn_update.setStyleSheet("""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                font-weight: bold;
                font-size: 9pt;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.14);
            }}
        """)
        btn_update.clicked.connect(self._on_mark_update)
        opt2_lay.addWidget(btn_update)
        options_layout.addWidget(opt2_frame)

        layout.addLayout(options_layout)

        # Cancel button row
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setFixedSize(90, 32)
        btn_cancel.setStyleSheet("""
            QPushButton {{
                background-color: transparent;
                color: rgba(255, 255, 255, 0.65);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                font-size: 8.5pt;
            }}
            QPushButton:hover {{
                color: #FFFFFF;
                border-color: rgba(255, 255, 255, 0.35);
            }}
        """)
        btn_cancel.clicked.connect(self.reject)
        bottom_row.addWidget(btn_cancel)
        layout.addLayout(bottom_row)

    def _on_delete_acf(self) -> None:
        self.choice = ACTION_DELETE_ACF
        self.accept()

    def _on_mark_update(self) -> None:
        self.choice = ACTION_MARK_UPDATE
        self.accept()
