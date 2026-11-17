"""
Tools tab implementation for GameDetailsDialogV2.
Handles DRM & Emulation (Steamless, Goldberg), Depot Management, Utility & Store Links,
and Experimental DlcData configuration.
"""

import json
import logging
import os
import platform
import threading
from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QScrollArea,
    QApplication,
)
from ui.dialogs.themed_message_box import ThemedMessageBox

from ui.dialogs.game_details.hero_header import section_title, thin_line
from ui.dialogs.settings_tabs.components_card import (
    prompt_component_missing,
    require_component,
)
from utils.component_manager import is_component_available
from utils.dlc_helpers import is_dlc_only_mode, get_all_dlcs_for_app
from utils.game_paths import find_compatdata_path
from utils.main_thread import post_to_main_thread
from utils.yaml_config_manager import (
    get_user_config_path,
    get_dlc_data,
    add_dlc_data_batch,
    remove_dlc_data,
)

logger = logging.getLogger(__name__)

_STEAMLESS_AIO_LABEL = "Steamless (Python)"
_STEAMLESS_CLI_LABEL = "Steamless (.NET CLI)"
_STEAMLESS_OVERLAY_DELAY_MS = 400


def _tools_qss(ac: str) -> str:
    """Unified stylesheet for tools tab buttons driven by dynamic 'variant' property."""
    return f"""
    QPushButton[variant] {{
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 8px;
        color: #FFFFFF;
        font-size: 8.5pt;
        font-weight: 600;
        padding: 0 10px;
    }}
    QPushButton[variant]:hover {{
        background: rgba(255, 255, 255, 0.10);
        border-color: {ac};
        color: {ac};
    }}
    QPushButton[variant]:pressed {{
        background: rgba(255, 255, 255, 0.16);
    }}
    QPushButton[variant]:disabled {{
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid rgba(255, 255, 255, 0.05);
        color: rgba(255, 255, 255, 0.25);
    }}

    QPushButton[variant="danger"] {{
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.07);
        color: rgba(255, 255, 255, 0.3);
    }}
    QPushButton[variant="danger"]:enabled {{
        background: rgba(160, 30, 20, 0.15);
        border-color: rgba(255, 100, 80, 0.4);
        color: #ff8a7a;
    }}
    QPushButton[variant="danger"]:enabled:hover {{
        background: rgba(160, 30, 20, 0.25);
        border-color: #ff8a7a;
        color: #ffffff;
    }}
    QPushButton[variant="danger"]:disabled {{
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid rgba(255, 255, 255, 0.05);
        color: rgba(255, 255, 255, 0.25);
    }}

    QPushButton[variant="warn"] {{
        background: rgba(255, 140, 0, 0.08);
        border: 1px solid rgba(255, 140, 0, 0.25);
        border-radius: 8px;
        color: #FFA726;
        font-size: 8.5pt;
        font-weight: 600;
        padding: 0 10px;
    }}
    QPushButton[variant="warn"]:hover {{
        background: rgba(255, 140, 0, 0.16);
        border-color: #FFA726;
        color: #FFB74D;
    }}
    QPushButton[variant="warn"]:disabled {{
        background: rgba(255, 255, 255, 0.02);
        border-color: rgba(255, 255, 255, 0.05);
        color: rgba(255, 255, 255, 0.25);
    }}

    QPushButton[variant="pill-accent"] {{
        background: transparent;
        border: none;
        color: {ac};
        font-weight: bold;
        font-size: 8.5pt;
        padding: 0 10px;
    }}
    QPushButton[variant="pill-accent"]:hover {{
        background: rgba(255, 255, 255, 0.08);
    }}
    QPushButton[variant="pill-accent"]:disabled {{
        color: rgba(255, 255, 255, 0.25);
        background: transparent;
    }}

    QPushButton[variant="pill-danger"] {{
        background: transparent;
        border: none;
        color: #e05a47;
        font-weight: bold;
        font-size: 8.5pt;
        padding: 0 10px;
    }}
    QPushButton[variant="pill-danger"]:hover {{
        background: rgba(224, 90, 71, 0.12);
    }}
    QPushButton[variant="pill-danger"]:disabled {{
        color: rgba(255, 255, 255, 0.25);
        background: transparent;
    }}
    """


