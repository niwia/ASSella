"""
Depots tab implementation for GameDetailsDialogV2.
Modular inspector and configuration viewer for AT0-M / Native Steam mode games.
Displays real-time status of AdditionalApps, AdditionalDepots, and DecryptionKeys
specifically for the current game, with sideways switch toggles, bulk category toggles (>5 items),
one-way dynamic cascade disabling, and unlockable custom editing.
"""

import logging
import re
from typing import Any, Callable, Dict, Optional, Set
import weakref

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.game_details.depots_tab_mixins import (
    ALL_SHARED_REDISTS,
    DepotsEnrichmentMixin,
    DepotsStateMixin,
    DepotsSyncMixin,
    DepotsTablesMixin,
    MiniSwitchToggle,
    _EnrichmentBridge,
    _QuotaBridge,
    _ToggleContainer,
    _WorkerBridge,
    _format_enriched_depot_desc,
    _make_bulk_toggle_bar,
    _make_local_indicator,
    _make_table,
    _set_table_height,
)
from ui.dialogs.game_details.hero_header import section_title
from ui.dialogs.themed_message_box import confirm_steam_owned_override

logger = logging.getLogger(__name__)

__all__ = [
    "ALL_SHARED_REDISTS",
    "DepotsTabController",
    "MiniSwitchToggle",
    "_EnrichmentBridge",
    "_QuotaBridge",
    "_ToggleContainer",
    "_WorkerBridge",
    "_format_enriched_depot_desc",
    "_make_bulk_toggle_bar",
    "_make_local_indicator",
    "_make_table",
    "_set_table_height",
    "build_depots_panel",
    "init_depots_tab",
]


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


class DepotsTabController(
    QObject,
    DepotsStateMixin,
    DepotsTablesMixin,
    DepotsSyncMixin,
    DepotsEnrichmentMixin,
):
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

    @property
    def dialog(self) -> Any:
        """Weakly referenced parent dialog to avoid reference cycles."""
        return self._dialog_ref() if self._dialog_ref is not None else None

    @dialog.setter
    def dialog(self, value: Any) -> None:
        if value is None:
            self._dialog_ref = None
        else:
            try:
                self._dialog_ref = weakref.ref(value)
            except TypeError:
                self._dialog_ref = None

    def __init__(
        self,
        dialog: Any,
        mode: str = "details",
        on_registered: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        super().__init__()
        self._dialog_ref = None
        self.dialog = dialog
        self._fallback_game_data: Dict[str, Any] = dict(
            getattr(dialog, "game_data", None) or {}
        )
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

        self._quota_bridge = _QuotaBridge(self)
        self._quota_bridge.quota_exhausted.connect(
            self._disable_refetch_due_to_quota
        )

        self._build_ui()
        self._init_quota_checks()

        if self.is_dlc_setup:
            self._seed_dlc_setup_defaults()
        self._refresh_ui()
        self._schedule_background_tasks()

    def _is_destroyed(self) -> bool:
        """Check if Qt container or parent dialog has been deleted by C++."""
        try:
            from PyQt6 import sip

            if (
                hasattr(self, "container")
                and self.container is not None
                and sip.isdeleted(self.container)
            ):
                return True
            dlg = self.dialog
            if isinstance(dlg, QObject) and sip.isdeleted(dlg):
                return True
        except Exception as e:
            logger.debug(f"[DepotsTab] Destruction guard check fallback: {e}")
        return False

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