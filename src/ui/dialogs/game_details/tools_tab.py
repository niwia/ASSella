"""
Tools tab implementation for GameDetailsDialogV2.
Handles DRM & Emulation (Steamless, Goldberg), Depot Management, Utility & Store Links,
and Experimental DlcData configuration.
"""

import json
import logging
from PyQt6.QtCore import Qt, QUrl
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

from ui.dialogs.game_details.hero_header import section_title, thin_line
from utils.dlc_helpers import is_dlc_only_mode, get_all_dlcs_for_app
from utils.yaml_config_manager import (
    get_user_config_path,
    get_dlc_data,
    add_dlc_data_batch,
    remove_dlc_data,
)

logger = logging.getLogger(__name__)


def init_tools_tab(dialog) -> None:
    """Initialize Tab 2 — Tools (DRM, Depots, Utility links, DlcData)."""
    inner = QWidget()
    inner.setStyleSheet("background: transparent;")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(10)

    scroll = QScrollArea()
    scroll.setWidget(inner)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    path = dialog.game_data.get("install_path")
    name = dialog.game_data.get("game_name")
    ac = dialog.accent_color

    grid_widget = QWidget()
    grid = QVBoxLayout(grid_widget)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(10)

    # ──────────────────────────────────────────
    # Section 1: DRM & Emulation
    # ──────────────────────────────────────────
    grid.addWidget(section_title("DRM & Emulation", ac))

    # Row 1: Steamless (Python) | Steamless (.NET CLI)
    dialog.b_steamless_aio = QPushButton("Steamless (Python)")
    dialog.b_steamless_aio.setToolTip("Remove Steam DRM using Python Steamless (AIO)")
    dialog.b_steamless_aio.setFixedHeight(36)
    dialog.b_steamless_aio.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.b_steamless_aio.setStyleSheet(f"""
        QPushButton {{
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 8px;
            color: #FFFFFF;
            font-size: 9pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: rgba(255,255,255,0.12);
            border-color: {ac};
            color: {ac};
        }}
        QPushButton:pressed {{ background: rgba(255,255,255,0.18); }}
        QPushButton:disabled {{
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.06);
            color: rgba(255,255,255,0.25);
        }}
    """)
    dialog.b_steamless_aio.clicked.connect(
        lambda: dialog.parent_window.main_window.task_manager.run_steamless_aio_for_game(path, name)
    )

    dialog.b_steamless_cli = QPushButton("Steamless (.NET CLI)")
    dialog.b_steamless_cli.setToolTip("Remove Steam DRM using .NET 9 Steamless CLI")
    dialog.b_steamless_cli.setFixedHeight(36)
    dialog.b_steamless_cli.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.b_steamless_cli.setStyleSheet(f"""
        QPushButton {{
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 8px;
            color: #FFFFFF;
            font-size: 9pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: rgba(255,255,255,0.12);
            border-color: {ac};
            color: {ac};
        }}
        QPushButton:pressed {{ background: rgba(255,255,255,0.18); }}
        QPushButton:disabled {{
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.06);
            color: rgba(255,255,255,0.25);
        }}
    """)
    dialog.b_steamless_cli.clicked.connect(
        lambda: dialog.parent_window.main_window.task_manager.run_steamless_for_game(path, name)
    )
    dialog.b_steamless = dialog.b_steamless_aio

    dialog.sl_row_widget = QWidget()
    sl_row = QHBoxLayout(dialog.sl_row_widget)
    sl_row.setContentsMargins(0, 0, 0, 0)
    sl_row.setSpacing(8)
    sl_row.addWidget(dialog.b_steamless_aio, 1)
    sl_row.addWidget(dialog.b_steamless_cli, 1)
    grid.addWidget(dialog.sl_row_widget)

    # Row 2: Apply Goldberg | Remove Goldberg
    dialog.gb_apply_btn = QPushButton("Apply Goldberg")
    dialog.gb_apply_btn.setToolTip("Apply Goldberg Steam emulator to this game")
    dialog.gb_apply_btn.setFixedHeight(36)
    dialog.gb_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.gb_apply_btn.setStyleSheet(f"""
        QPushButton {{
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 8px;
            color: #FFFFFF;
            font-size: 9pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: rgba(255,255,255,0.12);
            border-color: {ac};
            color: {ac};
        }}
        QPushButton:pressed {{ background: rgba(255,255,255,0.18); }}
        QPushButton:disabled {{
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.06);
            color: rgba(255,255,255,0.25);
        }}
    """)

    dialog.gb_remove_btn = QPushButton("Remove Goldberg")
    dialog.gb_remove_btn.setToolTip("Remove Goldberg Steam emulator from this game")
    dialog.gb_remove_btn.setFixedHeight(36)
    dialog.gb_remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.gb_remove_btn.setStyleSheet("""
        QPushButton {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.07);
            border-radius: 8px;
            color: rgba(255,255,255,0.3);
            font-size: 9pt;
            font-weight: 600;
        }
        QPushButton:enabled {
            background: rgba(160,30,20,0.15);
            border-color: rgba(255,100,80,0.4);
            color: #ff8a7a;
        }
        QPushButton:enabled:hover {
            background: rgba(160,30,20,0.25);
            border-color: #ff8a7a;
        }
        QPushButton:disabled {
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.06);
            color: rgba(255,255,255,0.25);
        }
    """)
    dialog.gb_remove_btn.setEnabled(False)

    if (
        dialog.parent_window
        and hasattr(dialog.parent_window, "goldberg_check_complete")
        and dialog.parent_window.goldberg_check_complete
    ):
        dialog.parent_window.goldberg_check_complete.connect(dialog._on_goldberg_check_complete)

        def _safe_disconnect_goldberg():
            try:
                if (
                    hasattr(dialog.parent_window, "goldberg_check_complete")
                    and dialog.parent_window.goldberg_check_complete
                ):
                    dialog.parent_window.goldberg_check_complete.disconnect(dialog._on_goldberg_check_complete)
            except Exception:
                pass

        dialog.finished.connect(_safe_disconnect_goldberg)

    if (
        dialog.parent_window
        and hasattr(dialog.parent_window, "executor")
        and dialog.parent_window.executor
    ):
        dialog.parent_window.executor.submit(dialog.parent_window._check_goldberg_async, path)

    def _apply_gb():
        if dialog.parent_window.main_window and dialog.parent_window.main_window.task_manager:
            dialog.parent_window.main_window.task_manager.apply_goldberg_to_game(
                path, dialog.appid, name, show_dialog=True
            )
            dialog.parent_window.executor.submit(dialog.parent_window._check_goldberg_async, path)

    def _remove_gb():
        if dialog.parent_window.main_window and dialog.parent_window.main_window.task_manager:
            dialog.parent_window.main_window.task_manager.remove_goldberg_from_game(
                path, dialog.appid, name, show_dialog=True
            )
            dialog.parent_window.executor.submit(dialog.parent_window._check_goldberg_async, path)

    dialog.gb_apply_btn.clicked.connect(_apply_gb)
    dialog.gb_remove_btn.clicked.connect(_remove_gb)

    gb_row_widget = QWidget()
    gb_row = QHBoxLayout(gb_row_widget)
    gb_row.setContentsMargins(0, 0, 0, 0)
    gb_row.setSpacing(8)
    gb_row.addWidget(dialog.gb_apply_btn, 1)
    gb_row.addWidget(dialog.gb_remove_btn, 1)
    grid.addWidget(gb_row_widget)

    refresh_drm_emulation_state(dialog)

    grid.addWidget(thin_line())

    # ──────────────────────────────────────────
    # Section 2: Depots & Installation
    # ──────────────────────────────────────────
    grid.addWidget(section_title("Depots & Installation", ac))

    depots_row_widget = QWidget()
    depots_row = QHBoxLayout(depots_row_widget)
    depots_row.setContentsMargins(0, 0, 0, 0)
    depots_row.setSpacing(8)

    # Grouped Choose + Reset (Material 3 Split Pill)
    choose_reset_group = QFrame()
    choose_reset_group.setFixedHeight(36)
    choose_reset_group.setStyleSheet("""
        QFrame {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
        }
    """)
    cr_layout = QHBoxLayout(choose_reset_group)
    cr_layout.setContentsMargins(0, 0, 0, 0)
    cr_layout.setSpacing(0)

    dialog.choose_depots_btn = QPushButton("Choose...")
    dialog.choose_depots_btn.setFixedHeight(34)
    dialog.choose_depots_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.choose_depots_btn.setStyleSheet(f"""
        QPushButton {{
            background: transparent;
            border: none;
            color: {ac};
            font-weight: bold;
            font-size: 8.5pt;
            padding: 0 10px;
        }}
        QPushButton:hover {{
            background: rgba(255, 255, 255, 0.08);
        }}
    """)
    dialog.choose_depots_btn.clicked.connect(dialog._configure_depots_wrapper)

    cr_divider = QFrame()
    cr_divider.setFixedWidth(1)
    cr_divider.setStyleSheet("background: rgba(255, 255, 255, 0.12);")

    reset_btn = QPushButton("Reset")
    reset_btn.setFixedHeight(34)
    reset_btn.setFixedWidth(60)
    reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_btn.setStyleSheet("""
        QPushButton {
            background: transparent;
            border: none;
            color: #e05a47;
            font-weight: bold;
            font-size: 8.5pt;
        }
        QPushButton:hover {
            background: rgba(224, 90, 71, 0.12);
        }
    """)
    reset_btn.clicked.connect(dialog._reset_depots_wrapper)

    cr_layout.addWidget(dialog.choose_depots_btn, 1)
    cr_layout.addWidget(cr_divider)
    cr_layout.addWidget(reset_btn)

    depots_row.addWidget(choose_reset_group, 1)

    dialog.fix_btn = QPushButton("Fix Installation")
    dialog.fix_btn.setFixedHeight(36)
    dialog.fix_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.fix_btn.setToolTip("Repairs game installation and forces Steam verification.")
    dialog.fix_btn.setStyleSheet(f"""
        QPushButton {{
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
            color: #FFFFFF;
            font-weight: bold;
            font-size: 8.5pt;
            padding: 0 12px;
        }}
        QPushButton:hover {{
            background: rgba(255, 255, 255, 0.10);
            border-color: {ac};
            color: {ac};
        }}
        QPushButton:disabled {{
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.05);
            color: rgba(255, 255, 255, 0.25);
        }}
    """)
    dialog.fix_btn.clicked.connect(lambda: dialog.parent_window._fix_game_install(dialog.game_data))
    depots_row.addWidget(dialog.fix_btn, 1)

    grid.addWidget(depots_row_widget)

    update_depot_label(dialog)

    grid.addWidget(thin_line())

    # ──────────────────────────────────────────
    # Section 3: Utility & Store Links
    # ──────────────────────────────────────────
    grid.addWidget(section_title("Utility & Store Links", ac))

    links_row_widget = QWidget()
    links_row = QHBoxLayout(links_row_widget)
    links_row.setContentsMargins(0, 0, 0, 0)
    links_row.setSpacing(6)

    is_real_app = dialog.appid not in ("0", "N/A", "unknown")

    steam_btn = QPushButton("Open Store")
    steam_btn.setFixedHeight(34)
    steam_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    steam_btn.setEnabled(is_real_app)
    steam_btn.setStyleSheet(f"""
        QPushButton {{
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: rgba(255,255,255,0.10);
            border-color: {ac};
            color: {ac};
        }}
        QPushButton:disabled {{
            color: rgba(255,255,255,0.3);
            border-color: rgba(255,255,255,0.05);
        }}
    """)
    steam_btn.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl(f"https://store.steampowered.com/app/{dialog.appid}/"))
    )
    links_row.addWidget(steam_btn, 1)

    steamdb_btn = QPushButton("Open SteamDB")
    steamdb_btn.setFixedHeight(34)
    steamdb_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    steamdb_btn.setEnabled(is_real_app)
    steamdb_btn.setStyleSheet(f"""
        QPushButton {{
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: rgba(255,255,255,0.10);
            border-color: {ac};
            color: {ac};
        }}
        QPushButton:disabled {{
            color: rgba(255,255,255,0.3);
            border-color: rgba(255,255,255,0.05);
        }}
    """)
    steamdb_btn.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl(f"https://www.steamdb.info/app/{dialog.appid}/"))
    )
    links_row.addWidget(steamdb_btn, 1)

    copy_appid = QPushButton("Copy App ID")
    copy_appid.setFixedHeight(34)
    copy_appid.setCursor(Qt.CursorShape.PointingHandCursor)
    copy_appid.setStyleSheet(f"""
        QPushButton {{
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: rgba(255,255,255,0.10);
            border-color: {ac};
            color: {ac};
        }}
    """)
    copy_appid.clicked.connect(lambda: QApplication.clipboard().setText(dialog.appid))
    links_row.addWidget(copy_appid, 1)

    copy_path = QPushButton("Copy Path")
    copy_path.setFixedHeight(34)
    copy_path.setCursor(Qt.CursorShape.PointingHandCursor)
    copy_path.setStyleSheet(f"""
        QPushButton {{
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: rgba(255,255,255,0.10);
            border-color: {ac};
            color: {ac};
        }}
    """)
    copy_path.clicked.connect(
        lambda: QApplication.clipboard().setText(str(dialog.game_data.get("install_path", "")))
    )
    links_row.addWidget(copy_path, 1)

    grid.addWidget(links_row_widget)

    grid.addWidget(thin_line())

    # ──────────────────────────────────────────
    # Section 4: Experimental DLC Tools
    # ──────────────────────────────────────────
    grid.addWidget(section_title("Experimental DLC Tools", ac))

    dialog.dlcdata_exp_btn = QPushButton("Move DLC to DlcData (Advanced)")
    dialog.dlcdata_exp_btn.setFixedHeight(34)
    dialog.dlcdata_exp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    refresh_dlcdata_btn_text(dialog)
    dialog.dlcdata_exp_btn.setStyleSheet(f"""
        QPushButton {{
            background: rgba(255, 140, 0, 0.08);
            border: 1px solid rgba(255, 140, 0, 0.25);
            border-radius: 8px;
            color: #FFA726;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: rgba(255, 140, 0, 0.16);
            border-color: #FFA726;
            color: #FFB74D;
        }}
        QPushButton:disabled {{
            color: rgba(255, 255, 255, 0.25);
            border-color: rgba(255, 255, 255, 0.05);
            background: rgba(255, 255, 255, 0.02);
        }}
    """)
    dialog.dlcdata_exp_btn.clicked.connect(lambda: handle_move_dlc_to_dlcdata(dialog))
    grid.addWidget(dialog.dlcdata_exp_btn)

    lay.addWidget(grid_widget)
    lay.addStretch()

    dialog.stacked.addWidget(scroll)


