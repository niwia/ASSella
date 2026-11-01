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

from PyQt6.QtCore import Qt, QObject, pyqtSignal, pyqtProperty, QPropertyAnimation, QEasingCurve, QSize
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
)
from ui.dialogs.themed_message_box import ThemedMessageBox

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
from utils.plugin_games import (
    SHARED_REDISTS,
    build_depot_comment,
    build_key_comment,
    comment_has_owner_tag,
    load_plugin_library,
)
from utils.lua_parsing import iter_live_matches
from utils.release_gate import ReleaseGate, STATUS_UNRELEASED, UNRELEASED_TAG, unreleased_tooltip
from ui.assets import DEPOT_BLACKLIST

logger = logging.getLogger(__name__)

# Unified set of shared redistributable depots across Steam
ALL_SHARED_REDISTS = SHARED_REDISTS | {str(d) for d in DEPOT_BLACKLIST}


class _WorkerBridge(QObject):
    finished = pyqtSignal(bool, str)


class _EnrichmentBridge(QObject):
    enriched = pyqtSignal()


def _format_enriched_depot_desc(
    depot_id: str,
    fallback_desc: str,
    enrich_data: Optional[Dict[str, Any]] = None,
    has_os_icon: bool = False,
    base_appid: Optional[str] = None,
) -> str:
    """Format depot title with OS, architecture, language, DLC badges, and clean name using format_depot_row_label."""
    from utils.depot_tag_helpers import format_depot_row_label
    return format_depot_row_label(
        depot_id=str(depot_id),
        depot_data=enrich_data or {},
        base_desc=fallback_desc,
        has_os_icon=has_os_icon,
        show_os_badge=(not has_os_icon),
        base_appid=base_appid,
    )


class MiniSwitchToggle(QWidget):
    """
    Minimal sideways switch toggle widget.
    Turns Green (#4CAF50) when checked (enabled) and Red (#E53935) when unchecked (disabled).
    Knob slides smoothly sideways with cubic easing.
    In read-only mode (locked), user interaction is disabled.
    """
    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, read_only: bool = True, greyscaled: bool = False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._read_only = read_only or greyscaled
        self._greyscaled = greyscaled
        self._hovered = False
        self.setFixedSize(36, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor if not self._read_only else Qt.CursorShape.ArrowCursor)

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
        self._read_only = read_only or self._greyscaled
        self.setCursor(Qt.CursorShape.PointingHandCursor if not self._read_only else Qt.CursorShape.ArrowCursor)
        self.update()

    def isReadOnly(self) -> bool:
        return self._read_only

    def setGreyscaled(self, greyscaled: bool) -> None:
        self._greyscaled = greyscaled
        if greyscaled:
            self._read_only = True
        self.setCursor(Qt.CursorShape.ArrowCursor if self._read_only else Qt.CursorShape.PointingHandCursor)
        self.update()

    def isGreyscaled(self) -> bool:
        return self._greyscaled

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
            if not self._read_only and not self._greyscaled:
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

        if self._greyscaled:
            track_color = QColor(255, 255, 255, 30)
            knob_color = QColor(135, 135, 135)
        else:
            # Background track: Green if checked, Red if unchecked
            track_color = QColor("#4CAF50") if self._checked else QColor("#E53935")
            if self._read_only:
                track_color.setAlpha(190)
            else:
                track_color.setAlpha(255)
            knob_color = QColor("#FFFFFF")

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(track_color))
        p.drawRoundedRect(0, 0, w, h, radius, radius)

        # Knob circle
        knob_d = 14.0
        knob_y = (h - knob_d) / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(knob_color))
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
        if event.button() == Qt.MouseButton.LeftButton and not self.toggle.isReadOnly() and not self.toggle.isGreyscaled():
            self.toggle.setChecked(not self.toggle.isChecked())
            event.accept()
        else:
            super().mousePressEvent(event)


