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
from utils.lua_parsing import iter_live_matches
from ui.assets import DEPOT_BLACKLIST

logger = logging.getLogger(__name__)

# Unified set of shared redistributable depots across Steam
ALL_SHARED_REDISTS = SHARED_REDISTS | {str(d) for d in DEPOT_BLACKLIST}


class _WorkerBridge(QObject):
    finished = pyqtSignal(bool, str)


class _EnrichmentBridge(QObject):
    enriched = pyqtSignal()


def _format_enriched_depot_desc(depot_id: str, fallback_desc: str, enrich_data: Optional[Dict[str, Any]] = None) -> str:
    """Format depot title with OS, language, DLC badges, and enriched name from Steam PICS / SteamDB."""
    if not enrich_data:
        return fallback_desc

    raw_name = enrich_data.get("name") or ""
    # Strip redundant DLC prefix if present in scraped name
    cleaned_name = re.sub(r"^DLC\s+\d+\s*-?\s*", "", str(raw_name), flags=re.IGNORECASE).strip()

    # Determine base descriptive text
    if cleaned_name and not re.match(r"^(?:Depot|App)\s+\d+$", cleaned_name, re.IGNORECASE):
        base_desc = cleaned_name
    elif fallback_desc and not re.match(r"^Depot\s+\d+$", fallback_desc, re.IGNORECASE):
        base_desc = fallback_desc
    else:
        base_desc = f"Depot {depot_id}"

    oslist = str(enrich_data.get("oslist") or "").lower()
    os_tag = ""
    if oslist == "windows":
        os_tag = "[Windows]"
    elif oslist == "linux":
        os_tag = "[Linux]"
    elif oslist in ("macos", "macosx"):
        os_tag = "[macOS]"
    elif "windows" in oslist and "linux" in oslist:
        os_tag = "[Windows, Linux]"
    elif "all" in oslist:
        os_tag = "[All]"

    lang = str(enrich_data.get("language") or "").strip()
    lang_tag = f"[{lang.capitalize()}]" if lang and lang.lower() not in ("english", "none", "") else ""

    is_dlc = enrich_data.get("is_dlc")
    dlc_id = enrich_data.get("dlcappid")
    dlc_tag = ""
    if is_dlc:
        dlc_tag = f"[DLC {dlc_id}]" if dlc_id and str(dlc_id).isdigit() else "[DLC]"

    tags = []
    base_lower = base_desc.lower()
    if os_tag and os_tag.lower() not in base_lower:
        tags.append(os_tag)
    if lang_tag and lang_tag.lower() not in base_lower:
        tags.append(lang_tag)
    if dlc_tag and dlc_tag.lower() not in base_lower:
        tags.append(dlc_tag)

    if tags:
        return f"{' '.join(tags)}  {base_desc}".strip()
    return base_desc


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
    tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
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
            selection-background-color: transparent;
            selection-color: #FFFFFF;
        }}
        QTableWidget::item {{
            padding: 4px 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            border: none;
        }}
        QTableWidget::item:selected {{
            background-color: rgba(255, 255, 255, 0.06);
            border: none;
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
    try:
        from managers.db_manager import DatabaseManager
        at0m_stat = DatabaseManager().get_at0m_update_status(appid_str)
        if at0m_stat:
            if at0m_stat.get("status") == "update_available":
                new_deps = at0m_stat.get("new_depots", [])
                cnt = len(new_deps) if isinstance(new_deps, list) else 1
                status_lbl.setText(f"★ Update Available: {cnt} new depot key(s) ready on Hubcap! Click 'Refetch' to fetch keys.")
                status_lbl.setStyleSheet("color: #4DD0E1; font-size: 8.5pt; font-weight: bold;")
            elif at0m_stat.get("status") == "key_pending":
                status_lbl.setText("ℹ Steam has updated depots, but keys are pending on Hubcap (cooldown active).")
                status_lbl.setStyleSheet("color: #FFB74D; font-size: 8.5pt;")
    except Exception:
        pass
    layout.addWidget(status_lbl)

    # Persistent Bottom Actions Bar
    btn_bar = QFrame()
    btn_bar.setStyleSheet("background: transparent; border: none;")
    bar_lay = QHBoxLayout(btn_bar)
    bar_lay.setContentsMargins(0, 0, 0, 0)
    bar_lay.setSpacing(8)

    refetch_btn = QPushButton("API Refetch")
    refetch_btn.setFixedHeight(28)
    refetch_btn.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.07);
            color: rgba(255, 255, 255, 0.9);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 4px;
            font-size: 8.5pt;
            font-weight: 500;
            padding: 2px 12px;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.12);
            border-color: rgba(255, 255, 255, 0.22);
            color: #FFFFFF;
        }
        QPushButton:disabled {
            background-color: rgba(255, 255, 255, 0.02);
            color: rgba(255, 255, 255, 0.25);
            border-color: rgba(255, 255, 255, 0.05);
        }
    """)

    unlock_btn = QPushButton("Unlock")
    unlock_btn.setFixedHeight(28)
    unlock_btn_style_locked = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.07);
            color: rgba(255, 255, 255, 0.9);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 4px;
            font-size: 8.5pt;
            font-weight: 500;
            padding: 2px 12px;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.12);
            border-color: rgba(255, 255, 255, 0.22);
            color: #FFFFFF;
        }
    """
    unlock_btn_style_unlocked = """
        QPushButton {
            background-color: rgba(255, 152, 0, 0.18);
            color: #ffb74d;
            border: 1px solid rgba(255, 152, 0, 0.45);
            border-radius: 4px;
            font-size: 8.5pt;
            font-weight: bold;
            padding: 2px 12px;
        }
        QPushButton:hover {
            background-color: rgba(255, 152, 0, 0.26);
        }
    """
    unlock_btn.setStyleSheet(unlock_btn_style_locked)

    sync_btn = QPushButton("Sync")
    sync_btn.setFixedHeight(28)
    sync_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {ac};
            color: #000000;
            border: none;
            border-radius: 4px;
            font-size: 8.5pt;
            font-weight: bold;
            padding: 2px 12px;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.92);
        }}
        QPushButton:disabled {{
            background-color: rgba(255, 255, 255, 0.08);
            color: rgba(255, 255, 255, 0.25);
        }}
    """)

    bar_lay.addWidget(refetch_btn, 1)
    bar_lay.addWidget(unlock_btn, 1)
    bar_lay.addWidget(sync_btn, 1)

    # Non-blocking check of Hubcap token quota (instant cached read + async background refresh)
    try:
        from utils.settings import get_settings
        cached_stats = get_settings().value("last_cached_user_stats", None)
        if isinstance(cached_stats, dict) and cached_stats:
            init_rem = max(0, (cached_stats.get("daily_limit") or 135) - cached_stats.get("daily_usage", 0))
            if init_rem <= 0 or not cached_stats.get("can_make_requests", True):
                refetch_btn.setEnabled(False)
                refetch_btn.setToolTip("Daily Hubcap API token limit reached.")
    except Exception:
        pass

    def _check_hubcap_quota_async():
        try:
            from core import morrenus_api
            from PyQt6.QtCore import QTimer
            st = morrenus_api.get_user_stats()
            if isinstance(st, dict) and st:
                rem = max(0, (st.get("daily_limit") or 135) - st.get("daily_usage", 0))
                if rem <= 0 or not st.get("can_make_requests", True):
                    QTimer.singleShot(0, lambda: (
                        refetch_btn.setEnabled(False),
                        refetch_btn.setToolTip("Daily Hubcap API token limit reached.")
                    ))
        except Exception:
            pass

    threading.Thread(target=_check_hubcap_quota_async, daemon=True).start()

    tab_container = QWidget()
    tab_container.setStyleSheet("background: transparent;")
    tab_layout = QVBoxLayout(tab_container)
    tab_layout.setContentsMargins(0, 0, 0, 0)
    tab_layout.setSpacing(0)

    scroll.setWidget(content_widget)
    tab_layout.addWidget(scroll, 1)

    footer_bar = QFrame()
    footer_bar.setStyleSheet("""
        QFrame {
            background-color: rgba(18, 18, 24, 0.96);
            border-top: 1px solid rgba(255, 255, 255, 0.08);
        }
    """)
    footer_lay = QVBoxLayout(footer_bar)
    footer_lay.setContentsMargins(16, 6, 16, 10)
    footer_lay.setSpacing(6)
    footer_lay.addWidget(status_lbl)
    footer_lay.addWidget(btn_bar)

    tab_layout.addWidget(footer_bar, 0)
    dialog.stacked.addWidget(tab_container)

    def _build_app_to_depots_map(
        state_apps: List[Dict[str, Any]],
        state_depots: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Set[str]]:
        """
        Dynamically map each AppID (base game and DLCs) to its associated depot IDs.
        Used for one-way dynamic cascade toggling across AdditionalApps,
        AdditionalDepots, and DecryptionKeys.
        """
        from utils.dlc_helpers import build_app_to_depots_map
        return build_app_to_depots_map(
            appid_str,
            game_data=dialog.game_data,
            extra_appids=[item["id"] for item in state_apps],
            candidate_depots=[d["id"] for d in state_depots] if state_depots else None,
        )

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
                for m in iter_live_matches(txt, r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)'):
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

        # Also correlate live_keys from config that match this game's depots or AppID
        for d in (set(gd_depots.keys()) | set(lua_depots.keys()) | {appid_str}):
            if d in live_keys and d not in ALL_SHARED_REDISTS:
                k = live_keys[d]
                if k and len(k) == 64:
                    game_keys.setdefault(d, k.lower())
                    if d not in local_db_keys:
                        # Auto-seed into local DB (depot_keys.db)
                        try:
                            dkm.save_depot_keys(appid_str, {d: k.lower()})
                            local_db_keys[d] = k.lower()
                        except Exception as e:
                            logger.debug(f"[DepotsTab] Auto-seed local key failed: {e}")

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
        # Pure license DLCs have no dedicated depot payload or decryption key
        content_dlcs = {d for d in dlc_id_set if (d in game_keys or d in lua_depots or d in gd_depots)}
        pure_license_dlcs = dlc_id_set - content_dlcs

        # Build Depots list for THIS GAME ONLY:
        # Collect any shared redistributables explicitly required/referenced by this game
        all_referenced_depots = (
            set(lua_depots.keys())
            | plugin_depots
            | set(game_keys.keys())
            | tagged_depots_from_config
            | set(gd_depots.keys())
        )
        game_shared_depots = (all_referenced_depots & ALL_SHARED_REDISTS) - {appid_str}

        game_depots_set = (
            set(lua_depots.keys())
            | plugin_depots
            | set(game_keys.keys())
            | tagged_depots_from_config
        ) - ALL_SHARED_REDISTS - {appid_str} - dlc_id_set

        from utils.yaml_config_manager import is_depot_shared_with_other_games
        from managers.db_manager import DatabaseManager
        db_mgr = DatabaseManager()
        cached_enrichments = db_mgr.get_depot_enrichments(appid_str) or {}
        cached_app_info = db_mgr.get_app_info(appid_str) or {}
        cached_app_depots = (cached_app_info.get("depots") or {}) if isinstance(cached_app_info, dict) else {}

        valid_key_depots = set(game_keys.keys())
        depots_list = []
        for d in sorted(game_depots_set, key=lambda x: int(x) if x.isdigit() else 0):
            if d in blacklisted_depots and d not in live_depots and d not in valid_key_depots:
                continue  # Blacklisted without keys
            desc = (
                plugin_depot_names.get(d)
                or lua_depots.get(d)
                or (gd_depots.get(d, {}).get("desc") if isinstance(gd_depots.get(d), dict) else "")
                or (f"[DLC] {lua_dlcs.get(d)}" if d in lua_dlcs else None)
                or f"Depot {d}"
            )
            enrich_info = cached_enrichments.get(d) or cached_app_depots.get(d)
            enriched_desc = _format_enriched_depot_desc(d, desc, enrich_info)

            size_str = ""
            if enrich_info:
                size_str = enrich_info.get("size_str") or ""
                if not size_str and enrich_info.get("size_bytes"):
                    try:
                        from utils.helpers import format_bytes
                        size_str = format_bytes(enrich_info["size_bytes"])
                    except Exception:
                        size_str = str(enrich_info["size_bytes"])
                elif not size_str and enrich_info.get("size") and str(enrich_info["size"]).isdigit():
                    try:
                        from utils.helpers import format_bytes
                        size_str = format_bytes(int(enrich_info["size"]))
                    except Exception:
                        size_str = str(enrich_info["size"])

            is_shared = is_depot_shared_with_other_games(d, excluding_appid=appid_str)
            depots_list.append({
                "id": d,
                "desc": enriched_desc,
                "raw_desc": desc,
                "has_key": (d in valid_key_depots),
                "is_shared": is_shared,
                "size_str": size_str,
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

        all_target_key_ids = (set(game_depots_set) | set(game_keys.keys()) | set(tagged_keys_from_config.keys())) - {appid_str} - ALL_SHARED_REDISTS
        for d in sorted(all_target_key_ids, key=lambda x: int(x) if x.isdigit() else 0):
            key_val = game_keys.get(d) or (live_keys.get(d) if d in tagged_keys_from_config else "")
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
            "game_name": game_name,
            "apps": apps_list,
            "depots": depots_list,
            "keys": keys_list,
            "live_apps": live_apps,
            "live_depots": live_depots,
            "live_keys": live_keys,
            "local_db_keys": local_db_keys,
            "lua_keys": lua_keys,
            "game_keys": game_keys,
            "game_shared_depots": game_shared_depots,
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
        app_to_depots = _build_app_to_depots_map(state["apps"], state.get("depots"))
        for item in state["apps"]:
            aid = item["id"]
            desired_apps[aid] = checked
            if aid in app_toggles:
                app_toggles[aid].setChecked(checked, animate=True)

            assoc_depots = app_to_depots.get(aid, set())
            if checked:
                # Auto-enable matching key in DecryptionKeys if valid key exists
                if aid in key_toggles and not desired_keys.get(aid, False):
                    key_meta = next((k for k in state["keys"] if k["id"] == aid), None)
                    if key_meta and key_meta.get("key"):
                        desired_keys[aid] = True
                        key_toggles[aid].setChecked(True, animate=True)

                # Auto-enable associated DLC depots and their keys
                for did in assoc_depots:
                    if did in depot_toggles and not desired_depots.get(did, False):
                        desired_depots[did] = True
                        depot_toggles[did].setChecked(True, animate=True)
                    if did in key_toggles and not desired_keys.get(did, False):
                        d_key_meta = next((k for k in state["keys"] if k["id"] == did), None)
                        if d_key_meta and d_key_meta.get("key"):
                            desired_keys[did] = True
                            key_toggles[did].setChecked(True, animate=True)
            else:
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
        status_lbl.setText(f"All AppIDs {'enabled (cascaded)' if checked else 'disabled (cascaded)'}. Click 'Sync Changes' to apply.")

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
        game_name = state.get("game_name") or dialog.game_data.get("game_name") or f"App {appid_str}"
        live_apps = state["live_apps"]
        live_depots = state["live_depots"]
        game_keys = state["game_keys"]
        app_to_depots = _build_app_to_depots_map(state["apps"], state.get("depots"))

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

                    # ONE-WAY DYNAMIC CASCADE:
                    # Enabling an app/DLC automatically enables its matching key & depots
                    # Disabling an app/DLC automatically disables its matching key & depots
                    assoc_depots = app_to_depots.get(app_id, set())

                    if checked:
                        # 1. Enable corresponding key in DecryptionKeys (if key exists)
                        if app_id in key_toggles and not desired_keys.get(app_id, False):
                            key_meta = next((k for k in state["keys"] if k["id"] == app_id), None)
                            if key_meta and key_meta.get("key"):
                                desired_keys[app_id] = True
                                key_toggles[app_id].setChecked(True, animate=True)

                        # 2. Enable associated DLC depots in AdditionalDepots and their keys in DecryptionKeys
                        for did in assoc_depots:
                            if did in depot_toggles and not desired_depots.get(did, False):
                                desired_depots[did] = True
                                depot_toggles[did].setChecked(True, animate=True)
                            if did in key_toggles and not desired_keys.get(did, False):
                                d_key_meta = next((k for k in state["keys"] if k["id"] == did), None)
                                if d_key_meta and d_key_meta.get("key"):
                                    desired_keys[did] = True
                                    key_toggles[did].setChecked(True, animate=True)
                    else:
                        # 1. Disable corresponding key in DecryptionKeys (if key ID == app_id)
                        if app_id in key_toggles and desired_keys.get(app_id):
                            desired_keys[app_id] = False
                            key_toggles[app_id].setChecked(False, animate=True)

                        # 2. Disable associated DLC depots in AdditionalDepots and their keys in DecryptionKeys
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
                        f"App {app_id} {'enabled (cascaded to depots/keys)' if checked else 'disabled (cascaded to depots/keys)'}. Click 'Sync Changes' to apply."
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

            desc_text = item["desc"]
            if item.get("is_shared"):
                desc_text = f"{desc_text} [Shared]"
            desc_item = QTableWidgetItem(desc_text)
            desc_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            tooltip_parts = [desc_text]
            if item.get("size_str"):
                tooltip_parts.append(f"Size: {item['size_str']}")
            if item.get("is_shared"):
                tooltip_parts.append("Shared with another game / protected from removal")
            desc_item.setToolTip("\n".join(tooltip_parts))

            depots_table.setItem(row, 0, id_item)
            depots_table.setItem(row, 1, desc_item)

            current_checked = desired_depots.get(did, is_live)
            toggle = MiniSwitchToggle(checked=current_checked, read_only=(not is_unlocked))
            depot_toggles[did] = toggle

            def _make_depot_toggle_cb(depot_id: str, is_sh: bool):
                def _cb(checked: bool):
                    desired_depots[depot_id] = checked
                    # STRICTLY ONE-WAY: does not touch AdditionalApps
                    _update_sync_button_label(state)
                    _update_bulk_states(state)
                    if not checked and is_sh:
                        status_lbl.setText(f"Depot {depot_id} is shared with other games. It will remain protected in config.")
                    else:
                        status_lbl.setText(f"Depot {depot_id} {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply.")
                return _cb

            toggle.toggled.connect(_make_depot_toggle_cb(did, bool(item.get("is_shared"))))
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
            # Add depot/app description tooltip for quick identification
            matching_desc = next((d["desc"] for d in state["depots"] if d["id"] == kid), None)
            if not matching_desc and (kid == appid_str or item.get("is_appkey")):
                matching_desc = f"{game_name} (AppKey)"
            elif not matching_desc:
                matching_app = next((a["desc"] for a in state.get("apps", []) if a["id"] == kid), None)
                if matching_app:
                    matching_desc = f"{matching_app} (Key)"
            if matching_desc:
                id_item.setToolTip(matching_desc)
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

                # Cleanup safeguard: if base appid was accidentally in live AdditionalDepots, prune it
                if appid_str in state["live_depots"]:
                    editor.remove_depot(appid_str, check_shared=False)
                    removed_count += 1

                # 2. Depots (add if desired and missing, remove if unchecked and present)
                for item in state["depots"]:
                    did = item["id"]
                    if did == appid_str:
                        continue  # Safeguard: AppIDs must never be in AdditionalDepots
                    desired = desired_depots.get(did, did in state["live_depots"])
                    if desired and did not in state["live_depots"]:
                        comment = f"{game_name} ({did})"
                        editor.add_depot(did, comment=comment, app_id=appid_str)
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
                # Cleanup safeguard: if base appid was accidentally in live AdditionalDepots, prune it
                if appid_str in state["live_depots"]:
                    editor.remove_depot(appid_str, check_shared=False)
                    removed_count += 1

                # 1. Base AppID ONLY (DLCs are never auto-added in default sync; user must unlock and manually enable them)
                from utils.dlc_helpers import is_dlc_only_mode
                if not is_dlc_only_mode(appid_str) and appid_str not in state["live_apps"]:
                    editor.add_app(appid_str, comment=f"{game_name} (Base)")
                    added_count += 1

                # 2. Depots: add missing depots that have valid keys
                valid_keys = set(item["id"] for item in state["keys"] if item["key"])
                for item in state["depots"]:
                    did = item["id"]
                    if did == appid_str:
                        continue  # Safeguard: AppIDs must never be in AdditionalDepots
                    if did not in state["live_depots"] and did in valid_keys:
                        comment = f"{game_name} ({did})"
                        editor.add_depot(did, comment=comment, app_id=appid_str)
                        added_count += 1

                # 3. Keys: add missing keys
                for item in state["keys"]:
                    kid = item["id"]
                    kval = item["key"]
                    if kval and not item["in_config"]:
                        comment = f"{game_name} [AppKey]" if kid == appid_str else f"{game_name} ({kid})"
                        editor.add_key(kid, kval, comment=comment)
                        added_count += 1

            # Always ensure any shared redistributables required by this game are enabled
            for s_did in sorted(state.get("game_shared_depots", set())):
                if s_did not in state["live_depots"]:
                    editor.add_depot(s_did, comment="Steamworks Shared")
                    added_count += 1
                s_key = (
                    state.get("lua_keys", {}).get(s_did)
                    or state.get("game_keys", {}).get(s_did)
                    or state.get("local_db_keys", {}).get(s_did)
                )
                if s_key and s_did not in state["live_keys"]:
                    editor.add_key(s_did, s_key, comment="Steamworks Shared")
                    added_count += 1

        # Always persist all valid keys from state/config to DepotKeyManager (local DB)
        try:
            from managers.depot_key_manager import DepotKeyManager
            keys_to_persist = {}
            for item in state.get("keys", []):
                kid = item["id"]
                kval = item.get("key") or state.get("live_keys", {}).get(kid)
                if kval and len(str(kval)) == 64:
                    keys_to_persist[kid] = str(kval).lower()
            if keys_to_persist:
                DepotKeyManager.get_instance().save_depot_keys(appid_str, keys_to_persist)
        except Exception as e:
            logger.debug(f"[DepotsTab] Error persisting keys to local DB on sync: {e}")

        if editor.has_changes:
            # Keep plugin_library.json in sync with live depots and keys for this game
            try:
                from utils.plugin_games import load_plugin_library, save_plugin_library
                plib = load_plugin_library()
                if appid_str in plib:
                    live_cfg_depots = set(get_additional_depots(cfg_path))
                    live_cfg_keys = get_decryption_keys(cfg_path)
                    # Retain any active depots present in config
                    existing_active = [d for d in plib[appid_str].get("depots", []) if d in live_cfg_depots]
                    toggled_active = [d["id"] for d in state.get("depots", []) if d["id"] in live_cfg_depots]
                    updated_rec_depots = sorted(list(set(existing_active) | set(toggled_active)))

                    updated_rec_keys = dict(plib[appid_str].get("keys", {}))
                    for k in state.get("keys", []):
                        kid = k["id"]
                        if kid in live_cfg_keys:
                            kval = k.get("key") or live_cfg_keys.get(kid)
                            if kval:
                                updated_rec_keys[kid] = str(kval).lower()
                        else:
                            updated_rec_keys.pop(kid, None)

                    plib[appid_str]["depots"] = updated_rec_depots
                    plib[appid_str]["keys"] = updated_rec_keys
                    save_plugin_library(plib)
            except Exception as e:
                logger.debug(f"[DepotsTab] Error updating plugin library on sync: {e}")

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
        from core import morrenus_api
        from PyQt6.QtWidgets import QMessageBox

        stats = morrenus_api.get_user_stats()
        daily_limit = stats.get("daily_limit") or stats.get("role_daily_limit") or 135
        daily_usage = stats.get("daily_usage", 0)
        can_make_requests = stats.get("can_make_requests", True)
        remaining = max(0, daily_limit - daily_usage)

        if not can_make_requests or remaining <= 0:
            QMessageBox.warning(
                dialog,
                "API Token Limit Reached",
                f"Daily Hubcap API token limit reached (0/{daily_limit} tokens remaining).\n\n"
                f"Please wait for your daily quota to reset, or change your ISP bypass settings in Settings.",
            )
            refetch_btn.setEnabled(False)
            status_lbl.setText(f"API token limit reached (0/{daily_limit}). Please wait for daily reset.")
            return

        reply = QMessageBox.question(
            dialog,
            "Confirm API Refetch",
            f"This action will consume your daily Hubcap API tokens.\n\n"
            f"You have {remaining}/{daily_limit} tokens remaining.\n\n"
            f"Do you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        refetch_btn.setEnabled(False)
        unlock_btn.setEnabled(False)
        sync_btn.setEnabled(False)
        status_lbl.setText("Refetching metadata from Hubcap...")

        bridge = _WorkerBridge()

        def _worker():
            try:
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
                            for m in iter_live_matches(lua_data, r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)'):
                                fresh_keys[m.group(1)] = m.group(2).lower()
                            if fresh_keys:
                                from managers.depot_key_manager import DepotKeyManager
                                DepotKeyManager.get_instance().save_depot_keys(appid_str, fresh_keys)

                                # Sync fresh keys and depots to config.yaml and plugin library
                                try:
                                    from utils.yaml_config_manager import (
                                        get_user_config_path,
                                        batch_config_edit,
                                        SHARED_REDISTS,
                                    )
                                    from utils.sls_bridge import SLSBridge
                                    from utils.plugin_games import load_plugin_library, save_plugin_library

                                    cfg_path = get_user_config_path()
                                    lib = load_plugin_library()
                                    rec = lib.get(appid_str)
                                    is_dlc_only = rec.get("dlc_only", False) if rec else False
                                    game_name = dialog.game_data.get("game_name") or f"App {appid_str}"

                                    if cfg_path and cfg_path.exists():
                                        with batch_config_edit(cfg_path) as editor:
                                            for did, k in fresh_keys.items():
                                                if did not in SHARED_REDISTS:
                                                    if did != appid_str and not is_dlc_only:
                                                        editor.add_depot(did, comment=f"{game_name} ({did})", app_id=appid_str)
                                                    key_comment = f"{game_name} [AppKey]" if did == appid_str else f"{game_name} ({did})"
                                                    editor.add_key(did, k, comment=key_comment)
                                        if editor.has_changes:
                                            SLSBridge.notify_reload()

                                    if rec:
                                        rec.setdefault("keys", {}).update(fresh_keys)
                                        save_plugin_library(lib)
                                except Exception as sync_err:
                                    logger.debug(f"[DepotsTab] Error syncing refetched keys to config: {sync_err}")

                    try:
                        from managers.db_manager import DatabaseManager
                        DatabaseManager().save_at0m_update_status(appid=appid_str, status="up_to_date", new_depots=[])
                    except Exception:
                        pass

                    bridge.finished.emit(True, "Refetch successful. Local cache and configuration updated.")
                else:
                    err_str = str(err or "")
                    if "404" in err_str or "not found" in err_str.lower():
                        bridge.finished.emit(False, "Hubcap returned 404 (Manifest not found on server).")
                    elif "429" in err_str or "limit" in err_str.lower():
                        bridge.finished.emit(False, f"Daily API token limit exceeded ({err}).")
                    else:
                        bridge.finished.emit(False, f"Refetch failed: {err_str or 'Unknown error'}")
            except Exception as e:
                err_str = str(e)
                if "404" in err_str or "not found" in err_str.lower():
                    bridge.finished.emit(False, "Hubcap returned 404 (Manifest not found).")
                else:
                    bridge.finished.emit(False, f"Refetch error: {e}")

        def _on_done(success: bool, msg: str):
            refetch_btn.setEnabled(True)
            unlock_btn.setEnabled(True)
            sync_btn.setEnabled(True)
            status_lbl.setText(msg)
            try:
                st = morrenus_api.get_user_stats(force=True)
                rem = max(0, (st.get("daily_limit") or 135) - st.get("daily_usage", 0))
                if rem <= 0 or not st.get("can_make_requests", True):
                    refetch_btn.setEnabled(False)
                    refetch_btn.setToolTip("API token limit reached.")
            except Exception:
                pass
            _refresh_ui()

        bridge.finished.connect(_on_done)
        threading.Thread(target=_worker, daemon=True).start()

    refetch_btn.clicked.connect(_on_refetch)
    unlock_btn.clicked.connect(_on_unlock)
    sync_btn.clicked.connect(_on_sync)

    enrichment_bridge = _EnrichmentBridge()
    enrichment_bridge.enriched.connect(_refresh_ui)

    def _run_background_enrichment():
        try:
            from core.steam_api import get_depot_info_from_api
            from core.steamdb_scraper import SteamDBScraper, ByparrManager
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            new_enrichments: Dict[str, dict] = {}

            # 1. Query SteamCMD / Steam PICS
            api_info = get_depot_info_from_api(int(appid_str) if appid_str.isdigit() else appid_str)
            if api_info and isinstance(api_info, dict) and api_info.get("depots"):
                for did, ddata in api_info["depots"].items():
                    if isinstance(ddata, dict):
                        new_enrichments[str(did)] = {
                            "depot_id": str(did),
                            "name": ddata.get("name") or "",
                            "oslist": ddata.get("oslist") or "",
                            "language": ddata.get("language") or "",
                            "is_dlc": bool(ddata.get("is_dlc")),
                            "dlcappid": ddata.get("dlcappid") or "",
                            "size_str": "",
                            "size_bytes": ddata.get("size") or 0,
                            "dl_str": "",
                        }

            # 2. Query SteamDB if Byparr is active
            if ByparrManager.is_running():
                try:
                    scraper = SteamDBScraper()
                    sdb_depots = scraper.get_app_depots(appid_str)
                    if sdb_depots:
                        for did, sdata in sdb_depots.items():
                            if did in new_enrichments:
                                if sdata.get("name"):
                                    new_enrichments[did]["name"] = sdata["name"]
                                if sdata.get("oslist"):
                                    new_enrichments[did]["oslist"] = sdata["oslist"]
                                if sdata.get("size_str"):
                                    new_enrichments[did]["size_str"] = sdata["size_str"]
                            else:
                                new_enrichments[did] = sdata
                except Exception as e:
                    logger.debug(f"[DepotsTab] SteamDB enrichment failed: {e}")

            if new_enrichments:
                db.save_depot_enrichments(appid_str, new_enrichments)
                enrichment_bridge.enriched.emit()
        except Exception as e:
            logger.debug(f"[DepotsTab] Background depot enrichment error: {e}")

    # Initial load
    _refresh_ui()

    # Check if any depots are still generic and trigger async enrichment if needed
    initial_state = _collect_state()
    needs_enrichment = any(
        bool(re.match(r"^(?:\[.*?\]\s*)?Depot\s+\d+$", item.get("desc", ""), re.IGNORECASE))
        for item in initial_state.get("depots", [])
    )
    if needs_enrichment:
        threading.Thread(target=_run_background_enrichment, daemon=True).start()
