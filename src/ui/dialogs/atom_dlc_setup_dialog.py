"""
AT0-M DLC setup for a Steam-owned game.

Hosts the same AdditionalApps / AdditionalDepots / DecryptionKeys panel as the
Game Details > Depots tab (``build_depots_panel(mode="dlc_setup")``), so DLC
setup and later edits look and behave identically:

- Steam-owned DLCs, their depots and keys, and the owned base game are greyed
  out and left untouched.
- Unreleased DLCs and everything mapped to them are shown but locked.
- Released DLCs Hubcap does not have are tagged (Key Ready / Awaiting Key / No Key).
- Nothing is downloaded: registering only writes the SLSsteam config and the
  plugin library record, which is all AT0-M needs for DLC content.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFrame, QLabel, QVBoxLayout, QWidget

from ui.dialog_titlebar import DialogTitleBar
from ui.dialogs.themed_message_box import ThemedMessageBox
from utils.settings import get_settings

logger = logging.getLogger(__name__)


def build_dlc_setup_game_data(
    app_id: str,
    game_name: str,
    discovery: Any = None,
    manifest_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Shape discovery + freshly fetched manifest data like Game Details ``game_data``."""
    app_id = str(app_id).strip()
    manifest_data = manifest_data or {}
    depots: Dict[str, Dict[str, Any]] = {}

    for cand in (getattr(discovery, "depots", None) or []):
        did = str(getattr(cand, "depot_id", "") or "").strip()
        if not did or did == app_id:
            continue
        depots[did] = {
            "desc": getattr(cand, "name", "") or f"Depot {did}",
            "dlcappid": str(getattr(cand, "dlc_appid", "") or "").strip(),
            "size": getattr(cand, "size", 0) or 0,
        }

    manifest_keys = {str(k): str(v) for k, v in (manifest_data.get("keys") or {}).items()}
    for did, info in (manifest_data.get("depots") or {}).items():
        did = str(did).strip()
        if not did:
            continue
        info = info if isinstance(info, dict) else {"desc": str(info)}
        entry = depots.setdefault(did, {})
        entry["desc"] = info.get("desc") or info.get("name") or entry.get("desc") or f"Depot {did}"
        if info.get("dlcappid"):
            entry["dlcappid"] = str(info["dlcappid"])
        key = info.get("key") or manifest_keys.get(did)
        if key:
            entry["key"] = str(key)

    dlcs: Dict[str, str] = {}
    dlcs.update({str(k): str(v) for k, v in (getattr(discovery, "catalog", None) or {}).items()})
    dlcs.update({str(k): str(v) for k, v in (manifest_data.get("dlcs") or {}).items()})
    dlcs.pop(app_id, None)

    keys = dict(manifest_keys)
    if manifest_data.get("app_key"):
        keys[app_id] = str(manifest_data["app_key"])

    return {
        "appid": app_id,
        "game_name": game_name or f"App {app_id}",
        "depots": depots,
        "dlcs": dlcs,
        "keys": keys,
        "missing_depots_from_hubcap": list(manifest_data.get("missing_depots_from_hubcap") or []),
        "is_dlc_only": True,
        "_dlc_only": True,
    }


class AtomDlcSetupDialog(QDialog):
    """Frameless window around the shared depots panel in ``dlc_setup`` mode."""

    def __init__(
        self,
        parent: Optional[QWidget],
        app_id: str,
        game_name: str,
        accent_color: str,
        discovery: Any = None,
        manifest_data: Optional[Dict[str, Any]] = None,
        cached_selected_depots: Optional[List[str]] = None,
    ):
        super().__init__(parent)
        self.appid = str(app_id).strip()
        self.app_id = self.appid
        self.game_name = game_name or f"App {self.appid}"
        self.accent_color = accent_color or "#6c5ce7"
        self.settings = get_settings()
        self.game_data = build_dlc_setup_game_data(self.appid, self.game_name, discovery, manifest_data)
        self._registered_success = False
        self._atom_registered = False
        self.summary: Dict[str, Any] = {}

        self.setWindowTitle(f"AT0-M DLC Setup — {self.game_name}")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(600, 480)
        self.resize(720, 620)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        card = QFrame()
        card.setObjectName("dialogCard")
        card.setStyleSheet("""
            QFrame#dialogCard {
                background-color: #16181f;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }
            QToolTip {
                background-color: #1e2029;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 4px;
                padding: 5px 8px;
                font-size: 8.5pt;
            }
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(DialogTitleBar(self, f"AT0-M DLC Setup — {self.game_name}", can_minimize=False, can_maximize=False))

        sub = QLabel(
            f"Choose which DLCs to unlock for <b>{self.game_name}</b> ({self.appid}) in AT0-M. "
            "Only the SLSsteam config is written; nothing is downloaded by ASSella."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color: rgba(255, 255, 255, 0.8); font-size: 8.5pt; padding: 10px 16px 0 16px;")
        lay.addWidget(sub)

        from ui.dialogs.game_details.depots_tab import build_depots_panel

        self.panel = build_depots_panel(self, mode="dlc_setup", on_registered=self._on_registered)
        lay.addWidget(self.panel, 1)
        root.addWidget(card)

    def _on_registered(self, summary: Dict[str, Any]) -> None:
        self.summary = dict(summary or {})
        self._registered_success = True
        self._atom_registered = True
        how = "DlcData" if self.summary.get("dlc_data") else "AdditionalApps"
        ThemedMessageBox.information(
            self,
            "DLC Registration Complete",
            f"Registered <b>{self.summary.get('dlcs', 0)}</b> DLC(s) via {how} and "
            f"<b>{self.summary.get('depots', 0)}</b> depot(s) for '{self.game_name}'.<br/><br/>"
            "Restart the Steam client so it picks up the new DLCs.",
        )
        self.accept()

    def registered(self) -> bool:
        return self._registered_success
