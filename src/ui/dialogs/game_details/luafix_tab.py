"""
luafix_tab.py - GameDetailsDialogV2 tab for browsing and applying lua.tools fixes.
Renders colored tag badges matching lua.tools, safe backups, receipts, and 1-click un-fix.
"""

import logging
import threading
from pathlib import Path
from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QFrame,
    QApplication,
    QSizePolicy,
)
from ui.dialogs.themed_message_box import ThemedMessageBox

from core.lua_tools_api import LuaToolsAPI
from managers.fix_manager import FixManager
from ui.dialogs.game_details.fix_preview_dialog import show_fix_preview_dialog, show_restore_preview_dialog
from utils.qt_utils import clear_layout

logger = logging.getLogger(__name__)


def _hex_to_rgba(hex_code: str, alpha: float = 0.15) -> str:
    h = str(hex_code).lstrip("#")
    if len(h) == 6:
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r}, {g}, {b}, {alpha})"
        except ValueError:
            return hex_code
    return hex_code


class ElidedLabel(QLabel):
    """Single-line QLabel that dynamically elides long text with '...' and sets full text as tooltip."""
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._full_text = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def setText(self, text: str):
        self._full_text = text
        self.setToolTip(text)
        self._update_elided()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self):
        fm = self.fontMetrics()
        width = self.width()
        if width > 0:
            elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideRight, width)
            super().setText(elided)
        else:
            super().setText(self._full_text)


class _LuaFixBridge(QObject):
    loaded = pyqtSignal(object)


