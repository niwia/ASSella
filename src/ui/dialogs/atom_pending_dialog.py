"""Small dialog for an AT0-M game that is registered but not downloaded yet."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton

from ui.dialogs.themed_dialog import install_themed_frame

logger = logging.getLogger(__name__)

MUTED = "rgba(255,255,255,0.55)"


class AtomPendingDialog(QDialog):
    """Shows where an added AT0-M game stands and lets the user start or remove it."""

    def __init__(self, game: Dict[str, Any], parent=None, accent_color: str = "#a1c9fd"):
        super().__init__(parent)
        self.game = game
        self.removed = False
        self.setWindowTitle(f"{game.get('game_name', 'Game')} — AT0-M")
        self.setMinimumWidth(440)

        from utils.atom_pending import pending_label

        body = install_themed_frame(self, margins=(16, 10, 16, 14), spacing=8)
        title = QLabel(game.get("game_name", "Game"))
        title.setStyleSheet("color: #FFFFFF; font-size: 13pt; font-weight: 700;")
        body.addWidget(title)
        appid = QLabel(f"<i>{game.get('appid')}</i>")
        appid.setStyleSheet(f"color: {MUTED}; font-size: 10pt;")
        body.addWidget(appid)

        text, color = pending_label(game)
        state = QLabel(text)
        state.setStyleSheet(f"color: {color}; font-weight: 600; font-size: 9.5pt;")
        body.addWidget(state)

        lines = []
        if game.get("pending_state") == "queued":
            lines.append("Steam has this game in its download queue. Start or resume it from Steam's Downloads page.")
        else:
            lines.append("Registered with SLSsteam and AT0-M, but Steam has not started downloading it.")
        if game.get("is_dlc_only"):
            lines.append("DLC-only: the base game is your own Steam copy; AT0-M unlocks its DLC.")
        ts = int(game.get("pending_updated_at") or 0)
        if ts:
            lines.append(f"Added {datetime.fromtimestamp(ts).strftime('%d/%m/%y %H:%M')}.")
        for issue in game.get("pending_issues") or []:
            lines.append(f"⚠ {issue[0].upper() + issue[1:]}.")
        if game.get("pending_issues"):
            lines.append("Repair writes it back into config.yaml from plugin_library.json; "
                         "Remove clears what is left if you meant to drop it.")
        info = QLabel("\n".join(lines))
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED}; font-size: 9pt;")
        info.setMinimumHeight(info.heightForWidth(420) + 4)
        body.addWidget(info)

        btns = QHBoxLayout()
        remove = QPushButton("Remove from AT0-M")
        remove.setToolTip(
            "Removes the game from plugin_library.json and SLSsteam's config.yaml\n"
            "(AdditionalApps, its keys and DLC entries not shared with other games)\n"
            "and Steam's empty appmanifest. Nothing is downloaded yet, so nothing else changes."
        )
        remove.setStyleSheet("color: #E57373; border: 1px solid rgba(229,115,115,0.45); border-radius: 6px; padding: 5px 12px;")
        steam = QPushButton("Open in Steam" if game.get("pending_state") == "queued" else "Install in Steam")
        steam.setStyleSheet(
            f"background: {accent_color}; color: #000; font-weight: 600; border: none; border-radius: 6px; padding: 5px 14px;"
        )
        close = QPushButton("Close")
        repair = QPushButton("Repair")
        repair.setToolTip("Add the missing AdditionalApps / keys back to config.yaml from plugin_library.json.")
        repair.setVisible(bool(game.get("pending_issues")))
        repair.clicked.connect(self._repair)
        for b in (remove, steam, close, repair):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(30)
        remove.clicked.connect(self._remove)
        steam.clicked.connect(self._open_steam)
        close.clicked.connect(self.reject)
        btns.addWidget(remove)
        btns.addStretch(1)
        btns.addWidget(close)
        btns.addWidget(repair)
        btns.addWidget(steam)
        body.addSpacing(6)
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
