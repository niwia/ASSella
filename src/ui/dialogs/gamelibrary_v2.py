"""
Modern Game Details Dialog (V2) with modular tab architecture.
Provides an integrated interface for Game Info, SteamDB Builds / Rollback,
DRM & Tools, Steam Workshop Mods, and SLSsteam Ownership Tickets.
"""

import logging
from typing import List

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QFrame,
    QStackedWidget,
    QScrollArea,
)

from utils.settings import get_settings
from core.steamdb_scraper import SteamDBBuildsCache, SteamDBScraper

# Re-export reusable widgets for backward compatibility
from ui.dialogs.game_details.widgets import (
    SwitchToggle,
    CenteredComboBox,
    HeroBanner,
    MaterialTile,
)

from ui.dialogs.game_details.hero_header import (
    load_hero_image,
    init_hero_v2,
    init_hero_legacy,
    update_title,
    thin_line,
    section_title,
    format_time_diff,
    get_manifest_age,
    get_lua_age,
    get_last_checked,
)

from ui.dialogs.game_details.info_tab import (
    init_info_tab,
    load_branches_immediate,
    load_branches_async,
    silent_refresh_branches,
    on_branches_loaded,
    on_branch_combo_changed,
    update_validate_button,
    on_validate_btn_clicked,
    get_available_depots,
    on_manual_rollback_clicked,
    on_steamdb_history_clicked,
    trigger_rollback_job,
    on_manifest_fetch_completed,
    do_package_and_submit_manual_job,
    build_uninstall_panel,
    do_standard_uninstall,
    toggle_uninstall_panel,
    uninstall_scroll_to_bottom,
    do_dlc_uninstall,
    on_dlc_only_toggled,
    update_eos_btn_state,
    on_netsock_btn_clicked,
    on_eos_btn_clicked,
    init_slsonline_logic,
    on_status_btn_clicked,
    update_status_ui,
    on_status_changed,
    on_hubcap_status_changed,
    on_pin_build_toggled,
    reconstruct_manifests_from_depotcache,
    get_installed_buildid,
)

from ui.dialogs.game_details.builds_tab import (
    init_builds_tab,
    fetch_steamdb_builds_async,
    on_builds_loaded,
    on_builds_error,
    populate_builds_cards,
    on_build_card_clicked,
    on_build_depots_loaded,
    on_build_depots_error,
    on_builds_download_clicked,
)

from ui.dialogs.game_details.tools_tab import (
    init_tools_tab,
    refresh_drm_emulation_state,
    update_depot_label,
    refresh_dlcdata_btn_text,
    handle_move_dlc_to_dlcdata,
)

from ui.dialogs.game_details.workshop_tab import (
    init_workshop_tab,
    scan_workshop_mods_async,
    on_workshop_mods_scanned,
    delete_workshop_item_dialog,
    update_workshop_items,
)

from ui.dialogs.game_details.tickets_tab import (
    init_tickets_tab,
    handle_ticket_file_import,
    handle_ticket_text_import,
    verify_ticket_status_dialog,
    paste_and_import_ticket,
    export_installed_ticket,
    delete_installed_ticket,
)

logger = logging.getLogger(__name__)


