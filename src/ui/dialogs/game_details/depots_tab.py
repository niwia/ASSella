"""
Depots tab implementation for GameDetailsDialogV2.
Modular inspector and configuration viewer for AT0-M / Native Steam mode games.
Displays real-time status of AdditionalApps, AdditionalDepots, and DecryptionKeys
specifically for the current game, with circular indicators and unlockable custom depot selection.
"""

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QMessageBox,
)

from ui.dialogs.game_details.hero_header import section_title
from utils.yaml_config_manager import (
    get_user_config_path,
    get_additional_apps,
    get_additional_depots,
    get_decryption_keys,
    batch_config_edit,
    ensure_plugins_enabled,
    _get_section_bounds,
)
from utils.sls_bridge import SLSBridge
from utils.plugin_games import SHARED_REDISTS, load_plugin_library
from ui.assets import DEPOT_BLACKLIST

logger = logging.getLogger(__name__)

# Unified set of shared redistributable depots across Steam
ALL_SHARED_REDISTS = SHARED_REDISTS | {str(d) for d in DEPOT_BLACKLIST}


class _WorkerBridge(QObject):
    finished = pyqtSignal(bool, str)


def _make_indicator(is_live: bool, is_ignored: bool = False) -> QWidget:
    """Create a sleek, non-interactive round status indicator (green if live, red if ignored, nothing if missing)."""
    container = QWidget()
    container.setStyleSheet("background: transparent;")
    h_lay = QHBoxLayout(container)
    h_lay.setContentsMargins(0, 0, 0, 0)
    h_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

    if is_live:
        dot = QFrame()
        dot.setFixedSize(14, 14)
        dot.setStyleSheet("""
            QFrame {
                background-color: #4caf50;
                border: 2px solid rgba(255, 255, 255, 0.25);
                border-radius: 7px;
            }
        """)
        h_lay.addWidget(dot)
    elif is_ignored:
        dot = QFrame()
        dot.setFixedSize(14, 14)
        dot.setStyleSheet("""
            QFrame {
                background-color: #f44336;
                border: 2px solid rgba(255, 255, 255, 0.25);
                border-radius: 7px;
            }
        """)
        h_lay.addWidget(dot)
    else:
        # Missing: show nothing
        empty = QLabel("")
        h_lay.addWidget(empty)

    return container


def _make_table(columns: List[str], accent_color: str) -> QTableWidget:
    """Create a styled table widget matching ASSella design conventions."""
    tbl = QTableWidget()
    tbl.setColumnCount(len(columns))
    for c_idx, col_name in enumerate(columns):
        item = QTableWidgetItem(col_name)
        if col_name in ("Config", "Local"):
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        tbl.setHorizontalHeaderItem(c_idx, item)

    tbl.verticalHeader().setVisible(False)
    tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    tbl.setShowGrid(False)
    tbl.setAlternatingRowColors(True)
    tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    tbl.setStyleSheet(f"""
        QTableWidget {{
            background-color: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
            gridline-color: transparent;
            outline: 0;
            color: #FFFFFF;
            font-size: 9pt;
        }}
        QTableWidget::item {{
            padding: 4px 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
        }}
        QTableWidget::item:selected {{
            background-color: rgba(255, 255, 255, 0.08);
            color: #FFFFFF;
        }}
        QHeaderView::section {{
            background-color: rgba(255, 255, 255, 0.04);
            color: rgba(255, 255, 255, 0.85);
            padding: 6px 10px;
            border: none;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            font-size: 8.5pt;
            font-weight: bold;
        }}
    """)
    return tbl


def _set_table_height(tbl: QTableWidget, row_count: int) -> None:
    """Set exact height to display all rows without internal scrollbar or clipping."""
    header_h = 32
    row_h = 32
    padding = 8
    total_h = header_h + (max(1, row_count) * row_h) + padding
    tbl.setFixedHeight(total_h)


