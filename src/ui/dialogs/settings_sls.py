import logging
import os
import sys
import subprocess
import json
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
)
from ui.widgets.modern_dropdown import ModernComboBox
from ui.dialogs.themed_file_dialog import ThemedFileDialog
from ui.dialogs.themed_message_box import ThemedMessageBox

from utils.helpers import create_checkbox_setting
from utils.settings import get_settings

logger = logging.getLogger(__name__)

# SLSsteam default path on SteamOS/Linux
SLS_DIR = os.path.expanduser("~/.local/share/SLSsteam")
SLS_VERSION_FILE = os.path.join(SLS_DIR, "version")
SLS_SO_PATH = os.path.join(SLS_DIR, "SLSsteam.so")
SLS_INJECT_PATH = os.path.join(SLS_DIR, "library-inject.so")


def get_sls_paths() -> dict:
    """Scan potential directories for SLSsteam.so and library-inject.so.
    Returns a dict with:
      - 'dir': directory path
      - 'so_path': SLSsteam.so absolute path
      - 'inject_path': library-inject.so absolute path
      - 'version_file': path to version file
      - 'is_system_wide': bool
      - 'detected': bool
    """
    native_dir = os.path.expanduser("~/.local/share/SLSsteam")
    flatpak_dir = os.path.expanduser("~/.var/app/com.valvesoftware.Steam/.local/share/SLSsteam")
    system_dir = "/usr/lib32"

    candidates = [
        {
            "dir": native_dir,
            "so": os.path.join(native_dir, "SLSsteam.so"),
            "inject": os.path.join(native_dir, "library-inject.so"),
            "system": False
        },
        {
            "dir": flatpak_dir,
            "so": os.path.join(flatpak_dir, "SLSsteam.so"),
            "inject": os.path.join(flatpak_dir, "library-inject.so"),
            "system": False
        },
        {
            "dir": system_dir,
            "so": "/usr/lib32/libSLSsteam.so",
            "inject": "/usr/lib32/libSLS-library-inject.so",
            "system": True
        },
    ]

    for cand in candidates:
        if os.path.exists(cand["so"]):
            return {
                "dir": cand["dir"],
                "so_path": cand["so"],
                "inject_path": cand["inject"],
                "version_file": os.path.join(cand["dir"], "version") if not cand["system"] else os.path.join(native_dir, "version"),
                "is_system_wide": cand["system"],
                "detected": True
            }

    # Fallback/Default: Check if Flatpak Steam directory exists
    flatpak_steam_home = os.path.expanduser("~/.var/app/com.valvesoftware.Steam")
    default_dir = flatpak_dir if os.path.exists(flatpak_steam_home) else native_dir
    return {
        "dir": default_dir,
        "so_path": os.path.join(default_dir, "SLSsteam.so"),
        "inject_path": os.path.join(default_dir, "library-inject.so"),
        "version_file": os.path.join(default_dir, "version"),
        "is_system_wide": False,
        "detected": False
    }


