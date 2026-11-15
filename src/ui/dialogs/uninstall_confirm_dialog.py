"""
uninstall_confirm_dialog.py - Dedicated confirmation modal for game uninstallation.

Features:
- Clear, color-coded summary of what will be removed vs what will be preserved.
- Asynchronous calculation of Proton prefix size with live progress updates (e.g. [??] -> [12 MB] -> [1.4 GB]).
- Checkbox allowing users to optionally wipe the Proton prefix (with local Windows saves).
- Respects DLC-only mode (preserves base game files and Proton prefix).
"""

import logging
import os
import platform
import time
from pathlib import Path
from typing import Any, Dict, Optional

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
                    except OSError:
                        pass
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
        except Exception:
            pass

    try:
        from core.steam_helpers import get_steam_libraries

        for lib in get_steam_libraries() or []:
            candidates.append(os.path.join(lib, "steamapps", "compatdata", str(appid)))
    except Exception:
        pass

    for cp in candidates:
        if os.path.exists(cp):
            return cp
    return None


class UninstallConfirmDialog(QDialog):
    """
    Dedicated confirmation modal for standard uninstallation.
    Displays what will be removed vs preserved, and lets the user optionally
    opt into wiping their Proton prefix.
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
                except Exception:
                    pass

        self.compatdata_path = None
        if platform.system() == "Linux" and not self.is_dlc_only:
            self.compatdata_path = find_compatdata_path(self.appid, self.install_path, self.library_path)

        self._scanner: Optional[PrefixScannerThread] = None
        self._scan_started = False
        self._prefix_size_str = "??"

        settings = get_settings()
        self.accent_color = (
            getattr(parent, "accent_color", None)
            or (settings.value("accent_color", "#a1c9fd") if settings else "#a1c9fd")
        )

        self.setMinimumWidth(460)
        self.setMaximumWidth(540)
        self._init_ui()

    def _init_ui(self) -> None:
        title = f"Uninstall - {self.game_name}"
        layout = install_themed_frame(self, title, use_power_close=True)
        layout.setSpacing(10)

        # Header question
        header_text = (
            f"Are you sure you want to uninstall DLC for <b>{self.game_name}</b>?"
            if self.is_dlc_only
            else f"Are you sure you want to uninstall <b>{self.game_name}</b>?"
        )
        self.header_lbl = QLabel(header_text)
        self.header_lbl.setWordWrap(True)
        self.header_lbl.setStyleSheet("font-size: 10pt; color: #FFFFFF;")
        layout.addWidget(self.header_lbl)

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

        # Checkbox for Proton prefix wiping
        self.wipe_prefix_cb = QCheckBox("Wipe Proton prefix (with saves) [??]")
        self.wipe_prefix_cb.setStyleSheet("""
            QCheckBox {
                color: #e5e7eb;
                font-size: 9pt;
                spacing: 8px;
            }
            QCheckBox:disabled {
                color: rgba(255, 255, 255, 0.35);
            }
        """)

        if platform.system() == "Linux" and not self.is_dlc_only:
            if self.compatdata_path:
                self.wipe_prefix_cb.setChecked(False)
                self.wipe_prefix_cb.toggled.connect(self._on_wipe_prefix_toggled)
                layout.addWidget(self.wipe_prefix_cb)
            else:
                self.wipe_prefix_cb.setText("Proton prefix: none found on disk")
                self.wipe_prefix_cb.setEnabled(False)
                self.wipe_prefix_cb.setChecked(False)
                layout.addWidget(self.wipe_prefix_cb)
        else:
            self.wipe_prefix_cb.hide()

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

        self.confirm_btn = QPushButton("Confirm Uninstall")
        self.confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_btn.setFixedHeight(28)
        self.confirm_btn.setMinimumWidth(130)
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
        self.confirm_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.confirm_btn)

        layout.addLayout(btn_layout)
        self._update_summary()

    def _update_summary(self) -> None:
        wipe_prefix = self.wipe_prefix_cb.isChecked() and self.wipe_prefix_cb.isEnabled()

        if self.is_dlc_only:
            html = """
            <div style="color: #ff8a7a; font-weight: bold; margin-bottom: 4px;">[Will Be Removed]</div>
            <div style="color: #d1d5db; line-height: 140%; margin-left: 6px;">
                • Selected DLC depot files<br>
                • DLC SLSsteam configuration entries
            </div>
            <div style="color: #4ade80; font-weight: bold; margin-top: 8px; margin-bottom: 4px;">[Will Be Preserved]</div>
            <div style="color: #d1d5db; line-height: 140%; margin-left: 6px;">
                • Base game directory (100% intact)<br>
                • Proton prefix & Windows saves (100% intact)<br>
                • Steam Cloud userdata saves
            </div>
            """
        else:
            removed_items = [
                f"• Game folder: {self.install_path or 'Known install directory'}",
                f"• Steam app manifest: appmanifest_{self.appid}.acf" if self.appid and self.appid != "0" else "• Steam app manifest (.acf)",
                "• SLSsteam & AT0-M configuration entries",
            ]
            if platform.system() == "Linux":
                removed_items.append("• Application shortcuts & icons")

            preserved_items = []
            if wipe_prefix:
                size_suffix = f" ({self._prefix_size_str})" if self._prefix_size_str and self._prefix_size_str != "??" else ""
                removed_items.append(f"• <b>Proton prefix (with saves):</b> {self.compatdata_path or 'compatdata'}{size_suffix}")
            else:
                if self.compatdata_path:
                    size_suffix = f" ({self._prefix_size_str})" if self._prefix_size_str and self._prefix_size_str != "??" else ""
                    preserved_items.append(f"• Proton prefix (Windows saves): {self.compatdata_path}{size_suffix}")
                else:
                    preserved_items.append("• Proton prefix: none found on disk")

            preserved_items.append("• Steam Cloud userdata saves")

            removed_html = "<br>".join(removed_items)
            preserved_html = "<br>".join(preserved_items)

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

    def _on_wipe_prefix_toggled(self, _checked: bool) -> None:
        self._update_summary()

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
        self.wipe_prefix_cb.setText(f"Wipe Proton prefix (with saves) [{formatted}]")
        self._update_summary()

    def _on_scan_finished(self, total_bytes: int) -> None:
        formatted = format_size(total_bytes)
        self._prefix_size_str = formatted
        self.wipe_prefix_cb.setText(f"Wipe Proton prefix (with saves) [{formatted}]")
        self._update_summary()

    def _cleanup_scanner(self) -> None:
        if self._scanner:
            try:
                self._scanner.stop()
                self._scanner.wait(150)
            except Exception:
                pass
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

    def should_wipe_prefix(self) -> bool:
        """Returns True if the user explicitly opted to wipe the Proton prefix."""
        return bool(self.wipe_prefix_cb.isChecked() and self.wipe_prefix_cb.isEnabled())