def _btn(text: str, *, variant: str = "default", tip: str = "", height: int = 34) -> QPushButton:
    """Button factory standardizing properties, cursor, height and tooltips."""
    btn = QPushButton(text)
    btn.setProperty("variant", variant)
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if tip:
        btn.setToolTip(tip)
    return btn


def _is_atom(dialog) -> bool:
    """Check if the dialog game is managed by AT0-M / Vapor."""
    return bool(
        getattr(dialog, "is_atom", False)
        or (getattr(dialog, "game_data", None) or {}).get("is_atom")
        or (getattr(dialog, "game_data", None) or {}).get("source") in ("Vapor", "at0-m", "AT0-M")
    )


def _dismiss_steamless_loading(dialog) -> None:
    """Stop deferred timer and dismiss any active Steamless loading overlay."""
    timer = getattr(dialog, "_steamless_overlay_timer", None)
    if timer is not None and not sip.isdeleted(timer):
        timer.stop()

    overlay = getattr(dialog, "_steamless_loading_overlay", None)
    if overlay is not None and not sip.isdeleted(overlay):
        try:
            overlay.close()
            overlay.deleteLater()
        except (RuntimeError, AttributeError) as exc:
            logger.debug("Could not dismiss Steamless loading overlay: %s", exc)
    dialog._steamless_loading_overlay = None


def _disconnect_steamless_signal(dialog) -> None:
    """Disconnect any tracked Steamless completion callback from TaskManager."""
    tm = getattr(dialog, "_steamless_tm_ref", None)
    slot = getattr(dialog, "_steamless_done_slot", None)
    if tm is not None and slot is not None:
        sig = getattr(tm, "steamless_manual_finished", None)
        if sig is not None:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError) as exc:
                logger.debug("Steamless signal disconnect ignored: %s", exc)
    dialog._steamless_done_slot = None
    dialog._steamless_tm_ref = None


def _finish_steamless_ui(dialog) -> None:
    """Restore button state and clear loading feedback when Steamless ends."""
    if sip.isdeleted(dialog):
        return
    dialog._steamless_running = False
    _dismiss_steamless_loading(dialog)
    _disconnect_steamless_signal(dialog)

    if (
        hasattr(dialog, "b_steamless_aio")
        and dialog.b_steamless_aio
        and not sip.isdeleted(dialog.b_steamless_aio)
    ):
        dialog.b_steamless_aio.setText(_STEAMLESS_AIO_LABEL)
    if (
        hasattr(dialog, "b_steamless_cli")
        and dialog.b_steamless_cli
        and not sip.isdeleted(dialog.b_steamless_cli)
    ):
        dialog.b_steamless_cli.setText(_STEAMLESS_CLI_LABEL)
    refresh_drm_emulation_state(dialog)


def _show_steamless_overlay_if_running(dialog, accent_color: str) -> None:
    """Reveal PixelLoadingOverlay only when Steamless runs longer than 400ms."""
    if sip.isdeleted(dialog) or not getattr(dialog, "_steamless_running", False):
        return
    if getattr(dialog, "_steamless_loading_overlay", None) is not None:
        return
    try:
        from ui.widgets.pixel_loader_overlay import PixelLoadingOverlay

        overlay = PixelLoadingOverlay(
            parent=dialog,
            label="Scanning for SteamStub DRM...",
            accent_color=accent_color,
        )
        dialog._steamless_loading_overlay = overlay
        overlay.show_centered()
    except Exception as exc:
        logger.debug("Could not show Steamless loading overlay: %s", exc)