def get_local_sls_version() -> str:
    """Helper to detect local SLSsteam installation version."""
    paths = get_sls_paths()
    if not paths["detected"]:
        return "Not Installed"
    
    if os.path.exists(paths["version_file"]):
        try:
            with open(paths["version_file"], "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
        except Exception:
            pass

    try:
        from utils.slssteam_integration import _binary_version_cache
        cached = _binary_version_cache.get("data", {})
        if cached.get("release_tag"):
            tag = cached["release_tag"]
            return f"{tag} (Headcrab)" if is_headcrab_installed() else tag
    except Exception:
        pass

    if is_headcrab_installed():
        return "Headcrab Managed"

    return "Installed"


def is_headcrab_installed() -> bool:
    """Detect whether Headcrab is installed using binaries and desktop shortcut."""
    dgsc_path = os.path.expanduser("~/.headcrab/dgsc")
    dlm_path = os.path.expanduser("~/.headcrab/dlm")
    desktop_path = os.path.expanduser("~/.local/share/applications/headcrab.desktop")
    return (os.path.exists(dgsc_path) and os.path.exists(dlm_path)) or os.path.exists(desktop_path)


def run_headcrab_and_quit(dialog) -> bool:
    """Run Headcrab curl installer in terminal and gracefully quit ASSella."""
    cmd = ["bash", "-c", "curl -fsSL headcrab.pages.dev | bash; echo; echo '--- Done. Press Enter to close ---'; read _"]
    launched = dialog._launch_terminal_command(cmd, os.path.expanduser("~"))
    if launched:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        QTimer.singleShot(600, QApplication.instance().quit)
        return True
    return False


def run_headcrab(dialog, callback=None) -> None:
    """Prompt and run Headcrab installation script inside a terminal, quitting ASSella upon launch."""
    already = is_headcrab_installed()
    verb = "re-run" if already else "install"
    reply = ThemedMessageBox.question(
        dialog,
        "Run Headcrab",
        f"This will {verb} Headcrab via:\n"
        "  curl -fsSL headcrab.pages.dev | bash\n\n"
        "A terminal window will open and ASSella will close so Headcrab can safely update SLSsteam without file conflicts.\n\n"
        "Proceed?",
        ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
        ThemedMessageBox.StandardButton.Yes,
    )
    if reply != ThemedMessageBox.StandardButton.Yes:
        return
    if not run_headcrab_and_quit(dialog) and callback:
        callback()


def create_sls_tab(dialog) -> QWidget:
    """Relocate Steam/SLS settings, ASShead fixer, and implement updater UI."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)

    sls_subtabs = QTabWidget()
    sls_subtabs.setUsesScrollButtons(True)
    bg_color = dialog.settings.value("background_color", "#141416")
    sls_subtabs.setStyleSheet(f"""
        QTabWidget::pane {{
            border: none;
        }}
        QTabBar::tab {{
            background: {bg_color};
            color: rgba(255, 255, 255, 0.6);
            padding: 6px 14px;
            border: none;
            font-weight: bold;
            font-size: 9pt;
        }}
        QTabBar::tab:selected {{
            color: {dialog.accent_color};
            border-bottom: 2px solid {dialog.accent_color};
        }}
        QTabBar::tab:hover {{
            color: #FFFFFF;
        }}
    """)

    # ── Subtab 1: Settings ──
    settings_widget = QWidget()
    settings_layout = QVBoxLayout(settings_widget)
    settings_layout.setContentsMargins(0, 8, 0, 0)
    settings_layout.setSpacing(12)

    # 1. Integration Card
    int_card, int_layout = dialog._create_card_frame("SLS Settings")

    from utils.helpers import is_arm64, is_armada_os
    if is_arm64():
        os_name = "Armada OS" if is_armada_os() else "ARM64 Linux"
        arm_badge = QLabel(
            f"ℹ️ <b>{os_name} (ARM64)</b>: SLSsteam is an x86_64 library injector and is not available "
            "on ARM64. ASSella is running in <b>Standalone Mode</b> (Direct Depot Downloads, Local Manifest Management, "
            "and Non-Steam Game launching)."
        )
        arm_badge.setStyleSheet(
            "background-color: rgba(64, 150, 255, 0.15); border: 1px solid rgba(64, 150, 255, 0.4); "
            "border-radius: 6px; padding: 8px; color: #E0E0E0; font-size: 8.5pt;"
        )
        arm_badge.setWordWrap(True)
        int_layout.addWidget(arm_badge)

    if sys.platform == "linux":
        wrapper_name = "SLSsteam"
        dialog.sls_mode_checkbox = None
        if not is_arm64():
            linux_hint = QLabel(
                "SLSsteam is enabled automatically for Steam library installs on Linux."
            )
            linux_hint.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt;")
            linux_hint.setWordWrap(True)
            int_layout.addWidget(linux_hint)
    else:
        wrapper_name = "GreenLuma"
        wrapper_full = "GreenLuma Wrapper Mode"
        tooltip = (
            "Integrate games with Steam using GreenLuma.\n"
            "Games appear in your Steam library automatically."
        )
        dialog.sls_mode_checkbox = create_checkbox_setting(
            wrapper_full, "slssteam_mode", False, dialog, tooltip
        )
        dialog.sls_mode_checkbox.stateChanged.connect(
            lambda: dialog.goldberg_checked_warning_from_mode(wrapper_name)
        )
        int_layout.addWidget(dialog.sls_mode_checkbox)

    dialog.experimental_acf_independent_checkbox = create_checkbox_setting(
        "SLSsteam API",
        "experimental_acf_independent",
        True,
        dialog,
        "Use SLSsteam native API for installation/uninstallation with native ACF generation.",
    )
    dialog.experimental_acf_independent_checkbox.stateChanged.connect(dialog._on_experimental_acf_toggled)
    int_layout.addWidget(dialog.experimental_acf_independent_checkbox)

    # All games online
    from utils.yaml_config_manager import get_user_config_path, get_fake_appid, add_fake_app_id, remove_fake_app_id
    cfg_path = get_user_config_path()
    # config.yaml is the source of truth: FakeAppIds "0: 480" present = on.
    current_all_online = bool(cfg_path.exists() and get_fake_appid(cfg_path, "0") is not None)

    dialog.all_games_online_checkbox = create_checkbox_setting(
        "All games online",
        "all_games_online",
        current_all_online,
        dialog,
        "Uses 0: 480 in SLSsteam FakeAppIds to map all unowned games to Spacewar (480) for online networking features.",
    )
    dialog.all_games_online_checkbox.setChecked(current_all_online)

    def _on_all_games_online_toggled(state):
        is_checked = bool(state)
        c_path = get_user_config_path()
        if not c_path.exists():
            return
        if is_checked:
            add_fake_app_id(c_path, "0", game_name="All Unowned Apps", fake_appid="480")
        else:
            remove_fake_app_id(c_path, "0", fake_appid="480")
            remove_fake_app_id(c_path, "0")

    dialog.all_games_online_checkbox.stateChanged.connect(_on_all_games_online_toggled)
    int_layout.addWidget(dialog.all_games_online_checkbox)

    # Enable EOSProxy by default
    dialog.enable_eosproxy_default_checkbox = create_checkbox_setting(
        "Enable EOSProxy by default",
        "enable_eosproxy_default",
        False,
        dialog,
        "Automatically detect and apply the EOS proxy DLL to newly downloaded games that use Epic Online Services.",
    )
    int_layout.addWidget(dialog.enable_eosproxy_default_checkbox)

    # Cleaned up: only single SLSsteam API toggle is shown.
    # Steam restart is disabled by default / forced off.
    # SLS config management is ON by default and toggles in lockstep with SLSsteam API.
    dialog.sls_config_management_checkbox = None
    dialog.prompt_steam_restart_checkbox = None
    dialog.settings.setValue("prompt_steam_restart", False)
    if dialog.settings.value("sls_config_management") is None:
        dialog.settings.setValue("sls_config_management", True)


    dialog.ignore_slssteam_updater_checkbox = None

    settings_layout.addWidget(int_card)
    settings_layout.addStretch()
    sls_subtabs.addTab(settings_widget, "Settings")
    from ui.dialogs.settings_tabs.sls_advanced_tab import create_sls_advanced_tab
    sls_subtabs.addTab(create_sls_advanced_tab(dialog), "Advanced")

    # ── Subtab 2: Tickets ──
    tickets_widget = QWidget()
    tickets_layout = QVBoxLayout(tickets_widget)
    tickets_layout.setContentsMargins(0, 8, 0, 0)
    tickets_layout.setSpacing(12)

    # 2. Generate & Share Tickets Card
    ticket_card, ticket_layout = dialog._create_card_frame("Generate & Share Tickets")
    ticket_layout.setSpacing(8)

    # Dropdown Selection for Available Games with Tickets
    from utils.ticket_manager import get_available_ticket_games, export_ticket, export_all_tickets

    combo_row = QHBoxLayout()
    combo_row.setSpacing(6)

    game_combo = ModernComboBox()

    available_games = get_available_ticket_games()
    if available_games:
        game_combo.addItem("Select from detected tickets...", "")
        for g in available_games:
            game_combo.addItem(g.get("display", f"AppID {g.get('appid')}"), str(g.get("appid")))
    else:
        game_combo.addItem("No tickets detected in Steam/SLS folder", "")

    combo_row.addWidget(game_combo, 1)

    refresh_btn = QPushButton("Refresh")
    refresh_btn.setFixedWidth(70)

    def _refresh_tickets():
        game_combo.clear()
        games = get_available_ticket_games()
        if games:
            game_combo.addItem("Select from detected tickets...", "")
            for g in games:
                game_combo.addItem(g.get("display", f"AppID {g.get('appid')}"), str(g.get("appid")))
        else:
            game_combo.addItem("No tickets detected in Steam/SLS folder", "")

    refresh_btn.clicked.connect(_refresh_tickets)
    combo_row.addWidget(refresh_btn)
    ticket_layout.addLayout(combo_row)

    # Custom AppID Export Row
    app_export_lay = QHBoxLayout()
    app_export_lay.setSpacing(6)

    appid_input = QLineEdit()
    appid_input.setPlaceholderText("Or enter custom AppID (e.g. 1086940)")
    appid_input.setStyleSheet("""
        QLineEdit {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 4px;
            padding: 4px 8px;
            color: #FFFFFF;
        }
    """)
    app_export_lay.addWidget(appid_input, 1)

    def _on_game_selected(idx):
        val = game_combo.itemData(idx)
        if val:
            appid_input.setText(str(val))

    game_combo.currentIndexChanged.connect(_on_game_selected)

    export_single_btn = QPushButton("Export Selected Ticket")
    export_single_btn.setStyleSheet("font-weight: bold; background: rgba(255, 255, 255, 0.15); color: #FFFFFF; border-radius: 4px; padding: 6px 12px;")

    def _do_export_single():
        try:
            appid = appid_input.text().strip()
            if not appid or not appid.isdigit():
                ThemedMessageBox.warning(dialog, "Invalid AppID", "Please select a game from dropdown or enter a valid numeric Steam AppID.")
                return
            save_path, _ = ThemedFileDialog.getSaveFileName(dialog, "Save Ownership Ticket YAML", f"ticket_{appid}.yaml", "YAML Files (*.yaml)")
            if save_path:
                ok, msg = export_ticket(appid, save_path)
                if ok:
                    ThemedMessageBox.information(dialog, "Ticket Exported", msg)
                else:
                    ThemedMessageBox.critical(dialog, "Export Failed", msg)
        except Exception as err:
            logger.error(f"Error exporting single ticket: {err}")
            ThemedMessageBox.critical(dialog, "Export Error", f"Failed to export ticket: {err}")

    export_single_btn.clicked.connect(lambda _c=False: _do_export_single())
    app_export_lay.addWidget(export_single_btn)
    ticket_layout.addLayout(app_export_lay)

    # Batch Export Row
    batch_btn = QPushButton("Export All Available Tickets (Batch)")
    batch_btn.setStyleSheet("font-weight: bold; background: rgba(255, 255, 255, 0.12); color: #FFFFFF; border-radius: 4px; padding: 6px 12px;")

    def _do_export_all(_checked=False):
        try:
            dest_dir = ThemedFileDialog.getExistingDirectory(dialog, "Select Output Directory for Ticket Batch Export")
            if dest_dir:
                from utils.ticket_manager import export_all_tickets
                ok, msg, count = export_all_tickets(dest_dir)
                if ok:
                    ThemedMessageBox.information(dialog, "Batch Export Complete", msg)
                else:
                    ThemedMessageBox.warning(dialog, "Batch Export", msg)
        except Exception as err:
            logger.error(f"Error exporting all tickets: {err}")
            ThemedMessageBox.critical(dialog, "Export Error", f"Failed batch export: {err}")

    batch_btn.clicked.connect(lambda _c=False: _do_export_all())
    ticket_layout.addWidget(batch_btn)

    tickets_layout.addWidget(ticket_card)
    tickets_layout.addStretch()
    sls_subtabs.addTab(tickets_widget, "Tickets")

    layout.addWidget(sls_subtabs)
    dialog.tab_widget.addTab(tab, "SLS")
    return tab
