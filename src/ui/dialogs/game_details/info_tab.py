"""
Info tab implementation for GameDetailsDialogV2.
Handles Quick Settings Material Tiles, branches, file verification,
rollbacks, uninstallation, SLS Online, Netsock, and EOS Proxy.
"""

import os
import re
import platform
import logging
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QMetaObject, Q_ARG
from PyQt6.QtGui import QColor, QIntValidator, QPalette
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QCheckBox,
    QLineEdit,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QScrollArea,
    QMessageBox,
    QProgressDialog,
    QApplication,
)

from ui.dialogs.game_details.widgets import CenteredComboBox, MaterialTile
from ui.dialogs.game_details.hero_header import thin_line, get_last_checked
from ui.progress_button import ProgressButton
from utils.helpers import get_base_path
from utils.yaml_config_manager import (
    get_user_config_path, add_fake_app_id, remove_fake_app_id,
    get_fake_appid, is_slssteam_config_management_enabled,
)

logger = logging.getLogger(__name__)


def init_info_tab(dialog) -> None:
    """Initialize the Info tab with actions, material tiles, and uninstall drawer."""
    inner = QWidget()
    inner.setStyleSheet("background: transparent;")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(0)

    scroll = QScrollArea()
    scroll.setWidget(inner)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    dialog.uninstall_scroll = scroll

    # ── Actions (Select Branch, Build & Validate) ────────────
    actions_row = QHBoxLayout()
    actions_row.setSpacing(8)

    installed_branch = dialog.settings.value(f"installed_branch/{dialog.appid}", "", type=str) if dialog.settings else ""
    if not installed_branch:
        installed_branch = dialog.game_data.get("installed_branch", "public")
    acf_bid = str(dialog.game_data.get("buildid") or "").strip()
    installed_bid = dialog.settings.value(
        f"installed_buildid/{dialog.appid}/{installed_branch}",
        dialog.settings.value(f"installed_buildid/{dialog.appid}", acf_bid, type=str) if dialog.settings else acf_bid,
        type=str) if dialog.settings else acf_bid
    if acf_bid and acf_bid != "Unknown" and acf_bid != installed_bid:
        installed_bid = acf_bid
        if dialog.settings:
            dialog.settings.setValue(f"installed_buildid/{dialog.appid}", acf_bid)
            if installed_branch:
                dialog.settings.setValue(f"installed_buildid/{dialog.appid}/{installed_branch}", acf_bid)

    saved_b = dialog.settings.value(f"selected_branch/{dialog.appid}", "", type=str) if dialog.settings else ""
    if not saved_b or (installed_branch and installed_branch != "public" and saved_b == "public"):
        saved_b = installed_branch or "public"
    if dialog.settings:
        dialog.settings.setValue(f"selected_branch/{dialog.appid}", saved_b)

    dialog.branch_combo = CenteredComboBox()
    dialog.branch_combo.addItem(f"{saved_b} ({installed_bid})" if installed_bid else saved_b, saved_b)
    dialog.branch_combo.setFixedHeight(26)
    dialog.branch_combo.setFixedWidth(200)
    dialog.branch_combo.setMaxVisibleItems(5)
    actions_row.addWidget(dialog.branch_combo, 0)

    dialog.validate_btn = ProgressButton("Verify Files", dialog)
    dialog.validate_btn.setFixedHeight(26)
    dialog.validate_btn.setEnabled(True)
    dialog.validate_btn.setStyleSheet("font-weight: bold; background: rgba(255, 255, 255, 0.047); color: rgba(255, 255, 255, 0.294); border: none;")
    actions_row.addWidget(dialog.validate_btn, 1)

    lay.addLayout(actions_row)
    lay.addSpacing(10)
    lay.addWidget(thin_line())
    lay.addSpacing(10)

    # ── Material You Quick Settings (Top Section Single Row) ───
    top_tiles_widget = QWidget()
    top_tiles_layout = QHBoxLayout(top_tiles_widget)
    top_tiles_layout.setContentsMargins(0, 0, 0, 0)
    top_tiles_layout.setSpacing(6)

    dialog.status_tile = MaterialTile("STATUS UNKNOWN", "Click to check", dialog, is_toggle=False)
    dialog.status_tile.clicked.connect(lambda: on_status_btn_clicked(dialog))
    top_tiles_layout.addWidget(dialog.status_tile, 1)

    dialog.folder_tile = MaterialTile("Open Folder", "Open directory", dialog, is_toggle=False)
    dialog.folder_tile.update_state(False, dialog.accent_color, inactive_sub="Open directory")
    dialog.folder_tile.clicked.connect(lambda: dialog.parent_window._open_folder(dialog.game_data.get("install_path")))
    top_tiles_layout.addWidget(dialog.folder_tile, 1)

    dialog.dlc_tile = MaterialTile("DLC Mode", "Inactive", dialog, is_toggle=True)
    is_dlc = dialog.settings.value(f"dlc_only_mode/{dialog.appid}", False, type=bool) if dialog.settings else False
    dialog.dlc_tile.update_state(is_dlc, dialog.accent_color)
    dialog.dlc_tile.clicked.connect(lambda: on_dlc_only_toggled(dialog, dialog.dlc_tile.isChecked()))
    top_tiles_layout.addWidget(dialog.dlc_tile, 1)

    dialog.pin_tile = MaterialTile("Pin Build", "Inactive", dialog, is_toggle=True)
    is_pinned = dialog.settings.value(f"pin_build/{dialog.appid}", False, type=bool) if dialog.settings else False
    dialog.pin_tile.update_state(is_pinned, dialog.accent_color)
    dialog.pin_tile.clicked.connect(lambda: on_pin_build_toggled(dialog, dialog.pin_tile.isChecked()))
    top_tiles_layout.addWidget(dialog.pin_tile, 1)

    is_exclude = dialog.settings.value(f"exclude_from_update_all/{dialog.appid}", False, type=bool) if dialog.settings else False
    dialog.update_all_tile = MaterialTile("Update-All", "Include" if not is_exclude else "Exclude", dialog, is_toggle=True)
    dialog.exclude_tile = dialog.update_all_tile
    dialog.update_all_tile.setChecked(not is_exclude)
    dialog.update_all_tile.update_state(not is_exclude, dialog.accent_color if not is_exclude else "#e05a47", active_sub="Include", inactive_sub="Exclude")

    if is_pinned:
        dialog.update_all_tile.setChecked(False)
        dialog.update_all_tile.update_state(False, "#e05a47", active_sub="Include", inactive_sub="Exclude")
        dialog.update_all_tile.setEnabled(False)
        if dialog.settings:
            dialog.settings.setValue(f"exclude_from_update_all/{dialog.appid}", True)

    def _update_all_toggled(checked):
        is_exc = not checked
        dialog.update_all_tile.update_state(checked, dialog.accent_color if checked else "#e05a47", active_sub="Include", inactive_sub="Exclude")
        if dialog.settings:
            dialog.settings.setValue(f"exclude_from_update_all/{dialog.appid}", is_exc)

    dialog.update_all_tile.clicked.connect(lambda: _update_all_toggled(dialog.update_all_tile.isChecked()))
    top_tiles_layout.addWidget(dialog.update_all_tile, 1)

    lay.addWidget(top_tiles_widget)
    lay.addSpacing(8)

    # ── Bottom Row Section (SLSonline & EOS Proxy) ───────────
    bottom_tiles_widget = QWidget()
    bottom_tiles_layout = QHBoxLayout(bottom_tiles_widget)
    bottom_tiles_layout.setContentsMargins(0, 0, 0, 0)
    bottom_tiles_layout.setSpacing(10)

    sls_container = QFrame()
    sls_container.setFixedHeight(44)
    sls_container.setStyleSheet("QFrame { background: transparent; border: none; }")
    sls_container_layout = QHBoxLayout(sls_container)
    sls_container_layout.setContentsMargins(0, 0, 0, 0)
    sls_container_layout.setSpacing(0)

    dialog.sls_tile = MaterialTile("SLSonline", "Inactive", dialog, is_toggle=True)
    sls_container_layout.addWidget(dialog.sls_tile, 1)

    dialog.sls_input_container = QFrame()
    dialog.sls_input_container.setFixedWidth(95)
    dialog.sls_input_container.setStyleSheet("""
        QFrame {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-left: none;
            border-top-right-radius: 8px;
            border-bottom-right-radius: 8px;
        }
    """)
    sls_input_lay = QHBoxLayout(dialog.sls_input_container)
    sls_input_lay.setContentsMargins(6, 2, 6, 2)
    sls_input_lay.setSpacing(4)

    fl = QLabel("FID:")
    fl.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 8pt; font-weight: bold;")
    dialog.sls_input = QLineEdit()
    dialog.sls_input.setPlaceholderText("480")
    dialog.sls_input.setValidator(QIntValidator())
    dialog.sls_input.setFixedHeight(22)
    dialog.sls_input.setFixedWidth(52)
    dialog.sls_input.setStyleSheet("""
        QLineEdit {
            background: rgba(0, 0, 0, 0.25);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 4px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: bold;
            padding: 0 4px;
        }
        QLineEdit:focus { border-color: #FFFFFF; }
    """)
    sls_input_lay.addWidget(fl)
    sls_input_lay.addWidget(dialog.sls_input)
    dialog.sls_input_container.setVisible(False)
    sls_container_layout.addWidget(dialog.sls_input_container)

    bottom_tiles_layout.addWidget(sls_container, 1)

    dialog.netsock_tile = MaterialTile("Netsock", "Inactive", dialog, is_toggle=True)
    dialog.netsock_tile.setVisible(False)
    bottom_tiles_layout.addWidget(dialog.netsock_tile, 1)

    dialog.eos_tile = MaterialTile("EOS Proxy", "Inactive", dialog, is_toggle=True)
    dialog.eos_tile.setVisible(False)
    bottom_tiles_layout.addWidget(dialog.eos_tile, 1)

    lay.addWidget(bottom_tiles_widget)
    lay.addSpacing(12)
    lay.addWidget(thin_line())
    lay.addSpacing(10)

    dialog.validate_btn.clicked.connect(lambda: on_validate_btn_clicked(dialog))
    dialog.branch_combo.currentIndexChanged.connect(lambda: on_branch_combo_changed(dialog))

    load_branches_immediate(dialog)
    update_status_ui(dialog, dialog.game_data.get("update_status"))

    if dialog.parent_window and hasattr(dialog.parent_window, "game_manager") and dialog.parent_window.game_manager:
        dialog.parent_window.game_manager.game_update_status_changed.connect(dialog._on_status_changed)
        dialog.parent_window.game_manager.game_hubcap_status_checked.connect(dialog._on_hubcap_status_changed)

        def _cleanup_signals():
            if dialog.parent_window and hasattr(dialog.parent_window, "game_manager") and dialog.parent_window.game_manager:
                try:
                    dialog.parent_window.game_manager.game_update_status_changed.disconnect(dialog._on_status_changed)
                except Exception:
                    pass
                try:
                    dialog.parent_window.game_manager.game_hubcap_status_checked.disconnect(dialog._on_hubcap_status_changed)
                except Exception:
                    pass
        dialog.finished.connect(_cleanup_signals)

    info_tab_container = QWidget()
    info_tab_container.setStyleSheet("background: transparent;")
    info_tab_layout = QVBoxLayout(info_tab_container)
    info_tab_layout.setContentsMargins(0, 0, 0, 0)
    info_tab_layout.setSpacing(0)
    info_tab_layout.addWidget(scroll, 1)

    # ── Floating Footer at the bottom ───────────────────────
    dialog.footer_widget = QWidget()
    dialog.footer_widget.setObjectName("floatingFooter")
    dialog.footer_widget.setStyleSheet(f"""
        QWidget#floatingFooter {{
            background-color: {dialog.background_color};
            border-top: 1px solid rgba(255, 255, 255, 0.08);
        }}
    """)
    footer_layout = QVBoxLayout(dialog.footer_widget)
    footer_layout.setContentsMargins(14, 10, 14, 10)
    footer_layout.setSpacing(6)

    btn_row = QHBoxLayout()
    btn_row.setContentsMargins(0, 0, 0, 0)
    btn_row.setSpacing(10)

    from utils.color_utils import get_semantic_colors
    sem_colors = get_semantic_colors(dialog.accent_color)
    err_color = sem_colors["error"]
    ec = QColor(err_color)

    err_bg = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.12)"
    err_border = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.22)"
    err_hover = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.20)"
    adv_bg = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.05)"
    adv_border = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.12)"
    adv_hover = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.08)"
    adv_checked_bg = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.18)"
    adv_checked_border = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.35)"
    panel_bg = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.04)"
    panel_border = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.15)"

    dialog._uninstall_pill = QPushButton("Uninstall")
    dialog._uninstall_pill.setFixedHeight(32)
    dialog._uninstall_pill.setStyleSheet(f"""
        QPushButton {{
            background: {err_bg};
            color: {err_color};
            border: 1px solid {err_border};
            border-radius: 6px;
            font-weight: bold;
            font-size: 9.5pt;
            padding: 0 16px;
        }}
        QPushButton:hover {{ background: {err_hover}; }}
    """)
    dialog._uninstall_pill.clicked.connect(lambda: do_standard_uninstall(dialog))
    btn_row.addWidget(dialog._uninstall_pill, 1)

    dialog._adv_uninstall_btn = QPushButton("Advanced Uninstall")
    dialog._adv_uninstall_btn.setFixedHeight(32)
    dialog._adv_uninstall_btn.setCheckable(True)
    dialog._adv_uninstall_btn.setStyleSheet(f"""
        QPushButton {{
            background: {adv_bg};
            color: {err_color};
            border: 1px solid {adv_border};
            border-radius: 6px;
            font-weight: bold;
            font-size: 9.5pt;
            padding: 0 16px;
        }}
        QPushButton:hover {{ background: {adv_hover}; }}
        QPushButton:checked {{
            background: {adv_checked_bg};
            color: #FFFFFF;
            border-color: {adv_checked_border};
        }}
    """)
    dialog._adv_uninstall_btn.clicked.connect(lambda: toggle_uninstall_panel(dialog))
    btn_row.addWidget(dialog._adv_uninstall_btn, 1)

    footer_layout.addLayout(btn_row)

    dialog._uninstall_expanded = False
    dialog._uninstall_panel = QFrame()
    dialog._uninstall_panel.setObjectName("uninstallPanel")
    dialog._uninstall_panel.setStyleSheet(f"""
        QFrame#uninstallPanel {{
            background-color: {panel_bg};
            border: 1px solid {panel_border};
            border-radius: 4px;
        }}
        QFrame#uninstallPanel QLabel {{
            border: none;
            background: transparent;
        }}
    """)
    dialog._uninstall_panel.setVisible(False)
    dialog._uninstall_inner = QVBoxLayout(dialog._uninstall_panel)
    dialog._uninstall_inner.setContentsMargins(10, 8, 10, 8)
    dialog._uninstall_inner.setSpacing(6)
    dialog._uninstall_content = QVBoxLayout()
    dialog._uninstall_inner.addLayout(dialog._uninstall_content)
    dialog._uninstall_panel_built = False

    footer_layout.addWidget(dialog._uninstall_panel)
    info_tab_layout.addWidget(dialog.footer_widget)

    dialog.stacked.addWidget(info_tab_container)


