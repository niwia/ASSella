"""
atom_complications_dialog.py - Preflight review dialog when moving a game to AT0-M.

Displayed only when the game being migrated has active complications:
- Pinned build (manifest overrides to write in config.yaml)
- DLC-Only mode (mapped DLC AppIDs, DLC depots, and keys)
- EOS Proxy active (recalled before handoff)
- Goldberg Emulator active (reverted before handoff)
"""

import logging
from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.themed_dialog import install_themed_frame
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class AtomMigrationComplicationsDialog(QDialog):
    """
    Review modal shown before moving a game with complications to AT0-M.
    """

    def __init__(
        self,
        parent: Optional[QWidget],
        game_name: str,
        appid: str,
        complications: Dict[str, Any],
    ):
        super().__init__(parent)
        self.setWindowTitle("AT0-M Migration Review")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setMaximumWidth(600)

        self.game_name = game_name
        self.appid = str(appid)
        self.complications = complications

        settings = get_settings()
        self.accent_color = (
            getattr(parent, "accent_color", None)
            or (settings.value("accent_color", "#a1c9fd") if settings else "#a1c9fd")
        )

        self._init_ui()

    def _init_ui(self) -> None:
        layout = install_themed_frame(self, "AT0-M Migration Review", use_power_close=True)
        layout.setSpacing(14)

        # Header description
        header_lbl = QLabel(
            f"Review the configuration and adjustments before moving <b>{self.game_name}</b> "
            f"({self.appid}) to <b>AT0-M (Steam Native)</b> mode:"
        )
        header_lbl.setWordWrap(True)
        header_lbl.setTextFormat(Qt.TextFormat.RichText)
        header_lbl.setStyleSheet("font-size: 9.5pt; color: #E8E8EC; background: transparent;")
        layout.addWidget(header_lbl)

        # Scrollable container for cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 4, 0)
        content_layout.setSpacing(12)

        pinned = self.complications.get("pinned")
        dlc_only = self.complications.get("dlc_only")
        eos = self.complications.get("eos")
        goldberg = self.complications.get("goldberg")

        # 1. Pinned Build Card
        if pinned:
            content_layout.addWidget(self._create_pinned_card(pinned))

        # 2. DLC-Only Card
        if dlc_only:
            content_layout.addWidget(self._create_dlc_card(dlc_only))

        # 3. EOS Proxy Card
        if eos:
            content_layout.addWidget(self._create_eos_card())

        # 4. Goldberg Emulator Card
        if goldberg:
            content_layout.addWidget(self._create_goldberg_card())

        content_layout.addStretch()
        scroll.setWidget(content_widget)
        layout.addWidget(scroll, 1)

        # Bottom buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedHeight(34)
        self.cancel_btn.setMinimumWidth(100)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.05);
                color: #C0C0C8;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                font-weight: 500;
                font-size: 9pt;
                padding: 4px 16px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: #FFFFFF;
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.proceed_btn = QPushButton("Proceed with Migration")
        self.proceed_btn.setFixedHeight(34)
        self.proceed_btn.setMinimumWidth(170)
        self.proceed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.proceed_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: #121214;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 9pt;
                padding: 4px 18px;
            }}
            QPushButton:hover {{
                background-color: {self._get_hover_color()};
            }}
            QPushButton:pressed {{
                background-color: {self.accent_color};
            }}
        """)
        self.proceed_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.proceed_btn)

        layout.addLayout(btn_layout)

    def _create_card_frame(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.035);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
        """)
        return card

    def _create_pinned_card(self, pinned_info: Dict[str, Any]) -> QFrame:
        card = self._create_card_frame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)

        title = QLabel("📌 Pinned Build Overrides (ManifestIds)")
        title.setStyleSheet(f"font-weight: bold; font-size: 9.5pt; color: {self.accent_color}; border: none;")
        card_layout.addWidget(title)

        bid = pinned_info.get("build_id") or "Unknown"
        manifest_map = pinned_info.get("manifest_map") or {}

        desc = QLabel(
            f"This game has build <b>{bid}</b> pinned. To prevent Steam from auto-updating and "
            f"overwriting game files, the following depot manifest overrides will be locked in "
            f"SLSsteam <code>ManifestIds</code>:"
        )
        desc.setWordWrap(True)
        desc.setTextFormat(Qt.TextFormat.RichText)
        desc.setStyleSheet("font-size: 8.5pt; color: #D0D0D8; border: none; background: transparent;")
        card_layout.addWidget(desc)

        if manifest_map:
            lines = [f"{did}  ➔  {gid}" for did, gid in sorted(manifest_map.items())]
            box_text = "\n".join(lines)
            box = QLabel(box_text)
            box.setStyleSheet("""
                font-family: monospace;
                font-size: 8pt;
                color: #A0C0E8;
                background-color: rgba(0, 0, 0, 0.35);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 4px;
                padding: 6px;
            """)
            card_layout.addWidget(box)

        return card

    def _create_dlc_card(self, dlc_info: Dict[str, Any]) -> QFrame:
        card = self._create_card_frame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)

        title = QLabel("🧩 DLC-Only Configuration")
        title.setStyleSheet(f"font-weight: bold; font-size: 9.5pt; color: {self.accent_color}; border: none;")
        card_layout.addWidget(title)

        desc = QLabel(
            "Game is installed in <b>DLC-Only</b> mode (base game is owned on Steam). "
            "Only the DLC entitlements mapped to your selected depots will be injected into SLSsteam:"
        )
        desc.setWordWrap(True)
        desc.setTextFormat(Qt.TextFormat.RichText)
        desc.setStyleSheet("font-size: 8.5pt; color: #D0D0D8; border: none; background: transparent;")
        card_layout.addWidget(desc)

        dlc_appids = dlc_info.get("dlc_appids") or []
        depots = dlc_info.get("depots") or []
        key_depots = dlc_info.get("key_depot_ids") or []

        apps_str = ", ".join(str(a) for a in dlc_appids) if dlc_appids else "None resolved"
        depots_str = ", ".join(str(d) for d in depots) if depots else "None"
        keys_str = ", ".join(str(d) for d in key_depots) if key_depots else "None"

        details = QLabel(
            f"• <b>AdditionalApps (DLC AppIDs):</b> {apps_str}<br>"
            f"• <b>AdditionalDepots (Content Depots):</b> {depots_str}<br>"
            f"• <b>DecryptionKeys (Key Depot IDs):</b> {keys_str}<br><br>"
            f"<span style='color: #85E89D;'>✓ Base AppID {self.appid} is strictly excluded from "
            f"AdditionalApps, preserving your legitimate base game Steam license.</span>"
        )
        details.setWordWrap(True)
        details.setTextFormat(Qt.TextFormat.RichText)
        details.setStyleSheet("font-size: 8.5pt; color: #C8C8D0; border: none; background: transparent;")
        card_layout.addWidget(details)

        return card

    def _create_eos_card(self) -> QFrame:
        card = self._create_card_frame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)

        title = QLabel("🛡️ Epic Online Services (EOS) Proxy")
        title.setStyleSheet(f"font-weight: bold; font-size: 9.5pt; color: {self.accent_color}; border: none;")
        card_layout.addWidget(title)

        desc = QLabel(
            "An <b>EOS Proxy</b> was detected in the game directory. The proxy DLL will be recalled "
            "and the original genuine EOS DLL restored, as Steam will overwrite modified files during adoption.<br><br>"
            "💡 <i>You can re-enable the EOS Proxy from the Game Details dialog after Steam finishes "
            "adopting/installing the game.</i>"
        )
        desc.setWordWrap(True)
        desc.setTextFormat(Qt.TextFormat.RichText)
        desc.setStyleSheet("font-size: 8.5pt; color: #D0D0D8; border: none; background: transparent;")
        card_layout.addWidget(desc)

        return card

    def _create_goldberg_card(self) -> QFrame:
        card = self._create_card_frame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)

        title = QLabel("🕹️ Goldberg Steam Emulator")
        title.setStyleSheet(f"font-weight: bold; font-size: 9.5pt; color: {self.accent_color}; border: none;")
        card_layout.addWidget(title)

        desc = QLabel(
            "<b>Goldberg Emulator</b> was detected in the game directory. Emulator DLLs will be removed "
            "and genuine Steam binaries restored from <code>.valve</code> backups, because native Steam requires "
            "authentic Steamworks binaries and will otherwise fail or overwrite them."
        )
        desc.setWordWrap(True)
        desc.setTextFormat(Qt.TextFormat.RichText)
        desc.setStyleSheet("font-size: 8.5pt; color: #D0D0D8; border: none; background: transparent;")
        card_layout.addWidget(desc)

        return card

    def _get_hover_color(self) -> str:
        try:
            qc = QColor(self.accent_color)
            h, s, v, a = qc.getHsv()
            val = min(255, int(v * 1.15)) if v > 0 else 30
            return QColor.fromHsv(h, s, val, a).name()
        except Exception:
            return self.accent_color
