"""
Modern Game Details Dialog (V2) with modular tab architecture.
Provides an integrated interface for Game Info, SteamDB Builds / Rollback,
DRM & Tools, Steam Workshop Mods, and SLSsteam Ownership Tickets.
"""

import logging
from typing import List

from PyQt6 import sip
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QFrame,
    QStackedWidget,
    QScrollArea,
    QWidget,
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

from ui.dialogs.game_details.window_style import (
    adapt_child_stylesheets,
    build_stylesheet,
    ink_on,
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

from ui.dialogs.game_details.shsah_reborn import (
    init_achievements_tab,
    ensure_achievements_loaded,
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
from ui.dialogs.game_details.luafix_tab import init_luafix_tab

logger = logging.getLogger(__name__)


class GameDetailsDialogV2(QDialog):
    branches_loaded = pyqtSignal(dict)
    builds_loaded = pyqtSignal(list)
    builds_error = pyqtSignal(str)
    build_depots_loaded = pyqtSignal(str, dict)
    build_depots_error = pyqtSignal(str)
    workshop_check_finished = pyqtSignal(bool)
    luafix_check_finished = pyqtSignal(bool, object)
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
        self.luafix_check_finished.connect(self._on_luafix_check_finished)
        self.manifest_fetch_completed.connect(self._on_manifest_fetch_completed)

        self.accent_color = getattr(parent, "accent_color", "#a1c9fd")
        self.background_color = getattr(parent, "background_color", "#111318")

        self.setWindowTitle(f"{game_data.get('game_name', 'Game')} — Details")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setMinimumSize(560, 440)
        self.resize(620, 500)
        self.setModal(True)

        # Workshop tab is shown only if game has workshop AND has downloaded content
        self._has_workshop = False

        self._apply_stylesheet()
        self._setup_ui()

        # Check workshop availability and downloaded content in background thread (only if enabled)
        if self.settings.value("enable_workshop_downloader", False, type=bool):
            from utils.workshop_helpers import check_game_workshop_available_async
            check_game_workshop_available_async(self.appid, self.game_data, callback=self.workshop_check_finished.emit)

        # Check lua.tools fixes availability asynchronously (only if unlocked & authenticated)
        if self._is_luatools_available():
            self._check_luafix_async()

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
        self.setStyleSheet(
            build_stylesheet(self.background_color, self.accent_color)
        )

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        from ui.dialog_titlebar import DialogTitleBar
        title_text = f"{self.game_data.get('game_name', 'Game')} — Details"
        self.title_bar = DialogTitleBar(self, title=title_text, can_minimize=False, can_maximize=True, use_power_close=True)
        root.addWidget(self.title_bar)

        # Hero Banner — v2 or legacy
        if self.USE_V2_HERO:
            self._init_hero_v2(root)
        else:
            self._init_hero_legacy(root)

        # Tab Bar
        tab_bar_frame = QFrame()
        tab_bar_frame.setStyleSheet(f"""
            QFrame {{
                background-color: rgba(0, 0, 0, 0.078);
                border-bottom: 1px solid {ink_on(self.background_color, 0.039)};
            }}
        """)
        tab_bar_layout = QHBoxLayout(tab_bar_frame)
        tab_bar_layout.setContentsMargins(8, 0, 8, 0)
        tab_bar_layout.setSpacing(2)

        is_atom = bool(
            self.game_data.get("is_atom")
            or self.game_data.get("is_vapor")
            or self.game_data.get("is_plugin_game")
            or self.game_data.get("update_status") in ("vapor", "at0m", "at0-m")
            or self.game_data.get("source") in ("Vapor", "at0-m", "AT0-M")
        )
        self._is_atom = is_atom

        if is_atom:
            has_depots = True
        else:
            from utils.yaml_config_manager import has_game_decryption_keys
            has_depots = has_game_decryption_keys(self.appid, self.game_data)
        self._has_depots = has_depots

        self._tab_buttons = []
        if has_depots:
            self._pages_info = [
                ("Info", 0),
                ("Depots", 1),
                ("Builds", 2),
                ("Tools", 3),
                ("Achievements", 4),
                ("Workshop", 5),
                ("Tickets", 6),
            ]
            self.depots_page_index = 1
            self.builds_page_index = 2
            self.achievements_page_index = 4
            self.ws_page_index = 5
            self._tickets_tab_index = 6
        else:
            self._pages_info = [
                ("Info", 0),
                ("Builds", 1),
                ("Tools", 2),
                ("Achievements", 3),
                ("Workshop", 4),
                ("Tickets", 5),
            ]
            self.depots_page_index = None
            self.builds_page_index = 1
            self.achievements_page_index = 3
            self.ws_page_index = 4
            self._tickets_tab_index = 5

        if self._is_luatools_available():
            idx = len(self._pages_info)
            self._pages_info.append(("LuaFix", idx))
            self.luafix_page_index = idx

        for label, idx in self._pages_info:
            btn = QPushButton(label)
            btn.setFlat(True)
            btn.setCheckable(True)
            btn.setFixedHeight(30)
            btn.setStyleSheet("border: none; border-radius: 0px; padding: 0px 10px; font-size: 9pt;")
            btn.clicked.connect(lambda _c, i=idx: self._switch_tab(i))
            tab_bar_layout.addWidget(btn)
            self._tab_buttons.append(btn)
            if label == "Workshop":
                self.ws_tab_btn = btn
                from utils.dlc_helpers import is_dlc_only_mode
                if is_dlc_only_mode(self.appid) or not self._has_workshop or is_atom:
                    btn.setVisible(False)
            elif label == "LuaFix":
                self.luafix_tab_btn = btn
                btn.setVisible(False)

        tab_bar_layout.addStretch()

        close_btn = QPushButton("✕ Close")
        close_btn.setFlat(True)
        close_btn.setFixedHeight(30)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                border: none; border-radius: 0;
                padding: 0 8px; font-size: 9pt;
                color: {ink_on(self.background_color, 0.6)};
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
        if has_depots:
            self._init_depots_tab()
        self._init_builds_tab()
        self._init_tools_tab()
        self._init_achievements_tab()
        self._init_workshop_tab()
        self._init_tickets_tab()
        if self._is_luatools_available():
            self._init_luafix_tab()
        root.addWidget(self.stacked, 1)

        self._switch_tab(0)
        self._init_slsonline_logic()
        adapt_child_stylesheets(self)

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
                        padding: 0px 10px; font-size: 9pt;
                        color: {self.accent_color};
                        font-weight: bold;
                    }}
                """)
            else:
                btn.setChecked(False)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        border: none; border-radius: 0;
                        padding: 0px 10px; font-size: 9pt;
                        color: {ink_on(self.background_color, 0.65)};
                    }}
                    QPushButton:hover {{ color: {self.accent_color}; }}
                """)
        adapt_child_stylesheets(self)

        if hasattr(self, "builds_page_index") and index == self.builds_page_index:
            self._ensure_builds_loaded()
        elif hasattr(self, "achievements_page_index") and index == self.achievements_page_index:
            self._ensure_achievements_loaded()
        elif hasattr(self, "ws_page_index") and index == self.ws_page_index:
            self._ensure_workshop_loaded()

    def _ensure_builds_loaded(self):
        if getattr(self, "_builds_fetch_requested", False):
            return
        self._builds_fetch_requested = True
        CACHE_FRESH_SECONDS = 3600
        try:
            from core.steamdb_scraper import ByparrManager, cffi_requests
            has_solver = (ByparrManager.find_byparr_dir() is not None) or ByparrManager.is_custom_service() or (cffi_requests is not None)
        except Exception:
            has_solver = False

        if not has_solver:
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
        is_vapor = bool(
            self.game_data.get("is_atom")
            or self.game_data.get("is_vapor")
            or self.game_data.get("is_plugin_game")
            or self.game_data.get("update_status") in ("vapor", "at0m", "at0-m")
            or self.game_data.get("source") in ("Vapor", "at0-m", "AT0-M")
        )
        if is_vapor:
            return
        if getattr(self, "_workshop_scanned", False):
            return
        self._workshop_scanned = True
        self._scan_workshop_mods_async()

    @pyqtSlot(bool)
    def _on_workshop_check_finished(self, has_ws: bool):
        from utils.dlc_helpers import is_dlc_only_mode
        is_vapor = bool(
            self.game_data.get("is_atom")
            or self.game_data.get("is_vapor")
            or self.game_data.get("is_plugin_game")
            or self.game_data.get("update_status") in ("vapor", "at0m", "at0-m")
            or self.game_data.get("source") in ("Vapor", "at0-m", "AT0-M")
        )
        if is_dlc_only_mode(self.appid) or is_vapor:
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

    def _trigger_rollback_job(self, depot_id, build_id: str, manifest_id: str = None, pin_build: bool = True):
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
        if sip.isdeleted(self):
            return
        populate_builds_cards(self, data)

    def _on_builds_loaded(self, builds: list):
        if sip.isdeleted(self):
            return
        on_builds_loaded(self, builds)

    def _on_builds_error(self, err_msg: str):
        if sip.isdeleted(self):
            return
        on_builds_error(self, err_msg)

    def _on_build_depots_loaded(self, build_id: str, depots: dict):
        if sip.isdeleted(self):
            return
        on_build_depots_loaded(self, build_id, depots)

    def _on_build_depots_error(self, err_msg: str):
        if sip.isdeleted(self):
            return
        on_build_depots_error(self, err_msg)

    def _on_build_card_clicked(self, idx: int):
        if sip.isdeleted(self):
            return
        on_build_card_clicked(self, idx)

    def _on_builds_download_clicked(self):
        if sip.isdeleted(self):
            return
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
        if hasattr(self.parent_window, "_configure_depots"):
            self.parent_window._configure_depots(self.game_data)
        self._update_depot_label()

    def _reset_depots_wrapper(self):
        if hasattr(self.parent_window, "_reset_depot_selection"):
            self.parent_window._reset_depot_selection(self.game_data)
        self._update_depot_label()

    def _refresh_dlcdata_btn_text(self):
        refresh_dlcdata_btn_text(self)

    def _handle_move_dlc_to_dlcdata(self):
        handle_move_dlc_to_dlcdata(self)

    # ──────────────────────────────────────────
    #  Achievements Tab Delegations (SHSAH Reborn)
    # ──────────────────────────────────────────
    def _init_achievements_tab(self):
        init_achievements_tab(self)

    def _ensure_achievements_loaded(self):
        ensure_achievements_loaded(self)

    # ──────────────────────────────────────────
    #  Workshop Tab Delegations
    # ──────────────────────────────────────────
    def _init_workshop_tab(self):
        is_vapor = bool(
            self.game_data.get("is_atom")
            or self.game_data.get("is_vapor")
            or self.game_data.get("is_plugin_game")
            or self.game_data.get("update_status") in ("vapor", "at0m", "at0-m")
            or self.game_data.get("source") in ("Vapor", "at0-m", "AT0-M")
        )
        ws_enabled = self.settings.value("enable_workshop_downloader", False, type=bool)
        if not ws_enabled or is_vapor:
            dummy = QWidget()
            self.stacked.addWidget(dummy)
            return
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

    # ──────────────────────────────────────────
    #  Depots Tab Delegations (AT0-M mode)
    # ──────────────────────────────────────────
    def _init_depots_tab(self):
        from ui.dialogs.game_details.depots_tab import init_depots_tab
        init_depots_tab(self)

    # ──────────────────────────────────────────
    #  LuaFix Tab Delegations (lua.tools integration)
    # ──────────────────────────────────────────
    def _is_luatools_available(self) -> bool:
        """Whether this window may show the LuaFix tab.

        Answers from a per-window cache: the check reads the session file, and
        building the details window asks three times.
        """
        if not self.settings.value("luatools_unlocked", False, type=bool):
            return False
        cached = getattr(self, "_luatools_available", None)
        if cached is not None:
            return cached
        try:
            from core.lua_tools_api import LuaToolsAPI
            available = LuaToolsAPI.is_authenticated(allow_cached=True)
        except (OSError, ValueError, ImportError) as e:
            logger.warning(f"[LuaFix] Could not read the lua.tools session: {e}")
            available = False
        self._luatools_available = available
        return available

    def _init_luafix_tab(self):
        init_luafix_tab(self)

    def _check_luafix_async(self):
        """Asynchronously check if this game has eligible fixes on lua.tools."""
        if not self._is_luatools_available():
            return

        def _worker():
            try:
                from core.lua_tools_api import LuaToolsAPI
                res = LuaToolsAPI.get_game_fixes(self.appid)
                ignore_online = self.settings.value("luatools_ignore_online_fixes", False, type=bool)
                fixes = LuaToolsAPI.filter_fixes(res.get("fixes", []), ignore_online=ignore_online)
                has_fixes = bool(res.get("success") and len(fixes) > 0)
                self.luafix_check_finished.emit(has_fixes, res)
            except Exception as e:
                logger.debug(f"[LuaFix] Async check failed for {self.appid}: {e}")
                self.luafix_check_finished.emit(False, None)

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(bool, object)
    def _on_luafix_check_finished(self, has_fixes: bool, res_data: object):
        if not self._is_luatools_available():
            if hasattr(self, "luafix_tab_btn") and self.luafix_tab_btn:
                self.luafix_tab_btn.setVisible(False)
            return
        if hasattr(self, "luafix_tab_btn") and self.luafix_tab_btn:
            self.luafix_tab_btn.setVisible(has_fixes)
        if has_fixes and res_data:
            self._cached_luafix_res = res_data
            if hasattr(self, "_luafix_bridge"):
                self._luafix_bridge.loaded.emit(res_data)
