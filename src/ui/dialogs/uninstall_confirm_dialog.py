"""
uninstall_confirm_dialog.py - Dedicated confirmation modal for game uninstallation.

Features:
- Unified confirmation modal combining standard and advanced uninstall options.
- Clear, color-coded live summary of what will be removed vs what will be preserved.
- Dynamically moves items between [Will Be Removed] and [Will Be Preserved] as checkboxes are toggled.
- Dedicated 'I bought the game' preset mode that automatically preserves files and saves while cleaning SLS entries.
- Asynchronous calculation of Proton prefix size with live progress updates (e.g. [??] -> [12 MB] -> [1.4 GB]).
- Respects DLC-only mode (allows individual DLC selection and preserves base game files/prefix).
"""

import logging
import os
import platform
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.library.widgets import format_size
from ui.dialogs.themed_dialog import install_themed_frame
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class PrefixScannerThread(QThread):
    """Background worker to lazily calculate directory size without freezing the GUI."""

    progress = pyqtSignal(int)
    finished = pyqtSignal(int)

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def run(self) -> None:
        total = 0
        last_emit = time.time()
        try:
            for root, _dirs, files in os.walk(self.path):
                if self._stopped:
                    return
                for f in files:
                    if self._stopped:
                        return
                    fp = os.path.join(root, f)
                    try:
                        total += os.path.getsize(fp)
                    except OSError as e:
                        logger.debug(f"[PrefixScanner] Could not get size of {fp}: {e}")
                now = time.time()
                if now - last_emit > 0.08:
                    self.progress.emit(total)
                    last_emit = now
        except Exception as e:
            logger.debug(f"[PrefixScanner] Error scanning {self.path}: {e}")
        if not self._stopped:
            self.finished.emit(total)


def find_compatdata_path(
    appid: str,
    install_path: Optional[str] = None,
    library_path: Optional[str] = None,
) -> Optional[str]:
    """Discover the Proton prefix (compatdata) path on disk for a given AppID."""
    if not appid or appid in ("0", "N/A", "unknown"):
        return None

    candidates = []
    if library_path:
        candidates.append(os.path.join(library_path, "steamapps", "compatdata", str(appid)))

    if install_path:
        try:
            p = Path(install_path).resolve()
            for parent in p.parents:
                if parent.name.lower() == "steamapps":
                    candidates.append(str(parent / "compatdata" / str(appid)))
                    break
        except Exception as e:
            logger.debug(f"[UninstallConfirm] Path traversal error checking compatdata: {e}")

    try:
        from core.steam_helpers import get_steam_libraries

        for lib in get_steam_libraries() or []:
            candidates.append(os.path.join(lib, "steamapps", "compatdata", str(appid)))
    except Exception as e:
        logger.debug(f"[UninstallConfirm] Error reading steam libraries for compatdata: {e}")

    for cp in candidates:
        if os.path.exists(cp):
            return cp
    return None