def init_luafix_tab(dialog) -> None:
    """Initialize Tab — LuaFix (Community game fixes from lua.tools)."""
    inner = QWidget()
    inner.setStyleSheet("background: transparent;")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(10)

    scroll = QScrollArea()
    scroll.setWidget(inner)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    # Title & Header
    header_box = QHBoxLayout()
    header_lbl = QLabel("lua.tools Game Fixes")
    header_lbl.setStyleSheet("color: #FFFFFF; font-size: 11pt; font-weight: bold;")
    header_box.addWidget(header_lbl)
    header_box.addStretch()

    dialog.luafix_refresh_btn = QPushButton("↻ Refresh")
    dialog.luafix_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.luafix_refresh_btn.setStyleSheet("""
        QPushButton {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 5px;
            color: #d1d5db;
            font-size: 8pt;
            padding: 4px 10px;
        }
        QPushButton:hover { background: rgba(255, 255, 255, 0.16); color: #FFFFFF; }
    """)
    header_box.addWidget(dialog.luafix_refresh_btn)
    lay.addLayout(header_box)

    dialog.luafix_content_box = QVBoxLayout()
    dialog.luafix_content_box.setSpacing(10)
    lay.addLayout(dialog.luafix_content_box)
    lay.addStretch()

    dialog.stacked.addWidget(scroll)

    dialog._luafix_bridge = _LuaFixBridge(dialog)

    def load_fixes(force_refresh: bool = False):
        if not force_refresh and getattr(dialog, "_cached_luafix_res", None):
            render_fixes(dialog._cached_luafix_res)
            return

        # Clear existing widgets
        clear_layout(dialog.luafix_content_box)

        loading_lbl = QLabel("Fetching fixes from lua.tools...")
        loading_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 9pt; padding: 20px;")
        loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dialog.luafix_content_box.addWidget(loading_lbl)

        def worker():
            try:
                res = LuaToolsAPI.get_game_fixes(dialog.appid, force_refresh=force_refresh)
            except Exception as e:
                res = {"success": False, "error": str(e), "fixes": []}
            dialog._cached_luafix_res = res
            dialog._luafix_bridge.loaded.emit(res)

        threading.Thread(target=worker, daemon=True).start()

    def render_fixes(res):
        from PyQt6 import sip
        if sip.isdeleted(dialog):
            return
        clear_layout(dialog.luafix_content_box)

        if not res or not res.get("success"):
            err_lbl = QLabel(f"Failed to load fixes: {res.get('error', 'Network error') if res else 'Unknown error'}")
            err_lbl.setStyleSheet("color: #f87171; font-size: 9pt; padding: 15px;")
            dialog.luafix_content_box.addWidget(err_lbl)
            return

        raw_fixes = res.get("fixes", [])
        ignore_online = dialog.settings.value("luatools_ignore_online_fixes", False, type=bool)
        fixes_list = LuaToolsAPI.filter_fixes(raw_fixes, ignore_online=ignore_online)
        if not fixes_list:
            msg = "No fixes found on lua.tools for this game."
            if raw_fixes and ignore_online:
                msg = "No offline fixes available for this game ('Ignore Online Fixes' is enabled in Settings)."
            no_lbl = QLabel(msg)
            no_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.45); font-size: 9pt; padding: 20px;")
            no_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dialog.luafix_content_box.addWidget(no_lbl)
            return

        game_path = dialog.game_data.get("install_path")
        applied_receipts = {}
        if game_path:
            for r in FixManager.get_all_receipts(game_path):
                fid = str(r.get("fix_id") or "")
                if fid:
                    applied_receipts[fid] = r

        for fix in fixes_list:
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background: rgba(255, 255, 255, 0.04);
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 8px;
                }
            """)
            c_lay = QVBoxLayout(card)
            c_lay.setContentsMargins(14, 12, 14, 12)
            c_lay.setSpacing(8)

            # Top row: Title + Tags
            top_row = QHBoxLayout()
            top_row.setSpacing(8)
            title_text = fix.get("title") or "Fix Release"
            title_lbl = ElidedLabel(title_text)
            title_lbl.setStyleSheet("color: #FFFFFF; font-size: 10pt; font-weight: bold; border: none; background: transparent;")
            top_row.addWidget(title_lbl, 1)

            # Render colored tags matching website
            tags = fix.get("tags", [])
            for tag in tags:
                tag_name = tag.get("name", "")
                tag_color = tag.get("color", "#3b82f6")
                bg_color = _hex_to_rgba(tag_color, 0.15)
                border_color = _hex_to_rgba(tag_color, 0.40)
                t_lbl = QLabel(f" {tag_name} ")

                t_lbl.setStyleSheet(f"""
                    QLabel {{
                        background: {bg_color};
                        color: {tag_color};
                        border: 1px solid {border_color};
                        border-radius: 4px;
                        font-size: 7.5pt;
                        font-weight: 600;
                        padding: 2px 5px;
                    }}
                """)
                top_row.addWidget(t_lbl)

            top_row.addStretch()

            # Date
            created_at = fix.get("createdAt", "")

            if created_at and len(created_at) >= 10:
                date_lbl = QLabel(created_at[:10])
                date_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 8pt; border: none; background: transparent;")
                top_row.addWidget(date_lbl)

            c_lay.addLayout(top_row)

            # Description
            desc = fix.get("description", "")
            if desc:
                desc_lbl = QLabel(desc)
                desc_lbl.setWordWrap(True)
                desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt; border: none; background: transparent;")
                c_lay.addWidget(desc_lbl)

            # Action button row
            action_row = QHBoxLayout()
            action_row.addStretch()

            fix_id = str(fix.get("id"))
            is_this_applied = fix_id in applied_receipts

            if is_this_applied:
                applied_badge = QLabel("✓ Applied")
                applied_badge.setStyleSheet("""
                    QLabel {
                        background: rgba(34, 197, 94, 0.15);
                        color: #4ade80;
                        border: 1px solid rgba(34, 197, 94, 0.4);
                        border-radius: 5px;
                        font-size: 8pt;
                        font-weight: bold;
                        padding: 4px 10px;
                    }
                """)
                action_row.addWidget(applied_badge)

                restore_btn = QPushButton("Restore")
                restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                restore_btn.setStyleSheet("""
                    QPushButton {
                        background: rgba(251, 191, 36, 0.15);
                        color: #fbbf24;
                        border: 1px solid rgba(251, 191, 36, 0.35);
                        border-radius: 5px;
                        padding: 5px 12px;
                        font-size: 8.5pt;
                        font-weight: 500;
                    }
                    QPushButton:hover { background: rgba(251, 191, 36, 0.28); }
                """)
                restore_btn.clicked.connect(
                    lambda _c, gp=game_path, fid=fix_id, ft=title_text: on_restore_fix_clicked(gp, fid, ft)
                )
                action_row.addWidget(restore_btn)
            else:
                apply_btn = QPushButton("Apply Fix")
                apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                apply_btn.setStyleSheet("""
                    QPushButton {
                        background: #3b82f6;
                        color: #FFFFFF;
                        border: 1px solid #2563eb;
                        border-radius: 5px;
                        padding: 6px 14px;
                        font-size: 8.5pt;
                        font-weight: 600;
                    }
                    QPushButton:hover { background: #2563eb; }
                """)
                apply_btn.clicked.connect(
                    lambda _c, f=fix, gp=game_path, b=apply_btn: on_apply_fix_clicked(f, gp, b)
                )
                action_row.addWidget(apply_btn)

            c_lay.addLayout(action_row)
            dialog.luafix_content_box.addWidget(card)

    def on_apply_fix_clicked(fix, game_path, btn=None):
        if not game_path or not Path(game_path).is_dir():
            ThemedMessageBox.critical(dialog, "Error", f"Game directory not found at:\n{game_path}")
            return

        # Check authentication
        auth_status = LuaToolsAPI.get_auth_status()
        if not auth_status.get("authenticated"):
            reply = ThemedMessageBox.question(
                dialog,
                "Authentication Required",
                "Downloading fixes from lua.tools requires connecting your free account.\n\n"
                "Would you like to open Settings to sign in now?",
                ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
                ThemedMessageBox.StandardButton.Yes,
            )
            if reply == ThemedMessageBox.StandardButton.Yes:
                if hasattr(dialog, "main_window") and dialog.main_window:
                    dialog.main_window.open_settings(initial_tab="LuaTools")
                else:
                    from ui.dialogs.settings import SettingsDialog
                    SettingsDialog(dialog, initial_tab="LuaTools").exec()
            return

        fix_id = fix.get("id")
        fix_title = fix.get("title") or "Fix"
        fix_filename = fix.get("fixFilename") or "fix.zip"

        confirm = ThemedMessageBox.question(
            dialog,
            "Apply Fix",
            f"Are you sure you want to apply '{fix_title}' to {dialog.game_data.get('game_name', 'this game')}?\n\n"
            f"Archive: {fix_filename}\n"
            "Existing game files will be backed up automatically for safe removal.",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
            ThemedMessageBox.StandardButton.Yes,
        )
        if confirm != ThemedMessageBox.StandardButton.Yes:
            return

        # Disable button during download to prevent double-invocations
        if btn is not None:
            btn.setEnabled(False)

        # Download the fix with modal progress dialog
        orig_title = dialog.windowTitle()
        from ui.dialogs.themed_progress_dialog import ThemedProgressDialog

        dl_dialog = ThemedProgressDialog(
            labelText=f"Connecting and downloading {fix_title}...",
            cancelButtonText=None,
            minimum=0,
            maximum=100,
            parent=dialog,
        )
        dl_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dl_dialog.setWindowTitle("Downloading Fix")
        dl_dialog.show()
        QApplication.processEvents()

        def dl_progress(downloaded: int, total: int):
            if total > 0:
                pct = max(0, min(100, int(downloaded * 100 / total)))
                dl_dialog.setValue(pct)
                mb_down = downloaded / (1024 * 1024)
                mb_tot = total / (1024 * 1024)
                dl_dialog.setLabelText(f"Downloading {fix_title}...\n{mb_down:.1f} MB of {mb_tot:.1f} MB ({pct}%)")
            else:
                mb_down = downloaded / (1024 * 1024)
                dl_dialog.setLabelText(f"Downloading {fix_title}...\n{mb_down:.1f} MB")
            QApplication.processEvents()

        try:
            zip_path, err = LuaToolsAPI.download_fix_zip(
                fix_id, dialog.appid, progress_cb=dl_progress
            )
        finally:
            dl_dialog.close()
            if btn is not None:
                btn.setEnabled(True)

        if not zip_path:
            ThemedMessageBox.critical(dialog, "Download Failed", f"Could not download fix:\n{err}")
            return

        # Show preview dialog to let user select files
        game_name = dialog.game_data.get("game_name", "Unknown Game")
        selected_files = show_fix_preview_dialog(
            dialog, zip_path, fix_title, game_name, game_path=game_path
        )

        # User cancelled preview — preserve cached zip archive for instant reuse!
        if selected_files is None:
            return

        # If user selected nothing, skip (preserve cached archive)
        if not selected_files:
            ThemedMessageBox.information(
                dialog,
                "No Files Selected",
                "You haven't selected any files to apply.\n\n"
                "No changes will be made to the game."
            )
            return

        # Apply the fix with selected files
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            def progress_cb(msg):
                if hasattr(dialog, 'setStatusMessage'):
                    dialog.setStatusMessage(msg)
                else:
                    dialog.setWindowTitle(f"Applying Fix - {msg}")

            ok, msg = FixManager.apply_fix(
                game_path=game_path,
                appid=dialog.appid,
                fix_id=fix_id,
                fix_title=fix_title,
                zip_path=zip_path,
                selected_files=selected_files,
                progress_cb=progress_cb,
            )

            # Clean up downloaded zip from cache once applied
            try:
                Path(zip_path).unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"[LuaFix] Could not delete temp zip {zip_path}: {e}")

            if ok:
                ThemedMessageBox.information(dialog, "Success", msg)
                load_fixes()
            else:
                ThemedMessageBox.critical(dialog, "Application Failed", msg)
        finally:
            if hasattr(dialog, 'setStatusMessage'):
                dialog.setStatusMessage("")
            else:
                dialog.setWindowTitle(orig_title)
            QApplication.restoreOverrideCursor()

    def on_restore_fix_clicked(game_path, fix_id=None, fix_title="Fix"):
        game_name = dialog.game_data.get("game_name", "Unknown Game")
        preview_data = FixManager.preview_restore(game_path, fix_id=fix_id)
        if not preview_data.get("receipt"):
            ThemedMessageBox.critical(
                dialog,
                "Restore Error",
                preview_data.get("error") or "No active fix receipt found in game directory."
            )
            return

        # Show restore preview dialog detailing actions, backups, and any conflicts
        action = show_restore_preview_dialog(
            dialog,
            game_path=game_path,
            fix_id=str(fix_id),
            fix_title=preview_data.get("fix_title") or fix_title,
            game_name=game_name,
            preview_data=preview_data,
        )
        if not action:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if action == "discard":
                ok, msg = FixManager.remove_fix(game_path, fix_id=fix_id, discard_only=True)
            elif action == "force":
                ok, msg = FixManager.remove_fix(game_path, fix_id=fix_id, force=True)
            else:
                ok, msg = FixManager.remove_fix(game_path, fix_id=fix_id)

            if ok:
                success_title = "Fix Record Discarded" if action == "discard" else "Fix Restored"
                ThemedMessageBox.information(dialog, success_title, msg)
                load_fixes(force_refresh=True)
            else:
                ThemedMessageBox.critical(dialog, "Restore Failed", msg)
        finally:
            QApplication.restoreOverrideCursor()

    dialog.luafix_refresh_btn.clicked.connect(lambda: load_fixes(force_refresh=True))
    dialog._luafix_bridge.loaded.connect(render_fixes)

    # Render preloaded fixes if available
    if getattr(dialog, "_cached_luafix_res", None):
        render_fixes(dialog._cached_luafix_res)