def load_branches_immediate(dialog) -> None:
    """Load branches synchronously from DB cache for instant startup display."""
    appid = dialog.appid
    loaded_from_cache = False

    try:
        from managers.db_manager import DatabaseManager
        db = DatabaseManager()
        app_info = db.get_app_info(appid, bypass_expiration=True)
        cached_branches = app_info.get("branches") if app_info else None
        if cached_branches and isinstance(cached_branches, dict) and len(cached_branches) > 0:
            on_branches_loaded(dialog, cached_branches)
            loaded_from_cache = True
        elif app_info and app_info.get("buildid"):
            fallback = {"public": {"buildid": str(app_info.get("buildid"))}}
            on_branches_loaded(dialog, fallback)
        elif dialog.game_data.get("buildid"):
            fallback = {"public": {"buildid": str(dialog.game_data.get("buildid"))}}
            on_branches_loaded(dialog, fallback)
    except Exception:
        pass

    threading.Thread(target=lambda: silent_refresh_branches(dialog), daemon=True).start()

    if not loaded_from_cache:
        dialog._is_fetching_branches = True
        if hasattr(dialog, "validate_btn"):
            dialog.validate_btn.setEnabled(False)
            dialog.validate_btn.setText("Fetching...")
            dialog.validate_btn.setToolTip("Fetching branch and depot data from Steam...")
        load_branches_async(dialog, force_refresh=True)


def load_branches_async(dialog, force_refresh: bool = False) -> None:
    appid = dialog.appid

    if not force_refresh:
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            app_info = db.get_app_info(appid, bypass_expiration=True)
            cached_branches = app_info.get("branches") if app_info else None
            if cached_branches and isinstance(cached_branches, dict) and len(cached_branches) > 0:
                dialog.branches_loaded.emit(cached_branches)
                threading.Thread(target=lambda: silent_refresh_branches(dialog), daemon=True).start()
                return
            elif app_info and app_info.get("buildid"):
                fallback = {"public": {"buildid": str(app_info.get("buildid"))}}
                dialog.branches_loaded.emit(fallback)
                threading.Thread(target=lambda: silent_refresh_branches(dialog), daemon=True).start()
                return
        except Exception:
            pass

    def _fetch():
        try:
            from core.steam_api import get_app_branches
            return get_app_branches(appid, force_refresh=True)
        except BaseException as e:
            logger.error(f"Error fetching branches for {appid}: {e}")
            return {"public": {"buildid": ""}}

    def run_thread():
        b_data = _fetch()
        dialog.branches_loaded.emit(b_data)

    threading.Thread(target=run_thread, daemon=True).start()


def silent_refresh_branches(dialog) -> None:
    try:
        from core.steam_api import get_app_branches
        fresh = get_app_branches(dialog.appid, force_refresh=True)
        if fresh:
            dialog.branches_loaded.emit(fresh)
    except BaseException:
        pass


def on_branches_loaded(dialog, branches_dict: dict) -> None:
    dialog._is_fetching_branches = False
    try:
        if not branches_dict or not isinstance(branches_dict, dict):
            branches_dict = {"public": {"buildid": str(dialog.game_data.get("buildid") or "")}}
        branches_dict = dict(branches_dict)
        dialog._branches_dict = branches_dict
        dialog.branch_combo.blockSignals(True)
        dialog.branch_combo.clear()

        sorted_keys = sorted(branches_dict.keys(), key=lambda k: (0 if k == "public" else 1, k))
        installed_branch = dialog.settings.value(f"installed_branch/{dialog.appid}", "", type=str) if dialog.settings else ""
        if not installed_branch:
            installed_branch = dialog.game_data.get("installed_branch", "public")
        saved_branch = dialog.settings.value(f"selected_branch/{dialog.appid}", "", type=str) if dialog.settings else ""
        if not saved_branch or (installed_branch and installed_branch != "public" and saved_branch == "public"):
            saved_branch = installed_branch or "public"

        if saved_branch and saved_branch not in branches_dict:
            installed_bid = dialog.settings.value(
                f"installed_buildid/{dialog.appid}/{saved_branch}",
                dialog.settings.value(f"installed_buildid/{dialog.appid}", "", type=str) if dialog.settings else "",
                type=str) if dialog.settings else ""
            branches_dict[saved_branch] = {"buildid": installed_bid or ""}
            sorted_keys.append(saved_branch)
            logger.info(
                f"Branch '{saved_branch}' for {dialog.appid} not in fetched branch list "
                f"({list(branches_dict.keys())}); keeping user's selection."
            )
        select_idx = 0

        for idx, b_name in enumerate(sorted_keys):
            b_info = branches_dict[b_name]
            bid = str(b_info.get("buildid", "")) if isinstance(b_info, dict) else ""
            label = f"{b_name} ({bid})" if bid else b_name
            dialog.branch_combo.addItem(label, b_name)
            if b_name == saved_branch:
                select_idx = idx

        dialog.branch_combo.setCurrentIndex(select_idx)
    except Exception as e:
        logger.error(f"Error in _on_branches_loaded: {e}", exc_info=True)
        dialog.branch_combo.clear()
        fallback_branch = dialog.settings.value(f"selected_branch/{dialog.appid}", "", type=str) if dialog.settings else "public"
        installed_bid = dialog.settings.value(f"installed_buildid/{dialog.appid}", str(dialog.game_data.get("buildid") or "")) if dialog.settings else ""
        dialog.branch_combo.addItem(f"{fallback_branch} ({installed_bid})" if installed_bid else fallback_branch, fallback_branch)
    finally:
        dialog.branch_combo.blockSignals(False)
        try:
            on_branch_combo_changed(dialog)
        except Exception as e:
            logger.error(f"Error in on_branch_combo_changed: {e}", exc_info=True)


