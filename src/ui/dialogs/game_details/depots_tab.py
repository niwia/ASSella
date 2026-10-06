"""
Depots tab implementation for GameDetailsDialogV2.
Modular inspector and configuration viewer for AT0-M / Native Steam mode games.
Displays real-time status of AdditionalApps, AdditionalDepots, and DecryptionKeys
specifically for the current game, with sideways switch toggles, bulk category toggles (>5 items),
one-way dynamic cascade disabling, and unlockable custom editing.
"""

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import Qt, QObject, pyqtSignal, pyqtProperty, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen
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


class MiniSwitchToggle(QWidget):
    """
    Minimal sideways switch toggle widget.
    Turns Green (#4CAF50) when checked (enabled) and Red (#E53935) when unchecked (disabled).
    Knob slides smoothly sideways with cubic easing.
    In read-only mode (locked), user interaction is disabled.
    """
    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, read_only: bool = True, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._read_only = read_only
        self._hovered = False
        self.setFixedSize(36, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor if not read_only else Qt.CursorShape.ArrowCursor)

        # Knob slide range: unchecked = 2.0 (left), checked = 20.0 (right)
        self._circle_pos = 20.0 if checked else 2.0
        self._anim = QPropertyAnimation(self, b"circle_pos", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    @pyqtProperty(float)
    def circle_pos(self) -> float:
        return self._circle_pos

    @circle_pos.setter
    def circle_pos(self, pos: float) -> None:
        self._circle_pos = pos
        self.update()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, animate: bool = True) -> None:
        if self._checked != checked:
            self._checked = checked
            target = 20.0 if checked else 2.0
            if animate:
                self._anim.stop()
                self._anim.setStartValue(self._circle_pos)
                self._anim.setEndValue(target)
                self._anim.start()
            else:
                self._circle_pos = target
                self.update()
            self.toggled.emit(self._checked)

    def setReadOnly(self, read_only: bool) -> None:
        self._read_only = read_only
        self.setCursor(Qt.CursorShape.PointingHandCursor if not read_only else Qt.CursorShape.ArrowCursor)
        self.update()

    def isReadOnly(self) -> bool:
        return self._read_only

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._read_only:
                self.setChecked(not self._checked)
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        radius = h / 2.0

        # Background track: Green if checked, Red if unchecked
        track_color = QColor("#4CAF50") if self._checked else QColor("#E53935")
        if self._read_only:
            track_color.setAlpha(190)
        else:
            track_color.setAlpha(255)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(track_color))
        p.drawRoundedRect(0, 0, w, h, radius, radius)

        # Subtle highlight border when hovering in interactive mode
        if self._hovered and not self._read_only:
            p.setPen(QPen(QColor(255, 255, 255, 120), 1.5))
            p.drawRoundedRect(1, 1, w - 2, h - 2, radius - 1, radius - 1)

        # White knob circle
        knob_d = 14.0
        knob_y = (h - knob_d) / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.drawEllipse(int(self._circle_pos), int(knob_y), int(knob_d), int(knob_d))
        p.end()


class _ToggleContainer(QWidget):
    """Container widget to center the switch toggle inside a table cell and forward clicks."""
    def __init__(self, toggle: MiniSwitchToggle, parent=None):
        super().__init__(parent)
        self.toggle = toggle
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(toggle)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.toggle.isReadOnly():
            self.toggle.setChecked(not self.toggle.isChecked())
            event.accept()
        else:
            super().mousePressEvent(event)


def _make_local_indicator(in_local: bool) -> QWidget:
    """Create a sleek, non-interactive indicator for Local DB status (never toggleable)."""
    container = QWidget()
    container.setStyleSheet("background: transparent;")
    h_lay = QHBoxLayout(container)
    h_lay.setContentsMargins(0, 0, 0, 0)
    h_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

    if in_local:
        dot = QFrame()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet("""
            QFrame {
                background-color: #4CAF50;
                border-radius: 5px;
            }
        """)
        h_lay.addWidget(dot)
    else:
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