def _make_local_indicator(in_local: bool, is_missing: bool = False) -> QWidget:
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
        dot.setToolTip("Decryption key present in local database")
        h_lay.addWidget(dot)
    elif is_missing:
        lbl = QLabel("—")
        lbl.setStyleSheet("color: rgba(255, 255, 255, 0.25); font-weight: bold; font-size: 11px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setToolTip("Decryption key not available locally or on Hubcap")
        h_lay.addWidget(lbl)
    else:
        empty = QLabel("")
        h_lay.addWidget(empty)

    return container


def _make_table(columns: List[str], accent_color: str) -> QTableWidget:
    """Create a styled table widget matching ASSella design conventions."""
    tbl = QTableWidget()
    tbl.setIconSize(QSize(18, 18))
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
            color: #FFFFFF;
            padding: 4px 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            border: none;
        }}
        QTableWidget::item:disabled {{
            color: rgba(255, 255, 255, 0.38);
            background-color: transparent;
        }}
        QTableWidget::item:selected {{
            background-color: rgba(255, 255, 255, 0.06);
            border: none;
            color: #FFFFFF;
        }}
        QToolTip {{
            background-color: #1e2029;
            color: #FFFFFF;
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 4px;
            padding: 5px 8px;
            font-size: 8.5pt;
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


def _make_bulk_toggle_bar(category_name: str, on_toggle_cb) -> Tuple[QFrame, MiniSwitchToggle]:
    """Create a sleek footer bar with a Select All / None switch for categories with >=5 items."""
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
    return bar, toggle


def init_depots_tab(dialog) -> None:
    """Initialize the modular Depots tab for AT0-M mode games."""
    dialog.stacked.addWidget(build_depots_panel(dialog))


def build_depots_panel(dialog, mode: str = "details", on_registered=None) -> QWidget:
    """The AdditionalApps / AdditionalDepots / DecryptionKeys editor.

    ``mode="details"``: the Game Details Depots tab (locked until Unlock).
    ``mode="dlc_setup"``: AT0-M DLC setup for a Steam-owned game. Opens
    unlocked with released, unowned DLCs (and their depots/keys) preselected;
    Steam-owned and unreleased items are greyed and locked. The button
    registers the selection (config + plugin library) and calls
    ``on_registered(summary)``.

    ``dialog`` only needs ``appid``, ``game_data`` and ``accent_color``.
    """
    is_dlc_setup = (mode == "dlc_setup")
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

    # Advanced unlock and desired states tracking (DLC setup starts unlocked)
    is_unlocked = is_dlc_setup
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

    apps_bulk_bar, apps_bulk_toggle = _make_bulk_toggle_bar(
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

    depots_bulk_bar, depots_bulk_toggle = _make_bulk_toggle_bar(
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

    keys_bulk_bar, keys_bulk_toggle = _make_bulk_toggle_bar(
        "Keys", lambda chk: _on_bulk_toggle_keys(chk)
    )
    layout.addWidget(keys_bulk_bar)


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
                ready_ids = [str(d.get("id")) for d in new_deps if isinstance(d, dict) and d.get("has_key")]
                ready_str = f" ({', '.join(ready_ids)})" if ready_ids else ""
                status_lbl.setText(f"★ Update Available: {cnt} new depot key(s) ready on Hubcap{ready_str}! Click 'API Refetch' to fetch keys.")
                status_lbl.setStyleSheet("color: #4DD0E1; font-size: 8.5pt; font-weight: bold;")
            elif at0m_stat.get("status") == "key_pending":
                new_deps = at0m_stat.get("new_depots", [])
                pending_ids = [str(d.get("id")) for d in new_deps if isinstance(d, dict) and not d.get("has_key")]
                pending_str = f" for depot(s) {', '.join(pending_ids)}" if pending_ids else ""
                status_lbl.setText(f"ℹ Steam has updated depots, but keys are pending on Hubcap{pending_str} (cooldown active).")
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
            from utils.main_thread import post_to_main_thread
            st = morrenus_api.get_user_stats()
            if isinstance(st, dict) and st:
                rem = max(0, (st.get("daily_limit") or 135) - st.get("daily_usage", 0))
                if rem <= 0 or not st.get("can_make_requests", True):
                    post_to_main_thread(lambda: (
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
        gd_dlcs_raw = gd.get("dlcs") or {}
        if isinstance(gd_dlcs_raw, dict):
            gd_dlcs = {str(k): str(v) for k, v in gd_dlcs_raw.items()}
        elif isinstance(gd_dlcs_raw, (list, set)):
            gd_dlcs = {str(d): f"DLC {d}" for d in gd_dlcs_raw}
        else:
            gd_dlcs = {}
        for dlc_id, dlc_desc in gd_dlcs.items():
            lua_dlcs.setdefault(dlc_id, dlc_desc)

        gd_depots_raw = gd.get("depots") or {}
        if isinstance(gd_depots_raw, dict):
            gd_depots = {str(k): (v if isinstance(v, dict) else {"desc": str(v)}) for k, v in gd_depots_raw.items()}
        elif isinstance(gd_depots_raw, (list, set)):
            gd_depots = {str(d): {"desc": f"Depot {d}"} for d in gd_depots_raw}
        else:
            gd_depots = {}

        for d_str, dinfo in gd_depots.items():
            desc = dinfo.get("desc") or dinfo.get("name") or ""
            lua_depots.setdefault(d_str, desc)
            k = dinfo.get("key") or dinfo.get("decryption_key")
            if k and len(str(k)) == 64:
                lua_keys.setdefault(d_str, str(k).lower())

        gd_keys = gd.get("keys") or {}
        if isinstance(gd_keys, dict):
            for did, k in gd_keys.items():
                if k and len(str(k)) == 64:
                    lua_keys.setdefault(str(did), str(k).lower())

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
                            if (game_name and game_name.lower() in comment.lower()) or comment_has_owner_tag(comment, appid_str):
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
                            if (game_name and game_name.lower() in comment.lower()) or comment_has_owner_tag(comment, appid_str):
                                tagged_depots_from_config.add(did)

                key_bounds = _get_section_bounds(cfg_text, "DecryptionKeys")
                if key_bounds:
                    sec_key = cfg_text[key_bounds[1]:key_bounds[2]]
                    for line in sec_key.splitlines():
                        m = re.match(r"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*['\"]?([a-fA-F0-9]{64})['\"]?[ \t]*(?:#[ \t]*(.*))?$", line)
                        if m:
                            did, kval, comment = m.group(1), m.group(2), (m.group(3) or "")
                            if (game_name and game_name.lower() in comment.lower()) or comment_has_owner_tag(comment, appid_str):
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

        from utils.yaml_config_manager import is_depot_shared_with_other_games
        from managers.db_manager import DatabaseManager
        from core.steam_helpers import get_owned_steam_appids
        from utils.dlc_helpers import is_dlc_only_mode

        db_mgr = DatabaseManager()
        cached_enrichments = db_mgr.get_depot_enrichments(appid_str) or {}
        cached_app_info = db_mgr.get_app_info(appid_str) or {}
        cached_app_depots = (cached_app_info.get("depots") or {}) if isinstance(cached_app_info, dict) else {}

        # Gate 1 (Steam release state) and gate 2 (Hubcap availability) live in one place.
        gate = ReleaseGate(appid_str, enrichments=cached_enrichments, app_depots=cached_app_depots, db=db_mgr)

        is_steam_owned_base = (appid_str in get_owned_steam_appids())
        try:
            from utils.dlc_helpers import get_owned_dlcs_from_steam
            steam_owned_dlcs = {str(x) for x in (get_owned_dlcs_from_steam(appid_str) or set())}
        except Exception as e:
            logger.debug(f"[DepotsTab] Could not read Steam DLC ownership: {e}")
            steam_owned_dlcs = set()
        is_dlc_only = bool(dialog.game_data.get("is_dlc_only") or dialog.game_data.get("_dlc_only") or is_dlc_only_mode(appid_str))
        is_base_owned = bool(is_steam_owned_base or is_dlc_only)

        # DLC AppIDs set (combining Lua, plugin library, and tagged AdditionalApps from config)
        dlc_id_set = set(lua_dlcs.keys()) | plugin_dlcs | set(tagged_apps_from_config.keys())
        dlc_id_set.discard(appid_str)

        # Build AppIDs list: Base AppID + DLC AppIDs
        base_desc = f"{game_name} [Steam Owned]" if is_base_owned else f"{game_name} (Base)"
        apps_list = [{
            "id": appid_str,
            "desc": base_desc,
            "is_base": True,
            "is_owned_base": is_base_owned,
            "is_unreleased": False,
        }]
        for dlc_id in sorted(dlc_id_set, key=lambda x: int(x) if x.isdigit() else 0):
            desc = lua_dlcs.get(dlc_id) or gd_dlcs.get(dlc_id) or tagged_apps_from_config.get(dlc_id) or f"DLC {dlc_id}"
            is_unrel = gate.is_unreleased(dlc_id, is_dlc_app=True)
            prefix = f"{UNRELEASED_TAG} [DLC]" if is_unrel else "[DLC]"
            is_owned_dlc = dlc_id in steam_owned_dlcs
            if is_owned_dlc:
                prefix = "[Steam Owned] [DLC]"
            apps_list.append({
                "id": dlc_id,
                "desc": f"{prefix} {desc}",
                "is_base": False,
                "is_owned_base": False,
                "is_owned": is_owned_dlc,
                "is_unreleased": is_unrel,
            })

        # Build Depots list for THIS GAME ONLY:
        # Pure license DLCs have no dedicated depot payload or decryption key
        content_dlcs = {d for d in dlc_id_set if (d in game_keys or d in lua_depots or d in gd_depots)}
        pure_license_dlcs = dlc_id_set - content_dlcs
        dlc_content_depots = set(dlc_id_set)
        for depot_id, depot_info in gd_depots.items():
            if isinstance(depot_info, dict) and str(depot_info.get("dlcappid") or "").strip() in dlc_id_set:
                dlc_content_depots.add(depot_id)

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
            | set(gd_depots.keys())
        ) - ALL_SHARED_REDISTS - {appid_str} - pure_license_dlcs
        # Depots the caller reports as missing from Hubcap (and that have no key
        # anywhere) are listed through the missing/status path, never as normal rows.
        reported_missing = {str(x) for x in (gd.get("missing_depots_from_hubcap") or [])} - set(game_keys.keys())
        game_depots_set -= reported_missing

        # Gate 1 cascade: an unreleased DLC takes its mapped content depots (and
        # therefore their keys) down with it, plus anything Steam itself marks prerelease.
        unreleased_dlc_ids = {a["id"] for a in apps_list if a.get("is_unreleased")}
        unreleased_ids: Set[str] = set(unreleased_dlc_ids)
        if unreleased_dlc_ids:
            candidate_ids = sorted(game_depots_set | set(cached_app_depots.keys()))
            mapped = _build_app_to_depots_map(apps_list, [{"id": str(c)} for c in candidate_ids])
            for dlc_id in unreleased_dlc_ids:
                unreleased_ids |= {str(x) for x in mapped.get(dlc_id, set())}

        # Steam-owned content: owned DLC apps and the depots mapped to them, and,
        # when the base game is owned (or DLC-only), the base game's own depots.
        owned_dlc_ids = {a["id"] for a in apps_list if a.get("is_owned")}
        owned_ids: Set[str] = set(owned_dlc_ids)
        _candidates = sorted(game_depots_set | set(cached_app_depots.keys()))
        _owner_map = _build_app_to_depots_map(apps_list, [{"id": str(c)} for c in _candidates])
        _depot_owner: Dict[str, str] = {}
        for _aid, _dset in _owner_map.items():
            if _aid != appid_str:
                for _d in _dset:
                    _depot_owner.setdefault(str(_d), _aid)
        for _d, _info in gd_depots.items():
            _o = str((_info or {}).get("dlcappid") or "").strip()
            if _o and _o != appid_str:
                _depot_owner[_d] = _o
        for _d in _candidates:
            _o = gate.owner_dlc(_d)
            if _o:
                _depot_owner.setdefault(str(_d), _o)
        for _d, _o in _depot_owner.items():
            if _o in owned_dlc_ids:
                owned_ids.add(_d)
        if is_base_owned:
            for _d in game_depots_set:
                if _d not in _depot_owner and _d not in dlc_content_depots:
                    owned_ids.add(_d)

        def _is_owned(did: str) -> bool:
            return did in owned_ids

        def _is_unreleased(did: str) -> bool:
            if did in unreleased_ids:
                return True
            gd_owner = str((gd_depots.get(did) or {}).get("dlcappid") or "").strip()
            if gd_owner and gd_owner in unreleased_dlc_ids:
                unreleased_ids.add(did)
                return True
            if gate.is_unreleased(did):
                unreleased_ids.add(did)
                return True
            return False

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
            if d in dlc_content_depots and not desc.upper().startswith("[DLC]"):
                desc = f"[DLC] {desc}"
            enrich_info = cached_enrichments.get(d) or cached_app_depots.get(d) or {}

            from utils.depot_tag_helpers import get_os_icon
            accent_val = getattr(dialog, "accent_color", None) or ac
            active_tint = QColor(accent_val) if accent_val and QColor(accent_val).isValid() else None
            os_icon = get_os_icon(enrich_info.get("oslist"), tint_color=active_tint) if enrich_info else None
            enriched_desc = _format_enriched_depot_desc(
                depot_id=d,
                fallback_desc=desc,
                enrich_data=enrich_info,
                has_os_icon=bool(os_icon),
                base_appid=appid_str,
            )

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
                "enrich_info": enrich_info,
                "os_icon": os_icon,
                "has_key": (d in valid_key_depots),
                "is_shared": is_shared,
                "size_str": size_str,
                "is_missing": False,
                "is_unreleased": _is_unreleased(d),
                "is_owned": _is_owned(d),
                "dlc_owner": _depot_owner.get(d, ""),
            })

        # Collect missing / pending depots from:
        # 1. at0m_update_status table (new_depots)
        # 2. missing_hubcap_depots table
        # 3. cached_app_depots matching active install platform that lack keys
        missing_depots_map: Dict[str, Dict[str, Any]] = {}

        at0m_stat = db_mgr.get_at0m_update_status(appid_str)
        if at0m_stat and isinstance(at0m_stat.get("new_depots"), list):
            for nd in at0m_stat["new_depots"]:
                if isinstance(nd, dict) and nd.get("id"):
                    nid = str(nd["id"])
                    if nid not in valid_key_depots and nid != appid_str and nid not in ALL_SHARED_REDISTS:
                        has_key = bool(nd.get("has_key"))
                        if _is_unreleased(nid):
                            missing_depots_map[nid] = {
                                "status": STATUS_UNRELEASED,
                                "tag": UNRELEASED_TAG,
                                "tooltip": unreleased_tooltip(nid),
                            }
                        elif has_key:
                            missing_depots_map[nid] = {
                                "status": "ready_on_hubcap",
                                "tag": "[Key Ready]",
                                "tooltip": f"Depot {nid} has an AES decryption key available on Hubcap.\nClick 'API Refetch' to download keys and manifest.",
                            }
                        else:
                            missing_depots_map[nid] = {
                                "status": "key_pending",
                                "tag": "[Awaiting Key]",
                                "tooltip": f"Depot {nid} is registered on Steam, but its AES decryption key is pending on Hubcap (cooldown active).\nIt cannot be downloaded or injected into SLSsteam until the key is published.",
                            }

        for mid in [str(x) for x in (gd.get("missing_depots_from_hubcap") or [])]:
            if mid and mid not in valid_key_depots and mid not in missing_depots_map and mid != appid_str and mid not in ALL_SHARED_REDISTS:
                if _is_unreleased(mid):
                    m_stat, m_tag, m_tip = STATUS_UNRELEASED, UNRELEASED_TAG, unreleased_tooltip(mid)
                else:
                    m_stat, m_tag, m_tip = gate.classify_missing(mid, reported_missing=True)
                missing_depots_map[mid] = {"status": m_stat, "tag": m_tag, "tooltip": m_tip}

        for row in db_mgr.get_missing_hubcap_depots(appid_str):
            mid = str(row.get("depot_id") or "")
            if mid and mid not in valid_key_depots and mid not in missing_depots_map and mid != appid_str and mid not in ALL_SHARED_REDISTS:
                if _is_unreleased(mid):
                    m_stat, m_tag, m_tip = STATUS_UNRELEASED, UNRELEASED_TAG, unreleased_tooltip(mid)
                else:
                    m_stat, m_tag, m_tip = gate.classify_missing(mid, reported_missing=True)
                missing_depots_map[mid] = {"status": m_stat, "tag": m_tag, "tooltip": m_tip}

        try:
            from managers.at0m_update_checker import At0mUpdateChecker
            install_platform = At0mUpdateChecker()._detect_install_platform(appid_str, cached_app_depots)
        except Exception:
            install_platform = None

        from ui.dialogs.depot_selection.rules import _depot_matches_platform
        for d, ddata in cached_app_depots.items():
            did_str = str(d)
            if (
                did_str not in valid_key_depots
                and did_str not in game_depots_set
                and did_str not in missing_depots_map
                and did_str != appid_str
                and did_str not in ALL_SHARED_REDISTS
                and isinstance(ddata, dict)
            ):
                if install_platform and not _depot_matches_platform(ddata, install_platform):
                    continue
                size = ddata.get("size")
                if size is not None and str(size).strip() == "0":
                    manifests = ddata.get("manifests") or {}
                    pub = manifests.get("public") if isinstance(manifests, dict) else {}
                    if not pub or (isinstance(pub, dict) and pub.get("download") in ("0", 0, None)):
                        continue
                if _is_unreleased(did_str) or ddata.get("manifest_id") or ddata.get("manifests") or ddata.get("size") or ddata.get("is_dlc"):
                    # Gate 1: unreleased is shown as such, never as "awaiting key".
                    # Gate 2: released depots are classified by Hubcap availability.
                    if _is_unreleased(did_str):
                        m_stat, m_tag, m_tip = STATUS_UNRELEASED, UNRELEASED_TAG, unreleased_tooltip(did_str)
                    else:
                        m_stat, m_tag, m_tip = gate.classify_missing(did_str)
                    missing_depots_map[did_str] = {
                        "status": m_stat,
                        "tag": m_tag,
                        "tooltip": m_tip,
                    }

        # Add missing / pending depots to depots_list
        existing_depot_ids = {d["id"] for d in depots_list}
        for d in sorted(missing_depots_map.keys() - existing_depot_ids, key=lambda x: int(x) if x.isdigit() else 0):
            m_info = missing_depots_map[d]
            desc = (
                plugin_depot_names.get(d)
                or lua_depots.get(d)
                or (gd_depots.get(d, {}).get("desc") if isinstance(gd_depots.get(d), dict) else "")
                or (f"[DLC] {lua_dlcs.get(d)}" if d in lua_dlcs else None)
                or f"Depot {d}"
            )
            if d in dlc_content_depots and not desc.upper().startswith("[DLC]"):
                desc = f"[DLC] {desc}"
            enrich_info = cached_enrichments.get(d) or cached_app_depots.get(d) or {}

            accent_val = getattr(dialog, "accent_color", None) or ac
            active_tint = QColor(accent_val) if accent_val and QColor(accent_val).isValid() else None
            os_icon = get_os_icon(enrich_info.get("oslist"), tint_color=active_tint) if enrich_info else None
            enriched_desc = _format_enriched_depot_desc(
                depot_id=d,
                fallback_desc=desc,
                enrich_data=enrich_info,
                has_os_icon=bool(os_icon),
                base_appid=appid_str,
            )

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

            depots_list.append({
                "id": d,
                "desc": enriched_desc,
                "raw_desc": desc,
                "enrich_info": enrich_info,
                "os_icon": os_icon,
                "has_key": False,
                "is_shared": False,
                "size_str": size_str,
                "is_missing": True,
                "is_unreleased": m_info["status"] == STATUS_UNRELEASED,
                "is_owned": _is_owned(d),
                "dlc_owner": _depot_owner.get(d, ""),
                "missing_status": m_info["status"],
                "missing_tag": m_info["tag"],
                "missing_tooltip": m_info["tooltip"],
            })

        # Build Keys list for THIS GAME ONLY:
        keys_list = []
        if appid_str in game_keys or appid_str in live_keys or is_base_owned:
            root_k = game_keys.get(appid_str) or live_keys.get(appid_str) or ""
            keys_list.append({
                "id": appid_str,
                "key": root_k,
                "is_appkey": True,
                "in_config": (appid_str in live_keys),
                "in_local": bool(appid_str in local_db_keys or appid_str in lua_keys or appid_str in plugin_keys),
                "is_missing": False,
                "is_base_owned": is_base_owned,
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
                "is_missing": False,
                "is_base_owned": False,
                "is_pure_license": (d in pure_license_dlcs),
                "is_unreleased": _is_unreleased(d),
                "is_owned": _is_owned(d),
            })

        # Append missing / pending keys to keys_list
        existing_key_ids = {k["id"] for k in keys_list}
        for d in sorted(missing_depots_map.keys() - existing_key_ids, key=lambda x: int(x) if x.isdigit() else 0):
            m_info = missing_depots_map[d]
            keys_list.append({
                "id": d,
                "key": "",
                "is_appkey": False,
                "in_config": False,
                "in_local": False,
                "is_missing": True,
                "missing_status": m_info["status"],
                "missing_tag": m_info["tag"],
                "missing_tooltip": m_info["tooltip"],
                "is_base_owned": False,
                "is_pure_license": (d in pure_license_dlcs),
                "is_unreleased": m_info["status"] == STATUS_UNRELEASED,
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
            "unreleased_ids": unreleased_ids,
            "owned_ids": owned_ids,
            "locked_ids": set(unreleased_ids) | set(owned_ids),
            "dlc_content_ids": set(dlc_content_depots) | set(_depot_owner.keys()),
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

        if is_dlc_setup:
            n_sel = sum(
                1 for a in state["apps"]
                if not a.get("is_base") and not a.get("is_owned") and not a.get("is_unreleased")
                and desired_apps.get(a["id"], False)
            )
            sync_btn.setText(f"Register {n_sel} DLC{'s' if n_sel != 1 else ''}")
            sync_btn.setEnabled(n_sel > 0 or is_dirty)
        elif is_dirty:
            sync_btn.setText("Sync Changes")
        else:
            sync_btn.setText("Sync")

    def _update_bulk_states(state: Dict[str, Any]) -> None:
        """Synchronize the checked state of the bulk Select All / None toggles."""
        nonlocal _internal_bulk_update
        _internal_bulk_update = True
        try:
            apps = state["apps"]
            valid_apps = [a for a in apps if not a.get("is_unreleased") and not a.get("is_owned_base") and not a.get("is_owned")]
            if len(apps) >= 5:
                all_apps_on = bool(valid_apps) and all(desired_apps.get(item["id"], False) for item in valid_apps)
                apps_bulk_toggle.setChecked(all_apps_on, animate=False)

            depots = state["depots"]
            valid_depots = [d for d in depots if not d.get("is_missing") and not d.get("is_unreleased") and not d.get("is_owned")]
            if len(depots) >= 5:
                all_depots_on = bool(valid_depots) and all(desired_depots.get(item["id"], False) for item in valid_depots)
                depots_bulk_toggle.setChecked(all_depots_on, animate=False)

            keys = state["keys"]
            valid_keys = [
                k for k in keys
                if k.get("key") and not k.get("is_missing") and not k.get("is_base_owned")
                and not k.get("is_pure_license") and not k.get("is_unreleased") and not k.get("is_owned")
            ]
            if len(keys) >= 5:
                all_keys_on = bool(valid_keys) and all(desired_keys.get(item["id"], False) for item in valid_keys)
                keys_bulk_toggle.setChecked(all_keys_on, animate=False)
        finally:
            _internal_bulk_update = False

    def _on_bulk_toggle_apps(checked: bool) -> None:
        if _internal_bulk_update or not is_unlocked:
            return
        state = _collect_state()
        app_to_depots = _build_app_to_depots_map(state["apps"], state.get("depots"))
        for item in state["apps"]:
            aid = item["id"]
            if item.get("is_unreleased") or item.get("is_owned_base") or item.get("is_owned"):
                continue  # Never bulk-toggle unreleased or Steam-owned entries
            desired_apps[aid] = checked
            if aid in app_toggles:
                app_toggles[aid].setChecked(checked, animate=True)

            assoc_depots = app_to_depots.get(aid, set())
            if checked:
                # Auto-enable matching key in DecryptionKeys if valid key exists
                if aid in key_toggles and not desired_keys.get(aid, False):
                    key_meta = next((k for k in state["keys"] if k["id"] == aid), None)
                    if key_meta and key_meta.get("key") and not key_meta.get("is_missing"):
                        desired_keys[aid] = True
                        key_toggles[aid].setChecked(True, animate=True)

                # Auto-enable associated DLC depots and their keys
                for did in assoc_depots:
                    if did in state.get("locked_ids", set()):
                        continue
                    d_meta = next((d for d in state["depots"] if d["id"] == did), None)
                    if did in depot_toggles and not desired_depots.get(did, False):
                        if not d_meta or not d_meta.get("is_missing"):
                            desired_depots[did] = True
                            depot_toggles[did].setChecked(True, animate=True)
                    if did in key_toggles and not desired_keys.get(did, False):
                        d_key_meta = next((k for k in state["keys"] if k["id"] == did), None)
                        if d_key_meta and d_key_meta.get("key") and not d_key_meta.get("is_missing"):
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
            if item.get("is_unreleased") or item.get("is_owned") or (checked and item.get("is_missing")):
                continue  # Unreleased / Steam-owned are locked; missing depots cannot be enabled
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
            if item.get("is_unreleased") or item.get("is_owned") or (checked and (not item.get("key") or item.get("is_missing"))):
                continue  # Unreleased / Steam-owned are locked; missing keys cannot be enabled
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
            if item.get("is_unreleased") or item.get("is_owned_base") or item.get("is_owned"):
                continue
            desired_apps[aid] = checked
            if aid in app_toggles:
                app_toggles[aid].setChecked(checked, animate=True)

        # 2. Depots
        for item in state["depots"]:
            if item.get("is_unreleased") or item.get("is_owned") or (checked and item.get("is_missing")):
                continue  # Unreleased / owned locked; missing cannot be enabled
            did = item["id"]
            desired_depots[did] = checked
            if did in depot_toggles:
                depot_toggles[did].setChecked(checked, animate=True)

        # 3. Keys
        for item in state["keys"]:
            kid = item["id"]
            if item.get("is_unreleased") or item.get("is_owned") or (checked and (not item.get("key") or item.get("is_missing"))):
                continue  # Unreleased / owned locked; missing cannot be enabled
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

            if item.get("is_owned_base") and is_base:
                id_item.setForeground(QColor("#777777"))
                desc_item.setForeground(QColor("#777777"))
                desc_item.setToolTip("Base game is owned on Steam. Cannot be added to AdditionalApps.")
                apps_table.setItem(row, 0, id_item)
                apps_table.setItem(row, 1, desc_item)
                toggle = MiniSwitchToggle(checked=False, read_only=True, greyscaled=True)
                toggle.setEnabled(False)
                toggle.setToolTip("Base game is owned on Steam. Cannot be added to AdditionalApps.")
                app_toggles[aid] = toggle
                apps_table.setCellWidget(row, 2, _ToggleContainer(toggle))
                desired_apps[aid] = False
                continue

            if item.get("is_owned"):
                id_item.setForeground(QColor("#777777"))
                desc_item.setForeground(QColor("#777777"))
                desc_item.setToolTip("DLC is owned on Steam. Nothing to add; existing config entries are left as they are.")
                apps_table.setItem(row, 0, id_item)
                apps_table.setItem(row, 1, desc_item)
                toggle = MiniSwitchToggle(checked=is_live, read_only=True, greyscaled=True)
                toggle.setEnabled(False)
                toggle.setToolTip("DLC is owned on Steam.")
                app_toggles[aid] = toggle
                apps_table.setCellWidget(row, 2, _ToggleContainer(toggle))
                desired_apps[aid] = is_live
                continue

            if item.get("is_unreleased"):
                id_item.setForeground(QColor("#777777"))
                desc_item.setForeground(QColor("#777777"))
                f_desc = desc_item.font()
                f_desc.setItalic(True)
                desc_item.setFont(f_desc)
                desc_item.setToolTip("DLC is unreleased on Steam (prerelease). Cannot be enabled.")
                apps_table.setItem(row, 0, id_item)
                apps_table.setItem(row, 1, desc_item)
                toggle = MiniSwitchToggle(checked=False, read_only=True, greyscaled=True)
                toggle.setEnabled(False)
                toggle.setToolTip("DLC is unreleased on Steam (prerelease). Cannot be enabled.")
                app_toggles[aid] = toggle
                apps_table.setCellWidget(row, 2, _ToggleContainer(toggle))
                desired_apps[aid] = False
                continue

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
                            if did in state.get("locked_ids", set()):
                                continue  # Unreleased / Steam-owned content stays locked
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

        # Bulk Select All/None for AdditionalApps (>=5 items)
        apps_bulk_bar.setVisible(len(apps) >= 5)
        apps_bulk_toggle.setReadOnly(not is_unlocked)

        # 2. Populate AdditionalDepots Table
        depots = state["depots"]
        depots_table.setRowCount(len(depots))
        for row, item in enumerate(depots):
            did = item["id"]
            is_live = did in live_depots
            is_unrel = bool(item.get("is_unreleased"))
            is_owned_row = bool(item.get("is_owned")) and not is_unrel
            # Unreleased / Steam-owned rows render like missing ones: greyed, tagged, locked toggle.
            is_missing = item.get("is_missing", False) or is_unrel or is_owned_row
            if is_unrel and not item.get("missing_tag"):
                item = dict(item, missing_status=STATUS_UNRELEASED, missing_tag=UNRELEASED_TAG,
                            missing_tooltip=unreleased_tooltip(did))
            elif is_owned_row:
                item = dict(item, missing_status="owned", missing_tag="[Steam Owned]",
                            missing_tooltip=f"Depot {did} belongs to content you own on Steam. Steam provides it; nothing to add.")

            id_item = QTableWidgetItem(did)
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if is_missing:
                id_item.setForeground(QColor(130, 130, 130))
                font_id = id_item.font()
                font_id.setItalic(True)
                id_item.setFont(font_id)
                id_item.setToolTip(item.get("missing_tooltip") or f"Depot {did} key is missing / awaiting Hubcap.")

            desc_text = item["desc"]
            if item.get("is_shared"):
                desc_text = f"{desc_text} [Shared]"
            if is_missing and item.get("missing_tag"):
                desc_text = f"{item['missing_tag']}  {desc_text}"

            desc_item = QTableWidgetItem(desc_text)
            desc_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if item.get("os_icon"):
                desc_item.setIcon(item["os_icon"])
            if is_missing:
                m_stat = item.get("missing_status")
                if m_stat == "ready_on_hubcap":
                    desc_item.setForeground(QColor("#4DD0E1"))
                elif m_stat == "key_pending":
                    desc_item.setForeground(QColor("#FFB74D"))
                else:
                    desc_item.setForeground(QColor(130, 130, 130))
                font_desc = desc_item.font()
                font_desc.setItalic(True)
                desc_item.setFont(font_desc)
                desc_item.setToolTip(item.get("missing_tooltip") or desc_text)
            else:
                tooltip_parts = [desc_text]
                if item.get("size_str"):
                    tooltip_parts.append(f"Size: {item['size_str']}")
                if item.get("is_shared"):
                    tooltip_parts.append("Shared with another game / protected from removal")
                desc_item.setToolTip("\n".join(tooltip_parts))

            depots_table.setItem(row, 0, id_item)
            depots_table.setItem(row, 1, desc_item)

            if is_missing:
                # Unreleased depots already in config show their live state, but stay locked.
                toggle = MiniSwitchToggle(checked=(is_owned_row and is_live), read_only=True, greyscaled=True)
                toggle.setEnabled(False)
                toggle.setToolTip(item.get("missing_tooltip") or "Depot key is not available — cannot enable in AdditionalDepots.")
                depot_toggles[did] = toggle
                depots_table.setCellWidget(row, 2, _ToggleContainer(toggle))
            else:
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

        # Bulk Select All/None for AdditionalDepots (>=5 items)
        depots_bulk_bar.setVisible(len(depots) >= 5)
        depots_bulk_toggle.setReadOnly(not is_unlocked)

        # 3. Populate DecryptionKeys Table
        keys = state["keys"]
        keys_table.setRowCount(len(keys))
        for row, item in enumerate(keys):
            kid = item["id"]
            is_missing = item.get("is_missing", False)

            id_item = QTableWidgetItem(kid)
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if is_missing:
                id_item.setForeground(QColor(130, 130, 130))
                font_id = id_item.font()
                font_id.setItalic(True)
                id_item.setFont(font_id)

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

            if is_missing:
                key_text = item.get("missing_tag", "[No Key]")
                key_item = QTableWidgetItem(key_text)
                key_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                m_stat = item.get("missing_status")
                if m_stat == "ready_on_hubcap":
                    key_item.setForeground(QColor("#4DD0E1"))
                elif m_stat == "key_pending":
                    key_item.setForeground(QColor("#FFB74D"))
                else:
                    key_item.setForeground(QColor(130, 130, 130))
                font_k = key_item.font()
                font_k.setItalic(True)
                key_item.setFont(font_k)
                key_item.setToolTip(item.get("missing_tooltip") or "No AES decryption key available.")
                keys_table.setItem(row, 1, key_item)

                toggle = MiniSwitchToggle(checked=False, read_only=True, greyscaled=True)
                toggle.setEnabled(False)
                toggle.setToolTip(item.get("missing_tooltip") or "Decryption key not available — cannot enable.")
                key_toggles[kid] = toggle
                keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                loc_ind = _make_local_indicator(in_local=False, is_missing=True)
                keys_table.setCellWidget(row, 3, loc_ind)
            elif item.get("is_unreleased") or item.get("is_owned"):
                raw_key = item.get("key") or ""
                short_key = f"{raw_key[:15]}..." if len(raw_key) > 15 else raw_key
                lock_tag = UNRELEASED_TAG if item.get("is_unreleased") else "[Steam Owned]"
                key_item = QTableWidgetItem(f"{lock_tag}  {short_key}".strip())
                key_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                key_item.setForeground(QColor(130, 130, 130))
                f_u = key_item.font()
                f_u.setItalic(True)
                key_item.setFont(f_u)
                lock_tip = unreleased_tooltip(kid) if item.get("is_unreleased") else (
                    f"{kid} belongs to content you own on Steam; its key is not needed."
                )
                key_item.setToolTip(lock_tip)
                keys_table.setItem(row, 1, key_item)

                # Locked: shows whether it is already in config, never changes it.
                toggle = MiniSwitchToggle(checked=bool(item.get("in_config")), read_only=True, greyscaled=True)
                toggle.setEnabled(False)
                toggle.setToolTip(lock_tip)
                key_toggles[kid] = toggle
                keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                loc_ind = _make_local_indicator(in_local=bool(item.get("in_local")), is_missing=False)
                keys_table.setCellWidget(row, 3, loc_ind)
            elif item.get("is_base_owned"):
                key_item = QTableWidgetItem("[Steam Owned]")
                key_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                key_item.setForeground(QColor("#777777"))
                key_item.setToolTip("Base game is owned legitimately on Steam. Base AppKey is not injected.")
                keys_table.setItem(row, 1, key_item)

                toggle = MiniSwitchToggle(checked=False, read_only=True, greyscaled=True)
                toggle.setEnabled(False)
                toggle.setToolTip("Base game is owned legitimately on Steam. Base AppKey is not injected.")
                key_toggles[kid] = toggle
                keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                loc_ind = _make_local_indicator(in_local=True, is_missing=False)
                keys_table.setCellWidget(row, 3, loc_ind)
            elif item.get("is_pure_license"):
                key_item = QTableWidgetItem("[License Only]")
                key_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                key_item.setForeground(QColor(234, 179, 8))
                f = key_item.font()
                f.setItalic(True)
                key_item.setFont(f)
                key_item.setToolTip("Entitlement is granted via AdditionalApps / DlcData. No depot files or AES key required.")
                keys_table.setItem(row, 1, key_item)

                toggle = MiniSwitchToggle(checked=False, read_only=True, greyscaled=True)
                toggle.setEnabled(False)
                toggle.setToolTip("Entitlement is granted via AdditionalApps / DlcData. No depot files or AES key required.")
                key_toggles[kid] = toggle
                keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                loc_ind = _make_local_indicator(in_local=False, is_missing=False)
                keys_table.setCellWidget(row, 3, loc_ind)
            else:
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
                loc_ind = _make_local_indicator(in_local=is_loc, is_missing=False)
                keys_table.setCellWidget(row, 3, loc_ind)

        _set_table_height(keys_table, len(keys))

        # Bulk Select All/None for DecryptionKeys (>=5 items)
        keys_bulk_bar.setVisible(len(keys) >= 5)
        keys_bulk_toggle.setReadOnly(not is_unlocked)

        _update_sync_button_label(state)
        _update_bulk_states(state)

    def _on_unlock():
        """Unlock custom depot and configuration selection after confirmation dialog."""
        nonlocal is_unlocked
        if not is_unlocked:
            reply = ThemedMessageBox.warning(
                dialog,
                "Advanced Configuration",
                "Modifying depot and configuration entries is intended for advanced users only.\n\n"
                "Excluding required depots or keys may prevent Steam from downloading or launching the game properly.\n\n"
                "Do you want to unlock custom configuration editing?",
                ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
                ThemedMessageBox.StandardButton.No,
            )
            if reply == ThemedMessageBox.StandardButton.Yes:
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

        # AT0-M DLC setup: 64+ DLC licenses go to DlcData instead of AdditionalApps.
        selected_dlc_apps = {
            a["id"]: a["desc"] for a in state["apps"]
            if is_dlc_setup and not a.get("is_base") and not a.get("is_owned") and not a.get("is_unreleased")
            and desired_apps.get(a["id"], False)
        }
        use_dlc_data = is_dlc_setup and len(selected_dlc_apps) >= 64
        dlc_data_pending: Dict[str, str] = {}

        with batch_config_edit(cfg_path) as editor:
            if is_unlocked:
                # Custom unlocked sync: strictly follow user's toggle desired states
                # 1. Apps (add if desired and missing, remove if unchecked and present)
                for item in state["apps"]:
                    aid = item["id"]
                    if item.get("is_owned") and not item.get("is_base"):
                        continue  # Steam-owned DLC: leave config exactly as it is
                    if item.get("is_owned_base") or item.get("is_unreleased"):
                        desired = False
                    else:
                        desired = desired_apps.get(aid, aid in state["live_apps"])
                    if desired and aid not in state["live_apps"]:
                        if use_dlc_data and not item.get("is_base"):
                            dlc_data_pending[aid] = re.sub(r"^(?:\[[^\]]*\]\s*)+", "", item["desc"]).strip()
                            continue
                        editor.add_app(aid, comment=item["desc"])
                        added_count += 1
                    elif not desired and aid in state["live_apps"]:
                        editor.remove_app(aid)
                        removed_count += 1

                # Cleanup safeguard: if base appid was accidentally in live AdditionalDepots, prune it
                if appid_str in state["live_depots"]:
                    logger.warning(
                        f"[DepotsTab] Pruned base AppID '{appid_str}' from AdditionalDepots for {game_name}"
                    )
                    editor.remove_depot(appid_str, check_shared=False)
                    removed_count += 1

                # 2. Depots (add if desired and missing, remove if unchecked and present)
                for item in state["depots"]:
                    did = item["id"]
                    if item.get("is_unreleased"):
                        # Gate 1: unreleased depots have no published files; never keep them
                        # in AdditionalDepots. Their keys are left untouched (see keys below).
                        if did in state["live_depots"] and did != appid_str:
                            editor.remove_depot(did, check_shared=True, excluding_appid=appid_str)
                            removed_count += 1
                        continue
                    if did == appid_str or item.get("is_missing") or item.get("is_owned"):
                        continue  # Safeguard: AppIDs, missing and Steam-owned depots are never changed here
                    desired = desired_depots.get(did, did in state["live_depots"])
                    if desired and did not in state["live_depots"]:
                        comment = build_depot_comment(game_name, did, "", appid_str)
                        editor.add_depot(did, comment=comment, app_id=appid_str)
                        added_count += 1
                    elif not desired and did in state["live_depots"]:
                        editor.remove_depot(did, check_shared=True, excluding_appid=appid_str)
                        removed_count += 1

                # 3. Keys (add if desired and missing, remove if unchecked and present)
                for item in state["keys"]:
                    kid = item["id"]
                    if (
                        item.get("is_missing") or not item.get("key") or item.get("is_base_owned")
                        or item.get("is_pure_license") or item.get("is_unreleased") or item.get("is_owned")
                    ):
                        # Missing / base-owned / license-only never go in DecryptionKeys; unreleased
                        # keys are left exactly as they are (never added, never sanitised away).
                        continue
                    desired = desired_keys.get(kid, kid in state["live_keys"])
                    kval = item["key"]
                    if desired and not item["in_config"]:
                        if kval:
                            comment = build_key_comment(game_name, kid, "", appid_str)
                            editor.add_key(kid, kval, comment=comment)
                            added_count += 1
                    elif not desired and item["in_config"]:
                        editor.remove_key(kid, check_shared=True, excluding_appid=appid_str)
                        removed_count += 1
            else:
                # Standard locked sync: automatically add missing entries
                # Cleanup safeguard: if base appid was accidentally in live AdditionalDepots, prune it
                if appid_str in state["live_depots"]:
                    logger.warning(
                        f"[DepotsTab] Pruned base AppID '{appid_str}' from AdditionalDepots for {game_name}"
                    )
                    editor.remove_depot(appid_str, check_shared=False)
                    removed_count += 1

                # 1. Base AppID ONLY (DLCs are never auto-added in default sync; user must unlock and manually enable them)
                from utils.dlc_helpers import is_dlc_only_mode
                base_app_meta = next((a for a in state["apps"] if a["id"] == appid_str), {})
                is_base_owned_locked = base_app_meta.get("is_owned_base", False)
                if not is_dlc_only_mode(appid_str) and not is_base_owned_locked and appid_str not in state["live_apps"]:
                    editor.add_app(appid_str, comment=f"{game_name} (Base)")
                    added_count += 1

                # 2. Depots: add missing depots that have valid keys
                valid_keys = set(
                    item["id"] for item in state["keys"]
                    if item["key"] and not item.get("is_base_owned") and not item.get("is_pure_license")
                    and not item.get("is_unreleased") and not item.get("is_owned")
                )
                for item in state["depots"]:
                    did = item["id"]
                    if item.get("is_unreleased"):
                        # Gate 1: prune unreleased depots from AdditionalDepots (keys untouched)
                        if did in state["live_depots"] and did != appid_str:
                            editor.remove_depot(did, check_shared=True, excluding_appid=appid_str)
                            removed_count += 1
                        continue
                    if did == appid_str or item.get("is_owned"):
                        continue  # Safeguard: AppIDs never in AdditionalDepots; Steam-owned not needed
                    if did not in state["live_depots"] and did in valid_keys:
                        comment = build_depot_comment(game_name, did, "", appid_str)
                        editor.add_depot(did, comment=comment, app_id=appid_str)
                        added_count += 1

                # 3. Keys: add missing keys
                for item in state["keys"]:
                    kid = item["id"]
                    if item.get("is_base_owned") or item.get("is_pure_license") or item.get("is_unreleased") or item.get("is_owned"):
                        continue
                    kval = item["key"]
                    if kval and not item["in_config"]:
                        comment = build_key_comment(game_name, kid, "", appid_str)
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

        if dlc_data_pending:
            try:
                from utils.yaml_config_manager import add_dlc_data_batch
                add_dlc_data_batch(cfg_path, appid_str, dlc_data_pending)
                added_count += len(dlc_data_pending)
            except Exception as e:
                logger.error(f"[DepotsTab] DlcData registration failed: {e}", exc_info=True)

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

        if is_dlc_setup:
            _register_dlc_setup(state, cfg_path, selected_dlc_apps, added_count, removed_count, bool(dlc_data_pending))
            return

        sync_btn.setText("Sync")
        _refresh_ui()

    def _register_dlc_setup(state, cfg_path, selected_dlc_apps, added_count, removed_count, used_dlc_data) -> None:
        """Record an AT0-M DLC-only setup (plugin library + DLC-only flag) and hand back."""
        game_name = dialog.game_data.get("game_name") or f"App {appid_str}"
        live_depots_now = set(get_additional_depots(cfg_path))
        live_keys_now = get_decryption_keys(cfg_path)
        reg_depots = {
            d["id"]: (d.get("raw_desc") or d["desc"]) for d in state["depots"]
            if d["id"] in live_depots_now and not d.get("is_owned")
        }
        reg_keys = {
            k["id"]: str(live_keys_now.get(k["id"]) or k.get("key") or "").lower()
            for k in state["keys"] if k["id"] in live_keys_now and not k.get("is_owned")
        }
        try:
            from utils.plugin_games import load_plugin_library, save_plugin_library
            plib = load_plugin_library()
            rec = plib.get(appid_str, {})
            dlcs = set(rec.get("dlc_appids", [])) | set(selected_dlc_apps.keys())
            names = dict(rec.get("depot_names", {}))
            names.update(reg_depots)
            keys = dict(rec.get("keys", {}))
            keys.update({k: v for k, v in reg_keys.items() if v})
            plib[appid_str] = {
                **rec,
                "name": game_name,
                "appid": appid_str,
                "is_atom": True,
                "is_dlc_only": True,
                "dlc_appids": sorted(dlcs, key=lambda x: int(x) if x.isdigit() else 0),
                "depots": sorted(set(rec.get("depots", [])) | set(reg_depots), key=lambda x: int(x) if x.isdigit() else 0),
                "keys": keys,
                "depot_names": names,
            }
            save_plugin_library(plib)
            from utils.settings import get_settings
            get_settings().setValue(f"dlc_only_mode/{appid_str}", True)
        except Exception as e:
            logger.error(f"[DepotsTab] Could not record AT0-M DLC setup for {appid_str}: {e}", exc_info=True)

        SLSBridge.notify_reload()
        summary = {
            "dlcs": len(selected_dlc_apps),
            "depots": len(reg_depots),
            "keys": len(reg_keys),
            "added": added_count,
            "removed": removed_count,
            "dlc_data": used_dlc_data,
        }
        if callable(on_registered):
            on_registered(summary)
        else:
            status_lbl.setText(f"Registered {summary['dlcs']} DLC(s), {summary['depots']} depot(s).")
            _refresh_ui()

    def _seed_dlc_setup_defaults() -> None:
        """DLC setup: preselect released, unowned DLCs plus their depots and keys."""
        state = _collect_state()
        dlc_content = state.get("dlc_content_ids", set())
        locked = state.get("locked_ids", set())
        for a in state["apps"]:
            if a.get("is_base") or a.get("is_owned") or a.get("is_unreleased"):
                continue
            desired_apps[a["id"]] = True
        for d in state["depots"]:
            if d.get("is_missing") or d["id"] in locked:
                continue
            # Only keyed depots go into AdditionalDepots (unkeyed content cannot decrypt).
            desired_depots[d["id"]] = (d["id"] in dlc_content and bool(d.get("has_key"))) or d["id"] in state["live_depots"]
        for k in state["keys"]:
            if (
                k.get("is_missing") or not k.get("key") or k.get("is_base_owned")
                or k.get("is_pure_license") or k["id"] in locked
            ):
                continue
            desired_keys[k["id"]] = k["id"] in dlc_content or bool(k.get("in_config"))

    def _on_refetch():
        """Refetch fresh Lua metadata and keys from Hubcap API."""
        from core import morrenus_api
        from ui.dialogs.themed_message_box import ThemedMessageBox

        stats = morrenus_api.get_user_stats()
        daily_limit = stats.get("daily_limit") or stats.get("role_daily_limit") or 135
        daily_usage = stats.get("daily_usage", 0)
        can_make_requests = stats.get("can_make_requests", True)
        remaining = max(0, daily_limit - daily_usage)

        if not can_make_requests or remaining <= 0:
            ThemedMessageBox.warning(
                dialog,
                "API Token Limit Reached",
                f"Daily Hubcap API token limit reached (0/{daily_limit} tokens remaining).\n\n"
                f"Please wait for your daily quota to reset, or change your ISP bypass settings in Settings.",
            )
            refetch_btn.setEnabled(False)
            status_lbl.setText(f"API token limit reached (0/{daily_limit}). Please wait for daily reset.")
            return

        reply = ThemedMessageBox.question(
            dialog,
            "Confirm API Refetch",
            f"This action will consume your daily Hubcap API tokens.\n\n"
            f"You have {remaining}/{daily_limit} tokens remaining.\n\n"
            f"Do you want to continue?",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.Cancel,
            ThemedMessageBox.StandardButton.Cancel,
        )
        if reply != ThemedMessageBox.StandardButton.Yes:
            return

        refetch_btn.setEnabled(False)
        unlock_btn.setEnabled(False)
        sync_btn.setEnabled(False)
        status_lbl.setText("Refetching metadata from Hubcap...")

        bridge = _WorkerBridge()

        def _worker():
            try:
                # 1. Force a fresh download from Hubcap. The cached zip is kept
                # until the new one arrives: deleting it first would leave the
                # user with no manifest at all if the download fails.
                zip_path, err = morrenus_api.download_manifest(appid_str, force_update=True)
                if zip_path and os.path.exists(zip_path):
                    import zipfile
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        all_discovered_depots = set()
                        for fname in zf.namelist():
                            if fname.endswith(".manifest"):
                                bname = os.path.basename(fname)
                                parts = bname.split("_")
                                if parts and parts[0].isdigit():
                                    all_discovered_depots.add(parts[0])

                        lua_files = [f for f in zf.namelist() if f.endswith(".lua")]
                        if lua_files:
                            from utils.helpers import get_base_path
                            dest_lua = Path(get_base_path()) / "cached_luas" / f"{appid_str}.lua"
                            dest_lua.parent.mkdir(parents=True, exist_ok=True)
                            lua_data = zf.read(lua_files[0]).decode("utf-8", errors="ignore")
                            dest_lua.write_text(lua_data, encoding="utf-8")

                            for m in iter_live_matches(lua_data, r'addappid\((\d+)'):
                                all_discovered_depots.add(m.group(1))

                            # Extract and persist keys to SQLite
                            fresh_keys = {}
                            for m in iter_live_matches(lua_data, r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)'):
                                fresh_keys[m.group(1)] = m.group(2).lower()
                            if fresh_keys:
                                from managers.depot_key_manager import DepotKeyManager
                                DepotKeyManager.get_instance().save_depot_keys(appid_str, fresh_keys)

                            # Sync fresh keys and mountable depots to config.yaml and plugin library
                            try:
                                import time
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
                                registered_depots = {
                                    str(d) for d in (rec.get("depots", []) if rec else [])
                                }
                                registered_dlc_apps = {
                                    str(d) for d in (rec.get("dlc_appids", []) if rec else [])
                                }
                                discovered_dlc_apps = {
                                    str(d) for d in (dialog.game_data.get("dlcs") or {})
                                } | registered_dlc_apps

                                # Only a depot with a decryption key can be served
                                # by SLSsteam. Mounting an unkeyed depot in
                                # AdditionalApps produces DepotWithoutKey.
                                keyed_depots = {
                                    d for d in all_discovered_depots
                                    if d != appid_str
                                    and d not in discovered_dlc_apps
                                    and d in fresh_keys
                                    and d not in SHARED_REDISTS
                                    and (not is_dlc_only or d in registered_depots)
                                }
                                allowed_key_ids = (
                                    registered_depots | registered_dlc_apps | {appid_str} | SHARED_REDISTS
                                    if is_dlc_only
                                    else keyed_depots | {appid_str} | SHARED_REDISTS
                                )
                                publish_keys = {
                                    did: key for did, key in fresh_keys.items()
                                    if did in allowed_key_ids
                                }

                                if cfg_path and cfg_path.exists():
                                    with batch_config_edit(cfg_path) as editor:
                                        for did, k in publish_keys.items():
                                            editor.add_key(did, k, comment=build_key_comment(game_name, did, "", appid_str))
                                        for did in keyed_depots:
                                            if not is_dlc_only:
                                                editor.add_depot(did, comment=build_depot_comment(game_name, did, "", appid_str), app_id=appid_str)
                                    if editor.has_changes:
                                        SLSBridge.notify_reload()

                                if rec:
                                    rec.setdefault("keys", {}).update(publish_keys)
                                    if not is_dlc_only:
                                        # A DLC-only record keeps its curated
                                        # depot list; bulk-adding every depot
                                        # would override the DLC-only scoping.
                                        existing_deps = set(str(d) for d in rec.get("depots", []))
                                        existing_deps.update(keyed_depots)
                                        rec["depots"] = sorted(list(existing_deps), key=lambda x: int(x) if x.isdigit() else 0)
                                    rec["updated_at"] = int(time.time())
                                    save_plugin_library(lib)

                                if hasattr(dialog, "game_data") and isinstance(dialog.game_data, dict):
                                    g_deps = set(str(d) for d in dialog.game_data.get("depots", []))
                                    g_deps.update(keyed_depots)
                                    dialog.game_data["depots"] = sorted(list(g_deps), key=lambda x: int(x) if x.isdigit() else 0)
                                    dialog.game_data.setdefault("keys", {}).update(publish_keys)
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

    if is_dlc_setup:
        # Setup flow: no unlock gate, and the manifest was just fetched.
        unlock_btn.setVisible(False)
        refetch_btn.setVisible(False)
        status_lbl.setText(
            "Pick the DLCs to add. Steam-owned and unreleased items are greyed out and left untouched."
        )
        status_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")

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

            # 2. Query SteamDB (via Fast-Path race or Byparr)
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
    if is_dlc_setup:
        _seed_dlc_setup_defaults()
    _refresh_ui()

    # Gate 1 safety net: DLC release states that were never verified against
    # Steam (legacy rows defaulted to "released", or no row at all) are checked
    # in the background, then the tables refresh with the real state.
    def _verify_release_states(dlc_ids: List[str], names: Dict[str, str]) -> None:
        try:
            from core.steam_api import resolve_dlc_release_states
            from managers.db_manager import DatabaseManager
            states = resolve_dlc_release_states(dlc_ids)
            if states:
                DatabaseManager().update_release_states(appid_str, states, names)
                enrichment_bridge.enriched.emit()
        except Exception as e:
            logger.debug(f"[DepotsTab] Release state verification failed: {e}")

    try:
        from managers.db_manager import DatabaseManager
        _enr = DatabaseManager().get_depot_enrichments(appid_str) or {}
        _state0 = _collect_state()
        _dlc_names = {a["id"]: a["desc"] for a in _state0["apps"] if not a.get("is_base")}
        _unverified = [d for d in _dlc_names if not (_enr.get(d) or {}).get("release_src")]
        if _unverified:
            if is_dlc_setup:
                status_lbl.setText(f"Checking Steam release state for {len(_unverified)} DLC(s)...")
                enrichment_bridge.enriched.connect(lambda: status_lbl.setText(
                    "Pick the DLCs to add. Steam-owned and unreleased items are greyed out and left untouched."
                ))
            threading.Thread(target=_verify_release_states, args=(_unverified, _dlc_names), daemon=True).start()
    except Exception as e:
        logger.debug(f"[DepotsTab] Could not schedule release state verification: {e}")

    # Check if any depots are still generic and trigger async enrichment if needed
    initial_state = _collect_state()
    needs_enrichment = any(
        bool(re.match(r"^(?:\[.*?\]\s*)?Depot\s+\d+$", item.get("desc", ""), re.IGNORECASE))
        for item in initial_state.get("depots", [])
    )
    if needs_enrichment:
        threading.Thread(target=_run_background_enrichment, daemon=True).start()

    return tab_container