def on_branch_combo_changed(dialog) -> None:
    sel_branch = dialog.branch_combo.currentData() or "public"
    if dialog.settings:
        dialog.settings.setValue(f"selected_branch/{dialog.appid}", sel_branch)

    b_dict = getattr(dialog, "_branches_dict", {})
    b_info = b_dict.get(sel_branch, {}) if isinstance(b_dict, dict) else {}
    branch_bid = str(b_info.get("buildid", "")) if isinstance(b_info, dict) else ""

    installed_bid = dialog._get_installed_buildid()
    installed_branch = dialog.settings.value(f"installed_branch/{dialog.appid}", "public", type=str) if dialog.settings else "public"
    if installed_bid and installed_branch == sel_branch and dialog.settings:
        dialog.settings.setValue(f"installed_buildid/{dialog.appid}/{sel_branch}", installed_bid)
        dialog.settings.setValue(f"installed_buildid/{dialog.appid}", installed_bid)

    if hasattr(dialog, "build_val_lbl"):
        if installed_bid:
            is_older = False
            if branch_bid and sel_branch == installed_branch:
                try:
                    is_older = int(installed_bid) < int(branch_bid)
                except (ValueError, TypeError):
                    is_older = (branch_bid != installed_bid)

            dialog.build_val_lbl.setText(installed_bid)
            if is_older:
                dialog.build_val_lbl.setStyleSheet("color: #FFB84D; font-size: 9.5pt; font-weight: bold; background: transparent;")
                dialog.build_val_lbl.setToolTip(f"Installed Build: {installed_bid}\nLatest on Steam ({sel_branch}): Build {branch_bid} (Update available)")
            else:
                dialog.build_val_lbl.setStyleSheet("color: #46b464; font-size: 9.5pt; font-weight: bold; background: transparent;")
                dialog.build_val_lbl.setToolTip(f"Installed Build: {installed_bid} (Up to date)")
        else:
            if branch_bid:
                dialog.build_val_lbl.setText(branch_bid)
                dialog.build_val_lbl.setStyleSheet("color: #7ab3ff; font-size: 9.5pt; font-weight: bold; background: transparent;")
                dialog.build_val_lbl.setToolTip(f"Latest on Steam ({sel_branch}): Build {branch_bid}\n(Game not installed or build ID unknown)")
            else:
                dialog.build_val_lbl.setText("Unknown")
                dialog.build_val_lbl.setStyleSheet(f"color: {dialog.accent_color}; font-size: 9.5pt; font-weight: bold; background: transparent;")

    update_validate_button(dialog)


