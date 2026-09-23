"""
Workshop tab implementation for GameDetailsDialogV2.
Handles scanning, listing, updating, and removing local Steam Workshop mods.
"""

import html
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import List

from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, QUrl, QSize
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QScrollArea,
    QMessageBox,
    QApplication,
)

from core.steam_helpers import get_steam_libraries, find_steam_install
from utils.workshop_helpers import fetch_workshop_details, strip_emojis, delete_workshop_item
from utils.color_utils import get_best_foreground_color, make_svg_icon
from utils.paths import Paths
from utils.settings import get_settings

logger = logging.getLogger(__name__)


def init_workshop_tab(dialog) -> None:
    """Initialize the Workshop tab with layout and scan trigger."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    ws_widget = QWidget()
    dialog.ws_layout = QVBoxLayout(ws_widget)
    dialog.ws_layout.setContentsMargins(16, 14, 16, 14)
    dialog.ws_layout.setSpacing(10)

    # Header Title
    title_lbl = QLabel(f"Installed Workshop Mods ({dialog.game_data.get('game_name', 'Game')})")
    title_lbl.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {dialog.accent_color};")
    dialog.ws_layout.addWidget(title_lbl)

    # Loading placeholder
    dialog.ws_loading_lbl = QLabel("Scanning local workshop directories...")
    dialog.ws_loading_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 9.5pt;")
    dialog.ws_layout.addWidget(dialog.ws_loading_lbl)

    ws_widget.setLayout(dialog.ws_layout)
    scroll.setWidget(ws_widget)
    dialog.stacked.addWidget(scroll)


def scan_workshop_mods_async(dialog) -> None:
    """Scan local Steam Workshop directory in background and invoke UI callback."""
    def _thread_scan():
        ws_mods = []
        if dialog.appid and dialog.appid not in ("0", "N/A", "unknown"):
            try:
                wids = []
                local_mod_data = []

                for lib in get_steam_libraries():
                    ws_dir = Path(lib) / "steamapps" / "workshop" / "content" / str(dialog.appid)
                    if ws_dir.exists():
                        for item_dir in ws_dir.iterdir():
                            try:
                                if item_dir.is_dir() and item_dir.name.isdigit():
                                    wid = item_dir.name
                                    wids.append(wid)
                                    size = 0
                                    mtimes = []
                                    try:
                                        for f in item_dir.rglob("*"):
                                            try:
                                                if f.is_file():
                                                    st = f.stat()
                                                    size += st.st_size
                                                    mtimes.append(st.st_mtime)
                                            except OSError:
                                                pass
                                    except OSError:
                                        pass
                                    folder_mtime = item_dir.stat().st_mtime if item_dir.exists() else 0
                                    local_mod_data.append({
                                        "wid": wid,
                                        "path": str(item_dir),
                                        "size": size,
                                        "mtime": folder_mtime or max(mtimes, default=0),
                                        "folder_mtime": folder_mtime,
                                    })
                            except OSError:
                                pass

                # Read appworkshop_<appid>.acf across libraries to get installed manifest & timeupdated
                acf_installed_meta = {}
                for lib in get_steam_libraries():
                    acf_p = Path(lib) / "steamapps" / "workshop" / f"appworkshop_{dialog.appid}.acf"
                    if acf_p.exists():
                        try:
                            content = acf_p.read_text(encoding="utf-8", errors="ignore")
                            import vdf
                            aw = vdf.loads(content).get("AppWorkshop", {})
                            det = aw.get("WorkshopItemDetails", {})
                            inst = aw.get("WorkshopItemsInstalled", {})
                            for w_id in set(list(det.keys()) + list(inst.keys())):
                                m_id = str(det.get(w_id, {}).get("manifest") or inst.get(w_id, {}).get("manifest") or "")
                                t_up = int(det.get(w_id, {}).get("timeupdated") or inst.get(w_id, {}).get("timeupdated") or 0)
                                acf_installed_meta[str(w_id)] = {
                                    "manifest": m_id,
                                    "timeupdated": t_up,
                                }
                        except Exception as acf_err:
                            logger.debug(f"Error parsing workshop ACF {acf_p}: {acf_err}")

                # Batch fetch real titles & updated timestamps from Steam API
                api_details = fetch_workshop_details(wids) if wids else {}

                for mod in local_mod_data:
                    wid = mod["wid"]
                    details = api_details.get(wid, {})
                    raw_title = details.get("title") or f"Workshop Item #{wid}"
                    time_updated = details.get("time_updated", 0)

                    installed_info = acf_installed_meta.get(wid, {})
                    installed_manifest = installed_info.get("manifest", "")
                    installed_time = installed_info.get("timeupdated", 0)

                    remote_manifest = str(details.get("manifest", "") or "")

                    # 1. Authoritative check: compare manifest GID if both are known
                    if remote_manifest and installed_manifest:
                        update_available = (remote_manifest != installed_manifest)
                    # 2. Timestamp check: compare remote update timestamp against ACF recorded timestamp
                    elif time_updated > 0 and installed_time > 0:
                        update_available = (time_updated > installed_time)
                    # 3. Fallback: folder modification time with generous margin (1 day)
                    elif time_updated > 0 and mod.get("folder_mtime", 0) > 0:
                        update_available = (time_updated > mod["folder_mtime"] + 86400)
                    else:
                        update_available = False

                    dt = datetime.fromtimestamp(mod["mtime"]) if mod["mtime"] > 0 else datetime.now()
                    date_str = dt.strftime("%m/%d/%Y")

                    ws_mods.append({
                        "wid": wid,
                        "title": raw_title,
                        "path": mod["path"],
                        "size": mod["size"],
                        "mtime": mod["mtime"],
                        "date_str": date_str,
                        "time_updated": time_updated,
                        "update_available": update_available,
                    })

            except Exception as e:
                logger.error(f"Error scanning workshop mods: {e}")

        QMetaObject.invokeMethod(
            dialog,
            "_on_workshop_mods_scanned",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(list, ws_mods),
        )

    threading.Thread(target=_thread_scan, daemon=True).start()


def on_workshop_mods_scanned(dialog, ws_mods: list) -> None:
    """Populate Workshop tab UI with scanned mods cards and actions."""
    if not hasattr(dialog, "ws_layout") or dialog.ws_layout is None:
        return

    # Clear existing layout items safely
    while dialog.ws_layout.count() > 0:
        item = dialog.ws_layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    btn_fg = get_best_foreground_color(dialog.accent_color)

    # Header Row
    header_widget = QWidget()
    header_lay = QHBoxLayout(header_widget)
    header_lay.setContentsMargins(0, 0, 0, 6)

    game_name = strip_emojis(dialog.game_data.get("game_name", "Game"))
    title_lbl = QLabel(f"Installed Workshop Mods ({game_name})")
    title_lbl.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {dialog.accent_color};")
    header_lay.addWidget(title_lbl, 1)

    outdated_wids = [m["wid"] for m in ws_mods if m.get("update_available")]
    if outdated_wids:
        btn_update_all = QPushButton(f"Update All ({len(outdated_wids)})")
        btn_update_all.setFixedHeight(28)
        btn_update_all.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_update_all.setStyleSheet("""
            QPushButton {
                background: #E5A93C;
                border: none;
                border-radius: 6px;
                color: #000000;
                font-size: 8.5pt;
                font-weight: bold;
                padding: 0 12px;
            }
            QPushButton:hover {
                background: #F0B84D;
            }
        """)
        btn_update_all.clicked.connect(lambda: update_workshop_items(dialog, outdated_wids))
        header_lay.addWidget(btn_update_all)

    btn_fix = QPushButton("Fix Status")
    btn_fix.setFixedHeight(28)
    btn_fix.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_fix.setToolTip("Repair workshop flags, fix 'Content Encrypted' errors, and synchronize manifests.")
    btn_fix.setStyleSheet("""
        QPushButton {
            background: rgba(56, 189, 248, 0.15);
            border: 1px solid rgba(56, 189, 248, 0.35);
            border-radius: 6px;
            color: #38BDF8;
            font-size: 8.5pt;
            font-weight: bold;
            padding: 0 12px;
        }
        QPushButton:hover {
            background: rgba(56, 189, 248, 0.25);
            border-color: #38BDF8;
        }
    """)
    btn_fix.clicked.connect(lambda: fix_workshop_flags_action(dialog))
    header_lay.addWidget(btn_fix)

    btn_rescan = QPushButton("Refresh")
    btn_rescan.setFixedHeight(28)
    btn_rescan.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_rescan.setStyleSheet("""
        QPushButton {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 6px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: bold;
            padding: 0 12px;
        }
        QPushButton:hover {
            background: rgba(255, 255, 255, 0.15);
        }
    """)
    btn_rescan.clicked.connect(lambda: scan_workshop_mods_async(dialog))
    header_lay.addWidget(btn_rescan)

    dialog.ws_layout.addWidget(header_widget)

    if not ws_mods:
        empty_box = QFrame()
        empty_box.setStyleSheet("background: rgba(255, 255, 255, 0.03); border-radius: 8px; padding: 20px;")
        empty_lay = QVBoxLayout(empty_box)
        empty_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lbl = QLabel(
            "No Workshop mods installed for this game yet.\n"
            "Use 'Fetch Manifest' -> 'Workshop Downloader' to install mods."
        )
        empty_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 9.5pt; line-height: 1.4;")
        empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.addWidget(empty_lbl)
        dialog.ws_layout.addWidget(empty_box)
    else:
        for mod in ws_mods:
            mod_card = QFrame()
            mod_card.setObjectName("modCard")
            mod_card.setStyleSheet("""
                QFrame#modCard {
                    background: rgba(255, 255, 255, 0.04);
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 10px;
                }
                QFrame#modCard:hover {
                    background: rgba(255, 255, 255, 0.07);
                    border-color: rgba(255, 255, 255, 0.15);
                }
            """)
            card_lay = QHBoxLayout(mod_card)
            card_lay.setContentsMargins(14, 10, 14, 10)
            card_lay.setSpacing(10)

            size_bytes = mod["size"]
            if size_bytes >= 1024 * 1024 * 1024:
                formatted_size = f"{size_bytes / (1024**3):.2f} GB"
            elif size_bytes >= 1024 * 1024:
                formatted_size = f"{size_bytes / (1024**2):.2f} MB"
            else:
                formatted_size = f"{size_bytes / 1024:.2f} KB"

            date_str = mod.get("date_str", "")
            details_line = f"{formatted_size} • {date_str}" if date_str else formatted_size
            clean_title = str(mod.get("title", ""))
            mod_title = html.escape(clean_title)

            update_badge = ""
            if mod.get("update_available"):
                update_badge = (
                    "<span style='background: #E5A93C; color: #000000; font-size: 7.5pt; "
                    "font-weight: bold; padding: 2px 6px; border-radius: 4px; margin-left: 8px;'>UPDATE AVAILABLE</span>"
                )

            info_text = (
                f"<b style='color: #FFFFFF; font-size: 9.5pt;'>{mod_title}</b>{update_badge}<br>"
                f"<span style='color: rgba(255,255,255,0.6); font-size: 8pt;'>{details_line}</span>"
            )
            mod_info = QLabel(info_text)
            mod_info.setStyleSheet("background: transparent; border: none;")
            card_lay.addWidget(mod_info, 1)

            wid = mod["wid"]
            mod_path = mod["path"]

            # Action buttons
            upd_color = "#E5A93C"
            del_color = "#EF4444"

            def _mk_btn(
                tooltip,
                icon_path,
                icon_color,
                bg_normal,
                bg_hover,
                border_normal,
                border_hover,
                fixed_size=34,
            ):
                btn = QPushButton()
                btn.setFixedSize(fixed_size, fixed_size)
                btn.setToolTip(tooltip)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setText("")
                ic = make_svg_icon(icon_path, icon_color, size=18)
                btn.setIcon(ic)
                btn.setIconSize(QSize(18, 18))
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {bg_normal};
                        border: 1px solid {border_normal};
                        border-radius: 7px;
                        padding: 0px;
                    }}
                    QPushButton:hover {{
                        background: {bg_hover};
                        border-color: {border_hover};
                    }}
                    QPushButton:pressed {{
                        background: {border_hover};
                        border-color: {border_hover};
                    }}
                """)
                return btn

            actions_layout = QHBoxLayout()
            actions_layout.setSpacing(5)
            actions_layout.setContentsMargins(0, 0, 0, 0)

            # 1. Update button
            btn_upd = _mk_btn(
                "Update mod to latest version",
                Paths.icon("up1.svg"),
                upd_color,
                "rgba(229,169,60,0.15)",
                upd_color,
                "rgba(229,169,60,0.35)",
                upd_color,
            )
            btn_upd.setVisible(bool(mod.get("update_available")))
            btn_upd.clicked.connect(lambda _c, w=wid: update_workshop_items(dialog, [w]))
            actions_layout.addWidget(btn_upd)

            # 2. View on Steam Workshop
            btn_view = _mk_btn(
                "View on Steam Workshop",
                Paths.icon("link.svg"),
                "rgba(255,255,255,0.70)",
                "rgba(255,255,255,0.07)",
                dialog.accent_color,
                "rgba(255,255,255,0.12)",
                dialog.accent_color,
            )
            btn_view.clicked.connect(
                lambda _c, w=wid: QDesktopServices.openUrl(
                    QUrl(f"https://steamcommunity.com/sharedfiles/filedetails/?id={w}")
                )
            )
            actions_layout.addWidget(btn_view)

            # 3. Open local folder
            btn_open = _mk_btn(
                "Open local mod folder",
                Paths.icon("folder.svg"),
                "rgba(255,255,255,0.70)",
                "rgba(255,255,255,0.07)",
                "rgba(255,255,255,0.18)",
                "rgba(255,255,255,0.12)",
                "rgba(255,255,255,0.30)",
            )
            btn_open.clicked.connect(
                lambda _c, p=mod_path: QDesktopServices.openUrl(QUrl.fromLocalFile(p))
            )
            actions_layout.addWidget(btn_open)

            # 4. Delete / uninstall
            btn_del = _mk_btn(
                "Uninstall / delete mod",
                Paths.icon("bin.svg"),
                del_color,
                "rgba(239,68,68,0.12)",
                del_color,
                "rgba(239,68,68,0.28)",
                del_color,
            )
            btn_del.clicked.connect(
                lambda _c, w=wid, p=mod_path, t=clean_title: delete_workshop_item_dialog(
                    dialog, w, p, t
                )
            )
            actions_layout.addWidget(btn_del)

            card_lay.addLayout(actions_layout)
            dialog.ws_layout.addWidget(mod_card)

    dialog.ws_layout.addStretch()