def _launch_steamless_with_feedback(
    dialog,
    *,
    use_aio: bool,
    path: str,
    name: str,
    accent_color: str,
) -> None:
    """Run manual Steamless with instant button feedback and 400ms delayed overlay."""
    if getattr(dialog, "_steamless_running", False):
        return

    if not use_aio and not require_component(dialog, "steamless"):
        return

    parent_win = getattr(dialog, "parent_window", None)
    main_win = getattr(parent_win, "main_window", None) if parent_win else None
    tm = getattr(main_win, "task_manager", None) if main_win else None
    if tm is None:
        ThemedMessageBox.warning(
            dialog,
            "Task Manager Unavailable",
            "Cannot run Steamless because TaskManager is not available.",
        )
        return

    done_sig = getattr(tm, "steamless_manual_finished", None)
    can_track = done_sig is not None and hasattr(done_sig, "connect")

    if can_track:
        _disconnect_steamless_signal(dialog)
        dialog._steamless_running = True
        clicked_btn = dialog.b_steamless_aio if use_aio else dialog.b_steamless_cli
        if hasattr(dialog, "b_steamless_aio") and dialog.b_steamless_aio:
            dialog.b_steamless_aio.setEnabled(False)
        if hasattr(dialog, "b_steamless_cli") and dialog.b_steamless_cli:
            dialog.b_steamless_cli.setEnabled(False)
        if clicked_btn and not sip.isdeleted(clicked_btn):
            clicked_btn.setText("Scanning...")

        def _on_done():
            _finish_steamless_ui(dialog)

        dialog._steamless_tm_ref = tm
        dialog._steamless_done_slot = _on_done
        done_sig.connect(_on_done)

        if hasattr(dialog, "finished") and not getattr(dialog, "_steamless_cleanup_wired", False):
            dialog._steamless_cleanup_wired = True
            dialog.finished.connect(lambda _res=0: _finish_steamless_ui(dialog))

        timer = getattr(dialog, "_steamless_overlay_timer", None)
        if timer is None or sip.isdeleted(timer):
            timer = QTimer(dialog)
            timer.setSingleShot(True)
            dialog._steamless_overlay_timer = timer
        else:
            try:
                timer.timeout.disconnect()
            except (RuntimeError, TypeError) as exc:
                logger.debug("Steamless timer disconnect ignored: %s", exc)
        timer.timeout.connect(
            lambda ac_val=accent_color: _show_steamless_overlay_if_running(dialog, ac_val)
        )
        timer.start(_STEAMLESS_OVERLAY_DELAY_MS)

    try:
        appid_str = str(getattr(dialog, "appid", "") or "")
        if use_aio:
            tm.run_steamless_aio_for_game(path, name, appid=appid_str)
        else:
            tm.run_steamless_for_game(path, name, appid=appid_str)
    except Exception:
        if can_track:
            _finish_steamless_ui(dialog)
        raise


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
    grid_widget.setStyleSheet(_tools_qss(ac))
    grid = QVBoxLayout(grid_widget)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(10)

    # ──────────────────────────────────────────
    # Section 1: DRM & Emulation
    # ──────────────────────────────────────────
    grid.addWidget(section_title("DRM & Emulation", ac))

    # Row 1: Steamless (Python) | Steamless (.NET CLI)
    dialog.b_steamless_aio = _btn(
        _STEAMLESS_AIO_LABEL,
        tip="Remove Steam DRM using Python Steamless (AIO)",
        height=34,
    )
    dialog.b_steamless_aio.clicked.connect(
        lambda: _launch_steamless_with_feedback(
            dialog, use_aio=True, path=path, name=name, accent_color=ac
        )
    )

    dialog.b_steamless_cli = _btn(
        _STEAMLESS_CLI_LABEL,
        tip="Remove Steam DRM using .NET 9 Steamless CLI",
        height=34,
    )
    dialog.b_steamless_cli.clicked.connect(
        lambda: _launch_steamless_with_feedback(
            dialog, use_aio=False, path=path, name=name, accent_color=ac
        )
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
    dialog.gb_apply_btn = _btn(
        "Apply Goldberg",
        tip="Apply Goldberg Steam emulator to this game",
        height=34,
    )

    dialog.gb_remove_btn = _btn(
        "Remove Goldberg",
        variant="danger",
        tip="Remove Goldberg Steam emulator from this game",
        height=34,
    )
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
        # Goldberg is no longer bundled - send the user to the download button
        # instead of failing on a missing folder.
        if not require_component(dialog, "goldberg"):
            return
        parent_win = getattr(dialog, "parent_window", None)
        main_win = getattr(parent_win, "main_window", None) if parent_win else None
        tm = getattr(main_win, "task_manager", None) if main_win else None
        if tm is None:
            ThemedMessageBox.warning(
                dialog,
                "Task Manager Unavailable",
                "Cannot apply Goldberg because TaskManager is not available.",
            )
            return
        tm.apply_goldberg_to_game(path, dialog.appid, name, show_dialog=True)
        executor = getattr(parent_win, "executor", None)
        check_fn = getattr(parent_win, "_check_goldberg_async", None)
        if executor and check_fn:
            executor.submit(check_fn, path)

    def _remove_gb():
        # Removing Goldberg from a game that never had it is a no-op, but the
        # user still needs the files present for the operation to make sense.
        if not is_component_available("goldberg"):
            prompt_component_missing(dialog, "goldberg")
            return
        parent_win = getattr(dialog, "parent_window", None)
        main_win = getattr(parent_win, "main_window", None) if parent_win else None
        tm = getattr(main_win, "task_manager", None) if main_win else None
        if tm is None:
            ThemedMessageBox.warning(
                dialog,
                "Task Manager Unavailable",
                "Cannot remove Goldberg because TaskManager is not available.",
            )
            return
        tm.remove_goldberg_from_game(path, dialog.appid, name, show_dialog=True)
        executor = getattr(parent_win, "executor", None)
        check_fn = getattr(parent_win, "_check_goldberg_async", None)
        if executor and check_fn:
            executor.submit(check_fn, path)

    dialog.gb_apply_btn.clicked.connect(_apply_gb)
    dialog.gb_remove_btn.clicked.connect(_remove_gb)

    if not is_component_available("goldberg"):
        dialog.gb_remove_btn.setToolTip("Goldberg is not downloaded on this machine.")
        dialog.gb_remove_btn.setEnabled(False)

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
    choose_reset_group.setFixedHeight(34)
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

    dialog.choose_depots_btn = _btn("Choose...", variant="pill-accent", height=34)
    dialog.choose_depots_btn.clicked.connect(dialog._configure_depots_wrapper)

    cr_divider = QFrame()
    cr_divider.setFixedWidth(1)
    cr_divider.setStyleSheet("background: rgba(255, 255, 255, 0.12);")

    reset_btn = _btn("Reset", variant="pill-danger", height=34)
    reset_btn.setFixedWidth(60)
    reset_btn.clicked.connect(dialog._reset_depots_wrapper)

    cr_layout.addWidget(dialog.choose_depots_btn, 1)
    cr_layout.addWidget(cr_divider)
    cr_layout.addWidget(reset_btn)

    is_atom = _is_atom(dialog)
    if is_atom:
        dialog.choose_depots_btn.setEnabled(False)
        reset_btn.setEnabled(False)
        tooltip_msg = "Depot selection is managed automatically by Steam for AT0-M games."
        dialog.choose_depots_btn.setToolTip(tooltip_msg)
        reset_btn.setToolTip(tooltip_msg)
        choose_reset_group.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
            }
        """)

    depots_row.addWidget(choose_reset_group, 1)

    dialog.fix_btn = _btn(
        "Fix Installation",
        tip="Repairs game installation and forces Steam verification.",
        height=34,
    )

    def _on_fix_clicked():
        parent_win = getattr(dialog, "parent_window", None)
        fix_fn = getattr(parent_win, "_fix_game_install", None)
        if fix_fn:
            fix_fn(dialog.game_data)
        else:
            ThemedMessageBox.warning(
                dialog,
                "Action Unavailable",
                "Fix Installation is not available in this context.",
            )

    dialog.fix_btn.clicked.connect(_on_fix_clicked)
    depots_row.addWidget(dialog.fix_btn, 1)

    # App-menu shortcut that starts the game through Steam (as ACCELA did).
    # ASSella-managed games on Linux only.
    from utils import desktop_shortcuts

    if desktop_shortcuts.supported() and not is_atom:
        dialog.desktop_btn = _btn("Desktop", tip="Checking for a shortcut...", height=34)
        dialog.desktop_btn.setEnabled(False)
        depots_row.addWidget(dialog.desktop_btn, 1)
        _wire_desktop_button(dialog)

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

    steam_btn = _btn("Open Store", height=34)
    steam_btn.setEnabled(is_real_app)
    if not is_real_app:
        steam_btn.setToolTip("No valid Steam App ID for this game")
    steam_btn.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl(f"https://store.steampowered.com/app/{dialog.appid}/"))
    )
    links_row.addWidget(steam_btn, 1)

    prefix_btn = _btn("Open Prefix", height=34)

    compat_path = None
    if platform.system() == "Linux" and is_real_app:
        compat_path = find_compatdata_path(
            dialog.appid,
            dialog.game_data.get("install_path"),
            dialog.game_data.get("library_path"),
        )

    has_prefix = bool(compat_path and os.path.exists(compat_path))
    prefix_btn.setEnabled(has_prefix)
    if has_prefix:
        prefix_btn.setToolTip(f"Open Proton prefix: {compat_path}")
    else:
        prefix_btn.setToolTip("No Proton prefix found on disk")

    if has_prefix and compat_path:
        def _open_prefix():
            if hasattr(dialog, "parent_window") and dialog.parent_window and hasattr(dialog.parent_window, "_open_folder"):
                dialog.parent_window._open_folder(compat_path)
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(compat_path))

        prefix_btn.clicked.connect(_open_prefix)
    links_row.addWidget(prefix_btn, 1)

    copy_appid = _btn("Copy App ID", tip="Copy Steam App ID to clipboard", height=34)
    copy_appid.setEnabled(is_real_app)
    if not is_real_app:
        copy_appid.setToolTip("No valid Steam App ID for this game")
    copy_appid.clicked.connect(lambda: QApplication.clipboard().setText(dialog.appid))
    links_row.addWidget(copy_appid, 1)

    copy_path = _btn("Copy Path", tip="Copy game installation path to clipboard", height=34)
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

    dialog.dlcdata_exp_btn = _btn("Move DLC to DlcData (Advanced)", variant="warn", height=34)
    refresh_dlcdata_btn_text(dialog)
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
    steamless_busy = bool(getattr(dialog, "_steamless_running", False))
    if hasattr(dialog, "sl_row_widget") and dialog.sl_row_widget:
        dialog.sl_row_widget.setVisible(True)
    if hasattr(dialog, "b_steamless_aio") and dialog.b_steamless_aio:
        dialog.b_steamless_aio.setEnabled(not steamless_busy)
        dialog.b_steamless_aio.setToolTip("Remove Steam DRM using Python Steamless (AIO)")
    if hasattr(dialog, "b_steamless_cli") and dialog.b_steamless_cli:
        dialog.b_steamless_cli.setEnabled(not steamless_busy)
        dialog.b_steamless_cli.setToolTip("Remove Steam DRM using .NET 9 Steamless CLI")
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
    if _is_atom(dialog):
        if hasattr(dialog, "choose_depots_btn") and dialog.choose_depots_btn:
            dialog.choose_depots_btn.setText("Depots: AT0-M")
            dialog.choose_depots_btn.setEnabled(False)
        return

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
            except Exception as exc:
                logger.debug("Failed to parse depot_selection for AppID %s: %s", getattr(dialog, "appid", "?"), exc)
    if hasattr(dialog, "choose_depots_btn") and dialog.choose_depots_btn:
        dialog.choose_depots_btn.setText(btn_text)


def refresh_dlcdata_btn_text(dialog) -> None:
    """Refreshes text on Move DLC to DlcData button depending on existing config."""
    if not hasattr(dialog, "dlcdata_exp_btn") or not dialog.dlcdata_exp_btn or sip.isdeleted(dialog.dlcdata_exp_btn):
        return
    try:
        cp = get_user_config_path()
        if cp.exists() and bool(get_dlc_data(cp, dialog.appid)):
            dialog.dlcdata_exp_btn.setText("Revert DLC from DlcData (Advanced)")
        else:
            dialog.dlcdata_exp_btn.setText("Move DLC to DlcData (Advanced)")
    except Exception as exc:
        logger.debug("Could not refresh DlcData button text: %s", exc)
        dialog.dlcdata_exp_btn.setText("Move DLC to DlcData (Advanced)")


def handle_move_dlc_to_dlcdata(dialog) -> None:
    """Moves all DLC entries into DlcData or reverts them."""
    cp = get_user_config_path()
    if not cp.exists():
        ThemedMessageBox.warning(dialog, "Config Not Found", "SLSsteam config.yaml could not be found.")
        return

    current_dlcs = get_dlc_data(cp, dialog.appid)
    game_name = dialog.game_data.get("game_name", f"AppID {dialog.appid}")

    if current_dlcs:
        ans = ThemedMessageBox.question(
            dialog,
            "Revert DLC from DlcData",
            f"This game currently has {len(current_dlcs)} DLC(s) configured under DlcData in SLSsteam config.yaml.\n\n"
            f"Are you sure you want to revert and remove these DLC entries from DlcData for '{game_name}'?",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
        )
        if ans == ThemedMessageBox.StandardButton.Yes:
            if remove_dlc_data(cp, dialog.appid):
                ThemedMessageBox.information(
                    dialog,
                    "DlcData Reverted",
                    f"✓ Successfully removed DlcData entries for '{game_name}'.",
                )
            else:
                ThemedMessageBox.warning(dialog, "Action Failed", "Could not remove entries from DlcData.")
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
    ans = ThemedMessageBox.question(
        dialog,
        "Move DLC to DlcData (Advanced)",
        warn_msg,
        ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
    )
    if ans != ThemedMessageBox.StandardButton.Yes:
        return

    dialog.dlcdata_exp_btn.setEnabled(False)
    dialog.dlcdata_exp_btn.setText("Fetching DLCs...")

    def _bg_fetch_and_add():
        try:
            dlc_list = get_all_dlcs_for_app(dialog.appid, dialog.game_data)
            if not dlc_list:
                def _on_empty():
                    if sip.isdeleted(dialog):
                        return
                    ThemedMessageBox.information(
                        dialog,
                        "No DLCs Found",
                        f"No downloadable or store DLCs could be found for '{game_name}'.",
                    )
                    dialog.dlcdata_exp_btn.setEnabled(True)
                    refresh_dlcdata_btn_text(dialog)

                post_to_main_thread(_on_empty)
                return

            dlc_dict = {str(d["dlc_appid"]): d["dlc_name"] for d in dlc_list}
            ok = add_dlc_data_batch(cp, dialog.appid, dlc_dict)

            def _on_success(write_ok: bool, count: int):
                if sip.isdeleted(dialog):
                    return
                if write_ok:
                    ThemedMessageBox.information(
                        dialog,
                        "DLCs Moved to DlcData",
                        f"✓ Successfully wrote {count} DLC(s) for '{game_name}' to DlcData in config.yaml.\n\n"
                        f"SLSsteam will now explicitly report all these DLCs to the game.",
                    )
                else:
                    ThemedMessageBox.critical(
                        dialog,
                        "Write Failed",
                        "Failed to write entries to DlcData in config.yaml.",
                    )
                dialog.dlcdata_exp_btn.setEnabled(True)
                refresh_dlcdata_btn_text(dialog)

            post_to_main_thread(lambda: _on_success(ok, len(dlc_dict)))
        except Exception as exc:
            err_str = str(exc)

            def _on_error(err: str):
                if sip.isdeleted(dialog):
                    return
                ThemedMessageBox.critical(dialog, "Error", f"Failed to move DLCs: {err}")
                dialog.dlcdata_exp_btn.setEnabled(True)
                refresh_dlcdata_btn_text(dialog)

            post_to_main_thread(lambda: _on_error(err_str))

    threading.Thread(target=_bg_fetch_and_add, name="dlcdata-worker", daemon=True).start()


def _wire_desktop_button(dialog) -> None:
    """Add / remove the app-menu shortcut; all file and network work off the GUI thread."""
    from utils import desktop_shortcuts

    btn = dialog.desktop_btn
    appid = str(dialog.appid)
    name = dialog.game_data.get("game_name", "") or ""
    state = {"has": False}

    def _show(has: bool) -> None:
        if sip.isdeleted(btn):
            return
        state["has"] = has
        btn.setEnabled(True)
        btn.setText("Desktop: Remove" if has else "Desktop")
        btn.setToolTip(
            "Remove this game's shortcut from your app menu."
            if has else
            "Add this game to your app menu, with its SteamGridDB icon. The shortcut starts it through Steam."
        )

    def _run(work, busy_text: str) -> None:
        btn.setEnabled(False)
        btn.setText(busy_text)

        def _bg():
            try:
                ok, msg = work()
            except Exception as e:
                ok, msg = False, str(e)
            has = desktop_shortcuts.has_shortcut(appid)

            def _done():
                if sip.isdeleted(btn):
                    return
                _show(has)
                if ok:
                    ThemedMessageBox.information(dialog, "Desktop shortcut", msg)
                else:
                    ThemedMessageBox.warning(dialog, "Desktop shortcut", msg)

            post_to_main_thread(_done)

        threading.Thread(target=_bg, name="desktop-shortcut", daemon=True).start()

    def _clicked() -> None:
        if state["has"]:
            _run(lambda: desktop_shortcuts.remove_shortcut(appid), "Removing...")
        else:
            _run(lambda: desktop_shortcuts.create_shortcut(appid, name), "Adding...")

    btn.clicked.connect(_clicked)
    threading.Thread(
        target=lambda: post_to_main_thread(lambda: _show(desktop_shortcuts.has_shortcut(appid))),
        name="desktop-shortcut-check", daemon=True,
    ).start()