def refresh_drm_emulation_state(dialog, is_applied=None) -> None:
    """Updates Steamless & Goldberg buttons state based on DLC-only mode and installation."""
    is_dlc = is_dlc_only_mode(dialog.appid)

    if is_applied is not None:
        dialog._last_goldberg_applied = is_applied
    applied = getattr(dialog, "_last_goldberg_applied", False)

    if is_dlc:
        if hasattr(dialog, "sl_row_widget") and dialog.sl_row_widget:
            dialog.sl_row_widget.setVisible(False)
        if hasattr(dialog, "b_steamless_aio") and dialog.b_steamless_aio:
            dialog.b_steamless_aio.setEnabled(False)
            dialog.b_steamless_aio.setToolTip("Not available in DLC-Only mode")
        if hasattr(dialog, "b_steamless_cli") and dialog.b_steamless_cli:
            dialog.b_steamless_cli.setEnabled(False)
            dialog.b_steamless_cli.setToolTip("Not available in DLC-Only mode")
        if hasattr(dialog, "b_steamless") and dialog.b_steamless:
            dialog.b_steamless.setEnabled(False)
            dialog.b_steamless.setToolTip("Not available in DLC-Only mode")
        if hasattr(dialog, "gb_apply_btn") and dialog.gb_apply_btn:
            dialog.gb_apply_btn.setEnabled(False)
            dialog.gb_apply_btn.setToolTip("Not available in DLC-Only mode")
        if hasattr(dialog, "gb_remove_btn") and dialog.gb_remove_btn:
            dialog.gb_remove_btn.setEnabled(False)
            dialog.gb_remove_btn.setToolTip("Not available in DLC-Only mode")
        if hasattr(dialog, "fix_btn") and dialog.fix_btn:
            dialog.fix_btn.setEnabled(False)
            dialog.fix_btn.setToolTip("Not needed for DLC-only games")
        return

    # Regular game mode - enable / configure buttons
    if hasattr(dialog, "sl_row_widget") and dialog.sl_row_widget:
        dialog.sl_row_widget.setVisible(True)
    if hasattr(dialog, "b_steamless_aio") and dialog.b_steamless_aio:
        dialog.b_steamless_aio.setEnabled(True)
        dialog.b_steamless_aio.setToolTip("Remove Steam DRM using Python Steamless (AIO)")
    if hasattr(dialog, "b_steamless_cli") and dialog.b_steamless_cli:
        dialog.b_steamless_cli.setEnabled(True)
        dialog.b_steamless_cli.setToolTip("Remove Steam DRM using .NET 9 Steamless CLI")
    if hasattr(dialog, "b_steamless") and dialog.b_steamless:
        dialog.b_steamless.setEnabled(True)
        dialog.b_steamless.setToolTip("Remove Steam DRM using Python Steamless (AIO)")
    if hasattr(dialog, "fix_btn") and dialog.fix_btn:
        dialog.fix_btn.setEnabled(True)
        dialog.fix_btn.setToolTip("Repairs game installation and forces Steam verification.")

    if hasattr(dialog, "gb_apply_btn") and hasattr(dialog, "gb_remove_btn"):
        if applied:
            dialog.gb_apply_btn.setEnabled(False)
            dialog.gb_apply_btn.setToolTip("Goldberg is currently applied")
            dialog.gb_remove_btn.setEnabled(True)
            dialog.gb_remove_btn.setToolTip("Remove Goldberg Steam emulator from this game")
        else:
            dialog.gb_apply_btn.setEnabled(True)
            dialog.gb_apply_btn.setToolTip("Apply Goldberg Steam emulator to this game")
            dialog.gb_remove_btn.setEnabled(False)
            dialog.gb_remove_btn.setToolTip("Goldberg is not applied")


def update_depot_label(dialog) -> None:
    """Updates choose depots button label based on stored selection."""
    btn_text = "Depots: Select"
    if dialog.settings:
        val = dialog.settings.value(f"depot_selection/{dialog.appid}", "", type=str)
        if val:
            try:
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


def refresh_dlcdata_btn_text(dialog) -> None:
    """Refreshes text on Move DLC to DlcData button depending on existing config."""
    if not hasattr(dialog, "dlcdata_exp_btn") or not dialog.dlcdata_exp_btn:
        return
    try:
        cp = get_user_config_path()
        if cp.exists() and bool(get_dlc_data(cp, dialog.appid)):
            dialog.dlcdata_exp_btn.setText("Revert DLC from DlcData (Advanced)")
        else:
            dialog.dlcdata_exp_btn.setText("Move DLC to DlcData (Advanced)")
    except Exception:
        dialog.dlcdata_exp_btn.setText("Move DLC to DlcData (Advanced)")


def handle_move_dlc_to_dlcdata(dialog) -> None:
    """Moves all DLC entries into DlcData or reverts them."""
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

    # Move to DlcData Option
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