def delete_workshop_item_dialog(dialog, wid: str, mod_path: str, title: str) -> None:
    """Prompt user to confirm deletion of workshop item and refresh list."""
    reply = QMessageBox.question(
        dialog,
        "Delete Workshop Mod",
        f"Are you sure you want to delete '{title}' (WID: {wid})?\nThis will permanently remove the item files.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply == QMessageBox.StandardButton.Yes:
        delete_workshop_item(dialog.appid, wid, mod_path)
        scan_workshop_mods_async(dialog)


def update_workshop_items(dialog, wids: List[str]) -> None:
    """Queue download job in main window job queue for outdated workshop items."""
    if not wids:
        return
    try:
        settings = get_settings()
        api_key = settings.value("morrenus_api_key", "", type=str).strip()
        max_downloads = settings.value("workshop_max_downloads", 8, type=int)
        cellid = settings.value("workshop_cell_id", "", type=str)
        steam_integration = settings.value("workshop_steam_enabled", True, type=bool)

        dest_path = find_steam_install()

        from ui.main_window import MainWindow

        mw = None
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, MainWindow):
                mw = widget
                break

        if mw and hasattr(mw, "job_queue"):
            mw.job_queue.add_workshop_job(wids, api_key, max_downloads, cellid, steam_integration, dest_path)
            QMessageBox.information(
                dialog,
                "Workshop Update",
                f"Queued update for {len(wids)} workshop mod(s)!\nCheck Job Manager for download progress.",
            )
        else:
            QMessageBox.warning(dialog, "Error", "Job queue manager not available.")
    except Exception as e:
        logger.error(f"Failed to queue workshop update: {e}")


