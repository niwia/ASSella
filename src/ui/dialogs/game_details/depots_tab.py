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
import time
import zipfile
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.assets import DEPOT_BLACKLIST
from ui.dialogs.game_details.hero_header import section_title
from ui.dialogs.themed_message_box import ThemedMessageBox, confirm_steam_owned_override
from utils.lua_parsing import iter_live_matches
from utils.plugin_games import (
    SHARED_REDISTS,
    build_depot_comment,
    build_key_comment,
    comment_has_owner_tag,
    load_plugin_library,
    save_plugin_library,
)
from utils.release_gate import (
    STATUS_UNRELEASED,
    UNRELEASED_TAG,
    ReleaseGate,
    unreleased_tooltip,
)
from utils.sls_bridge import SLSBridge
from utils.yaml_config_manager import (
    _get_section_bounds,
    batch_config_edit,
    ensure_plugins_enabled,
    get_additional_apps,
    get_additional_depots,
    get_decryption_keys,
    get_user_config_path,
)

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

    def __init__(
        self,
        checked: bool = False,
        read_only: bool = True,
        greyscaled: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._checked = checked
        self._read_only = read_only or greyscaled
        self._greyscaled = greyscaled
        self._hovered = False
        self.setFixedSize(36, 18)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if not self._read_only
            else Qt.CursorShape.ArrowCursor
        )

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
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if not self._read_only
            else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def isReadOnly(self) -> bool:
        return self._read_only

    def setGreyscaled(self, greyscaled: bool) -> None:
        self._greyscaled = greyscaled
        if greyscaled:
            self._read_only = True
        self.setCursor(
            Qt.CursorShape.ArrowCursor
            if self._read_only
            else Qt.CursorShape.PointingHandCursor
        )
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

        try:
            from utils.settings import get_settings

            s = get_settings()
            is_christmas = s.value(
                "material_preset", "ocean", type=str
            ) == "christmas" or s.value("accent_color", "", type=str).lower() in (
                "#e53935",
                "#d32f2f",
                "#ff334b",
            )
        except Exception:
            is_christmas = False

        if is_christmas and not self._greyscaled:
            r = 4.0
            track_rect = QRectF(1.0, 1.0, w - 2.0, h - 2.0)
            inner_rect = QRectF(3.0, 3.0, w - 6.0, h - 6.0)

            path = QPainterPath()
            path.addRoundedRect(track_rect, r, r)
            inner_path = QPainterPath()
            inner_path.addRoundedRect(inner_rect, r - 1.0, r - 1.0)
            border_path = path.subtracted(inner_path)

            p.save()
            p.setClipPath(border_path)
            for x in range(-h, w + h, 5):
                p.fillRect(x, 0, 2, h, QColor("#FF334B"))
                p.fillRect(x + 2, 0, 3, h, QColor("#FFFFFF"))
            p.restore()

            bg_col = QColor(140, 20, 20, 170) if self._checked else QColor("#0c1017")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(bg_col))
            p.drawRoundedRect(inner_rect, r - 1.0, r - 1.0)

            if self._checked:
                check_path = QPainterPath()
                check_path.moveTo(6.0, h / 2.0)
                check_path.lineTo(9.5, h / 2.0 + 3.0)
                check_path.lineTo(14.5, h / 2.0 - 3.5)
                p.strokePath(
                    check_path,
                    QPen(
                        QColor("#FFFFFF"),
                        1.8,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin,
                    ),
                )

            knob_d = 12.0
            knob_y = (h - knob_d) / 2.0
            knob_rect = QRectF(self._circle_pos, knob_y, knob_d, knob_d)
            p.setPen(QPen(QColor("#FF334B"), 1.0))
            p.setBrush(QBrush(QColor("#FFFFFF")))
            p.drawRoundedRect(knob_rect, 3.0, 3.0)
            p.end()
            return

        radius = h / 2.0

        if self._greyscaled:
            track_color = QColor(255, 255, 255, 30)
            knob_color = QColor(135, 135, 135)
        else:
            # Background track: green when on, neutral grey when off
            track_color = (
                QColor("#4CAF50") if self._checked else QColor(255, 255, 255, 70)
            )
            if self._checked:
                track_color.setAlpha(190 if self._read_only else 255)
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

    def __init__(self, toggle: MiniSwitchToggle, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.toggle = toggle
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(toggle)

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self.toggle.isReadOnly()
            and not self.toggle.isGreyscaled()
        ):
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
        lbl.setStyleSheet(
            "color: rgba(255, 255, 255, 0.25); font-weight: bold; font-size: 11px;"
        )
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
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
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


def _make_bulk_toggle_bar(
    category_name: str, on_toggle_cb: Callable[[bool], None]
) -> Tuple[QFrame, MiniSwitchToggle]:
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


def init_depots_tab(dialog: Any, parent_layout: Optional[Any] = None) -> None:
    """Initialize the modular Depots tab for AT0-M mode games."""
    panel = build_depots_panel(dialog)
    if parent_layout is not None and hasattr(parent_layout, "addWidget"):
        parent_layout.addWidget(panel)
    else:
        dialog.stacked.addWidget(panel)