def init_depots_tab(dialog) -> None:
    """Initialize the modular Depots tab for AT0-M mode games."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    content_widget = QWidget()
    content_widget.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(content_widget)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(14)

    ac = dialog.accent_color
    appid_str = str(dialog.appid).strip()

    # Advanced unlock and custom depot exclusion tracking
    is_unlocked = False
    ignored_depots: Set[str] = set()

    # Section 1: AdditionalApps (AppIDs & DLCs)
    layout.addWidget(section_title("AppIDs & Licenses (AdditionalApps)", ac))
    apps_table = _make_table(["AppID", "Name", "Config"], ac)
    apps_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    apps_table.setColumnWidth(0, 110)
    apps_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    apps_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    apps_table.setColumnWidth(2, 75)
    layout.addWidget(apps_table)

    # Section 2: AdditionalDepots (Depots)
    layout.addWidget(section_title("Depots Registered (AdditionalDepots)", ac))
    depots_table = _make_table(["Depot ID", "Depot names", "Config"], ac)
    depots_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    depots_table.setColumnWidth(0, 110)
    depots_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    depots_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    depots_table.setColumnWidth(2, 75)
    layout.addWidget(depots_table)

    # Section 3: DecryptionKeys (Keys)
    layout.addWidget(section_title("AES Decryption Keys (DecryptionKeys)", ac))
    keys_table = _make_table(["Target ID", "AES keys", "Config", "Local"], ac)
    keys_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    keys_table.setColumnWidth(0, 110)
    keys_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    keys_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    keys_table.setColumnWidth(2, 75)
    keys_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
    keys_table.setColumnWidth(3, 75)
    layout.addWidget(keys_table)

    # Status Label
    status_lbl = QLabel("")
    status_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(status_lbl)

    # Bottom Actions Bar: 3 buttons filling the bar
    btn_bar = QFrame()
    btn_bar.setStyleSheet("background: transparent; border: none;")
    bar_lay = QHBoxLayout(btn_bar)
    bar_lay.setContentsMargins(0, 4, 0, 0)
    bar_lay.setSpacing(10)

    refetch_btn = QPushButton("Refetch")
    refetch_btn.setFixedHeight(34)
    refetch_btn.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            color: #FFFFFF;
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 6px;
            font-size: 9pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.14);
            border-color: rgba(255, 255, 255, 0.25);
        }
        QPushButton:disabled {
            background-color: rgba(255, 255, 255, 0.03);
            color: rgba(255, 255, 255, 0.3);
            border-color: rgba(255, 255, 255, 0.05);
        }
    """)

    unlock_btn = QPushButton("Unlock")
    unlock_btn.setFixedHeight(34)
    unlock_btn_style_locked = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            color: #FFFFFF;
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 6px;
            font-size: 9pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.14);
            border-color: rgba(255, 255, 255, 0.25);
        }
    """
    unlock_btn_style_unlocked = """
        QPushButton {
            background-color: rgba(255, 152, 0, 0.18);
            color: #ffb74d;
            border: 1px solid rgba(255, 152, 0, 0.45);
            border-radius: 6px;
            font-size: 9pt;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: rgba(255, 152, 0, 0.26);
        }
    """
    unlock_btn.setStyleSheet(unlock_btn_style_locked)

    sync_btn = QPushButton("Sync")
    sync_btn.setFixedHeight(34)
    sync_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {ac};
            color: #000000;
            border: none;
            border-radius: 6px;
            font-size: 9pt;
            font-weight: bold;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.9);
        }}
        QPushButton:disabled {{
            background-color: rgba(255, 255, 255, 0.1);
            color: rgba(255, 255, 255, 0.3);
        }}
    """)

    bar_lay.addWidget(refetch_btn, 1)
    bar_lay.addWidget(unlock_btn, 1)
    bar_lay.addWidget(sync_btn, 1)
    layout.addWidget(btn_bar)

    scroll.setWidget(content_widget)
    dialog.stacked.addWidget(scroll)

    def _collect_state() -> Dict[str, Any]:
        """Collect and cross-reference data strictly for THIS game."""
        cfg_path = get_user_config_path()
        live_apps = set(get_additional_apps(cfg_path)) if cfg_path.exists() else set()
        live_depots = set(get_additional_depots(cfg_path)) if cfg_path.exists() else set()
        live_keys = get_decryption_keys(cfg_path) if cfg_path.exists() else {}

        # 1. Plugin Library record for THIS game
        plugin_lib = load_plugin_library()
        game_record = plugin_lib.get(appid_str, {})
        game_name = dialog.game_data.get("game_name") or game_record.get("name") or f"App {appid_str}"

        plugin_depots = {str(d) for d in game_record.get("depots", [])}
        plugin_keys = {str(d): str(k).lower() for d, k in game_record.get("keys", {}).items()}
        plugin_depot_names = {str(d): str(n) for d, n in game_record.get("depot_names", {}).items()}
        plugin_dlcs = {str(d) for d in game_record.get("dlc_appids", [])}

        # 2. Local SQLite depot keys for THIS appid
        from managers.depot_key_manager import DepotKeyManager
        dkm = DepotKeyManager.get_instance()
        local_db_keys = {str(d): str(k).lower() for d, k in (dkm.get_keys_for_app(appid_str) or {}).items()}

        # 3. Cached Lua parsing for THIS game
        from utils.helpers import get_base_path
        cached_lua_path = Path(get_base_path()) / "cached_luas" / f"{appid_str}.lua"
        lua_keys: Dict[str, str] = {}
        lua_dlcs: Dict[str, str] = {}
        lua_depots: Dict[str, str] = {}
        blacklisted_depots: Set[str] = set()

        if cached_lua_path.exists():
            try:
                txt = cached_lua_path.read_text(encoding="utf-8", errors="ignore")
                for line in txt.splitlines():
                    if "blacklisted" in line.lower() and "depot" in line.lower():
                        for num in re.findall(r"\b(\d{4,9})\b", line):
                            blacklisted_depots.add(num)

                # addappid keys
                for m in re.finditer(r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)', txt):
                    did, key = m.group(1), m.group(2)
                    lua_keys[str(did)] = key.lower()

                from core.tasks.process_zip_task import ProcessZipTask
                parsed: Dict[str, Any] = {}
                ProcessZipTask._parse_lua(txt, parsed)
                if parsed.get("app_key"):
                    lua_keys[appid_str] = parsed["app_key"].lower()
                if parsed.get("dlcs"):
                    for dlc_id, dlc_name in parsed["dlcs"].items():
                        lua_dlcs[str(dlc_id)] = str(dlc_name)
                if parsed.get("depots"):
                    for d, info in parsed["depots"].items():
                        d_str = str(d)
                        if isinstance(info, dict):
                            desc = info.get("desc") or info.get("name") or ""
                            lua_depots[d_str] = desc
                            if info.get("key"):
                                lua_keys[d_str] = str(info["key"]).lower()
            except Exception as e:
                logger.debug(f"[DepotsTab] Error parsing cached Lua: {e}")

        # 4. Merge game_data metadata
        gd = dialog.game_data or {}
        gd_dlcs = gd.get("dlcs") or {}
        for dlc_id, dlc_desc in gd_dlcs.items():
            lua_dlcs.setdefault(str(dlc_id), str(dlc_desc))

        gd_depots = gd.get("depots") or {}
        for d, dinfo in gd_depots.items():
            d_str = str(d)
            if isinstance(dinfo, dict):
                desc = dinfo.get("desc") or dinfo.get("name") or ""
                lua_depots.setdefault(d_str, desc)
                k = dinfo.get("key") or dinfo.get("decryption_key")
                if k and len(str(k)) == 64:
                    lua_keys.setdefault(d_str, str(k).lower())

        # 5. Check config.yaml entries specifically tagged with this game's name or AppID
        tagged_depots_from_config: Set[str] = set()
        tagged_keys_from_config: Dict[str, str] = {}
        if cfg_path.exists():
            try:
                cfg_text = cfg_path.read_text(encoding="utf-8", errors="ignore")
                dep_bounds = _get_section_bounds(cfg_text, "AdditionalDepots")
                if dep_bounds:
                    sec_dep = cfg_text[dep_bounds[1]:dep_bounds[2]]
                    for line in sec_dep.splitlines():
                        m = re.match(r"^[ \t]*-[ \t]*(\d+)[ \t]*(?:#[ \t]*(.*))?$", line)
                        if m:
                            did, comment = m.group(1), (m.group(2) or "")
                            if (game_name and game_name.lower() in comment.lower()) or appid_str in comment:
                                tagged_depots_from_config.add(did)

                key_bounds = _get_section_bounds(cfg_text, "DecryptionKeys")
                if key_bounds:
                    sec_key = cfg_text[key_bounds[1]:key_bounds[2]]
                    for line in sec_key.splitlines():
                        m = re.match(r"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*['\"]?([a-fA-F0-9]{64})['\"]?[ \t]*(?:#[ \t]*(.*))?$", line)
                        if m:
                            did, kval, comment = m.group(1), m.group(2), (m.group(3) or "")
                            if (game_name and game_name.lower() in comment.lower()) or appid_str in comment:
                                tagged_keys_from_config[did] = kval.lower()
            except Exception as e:
                logger.debug(f"[DepotsTab] Error reading tagged config comments: {e}")

        # Compile game-specific keys map (strictly excluding common shared redists)
        game_keys: Dict[str, str] = {}
        game_keys.update(local_db_keys)
        game_keys.update(lua_keys)
        game_keys.update(plugin_keys)
        game_keys.update(tagged_keys_from_config)
        game_keys = {d: k for d, k in game_keys.items() if d not in ALL_SHARED_REDISTS}

        # DLC AppIDs set
        dlc_id_set = set(lua_dlcs.keys()) | plugin_dlcs
        dlc_id_set.discard(appid_str)

        # Build AppIDs list: Base AppID + DLC AppIDs
        apps_list = [{"id": appid_str, "desc": f"{game_name} (Base)", "is_base": True}]
        for dlc_id in sorted(dlc_id_set, key=lambda x: int(x) if x.isdigit() else 0):
            desc = lua_dlcs.get(dlc_id) or gd_dlcs.get(dlc_id) or f"DLC {dlc_id}"
            apps_list.append({"id": dlc_id, "desc": f"[DLC] {desc}", "is_base": False})

        # Build Depots list for THIS GAME ONLY:
        # Candidate depots known to belong to this game (excluding shared redists, base appid, DLCs)
        game_depots_set = (
            set(lua_depots.keys())
            | plugin_depots
            | set(game_keys.keys())
            | tagged_depots_from_config
        ) - ALL_SHARED_REDISTS - {appid_str} - dlc_id_set

        valid_key_depots = set(game_keys.keys())
        depots_list = []
        for d in sorted(game_depots_set, key=lambda x: int(x) if x.isdigit() else 0):
            if d in blacklisted_depots and d not in live_depots and d not in valid_key_depots:
                continue  # Blacklisted without keys
            desc = (
                plugin_depot_names.get(d)
                or lua_depots.get(d)
                or (gd_depots.get(d, {}).get("desc") if isinstance(gd_depots.get(d), dict) else "")
                or f"Depot {d}"
            )
            depots_list.append({
                "id": d,
                "desc": desc,
                "has_key": (d in valid_key_depots),
            })

        # Build Keys list for THIS GAME ONLY:
        # Includes Root AppKey (if available) + this game's depot keys
        keys_list = []
        if appid_str in game_keys or appid_str in live_keys:
            root_k = game_keys.get(appid_str) or live_keys.get(appid_str) or ""
            keys_list.append({
                "id": appid_str,
                "key": root_k,
                "is_appkey": True,
                "in_config": (appid_str in live_keys),
                "in_local": bool(appid_str in local_db_keys or appid_str in lua_keys or appid_str in plugin_keys),
            })

        for d in sorted(game_depots_set, key=lambda x: int(x) if x.isdigit() else 0):
            key_val = game_keys.get(d) or (live_keys.get(d) if d in tagged_depots_from_config else "")
            if not key_val and d not in game_keys and d not in live_keys:
                continue
            keys_list.append({
                "id": d,
                "key": key_val or live_keys.get(d, ""),
                "is_appkey": False,
                "in_config": (d in live_keys),
                "in_local": bool(d in local_db_keys or d in lua_keys or d in plugin_keys),
            })

        return {
            "apps": apps_list,
            "depots": depots_list,
            "keys": keys_list,
            "live_apps": live_apps,
            "live_depots": live_depots,
            "live_keys": live_keys,
            "local_db_keys": local_db_keys,
            "lua_keys": lua_keys,
        }

    def _refresh_ui():
        """Populate table widgets with gathered data."""
        state = _collect_state()

        # 1. Populate AdditionalApps Table
        apps = state["apps"]
        live_apps = state["live_apps"]
        apps_table.setRowCount(len(apps))
        for row, item in enumerate(apps):
            id_item = QTableWidgetItem(item["id"])
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            apps_table.setItem(row, 0, id_item)

            desc_item = QTableWidgetItem(item["desc"])
            desc_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            apps_table.setItem(row, 1, desc_item)

            is_live = item["id"] in live_apps
            indicator = _make_indicator(is_live=is_live)
            apps_table.setCellWidget(row, 2, indicator)

        _set_table_height(apps_table, len(apps))

        # 2. Populate AdditionalDepots Table
        depots = state["depots"]
        live_depots = state["live_depots"]
        depots_table.setRowCount(len(depots))
        for row, item in enumerate(depots):
            did = item["id"]
            is_live = did in live_depots
            is_ignored = did in ignored_depots

            id_item = QTableWidgetItem(did)
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            desc_txt = item["desc"]
            if is_ignored:
                desc_txt = f"{desc_txt} (Ignored)"
            desc_item = QTableWidgetItem(desc_txt)
            desc_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            if is_ignored:
                id_item.setForeground(QColor("#e57373"))
                desc_item.setForeground(QColor("#e57373"))

            depots_table.setItem(row, 0, id_item)
            depots_table.setItem(row, 1, desc_item)

            indicator = _make_indicator(is_live=is_live, is_ignored=is_ignored)
            depots_table.setCellWidget(row, 2, indicator)

        _set_table_height(depots_table, len(depots))

        # 3. Populate DecryptionKeys Table
        keys = state["keys"]
        keys_table.setRowCount(len(keys))
        for row, item in enumerate(keys):
            id_item = QTableWidgetItem(item["id"])
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            keys_table.setItem(row, 0, id_item)

            raw_key = item["key"]
            short_key = f"{raw_key[:15]}..." if len(raw_key) >= 15 else (raw_key or "N/A")
            key_item = QTableWidgetItem(short_key)
            key_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            keys_table.setItem(row, 1, key_item)

            is_cfg = item["in_config"]
            cfg_ind = _make_indicator(is_live=is_cfg)
            keys_table.setCellWidget(row, 2, cfg_ind)

            is_loc = item["in_local"]
            loc_ind = _make_indicator(is_live=is_loc)
            keys_table.setCellWidget(row, 3, loc_ind)

        _set_table_height(keys_table, len(keys))

    def _on_depot_cell_clicked(row: int, _col: int):
        """Toggle missing depot between included and ignored when in unlock mode."""
        nonlocal is_unlocked
        if not is_unlocked:
            return
        state = _collect_state()
        depots = state["depots"]
        if row < 0 or row >= len(depots):
            return
        item = depots[row]
        did = item["id"]

        # Only missing depots can be toggled
        if did in state["live_depots"]:
            return

        if did in ignored_depots:
            ignored_depots.remove(did)
            status_lbl.setText(f"Depot {did} marked for injection.")
        else:
            ignored_depots.add(did)
            status_lbl.setText(f"Depot {did} marked as ignored (will not be injected).")

        sync_btn.setText("Sync Changes")
        _refresh_ui()

    depots_table.cellClicked.connect(_on_depot_cell_clicked)

    def _on_unlock():
        """Unlock custom depot selection after confirmation dialog."""
        nonlocal is_unlocked
        if not is_unlocked:
            reply = QMessageBox.warning(
                dialog,
                "Advanced Configuration",
                "Modifying depot selection is intended for advanced users only.\n\n"
                "Excluding required depots or keys may prevent Steam from downloading or launching the game properly.\n\n"
                "Do you want to unlock custom depot selection?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                is_unlocked = True
                unlock_btn.setText("Unlocked")
                unlock_btn.setStyleSheet(unlock_btn_style_unlocked)
                status_lbl.setText("Custom depot selection unlocked. Click missing depots to toggle/ignore.")
                _refresh_ui()
        else:
            is_unlocked = False
            unlock_btn.setText("Unlock")
            unlock_btn.setStyleSheet(unlock_btn_style_locked)
            status_lbl.setText("Depot selection locked.")
            _refresh_ui()

    def _on_sync():
        """Sync missing AppIDs, non-ignored depots, and keys specifically for this game into config.yaml."""
        state = _collect_state()
        cfg_path = get_user_config_path()
        if not cfg_path.exists():
            status_lbl.setText("Error: config.yaml not found.")
            return

        ensure_plugins_enabled(cfg_path)
        game_name = dialog.game_data.get("game_name") or f"App {appid_str}"

        added_apps = 0
        added_depots = 0
        added_keys = 0

        with batch_config_edit(cfg_path) as editor:
            # 1. Apps
            for item in state["apps"]:
                if item["id"] not in state["live_apps"]:
                    editor.add_app(item["id"], comment=item["desc"])
                    added_apps += 1

            # 2. Depots (only keyed depots belonging to this game, excluding ignored depots)
            valid_keys = set(item["id"] for item in state["keys"] if item["key"])
            for item in state["depots"]:
                did = item["id"]
                if did in ignored_depots:
                    continue  # User explicitly ignored this depot
                if did not in state["live_depots"] and did in valid_keys:
                    comment = f"{game_name} ({did})"
                    editor.add_depot(did, comment=comment)
                    added_depots += 1

            # 3. Keys
            for item in state["keys"]:
                did = item["id"]
                kval = item["key"]
                if kval and not item["in_config"]:
                    comment = f"{game_name} [AppKey]" if did == appid_str else f"{game_name} ({did})"
                    editor.add_key(did, kval, comment=comment)
                    added_keys += 1

        if editor.has_changes:
            SLSBridge.notify_reload()
            status_lbl.setText(f"Synced: {added_apps} app(s), {added_depots} depot(s), {added_keys} key(s).")
        else:
            status_lbl.setText("All eligible items are already live in Config.")

        sync_btn.setText("Sync")
        _refresh_ui()

    def _on_refetch():
        """Refetch fresh Lua metadata and keys from Hubcap API."""
        refetch_btn.setEnabled(False)
        unlock_btn.setEnabled(False)
        sync_btn.setEnabled(False)
        status_lbl.setText("Refetching metadata from Hubcap...")

        bridge = _WorkerBridge()

        def _worker():
            try:
                from core import morrenus_api
                zip_path, err = morrenus_api.download_manifest(appid_str, force_update=True)
                if zip_path and os.path.exists(zip_path):
                    import zipfile
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        lua_files = [f for f in zf.namelist() if f.endswith(".lua")]
                        if lua_files:
                            from utils.helpers import get_base_path
                            dest_lua = Path(get_base_path()) / "cached_luas" / f"{appid_str}.lua"
                            dest_lua.parent.mkdir(parents=True, exist_ok=True)
                            lua_data = zf.read(lua_files[0]).decode("utf-8", errors="ignore")
                            dest_lua.write_text(lua_data, encoding="utf-8")

                            # Extract and persist keys to SQLite
                            fresh_keys = {}
                            for m in re.finditer(r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)', lua_data):
                                fresh_keys[m.group(1)] = m.group(2).lower()
                            if fresh_keys:
                                from managers.depot_key_manager import DepotKeyManager
                                DepotKeyManager.get_instance().save_depot_keys(appid_str, fresh_keys)

                    bridge.finished.emit(True, "Refetch successful. Local cache updated.")
                else:
                    bridge.finished.emit(False, f"Refetch failed: {err or 'Unknown error'}")
            except Exception as e:
                bridge.finished.emit(False, f"Refetch error: {e}")

        def _on_done(success: bool, msg: str):
            refetch_btn.setEnabled(True)
            unlock_btn.setEnabled(True)
            sync_btn.setEnabled(True)
            status_lbl.setText(msg)
            _refresh_ui()

        bridge.finished.connect(_on_done)
        threading.Thread(target=_worker, daemon=True).start()

    refetch_btn.clicked.connect(_on_refetch)
    unlock_btn.clicked.connect(_on_unlock)
    sync_btn.clicked.connect(_on_sync)

    # Initial load
    _refresh_ui()