def fix_workshop_flags_action(dialog) -> None:
    """Repair workshop ACF, base game appmanifest, and subscriptions to fix 'Content Encrypted' loops."""
    from utils.workshop_helpers import repair_workshop_for_game

    try:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        res = repair_workshop_for_game(dialog.appid)
        QApplication.restoreOverrideCursor()

        if res.get("success"):
            items_cnt = res.get("items_count", 0)
            repaired_bg = res.get("repaired_base_game", False)
            msg = f"Repaired workshop manifest for {items_cnt} mod(s)."
            if repaired_bg:
                msg += "\n• Synchronized base game AppManifest depots."
            msg += "\n• NeedsDownload and NeedsUpdate flags reset to 0."
            msg += "\n• UGC subscriptions list updated."
            msg += "\n\nTip: If Steam was already open, restart Steam once to reload UGC cache."
            QMessageBox.information(dialog, "Workshop Repaired", msg)
            scan_workshop_mods_async(dialog)
        else:
            QMessageBox.warning(dialog, "Workshop Repair", f"Could not repair workshop: {res.get('error', 'Unknown error')}")
    except Exception as e:
        QApplication.restoreOverrideCursor()
        logger.error(f"Error repairing workshop flags: {e}")
        QMessageBox.critical(dialog, "Error", f"Failed to repair workshop: {e}")