def build_depots_panel(
    dialog: Any,
    mode: str = "details",
    on_registered: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> QWidget:
    """The AdditionalApps / AdditionalDepots / DecryptionKeys editor.

    ``mode="details"``: the Game Details Depots tab (locked until Unlock).
    ``mode="dlc_setup"``: AT0-M DLC setup for a Steam-owned game. Opens
    unlocked with released, unowned DLCs (and their depots/keys) preselected;
    unreleased items are greyed and locked, and suspected Steam-owned items
    start off (unless live) and require confirmation to enable. The button
    registers the selection (config + plugin library) and calls
    ``on_registered(summary)``.

    ``dialog`` only needs ``appid``, ``game_data`` and ``accent_color``.
    """
    controller = DepotsTabController(
        dialog=dialog, mode=mode, on_registered=on_registered
    )
    return controller.container


class DepotsTabController(QObject):
    """
    Controller managing state tracking, table population, bulk toggles,
    background metadata/quota enrichment, and YAML/SQLite persistence for the Depots tab.
    """

    _BTN_BASE_STYLE = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.05);
            color: rgba(255, 255, 255, 0.9);
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 6px;
            font-size: 8.5pt;
            font-weight: 500;
            padding: 0 12px;
        }
        QPushButton:hover { background-color: rgba(255, 255, 255, 0.10); border-color: rgba(255, 255, 255, 0.28); }
        QPushButton:disabled { background-color: rgba(255, 255, 255, 0.02); color: rgba(255, 255, 255, 0.25);
            border-color: rgba(255, 255, 255, 0.06); }
    """

    _BTN_ATTENTION_STYLE = """
        QPushButton {
            background-color: rgba(255, 183, 77, 0.12);
            color: #FFB74D;
            border: 1px solid rgba(255, 183, 77, 0.6);
            border-radius: 6px;
            font-size: 8.5pt;
            font-weight: 600;
            padding: 0 12px;
        }
        QPushButton:hover { background-color: rgba(255, 183, 77, 0.22); }
        QPushButton:disabled { background-color: rgba(255, 255, 255, 0.02); color: rgba(255, 255, 255, 0.25);
            border-color: rgba(255, 255, 255, 0.06); }
    """

    def __init__(
        self,
        dialog: Any,
        mode: str = "details",
        on_registered: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        super().__init__()
        self.dialog = dialog
        self.mode = mode
        self.is_dlc_setup = mode == "dlc_setup"
        self.on_registered = on_registered

        self.ac: str = getattr(dialog, "accent_color", "#4CAF50")
        self.appid_str: str = str(dialog.appid).strip()

        # 1. State tracking
        self.is_unlocked: bool = self.is_dlc_setup
        self.desired_apps: Dict[str, bool] = {}
        self.desired_depots: Dict[str, bool] = {}
        self.desired_keys: Dict[str, bool] = {}

        self.app_toggles: Dict[str, MiniSwitchToggle] = {}
        self.depot_toggles: Dict[str, MiniSwitchToggle] = {}
        self.key_toggles: Dict[str, MiniSwitchToggle] = {}

        self.current_state: Dict[str, Any] = {}
        self.app_to_depots: Dict[str, Set[str]] = {}

        self._internal_bulk_update: bool = False
        self._suppress_owned_confirm: bool = False
        self._worker_bridge: Optional[_WorkerBridge] = None

        # Build UI & parent this controller to the container widget to tie lifecycle
        self.container = QWidget()
        self.setParent(self.container)
        self.container._depots_controller = self  # type: ignore[attr-defined]

        self._enrichment_bridge = _EnrichmentBridge(self)
        self._enrichment_bridge.enriched.connect(self._on_enrichment_ready)

        self._build_ui()
        self._init_quota_checks()

        if self.is_dlc_setup:
            self._seed_dlc_setup_defaults()
        self._refresh_ui()

    def _is_destroyed(self) -> bool:
        """Check if Qt container or parent dialog has been deleted by C++."""
        try:
            from PyQt6 import sip
            if hasattr(self, "container") and sip.isdeleted(self.container):
                return True
            if hasattr(self, "dialog") and sip.isdeleted(self.dialog):
                return True
        except Exception:
            pass
        return False

        self._schedule_background_tasks()

    # ------------------------------------------------------------------
    # UI Construction & Helpers
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.container.setStyleSheet("background: transparent;")
        tab_layout = QVBoxLayout(self.container)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)

        # Section 1: AdditionalApps (AppIDs & DLCs)
        layout.addWidget(section_title("AppIDs & Licenses (AdditionalApps)", self.ac))
        self.apps_table = _make_table(["AppID", "Name", "Config"], self.ac)
        self.apps_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self.apps_table.setColumnWidth(0, 110)
        self.apps_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.apps_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed
        )
        self.apps_table.setColumnWidth(2, 75)
        layout.addWidget(self.apps_table)

        self.apps_bulk_bar, self.apps_bulk_toggle = _make_bulk_toggle_bar(
            "AppIDs", self._on_bulk_toggle_apps
        )
        layout.addWidget(self.apps_bulk_bar)

        # Section 2: AdditionalDepots (Depots)
        layout.addWidget(section_title("Depots Registered (AdditionalDepots)", self.ac))
        self.depots_table = _make_table(["Depot ID", "Depot names", "Config"], self.ac)
        self.depots_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self.depots_table.setColumnWidth(0, 110)
        self.depots_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.depots_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed
        )
        self.depots_table.setColumnWidth(2, 75)
        layout.addWidget(self.depots_table)

        self.depots_bulk_bar, self.depots_bulk_toggle = _make_bulk_toggle_bar(
            "Depots", self._on_bulk_toggle_depots
        )
        layout.addWidget(self.depots_bulk_bar)

        # Section 3: DecryptionKeys (Keys)
        layout.addWidget(section_title("AES Decryption Keys (DecryptionKeys)", self.ac))
        self.keys_table = _make_table(
            ["Target ID", "AES keys", "Config", "Local"], self.ac
        )
        self.keys_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self.keys_table.setColumnWidth(0, 110)
        self.keys_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.keys_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed
        )
        self.keys_table.setColumnWidth(2, 75)
        self.keys_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Fixed
        )
        self.keys_table.setColumnWidth(3, 75)
        layout.addWidget(self.keys_table)

        self.keys_bulk_bar, self.keys_bulk_toggle = _make_bulk_toggle_bar(
            "Keys", self._on_bulk_toggle_keys
        )
        layout.addWidget(self.keys_bulk_bar)

        # Status Label
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(
            "color: rgba(255, 255, 255, 0.55); font-size: 8.5pt; background: transparent; border: none;"
        )
        self.status_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.status_lbl.setWordWrap(True)
        self._set_status_raw = self.status_lbl.setText
        self.status_lbl.setText = self._set_status  # type: ignore[method-assign]

        # Persistent Bottom Actions Bar
        btn_bar = QFrame()
        btn_bar.setStyleSheet("background: transparent; border: none;")
        bar_lay = QHBoxLayout(btn_bar)
        bar_lay.setContentsMargins(0, 0, 0, 0)
        bar_lay.setSpacing(8)

        self.refetch_btn = QPushButton("Refetch keys")
        self.refetch_btn.setFixedHeight(30)
        self.refetch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refetch_btn.setStyleSheet(self._BTN_BASE_STYLE)
        self.refetch_btn.setToolTip(
            "Download the latest keys and manifests for this game from Hubcap."
        )

        # View / Edit switch (replaces the old Unlock button; same confirmation).
        self.unlock_btn = QPushButton("Unlock")
        self.unlock_btn.setVisible(False)

        self.mode_seg = QFrame()
        self.mode_seg.setObjectName("modeSeg")
        self.mode_seg.setStyleSheet(
            "QFrame#modeSeg { border: 1px solid rgba(255,255,255,0.16); border-radius: 6px; background: transparent; }"
        )
        seg_lay = QHBoxLayout(self.mode_seg)
        seg_lay.setContentsMargins(2, 2, 2, 2)
        seg_lay.setSpacing(2)
        self.view_btn = QPushButton("View")
        self.edit_btn = QPushButton("Edit")
        for b in (self.view_btn, self.edit_btn):
            b.setCheckable(True)
            b.setFixedHeight(24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: none; border-radius: 4px; padding: 0 12px;
                    color: rgba(255,255,255,0.55); font-size: 8.5pt; }}
                QPushButton:checked {{ background: rgba(255,255,255,0.12); color: #FFFFFF; font-weight: 600; }}
                QPushButton:hover:!checked {{ color: {self.ac}; }}
            """)
            seg_lay.addWidget(b)
        self.view_btn.setChecked(not self.is_unlocked)
        self.edit_btn.setChecked(self.is_unlocked)
        self.view_btn.setToolTip(
            "Read-only: the switches show what SLSsteam has configured."
        )
        self.edit_btn.setToolTip(
            "Change which apps, depots and keys are in SLSsteam's config."
        )

        self.view_btn.clicked.connect(self._on_view_btn_clicked)
        self.edit_btn.clicked.connect(self._on_edit_btn_clicked)

        self.sync_btn = QPushButton("Save")
        self.sync_btn.setFixedHeight(30)
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.ac};
                color: #000000;
                border: none;
                border-radius: 6px;
                font-size: 8.5pt;
                font-weight: bold;
                padding: 0 16px;
            }}
            QPushButton:hover {{ background-color: rgba(255, 255, 255, 0.92); }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.06);
                color: rgba(255, 255, 255, 0.28);
                border: 1px solid rgba(255, 255, 255, 0.08);
            }}
        """)
        self.sync_btn.setEnabled(False)

        bar_lay.addWidget(self.mode_seg, 0)
        bar_lay.addWidget(self.refetch_btn, 0)
        bar_lay.addWidget(self.status_lbl, 1)
        bar_lay.addWidget(self.sync_btn, 0)

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
        footer_lay.setContentsMargins(14, 8, 14, 8)
        footer_lay.setSpacing(0)
        footer_lay.addWidget(btn_bar)
        tab_layout.addWidget(footer_bar, 0)

        self.refetch_btn.clicked.connect(self._on_refetch)
        self.unlock_btn.clicked.connect(self._on_unlock)
        self.sync_btn.clicked.connect(self._on_sync)

        if self.is_dlc_setup:
            self.mode_seg.setVisible(False)
            self.refetch_btn.setVisible(False)
            self.status_lbl.setText(
                "Pick the DLCs to add. Steam-owned items require confirmation; unreleased items are greyed out and left untouched."
            )
            self.status_lbl.setStyleSheet(
                "color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;"
            )

    def _set_status(self, text: str) -> None:
        """Strip legacy 'Click Sync' nags and auto-clear status after 6 seconds."""
        cleaned = re.sub(
            r"\s*Click 'Sync( Changes)?' to apply\.?", "", str(text or "")
        ).strip()
        self._set_status_raw(cleaned)
        if cleaned:
            QTimer.singleShot(
                6000, lambda t=cleaned: self._clear_status_if_matches(t)
            )

    def _clear_status_if_matches(self, expected_text: str) -> None:
        if self._is_destroyed():
            return
        if self.status_lbl.text() == expected_text:
            self._set_status_raw("")

    def _sync_mode_seg(self) -> None:
        self.view_btn.setChecked(not self.is_unlocked)
        self.edit_btn.setChecked(self.is_unlocked)

    def _on_view_btn_clicked(self) -> None:
        if self.is_unlocked:
            self._on_unlock()
        else:
            self._sync_mode_seg()

    def _on_edit_btn_clicked(self) -> None:
        if not self.is_unlocked:
            self._on_unlock()
        else:
            self._sync_mode_seg()

    @staticmethod
    def _set_switch_silently(toggle: MiniSwitchToggle, checked: bool) -> None:
        toggle.blockSignals(True)
        try:
            toggle.setChecked(checked, animate=False)
        finally:
            toggle.blockSignals(False)

    def _confirm_owned_action(
        self, toggle: Optional[MiniSwitchToggle], should_confirm: bool
    ) -> bool:
        if (
            not should_confirm
            or self._suppress_owned_confirm
            or self._internal_bulk_update
        ):
            return True
        if confirm_steam_owned_override(self.dialog):
            return True
        if toggle is not None:
            self._set_switch_silently(toggle, False)
        return False

    # ------------------------------------------------------------------
    # State Collection & Mapping
    # ------------------------------------------------------------------

    def _build_app_to_depots_map(
        self,
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
            self.appid_str,
            game_data=self.dialog.game_data,
            extra_appids=[item["id"] for item in state_apps],
            candidate_depots=[d["id"] for d in state_depots]
            if state_depots
            else None,
        )

    def _collect_state(self) -> Dict[str, Any]:
        """Collect and cross-reference data strictly for THIS game."""
        cfg_path = get_user_config_path()
        live_apps = (
            set(get_additional_apps(cfg_path)) if cfg_path.exists() else set()
        )
        live_depots = (
            set(get_additional_depots(cfg_path)) if cfg_path.exists() else set()
        )
        live_keys = get_decryption_keys(cfg_path) if cfg_path.exists() else {}

        # 1. Plugin Library record for THIS game
        plugin_lib = load_plugin_library()
        game_record = plugin_lib.get(self.appid_str, {})
        game_name = (
            self.dialog.game_data.get("game_name")
            or game_record.get("name")
            or f"App {self.appid_str}"
        )

        plugin_depots = {str(d) for d in game_record.get("depots", [])}
        plugin_keys = {
            str(d): str(k).lower() for d, k in game_record.get("keys", {}).items()
        }
        plugin_depot_names = {
            str(d): str(n) for d, n in game_record.get("depot_names", {}).items()
        }
        plugin_dlcs = {str(d) for d in game_record.get("dlc_appids", [])}

        # 2. Local SQLite depot keys for THIS appid
        from managers.depot_key_manager import DepotKeyManager

        dkm = DepotKeyManager.get_instance()
        local_db_keys = {
            str(d): str(k).lower()
            for d, k in (dkm.get_keys_for_app(self.appid_str) or {}).items()
        }

        # 3. Cached Lua parsing for THIS game
        from utils.helpers import get_base_path

        cached_lua_path = (
            Path(get_base_path()) / "cached_luas" / f"{self.appid_str}.lua"
        )
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
                for m in iter_live_matches(
                    txt, r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)'
                ):
                    did, key = m.group(1), m.group(2)
                    lua_keys[str(did)] = key.lower()

                from core.tasks.process_zip_task import ProcessZipTask

                parsed: Dict[str, Any] = {}
                ProcessZipTask._parse_lua(txt, parsed)
                if parsed.get("app_key"):
                    lua_keys[self.appid_str] = parsed["app_key"].lower()
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
        gd = self.dialog.game_data or {}
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
            gd_depots = {
                str(k): (v if isinstance(v, dict) else {"desc": str(v)})
                for k, v in gd_depots_raw.items()
            }
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
                    sec_app = cfg_text[app_bounds[1] : app_bounds[2]]
                    for line in sec_app.splitlines():
                        m = re.match(
                            r"^[ \t]*-[ \t]*(\d+)[ \t]*(?:#[ \t]*(.*))?$", line
                        )
                        if m:
                            aid, comment = m.group(1), (m.group(2) or "")
                            if (
                                game_name and game_name.lower() in comment.lower()
                            ) or comment_has_owner_tag(comment, self.appid_str):
                                if aid != self.appid_str:
                                    clean_c = re.sub(
                                        r"^\[DLC\]\s*",
                                        "",
                                        comment.strip(),
                                        flags=re.IGNORECASE,
                                    )
                                    tagged_apps_from_config[aid] = (
                                        clean_c or f"DLC {aid}"
                                    )

                dep_bounds = _get_section_bounds(cfg_text, "AdditionalDepots")
                if dep_bounds:
                    sec_dep = cfg_text[dep_bounds[1] : dep_bounds[2]]
                    for line in sec_dep.splitlines():
                        m = re.match(
                            r"^[ \t]*-[ \t]*(\d+)[ \t]*(?:#[ \t]*(.*))?$", line
                        )
                        if m:
                            did, comment = m.group(1), (m.group(2) or "")
                            if (
                                game_name and game_name.lower() in comment.lower()
                            ) or comment_has_owner_tag(comment, self.appid_str):
                                tagged_depots_from_config.add(did)

                key_bounds = _get_section_bounds(cfg_text, "DecryptionKeys")
                if key_bounds:
                    sec_key = cfg_text[key_bounds[1] : key_bounds[2]]
                    for line in sec_key.splitlines():
                        m = re.match(
                            r"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*['\"]?([a-fA-F0-9]{64})['\"]?[ \t]*(?:#[ \t]*(.*))?$",
                            line,
                        )
                        if m:
                            did, kval, comment = (
                                m.group(1),
                                m.group(2),
                                (m.group(3) or ""),
                            )
                            if (
                                game_name and game_name.lower() in comment.lower()
                            ) or comment_has_owner_tag(comment, self.appid_str):
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
        for d in set(gd_depots.keys()) | set(lua_depots.keys()) | {self.appid_str}:
            if d in live_keys and d not in ALL_SHARED_REDISTS:
                k = live_keys[d]
                if k and len(k) == 64:
                    game_keys.setdefault(d, k.lower())
                    if d not in local_db_keys:
                        try:
                            dkm.save_depot_keys(self.appid_str, {d: k.lower()})
                            local_db_keys[d] = k.lower()
                        except Exception as e:
                            logger.debug(f"[DepotsTab] Auto-seed local key failed: {e}")

        game_keys = {
            d: k for d, k in game_keys.items() if d not in ALL_SHARED_REDISTS
        }

        from core.steam_helpers import get_owned_steam_appids
        from managers.db_manager import DatabaseManager
        from utils.dlc_helpers import is_dlc_only_mode
        from utils.yaml_config_manager import is_depot_shared_with_other_games

        db_mgr = DatabaseManager()
        cached_enrichments = db_mgr.get_depot_enrichments(self.appid_str) or {}
        cached_app_info = db_mgr.get_app_info(self.appid_str) or {}
        cached_app_depots = (
            (cached_app_info.get("depots") or {})
            if isinstance(cached_app_info, dict)
            else {}
        )

        gate = ReleaseGate(
            self.appid_str,
            enrichments=cached_enrichments,
            app_depots=cached_app_depots,
            db=db_mgr,
        )

        is_steam_owned_base = self.appid_str in get_owned_steam_appids()
        try:
            from utils.dlc_helpers import get_owned_dlcs_from_steam

            steam_owned_dlcs = {
                str(x)
                for x in (get_owned_dlcs_from_steam(self.appid_str) or set())
            }
        except Exception as e:
            logger.debug(f"[DepotsTab] Could not read Steam DLC ownership: {e}")
            steam_owned_dlcs = set()
        is_dlc_only = bool(
            self.dialog.game_data.get("is_dlc_only")
            or self.dialog.game_data.get("_dlc_only")
            or is_dlc_only_mode(self.appid_str)
        )
        is_dlc_context = bool(self.is_dlc_setup or is_dlc_only)
        is_base_owned = bool(is_steam_owned_base or is_dlc_only)

        # DLC AppIDs set
        dlc_id_set = (
            set(lua_dlcs.keys())
            | plugin_dlcs
            | set(tagged_apps_from_config.keys())
        )
        dlc_id_set.discard(self.appid_str)

        # Build AppIDs list: Base AppID + DLC AppIDs
        base_desc = (
            f"{game_name} [Steam Owned]"
            if is_base_owned
            else f"{game_name} (Base)"
        )
        apps_list: List[Dict[str, Any]] = [
            {
                "id": self.appid_str,
                "desc": base_desc,
                "is_base": True,
                "is_owned_base": is_base_owned,
                "is_owned": is_steam_owned_base,
                "is_dlc_base_locked": is_dlc_context,
                "is_unreleased": False,
            }
        ]
        for dlc_id in sorted(
            dlc_id_set, key=lambda x: int(x) if x.isdigit() else 0
        ):
            desc = (
                lua_dlcs.get(dlc_id)
                or gd_dlcs.get(dlc_id)
                or tagged_apps_from_config.get(dlc_id)
                or f"DLC {dlc_id}"
            )
            is_unrel = gate.is_unreleased(dlc_id, is_dlc_app=True)
            prefix = f"{UNRELEASED_TAG} [DLC]" if is_unrel else "[DLC]"
            is_owned_dlc = dlc_id in steam_owned_dlcs
            if is_owned_dlc:
                prefix = "[Steam Owned] [DLC]"
            apps_list.append(
                {
                    "id": dlc_id,
                    "desc": f"{prefix} {desc}",
                    "is_base": False,
                    "is_owned_base": False,
                    "is_owned": is_owned_dlc,
                    "is_unreleased": is_unrel,
                }
            )

        content_dlcs = {
            d
            for d in dlc_id_set
            if (d in game_keys or d in lua_depots or d in gd_depots)
        }
        pure_license_dlcs = dlc_id_set - content_dlcs
        dlc_content_depots = set(dlc_id_set)
        for depot_id, depot_info in gd_depots.items():
            if (
                isinstance(depot_info, dict)
                and str(depot_info.get("dlcappid") or "").strip() in dlc_id_set
            ):
                dlc_content_depots.add(depot_id)

        all_referenced_depots = (
            set(lua_depots.keys())
            | plugin_depots
            | set(game_keys.keys())
            | tagged_depots_from_config
            | set(gd_depots.keys())
        )
        game_shared_depots = (
            all_referenced_depots & ALL_SHARED_REDISTS
        ) - {self.appid_str}

        game_depots_set = (
            all_referenced_depots
            - ALL_SHARED_REDISTS
            - {self.appid_str}
            - pure_license_dlcs
        )
        reported_missing = {
            str(x) for x in (gd.get("missing_depots_from_hubcap") or [])
        } - set(game_keys.keys())
        game_depots_set -= reported_missing

        # Gate 1 cascade: unreleased DLCs take mapped depots down with them
        unreleased_dlc_ids = {
            a["id"] for a in apps_list if a.get("is_unreleased")
        }
        unreleased_ids: Set[str] = set(unreleased_dlc_ids)
        if unreleased_dlc_ids:
            candidate_ids = sorted(
                game_depots_set | set(cached_app_depots.keys())
            )
            mapped = self._build_app_to_depots_map(
                apps_list, [{"id": str(c)} for c in candidate_ids]
            )
            for dlc_id in unreleased_dlc_ids:
                unreleased_ids |= {
                    str(x) for x in mapped.get(dlc_id, set())
                }

        # Steam-owned content mapping
        owned_dlc_ids = {a["id"] for a in apps_list if a.get("is_owned")}
        owned_ids: Set[str] = set(owned_dlc_ids)
        _candidates = sorted(game_depots_set | set(cached_app_depots.keys()))
        _owner_map = self._build_app_to_depots_map(
            apps_list, [{"id": str(c)} for c in _candidates]
        )
        _depot_owner: Dict[str, str] = {}
        for _aid, _dset in _owner_map.items():
            if _aid != self.appid_str:
                for _d in _dset:
                    _depot_owner.setdefault(str(_d), _aid)
        for _d, _info in gd_depots.items():
            _o = str((_info or {}).get("dlcappid") or "").strip()
            if _o and _o != self.appid_str:
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
            gd_owner = str(
                (gd_depots.get(did) or {}).get("dlcappid") or ""
            ).strip()
            if gd_owner and gd_owner in unreleased_dlc_ids:
                unreleased_ids.add(did)
                return True
            if gate.is_unreleased(did):
                unreleased_ids.add(did)
                return True
            return False

        from utils.depot_tag_helpers import get_os_icon

        accent_val = getattr(self.dialog, "accent_color", None) or self.ac
        active_tint = (
            QColor(accent_val)
            if accent_val and QColor(accent_val).isValid()
            else None
        )

        valid_key_depots = set(game_keys.keys())
        depots_list: List[Dict[str, Any]] = []
        for d in sorted(
            game_depots_set, key=lambda x: int(x) if x.isdigit() else 0
        ):
            if (
                d in blacklisted_depots
                and d not in live_depots
                and d not in valid_key_depots
            ):
                continue
            desc = (
                plugin_depot_names.get(d)
                or lua_depots.get(d)
                or (
                    gd_depots.get(d, {}).get("desc")
                    if isinstance(gd_depots.get(d), dict)
                    else ""
                )
                or (f"[DLC] {lua_dlcs.get(d)}" if d in lua_dlcs else None)
                or f"Depot {d}"
            )
            if d in dlc_content_depots and not desc.upper().startswith("[DLC]"):
                desc = f"[DLC] {desc}"
            enrich_info = (
                cached_enrichments.get(d) or cached_app_depots.get(d) or {}
            )

            os_icon = (
                get_os_icon(enrich_info.get("oslist"), tint_color=active_tint)
                if enrich_info
                else None
            )
            enriched_desc = _format_enriched_depot_desc(
                depot_id=d,
                fallback_desc=desc,
                enrich_data=enrich_info,
                has_os_icon=bool(os_icon),
                base_appid=self.appid_str,
            )

            size_str = self._extract_size_str(enrich_info)
            is_shared = is_depot_shared_with_other_games(
                d, excluding_appid=self.appid_str
            )
            depots_list.append(
                {
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
                }
            )

        # Collect missing / pending depots
        missing_depots_map: Dict[str, Dict[str, Any]] = {}

        at0m_stat = db_mgr.get_at0m_update_status(self.appid_str)
        if at0m_stat and isinstance(at0m_stat.get("new_depots"), list):
            for nd in at0m_stat["new_depots"]:
                if isinstance(nd, dict) and nd.get("id"):
                    nid = str(nd["id"])
                    if (
                        nid not in valid_key_depots
                        and nid != self.appid_str
                        and nid not in ALL_SHARED_REDISTS
                    ):
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
                                "tooltip": f"Depot {nid} has an AES decryption key available on Hubcap.\nClick 'Refetch keys' to download it.",
                            }
                        else:
                            missing_depots_map[nid] = {
                                "status": "key_pending",
                                "tag": "[Awaiting Key]",
                                "tooltip": f"Depot {nid} is registered on Steam, but its AES decryption key is pending on Hubcap (cooldown active).\nIt cannot be downloaded or injected into SLSsteam until the key is published.",
                            }

        for mid in [
            str(x) for x in (gd.get("missing_depots_from_hubcap") or [])
        ]:
            if (
                mid
                and mid not in valid_key_depots
                and mid not in missing_depots_map
                and mid != self.appid_str
                and mid not in ALL_SHARED_REDISTS
            ):
                if _is_unreleased(mid):
                    m_stat, m_tag, m_tip = (
                        STATUS_UNRELEASED,
                        UNRELEASED_TAG,
                        unreleased_tooltip(mid),
                    )
                else:
                    m_stat, m_tag, m_tip = gate.classify_missing(
                        mid, reported_missing=True
                    )
                missing_depots_map[mid] = {
                    "status": m_stat,
                    "tag": m_tag,
                    "tooltip": m_tip,
                }

        for row in db_mgr.get_missing_hubcap_depots(self.appid_str):
            mid = str(row.get("depot_id") or "")
            if (
                mid
                and mid not in valid_key_depots
                and mid not in missing_depots_map
                and mid != self.appid_str
                and mid not in ALL_SHARED_REDISTS
            ):
                if _is_unreleased(mid):
                    m_stat, m_tag, m_tip = (
                        STATUS_UNRELEASED,
                        UNRELEASED_TAG,
                        unreleased_tooltip(mid),
                    )
                else:
                    m_stat, m_tag, m_tip = gate.classify_missing(
                        mid, reported_missing=True
                    )
                missing_depots_map[mid] = {
                    "status": m_stat,
                    "tag": m_tag,
                    "tooltip": m_tip,
                }

        try:
            from managers.at0m_update_checker import At0mUpdateChecker

            install_platform = At0mUpdateChecker()._detect_install_platform(
                self.appid_str, cached_app_depots
            )
        except Exception:
            install_platform = None

        from ui.dialogs.depot_selection.rules import _depot_matches_platform

        for d, ddata in cached_app_depots.items():
            did_str = str(d)
            if (
                did_str not in valid_key_depots
                and did_str not in game_depots_set
                and did_str not in missing_depots_map
                and did_str != self.appid_str
                and did_str not in ALL_SHARED_REDISTS
                and isinstance(ddata, dict)
            ):
                if install_platform and not _depot_matches_platform(
                    ddata, install_platform
                ):
                    continue
                size = ddata.get("size")
                if size is not None and str(size).strip() == "0":
                    manifests = ddata.get("manifests") or {}
                    pub = (
                        manifests.get("public")
                        if isinstance(manifests, dict)
                        else {}
                    )
                    if not pub or (
                        isinstance(pub, dict)
                        and pub.get("download") in ("0", 0, None)
                    ):
                        continue
                if (
                    _is_unreleased(did_str)
                    or ddata.get("manifest_id")
                    or ddata.get("manifests")
                    or ddata.get("size")
                    or ddata.get("is_dlc")
                ):
                    if _is_unreleased(did_str):
                        m_stat, m_tag, m_tip = (
                            STATUS_UNRELEASED,
                            UNRELEASED_TAG,
                            unreleased_tooltip(did_str),
                        )
                    else:
                        m_stat, m_tag, m_tip = gate.classify_missing(did_str)
                    missing_depots_map[did_str] = {
                        "status": m_stat,
                        "tag": m_tag,
                        "tooltip": m_tip,
                    }

        existing_depot_ids = {d["id"] for d in depots_list}
        for d in sorted(
            missing_depots_map.keys() - existing_depot_ids,
            key=lambda x: int(x) if x.isdigit() else 0,
        ):
            m_info = missing_depots_map[d]
            desc = (
                plugin_depot_names.get(d)
                or lua_depots.get(d)
                or (
                    gd_depots.get(d, {}).get("desc")
                    if isinstance(gd_depots.get(d), dict)
                    else ""
                )
                or (f"[DLC] {lua_dlcs.get(d)}" if d in lua_dlcs else None)
                or f"Depot {d}"
            )
            if d in dlc_content_depots and not desc.upper().startswith("[DLC]"):
                desc = f"[DLC] {desc}"
            enrich_info = (
                cached_enrichments.get(d) or cached_app_depots.get(d) or {}
            )

            os_icon = (
                get_os_icon(enrich_info.get("oslist"), tint_color=active_tint)
                if enrich_info
                else None
            )
            enriched_desc = _format_enriched_depot_desc(
                depot_id=d,
                fallback_desc=desc,
                enrich_data=enrich_info,
                has_os_icon=bool(os_icon),
                base_appid=self.appid_str,
            )

            size_str = self._extract_size_str(enrich_info)
            depots_list.append(
                {
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
                }
            )

        # Build Keys list for THIS GAME ONLY
        keys_list: List[Dict[str, Any]] = []
        if (
            self.appid_str in game_keys
            or self.appid_str in live_keys
            or is_base_owned
        ):
            root_k = (
                game_keys.get(self.appid_str)
                or live_keys.get(self.appid_str)
                or ""
            )
            keys_list.append(
                {
                    "id": self.appid_str,
                    "key": root_k,
                    "is_appkey": True,
                    "in_config": (self.appid_str in live_keys),
                    "in_local": bool(
                        self.appid_str in local_db_keys
                        or self.appid_str in lua_keys
                        or self.appid_str in plugin_keys
                    ),
                    "is_missing": False,
                    "is_base_owned": is_base_owned,
                    "is_dlc_base_locked": is_dlc_context,
                    "is_owned": is_steam_owned_base,
                }
            )

        all_target_key_ids = (
            (
                set(game_depots_set)
                | set(game_keys.keys())
                | set(tagged_keys_from_config.keys())
            )
            - {self.appid_str}
            - ALL_SHARED_REDISTS
        )
        for d in sorted(
            all_target_key_ids, key=lambda x: int(x) if x.isdigit() else 0
        ):
            key_val = game_keys.get(d) or (
                live_keys.get(d) if d in tagged_keys_from_config else ""
            )
            if not key_val and d not in game_keys and d not in live_keys:
                continue
            keys_list.append(
                {
                    "id": d,
                    "key": key_val or live_keys.get(d, ""),
                    "is_appkey": False,
                    "in_config": (d in live_keys),
                    "in_local": bool(
                        d in local_db_keys or d in lua_keys or d in plugin_keys
                    ),
                    "is_missing": False,
                    "is_base_owned": False,
                    "is_pure_license": (d in pure_license_dlcs),
                    "is_unreleased": _is_unreleased(d),
                    "is_owned": _is_owned(d),
                }
            )

        existing_key_ids = {k["id"] for k in keys_list}
        for d in sorted(
            missing_depots_map.keys() - existing_key_ids,
            key=lambda x: int(x) if x.isdigit() else 0,
        ):
            m_info = missing_depots_map[d]
            keys_list.append(
                {
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
                }
            )

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
            "dlc_content_ids": set(dlc_content_depots)
            | set(_depot_owner.keys()),
        }

    @staticmethod
    def _extract_size_str(enrich_info: Dict[str, Any]) -> str:
        if not enrich_info:
            return ""
        size_str = enrich_info.get("size_str") or ""
        if not size_str and enrich_info.get("size_bytes"):
            try:
                from utils.helpers import format_bytes

                size_str = format_bytes(enrich_info["size_bytes"])
            except Exception:
                size_str = str(enrich_info["size_bytes"])
        elif (
            not size_str
            and enrich_info.get("size")
            and str(enrich_info["size"]).isdigit()
        ):
            try:
                from utils.helpers import format_bytes

                size_str = format_bytes(int(enrich_info["size"]))
            except Exception:
                size_str = str(enrich_info["size"])
        return size_str

    def _seed_dlc_setup_defaults(self) -> None:
        """DLC setup: preselect released, unowned DLCs plus their depots and keys.
        Suspected Steam-owned items start off unless already live in config."""
        state = self._collect_state()
        self.current_state = state
        dlc_content = state.get("dlc_content_ids", set())
        locked = state.get("locked_ids", set())
        for a in state["apps"]:
            if a.get("is_base") or a.get("is_unreleased"):
                continue
            if a.get("is_owned"):
                self.desired_apps[a["id"]] = a["id"] in state["live_apps"]
            else:
                self.desired_apps[a["id"]] = True
        for d in state["depots"]:
            if d.get("is_missing") or d.get("is_unreleased"):
                continue
            if d["id"] in locked:
                self.desired_depots[d["id"]] = d["id"] in state["live_depots"]
                continue
            self.desired_depots[d["id"]] = (
                d["id"] in dlc_content and bool(d.get("has_key"))
            ) or d["id"] in state["live_depots"]
        for k in state["keys"]:
            if (
                k.get("is_missing")
                or not k.get("key")
                or k.get("is_dlc_base_locked")
                or k.get("is_pure_license")
                or k.get("is_unreleased")
            ):
                continue
            if k["id"] in locked:
                self.desired_keys[k["id"]] = bool(k.get("in_config"))
                continue
            self.desired_keys[k["id"]] = k["id"] in dlc_content or bool(
                k.get("in_config")
            )

    # ------------------------------------------------------------------
    # 2. Table Setup & Row Population
    # ------------------------------------------------------------------

    def _refresh_ui(self) -> None:
        if self._is_destroyed():
            return
        """Populate table widgets with gathered data."""
        state = self._collect_state()
        self.current_state = state
        self.app_to_depots = self._build_app_to_depots_map(
            state["apps"], state.get("depots")
        )

        live_apps = state["live_apps"]
        live_depots = state["live_depots"]

        self.app_toggles.clear()
        self.depot_toggles.clear()
        self.key_toggles.clear()

        if not self.is_unlocked:
            self.desired_apps.clear()
            for item in state["apps"]:
                self.desired_apps[item["id"]] = item["id"] in live_apps
            self.desired_depots.clear()
            for item in state["depots"]:
                self.desired_depots[item["id"]] = item["id"] in live_depots
            self.desired_keys.clear()
            for item in state["keys"]:
                self.desired_keys[item["id"]] = item["in_config"]

        self._populate_apps_table(state)
        self._populate_depots_table(state)
        self._populate_keys_table(state)

        self._update_sync_button_label(state)
        self._update_bulk_states(state)

    def _populate_apps_table(self, state: Dict[str, Any]) -> None:
        apps = state["apps"]
        live_apps = state["live_apps"]
        game_keys = state["game_keys"]

        self.apps_table.setRowCount(len(apps))
        for row, item in enumerate(apps):
            aid = item["id"]
            is_base = item.get("is_base", False)
            is_live = aid in live_apps
            has_matching_key = (not is_base) and (aid in game_keys)

            id_item = QTableWidgetItem(aid)
            id_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            desc_item = QTableWidgetItem(item["desc"])
            desc_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )

            if has_matching_key:
                id_item.setForeground(QColor("#777777"))
                desc_item.setForeground(QColor("#777777"))
                desc_item.setToolTip(
                    "DLC has matching depot key. Decryption is handled via DecryptionKeys."
                )

            if item.get("is_dlc_base_locked") and is_base:
                id_item.setForeground(QColor("#777777"))
                desc_item.setForeground(QColor("#777777"))
                desc_item.setToolTip(
                    "DLC-Only Mode is active. Base game AppID cannot be added to AdditionalApps."
                )
                self.apps_table.setItem(row, 0, id_item)
                self.apps_table.setItem(row, 1, desc_item)
                toggle = MiniSwitchToggle(
                    checked=False, read_only=True, greyscaled=True
                )
                toggle.setEnabled(False)
                toggle.setToolTip(
                    "DLC-Only Mode is active. Base game AppID cannot be added to AdditionalApps."
                )
                self.app_toggles[aid] = toggle
                self.apps_table.setCellWidget(row, 2, _ToggleContainer(toggle))
                self.desired_apps[aid] = False
                continue

            is_owned_app = bool(
                item.get("is_owned") or item.get("is_owned_base")
            )
            if is_owned_app and not item.get("is_unreleased"):
                id_item.setForeground(QColor("#777777"))
                desc_item.setForeground(QColor("#777777"))
                desc_item.setToolTip(
                    "ASSella suspects this item is owned on Steam. Toggle only if you do not own it on Steam."
                )

            if item.get("is_unreleased"):
                id_item.setForeground(QColor("#777777"))
                desc_item.setForeground(QColor("#777777"))
                f_desc = desc_item.font()
                f_desc.setItalic(True)
                desc_item.setFont(f_desc)
                desc_item.setToolTip(
                    "DLC is unreleased on Steam (prerelease). Cannot be enabled."
                )
                self.apps_table.setItem(row, 0, id_item)
                self.apps_table.setItem(row, 1, desc_item)
                toggle = MiniSwitchToggle(
                    checked=False, read_only=True, greyscaled=True
                )
                toggle.setEnabled(False)
                toggle.setToolTip(
                    "DLC is unreleased on Steam (prerelease). Cannot be enabled."
                )
                self.app_toggles[aid] = toggle
                self.apps_table.setCellWidget(row, 2, _ToggleContainer(toggle))
                self.desired_apps[aid] = False
                continue

            self.apps_table.setItem(row, 0, id_item)
            self.apps_table.setItem(row, 1, desc_item)

            current_checked = self.desired_apps.get(aid, is_live)
            toggle = MiniSwitchToggle(
                checked=current_checked, read_only=(not self.is_unlocked)
            )
            if is_owned_app:
                toggle.setToolTip(
                    "ASSella suspects this item is owned on Steam. Enabling requires confirmation."
                )
            self.app_toggles[aid] = toggle

            toggle.toggled.connect(
                partial(self._on_app_row_toggled, aid, is_owned_app)
            )
            self.apps_table.setCellWidget(row, 2, _ToggleContainer(toggle))

        _set_table_height(self.apps_table, len(apps))
        self.apps_bulk_bar.setVisible(len(apps) >= 5)
        self.apps_bulk_toggle.setReadOnly(not self.is_unlocked)

    def _populate_depots_table(self, state: Dict[str, Any]) -> None:
        depots = state["depots"]
        live_depots = state["live_depots"]

        self.depots_table.setRowCount(len(depots))
        for row, item in enumerate(depots):
            did = item["id"]
            is_live = did in live_depots
            is_unrel = bool(item.get("is_unreleased"))
            is_owned_row = bool(item.get("is_owned")) and not is_unrel
            is_missing_key = bool(item.get("is_missing", False))
            is_locked_row = is_missing_key or is_unrel

            if is_unrel and not item.get("missing_tag"):
                item = dict(
                    item,
                    missing_status=STATUS_UNRELEASED,
                    missing_tag=UNRELEASED_TAG,
                    missing_tooltip=unreleased_tooltip(did),
                )
            elif is_owned_row and not is_missing_key:
                item = dict(
                    item,
                    missing_status="owned",
                    missing_tag="[Steam Owned]",
                    missing_tooltip=(
                        f"ASSella suspects depot {did} belongs to content you own on Steam. "
                        f"Toggle only if you do not own it on Steam."
                    ),
                )

            is_muted = is_locked_row or is_owned_row
            id_item = QTableWidgetItem(did)
            id_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            if is_muted:
                id_item.setForeground(QColor(130, 130, 130))
                font_id = id_item.font()
                font_id.setItalic(True)
                id_item.setFont(font_id)
                id_item.setToolTip(
                    item.get("missing_tooltip")
                    or f"Depot {did} key is missing / awaiting Hubcap."
                )

            desc_text = item["desc"]
            if item.get("is_shared"):
                desc_text = f"{desc_text} [Shared]"
            if is_muted and item.get("missing_tag"):
                desc_text = f"{item['missing_tag']}  {desc_text}"

            desc_item = QTableWidgetItem(desc_text)
            desc_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            if item.get("os_icon"):
                desc_item.setIcon(item["os_icon"])
            if is_muted:
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
                    tooltip_parts.append(
                        "Shared with another game / protected from removal"
                    )
                desc_item.setToolTip("\n".join(tooltip_parts))

            self.depots_table.setItem(row, 0, id_item)
            self.depots_table.setItem(row, 1, desc_item)

            if is_locked_row:
                toggle = MiniSwitchToggle(
                    checked=False, read_only=True, greyscaled=True
                )
                toggle.setEnabled(False)
                toggle.setToolTip(
                    item.get("missing_tooltip")
                    or "Depot key is not available — cannot enable in AdditionalDepots."
                )
                self.depot_toggles[did] = toggle
                self.depots_table.setCellWidget(row, 2, _ToggleContainer(toggle))
            else:
                current_checked = self.desired_depots.get(did, is_live)
                toggle = MiniSwitchToggle(
                    checked=current_checked, read_only=(not self.is_unlocked)
                )
                if is_owned_row:
                    toggle.setToolTip(
                        item.get("missing_tooltip")
                        or "ASSella suspects this depot is owned on Steam."
                    )
                self.depot_toggles[did] = toggle

                toggle.toggled.connect(
                    partial(
                        self._on_depot_row_toggled,
                        did,
                        bool(item.get("is_shared")),
                        is_owned_row,
                    )
                )
                self.depots_table.setCellWidget(row, 2, _ToggleContainer(toggle))

        _set_table_height(self.depots_table, len(depots))
        self.depots_bulk_bar.setVisible(len(depots) >= 5)
        self.depots_bulk_toggle.setReadOnly(not self.is_unlocked)

    def _populate_keys_table(self, state: Dict[str, Any]) -> None:
        keys = state["keys"]
        game_name = (
            state.get("game_name")
            or self.dialog.game_data.get("game_name")
            or f"App {self.appid_str}"
        )

        self.keys_table.setRowCount(len(keys))
        for row, item in enumerate(keys):
            kid = item["id"]
            is_missing = item.get("is_missing", False)

            id_item = QTableWidgetItem(kid)
            id_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            if is_missing:
                id_item.setForeground(QColor(130, 130, 130))
                font_id = id_item.font()
                font_id.setItalic(True)
                id_item.setFont(font_id)

            matching_desc = next(
                (d["desc"] for d in state["depots"] if d["id"] == kid), None
            )
            if not matching_desc and (
                kid == self.appid_str or item.get("is_appkey")
            ):
                matching_desc = f"{game_name} (AppKey)"
            elif not matching_desc:
                matching_app = next(
                    (
                        a["desc"]
                        for a in state.get("apps", [])
                        if a["id"] == kid
                    ),
                    None,
                )
                if matching_app:
                    matching_desc = f"{matching_app} (Key)"
            if matching_desc:
                id_item.setToolTip(matching_desc)
            self.keys_table.setItem(row, 0, id_item)

            if is_missing:
                key_text = item.get("missing_tag", "[No Key]")
                key_item = QTableWidgetItem(key_text)
                key_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
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
                key_item.setToolTip(
                    item.get("missing_tooltip")
                    or "No AES decryption key available."
                )
                self.keys_table.setItem(row, 1, key_item)

                toggle = MiniSwitchToggle(
                    checked=False, read_only=True, greyscaled=True
                )
                toggle.setEnabled(False)
                toggle.setToolTip(
                    item.get("missing_tooltip")
                    or "Decryption key not available — cannot enable."
                )
                self.key_toggles[kid] = toggle
                self.keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                loc_ind = _make_local_indicator(in_local=False, is_missing=True)
                self.keys_table.setCellWidget(row, 3, loc_ind)
            elif item.get("is_unreleased"):
                raw_key = item.get("key") or ""
                short_key = (
                    f"{raw_key[:15]}..." if len(raw_key) > 15 else raw_key
                )
                key_item = QTableWidgetItem(
                    f"{UNRELEASED_TAG}  {short_key}".strip()
                )
                key_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                key_item.setForeground(QColor(130, 130, 130))
                f_u = key_item.font()
                f_u.setItalic(True)
                key_item.setFont(f_u)
                lock_tip = unreleased_tooltip(kid)
                key_item.setToolTip(lock_tip)
                self.keys_table.setItem(row, 1, key_item)

                toggle = MiniSwitchToggle(
                    checked=bool(item.get("in_config")),
                    read_only=True,
                    greyscaled=True,
                )
                toggle.setEnabled(False)
                toggle.setToolTip(lock_tip)
                self.key_toggles[kid] = toggle
                self.keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                loc_ind = _make_local_indicator(
                    in_local=bool(item.get("in_local")), is_missing=False
                )
                self.keys_table.setCellWidget(row, 3, loc_ind)
            elif item.get("is_dlc_base_locked") or (
                item.get("is_base_owned") and not item.get("key")
            ):
                locked_tip = (
                    "DLC-Only Mode is active. Base AppKey is not injected."
                    if item.get("is_dlc_base_locked")
                    else "Base game is owned legitimately on Steam and has no AppKey available."
                )
                key_item = QTableWidgetItem(
                    "[Steam Owned]"
                    if item.get("is_base_owned")
                    else "[No Key]"
                )
                key_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                key_item.setForeground(QColor("#777777"))
                key_item.setToolTip(locked_tip)
                self.keys_table.setItem(row, 1, key_item)

                toggle = MiniSwitchToggle(
                    checked=False, read_only=True, greyscaled=True
                )
                toggle.setEnabled(False)
                toggle.setToolTip(locked_tip)
                self.key_toggles[kid] = toggle
                self.keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                loc_ind = _make_local_indicator(
                    in_local=bool(item.get("in_local")), is_missing=False
                )
                self.keys_table.setCellWidget(row, 3, loc_ind)
            elif item.get("is_pure_license"):
                key_item = QTableWidgetItem("[License Only]")
                key_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                key_item.setForeground(QColor(234, 179, 8))
                f = key_item.font()
                f.setItalic(True)
                key_item.setFont(f)
                key_item.setToolTip(
                    "Entitlement is granted via AdditionalApps / DlcData. No depot files or AES key required."
                )
                self.keys_table.setItem(row, 1, key_item)

                toggle = MiniSwitchToggle(
                    checked=False, read_only=True, greyscaled=True
                )
                toggle.setEnabled(False)
                toggle.setToolTip(
                    "Entitlement is granted via AdditionalApps / DlcData. No depot files or AES key required."
                )
                self.key_toggles[kid] = toggle
                self.keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                loc_ind = _make_local_indicator(
                    in_local=False, is_missing=False
                )
                self.keys_table.setCellWidget(row, 3, loc_ind)
            else:
                raw_key = item["key"]
                is_owned_key = bool(
                    item.get("is_owned") or item.get("is_base_owned")
                )
                short_key = (
                    f"{raw_key[:15]}..."
                    if len(raw_key) > 15
                    else (raw_key or "N/A")
                )
                display_key = (
                    f"[Steam Owned]  {short_key}".strip()
                    if is_owned_key
                    else short_key
                )
                key_item = QTableWidgetItem(display_key)
                key_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                if is_owned_key:
                    key_item.setForeground(QColor(130, 130, 130))
                    f_o = key_item.font()
                    f_o.setItalic(True)
                    key_item.setFont(f_o)
                    owned_tip = (
                        f"ASSella suspects {kid} belongs to content you own on Steam. "
                        f"Toggle only if you do not own it on Steam."
                    )
                    key_item.setToolTip(
                        f"{owned_tip}\n{raw_key}" if raw_key else owned_tip
                    )
                elif raw_key:
                    key_item.setToolTip(raw_key)
                self.keys_table.setItem(row, 1, key_item)

                is_cfg = item["in_config"]
                current_checked = self.desired_keys.get(kid, is_cfg)
                toggle = MiniSwitchToggle(
                    checked=current_checked, read_only=(not self.is_unlocked)
                )
                if is_owned_key:
                    toggle.setToolTip(
                        "ASSella suspects this key belongs to content you own on Steam."
                    )
                self.key_toggles[kid] = toggle

                toggle.toggled.connect(
                    partial(self._on_key_row_toggled, kid, is_owned_key)
                )
                self.keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                is_loc = item["in_local"]
                loc_ind = _make_local_indicator(
                    in_local=is_loc, is_missing=False
                )
                self.keys_table.setCellWidget(row, 3, loc_ind)

        _set_table_height(self.keys_table, len(keys))
        self.keys_bulk_bar.setVisible(len(keys) >= 5)
        self.keys_bulk_toggle.setReadOnly(not self.is_unlocked)

    # ------------------------------------------------------------------
    # Row Toggle Callbacks & Status Synchronization
    # ------------------------------------------------------------------

    def _on_app_row_toggled(
        self, app_id: str, owned_item: bool, checked: bool
    ) -> None:
        state = self.current_state
        assoc_depots = self.app_to_depots.get(app_id, set())
        unreleased = state.get("unreleased_ids", set())
        if checked:
            has_owned_cascade = any(
                did not in unreleased
                and did in state.get("owned_ids", set())
                and (
                    (
                        did in self.depot_toggles
                        and not self.desired_depots.get(did, False)
                    )
                    or (
                        did in self.key_toggles
                        and not self.desired_keys.get(did, False)
                    )
                )
                for did in assoc_depots
            )
            if not self._confirm_owned_action(
                self.app_toggles.get(app_id), owned_item or has_owned_cascade
            ):
                self.desired_apps[app_id] = False
                self._update_sync_button_label(state)
                self._update_bulk_states(state)
                return

        self.desired_apps[app_id] = checked

        # ONE-WAY DYNAMIC CASCADE:
        # Enabling/disabling an app/DLC automatically cascades to its matching key & depots
        self._suppress_owned_confirm = True
        try:
            if checked:
                if app_id in self.key_toggles and not self.desired_keys.get(
                    app_id, False
                ):
                    key_meta = next(
                        (k for k in state["keys"] if k["id"] == app_id), None
                    )
                    if (
                        key_meta
                        and key_meta.get("key")
                        and not key_meta.get("is_dlc_base_locked")
                    ):
                        self.desired_keys[app_id] = True
                        self.key_toggles[app_id].setChecked(True, animate=True)

                for did in assoc_depots:
                    if did in unreleased:
                        continue
                    if (
                        did in self.depot_toggles
                        and not self.desired_depots.get(did, False)
                    ):
                        self.desired_depots[did] = True
                        self.depot_toggles[did].setChecked(True, animate=True)
                    if did in self.key_toggles and not self.desired_keys.get(
                        did, False
                    ):
                        d_key_meta = next(
                            (k for k in state["keys"] if k["id"] == did), None
                        )
                        if (
                            d_key_meta
                            and d_key_meta.get("key")
                            and not d_key_meta.get("is_dlc_base_locked")
                        ):
                            self.desired_keys[did] = True
                            self.key_toggles[did].setChecked(
                                True, animate=True
                            )
            else:
                if app_id in self.key_toggles and self.desired_keys.get(
                    app_id
                ):
                    self.desired_keys[app_id] = False
                    self.key_toggles[app_id].setChecked(False, animate=True)

                for did in assoc_depots:
                    if did in self.depot_toggles and self.desired_depots.get(
                        did
                    ):
                        self.desired_depots[did] = False
                        self.depot_toggles[did].setChecked(
                            False, animate=True
                        )
                    if did in self.key_toggles and self.desired_keys.get(did):
                        self.desired_keys[did] = False
                        self.key_toggles[did].setChecked(False, animate=True)
        finally:
            self._suppress_owned_confirm = False

        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        self.status_lbl.setText(
            f"App {app_id} {'enabled (cascaded to depots/keys)' if checked else 'disabled (cascaded to depots/keys)'}. Click 'Sync Changes' to apply."
        )

    def _on_depot_row_toggled(
        self, depot_id: str, is_shared: bool, owned_item: bool, checked: bool
    ) -> None:
        state = self.current_state
        if checked and not self._confirm_owned_action(
            self.depot_toggles.get(depot_id), owned_item
        ):
            self.desired_depots[depot_id] = False
            self._update_sync_button_label(state)
            self._update_bulk_states(state)
            return
        self.desired_depots[depot_id] = checked
        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        if not checked and is_shared:
            self.status_lbl.setText(
                f"Depot {depot_id} is shared with other games. It will remain protected in config."
            )
        else:
            self.status_lbl.setText(
                f"Depot {depot_id} {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply."
            )

    def _on_key_row_toggled(
        self, key_id: str, owned_item: bool, checked: bool
    ) -> None:
        state = self.current_state
        if checked and not self._confirm_owned_action(
            self.key_toggles.get(key_id), owned_item
        ):
            self.desired_keys[key_id] = False
            self._update_sync_button_label(state)
            self._update_bulk_states(state)
            return
        self.desired_keys[key_id] = checked
        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        self.status_lbl.setText(
            f"Key {key_id} {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply."
        )

    def _update_sync_button_label(self, state: Dict[str, Any]) -> None:
        """Light up Save only when something differs from the live config, and
        flag Refetch when Hubcap has keys this game is missing."""
        n_changes = sum(
            1
            for item in state["apps"]
            if item["id"] in self.desired_apps
            and self.desired_apps[item["id"]]
            != (item["id"] in state["live_apps"])
        )
        n_changes += sum(
            1
            for item in state["depots"]
            if item["id"] in self.desired_depots
            and self.desired_depots[item["id"]]
            != (item["id"] in state["live_depots"])
        )
        n_changes += sum(
            1
            for item in state["keys"]
            if item["id"] in self.desired_keys
            and self.desired_keys[item["id"]] != item["in_config"]
        )
        is_dirty = n_changes > 0

        ready = {
            str(item["id"])
            for item in (state["depots"] + state["keys"])
            if str(item.get("missing_tag") or "") == "[Key Ready]"
        }
        if self.refetch_btn.isEnabled() or not self.refetch_btn.toolTip().startswith(
            ("Daily", "API token")
        ):
            if ready:
                self.refetch_btn.setText(
                    f"Refetch keys \u00b7 {len(ready)} ready"
                )
                self.refetch_btn.setStyleSheet(self._BTN_ATTENTION_STYLE)
            else:
                self.refetch_btn.setText("Refetch keys")
                self.refetch_btn.setStyleSheet(self._BTN_BASE_STYLE)

        if self.is_dlc_setup:
            n_sel = sum(
                1
                for a in state["apps"]
                if not a.get("is_base")
                and not a.get("is_unreleased")
                and self.desired_apps.get(a["id"], False)
            )
            self.sync_btn.setText(
                f"Register {n_sel} DLC{'s' if n_sel != 1 else ''}"
            )
            self.sync_btn.setEnabled(n_sel > 0 or is_dirty)
        else:
            self.sync_btn.setText(
                f"Save {n_changes} change{'s' if n_changes != 1 else ''}"
                if is_dirty
                else "Save"
            )
            self.sync_btn.setEnabled(is_dirty)

    def _update_bulk_states(self, state: Dict[str, Any]) -> None:
        """Synchronize the checked state of the bulk Select All / None toggles."""
        self._internal_bulk_update = True
        try:
            apps = state["apps"]
            valid_apps = [
                a
                for a in apps
                if not a.get("is_unreleased")
                and not a.get("is_dlc_base_locked")
            ]
            if len(apps) >= 5:
                all_apps_on = bool(valid_apps) and all(
                    self.desired_apps.get(item["id"], False)
                    for item in valid_apps
                )
                self.apps_bulk_toggle.setChecked(all_apps_on, animate=False)

            depots = state["depots"]
            valid_depots = [
                d
                for d in depots
                if not d.get("is_missing") and not d.get("is_unreleased")
            ]
            if len(depots) >= 5:
                all_depots_on = bool(valid_depots) and all(
                    self.desired_depots.get(item["id"], False)
                    for item in valid_depots
                )
                self.depots_bulk_toggle.setChecked(
                    all_depots_on, animate=False
                )

            keys = state["keys"]
            valid_keys = [
                k
                for k in keys
                if k.get("key")
                and not k.get("is_missing")
                and not k.get("is_dlc_base_locked")
                and not k.get("is_pure_license")
                and not k.get("is_unreleased")
            ]
            if len(keys) >= 5:
                all_keys_on = bool(valid_keys) and all(
                    self.desired_keys.get(item["id"], False)
                    for item in valid_keys
                )
                self.keys_bulk_toggle.setChecked(all_keys_on, animate=False)
        finally:
            self._internal_bulk_update = False

    # ------------------------------------------------------------------
    # 3. Bulk Action Handlers
    # ------------------------------------------------------------------

    def _on_bulk_toggle_apps(self, checked: bool) -> None:
        if self._internal_bulk_update or not self.is_unlocked:
            return
        state = self.current_state or self._collect_state()
        app_to_depots = self.app_to_depots or self._build_app_to_depots_map(
            state["apps"], state.get("depots")
        )
        unreleased = state.get("unreleased_ids", set())
        if checked:
            needs_confirm = False
            for item in state["apps"]:
                aid = item["id"]
                if item.get("is_unreleased") or item.get("is_dlc_base_locked"):
                    continue
                if (
                    item.get("is_owned") or item.get("is_owned_base")
                ) and not self.desired_apps.get(aid, False):
                    needs_confirm = True
                    break
                for did in app_to_depots.get(aid, set()):
                    if did in unreleased or did not in state.get(
                        "owned_ids", set()
                    ):
                        continue
                    d_meta = next(
                        (d for d in state["depots"] if d["id"] == did), None
                    )
                    k_meta = next(
                        (k for k in state["keys"] if k["id"] == did), None
                    )
                    will_dep = (
                        did in self.depot_toggles
                        and not self.desired_depots.get(did, False)
                        and (not d_meta or not d_meta.get("is_missing"))
                    )
                    will_key = (
                        did in self.key_toggles
                        and not self.desired_keys.get(did, False)
                        and k_meta
                        and k_meta.get("key")
                        and not k_meta.get("is_missing")
                    )
                    if will_dep or will_key:
                        needs_confirm = True
                        break
                if needs_confirm:
                    break
            if needs_confirm and not confirm_steam_owned_override(self.dialog):
                self._set_switch_silently(self.apps_bulk_toggle, False)
                return

        self._suppress_owned_confirm = True
        try:
            for item in state["apps"]:
                aid = item["id"]
                if item.get("is_unreleased") or item.get("is_dlc_base_locked"):
                    continue
                self.desired_apps[aid] = checked
                if aid in self.app_toggles:
                    self.app_toggles[aid].setChecked(checked, animate=True)

                assoc_depots = app_to_depots.get(aid, set())
                if checked:
                    if aid in self.key_toggles and not self.desired_keys.get(
                        aid, False
                    ):
                        key_meta = next(
                            (k for k in state["keys"] if k["id"] == aid), None
                        )
                        if (
                            key_meta
                            and key_meta.get("key")
                            and not key_meta.get("is_missing")
                            and not key_meta.get("is_dlc_base_locked")
                        ):
                            self.desired_keys[aid] = True
                            self.key_toggles[aid].setChecked(
                                True, animate=True
                            )

                    for did in assoc_depots:
                        if did in unreleased:
                            continue
                        d_meta = next(
                            (d for d in state["depots"] if d["id"] == did),
                            None,
                        )
                        if (
                            did in self.depot_toggles
                            and not self.desired_depots.get(did, False)
                        ):
                            if not d_meta or not d_meta.get("is_missing"):
                                self.desired_depots[did] = True
                                self.depot_toggles[did].setChecked(
                                    True, animate=True
                                )
                        if (
                            did in self.key_toggles
                            and not self.desired_keys.get(did, False)
                        ):
                            d_key_meta = next(
                                (k for k in state["keys"] if k["id"] == did),
                                None,
                            )
                            if (
                                d_key_meta
                                and d_key_meta.get("key")
                                and not d_key_meta.get("is_missing")
                            ):
                                self.desired_keys[did] = True
                                self.key_toggles[did].setChecked(
                                    True, animate=True
                                )
                else:
                    if aid in self.key_toggles and self.desired_keys.get(aid):
                        self.desired_keys[aid] = False
                        self.key_toggles[aid].setChecked(False, animate=True)
                    for did in assoc_depots:
                        if (
                            did in self.depot_toggles
                            and self.desired_depots.get(did)
                        ):
                            self.desired_depots[did] = False
                            self.depot_toggles[did].setChecked(
                                False, animate=True
                            )
                        if did in self.key_toggles and self.desired_keys.get(
                            did
                        ):
                            self.desired_keys[did] = False
                            self.key_toggles[did].setChecked(
                                False, animate=True
                            )
        finally:
            self._suppress_owned_confirm = False

        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        self.status_lbl.setText(
            f"All AppIDs {'enabled (cascaded)' if checked else 'disabled (cascaded)'}. Click 'Sync Changes' to apply."
        )

    def _on_bulk_toggle_depots(self, checked: bool) -> None:
        if self._internal_bulk_update or not self.is_unlocked:
            return
        state = self.current_state or self._collect_state()
        if checked:
            needs_confirm = any(
                item.get("is_owned")
                and not item.get("is_unreleased")
                and not item.get("is_missing")
                and not self.desired_depots.get(item["id"], False)
                for item in state["depots"]
            )
            if needs_confirm and not confirm_steam_owned_override(self.dialog):
                self._set_switch_silently(self.depots_bulk_toggle, False)
                return

        self._suppress_owned_confirm = True
        try:
            for item in state["depots"]:
                if item.get("is_unreleased") or item.get("is_missing"):
                    continue
                did = item["id"]
                self.desired_depots[did] = checked
                if did in self.depot_toggles:
                    self.depot_toggles[did].setChecked(checked, animate=True)
        finally:
            self._suppress_owned_confirm = False

        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        self.status_lbl.setText(
            f"All Depots {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply."
        )

    def _on_bulk_toggle_keys(self, checked: bool) -> None:
        if self._internal_bulk_update or not self.is_unlocked:
            return
        state = self.current_state or self._collect_state()
        if checked:
            needs_confirm = any(
                (item.get("is_owned") or item.get("is_base_owned"))
                and item.get("key")
                and not item.get("is_missing")
                and not item.get("is_unreleased")
                and not item.get("is_dlc_base_locked")
                and not item.get("is_pure_license")
                and not self.desired_keys.get(item["id"], False)
                for item in state["keys"]
            )
            if needs_confirm and not confirm_steam_owned_override(self.dialog):
                self._set_switch_silently(self.keys_bulk_toggle, False)
                return

        self._suppress_owned_confirm = True
        try:
            for item in state["keys"]:
                kid = item["id"]
                if (
                    item.get("is_unreleased")
                    or item.get("is_dlc_base_locked")
                    or item.get("is_pure_license")
                    or not item.get("key")
                    or item.get("is_missing")
                ):
                    continue
                self.desired_keys[kid] = checked
                if kid in self.key_toggles:
                    self.key_toggles[kid].setChecked(checked, animate=True)
        finally:
            self._suppress_owned_confirm = False

        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        self.status_lbl.setText(
            f"All Keys {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply."
        )

    def _on_bulk_toggle_all(self, checked: bool) -> None:
        if self._internal_bulk_update or not self.is_unlocked:
            return
        state = self.current_state or self._collect_state()
        if checked:
            needs_confirm = (
                any(
                    (item.get("is_owned") or item.get("is_owned_base"))
                    and not item.get("is_unreleased")
                    and not item.get("is_dlc_base_locked")
                    and not self.desired_apps.get(item["id"], False)
                    for item in state["apps"]
                )
                or any(
                    item.get("is_owned")
                    and not item.get("is_unreleased")
                    and not item.get("is_missing")
                    and not self.desired_depots.get(item["id"], False)
                    for item in state["depots"]
                )
                or any(
                    (item.get("is_owned") or item.get("is_base_owned"))
                    and item.get("key")
                    and not item.get("is_missing")
                    and not item.get("is_unreleased")
                    and not item.get("is_dlc_base_locked")
                    and not item.get("is_pure_license")
                    and not self.desired_keys.get(item["id"], False)
                    for item in state["keys"]
                )
            )
            if needs_confirm and not confirm_steam_owned_override(self.dialog):
                return

        self._suppress_owned_confirm = True
        try:
            for item in state["apps"]:
                aid = item["id"]
                if item.get("is_unreleased") or item.get("is_dlc_base_locked"):
                    continue
                self.desired_apps[aid] = checked
                if aid in self.app_toggles:
                    self.app_toggles[aid].setChecked(checked, animate=True)

            for item in state["depots"]:
                if item.get("is_unreleased") or item.get("is_missing"):
                    continue
                did = item["id"]
                self.desired_depots[did] = checked
                if did in self.depot_toggles:
                    self.depot_toggles[did].setChecked(checked, animate=True)

            for item in state["keys"]:
                kid = item["id"]
                if (
                    item.get("is_unreleased")
                    or item.get("is_dlc_base_locked")
                    or item.get("is_pure_license")
                    or not item.get("key")
                    or item.get("is_missing")
                ):
                    continue
                self.desired_keys[kid] = checked
                if kid in self.key_toggles:
                    self.key_toggles[kid].setChecked(checked, animate=True)
        finally:
            self._suppress_owned_confirm = False

        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        self.status_lbl.setText(
            f"All sections {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply."
        )

    def _on_unlock(self) -> None:
        """Unlock custom depot and configuration selection after confirmation dialog."""
        if not self.is_unlocked:
            reply = ThemedMessageBox.warning(
                self.dialog,
                "Advanced Configuration",
                "Modifying depot and configuration entries is intended for advanced users only.\n\n"
                "Excluding required depots or keys may prevent Steam from downloading or launching the game properly.\n\n"
                "Do you want to unlock custom configuration editing?",
                ThemedMessageBox.StandardButton.Yes
                | ThemedMessageBox.StandardButton.No,
                ThemedMessageBox.StandardButton.No,
            )
            if reply == ThemedMessageBox.StandardButton.Yes:
                self.is_unlocked = True
                self._sync_mode_seg()
                self.status_lbl.setText(
                    "Editing on. Change the switches, then Save."
                )
                self._refresh_ui()
            else:
                self._sync_mode_seg()
        else:
            self.is_unlocked = False
            self._sync_mode_seg()
            self.status_lbl.setText("")
            self._refresh_ui()

    # ------------------------------------------------------------------
    # 4. Asynchronous Background Enrichment, Quota & Refetch
    # ------------------------------------------------------------------

    def _init_quota_checks(self) -> None:
        """Non-blocking check of Hubcap token quota (instant cached read + async background refresh)."""
        try:
            from utils.settings import get_settings

            cached_stats = get_settings().value(
                "last_cached_user_stats", None
            )
            if isinstance(cached_stats, dict) and cached_stats:
                init_rem = max(
                    0,
                    (cached_stats.get("daily_limit") or 135)
                    - cached_stats.get("daily_usage", 0),
                )
                if init_rem <= 0 or not cached_stats.get(
                    "can_make_requests", True
                ):
                    self.refetch_btn.setEnabled(False)
                    self.refetch_btn.setToolTip(
                        "Daily Hubcap API token limit reached."
                    )
        except Exception:
            pass

        threading.Thread(
            target=self._check_hubcap_quota_async, daemon=True
        ).start()

    def _check_hubcap_quota_async(self) -> None:
        try:
            from core import morrenus_api
            from utils.main_thread import post_to_main_thread

            st = morrenus_api.get_user_stats()
            if isinstance(st, dict) and st:
                rem = max(
                    0,
                    (st.get("daily_limit") or 135)
                    - st.get("daily_usage", 0),
                )
                if rem <= 0 or not st.get("can_make_requests", True):
                    post_to_main_thread(self._disable_refetch_due_to_quota)
        except Exception:
            pass

    def _disable_refetch_due_to_quota(self) -> None:
        if self._is_destroyed():
            return
        self.refetch_btn.setEnabled(False)
        self.refetch_btn.setToolTip("Daily Hubcap API token limit reached.")

    def _schedule_background_tasks(self) -> None:
        """Schedule background Steam release state verification and depot enrichment."""
        try:
            from managers.db_manager import DatabaseManager

            _enr = (
                DatabaseManager().get_depot_enrichments(self.appid_str) or {}
            )
            _state0 = self.current_state or self._collect_state()
            _dlc_names = {
                a["id"]: a["desc"]
                for a in _state0["apps"]
                if not a.get("is_base")
            }
            _unverified = [
                d
                for d in _dlc_names
                if not (_enr.get(d) or {}).get("release_src")
            ]
            if _unverified:
                if self.is_dlc_setup:
                    self.status_lbl.setText(
                        f"Checking Steam release state for {len(_unverified)} DLC(s)..."
                    )
                threading.Thread(
                    target=self._verify_release_states,
                    args=(_unverified, _dlc_names),
                    daemon=True,
                ).start()
        except Exception as e:
            logger.debug(
                f"[DepotsTab] Could not schedule release state verification: {e}"
            )

        initial_state = self.current_state or self._collect_state()
        needs_enrichment = any(
            bool(
                re.match(
                    r"^(?:\[.*?\]\s*)?Depot\s+\d+$",
                    item.get("desc", ""),
                    re.IGNORECASE,
                )
            )
            for item in initial_state.get("depots", [])
        )
        if needs_enrichment:
            threading.Thread(
                target=self._run_background_enrichment, daemon=True
            ).start()

    def _on_enrichment_ready(self) -> None:
        if self._is_destroyed():
            return
        self._refresh_ui()
        if self.is_dlc_setup:
            self.status_lbl.setText(
                "Pick the DLCs to add. Steam-owned items require confirmation; unreleased items are greyed out and left untouched."
            )

    def _verify_release_states(
        self, dlc_ids: List[str], names: Dict[str, str]
    ) -> None:
        try:
            from core.steam_api import resolve_dlc_release_states
            from managers.db_manager import DatabaseManager

            states = resolve_dlc_release_states(dlc_ids)
            if states:
                DatabaseManager().update_release_states(
                    self.appid_str, states, names
                )
                self._enrichment_bridge.enriched.emit()
        except Exception as e:
            logger.debug(
                f"[DepotsTab] Release state verification failed: {e}"
            )

    def _run_background_enrichment(self) -> None:
        try:
            from core.steam_api import get_depot_info_from_api
            from core.steamdb_scraper import SteamDBScraper
            from managers.db_manager import DatabaseManager

            db = DatabaseManager()
            new_enrichments: Dict[str, dict] = {}

            # 1. Query SteamCMD / Steam PICS
            api_info = get_depot_info_from_api(
                int(self.appid_str)
                if self.appid_str.isdigit()
                else self.appid_str
            )
            if (
                api_info
                and isinstance(api_info, dict)
                and api_info.get("depots")
            ):
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
                sdb_depots = scraper.get_app_depots(self.appid_str)
                if sdb_depots:
                    for did, sdata in sdb_depots.items():
                        if did in new_enrichments:
                            if sdata.get("name"):
                                new_enrichments[did]["name"] = sdata["name"]
                            if sdata.get("oslist"):
                                new_enrichments[did]["oslist"] = sdata[
                                    "oslist"
                                ]
                            if sdata.get("size_str"):
                                new_enrichments[did]["size_str"] = sdata[
                                    "size_str"
                                ]
                        else:
                            new_enrichments[did] = sdata
            except Exception as e:
                logger.debug(f"[DepotsTab] SteamDB enrichment failed: {e}")

            if new_enrichments:
                db.save_depot_enrichments(self.appid_str, new_enrichments)
                self._enrichment_bridge.enriched.emit()
        except Exception as e:
            logger.debug(
                f"[DepotsTab] Background depot enrichment error: {e}"
            )

    def _on_refetch(self) -> None:
        """Refetch fresh Lua metadata and keys from Hubcap API."""
        from core import morrenus_api

        stats = morrenus_api.get_user_stats()
        daily_limit = (
            stats.get("daily_limit") or stats.get("role_daily_limit") or 135
        )
        daily_usage = stats.get("daily_usage", 0)
        can_make_requests = stats.get("can_make_requests", True)
        remaining = max(0, daily_limit - daily_usage)

        if not can_make_requests or remaining <= 0:
            ThemedMessageBox.warning(
                self.dialog,
                "API Token Limit Reached",
                f"Daily Hubcap API token limit reached (0/{daily_limit} tokens remaining).\n\n"
                f"Please wait for your daily quota to reset, or change your ISP bypass settings in Settings.",
            )
            self.refetch_btn.setEnabled(False)
            self.status_lbl.setText(
                f"API token limit reached (0/{daily_limit}). Please wait for daily reset."
            )
            return

        reply = ThemedMessageBox.question(
            self.dialog,
            "Refetch Keys",
            f"This action will consume your daily Hubcap API tokens.\n\n"
            f"You have {remaining}/{daily_limit} tokens remaining.\n\n"
            f"Do you want to continue?",
            ThemedMessageBox.StandardButton.Yes
            | ThemedMessageBox.StandardButton.Cancel,
            ThemedMessageBox.StandardButton.Cancel,
        )
        if reply != ThemedMessageBox.StandardButton.Yes:
            return

        self.refetch_btn.setEnabled(False)
        self.mode_seg.setEnabled(False)
        self.sync_btn.setEnabled(False)
        self.status_lbl.setText("Refetching metadata from Hubcap...")

        self._worker_bridge = _WorkerBridge(self)
        self._worker_bridge.finished.connect(self._on_refetch_done)
        threading.Thread(
            target=self._refetch_worker,
            args=(self._worker_bridge,),
            daemon=True,
        ).start()

    def _refetch_worker(self, bridge: _WorkerBridge) -> None:
        from core import morrenus_api

        try:
            zip_path, err = morrenus_api.download_manifest(
                self.appid_str, force_update=True
            )
            if zip_path and os.path.exists(zip_path):
                with zipfile.ZipFile(zip_path, "r") as zf:
                    all_discovered_depots = set()
                    for fname in zf.namelist():
                        if fname.endswith(".manifest"):
                            bname = os.path.basename(fname)
                            parts = bname.split("_")
                            if parts and parts[0].isdigit():
                                all_discovered_depots.add(parts[0])

                    lua_files = [
                        f for f in zf.namelist() if f.endswith(".lua")
                    ]
                    if lua_files:
                        from utils.helpers import get_base_path

                        dest_lua = (
                            Path(get_base_path())
                            / "cached_luas"
                            / f"{self.appid_str}.lua"
                        )
                        dest_lua.parent.mkdir(parents=True, exist_ok=True)
                        lua_data = zf.read(lua_files[0]).decode(
                            "utf-8", errors="ignore"
                        )
                        dest_lua.write_text(lua_data, encoding="utf-8")

                        for m in iter_live_matches(
                            lua_data, r"addappid\((\d+)"
                        ):
                            all_discovered_depots.add(m.group(1))

                        fresh_keys = {}
                        for m in iter_live_matches(
                            lua_data,
                            r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)',
                        ):
                            fresh_keys[m.group(1)] = m.group(2).lower()
                        if fresh_keys:
                            from managers.depot_key_manager import (
                                DepotKeyManager,
                            )

                            DepotKeyManager.get_instance().save_depot_keys(
                                self.appid_str, fresh_keys
                            )

                        try:
                            cfg_path = get_user_config_path()
                            lib = load_plugin_library()
                            rec = lib.get(self.appid_str)
                            is_dlc_only = (
                                rec.get("dlc_only", False) if rec else False
                            )
                            game_name = (
                                self.dialog.game_data.get("game_name")
                                or f"App {self.appid_str}"
                            )
                            registered_depots = {
                                str(d)
                                for d in (rec.get("depots", []) if rec else [])
                            }
                            registered_dlc_apps = {
                                str(d)
                                for d in (
                                    rec.get("dlc_appids", []) if rec else []
                                )
                            }
                            discovered_dlc_apps = {
                                str(d)
                                for d in (
                                    self.dialog.game_data.get("dlcs") or {}
                                )
                            } | registered_dlc_apps

                            keyed_depots = {
                                d
                                for d in all_discovered_depots
                                if d != self.appid_str
                                and d not in discovered_dlc_apps
                                and d in fresh_keys
                                and d not in SHARED_REDISTS
                                and (
                                    not is_dlc_only or d in registered_depots
                                )
                            }
                            allowed_key_ids = (
                                registered_depots
                                | registered_dlc_apps
                                | {self.appid_str}
                                | SHARED_REDISTS
                                if is_dlc_only
                                else keyed_depots
                                | {self.appid_str}
                                | SHARED_REDISTS
                            )
                            publish_keys = {
                                did: key
                                for did, key in fresh_keys.items()
                                if did in allowed_key_ids
                            }

                            if cfg_path and cfg_path.exists():
                                with batch_config_edit(cfg_path) as editor:
                                    for did, k in publish_keys.items():
                                        editor.add_key(
                                            did,
                                            k,
                                            comment=build_key_comment(
                                                game_name,
                                                did,
                                                "",
                                                self.appid_str,
                                            ),
                                        )
                                    for did in keyed_depots:
                                        if not is_dlc_only:
                                            editor.add_depot(
                                                did,
                                                comment=build_depot_comment(
                                                    game_name,
                                                    did,
                                                    "",
                                                    self.appid_str,
                                                ),
                                                app_id=self.appid_str,
                                            )
                                if editor.has_changes:
                                    SLSBridge.notify_reload()

                            if rec:
                                rec.setdefault("keys", {}).update(publish_keys)
                                if not is_dlc_only:
                                    existing_deps = set(
                                        str(d) for d in rec.get("depots", [])
                                    )
                                    existing_deps.update(keyed_depots)
                                    rec["depots"] = sorted(
                                        list(existing_deps),
                                        key=lambda x: int(x)
                                        if x.isdigit()
                                        else 0,
                                    )
                                rec["updated_at"] = int(time.time())
                                save_plugin_library(lib)

                            if hasattr(self.dialog, "game_data") and isinstance(
                                self.dialog.game_data, dict
                            ):
                                g_deps = set(
                                    str(d)
                                    for d in self.dialog.game_data.get(
                                        "depots", []
                                    )
                                )
                                g_deps.update(keyed_depots)
                                self.dialog.game_data["depots"] = sorted(
                                    list(g_deps),
                                    key=lambda x: int(x) if x.isdigit() else 0,
                                )
                                self.dialog.game_data.setdefault(
                                    "keys", {}
                                ).update(publish_keys)
                        except Exception as sync_err:
                            logger.debug(
                                f"[DepotsTab] Error syncing refetched keys to config: {sync_err}"
                            )

                try:
                    from managers.db_manager import DatabaseManager

                    DatabaseManager().save_at0m_update_status(
                        appid=self.appid_str,
                        status="up_to_date",
                        new_depots=[],
                    )
                except Exception:
                    pass

                bridge.finished.emit(
                    True,
                    "Refetch successful. Local cache and configuration updated.",
                )
            else:
                err_str = str(err or "")
                if "404" in err_str or "not found" in err_str.lower():
                    bridge.finished.emit(
                        False,
                        "Hubcap returned 404 (Manifest not found on server).",
                    )
                elif "429" in err_str or "limit" in err_str.lower():
                    bridge.finished.emit(
                        False, f"Daily API token limit exceeded ({err})."
                    )
                else:
                    bridge.finished.emit(
                        False, f"Refetch failed: {err_str or 'Unknown error'}"
                    )
        except Exception as e:
            err_str = str(e)
            if "404" in err_str or "not found" in err_str.lower():
                bridge.finished.emit(
                    False, "Hubcap returned 404 (Manifest not found)."
                )
            else:
                bridge.finished.emit(False, f"Refetch error: {e}")

    def _on_refetch_done(self, success: bool, msg: str) -> None:
        if self._is_destroyed():
            return
        from core import morrenus_api

        self.refetch_btn.setEnabled(True)
        self.mode_seg.setEnabled(True)
        self.status_lbl.setText(msg)
        try:
            st = morrenus_api.get_user_stats(force=True)
            rem = max(
                0, (st.get("daily_limit") or 135) - st.get("daily_usage", 0)
            )
            if rem <= 0 or not st.get("can_make_requests", True):
                self.refetch_btn.setEnabled(False)
                self.refetch_btn.setToolTip("API token limit reached.")
        except Exception:
            pass
        self._refresh_ui()

    # ------------------------------------------------------------------
    # 5. Persistence via batch_config_edit
    # ------------------------------------------------------------------

    def _on_sync(self) -> None:
        """Sync desired additions and removals specifically for this game into config.yaml."""
        state = self._collect_state()
        self.current_state = state
        cfg_path = get_user_config_path()
        if not cfg_path.exists():
            self.status_lbl.setText("Error: config.yaml not found.")
            return

        ensure_plugins_enabled(cfg_path)
        game_name = (
            self.dialog.game_data.get("game_name") or f"App {self.appid_str}"
        )

        added_count = 0
        removed_count = 0

        selected_dlc_apps = {
            a["id"]: a["desc"]
            for a in state["apps"]
            if self.is_dlc_setup
            and not a.get("is_base")
            and not a.get("is_unreleased")
            and self.desired_apps.get(a["id"], False)
        }
        use_dlc_data = self.is_dlc_setup and len(selected_dlc_apps) >= 64
        dlc_data_pending: Dict[str, str] = {}

        with batch_config_edit(cfg_path) as editor:
            if self.is_unlocked:
                # 1. Apps
                for item in state["apps"]:
                    aid = item["id"]
                    if item.get("is_dlc_base_locked") or item.get(
                        "is_unreleased"
                    ):
                        desired = False
                    else:
                        desired = self.desired_apps.get(
                            aid, aid in state["live_apps"]
                        )
                    if desired and aid not in state["live_apps"]:
                        if use_dlc_data and not item.get("is_base"):
                            dlc_data_pending[aid] = re.sub(
                                r"^(?:\[[^\]]*\]\s*)+", "", item["desc"]
                            ).strip()
                            continue
                        editor.add_app(aid, comment=item["desc"])
                        added_count += 1
                    elif not desired and aid in state["live_apps"]:
                        editor.remove_app(aid)
                        removed_count += 1

                if self.appid_str in state["live_depots"]:
                    logger.warning(
                        f"[DepotsTab] Pruned base AppID '{self.appid_str}' from AdditionalDepots for {game_name}"
                    )
                    editor.remove_depot(self.appid_str, check_shared=False)
                    removed_count += 1

                # 2. Depots
                for item in state["depots"]:
                    did = item["id"]
                    if item.get("is_unreleased"):
                        if (
                            did in state["live_depots"]
                            and did != self.appid_str
                        ):
                            editor.remove_depot(
                                did,
                                check_shared=True,
                                excluding_appid=self.appid_str,
                            )
                            removed_count += 1
                        continue
                    if did == self.appid_str or item.get("is_missing"):
                        continue
                    desired = self.desired_depots.get(
                        did, did in state["live_depots"]
                    )
                    if desired and did not in state["live_depots"]:
                        comment = build_depot_comment(
                            game_name, did, "", self.appid_str
                        )
                        editor.add_depot(
                            did, comment=comment, app_id=self.appid_str
                        )
                        added_count += 1
                    elif not desired and did in state["live_depots"]:
                        editor.remove_depot(
                            did,
                            check_shared=True,
                            excluding_appid=self.appid_str,
                        )
                        removed_count += 1

                # 3. Keys
                for item in state["keys"]:
                    kid = item["id"]
                    if (
                        item.get("is_missing")
                        or not item.get("key")
                        or item.get("is_dlc_base_locked")
                        or item.get("is_pure_license")
                        or item.get("is_unreleased")
                    ):
                        continue
                    desired = self.desired_keys.get(kid, item["in_config"])
                    kval = item["key"]
                    if desired and not item["in_config"]:
                        if kval:
                            comment = build_key_comment(
                                game_name, kid, "", self.appid_str
                            )
                            editor.add_key(kid, kval, comment=comment)
                            added_count += 1
                    elif not desired and item["in_config"]:
                        editor.remove_key(
                            kid,
                            check_shared=True,
                            excluding_appid=self.appid_str,
                        )
                        removed_count += 1
            else:
                if self.appid_str in state["live_depots"]:
                    logger.warning(
                        f"[DepotsTab] Pruned base AppID '{self.appid_str}' from AdditionalDepots for {game_name}"
                    )
                    editor.remove_depot(self.appid_str, check_shared=False)
                    removed_count += 1

                # 1. Base AppID ONLY
                from utils.dlc_helpers import is_dlc_only_mode

                base_app_meta = next(
                    (a for a in state["apps"] if a["id"] == self.appid_str), {}
                )
                is_base_owned_locked = base_app_meta.get(
                    "is_owned_base", False
                )
                if (
                    not is_dlc_only_mode(self.appid_str)
                    and not is_base_owned_locked
                    and self.appid_str not in state["live_apps"]
                ):
                    editor.add_app(
                        self.appid_str, comment=f"{game_name} (Base)"
                    )
                    added_count += 1

                # 2. Depots
                valid_keys = set(
                    item["id"]
                    for item in state["keys"]
                    if item["key"]
                    and not item.get("is_base_owned")
                    and not item.get("is_pure_license")
                    and not item.get("is_unreleased")
                    and not item.get("is_owned")
                )
                for item in state["depots"]:
                    did = item["id"]
                    if item.get("is_unreleased"):
                        if (
                            did in state["live_depots"]
                            and did != self.appid_str
                        ):
                            editor.remove_depot(
                                did,
                                check_shared=True,
                                excluding_appid=self.appid_str,
                            )
                            removed_count += 1
                        continue
                    if did == self.appid_str or item.get("is_owned"):
                        continue
                    if did not in state["live_depots"] and did in valid_keys:
                        comment = build_depot_comment(
                            game_name, did, "", self.appid_str
                        )
                        editor.add_depot(
                            did, comment=comment, app_id=self.appid_str
                        )
                        added_count += 1

                # 3. Keys
                for item in state["keys"]:
                    kid = item["id"]
                    if (
                        item.get("is_base_owned")
                        or item.get("is_pure_license")
                        or item.get("is_unreleased")
                        or item.get("is_owned")
                    ):
                        continue
                    kval = item["key"]
                    if kval and not item["in_config"]:
                        comment = build_key_comment(
                            game_name, kid, "", self.appid_str
                        )
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

                add_dlc_data_batch(cfg_path, self.appid_str, dlc_data_pending)
                added_count += len(dlc_data_pending)
            except Exception as e:
                logger.error(
                    f"[DepotsTab] DlcData registration failed: {e}",
                    exc_info=True,
                )

        try:
            from managers.depot_key_manager import DepotKeyManager

            keys_to_persist = {}
            for item in state.get("keys", []):
                kid = item["id"]
                kval = item.get("key") or state.get("live_keys", {}).get(kid)
                if kval and len(str(kval)) == 64:
                    keys_to_persist[kid] = str(kval).lower()
            if keys_to_persist:
                DepotKeyManager.get_instance().save_depot_keys(
                    self.appid_str, keys_to_persist
                )
        except Exception as e:
            logger.debug(
                f"[DepotsTab] Error persisting keys to local DB on sync: {e}"
            )

        if editor.has_changes:
            try:
                plib = load_plugin_library()
                if self.appid_str in plib:
                    live_cfg_depots = set(get_additional_depots(cfg_path))
                    live_cfg_keys = get_decryption_keys(cfg_path)
                    existing_active = [
                        d
                        for d in plib[self.appid_str].get("depots", [])
                        if d in live_cfg_depots
                    ]
                    toggled_active = [
                        d["id"]
                        for d in state.get("depots", [])
                        if d["id"] in live_cfg_depots
                    ]
                    updated_rec_depots = sorted(
                        list(set(existing_active) | set(toggled_active))
                    )

                    updated_rec_keys = dict(
                        plib[self.appid_str].get("keys", {})
                    )
                    for k in state.get("keys", []):
                        kid = k["id"]
                        if kid in live_cfg_keys:
                            kval = k.get("key") or live_cfg_keys.get(kid)
                            if kval:
                                updated_rec_keys[kid] = str(kval).lower()
                        else:
                            updated_rec_keys.pop(kid, None)

                    plib[self.appid_str]["depots"] = updated_rec_depots
                    plib[self.appid_str]["keys"] = updated_rec_keys
                    save_plugin_library(plib)
            except Exception as e:
                logger.debug(
                    f"[DepotsTab] Error updating plugin library on sync: {e}"
                )

            SLSBridge.notify_reload()
            parts = []
            if added_count:
                parts.append(f"{added_count} added")
            if removed_count:
                parts.append(f"{removed_count} removed")
            self.status_lbl.setText(f"Saved: {', '.join(parts)}.")
        else:
            self.status_lbl.setText("Nothing to save.")

        if self.is_dlc_setup:
            self._register_dlc_setup(
                state,
                cfg_path,
                selected_dlc_apps,
                added_count,
                removed_count,
                bool(dlc_data_pending),
            )
            return

        self._refresh_ui()

    def _register_dlc_setup(
        self,
        state: Dict[str, Any],
        cfg_path: Path,
        selected_dlc_apps: Dict[str, str],
        added_count: int,
        removed_count: int,
        used_dlc_data: bool,
    ) -> None:
        """Record an AT0-M DLC-only setup (plugin library + DLC-only flag) and hand back."""
        game_name = (
            self.dialog.game_data.get("game_name") or f"App {self.appid_str}"
        )
        live_depots_now = set(get_additional_depots(cfg_path))
        live_keys_now = get_decryption_keys(cfg_path)
        reg_depots = {
            d["id"]: (d.get("raw_desc") or d["desc"])
            for d in state["depots"]
            if d["id"] in live_depots_now
        }
        reg_keys = {
            k["id"]: str(
                live_keys_now.get(k["id"]) or k.get("key") or ""
            ).lower()
            for k in state["keys"]
            if k["id"] in live_keys_now and not k.get("is_dlc_base_locked")
        }
        try:
            plib = load_plugin_library()
            rec = plib.get(self.appid_str, {})
            dlcs = set(rec.get("dlc_appids", [])) | set(
                selected_dlc_apps.keys()
            )
            names = dict(rec.get("depot_names", {}))
            names.update(reg_depots)
            keys = dict(rec.get("keys", {}))
            keys.update({k: v for k, v in reg_keys.items() if v})
            plib[self.appid_str] = {
                **rec,
                "name": game_name,
                "appid": self.appid_str,
                "is_atom": True,
                "is_dlc_only": True,
                "dlc_appids": sorted(
                    dlcs, key=lambda x: int(x) if x.isdigit() else 0
                ),
                "depots": sorted(
                    set(rec.get("depots", [])) | set(reg_depots),
                    key=lambda x: int(x) if x.isdigit() else 0,
                ),
                "keys": keys,
                "depot_names": names,
            }
            save_plugin_library(plib)
            from utils.settings import get_settings

            get_settings().setValue(f"dlc_only_mode/{self.appid_str}", True)
            get_settings().setValue(
                f"dlc_mode_source/{self.appid_str}", "native"
            )
        except Exception as e:
            logger.error(
                f"[DepotsTab] Could not record AT0-M DLC setup for {self.appid_str}: {e}",
                exc_info=True,
            )

        SLSBridge.notify_reload()
        summary = {
            "dlcs": len(selected_dlc_apps),
            "depots": len(reg_depots),
            "keys": len(reg_keys),
            "added": added_count,
            "removed": removed_count,
            "dlc_data": used_dlc_data,
        }
        if callable(self.on_registered):
            self.on_registered(summary)
        else:
            self.status_lbl.setText(
                f"Registered {summary['dlcs']} DLC(s), {summary['depots']} depot(s)."
            )
            self._refresh_ui()