def _make_bulk_toggle_bar(category_name: str, on_toggle_cb) -> Tuple[QFrame, MiniSwitchToggle, QPushButton, QPushButton]:
    """Create a sleek footer bar with a Select All / None switch and quick buttons for categories with >5 items."""
    bar = QFrame()
    bar.setStyleSheet("background: transparent; border: none;")
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(4, 2, 4, 4)
    lay.setSpacing(8)

    lbl = QLabel(f"Select All / None ({category_name}):")
    lbl.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    lay.addWidget(lbl)

    toggle = MiniSwitchToggle(checked=False, read_only=True)
    toggle.toggled.connect(on_toggle_cb)
    lay.addWidget(toggle)

    lay.addStretch()

    btn_all = QPushButton("All")
    btn_all.setFixedSize(38, 22)
    btn_all.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            color: rgba(255, 255, 255, 0.85);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 4px;
            font-size: 8pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.15);
            color: #FFFFFF;
        }
        QPushButton:disabled {
            background-color: rgba(255, 255, 255, 0.03);
            color: rgba(255, 255, 255, 0.3);
            border-color: rgba(255, 255, 255, 0.05);
        }
    """)

    btn_none = QPushButton("None")
    btn_none.setFixedSize(44, 22)
    btn_none.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            color: rgba(255, 255, 255, 0.85);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 4px;
            font-size: 8pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.15);
            color: #FFFFFF;
        }
        QPushButton:disabled {
            background-color: rgba(255, 255, 255, 0.03);
            color: rgba(255, 255, 255, 0.3);
            border-color: rgba(255, 255, 255, 0.05);
        }
    """)

    def _do_toggle(val: bool):
        toggle.setChecked(val)
        on_toggle_cb(val)

    btn_all.clicked.connect(lambda: _do_toggle(True))
    btn_none.clicked.connect(lambda: _do_toggle(False))

    lay.addWidget(btn_all)
    lay.addWidget(btn_none)

    return bar, toggle, btn_all, btn_none


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

    # Advanced unlock and desired states tracking
    is_unlocked = False
    desired_apps: Dict[str, bool] = {}
    desired_depots: Dict[str, bool] = {}
    desired_keys: Dict[str, bool] = {}

    # Active row switch references for dynamic cascade updates
    app_toggles: Dict[str, MiniSwitchToggle] = {}
    depot_toggles: Dict[str, MiniSwitchToggle] = {}
    key_toggles: Dict[str, MiniSwitchToggle] = {}

    # State flag to prevent recursive loops during bulk select
    _internal_bulk_update = False

    # Section 1: AdditionalApps (AppIDs & DLCs)
    layout.addWidget(section_title("AppIDs & Licenses (AdditionalApps)", ac))
    apps_table = _make_table(["AppID", "Name", "Config"], ac)
    apps_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    apps_table.setColumnWidth(0, 110)
    apps_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    apps_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    apps_table.setColumnWidth(2, 75)
    layout.addWidget(apps_table)

    apps_bulk_bar, apps_bulk_toggle, apps_btn_all, apps_btn_none = _make_bulk_toggle_bar(
        "AppIDs", lambda chk: _on_bulk_toggle_apps(chk)
    )
    layout.addWidget(apps_bulk_bar)

    # Section 2: AdditionalDepots (Depots)
    layout.addWidget(section_title("Depots Registered (AdditionalDepots)", ac))
    depots_table = _make_table(["Depot ID", "Depot names", "Config"], ac)
    depots_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    depots_table.setColumnWidth(0, 110)
    depots_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    depots_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    depots_table.setColumnWidth(2, 75)
    layout.addWidget(depots_table)

    depots_bulk_bar, depots_bulk_toggle, depots_btn_all, depots_btn_none = _make_bulk_toggle_bar(
        "Depots", lambda chk: _on_bulk_toggle_depots(chk)
    )
    layout.addWidget(depots_bulk_bar)

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

    keys_bulk_bar, keys_bulk_toggle, keys_btn_all, keys_btn_none = _make_bulk_toggle_bar(
        "Keys", lambda chk: _on_bulk_toggle_keys(chk)
    )
    layout.addWidget(keys_bulk_bar)

    # Master Select All / None toggle bar at the bottom of the tab
    tab_bulk_bar, tab_bulk_toggle, tab_btn_all, tab_btn_none = _make_bulk_toggle_bar(
        "All Sections", lambda chk: _on_bulk_toggle_all(chk)
    )
    layout.addWidget(tab_bulk_bar)

    # Status Label
    status_lbl = QLabel("")
    status_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(status_lbl)

    # Bottom Actions Bar: 3 buttons evenly filling the bar
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
            background-color: rgba(255, 152, 0, 0.22);
            color: #ffb74d;
            border: 1px solid rgba(255, 152, 0, 0.55);
            border-radius: 6px;
            font-size: 9pt;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: rgba(255, 152, 0, 0.32);
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

    def _build_app_to_depots_map(state_apps: List[Dict[str, Any]]) -> Dict[str, Set[str]]:
        """
        Dynamically map each AppID (base game and DLCs) to its associated depot IDs.
        Used for one-way dynamic cascade disabling.
        """
        mapping: Dict[str, Set[str]] = {}

        # 1. Check DatabaseManager SQLite records
        try:
            from managers.db_manager import DatabaseManager
            db_info = DatabaseManager().get_app_info(appid_str)
            if db_info and db_info.get("depots"):
                for did, d_meta in db_info["depots"].items():
                    did_str = str(did).strip()
                    dlc_id = str(d_meta.get("dlcappid") or "").strip()
                    if dlc_id and dlc_id.isdigit():
                        mapping.setdefault(dlc_id, set()).add(did_str)
                    elif d_meta.get("is_dlc"):
                        desc = d_meta.get("name") or d_meta.get("desc") or ""
                        m = re.search(r"\[DLC\s*(\d+)\]", desc)
                        if m:
                            mapping.setdefault(m.group(1), set()).add(did_str)
        except Exception:
            pass

        # 2. Check game_data / installed_depots from ACF
        gd = dialog.game_data or {}
        inst_depots = gd.get("installed_depots") or {}
        for did, dinfo in inst_depots.items():
            did_str = str(did).strip()
            if isinstance(dinfo, dict):
                dlc_id = str(dinfo.get("dlcappid") or "").strip()
                if dlc_id and dlc_id.isdigit():
                    mapping.setdefault(dlc_id, set()).add(did_str)

        # 3. Check cached Lua DLC sections
        try:
            from utils.helpers import get_base_path
            lua_path = Path(get_base_path()) / "cached_luas" / f"{appid_str}.lua"
            if lua_path.exists():
                txt = lua_path.read_text(encoding="utf-8", errors="ignore")
                current_dlc = None
                for line in txt.splitlines():
                    m_dlc = re.search(r"\(AppID:\s*(\d+)\)", line, re.IGNORECASE)
                    if m_dlc:
                        current_dlc = m_dlc.group(1)
                    elif current_dlc:
                        m_app = re.search(r"addappid\((\d+)", line)
                        if m_app:
                            mapping.setdefault(current_dlc, set()).add(m_app.group(1))
                        elif line.startswith("-- MAIN") or line.startswith("-- SHARED"):
                            current_dlc = None
        except Exception:
            pass

        # 4. Direct match fallback: any depot with ID == DLC AppID
        for item in state_apps:
            aid = item["id"]
            mapping.setdefault(aid, set()).add(aid)

        return mapping

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
        tagged_apps_from_config: Dict[str, str] = {}
        tagged_depots_from_config: Set[str] = set()
        tagged_keys_from_config: Dict[str, str] = {}
        if cfg_path.exists():
            try:
                cfg_text = cfg_path.read_text(encoding="utf-8", errors="ignore")
                app_bounds = _get_section_bounds(cfg_text, "AdditionalApps")
                if app_bounds:
                    sec_app = cfg_text[app_bounds[1]:app_bounds[2]]
                    for line in sec_app.splitlines():
                        m = re.match(r"^[ \t]*-[ \t]*(\d+)[ \t]*(?:#[ \t]*(.*))?$", line)
                        if m:
                            aid, comment = m.group(1), (m.group(2) or "")
                            if (game_name and game_name.lower() in comment.lower()) or appid_str in comment:
                                if aid != appid_str:
                                    clean_c = re.sub(r"^\[DLC\]\s*", "", comment.strip(), flags=re.IGNORECASE)
                                    tagged_apps_from_config[aid] = clean_c or f"DLC {aid}"

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

        # DLC AppIDs set (combining Lua, plugin library, and tagged AdditionalApps from config)
        dlc_id_set = set(lua_dlcs.keys()) | plugin_dlcs | set(tagged_apps_from_config.keys())
        dlc_id_set.discard(appid_str)

        # Build AppIDs list: Base AppID + DLC AppIDs
        apps_list = [{"id": appid_str, "desc": f"{game_name} (Base)", "is_base": True}]
        for dlc_id in sorted(dlc_id_set, key=lambda x: int(x) if x.isdigit() else 0):
            desc = lua_dlcs.get(dlc_id) or gd_dlcs.get(dlc_id) or tagged_apps_from_config.get(dlc_id) or f"DLC {dlc_id}"
            apps_list.append({"id": dlc_id, "desc": f"[DLC] {desc}", "is_base": False})

        # Build Depots list for THIS GAME ONLY:
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
            "game_keys": game_keys,
        }

    def _update_sync_button_label(state: Dict[str, Any]) -> None:
        """Check if desired states differ from live config state and update Sync button text."""
        is_dirty = False
        for item in state["apps"]:
            aid = item["id"]
            if aid in desired_apps and desired_apps[aid] != (aid in state["live_apps"]):
                is_dirty = True
                break
        if not is_dirty:
            for item in state["depots"]:
                did = item["id"]
                if did in desired_depots and desired_depots[did] != (did in state["live_depots"]):
                    is_dirty = True
                    break
        if not is_dirty:
            for item in state["keys"]:
                kid = item["id"]
                if kid in desired_keys and desired_keys[kid] != item["in_config"]:
                    is_dirty = True
                    break

        if is_dirty:
            sync_btn.setText("Sync Changes")
        else:
            sync_btn.setText("Sync")

    def _update_bulk_states(state: Dict[str, Any]) -> None:
        """Synchronize the checked state of the bulk Select All / None toggles."""
        nonlocal _internal_bulk_update
        _internal_bulk_update = True
        try:
            apps = state["apps"]
            if len(apps) > 5:
                all_apps_on = bool(apps) and all(desired_apps.get(item["id"], False) for item in apps)
                apps_bulk_toggle.setChecked(all_apps_on, animate=False)

            depots = state["depots"]
            if len(depots) > 5:
                all_depots_on = bool(depots) and all(desired_depots.get(item["id"], False) for item in depots)
                depots_bulk_toggle.setChecked(all_depots_on, animate=False)

            keys = state["keys"]
            valid_keys = [k for k in keys if k.get("key")]
            if len(keys) > 5:
                all_keys_on = bool(valid_keys) and all(desired_keys.get(item["id"], False) for item in valid_keys)
                keys_bulk_toggle.setChecked(all_keys_on, animate=False)

            # Master tab toggle (if any category has >5 items)
            if len(apps) > 5 or len(depots) > 5 or len(keys) > 5:
                all_master_on = (
                    (not apps or all(desired_apps.get(item["id"], False) for item in apps))
                    and (not depots or all(desired_depots.get(item["id"], False) for item in depots))
                    and (not valid_keys or all(desired_keys.get(item["id"], False) for item in valid_keys))
                )
                tab_bulk_toggle.setChecked(all_master_on, animate=False)
        finally:
            _internal_bulk_update = False

    def _on_bulk_toggle_apps(checked: bool) -> None:
        if _internal_bulk_update or not is_unlocked:
            return
        state = _collect_state()
        app_to_depots = _build_app_to_depots_map(state["apps"])
        for item in state["apps"]:
            aid = item["id"]
            desired_apps[aid] = checked
            if aid in app_toggles:
                app_toggles[aid].setChecked(checked, animate=True)

            # Cascade disable if turning off
            if not checked:
                if aid in key_toggles and desired_keys.get(aid):
                    desired_keys[aid] = False
                    key_toggles[aid].setChecked(False, animate=True)
                for did in app_to_depots.get(aid, set()):
                    if did in depot_toggles and desired_depots.get(did):
                        desired_depots[did] = False
                        depot_toggles[did].setChecked(False, animate=True)
                    if did in key_toggles and desired_keys.get(did):
                        desired_keys[did] = False
                        key_toggles[did].setChecked(False, animate=True)

        _update_sync_button_label(state)
        _update_bulk_states(state)
        status_lbl.setText(f"All AppIDs {'enabled' if checked else 'disabled (cascaded)'}. Click 'Sync Changes' to apply.")

    def _on_bulk_toggle_depots(checked: bool) -> None:
        if _internal_bulk_update or not is_unlocked:
            return
        state = _collect_state()
        for item in state["depots"]:
            did = item["id"]
            desired_depots[did] = checked
            if did in depot_toggles:
                depot_toggles[did].setChecked(checked, animate=True)

        # STRICTLY ONE-WAY: does not touch AdditionalApps!
        _update_sync_button_label(state)
        _update_bulk_states(state)
        status_lbl.setText(f"All Depots {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply.")

    def _on_bulk_toggle_keys(checked: bool) -> None:
        if _internal_bulk_update or not is_unlocked:
            return
        state = _collect_state()
        for item in state["keys"]:
            kid = item["id"]
            if checked and not item.get("key"):
                continue  # Cannot enable missing keys
            desired_keys[kid] = checked
            if kid in key_toggles:
                key_toggles[kid].setChecked(checked, animate=True)

        # STRICTLY ONE-WAY: does not touch AdditionalApps!
        _update_sync_button_label(state)
        _update_bulk_states(state)
        status_lbl.setText(f"All Keys {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply.")

    def _on_bulk_toggle_all(checked: bool) -> None:
        if _internal_bulk_update or not is_unlocked:
            return
        state = _collect_state()
        # 1. Apps
        for item in state["apps"]:
            aid = item["id"]
            desired_apps[aid] = checked
            if aid in app_toggles:
                app_toggles[aid].setChecked(checked, animate=True)

        # 2. Depots
        for item in state["depots"]:
            did = item["id"]
            desired_depots[did] = checked
            if did in depot_toggles:
                depot_toggles[did].setChecked(checked, animate=True)

        # 3. Keys
        for item in state["keys"]:
            kid = item["id"]
            if checked and not item.get("key"):
                continue  # Cannot enable missing keys
            desired_keys[kid] = checked
            if kid in key_toggles:
                key_toggles[kid].setChecked(checked, animate=True)

        _update_sync_button_label(state)
        _update_bulk_states(state)
        status_lbl.setText(f"All sections {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply.")

    def _refresh_ui():
        """Populate table widgets with gathered data."""
        state = _collect_state()
        live_apps = state["live_apps"]
        live_depots = state["live_depots"]
        game_keys = state["game_keys"]
        app_to_depots = _build_app_to_depots_map(state["apps"])

        # Clear toggle references
        app_toggles.clear()
        depot_toggles.clear()
        key_toggles.clear()

        # When locked, desired states always match live config
        if not is_unlocked:
            desired_apps.clear()
            for item in state["apps"]:
                desired_apps[item["id"]] = item["id"] in live_apps
            desired_depots.clear()
            for item in state["depots"]:
                desired_depots[item["id"]] = item["id"] in live_depots
            desired_keys.clear()
            for item in state["keys"]:
                desired_keys[item["id"]] = item["in_config"]

        # 1. Populate AdditionalApps Table
        apps = state["apps"]
        apps_table.setRowCount(len(apps))
        for row, item in enumerate(apps):
            aid = item["id"]
            is_base = item.get("is_base", False)
            is_live = aid in live_apps
            has_matching_key = (not is_base) and (aid in game_keys)

            id_item = QTableWidgetItem(aid)
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            desc_item = QTableWidgetItem(item["desc"])
            desc_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            # Greyscale DLCs whose AppID matches a depot key
            if has_matching_key:
                id_item.setForeground(QColor("#777777"))
                desc_item.setForeground(QColor("#777777"))
                desc_item.setToolTip("DLC has matching depot key. Decryption is handled via DecryptionKeys.")

            apps_table.setItem(row, 0, id_item)
            apps_table.setItem(row, 1, desc_item)

            current_checked = desired_apps.get(aid, is_live)
            toggle = MiniSwitchToggle(checked=current_checked, read_only=(not is_unlocked))
            app_toggles[aid] = toggle

            def _make_app_toggle_cb(app_id: str):
                def _cb(checked: bool):
                    desired_apps[app_id] = checked

                    # ONE-WAY DYNAMIC CASCADE: Disabling an app/DLC automatically disables its keys & depots
                    if not checked:
                        # 1. Disable corresponding key in DecryptionKeys (if key ID == app_id)
                        if app_id in key_toggles and desired_keys.get(app_id):
                            desired_keys[app_id] = False
                            key_toggles[app_id].setChecked(False, animate=True)

                        # 2. Disable associated DLC depots in AdditionalDepots and their keys in DecryptionKeys
                        assoc_depots = app_to_depots.get(app_id, set())
                        for did in assoc_depots:
                            if did in depot_toggles and desired_depots.get(did):
                                desired_depots[did] = False
                                depot_toggles[did].setChecked(False, animate=True)
                            if did in key_toggles and desired_keys.get(did):
                                desired_keys[did] = False
                                key_toggles[did].setChecked(False, animate=True)

                    _update_sync_button_label(state)
                    _update_bulk_states(state)
                    status_lbl.setText(
                        f"App {app_id} {'enabled' if checked else 'disabled (cascaded to depots/keys)'}. Click 'Sync Changes' to apply."
                    )
                return _cb

            toggle.toggled.connect(_make_app_toggle_cb(aid))
            apps_table.setCellWidget(row, 2, _ToggleContainer(toggle))

        _set_table_height(apps_table, len(apps))

        # Bulk Select All/None for AdditionalApps (>5 items)
        apps_bulk_bar.setVisible(len(apps) > 5)
        apps_bulk_toggle.setReadOnly(not is_unlocked)
        apps_btn_all.setEnabled(is_unlocked)
        apps_btn_none.setEnabled(is_unlocked)

        # 2. Populate AdditionalDepots Table
        depots = state["depots"]
        depots_table.setRowCount(len(depots))
        for row, item in enumerate(depots):
            did = item["id"]
            is_live = did in live_depots

            id_item = QTableWidgetItem(did)
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            desc_item = QTableWidgetItem(item["desc"])
            desc_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            depots_table.setItem(row, 0, id_item)
            depots_table.setItem(row, 1, desc_item)

            current_checked = desired_depots.get(did, is_live)
            toggle = MiniSwitchToggle(checked=current_checked, read_only=(not is_unlocked))
            depot_toggles[did] = toggle

            def _make_depot_toggle_cb(depot_id: str):
                def _cb(checked: bool):
                    desired_depots[depot_id] = checked
                    # STRICTLY ONE-WAY: does not touch AdditionalApps
                    _update_sync_button_label(state)
                    _update_bulk_states(state)
                    status_lbl.setText(f"Depot {depot_id} {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply.")
                return _cb

            toggle.toggled.connect(_make_depot_toggle_cb(did))
            depots_table.setCellWidget(row, 2, _ToggleContainer(toggle))

        _set_table_height(depots_table, len(depots))

        # Bulk Select All/None for AdditionalDepots (>5 items)
        depots_bulk_bar.setVisible(len(depots) > 5)
        depots_bulk_toggle.setReadOnly(not is_unlocked)
        depots_btn_all.setEnabled(is_unlocked)
        depots_btn_none.setEnabled(is_unlocked)

        # 3. Populate DecryptionKeys Table
        keys = state["keys"]
        keys_table.setRowCount(len(keys))
        for row, item in enumerate(keys):
            kid = item["id"]
            id_item = QTableWidgetItem(kid)
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            keys_table.setItem(row, 0, id_item)

            raw_key = item["key"]
            short_key = f"{raw_key[:15]}..." if len(raw_key) > 15 else (raw_key or "N/A")
            key_item = QTableWidgetItem(short_key)
            key_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if raw_key:
                key_item.setToolTip(raw_key)
            keys_table.setItem(row, 1, key_item)

            # Config column (switch toggle)
            is_cfg = item["in_config"]
            current_checked = desired_keys.get(kid, is_cfg)
            toggle = MiniSwitchToggle(checked=current_checked, read_only=(not is_unlocked))
            key_toggles[kid] = toggle

            def _make_key_toggle_cb(key_id: str):
                def _cb(checked: bool):
                    desired_keys[key_id] = checked
                    # STRICTLY ONE-WAY: does not touch AdditionalApps
                    _update_sync_button_label(state)
                    _update_bulk_states(state)
                    status_lbl.setText(f"Key {key_id} {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply.")
                return _cb

            toggle.toggled.connect(_make_key_toggle_cb(kid))
            keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

            # Local column (strictly non-interactive status indicator)
            is_loc = item["in_local"]
            loc_ind = _make_local_indicator(in_local=is_loc)
            keys_table.setCellWidget(row, 3, loc_ind)

        _set_table_height(keys_table, len(keys))

        # Bulk Select All/None for DecryptionKeys (>5 items)
        keys_bulk_bar.setVisible(len(keys) > 5)
        keys_bulk_toggle.setReadOnly(not is_unlocked)
        keys_btn_all.setEnabled(is_unlocked)
        keys_btn_none.setEnabled(is_unlocked)

        # Master Select All/None at bottom of tab (if any category has >5 items)
        has_large = len(apps) > 5 or len(depots) > 5 or len(keys) > 5
        tab_bulk_bar.setVisible(has_large)
        tab_bulk_toggle.setReadOnly(not is_unlocked)
        tab_btn_all.setEnabled(is_unlocked)
        tab_btn_none.setEnabled(is_unlocked)

        _update_sync_button_label(state)
        _update_bulk_states(state)

    def _on_unlock():
        """Unlock custom depot and configuration selection after confirmation dialog."""
        nonlocal is_unlocked
        if not is_unlocked:
            reply = QMessageBox.warning(
                dialog,
                "Advanced Configuration",
                "Modifying depot and configuration entries is intended for advanced users only.\n\n"
                "Excluding required depots or keys may prevent Steam from downloading or launching the game properly.\n\n"
                "Do you want to unlock custom configuration editing?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                is_unlocked = True
                unlock_btn.setText("Unlocked")
                unlock_btn.setStyleSheet(unlock_btn_style_unlocked)
                status_lbl.setText("Custom editing unlocked. Switch toggles Green (enable) or Red (disable), then Sync.")
                _refresh_ui()
        else:
            is_unlocked = False
            unlock_btn.setText("Unlock")
            unlock_btn.setStyleSheet(unlock_btn_style_locked)
            status_lbl.setText("Configuration locked.")
            _refresh_ui()

    def _on_sync():
        """Sync desired additions and removals specifically for this game into config.yaml."""
        state = _collect_state()
        cfg_path = get_user_config_path()
        if not cfg_path.exists():
            status_lbl.setText("Error: config.yaml not found.")
            return

        ensure_plugins_enabled(cfg_path)
        game_name = dialog.game_data.get("game_name") or f"App {appid_str}"

        added_count = 0
        removed_count = 0

        with batch_config_edit(cfg_path) as editor:
            if is_unlocked:
                # Custom unlocked sync: strictly follow user's toggle desired states
                # 1. Apps (add if desired and missing, remove if unchecked and present)
                for item in state["apps"]:
                    aid = item["id"]
                    desired = desired_apps.get(aid, aid in state["live_apps"])
                    if desired and aid not in state["live_apps"]:
                        editor.add_app(aid, comment=item["desc"])
                        added_count += 1
                    elif not desired and aid in state["live_apps"]:
                        editor.remove_app(aid)
                        removed_count += 1

                # 2. Depots (add if desired and missing, remove if unchecked and present)
                for item in state["depots"]:
                    did = item["id"]
                    desired = desired_depots.get(did, did in state["live_depots"])
                    if desired and did not in state["live_depots"]:
                        comment = f"{game_name} ({did})"
                        editor.add_depot(did, comment=comment)
                        added_count += 1
                    elif not desired and did in state["live_depots"]:
                        editor.remove_depot(did, check_shared=True, excluding_appid=appid_str)
                        removed_count += 1

                # 3. Keys (add if desired and missing, remove if unchecked and present)
                for item in state["keys"]:
                    kid = item["id"]
                    desired = desired_keys.get(kid, kid in state["live_keys"])
                    kval = item["key"]
                    if desired and not item["in_config"]:
                        if kval:
                            comment = f"{game_name} [AppKey]" if kid == appid_str else f"{game_name} ({kid})"
                            editor.add_key(kid, kval, comment=comment)
                            added_count += 1
                    elif not desired and item["in_config"]:
                        editor.remove_key(kid, check_shared=True, excluding_appid=appid_str)
                        removed_count += 1
            else:
                # Standard locked sync: automatically add missing entries
                # 1. Base AppID ONLY (DLCs are never auto-added in default sync; user must unlock and manually enable them)
                if appid_str not in state["live_apps"]:
                    editor.add_app(appid_str, comment=f"{game_name} (Base)")
                    added_count += 1

                # 2. Depots: add missing depots that have valid keys
                valid_keys = set(item["id"] for item in state["keys"] if item["key"])
                for item in state["depots"]:
                    did = item["id"]
                    if did not in state["live_depots"] and did in valid_keys:
                        comment = f"{game_name} ({did})"
                        editor.add_depot(did, comment=comment)
                        added_count += 1

                # 3. Keys: add missing keys
                for item in state["keys"]:
                    kid = item["id"]
                    kval = item["key"]
                    if kval and not item["in_config"]:
                        comment = f"{game_name} [AppKey]" if kid == appid_str else f"{game_name} ({kid})"
                        editor.add_key(kid, kval, comment=comment)
                        added_count += 1

        if editor.has_changes:
            SLSBridge.notify_reload()
            parts = []
            if added_count:
                parts.append(f"{added_count} added")
            if removed_count:
                parts.append(f"{removed_count} removed")
            status_lbl.setText(f"Synced changes: {', '.join(parts)}.")
        else:
            status_lbl.setText("Configuration is already up to date.")

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