def update_validate_button(dialog) -> None:
    if not hasattr(dialog, "validate_btn") or not dialog.validate_btn:
        return

    if getattr(dialog, "_is_fetching_branches", False):
        dialog.validate_btn.setEnabled(False)
        dialog.validate_btn.setText("Fetching...")
        dialog.validate_btn.setToolTip("Fetching branch and depot data from Steam...")
        return

    sel_branch = dialog.branch_combo.currentData() or "public" if hasattr(dialog, "branch_combo") else "public"
    installed_branch = dialog.settings.value(f"installed_branch/{dialog.appid}", "public", type=str) if dialog.settings else "public"
    pinned = dialog.settings.value(f"pin_build/{dialog.appid}", False, type=bool) if dialog.settings else False
    if pinned:
        reconstruct_manifests_from_depotcache(dialog)
    installed_bid = dialog.settings.value(f"installed_buildid/{dialog.appid}", "") if dialog.settings else ""

    manifests_dir = get_base_path() / "hubcap_manifests"
    specific_zip = manifests_dir / f"accela_fetch_{dialog.appid}_build_{installed_bid}.zip" if installed_bid else None

    if pinned and specific_zip and specific_zip.exists():
        has_cache = True
    elif sel_branch != "public":
        local_zip = manifests_dir / f"accela_fetch_{dialog.appid}_branch_{sel_branch}.zip"
        has_cache = local_zip.exists()
    else:
        local_zip = manifests_dir / f"accela_fetch_{dialog.appid}.zip"
        has_cache = local_zip.exists()

    same_branch = (installed_branch == sel_branch)

    from managers.depot_key_manager import DepotKeyManager
    dkm = DepotKeyManager()
    has_keys = dkm.has_depot_keys(dialog.appid)
    is_missing_manifest_or_lua = (not has_cache) or (not has_keys)

    dialog.validate_btn.setEnabled(True)

    accent_hex = dialog.accent_color
    try:
        accent_qcolor = QColor(accent_hex)
        h, s, v, a = accent_qcolor.getHsv()
        s_s = max(s, 100)
        v_s = max(v, 120)
        success_qcolor = QColor.fromHsv(120, s_s, v_s, a)
        success_hex = success_qcolor.name()
    except Exception:
        success_hex = "#46b464"

    main_win = dialog.parent_window.main_window if hasattr(dialog.parent_window, "main_window") else None
    active_job = main_win.task_manager.game_data if (main_win and hasattr(main_win, "task_manager") and main_win.task_manager) else None
    if not active_job or str(active_job.get("appid")) != str(dialog.appid):
        dialog.validate_btn.set_progress(0.0)

    from utils.color_utils import get_best_foreground_color

    def set_btn_style(base_hex):
        text_hex = get_best_foreground_color(base_hex, dark_color="#121214", light_color="#FFFFFF")
        base_qcolor = QColor(base_hex)
        h, s, v, a = base_qcolor.getHsv()
        val_hover = min(255, int(v * 1.15)) if v > 0 else 30
        val_pressed = int(v * 0.85)
        hover_qcolor = QColor.fromHsv(h, s, val_hover, a)
        pressed_qcolor = QColor.fromHsv(h, s, val_pressed, a)

        from utils.color_utils import get_dark_container_color
        disabled_bg = get_dark_container_color(base_hex)

        dialog.validate_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {base_hex};
                color: {text_hex};
                font-weight: bold;
                border: none;
                border-radius: 6px;
            }}
            QPushButton:hover {{ background-color: {hover_qcolor.name()}; }}
            QPushButton:pressed {{ background-color: {pressed_qcolor.name()}; }}
            QPushButton:disabled {{
                background-color: {disabled_bg};
                color: rgba(255, 255, 255, 0.4);
            }}
        """)
        palette = dialog.validate_btn.palette()
        palette.setColor(QPalette.ColorRole.Highlight, base_qcolor)
        dialog.validate_btn.setPalette(palette)

    if pinned and has_cache and not is_missing_manifest_or_lua:
        dialog.validate_btn.setText("Verify Pinned Build")
        set_btn_style(success_hex)
    elif not same_branch:
        b_dict = getattr(dialog, "_branches_dict", {})
        branch_bid = ""
        if isinstance(b_dict, dict):
            b_info = b_dict.get(sel_branch, {})
            if isinstance(b_info, dict):
                branch_bid = str(b_info.get("buildid", ""))
        label = f"Install {sel_branch}"
        if branch_bid:
            label += f" ({branch_bid})"
        dialog.validate_btn.setText(label)
        set_btn_style(accent_hex)
    elif is_missing_manifest_or_lua:
        dialog.validate_btn.setText("Refetch")
        set_btn_style(accent_hex)
    elif dialog.game_data.get("update_status") == "update_available":
        b_dict = getattr(dialog, "_branches_dict", {})
        branch_bid = ""
        if isinstance(b_dict, dict):
            b_info = b_dict.get(sel_branch, {})
            if isinstance(b_info, dict):
                branch_bid = str(b_info.get("buildid", ""))
        if not branch_bid:
            branch_bid = str(dialog.game_data.get("latest_buildid") or dialog.game_data.get("buildid") or "")
        label = f"Download Update ({branch_bid})" if branch_bid else "Download Update"
        dialog.validate_btn.setText(label)
        set_btn_style(accent_hex)
    else:
        dialog.validate_btn.setText("Verify Files")
        set_btn_style(success_hex)


def on_validate_btn_clicked(dialog) -> None:
    sel_branch = dialog.branch_combo.currentData() or "public" if hasattr(dialog, "branch_combo") else "public"
    btn_text = dialog.validate_btn.text()

    dialog.validate_btn.set_loading(True)
    dialog.validate_btn.setEnabled(False)
    dialog.validate_btn.setToolTip("Task in progress...")
    if "Update" in btn_text:
        dialog.validate_btn.setText("Preparing Update...")
    elif btn_text == "Refetch":
        dialog.validate_btn.setText("Refetching...")
    else:
        dialog.validate_btn.setText("Verifying...")

    if btn_text == "Refetch":
        dialog.parent_window._fetch_game_manifest(
            dialog.game_data, dialog, branch=sel_branch, download_only=True
        )
    else:
        pinned = dialog.settings.value(f"pin_build/{dialog.appid}", False, type=bool) if dialog.settings else False
        installed_bid = dialog.settings.value(f"installed_buildid/{dialog.appid}", "") if dialog.settings else ""
        manifests_dir = get_base_path() / "hubcap_manifests"
        specific_zip = manifests_dir / f"accela_fetch_{dialog.appid}_build_{installed_bid}.zip" if installed_bid else None

        local_path_override = None
        if pinned and specific_zip and specific_zip.exists():
            local_path_override = str(specific_zip)

        dialog.parent_window._fetch_game_manifest(
            dialog.game_data, dialog, branch=sel_branch, download_only=False, local_path_override=local_path_override
        )


def get_available_depots(dialog) -> dict:
    depots_dict = {}

    if isinstance(dialog.game_data.get("installed_depots"), dict) and dialog.game_data["installed_depots"]:
        depots_dict = dict(dialog.game_data["installed_depots"])
    elif isinstance(dialog.game_data.get("depots"), dict) and dialog.game_data["depots"]:
        depots_dict = dict(dialog.game_data["depots"])

    if not depots_dict and hasattr(dialog, "_cached_build_depots") and dialog._cached_build_depots:
        for bd in dialog._cached_build_depots.values():
            if isinstance(bd, dict):
                for d_id, d_info in bd.items():
                    depots_dict[str(d_id)] = d_info

    if not depots_dict and hasattr(dialog, "builds_cache"):
        try:
            aid = int(dialog.appid) if dialog.appid.isdigit() else 0
            c_builds = dialog.builds_cache.get_builds(aid)
            for b in c_builds:
                if b.get("depots") and isinstance(b["depots"], dict):
                    for d_id, d_info in b["depots"].items():
                        depots_dict[str(d_id)] = d_info
        except Exception:
            pass

    if not depots_dict:
        try:
            from managers.db_manager import DatabaseManager
            from ui.assets import DEPOT_BLACKLIST
            string_blacklist = {str(item) for item in DEPOT_BLACKLIST}
            db = DatabaseManager()
            with db._conn_lock:
                cur = db.conn.cursor()
                cur.execute("SELECT depots_json FROM apps WHERE appid = ?", (dialog.appid,))
                row = cur.fetchone()
                if row and row["depots_json"]:
                    depots_data = db._decompress_depots(row["depots_json"], dialog.appid)
                    if depots_data:
                        depots_dict = {
                            k: v for k, v in depots_data.items()
                            if k not in ("branches", "workshopdepots", "branches_public")
                            and k not in string_blacklist
                            and isinstance(v, dict)
                        }
        except Exception as e:
            logger.debug(f"Depot resolution from DB failed: {e}")

    if not depots_dict:
        acf_path = dialog.game_data.get("appmanifest_path")
        if acf_path and os.path.exists(acf_path):
            try:
                import vdf
                with open(acf_path, "r", encoding="utf-8") as f:
                    vd = vdf.loads(f.read())
                ins_depots = vd.get("AppState", {}).get("InstalledDepots")
                if isinstance(ins_depots, dict) and ins_depots:
                    depots_dict = ins_depots
            except Exception:
                pass

    return depots_dict


def on_manual_rollback_clicked(dialog) -> None:
    from ui.dialogs.rollback_dialogs import ManualRollbackDialog
    depots = get_available_depots(dialog)
    dlg = ManualRollbackDialog(
        parent=dialog,
        appid=dialog.appid,
        game_name=dialog.game_data.get("game_name", ""),
        depots_dict=depots,
        accent_color=dialog.accent_color,
    )
    from PyQt6.QtWidgets import QDialog
    if dlg.exec() == QDialog.DialogCode.Accepted:
        trigger_rollback_job(
            dialog,
            dlg.selected_depot_id,
            dlg.selected_build_id,
            dlg.selected_manifest_id,
            pin_build=dlg.should_pin_build
        )


def on_steamdb_history_clicked(dialog) -> None:
    from ui.dialogs.rollback_dialogs import SteamDBHistoryDialog
    dlg = SteamDBHistoryDialog(
        parent=dialog,
        appid=dialog.appid,
        game_name=dialog.game_data.get("game_name", ""),
        accent_color=dialog.accent_color,
    )
    dlg.rollback_requested.connect(
        lambda depot_id, build_id, manifest_id: trigger_rollback_job(
            dialog, depot_id, build_id, manifest_id, pin_build=True
        )
    )
    dlg.exec()


def trigger_rollback_job(dialog, depot_id: str, build_id: str, manifest_id: str, pin_build: bool = True) -> None:
    if not depot_id or not build_id or not manifest_id:
        QMessageBox.warning(dialog, "Missing Fields", "Please specify Depot ID, Build ID, and Manifest ID.")
        return

    dialog._last_rollback_pin_build = pin_build

    manifest_filename = f"{depot_id}_{manifest_id}.manifest"
    global_manifests_dir = get_base_path() / "manifests"
    src_manifest_path = global_manifests_dir / manifest_filename

    if src_manifest_path.exists():
        do_package_and_submit_manual_job(dialog, src_manifest_path, manifest_filename, depot_id, build_id, manifest_id, pin_build=pin_build)
        return

    progress = QProgressDialog("Generating manifest from Hubcap...", None, 0, 0, dialog)
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setCancelButton(None)
    progress.show()

    def _fetch_thread():
        error_msg = None
        try:
            from core.morrenus_api import get_session, _get_headers
            headers = _get_headers()
            if not headers:
                error_msg = "API key not configured in settings."
            else:
                url = f"https://hubcapmanifest.com/api/v1/generate/manifest?depot_id={depot_id}&manifest_id={manifest_id}"
                from utils.isp_bypass import execute_hubcap_request
                r = execute_hubcap_request(get_session(), "GET", url, headers=headers, timeout=30)
                if r.status_code == 200:
                    global_manifests_dir.mkdir(parents=True, exist_ok=True)
                    with open(src_manifest_path, "wb") as f:
                        f.write(r.content)
                else:
                    try:
                        detail = r.json().get("detail", r.text)
                    except Exception:
                        detail = r.text
                    error_msg = f"Hubcap returned status code {r.status_code}: {detail}"
        except Exception as e:
            logger.error(f"Error generating manifest from Hubcap: {e}", exc_info=True)
            error_msg = str(e)

        if hasattr(dialog, "manifest_fetch_completed"):
            dialog.manifest_fetch_completed.emit(
                error_msg or "",
                str(src_manifest_path),
                manifest_filename,
                str(depot_id),
                str(build_id),
                str(manifest_id),
                progress
            )
        else:
            QMetaObject.invokeMethod(
                dialog,
                "_on_manifest_fetch_completed",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, error_msg or ""),
                Q_ARG(str, str(src_manifest_path)),
                Q_ARG(str, manifest_filename),
                Q_ARG(str, str(depot_id)),
                Q_ARG(str, str(build_id)),
                Q_ARG(str, str(manifest_id)),
                Q_ARG(object, progress)
            )

    threading.Thread(target=_fetch_thread, daemon=True).start()


def on_manifest_fetch_completed(dialog, error_msg, src_manifest_path_str, manifest_filename, depot_id, build_id, manifest_id, progress_dialog) -> None:
    if progress_dialog:
        try:
            progress_dialog.close()
        except Exception:
            pass

    if error_msg:
        global_manifests_dir = get_base_path() / "manifests"
        QMessageBox.critical(
            dialog,
            "Manifest Retrieval Failed",
            f"Failed to automatically download manifest from Hubcap.\n\n"
            f"Error: {error_msg}\n\n"
            f"Please manually place your manifest file '{manifest_filename}' into:\n"
            f"{global_manifests_dir}/\n\n"
            "Then try again."
        )
        return

    src_manifest_path = Path(src_manifest_path_str)
    pin_build = getattr(dialog, "_last_rollback_pin_build", True)
    do_package_and_submit_manual_job(dialog, src_manifest_path, manifest_filename, depot_id, build_id, manifest_id, pin_build=pin_build)


def do_package_and_submit_manual_job(dialog, src_manifest_path, manifest_filename, depot_id, build_id, manifest_id, pin_build: bool = True) -> None:
    import zipfile
    import shutil

    manifests_dir = get_base_path() / "hubcap_manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    local_zip_path = manifests_dir / f"accela_fetch_{dialog.appid}_branch_manual.zip"

    try:
        with zipfile.ZipFile(local_zip_path, "w", zipfile.ZIP_DEFLATED) as zip_ref:
            zip_ref.write(src_manifest_path, manifest_filename)

        specific_zip_path = manifests_dir / f"accela_fetch_{dialog.appid}_build_{build_id}.zip"
        shutil.copy(local_zip_path, specific_zip_path)

        if dialog.settings:
            dialog.settings.setValue(f"pin_build/{dialog.appid}", pin_build)
            dialog.settings.setValue(f"exclude_from_update_all/{dialog.appid}", False)
            dialog.settings.setValue(f"installed_buildid/{dialog.appid}", build_id)
        if hasattr(dialog, "pin_tile") and dialog.pin_tile:
            dialog.pin_tile.setChecked(True)
            dialog.pin_tile.update_state(True, dialog.accent_color)
        if hasattr(dialog, "exclude_tile") and dialog.exclude_tile:
            dialog.exclude_tile.setChecked(False)
            dialog.exclude_tile.update_state(False, "#e05a47")
            dialog.exclude_tile.setEnabled(False)
    except Exception as e:
        logger.error(f"Failed to create temporary manifest zip: {e}", exc_info=True)
        QMessageBox.critical(dialog, "Error", f"Failed to package manifest file: {e}")
        return

    game_data = dialog.game_data.copy()
    game_data["buildid"] = build_id
    game_data["branch"] = "public"
    game_data["_is_rollback"] = True
    game_data.setdefault("manifests", {})[depot_id] = manifest_id

    depots_dict = {}
    try:
        from managers.db_manager import DatabaseManager
        from ui.assets import DEPOT_BLACKLIST
        string_blacklist = {str(item) for item in DEPOT_BLACKLIST}
        db = DatabaseManager()
        with db._conn_lock:
            cur = db.conn.cursor()
            cur.execute("SELECT depots_json FROM apps WHERE appid = ?", (dialog.appid,))
            row = cur.fetchone()
            if row and row["depots_json"]:
                depots_data = db._decompress_depots(row["depots_json"], dialog.appid)
                if depots_data:
                    depots_dict = {
                        k: v for k, v in depots_data.items()
                        if k not in ("branches", "workshopdepots", "branches_public")
                        and k not in string_blacklist
                        and isinstance(v, dict)
                    }
    except Exception as e:
        logger.error(f"Failed to load depots from DB directly for job: {e}", exc_info=True)

    if depots_dict:
        game_data["depots"] = depots_dict
    else:
        game_data.setdefault("depots", {})

    dialog.parent_window._submit_job(str(local_zip_path), game_data, dialog)


def build_uninstall_panel(dialog) -> None:
    while dialog._uninstall_content.count():
        item = dialog._uninstall_content.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    from utils.dlc_helpers import is_dlc_only_mode, get_dlc_only_info
    is_dlc = is_dlc_only_mode(dialog.appid)

    if is_dlc:
        dlc_list = get_dlc_only_info(dialog.appid)
        info = QLabel("Choose DLC depot files to remove:")
        info.setStyleSheet("color: #ff8a7a; font-size: 9.5pt; background: transparent;")
        dialog._uninstall_content.addWidget(info)
        dialog._dlc_checkboxes = {}
        for dlc in (dlc_list or []):
            did = dlc.get("dlc_appid", "")
            dname = dlc.get("dlc_name") or did
            cb = QCheckBox(f"{dname}  ({did})")
            cb.setChecked(True)
            cb.setStyleSheet("color: #ffd0c8; font-size: 9.5pt; background: transparent;")
            dialog._dlc_checkboxes[did] = cb
            dialog._uninstall_content.addWidget(cb)
        if not dlc_list:
            dialog._uninstall_content.addWidget(QLabel("No DLC depot info found."))
        confirm = QPushButton("Remove Selected DLC Files")
        confirm.setFixedHeight(25)
        confirm.setStyleSheet("""
            QPushButton { background: rgba(160, 40, 30, 0.235); color: #ff8a7a;
                border: none; font-size: 9.5pt; font-weight: bold; }
            QPushButton:hover { background: rgba(180, 50, 35, 0.314); }
        """)
        confirm.clicked.connect(lambda: do_dlc_uninstall(dialog))
        dialog._uninstall_content.addWidget(confirm)
    else:
        warn = QLabel(f"Permanently removes files for '{dialog.game_data.get('game_name','this game')}'.")
        warn.setStyleSheet("color: #ff8a7a; font-size: 9.5pt; background: transparent; border: none;")
        warn.setWordWrap(True)
        dialog._uninstall_content.addWidget(warn)

        dialog._uninstall_opts = {}
        options = [("wipe_sls_only", "I bought the game")]
        if platform.system() == "Linux":
            options.extend([
                ("compat", "Remove Proton/Wine prefix"),
                ("saves", "Remove local cloud saves"),
            ])

        for key, text in options:
            cb = QCheckBox(text)
            cb.setStyleSheet("color: #ffd0c8; font-size: 9.5pt; background: transparent;")
            dialog._uninstall_opts[key] = cb
            dialog._uninstall_content.addWidget(cb)

        confirm = QPushButton("Confirm Uninstall")
        confirm.setFixedHeight(28)
        confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        dialog._uninstall_content.addWidget(confirm)

        def _update_uninstall_ui():
            is_bought_mode = bool(dialog._uninstall_opts.get("wipe_sls_only") and dialog._uninstall_opts["wipe_sls_only"].isChecked())
            for k in ("compat", "saves"):
                if k in dialog._uninstall_opts:
                    dialog._uninstall_opts[k].setEnabled(not is_bought_mode)
                    if is_bought_mode:
                        dialog._uninstall_opts[k].setChecked(False)

            if is_bought_mode:
                confirm.setText("Take away my sins")
                confirm.setStyleSheet("""
                    QPushButton {
                        background: rgba(34, 197, 94, 0.25);
                        color: #4ADE80;
                        border: 1px solid rgba(34, 197, 94, 0.4);
                        border-radius: 4px;
                        font-size: 9.5pt;
                        font-weight: bold;
                    }
                    QPushButton:hover { background: rgba(34, 197, 94, 0.35); }
                """)
            else:
                confirm.setText("Confirm Uninstall")
                confirm.setStyleSheet("""
                    QPushButton {
                        background: rgba(160, 40, 30, 0.235);
                        color: #ff8a7a;
                        border: none;
                        border-radius: 4px;
                        font-size: 9.5pt;
                        font-weight: bold;
                    }
                    QPushButton:hover { background: rgba(180, 50, 35, 0.314); }
                """)

        for cb in dialog._uninstall_opts.values():
            cb.toggled.connect(lambda _: _update_uninstall_ui())

        _update_uninstall_ui()

        confirm.clicked.connect(
            lambda: dialog.parent_window._uninstall_game(
                dialog.game_data, dialog, {
                    key: cb.isChecked() for key, cb in getattr(dialog, "_uninstall_opts", {}).items()
                }))
        dialog._uninstall_panel_built = True


def do_standard_uninstall(dialog) -> None:
    from utils.dlc_helpers import is_dlc_only_mode, get_dlc_only_info
    if is_dlc_only_mode(dialog.appid):
        dlc_list = get_dlc_only_info(dialog.appid)
        all_dlc_ids = [dlc.get("dlc_appid") for dlc in (dlc_list or []) if dlc.get("dlc_appid")]
        gd = dict(dialog.game_data)
        gd["_dlc_uninstall_ids"] = all_dlc_ids
        dialog.parent_window._uninstall_game(gd, dialog, {})
    else:
        dialog.parent_window._uninstall_game(
            dialog.game_data, dialog, {"compat": False, "saves": False, "wipe_sls": True, "wipe_sls_only": False}
        )


def toggle_uninstall_panel(dialog) -> None:
    dialog._uninstall_expanded = not dialog._uninstall_expanded
    if dialog._uninstall_expanded and not getattr(dialog, "_uninstall_panel_built", False):
        build_uninstall_panel(dialog)
    dialog._uninstall_panel.setVisible(dialog._uninstall_expanded)
    if hasattr(dialog, "_adv_uninstall_btn") and dialog._adv_uninstall_btn:
        dialog._adv_uninstall_btn.setChecked(dialog._uninstall_expanded)
    if dialog._uninstall_expanded and hasattr(dialog, "uninstall_scroll"):
        QTimer.singleShot(100, lambda: uninstall_scroll_to_bottom(dialog))


def uninstall_scroll_to_bottom(dialog) -> None:
    sb = dialog.uninstall_scroll.verticalScrollBar()
    if sb:
        sb.setValue(sb.maximum())


def do_dlc_uninstall(dialog) -> None:
    checked = [did for did, cb in getattr(dialog, "_dlc_checkboxes", {}).items() if cb.isChecked()]
    if not checked:
        QMessageBox.information(dialog, "Nothing selected", "Select at least one DLC to remove.")
        return
    gd = dict(dialog.game_data)
    gd["_dlc_uninstall_ids"] = checked
    dialog.parent_window._uninstall_game(gd, dialog, {})


def on_dlc_only_toggled(dialog, state: bool) -> None:
    if state:
        try:
            try:
                from ui.dialogs.dlc_warning_dialog import show_dlc_mode_warning
            except ImportError:
                try:
                    from ..dlc_warning_dialog import show_dlc_mode_warning
                except ImportError:
                    from dlc_warning_dialog import show_dlc_mode_warning
            show_dlc_mode_warning(dialog)
        except Exception as e:
            logger.warning(f"DLC warning dialog error: {e}")
    if hasattr(dialog, "dlc_tile") and dialog.dlc_tile:
        dialog.dlc_tile.update_state(state, dialog.accent_color)
    if dialog.settings:
        dialog.settings.setValue(f"dlc_only_mode/{dialog.appid}", state)
        try:
            from utils.yaml_config_manager import get_user_config_path
            from utils.dlc_helpers import sync_dlc_only_sls_config
            cp = get_user_config_path()
            if cp.exists():
                sync_dlc_only_sls_config(
                    cp, dialog.appid, dialog.game_data.get("game_name", ""), dialog.game_data
                )
        except Exception as e:
            logger.debug(f"DLC sync error: {e}")
    if getattr(dialog, "_uninstall_expanded", False):
        build_uninstall_panel(dialog)
    else:
        dialog._uninstall_panel_built = False
    dialog._refresh_drm_emulation_state()
    dialog.update_title()

    if state:
        if hasattr(dialog, "sls_tile") and dialog.sls_tile:
            dialog.sls_tile.setEnabled(False)
            dialog.sls_tile.setToolTip("Not available in DLC-Only mode")
        if hasattr(dialog, "sls_input") and dialog.sls_input:
            dialog.sls_input.setEnabled(False)
        if hasattr(dialog, "eos_tile") and dialog.eos_tile:
            dialog.eos_tile.setEnabled(False)
            dialog.eos_tile.setToolTip("Not available in DLC-Only mode")
        if hasattr(dialog, "ws_tab_btn") and dialog.ws_tab_btn:
            dialog.ws_tab_btn.setVisible(False)
            if hasattr(dialog, "ws_page_index") and dialog.stacked.currentIndex() == dialog.ws_page_index:
                dialog._switch_tab(0)
    else:
        if hasattr(dialog, "sls_tile") and dialog.sls_tile:
            dialog.sls_tile.setEnabled(True)
            dialog.sls_tile.setToolTip("")
        if hasattr(dialog, "sls_input") and dialog.sls_input:
            dialog.sls_input.setEnabled(True)
        update_eos_btn_state(dialog)
        if hasattr(dialog, "ws_tab_btn") and dialog.ws_tab_btn:
            if getattr(dialog, "_has_workshop", False):
                dialog.ws_tab_btn.setVisible(True)


def update_eos_btn_state(dialog) -> None:
    from utils.dlc_helpers import is_dlc_only_mode
    if is_dlc_only_mode(dialog.appid):
        if hasattr(dialog, "eos_tile") and dialog.eos_tile:
            dialog.eos_tile.setEnabled(False)
            dialog.eos_tile.setToolTip("Not available in DLC-Only mode")
        if hasattr(dialog, "netsock_tile") and dialog.netsock_tile:
            dialog.netsock_tile.setEnabled(False)
            dialog.netsock_tile.setToolTip("Not available in DLC-Only mode")
        return

    install_path = dialog.game_data.get("install_path")
    if not install_path or not os.path.exists(install_path):
        if hasattr(dialog, "eos_tile") and dialog.eos_tile:
            dialog.eos_tile.setVisible(False)
        if hasattr(dialog, "netsock_tile") and dialog.netsock_tile:
            dialog.netsock_tile.setVisible(False)
        return

    from utils.eos_detector import EOSDetector
    status = EOSDetector.get_proxy_status(install_path)
    is_sls_active = dialog.sls_tile.isChecked() if hasattr(dialog, "sls_tile") else False

    if status != "none":
        if hasattr(dialog, "eos_tile") and dialog.eos_tile:
            dialog.eos_tile.setVisible(True)
        if hasattr(dialog, "netsock_tile") and dialog.netsock_tile:
            dialog.netsock_tile.setVisible(False)

        if status == "active":
            dialog.eos_tile.setEnabled(True)
            dialog.eos_tile.update_state(True, dialog.accent_color, active_sub="Remove Proxy", inactive_sub="Enable Proxy")
            dialog.eos_tile.setToolTip("Epic Online Services proxy is active. Click to remove proxy and restore original DLL.")
        elif status == "stale":
            dialog.eos_tile.setEnabled(True)
            dialog.eos_tile.update_state(True, dialog.accent_color, active_sub="Reapply Proxy", inactive_sub="Reapply Proxy", custom_color="#F59E0B")
            dialog.eos_tile.setToolTip("Game was updated with unpatched EOS binaries. Click to reapply Epic Online Services proxy.")
        else:
            if not is_sls_active:
                dialog.eos_tile.setEnabled(False)
                dialog.eos_tile.update_state(False, dialog.accent_color, active_sub="Remove Proxy", inactive_sub="Enable SLSonline")
                dialog.eos_tile.setToolTip("Activate SLSonline first to enable EOS Proxy.")
            else:
                dialog.eos_tile.setEnabled(True)
                dialog.eos_tile.update_state(False, dialog.accent_color, active_sub="Remove Proxy", inactive_sub="Enable Proxy")
                dialog.eos_tile.setToolTip("Apply Epic Online Services proxy DLL.")
    else:
        if hasattr(dialog, "eos_tile") and dialog.eos_tile:
            dialog.eos_tile.setVisible(False)
        if hasattr(dialog, "netsock_tile") and dialog.netsock_tile:
            dialog.netsock_tile.setVisible(True)

            from utils.yaml_config_manager import get_user_config_path, get_launch_option
            cfg_path = get_user_config_path()
            cur_opt = get_launch_option(cfg_path, str(dialog.appid)) if cfg_path.exists() else None
            is_netsock_configured = bool(cur_opt and "netsock.so" in cur_opt)

            if not is_sls_active:
                dialog.netsock_tile.setEnabled(False)
                dialog.netsock_tile.setChecked(False)
                dialog.netsock_tile.update_state(False, dialog.accent_color, active_sub="Active", inactive_sub="Enable SLSonline")
                dialog.netsock_tile.setToolTip("Activate SLSonline first to enable Netsock.")
            else:
                dialog.netsock_tile.setEnabled(True)
                dialog.netsock_tile.setChecked(is_netsock_configured)
                dialog.netsock_tile.update_state(
                    is_netsock_configured,
                    dialog.accent_color,
                    active_sub="Active",
                    inactive_sub="Inactive"
                )
                if is_netsock_configured:
                    dialog.netsock_tile.setToolTip("Netsock LD_AUDIT patch active for multiplayer sockets. Click to disable.")
                else:
                    dialog.netsock_tile.setToolTip("Enable Netsock LD_AUDIT patch for Steam Networking Sockets.")


def on_netsock_btn_clicked(dialog) -> None:
    from utils.yaml_config_manager import (
        get_user_config_path, get_launch_option, add_launch_option,
        remove_launch_option, ensure_netsock_binary, get_netsock_so_launch_path
    )
    config = get_user_config_path()
    if not config.exists():
        return

    cur_opt = get_launch_option(config, str(dialog.appid))
    is_currently_active = bool(cur_opt and "netsock.so" in cur_opt)

    if not is_currently_active:
        reply = QMessageBox.warning(
            dialog,
            "Netsock Compatibility Warning",
            "Netsock injects via LD_AUDIT for Steam Networking Sockets multiplayer.\n\n"
            "⚠️ Notice: This may not work with Easy Anti-Cheat (EAC) games or titles with strict anti-cheat.\n\n"
            "Do you want to enable Netsock for this game?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            dialog.netsock_tile.setChecked(False)
            dialog.netsock_tile.update_state(False, dialog.accent_color, active_sub="Active", inactive_sub="Inactive")
            return

        ensure_netsock_binary()
        launch_path = get_netsock_so_launch_path()
        cmd = f'"env LD_AUDIT=\\"{launch_path}\\" %command%"'
        if add_launch_option(config, str(dialog.appid), cmd):
            dialog.netsock_tile.setChecked(True)
            dialog.netsock_tile.update_state(True, dialog.accent_color, active_sub="Active", inactive_sub="Inactive")
            dialog.netsock_tile.setToolTip("Netsock LD_AUDIT patch active. Click to disable.")
            QMessageBox.information(dialog, "Netsock Enabled", "Netsock multiplayer patch has been enabled for this game.")
        else:
            QMessageBox.warning(dialog, "Error", "Failed to write LaunchOptions to config.yaml.")
            dialog.netsock_tile.setChecked(False)
            dialog.netsock_tile.update_state(False, dialog.accent_color, active_sub="Active", inactive_sub="Inactive")
    else:
        if remove_launch_option(config, str(dialog.appid)):
            dialog.netsock_tile.setChecked(False)
            dialog.netsock_tile.update_state(False, dialog.accent_color, active_sub="Active", inactive_sub="Inactive")
            dialog.netsock_tile.setToolTip("Enable Netsock LD_AUDIT patch for Steam Networking Sockets.")
            QMessageBox.information(dialog, "Netsock Disabled", "Netsock multiplayer patch has been removed for this game.")
        else:
            QMessageBox.warning(dialog, "Error", "Failed to remove LaunchOptions from config.yaml.")


def on_eos_btn_clicked(dialog) -> None:
    install_path = dialog.game_data.get("install_path")
    if not install_path or not os.path.exists(install_path):
        return

    from utils.eos_detector import EOSDetector
    status = EOSDetector.get_proxy_status(install_path)
    if status == "none":
        return

    try:
        if status == "active":
            success = EOSDetector.remove_proxy(install_path)
            if success:
                QMessageBox.information(dialog, "EOS Proxy", "Epic Online Services proxy removed successfully.")
            else:
                QMessageBox.warning(dialog, "EOS Proxy", "Failed to remove Epic Online Services proxy.")
        elif status == "stale":
            success = EOSDetector.apply_proxy(install_path)
            if success:
                QMessageBox.information(dialog, "EOS Proxy", "Epic Online Services proxy reapplied successfully.")
            else:
                QMessageBox.warning(dialog, "EOS Proxy", "Failed to reapply Epic Online Services proxy.")
        else:
            success = EOSDetector.apply_proxy(install_path)
            if success:
                QMessageBox.information(dialog, "EOS Proxy", "Epic Online Services proxy enabled successfully.")
            else:
                from utils.paths import Paths
                proxy_src = Paths.deps("EOSSDK-Win64-Shipping.dll")
                if not proxy_src.exists():
                    QMessageBox.critical(dialog, "Error", "Bundled proxy file EOSSDK-Win64-Shipping.dll not found in deps folder.")
                else:
                    QMessageBox.warning(dialog, "EOS Proxy", "No target EOSSDK-Win64-Shipping.dll found to replace.")
    except Exception as e:
        logger.error(f"Failed to toggle EOS Proxy: {e}", exc_info=True)
        QMessageBox.critical(dialog, "Error", f"Failed to toggle EOS Proxy:\n{e}")

    update_eos_btn_state(dialog)


def init_slsonline_logic(dialog) -> None:
    dialog.eos_tile.clicked.connect(lambda: on_eos_btn_clicked(dialog))
    dialog.netsock_tile.clicked.connect(lambda: on_netsock_btn_clicked(dialog))

    if is_slssteam_config_management_enabled() and dialog.appid not in ("0", "N/A", "unknown", "480"):
        config = get_user_config_path()
        if config.exists():
            existing = get_fake_appid(config, dialog.appid)

            dialog.sls_tile.blockSignals(True)

            def _apply_split_style(checked):
                dialog.sls_tile.update_state(checked, dialog.accent_color)
                if checked:
                    from utils.color_utils import get_best_foreground_color
                    text_color = get_best_foreground_color(dialog.accent_color, dark_color="#121214", light_color="#FFFFFF")
                    dialog.sls_tile.setStyleSheet(f"""
                        QPushButton {{
                            background-color: {dialog.accent_color};
                            border: none;
                            border-top-left-radius: 8px;
                            border-bottom-left-radius: 8px;
                            border-top-right-radius: 0px;
                            border-bottom-right-radius: 0px;
                        }}
                    """)
                    dialog.sls_tile.title_lbl.setStyleSheet(f"font-weight: bold; font-size: 8.5pt; color: {text_color}; background: transparent;")
                    dialog.sls_tile.sub_lbl.setStyleSheet(f"font-size: 7.5pt; font-style: italic; color: {text_color}; opacity: 0.85; background: transparent;")

            if existing:
                dialog.sls_tile.setChecked(True)
                _apply_split_style(True)
                dialog.sls_input.setText(existing)
                dialog.sls_input_container.setVisible(True)
            else:
                dialog.sls_tile.setChecked(False)
                _apply_split_style(False)
                dialog.sls_input.setText("480")
                dialog.sls_input_container.setVisible(False)
            dialog.sls_tile.blockSignals(False)

            def _tog(checked):
                _apply_split_style(checked)
                dialog.sls_input_container.setVisible(checked)
                fid = dialog.sls_input.text().strip() or "480"
                name = dialog.game_data.get("game_name", "Unknown")
                if checked:
                    cur = get_fake_appid(config, dialog.appid)
                    if cur:
                        remove_fake_app_id(config, dialog.appid, cur)
                    add_fake_app_id(config, dialog.appid, name, fid)
                else:
                    cur = get_fake_appid(config, dialog.appid)
                    if cur:
                        remove_fake_app_id(config, dialog.appid, cur)
                    from utils.yaml_config_manager import remove_launch_option
                    remove_launch_option(config, str(dialog.appid))
                    if hasattr(dialog, "netsock_tile"):
                        dialog.netsock_tile.setChecked(False)
                update_eos_btn_state(dialog)

            def _fin():
                if dialog.sls_tile.isChecked():
                    fid = dialog.sls_input.text().strip() or "480"
                    name = dialog.game_data.get("game_name", "Unknown")
                    cur = get_fake_appid(config, dialog.appid)
                    if cur != fid:
                        if cur:
                            remove_fake_app_id(config, dialog.appid, cur)
                        add_fake_app_id(config, dialog.appid, name, fid)

            dialog.sls_tile.clicked.connect(_tog)
            dialog.sls_input.editingFinished.connect(_fin)
    else:
        dialog.sls_tile.setEnabled(False)
        dialog.sls_tile.update_state(False, dialog.accent_color)
        dialog.sls_input.setEnabled(False)

    update_eos_btn_state(dialog)

    from utils.dlc_helpers import is_dlc_only_mode
    if is_dlc_only_mode(dialog.appid):
        dialog.sls_tile.setEnabled(False)
        dialog.sls_tile.setToolTip("Not available in DLC-Only mode")
        dialog.sls_input.setEnabled(False)
        dialog.eos_tile.setEnabled(False)
        dialog.eos_tile.setToolTip("Not available in DLC-Only mode")


def on_status_btn_clicked(dialog) -> None:
    if dialog.parent_window and hasattr(dialog.parent_window, "game_manager") and dialog.parent_window.game_manager:
        dialog.status_tile.setEnabled(False)
        update_status_ui(dialog, "checking")
        load_branches_async(dialog, force_refresh=True)
        dialog.parent_window.game_manager.check_single_game_update(dialog.appid)


def update_status_ui(dialog, status) -> None:
    ac = dialog.accent_color
    last_chk = get_last_checked(dialog)
    sub = last_chk if last_chk != "Never" else "Click to check"

    from utils.color_utils import get_semantic_colors
    if status == "vapor" or dialog.game_data.get("is_vapor") or dialog.game_data.get("is_plugin_game"):
        title = "VAPOR"
        sub = "Steam Native / SLSsteam"
        dialog.status_tile.title_lbl.setText(title)
        dialog.status_tile.sub_lbl.setText(sub)

        tonal_bg = "rgba(186, 104, 200, 0.22)"
        tonal_hover = "rgba(186, 104, 200, 0.32)"
        border_color = "rgba(206, 147, 216, 0.45)"
        purple_text = "#E1BEE7"

        dialog.status_tile.setChecked(True)
        dialog.status_tile._is_active = True
        dialog.status_tile._current_text_color = purple_text
        dialog.status_tile.setStyleSheet(f"""
            QPushButton {{
                background-color: {tonal_bg};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background-color: {tonal_hover};
                border: 1px solid rgba(225, 190, 231, 0.6);
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.04);
            }}
        """)
        dialog.status_tile.title_lbl.setStyleSheet(f"font-weight: bold; font-size: 8.5pt; color: {purple_text}; background: transparent;")
        dialog.status_tile.sub_lbl.setStyleSheet("font-size: 7.5pt; font-style: italic; color: rgba(225, 190, 231, 0.85); background: transparent;")
        dialog.status_tile.setEnabled(False)
        update_validate_button(dialog)
        return

    if status == "update_available":
        dialog.status_tile.title_lbl.setText("UPDATE")
        dialog.status_tile.sub_lbl.setText(sub)
        dialog.status_tile.update_state(True, sem_colors["warning"], active_sub=sub)
        dialog.status_tile.setEnabled(True)

    elif status == "up_to_date":
        title = "UP TO DATE"
        dialog.status_tile.title_lbl.setText(title)
        dialog.status_tile.sub_lbl.setText(sub)

        tonal_bg = "rgba(46, 125, 50, 0.22)"
        tonal_hover = "rgba(46, 125, 50, 0.32)"
        border_color = "rgba(129, 199, 132, 0.35)"
        mint_text = "#A8F5B8"

        dialog.status_tile.setChecked(True)
        dialog.status_tile._is_active = True
        dialog.status_tile._current_text_color = mint_text
        dialog.status_tile.setStyleSheet(f"""
            QPushButton {{
                background-color: {tonal_bg};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background-color: {tonal_hover};
                border: 1px solid rgba(168, 245, 184, 0.6);
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.04);
            }}
        """)
        dialog.status_tile.title_lbl.setStyleSheet(f"font-weight: bold; font-size: 8.5pt; color: {mint_text}; background: transparent;")
        dialog.status_tile.sub_lbl.setStyleSheet("font-size: 7.5pt; font-style: italic; color: rgba(168, 245, 184, 0.85); background: transparent;")
        dialog.status_tile.sub_lbl.setText(sub)
        dialog.status_tile.setEnabled(True)

    elif status == "checking":
        title = "CHECKING..."
        sub = "Checking Steam API..."
        dialog.status_tile.title_lbl.setText(title)
        dialog.status_tile.sub_lbl.setText(sub)
        dialog.status_tile.update_state(True, sem_colors["info"], active_sub=sub)
        dialog.status_tile.setEnabled(False)

    else:
        title = "STATUS UNKNOWN"
        sub = "Click to check"
        dialog.status_tile.title_lbl.setText(title)
        dialog.status_tile.sub_lbl.setText(sub)
        dialog.status_tile.update_state(False, ac, inactive_sub=sub)
        dialog.status_tile.setEnabled(True)

    update_validate_button(dialog)


def on_status_changed(dialog, changed_appid, new_status) -> None:
    if changed_appid != dialog.appid:
        return
    dialog.game_data["update_status"] = new_status
    update_status_ui(dialog, new_status)


def on_hubcap_status_changed(dialog, changed_appid, needs_update, update_in_progress) -> None:
    if changed_appid != dialog.appid:
        return
    dialog.game_data["hubcap_needs_update"] = needs_update
    dialog.game_data["hubcap_update_in_progress"] = update_in_progress
    update_status_ui(dialog, dialog.game_data.get("update_status"))


def on_pin_build_toggled(dialog, pinned: bool) -> None:
    from utils.dlc_helpers import is_dlc_only_mode
    if pinned and is_dlc_only_mode(dialog.appid):
        res = QMessageBox.warning(
            dialog,
            "Pin Build Warning",
            f"'{dialog.game_data.get('game_name', 'This game')}' is currently in DLC-Only mode.\n\n"
            "Pinning a build for a DLC-only game is typically not required and may freeze manifest tracking.\n\n"
            "Are you sure you want to enable Pin Build?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if res != QMessageBox.StandardButton.Yes:
            if hasattr(dialog, "pin_tile") and dialog.pin_tile:
                dialog.pin_tile.blockSignals(True)
                dialog.pin_tile.setChecked(False)
                dialog.pin_tile.update_state(False, dialog.accent_color)
                dialog.pin_tile.blockSignals(False)
            return

    if hasattr(dialog, "pin_tile") and dialog.pin_tile:
        dialog.pin_tile.update_state(pinned, dialog.accent_color)
    if dialog.settings:
        dialog.settings.setValue(f"pin_build/{dialog.appid}", pinned)

    if pinned:
        dialog.game_data["update_status"] = "up_to_date"
        try:
            from utils.update_status_cache import get_update_cache
            get_update_cache().set_status(dialog.appid, "up_to_date")
            get_update_cache().save_async()
            if dialog.parent_window and hasattr(dialog.parent_window, "game_manager") and dialog.parent_window.game_manager:
                dialog.parent_window.game_manager.game_update_status_changed.emit(dialog.appid, "up_to_date")
        except Exception:
            pass

        if hasattr(dialog, "update_all_tile") and dialog.update_all_tile:
            dialog.update_all_tile.setChecked(False)
            dialog.update_all_tile.update_state(False, "#e05a47", active_sub="Include", inactive_sub="Exclude")
            dialog.update_all_tile.setEnabled(False)
        if dialog.settings:
            dialog.settings.setValue(f"exclude_from_update_all/{dialog.appid}", True)

        try:
            from core.morrenus_api import get_manifest_zip_path
            manifests_dir = get_base_path() / "hubcap_manifests"
            installed_bid = dialog.settings.value(f"installed_buildid/{dialog.appid}", "") if dialog.settings else ""
            installed_branch = dialog.settings.value(f"installed_branch/{dialog.appid}", "public", type=str) if dialog.settings else "public"
            if installed_bid:
                specific_zip = manifests_dir / f"accela_fetch_{dialog.appid}_build_{installed_bid}.zip"
                if not specific_zip.exists():
                    default_zip = get_manifest_zip_path(dialog.appid, installed_branch)
                    if default_zip.exists():
                        import shutil
                        shutil.copy(default_zip, specific_zip)
                        logger.info(f"Duplicated {default_zip.name} (branch '{installed_branch}') to {specific_zip.name} on pin build activation.")
                    else:
                        logger.warning(
                            f"Pin build: no cached bundle for branch '{installed_branch}' ({default_zip.name}); "
                            f"no pinned snapshot created."
                        )
        except Exception as e:
            logger.warning(f"Failed to duplicate manifest zip on pin build activation: {e}")
    else:
        if hasattr(dialog, "update_all_tile") and dialog.update_all_tile:
            dialog.update_all_tile.setEnabled(True)
            dialog.update_all_tile.setChecked(True)
            dialog.update_all_tile.update_state(True, dialog.accent_color, active_sub="Include", inactive_sub="Exclude")
        if dialog.settings:
            dialog.settings.setValue(f"exclude_from_update_all/{dialog.appid}", False)

        if dialog.parent_window and hasattr(dialog.parent_window, "_update_pending_updates_ui"):
            dialog.parent_window._update_pending_updates_ui()

        if hasattr(dialog, "_on_status_btn_clicked"):
            dialog._on_status_btn_clicked()
        else:
            on_status_btn_clicked(dialog)

    if hasattr(dialog, "_update_validate_button"):
        dialog._update_validate_button()
    else:
        update_validate_button(dialog)


def reconstruct_manifests_from_depotcache(dialog) -> None:
    install_path = dialog.game_data.get("install_path")
    if not install_path:
        return
    try:
        path = Path(install_path).resolve()
        depotcache_dir = path.parents[1] / "depotcache"
        if not (depotcache_dir.exists() and depotcache_dir.is_dir()):
            local_depotcache = path / "depotcache"
            if local_depotcache.exists() and local_depotcache.is_dir():
                depotcache_dir = local_depotcache
            else:
                return

        manifests_map = {}
        for f in depotcache_dir.glob("*.manifest"):
            parts = f.name.replace(".manifest", "").split("_")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                manifests_map[parts[0]] = parts[1]

        if manifests_map:
            dialog.game_data.setdefault("manifests", {}).update(manifests_map)
            logger.info(f"Reconstructed {len(manifests_map)} manifests from depotcache: {manifests_map}")
    except Exception as e:
        logger.warning(f"Failed to reconstruct manifests from depotcache: {e}")


def get_installed_buildid(dialog) -> str:
    bid = str(dialog.game_data.get("buildid") or "").strip()
    if bid and bid.isdigit() and bid != "0":
        return bid

    acf_path = dialog.game_data.get("appmanifest_path")
    if not acf_path and dialog.appid and dialog.appid not in ("0", "N/A", "unknown"):
        from core.steam_helpers import get_steam_libraries
        try:
            for lib in get_steam_libraries():
                p = Path(lib) / "steamapps" / f"appmanifest_{dialog.appid}.acf"
                if p.exists():
                    acf_path = str(p)
                    dialog.game_data["appmanifest_path"] = acf_path
                    break
        except Exception:
            pass

    if acf_path and os.path.exists(acf_path):
        try:
            with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            m = re.search(r'"buildid"\s+"([^"]+)"', content)
            if m and m.group(1).strip() and m.group(1).strip() != "0":
                found_bid = m.group(1).strip()
                dialog.game_data["buildid"] = found_bid
                return found_bid
        except Exception:
            pass

    if dialog.settings and dialog.appid:
        installed_branch = dialog.settings.value(f"installed_branch/{dialog.appid}", "", type=str)
        if installed_branch:
            saved = dialog.settings.value(f"installed_buildid/{dialog.appid}/{installed_branch}", "", type=str)
            if saved and str(saved).isdigit() and str(saved) != "0":
                dialog.game_data["buildid"] = str(saved)
                return str(saved)
        saved = dialog.settings.value(f"installed_buildid/{dialog.appid}", "", type=str)
        if saved and str(saved).isdigit() and str(saved) != "0":
            dialog.game_data["buildid"] = str(saved)
            return str(saved)

    if dialog.appid and dialog.appid not in ("0", "N/A", "unknown"):
        try:
            import json
            meta_path = get_base_path() / "metadata" / f"{dialog.appid}.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    m_data = json.load(f)
                    mbid = str(m_data.get("buildid", "")).strip()
                    if mbid and mbid.isdigit() and mbid != "0":
                        dialog.game_data["buildid"] = mbid
                        return mbid
        except Exception:
            pass

    if bid and bid.lower() not in ("unknown", "none", "0", ""):
        return bid

    return ""


def update_depot_label(dialog) -> None:
    btn_text = "Depots: Select"
    if dialog.settings:
        val = dialog.settings.value(f"depot_selection/{dialog.appid}", "", type=str)
        if val:
            try:
                import json
                data = json.loads(val)
                sel = data.get("selected", [])
                tot = len(data.get("all_available", []))
                if sel and tot and len(sel) < tot:
                    btn_text = f"Depots: {len(sel)} of {tot}"
                elif sel and tot and len(sel) == tot:
                    btn_text = "Depots: All"
                elif sel:
                    btn_text = f"Depots: {len(sel)} Selected"
                else:
                    btn_text = "Depots: Select"
            except Exception:
                pass
    if hasattr(dialog, "choose_depots_btn") and dialog.choose_depots_btn:
        dialog.choose_depots_btn.setText(btn_text)


def configure_depots_wrapper(dialog) -> None:
    dialog.parent_window._configure_depots(dialog.game_data)
    update_depot_label(dialog)


def reset_depots_wrapper(dialog) -> None:
    dialog.parent_window._reset_depot_selection(dialog.game_data)
    update_depot_label(dialog)


def refresh_dlcdata_btn_text(dialog) -> None:
    if not hasattr(dialog, "dlcdata_exp_btn") or not dialog.dlcdata_exp_btn:
        return
    try:
        from utils.yaml_config_manager import get_user_config_path, get_dlc_data
        cp = get_user_config_path()
        if cp.exists() and bool(get_dlc_data(cp, dialog.appid)):
            dialog.dlcdata_exp_btn.setText("Revert DLC from DlcData (Advanced)")
        else:
            dialog.dlcdata_exp_btn.setText("Move DLC to DlcData (Advanced)")
    except Exception:
        dialog.dlcdata_exp_btn.setText("Move DLC to DlcData (Advanced)")


def handle_move_dlc_to_dlcdata(dialog) -> None:
    from utils.yaml_config_manager import (
        get_user_config_path, get_dlc_data, add_dlc_data_batch, remove_dlc_data
    )
    from utils.dlc_helpers import get_all_dlcs_for_app

    cp = get_user_config_path()
    if not cp.exists():
        QMessageBox.warning(dialog, "Config Not Found", "SLSsteam config.yaml could not be found.")
        return

    current_dlcs = get_dlc_data(cp, dialog.appid)
    game_name = dialog.game_data.get("game_name", f"AppID {dialog.appid}")

    if current_dlcs:
        ans = QMessageBox.question(
            dialog,
            "Revert DLC from DlcData",
            f"This game currently has {len(current_dlcs)} DLC(s) configured under DlcData in SLSsteam config.yaml.\n\n"
            f"Are you sure you want to revert and remove these DLC entries from DlcData for '{game_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            if remove_dlc_data(cp, dialog.appid):
                QMessageBox.information(
                    dialog,
                    "DlcData Reverted",
                    f"✓ Successfully removed DlcData entries for '{game_name}'.",
                )
            else:
                QMessageBox.warning(dialog, "Action Failed", "Could not remove entries from DlcData.")
            refresh_dlcdata_btn_text(dialog)
        return

    warn_msg = (
        f"⚠️ EXPERIMENTAL / ADVANCED OPTION\n\n"
        f"This will scan all DLCs for '{game_name}' (AppID {dialog.appid}) "
        f"and write them to the DlcData section in SLSsteam config.yaml.\n\n"
        f"Notice: This is NOT needed in normal cases! It is only required for rare games "
        f"or games hitting Steam's 64 DLC limit where in-game DLCs do not appear unlocked.\n\n"
        f"You can revert this action at any time using this same button.\n\n"
        f"Do you want to proceed?"
    )
    ans = QMessageBox.question(
        dialog,
        "Move DLC to DlcData (Advanced)",
        warn_msg,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if ans != QMessageBox.StandardButton.Yes:
        return

    dialog.dlcdata_exp_btn.setEnabled(False)
    dialog.dlcdata_exp_btn.setText("Fetching DLCs...")
    QApplication.processEvents()

    try:
        dlc_list = get_all_dlcs_for_app(dialog.appid, dialog.game_data)
        if not dlc_list:
            QMessageBox.information(
                dialog,
                "No DLCs Found",
                f"No downloadable or store DLCs could be found for '{game_name}'.",
            )
            return

        dlc_dict = {str(d["dlc_appid"]): d["dlc_name"] for d in dlc_list}
        ok = add_dlc_data_batch(cp, dialog.appid, dlc_dict)
        if ok:
            QMessageBox.information(
                dialog,
                "DLCs Moved to DlcData",
                f"✓ Successfully wrote {len(dlc_dict)} DLC(s) for '{game_name}' to DlcData in config.yaml.\n\n"
                f"SLSsteam will now explicitly report all these DLCs to the game.",
            )
        else:
            QMessageBox.critical(
                dialog,
                "Write Failed",
                "Failed to write entries to DlcData in config.yaml.",
            )
    except Exception as e:
        QMessageBox.critical(dialog, "Error", f"Failed to move DLCs: {e}")
    finally:
        dialog.dlcdata_exp_btn.setEnabled(True)
        refresh_dlcdata_btn_text(dialog)