class UninstallConfirmDialog(QDialog):
    """
    Unified confirmation modal for uninstallation.
    Displays customizable removal options with a live preview of what will be
    removed vs preserved, and lets the user choose their exact desired cleanup scope.
    """

    def __init__(self, parent: Optional[QWidget] = None, game_data: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.game_data = game_data or {}
        self.game_name = self.game_data.get("game_name", "this game")
        self.appid = str(self.game_data.get("appid", "0"))
        self.install_path = self.game_data.get("install_path") or ""
        self.library_path = self.game_data.get("library_path") or ""

        # Fallback path resolution for AT0-M titles
        if not self.install_path or not os.path.exists(self.install_path):
            try:
                from utils.plugin_games import get_atom_game_install_info

                atom_info = get_atom_game_install_info(self.appid)
                if atom_info:
                    self.install_path = atom_info.get("common_path") or atom_info.get("install_path") or ""
                    if not self.library_path:
                        self.library_path = atom_info.get("library_path") or ""
            except Exception as e:
                logger.debug(f"[UninstallConfirm] Could not resolve AT0-M install info: {e}")

        # DLC-Only detection
        self.is_dlc_only = False
        if self.appid and self.appid not in ("0", "N/A", "unknown"):
            from utils.dlc_helpers import is_dlc_only_mode

            self.is_dlc_only = (
                is_dlc_only_mode(self.appid)
                or bool(self.game_data.get("dlc_only"))
                or bool(self.game_data.get("is_dlc_only"))
            )
            if not self.is_dlc_only:
                try:
                    from utils.plugin_games import get_plugin_game

                    prec = get_plugin_game(self.appid, active_only=False)
                    if prec and prec.get("dlc_only"):
                        self.is_dlc_only = True
                except Exception as e:
                    logger.debug(f"[UninstallConfirm] Could not check DLC-only status: {e}")

        self.compatdata_path = None
        if platform.system() == "Linux" and not self.is_dlc_only:
            self.compatdata_path = find_compatdata_path(self.appid, self.install_path, self.library_path)

        self._scanner: Optional[PrefixScannerThread] = None
        self._scan_started = False
        self._prefix_size_str = "??"
        self._dlc_checkboxes: Dict[str, QCheckBox] = {}

        settings = get_settings()
        self.accent_color = (
            getattr(parent, "accent_color", None)
            or (settings.value("accent_color", "#a1c9fd") if settings else "#a1c9fd")
        )

        self.setMinimumWidth(480)
        self.setMaximumWidth(560)
        self._init_ui()

    def _init_ui(self) -> None:
        title = f"Uninstall - {self.game_name}"
        layout = install_themed_frame(self, title, use_power_close=True)
        layout.setSpacing(10)

        # Header question
        header_text = (
            f"Select the uninstallation options for DLCs of <b>{self.game_name}</b>:"
            if self.is_dlc_only
            else f"Select the uninstallation options for <b>{self.game_name}</b>:"
        )
        self.header_lbl = QLabel(header_text)
        self.header_lbl.setWordWrap(True)
        self.header_lbl.setStyleSheet("font-size: 10pt; color: #FFFFFF;")
        layout.addWidget(self.header_lbl)

        # Options Container Frame
        self.options_frame = QFrame()
        self.options_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 4px;
            }
        """)
        options_layout = QVBoxLayout(self.options_frame)
        options_layout.setContentsMargins(10, 8, 10, 8)
        options_layout.setSpacing(8)

        cb_style = """
            QCheckBox {
                color: #e5e7eb;
                font-size: 9pt;
                spacing: 8px;
            }
            QCheckBox:disabled {
                color: rgba(255, 255, 255, 0.35);
            }
        """

        if not self.is_dlc_only:
            # 1. Preset Checkbox: 'I bought the game'
            self.bought_preset_cb = QCheckBox(
                "I bought the game (Only remove SLSsteam entries; keep game files, saves, and prefix 100% intact)"
            )
            self.bought_preset_cb.setToolTip(
                "Cleans all SLSsteam integrations while leaving your game folder, saves, achievements, and prefix untouched."
            )
            self.bought_preset_cb.setStyleSheet("""
                QCheckBox {
                    color: #4ade80;
                    font-size: 9.5pt;
                    font-weight: bold;
                    spacing: 8px;
                }
            """)
            self.bought_preset_cb.toggled.connect(self._on_bought_preset_toggled)
            options_layout.addWidget(self.bought_preset_cb)

            # Divider line inside options
            divider = QFrame()
            divider.setFrameShape(QFrame.Shape.HLine)
            divider.setFrameShadow(QFrame.Shadow.Sunken)
            divider.setStyleSheet("background-color: rgba(255, 255, 255, 0.06);")
            options_layout.addWidget(divider)

            # 2. Delete game installation files
            self.delete_files_cb = QCheckBox("Delete game installation files")
            self.delete_files_cb.setToolTip("Deletes the game files folder from your Steam library storage.")
            self.delete_files_cb.setStyleSheet(cb_style)
            self.delete_files_cb.setChecked(True)
            self.delete_files_cb.toggled.connect(self._update_summary)
            options_layout.addWidget(self.delete_files_cb)

            # 3. Remove SLSsteam & AT0-M configuration entries (always checked)
            self.remove_sls_cb = QCheckBox("Remove SLSsteam & AT0-M configuration entries")
            self.remove_sls_cb.setToolTip("Cleans AppID, DLC, AppTokens, and manifest tracking from SLSsteam config.yaml.")
            self.remove_sls_cb.setStyleSheet(cb_style)
            self.remove_sls_cb.setChecked(True)
            self.remove_sls_cb.setEnabled(False)
            options_layout.addWidget(self.remove_sls_cb)

            # 4. Remove desktop shortcuts & icons (Linux only)
            if platform.system() == "Linux":
                self.remove_shortcuts_cb = QCheckBox("Remove desktop application shortcuts & icons")
                self.remove_shortcuts_cb.setStyleSheet(cb_style)
                self.remove_shortcuts_cb.setChecked(True)
                self.remove_shortcuts_cb.toggled.connect(self._update_summary)
                options_layout.addWidget(self.remove_shortcuts_cb)
            else:
                self.remove_shortcuts_cb = QCheckBox("Remove desktop application shortcuts & icons")
                self.remove_shortcuts_cb.hide()

            # 5. Wipe Proton prefix
            self.wipe_prefix_cb = QCheckBox("Wipe Proton prefix (with saves) [??]")
            self.wipe_prefix_cb.setToolTip(
                "Permanently deletes the Proton prefix directory (compatdata), including any Windows saves in AppData/Documents."
            )
            self.wipe_prefix_cb.setStyleSheet(cb_style)
            if platform.system() == "Linux":
                if self.compatdata_path:
                    self.wipe_prefix_cb.setChecked(False)
                    self.wipe_prefix_cb.toggled.connect(self._update_summary)
                    options_layout.addWidget(self.wipe_prefix_cb)
                else:
                    self.wipe_prefix_cb.setText("Proton prefix: none found on disk")
                    self.wipe_prefix_cb.setEnabled(False)
                    self.wipe_prefix_cb.setChecked(False)
                    options_layout.addWidget(self.wipe_prefix_cb)
            else:
                self.wipe_prefix_cb.hide()

            # 6. Remove local Steam Cloud saves
            self.remove_saves_cb = QCheckBox("Remove local Steam Cloud userdata saves")
            self.remove_saves_cb.setToolTip("Permanently deletes local copies of Steam Cloud saves stored in userdata directory.")
            self.remove_saves_cb.setStyleSheet(cb_style)
            self.remove_saves_cb.setChecked(False)
            self.remove_saves_cb.toggled.connect(self._update_summary)
            options_layout.addWidget(self.remove_saves_cb)

        else:
            # DLC-Only mode: List individual DLCs with checkboxes
            from utils.dlc_helpers import get_dlc_only_info

            dlc_list = get_dlc_only_info(self.appid) or []
            self.bought_preset_cb = QCheckBox("I bought the game")
            self.bought_preset_cb.hide()
            self.delete_files_cb = QCheckBox("Delete game installation files")
            self.delete_files_cb.hide()
            self.remove_shortcuts_cb = QCheckBox("Remove desktop application shortcuts & icons")
            self.remove_shortcuts_cb.hide()
            self.wipe_prefix_cb = QCheckBox("Wipe Proton prefix (with saves)")
            self.wipe_prefix_cb.hide()
            self.remove_saves_cb = QCheckBox("Remove local Steam Cloud userdata saves")
            self.remove_saves_cb.hide()

            dlc_header = QLabel("Select DLCs to remove:")
            dlc_header.setStyleSheet("color: #ff8a7a; font-weight: bold; font-size: 9.5pt;")
            options_layout.addWidget(dlc_header)

            for dlc in dlc_list:
                did = str(dlc.get("dlc_appid", "")).strip()
                if not did:
                    continue
                dname = dlc.get("dlc_name") or f"DLC {did}"
                cb = QCheckBox(f"{dname} ({did})")
                cb.setStyleSheet(cb_style)
                cb.setChecked(True)
                cb.toggled.connect(self._update_summary)
                self._dlc_checkboxes[did] = cb
                options_layout.addWidget(cb)

            if not dlc_list:
                options_layout.addWidget(QLabel("No DLC depot configuration records found."))

        layout.addWidget(self.options_frame)

        # Content card summarizing removed & preserved items
        self.summary_card = QFrame()
        self.summary_card.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.09);
                border-radius: 8px;
                padding: 10px;
            }
        """)
        card_layout = QVBoxLayout(self.summary_card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(6)

        self.summary_lbl = QLabel()
        self.summary_lbl.setWordWrap(True)
        self.summary_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.summary_lbl.setStyleSheet("font-size: 9pt; background: transparent; border: none;")
        card_layout.addWidget(self.summary_lbl)
        layout.addWidget(self.summary_card)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setFixedSize(100, 28)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                color: #e5e7eb;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                color: #ffffff;
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.confirm_btn = QPushButton("Uninstall")
        self.confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_btn.setFixedHeight(28)
        self.confirm_btn.setMinimumWidth(130)
        self.confirm_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.confirm_btn)

        layout.addLayout(btn_layout)
        self._update_summary()

    def _on_bought_preset_toggled(self, checked: bool) -> None:
        """When 'I bought the game' is checked, disable other options and update UI."""
        if checked:
            if self.delete_files_cb:
                self.delete_files_cb.setEnabled(False)
            if self.remove_shortcuts_cb:
                self.remove_shortcuts_cb.setEnabled(False)
            if self.wipe_prefix_cb and self.compatdata_path:
                self.wipe_prefix_cb.setEnabled(False)
                self.wipe_prefix_cb.setChecked(False)
            if self.remove_saves_cb:
                self.remove_saves_cb.setEnabled(False)
                self.remove_saves_cb.setChecked(False)
        else:
            if self.delete_files_cb:
                self.delete_files_cb.setEnabled(True)
            if self.remove_shortcuts_cb:
                self.remove_shortcuts_cb.setEnabled(True)
            if self.wipe_prefix_cb and self.compatdata_path:
                self.wipe_prefix_cb.setEnabled(True)
            if self.remove_saves_cb:
                self.remove_saves_cb.setEnabled(True)

        self._update_summary()

    def _update_summary(self) -> None:
        """Dynamically moves items between [Will Be Removed] and [Will Be Preserved]."""
        if self.is_dlc_only:
            checked_dlcs = [
                did for did, cb in self._dlc_checkboxes.items() if cb.isChecked()
            ]
            unchecked_dlcs = [
                did for did, cb in self._dlc_checkboxes.items() if not cb.isChecked()
            ]

            removed_items = []
            if checked_dlcs:
                removed_items.append(f"• Selected DLC files ({len(checked_dlcs)} DLC(s))")
                removed_items.append("• Mapped DLC SLSsteam configuration entries")
            else:
                removed_items.append("• (No DLCs selected for removal)")

            preserved_items = [
                "• Base game directory (100% intact)",
                "• Proton prefix & Windows saves (100% intact)",
                "• Steam Cloud userdata saves",
            ]
            if unchecked_dlcs:
                preserved_items.append(f"• Preserved DLC files ({len(unchecked_dlcs)} DLC(s))")

            self.confirm_btn.setText("Uninstall Selected DLC")
            self.confirm_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(229, 115, 115, 0.22);
                    color: #ff8a7a;
                    border: 1px solid rgba(229, 115, 115, 0.4);
                    border-radius: 5px;
                    font-size: 8.5pt;
                    font-weight: bold;
                    padding: 0 14px;
                }
                QPushButton:hover {
                    background-color: rgba(229, 115, 115, 0.35);
                    color: #ffffff;
                }
            """)
            self.confirm_btn.setEnabled(bool(checked_dlcs))

        elif self.bought_preset_cb and self.bought_preset_cb.isChecked():
            # 'I bought the game' Preset active
            removed_items = [
                "• SLSsteam & AT0-M configuration entries",
                "• Unregistered from ASSella library manager",
            ]
            preserved_items = [
                f"• Game folder: {self.install_path or 'Game directory'} (100% intact)",
                "• Proton prefix & local Windows saves (100% intact)",
                "• Steam Cloud userdata saves (100% intact)",
            ]
            if platform.system() == "Linux":
                preserved_items.append("• Application shortcuts & icons (intact)")

            self.confirm_btn.setText("Clean Integrations")
            self.confirm_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(34, 197, 94, 0.22);
                    color: #4ade80;
                    border: 1px solid rgba(34, 197, 94, 0.45);
                    border-radius: 5px;
                    font-size: 8.5pt;
                    font-weight: bold;
                    padding: 0 14px;
                }
                QPushButton:hover {
                    background-color: rgba(34, 197, 94, 0.35);
                    color: #ffffff;
                }
            """)
            self.confirm_btn.setEnabled(True)

        else:
            # Standard configurable uninstall
            delete_files = bool(self.delete_files_cb and self.delete_files_cb.isChecked())
            remove_shortcuts = bool(self.remove_shortcuts_cb and self.remove_shortcuts_cb.isChecked())
            wipe_prefix = bool(self.wipe_prefix_cb and self.wipe_prefix_cb.isChecked() and self.wipe_prefix_cb.isEnabled())
            remove_saves = bool(self.remove_saves_cb and self.remove_saves_cb.isChecked())

            removed_items = []
            preserved_items = []

            # 1. Game files
            if delete_files:
                removed_items.append(f"• Game folder: {self.install_path or 'Known install directory'}")
                manifest_name = f"appmanifest_{self.appid}.acf" if self.appid and self.appid != "0" else "appmanifest (.acf)"
                removed_items.append(f"• Steam app manifest: {manifest_name}")
            else:
                preserved_items.append(f"• Game folder: {self.install_path or 'Game directory'} (preserved on disk)")

            # 2. SLS & AT0-M configuration (always removed in standard mode)
            removed_items.append("• SLSsteam & AT0-M configuration entries")

            # 3. Desktop shortcuts
            if platform.system() == "Linux":
                if remove_shortcuts:
                    removed_items.append("• Application shortcuts & icons")
                else:
                    preserved_items.append("• Application shortcuts & icons (preserved)")

            # 4. Proton prefix
            size_suffix = f" ({self._prefix_size_str})" if self._prefix_size_str and self._prefix_size_str != "??" else ""
            if wipe_prefix:
                removed_items.append(f"• <b>Proton prefix (with saves):</b> {self.compatdata_path or 'compatdata'}{size_suffix}")
            else:
                if self.compatdata_path:
                    preserved_items.append(f"• Proton prefix (Windows saves): {self.compatdata_path}{size_suffix}")
                else:
                    preserved_items.append("• Proton prefix: none found on disk")

            # 5. Steam Cloud saves
            if remove_saves:
                removed_items.append("• Local Steam Cloud userdata saves")
            else:
                preserved_items.append("• Steam Cloud userdata saves (preserved)")

            self.confirm_btn.setText("Uninstall")
            self.confirm_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(229, 115, 115, 0.22);
                    color: #ff8a7a;
                    border: 1px solid rgba(229, 115, 115, 0.4);
                    border-radius: 5px;
                    font-size: 8.5pt;
                    font-weight: bold;
                    padding: 0 14px;
                }
                QPushButton:hover {
                    background-color: rgba(229, 115, 115, 0.35);
                    color: #ffffff;
                }
            """)
            self.confirm_btn.setEnabled(True)

        removed_html = "<br>".join(removed_items) if removed_items else "• None"
        preserved_html = "<br>".join(preserved_items) if preserved_items else "• None"

        html = f"""
        <div style="color: #ff8a7a; font-weight: bold; margin-bottom: 4px;">[Will Be Removed]</div>
        <div style="color: #d1d5db; line-height: 140%; margin-left: 6px;">
            {removed_html}
        </div>
        <div style="color: #4ade80; font-weight: bold; margin-top: 8px; margin-bottom: 4px;">[Will Be Preserved]</div>
        <div style="color: #d1d5db; line-height: 140%; margin-left: 6px;">
            {preserved_html}
        </div>
        """
        self.summary_lbl.setText(html)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if (
            not self._scan_started
            and self.compatdata_path
            and os.path.exists(self.compatdata_path)
            and not self.is_dlc_only
        ):
            self._scan_started = True
            self._scanner = PrefixScannerThread(self.compatdata_path)
            self._scanner.progress.connect(self._on_scan_progress)
            self._scanner.finished.connect(self._on_scan_finished)
            self._scanner.start()

    def _on_scan_progress(self, bytes_scanned: int) -> None:
        formatted = format_size(bytes_scanned)
        self._prefix_size_str = formatted
        if self.wipe_prefix_cb:
            self.wipe_prefix_cb.setText(f"Wipe Proton prefix (with saves) [{formatted}]")
        self._update_summary()

    def _on_scan_finished(self, total_bytes: int) -> None:
        formatted = format_size(total_bytes)
        self._prefix_size_str = formatted
        if self.wipe_prefix_cb:
            self.wipe_prefix_cb.setText(f"Wipe Proton prefix (with saves) [{formatted}]")
        self._update_summary()

    def _cleanup_scanner(self) -> None:
        if self._scanner:
            try:
                self._scanner.stop()
                self._scanner.wait(150)
            except Exception as e:
                logger.debug(f"[UninstallConfirm] Error stopping prefix scanner: {e}")
            self._scanner = None

    def closeEvent(self, event) -> None:
        self._cleanup_scanner()
        super().closeEvent(event)

    def reject(self) -> None:
        self._cleanup_scanner()
        super().reject()

    def accept(self) -> None:
        self._cleanup_scanner()
        super().accept()

    def should_wipe_sls_only(self) -> bool:
        """Returns True if the user selected 'I bought the game' preset."""
        return bool(self.bought_preset_cb and self.bought_preset_cb.isChecked())

    def should_delete_game_files(self) -> bool:
        """Returns True if game files should be deleted from disk."""
        if self.should_wipe_sls_only():
            return False
        if self.delete_files_cb:
            return bool(self.delete_files_cb.isChecked())
        return True

    def should_remove_sls(self) -> bool:
        """Always removes SLSsteam entries on uninstall."""
        return True

    def should_wipe_prefix(self) -> bool:
        """Returns True if user explicitly opted to wipe the Proton prefix."""
        if self.should_wipe_sls_only():
            return False
        return bool(self.wipe_prefix_cb and self.wipe_prefix_cb.isChecked() and self.wipe_prefix_cb.isEnabled())

    def should_remove_saves(self) -> bool:
        """Returns True if user opted to delete local Steam Cloud userdata saves."""
        if self.should_wipe_sls_only():
            return False
        return bool(self.remove_saves_cb and self.remove_saves_cb.isChecked())

    def should_remove_shortcuts(self) -> bool:
        """Returns True if user opted to remove desktop shortcuts."""
        if self.should_wipe_sls_only():
            return False
        if self.remove_shortcuts_cb:
            return bool(self.remove_shortcuts_cb.isChecked())
        return True

    def get_target_dlc_ids(self) -> Optional[List[str]]:
        """Returns list of selected DLC IDs to uninstall for DLC-only games."""
        if not self.is_dlc_only:
            return None
        return [did for did, cb in self._dlc_checkboxes.items() if cb.isChecked()]