class GameDetailsDialogV2(QDialog):
    branches_loaded = pyqtSignal(dict)
    builds_loaded = pyqtSignal(list)
    builds_error = pyqtSignal(str)
    build_depots_loaded = pyqtSignal(str, dict)
    build_depots_error = pyqtSignal(str)
    workshop_check_finished = pyqtSignal(bool)
    manifest_fetch_completed = pyqtSignal(str, str, str, str, str, str, object)

    # Rollback toggle: set False to use the old 65px hero layout
    USE_V2_HERO = True

    def __init__(self, parent, game_data):
        super().__init__(parent)
        self.parent_window = parent
        self.game_data = game_data
        self.appid = str(game_data.get("appid") or game_data.get("app_id") or "0")
        self.settings = get_settings()
        self._active_fetchers = {}
        self.branches_loaded.connect(self._on_branches_loaded)

        # SteamDB Builds Cache and Scraper
        self.builds_cache = SteamDBBuildsCache()
        self.steamdb_scraper = SteamDBScraper()
        self._cached_build_depots = {}
        self.builds_loaded.connect(self._on_builds_loaded)
        self.builds_error.connect(self._on_builds_error)
        self.build_depots_loaded.connect(self._on_build_depots_loaded)
        self.build_depots_error.connect(self._on_build_depots_error)
        self.workshop_check_finished.connect(self._on_workshop_check_finished)
        self.manifest_fetch_completed.connect(self._on_manifest_fetch_completed)

        self.accent_color = getattr(parent, "accent_color", "#a1c9fd")
        self.background_color = getattr(parent, "background_color", "#111318")

        self.setWindowTitle(f"{game_data.get('game_name', 'Game')} — Details")
        self.setMinimumSize(540, 420)
        self.resize(580, 480)
        self.setModal(True)

        # Check if game supports Steam Workshop fast (local & cached only on UI thread)
        from utils.workshop_helpers import check_game_has_workshop, check_game_has_workshop_async
        self._has_workshop = check_game_has_workshop(self.appid, self.game_data, allow_network=False)

        self._apply_stylesheet()
        self._setup_ui()

        # If workshop status not confirmed yet, check Steam API in background
        if not self._has_workshop:
            check_game_has_workshop_async(self.appid, self.game_data, callback=self.workshop_check_finished.emit)

        # Initial cached builds status for instant display (scraping is deferred until user clicks Builds tab)
        aid = int(self.appid) if self.appid.isdigit() else 0
        self._cached_builds, self._cache_age = self.builds_cache.get_builds_with_age(aid)
        self._builds_fetch_requested = False

        try:
            from core.steamdb_scraper import ByparrManager
            has_byparr = ByparrManager.find_byparr_dir() is not None
        except Exception:
            has_byparr = False

        if self._cached_builds and has_byparr:
            self._populate_builds_cards(self._cached_builds)
            self.builds_center_stack.setCurrentIndex(1)
            if hasattr(self, "builds_bottom_bar") and self.builds_bottom_bar:
                self.builds_bottom_bar.setVisible(True)
        else:
            self.builds_center_stack.setCurrentIndex(0 if has_byparr else 2)
            if hasattr(self, "builds_bottom_bar") and self.builds_bottom_bar:
                self.builds_bottom_bar.setVisible(has_byparr)

        if self.parent():
            from ui.dialogs.dialog_raiser import DialogRaiser
            DialogRaiser(self.parent(), self)

        # Hook up main progress bar
        main_win = parent.main_window if hasattr(parent, "main_window") else None
        if main_win and hasattr(main_win, "progress_bar"):
            main_win.progress_bar.valueChanged.connect(self._on_main_progress_changed)

    def _on_main_progress_changed(self, value):
        try:
            main_win = self.parent_window.main_window if hasattr(self.parent_window, "main_window") else None
            if main_win and hasattr(main_win, "task_manager") and main_win.task_manager:
                active_job = main_win.task_manager.game_data
                if active_job and str(active_job.get("appid")) == str(self.appid):
                    self.validate_btn.set_progress(value / 100.0)
        except Exception:
            pass

    def _apply_stylesheet(self):
        ac = self.accent_color
        bg = self.background_color
        from utils.color_utils import get_dark_container_color
        sel_bg_hex = get_dark_container_color(ac)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: #FFFFFF;
            }}
            QFrame {{
                border: none;
                background: transparent;
            }}
            QLabel {{
                color: #FFFFFF;
                border: none;
                background: transparent;
                font-size: 9.5pt;
            }}
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 5px 14px;
                font-size: 9.5pt;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.16);
                color: {ac};
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.02);
                color: rgba(255, 255, 255, 0.098);
            }}
            QLineEdit {{
                background-color: rgba(0, 0, 0, 0.196);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.059);
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 9.5pt;
            }}
            QLineEdit:focus {{ border-color: {ac}; }}
            QLineEdit:disabled {{
                color: rgba(255, 255, 255, 0.118);
                border-color: rgba(255, 255, 255, 8);
            }}
            QComboBox {{
                background-color: rgba(0, 0, 0, 0.196);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.059);
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 9.5pt;
            }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox QAbstractItemView {{
                background-color: #1b1b1f;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                selection-background-color: {sel_bg_hex};
                selection-color: #FFFFFF;
                font-size: 9.5pt;
                outline: 0px;
                padding: 4px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 28px;
                padding: 4px 12px;
                color: #E0E0E0;
            }}
            QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{
                background-color: {sel_bg_hex} !important;
                color: #FFFFFF !important;
            }}
            QCheckBox {{
                color: #FFFFFF;
                font-size: 9.5pt;
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 14px; height: 14px;
                border: 1px solid rgba(255, 255, 255, 0.078);
                border-radius: 3px;
                background: rgba(0, 0, 0, 0.157);
            }}
            QCheckBox::indicator:checked {{
                background-color: {ac};
                border-color: {ac};
            }}
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                border: none;
                background: rgba(0, 0, 0, 0.059);
                width: 4px;
                margin: 0px;
                border-radius: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.137);
                min-height: 20px;
                border-radius: 2px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(255, 255, 255, 0.255);
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Hero Banner — v2 or legacy
        if self.USE_V2_HERO:
            self._init_hero_v2(root)
        else:
            self._init_hero_legacy(root)

        # Tab Bar
        tab_bar_frame = QFrame()
        tab_bar_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(0, 0, 0, 0.078);
                border-bottom: 1px solid rgba(255, 255, 255, 0.039);
            }
        """)
        tab_bar_layout = QHBoxLayout(tab_bar_frame)
        tab_bar_layout.setContentsMargins(10, 0, 10, 0)
        tab_bar_layout.setSpacing(0)

        self._tab_buttons = []
        self._pages_info = [
            ("Info", 0),
            ("Builds", 1),
            ("Tools", 2),
            ("Workshop", 3),
            ("Tickets", 4),
        ]
        self.ws_page_index = 3
        self._tickets_tab_index = 4

        for label, idx in self._pages_info:
            btn = QPushButton(label)
            btn.setFlat(True)
            btn.setCheckable(True)
            btn.setFixedHeight(30)
            btn.setStyleSheet("border: none; border-radius: 0px; padding: 0px 16px; font-size: 9.5pt;")
            btn.clicked.connect(lambda _c, i=idx: self._switch_tab(i))
            tab_bar_layout.addWidget(btn)
            self._tab_buttons.append(btn)
            if label == "Workshop":
                self.ws_tab_btn = btn
                from utils.dlc_helpers import is_dlc_only_mode
                if is_dlc_only_mode(self.appid) or not self._has_workshop:
                    btn.setVisible(False)

        tab_bar_layout.addStretch()

        close_btn = QPushButton("✕ Close")
        close_btn.setFlat(True)
        close_btn.setFixedHeight(30)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                border: none; border-radius: 0;
                padding: 0 10px; font-size: 9.5pt;
                color: rgba(255, 255, 255, 0.6);
            }}
            QPushButton:hover {{ color: {self.accent_color}; }}
        """)
        close_btn.clicked.connect(self.accept)
        tab_bar_layout.addWidget(close_btn)

        root.addWidget(tab_bar_frame)

        # Stacked content
        self.stacked = QStackedWidget()
        self.stacked.setStyleSheet("background: transparent;")
        self._init_info_tab()
        self._init_builds_tab()
        self._init_tools_tab()
        self._init_workshop_tab()
        self._init_tickets_tab()
        root.addWidget(self.stacked, 1)

        self._switch_tab(0)
        self._init_slsonline_logic()

    def _switch_tab(self, index):
        self.stacked.setCurrentIndex(index)
        for i, btn in enumerate(self._tab_buttons):
            active = (i == index)
            if active:
                btn.setChecked(True)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        border: none; border-radius: 0;
                        border-bottom: 2px solid {self.accent_color};
                        padding: 0px 16px; font-size: 9.5pt;
                        color: {self.accent_color};
                        font-weight: bold;
                    }}
                """)
            else:
                btn.setChecked(False)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        border: none; border-radius: 0;
                        padding: 0px 16px; font-size: 9.5pt;
                        color: rgba(255, 255, 255, 0.6);
                    }}
                    QPushButton:hover {{ color: {self.accent_color}; }}
                """)

        if index == 1:
            self._ensure_builds_loaded()
        elif hasattr(self, "ws_page_index") and index == self.ws_page_index:
            self._ensure_workshop_loaded()

    def _ensure_builds_loaded(self):
        if getattr(self, "_builds_fetch_requested", False):
            return
        self._builds_fetch_requested = True
        CACHE_FRESH_SECONDS = 3600
        try:
            from core.steamdb_scraper import ByparrManager
            has_byparr = ByparrManager.find_byparr_dir() is not None
        except Exception:
            has_byparr = False

        if not has_byparr:
            self.builds_center_stack.setCurrentIndex(2)
            if hasattr(self, "builds_bottom_bar") and self.builds_bottom_bar:
                self.builds_bottom_bar.setVisible(False)
            return

        if hasattr(self, "builds_bottom_bar") and self.builds_bottom_bar:
            self.builds_bottom_bar.setVisible(True)

        if not getattr(self, "_cached_builds", None):
            self.builds_center_stack.setCurrentIndex(0)
            self._fetch_steamdb_builds_async()
        elif self._cache_age < 0 or self._cache_age >= CACHE_FRESH_SECONDS:
            self._fetch_steamdb_builds_async()

    def _ensure_workshop_loaded(self):
        if getattr(self, "_workshop_scanned", False):
            return
        self._workshop_scanned = True
        self._scan_workshop_mods_async()

    @pyqtSlot(bool)
    def _on_workshop_check_finished(self, has_ws: bool):
        from utils.dlc_helpers import is_dlc_only_mode
        if is_dlc_only_mode(self.appid):
            return
        if has_ws:
            self._has_workshop = True
            if hasattr(self, "ws_tab_btn") and self.ws_tab_btn:
                self.ws_tab_btn.setVisible(True)

    # ──────────────────────────────────────────
    #  Hero Header & Helpers Delegations
    # ──────────────────────────────────────────
    def _init_hero_v2(self, root):
        init_hero_v2(self, root)

    def _init_hero_legacy(self, root):
        init_hero_legacy(self, root)

    def _load_hero_image(self):
        load_hero_image(self)

    def update_title(self) -> None:
        update_title(self)

    def _thin_line(self):
        return thin_line()

    def _section_title(self, text: str):
        return section_title(text, self.accent_color)

    def _card_btn(self, text, tooltip=None):
        b = QPushButton(text)
        b.setFixedHeight(25)
        if tooltip:
            b.setToolTip(tooltip)
        return b

    def _format_time_diff(self, ts):
        return format_time_diff(ts)

    def _get_manifest_age(self):
        return get_manifest_age(self)

    def _get_lua_age(self):
        return get_lua_age(self)

    def _get_last_checked(self):
        return get_last_checked(self)

    def _cleanup_fetcher(self, key):
        self._active_fetchers.pop(key, None)

    # ──────────────────────────────────────────
    #  Info Tab Delegations
    # ──────────────────────────────────────────
    def _init_info_tab(self):
        init_info_tab(self)

    def _load_branches_immediate(self):
        load_branches_immediate(self)

    def _load_branches_async(self, force_refresh: bool = False):
        load_branches_async(self, force_refresh)

    def _silent_refresh_branches(self):
        silent_refresh_branches(self)

    def _on_branches_loaded(self, branches_dict: dict):
        on_branches_loaded(self, branches_dict)

    def _on_branch_combo_changed(self):
        on_branch_combo_changed(self)

    def _update_validate_button(self):
        update_validate_button(self)

    def _on_validate_btn_clicked(self):
        on_validate_btn_clicked(self)

    def _get_available_depots(self) -> dict:
        return get_available_depots(self)

    def _on_manual_rollback_clicked(self):
        on_manual_rollback_clicked(self)

    def _on_steamdb_history_clicked(self):
        on_steamdb_history_clicked(self)

    def _trigger_rollback_job(self, depot_id: str, build_id: str, manifest_id: str, pin_build: bool = True):
        trigger_rollback_job(self, depot_id, build_id, manifest_id, pin_build)

    @pyqtSlot(str, str, str, str, str, str, object)
    def _on_manifest_fetch_completed(
        self, error_msg, src_manifest_path_str, manifest_filename, depot_id, build_id, manifest_id, progress_dialog
    ):
        on_manifest_fetch_completed(
            self, error_msg, src_manifest_path_str, manifest_filename, depot_id, build_id, manifest_id, progress_dialog
        )

    def _do_package_and_submit_manual_job(
        self, src_manifest_path, manifest_filename, depot_id, build_id, manifest_id, pin_build: bool = True
    ):
        do_package_and_submit_manual_job(
            self, src_manifest_path, manifest_filename, depot_id, build_id, manifest_id, pin_build
        )

    def _build_uninstall_panel(self):
        build_uninstall_panel(self)

    def _do_standard_uninstall(self):
        do_standard_uninstall(self)

    def _toggle_uninstall_panel(self):
        toggle_uninstall_panel(self)

    def _uninstall_scroll_to_bottom(self):
        uninstall_scroll_to_bottom(self)

    def _do_dlc_uninstall(self):
        do_dlc_uninstall(self)

    def _on_dlc_only_toggled(self, state: bool):
        on_dlc_only_toggled(self, state)

    def _update_eos_btn_state(self):
        update_eos_btn_state(self)

    def _on_netsock_btn_clicked(self):
        on_netsock_btn_clicked(self)

    def _on_eos_btn_clicked(self):
        on_eos_btn_clicked(self)

    def _init_slsonline_logic(self):
        init_slsonline_logic(self)

    def _on_status_btn_clicked(self):
        on_status_btn_clicked(self)

    def _update_status_ui(self, status):
        update_status_ui(self, status)

    def _on_status_changed(self, changed_appid, new_status):
        on_status_changed(self, changed_appid, new_status)

    def _on_hubcap_status_changed(self, changed_appid, needs_update, update_in_progress):
        on_hubcap_status_changed(self, changed_appid, needs_update, update_in_progress)

    def _on_pin_build_toggled(self, pinned: bool):
        on_pin_build_toggled(self, pinned)

    def _reconstruct_manifests_from_depotcache(self):
        reconstruct_manifests_from_depotcache(self)

    def _get_installed_buildid(self) -> str:
        return get_installed_buildid(self)

    # ──────────────────────────────────────────
    #  Builds Tab Delegations
    # ──────────────────────────────────────────
    def _init_builds_tab(self):
        init_builds_tab(self)

    def _fetch_steamdb_builds_async(self):
        fetch_steamdb_builds_async(self)

    def _populate_builds_cards(self, data: list):
        populate_builds_cards(self, data)

    def _on_builds_loaded(self, builds: list):
        on_builds_loaded(self, builds)

    def _on_builds_error(self, err_msg: str):
        on_builds_error(self, err_msg)

    def _on_build_depots_loaded(self, build_id: str, depots: dict):
        on_build_depots_loaded(self, build_id, depots)

    def _on_build_depots_error(self, err_msg: str):
        on_build_depots_error(self, err_msg)

    def _on_build_card_clicked(self, idx: int):
        on_build_card_clicked(self, idx)

    def _on_builds_download_clicked(self):
        on_builds_download_clicked(self)

    # ──────────────────────────────────────────
    #  Tools Tab Delegations
    # ──────────────────────────────────────────
    def _init_tools_tab(self):
        init_tools_tab(self)

    def _on_goldberg_check_complete(self, is_applied):
        self._refresh_drm_emulation_state(is_applied)

    def _refresh_drm_emulation_state(self, is_applied=None):
        refresh_drm_emulation_state(self, is_applied)

    def _update_depot_label(self):
        update_depot_label(self)

    def _configure_depots_wrapper(self):
        self.parent_window._configure_depots(self.game_data)
        self._update_depot_label()

    def _reset_depots_wrapper(self):
        self.parent_window._reset_depot_selection(self.game_data)
        self._update_depot_label()

    def _refresh_dlcdata_btn_text(self):
        refresh_dlcdata_btn_text(self)

    def _handle_move_dlc_to_dlcdata(self):
        handle_move_dlc_to_dlcdata(self)

    # ──────────────────────────────────────────
    #  Workshop Tab Delegations
    # ──────────────────────────────────────────
    def _init_workshop_tab(self):
        init_workshop_tab(self)

    def _scan_workshop_mods_async(self):
        scan_workshop_mods_async(self)

    @pyqtSlot(list)
    def _on_workshop_mods_scanned(self, ws_mods: list):
        on_workshop_mods_scanned(self, ws_mods)

    def _delete_workshop_item_dialog(self, wid: str, mod_path: str, title: str):
        delete_workshop_item_dialog(self, wid, mod_path, title)

    def _update_workshop_items(self, wids: List[str]):
        update_workshop_items(self, wids)

    # ──────────────────────────────────────────
    #  Tickets Tab Delegations
    # ──────────────────────────────────────────
    def _init_tickets_tab(self):
        init_tickets_tab(self)

    def _handle_ticket_file_import(self, file_path: str):
        handle_ticket_file_import(self, file_path)

    def _handle_ticket_text_import(self, raw_text: str):
        handle_ticket_text_import(self, raw_text)

    def _verify_ticket_status_dialog(self):
        verify_ticket_status_dialog(self)

    def _paste_and_import_ticket(self):
        paste_and_import_ticket(self)

    def _export_installed_ticket(self):
        export_installed_ticket(self)

    def _delete_installed_ticket(self):
        delete_installed_ticket(self)
